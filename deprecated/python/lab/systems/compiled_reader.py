"""Compiled questions: recurring question shapes compiled into templates that run as SQL + code, with no model.

The read-side twin of the compiled writer (lab/systems/compiled_writer.py):
compile   one LLM call per round reads the query log's trusted questions (verifier >= 0.7) and their plans, and writes
          templates: a full-match regex with named groups, plus how each plan slot is filled
validate  code keeps a template only if it reproduces the logged plan on >= 2 trusted questions and on every trusted
          question it matches; rejected templates go back to the LLM with the reason (up to `rounds` rounds)
answer    the first template that fully matches, with every group mapped to a stored value, runs through the
          deterministic reader's own select/apply code; anything else goes to the Jev reader
Group words that differ from stored values ("Uber and Bolt rides" -> transport) are learned from the examples, and must
map consistently.
"""
# @ref LLP 0003#compiled-questions — templates from trusted plans, validated by replaying the log
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta

from lab.common.llm import chat_json
from lab.systems.derived import fold

SCHEMA = {"type": "object", "properties": {"templates": {"type": "array", "items": {"type": "object", "properties": {
    "regex": {"type": "string", "description": "Python regex that matches the WHOLE question, with named groups"},
    "table": {"type": "string"},
    "op": {"type": "string"},
    "column": {"type": "string"},
    "filters": {"type": "array", "items": {"type": "object", "properties": {
        "column": {"type": "string"},
        "source": {"type": "string", "description": "group:NAME or const:VALUE"}},
        "required": ["column", "source"], "additionalProperties": False}},
    "period": {"type": "string", "description": "group:period, or const:PERIOD_ID"}},
    "required": ["regex", "table", "op", "column", "filters", "period"], "additionalProperties": False}}},
    "required": ["templates"], "additionalProperties": False}

PROMPT = r"""Questions like the ones below keep being asked of a personal database. Each was answered by the query plan
shown with it: a table, an operation, the column it applies to, filters (column = value) and a period id. Write
question templates, so code can answer future questions of the same shapes without a model.

Each template:
- regex: a Python regex that must match the WHOLE question (it is used with re.fullmatch and re.IGNORECASE). Capture
  in a named group every part that varies between questions of the same shape: the thing asked about, a merchant or a
  name, and the time phrase in a group named `period`. Write GENERAL patterns that also match values never seen here:
  (?P<period>in \w+ \d{{4}}|in \d{{4}}|this year|last year|this month|last month) for time phrases (make the group
  optional, with the space before it, when some questions have none), and (?P<merchant>.+?) for names. Never list the
  specific values seen in the examples. Keep the fixed wording literal, so that a question with any extra condition or
  a different meaning does NOT match. A group name may appear only once in a regex (put alternatives inside it).
- table, op, column: as in the plans.
- filters: one per filter column in the plans. Source "group:NAME" when the value varies with the question (code maps
  the captured words to a stored value), "const:VALUE" when it is always the same.
- period: "group:period", or "const:all" when these questions never have a time limit.
Write one template per question shape; phrasings of one shape may share a template (alternatives in the regex).
Period ids: all, year:YYYY, in:YYYY-MM, since:YYYY-MM; code turns the captured time phrase into one.

QUESTIONS AND THEIR PLANS:
{examples}{feedback}"""

MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                       "september", "october", "november", "december"], 1)}


def parse_period(text: str | None, now: datetime) -> str | None:
    """A time phrase -> a period id of the deterministic reader ("in March 2025" -> in:2025-03); None if unknown."""
    t = re.sub(r"[?.!,]+$", "", (text or "").strip().lower())
    t = re.sub(r"^(in|during|for|over|of)\s+", "", t).strip()
    if t in ("", "ever", "all time", "in total", "overall", "so far", "total"):
        return "all"
    if t == "this year":
        return f"year:{now.year}"
    if t == "last year":
        return f"year:{now.year - 1}"
    if t == "this month":
        return f"in:{now:%Y-%m}"
    if t == "last month":
        return f"in:{now.replace(day=1) - timedelta(days=1):%Y-%m}"
    if re.fullmatch(r"20\d\d", t):
        return f"year:{t}"
    m = re.fullmatch(r"(since\s+)?([a-z]+)\s+(20\d\d)", t)
    if m:
        month = next((i for name, i in MONTHS.items() if name.startswith(m.group(2)[:3])), None)
        if month and len(m.group(2)) >= 3:
            return f"{'since' if m.group(1) else 'in'}:{m.group(3)}-{month:02d}"
    return None


class CompiledReader:
    def __init__(self, reader, now: datetime, model: str = "openai:gpt-6-luna"):
        self.reader, self.now, self.model = reader, now, model
        self.templates: list[dict] = []
        self.aliases: dict[tuple[str, str], dict[str, str]] = {}   # (table, column) -> {folded words: value}
        self.report: list[dict] = []

    # ------------------------------------------------------------------ plans
    def _norm(self, plan: dict) -> dict:
        """A plan for comparison: with or without the filter on the user themself is the same plan."""
        f = {d: v for d, v in (plan.get("filters") or {}).items() if v != self.reader.user}
        return {"table": plan.get("table"), "op": plan.get("op"), "column": plan.get("column"), "filters": f,
                "period": plan.get("period")}

    # @ref LLP 0003#compiled-questions — a capture may be part of a stored value, never contain one
    def _map(self, table: str, column: str, text: str | None, learned: dict | None = None) -> str | None:
        if text is None or table not in self.reader.meta or column not in self.reader.meta[table]["dims"]:
            return None
        key = fold(text).strip()
        if learned and (column, key) in learned:
            return learned[(column, key)]
        if key in self.aliases.get((table, column), {}):
            return self.aliases[(table, column)][key]
        values = self.reader._values(table, column)
        exact = [v for v in values if fold(v).strip() == key]
        if len(exact) == 1:
            return exact[0]
        # the captured words may be part of one stored value ("Onesip" -> "Onesip Coffee"), never the other way round:
        # a value inside the capture means the capture swallowed more ("Kantýna in July 2024")
        inside = [v for v in values if len(key) >= 3 and re.search(rf"(?<!\w){re.escape(key)}(?!\w)", fold(v))]
        return inside[0] if len(inside) == 1 else None

    def plan(self, tpl: dict, question: str, learned: dict | None = None, want: dict | None = None):
        """(plan, None) for a full match whose groups all map to stored values, else (None, reason). With `want` (a
        trusted plan), unmapped group words are learned into `learned` from it."""
        try:
            m = re.fullmatch(tpl["regex"], question.strip(), re.I)
        except re.error as e:
            return None, f"bad regex: {e}"
        if not m:
            return None, "no match"
        filters = {}
        for f in tpl["filters"]:
            src, col = f["source"], f["column"]
            if src.startswith("const:"):
                filters[col] = src[6:]
                continue
            group = src[6:]
            if group not in m.re.groupindex:
                return None, f"no group {group!r} in the regex"
            text = m.group(group)
            value = self._map(tpl["table"], col, text, learned)
            if value is None and want is not None and text is not None and col in want["filters"]:
                learned[(col, fold(text).strip())] = value = want["filters"][col]
            if value is None:
                return None, f"{col}: no stored value for {text!r}"
            filters[col] = value
        if tpl["period"].startswith("group:"):
            group = tpl["period"][6:]
            if group not in m.re.groupindex:
                return None, f"no group {group!r} in the regex"
            period = parse_period(m.group(group), self.now)
            if period is None:
                return None, f"period: can't read {m.group(group)!r}"
        else:
            period = tpl["period"][6:] if tpl["period"].startswith("const:") else tpl["period"]
        return {"table": tpl["table"], "op": tpl["op"], "column": tpl["column"], "filters": filters, "period": period}, None

    # ---------------------------------------------------------------- compile
    def validate(self, tpl: dict, examples: list[dict]) -> dict:
        learned, matched, exact, wrong = {}, 0, 0, []
        for ex in examples:
            want = self._norm(ex["plan"])
            got, why = self.plan(tpl, ex["question"], learned, ex["plan"])
            if got is None and why == "no match":
                continue
            matched += 1
            # the same plan, or a plan that gives the same answer (a redundant filter such as unit = 'kg' changes nothing)
            if got is not None and (self._norm(got) == want or self.run(got) == self.run(ex["plan"]) is not None):
                exact += 1
            else:
                wrong.append(f"{ex['question']!r}: " + (why or f"got {got}, the logged plan is {want}"))
        keep = matched >= 2 and exact == matched
        return {"regex": tpl["regex"], "matched": matched, "exact": exact, "keep": keep, "wrong": wrong[:3],
                "learned": learned}

    def compile(self, examples: list[dict], rounds: int = 2) -> list[dict]:
        """examples: [{"question", "plan": {table, op, column, filters, period}}] from the query log."""
        lines = "\n".join(f"- {ex['question']}  ->  " + str({k: ex["plan"].get(k) for k in ("table", "op", "column", "filters", "period")})
                          for ex in examples)
        feedback, kept = "", []
        for rnd in range(rounds):
            out, _ = chat_json(self.model, PROMPT.format(examples=lines, feedback=feedback), SCHEMA,
                               schema_name="templates", tag="compiled_reader:compile", effort="low", max_tokens=8000)
            rejected = []
            for tpl in out["templates"]:
                v = self.validate(tpl, examples)
                self.report.append({"round": rnd, **{k: v[k] for k in ("regex", "matched", "exact", "keep", "wrong")}})
                if v["keep"] and not any(k["regex"] == tpl["regex"] for k in kept):
                    kept.append(tpl)
                    self._learn(tpl, v["learned"], examples)
                elif not v["keep"]:
                    rejected.append((tpl, v))
            if not rejected:
                break
            feedback = "\n\nTEMPLATES YOU WROTE BEFORE THAT CODE REJECTED (fix them or drop them):\n" + "\n".join(
                f"- {t['regex']}: matched {v['matched']}, reproduced {v['exact']}; " + "; ".join(v["wrong"])
                for t, v in rejected)
        self.templates = kept
        return kept

    def _learn(self, tpl: dict, learned: dict, examples: list[dict]):
        """Keep a template's learned words as nicknames of the column its groups fill (only consistent ones)."""
        for f in tpl["filters"]:
            if not f["source"].startswith("group:"):
                continue
            store = self.aliases.setdefault((tpl["table"], f["column"]), {})
            for ex in examples:
                m = re.fullmatch(tpl["regex"], ex["question"].strip(), re.I)
                if not m or f["source"][6:] not in m.re.groupindex or m.group(f["source"][6:]) is None:
                    continue
                key = fold(m.group(f["source"][6:])).strip()
                value = (ex["plan"].get("filters") or {}).get(f["column"])
                if (f["column"], key) in learned and value is not None:
                    if store.get(key, value) != value:
                        store[key] = None   # the same words meant two values: never use them
                    elif key not in store:
                        store[key] = value
            for k in [k for k, v in store.items() if v is None]:
                del store[k]

    # ----------------------------------------------------------------- answer
    def run(self, plan: dict) -> str | None:
        r = self.reader
        if plan.get("table") not in r.meta:
            return None
        with r._lock:
            try:
                rows = r._select(plan["table"], dict(plan["filters"]), plan["period"], plan["column"] or "__row__")
                return r._apply(plan["table"], plan["op"], plan["column"] or "__row__", rows)
            except (KeyError, TypeError, ValueError):
                return None

    def answer(self, question: str) -> dict | None:
        t0 = time.perf_counter()
        for i, tpl in enumerate(self.templates):
            plan, _ = self.plan(tpl, question)
            if plan is None:
                continue
            r = self.reader
            with r._lock:
                filters = dict(plan["filters"])
                rows = r._select(plan["table"], filters, plan["period"], plan["column"])
                ans = r._apply(plan["table"], plan["op"], plan["column"], rows)
            if ans is None:
                return None
            return {"answer": ans, "plan": plan, "template": i, "wall_s": time.perf_counter() - t0,
                    "text": r._plan_text(plan["table"], plan["op"], plan["column"], plan["filters"], plan["period"], len(rows))}
        return None
