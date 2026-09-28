"""Tier A of the scalable writer: patterns an LLM compiles from the planner's own work, then applied by code.

compile: for every table the planner filled from many single-row messages, one LLM call sees ~30 (message -> row)
    examples and returns regex patterns with named groups, plus where each column's value comes from. Code keeps a
    pattern only if it reproduces the planner's rows on the examples it matches and never matches messages that went
    to other tables.
write: the first kept pattern that matches builds the row (numbers parsed, currencies normalized by the engine,
    dates from the message time, "yesterday" = the day before, or an explicit date in the text) and the engine applies
    a plain insert with provenance. No model call. Messages no pattern matches go to the LLM planner (tier C).
"""
# @ref LLP 0005#compiled-writer — tier A: patterns compiled from the planner's own rows, run by code
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timedelta

from lab.common.llm import chat_json

SCHEMA = {"type": "object", "properties": {"patterns": {"type": "array", "items": {"type": "object", "properties": {
    "regex": {"type": "string"},
    "values": {"type": "array", "items": {"type": "object", "properties": {
        "column": {"type": "string"}, "source": {"type": "string"}},
        "required": ["column", "source"], "additionalProperties": False}}},
    "required": ["regex", "values"], "additionalProperties": False}}},
    "required": ["patterns"], "additionalProperties": False}

PROMPT = r"""You write extraction patterns for a personal database. Messages like the examples below keep arriving, and
code (not a model) must turn each one into the same row the examples show.

Table `{table}`: {description}
Columns: {columns}

Examples (message -> the row that was stored for it):
{examples}

Write Python regexes (they are applied case-insensitively with re.search) that recognise messages of this kind and
capture their values in named groups. Cover every phrasing in the examples with as few patterns as possible, and make
them specific enough that they don't match other kinds of messages. Capture names (merchants, places, people) with a
general sub-pattern anchored by the surrounding words, e.g. at (?P<merchant>[^,$\d]+?)\s*[,$\d]; never list the names
seen in the examples: new ones will keep coming, and code maps each capture to its existing spelling. For a link
column (one ending in _id), capture the linked thing's name; code looks up its id. For each pattern, list for every
column of the row where its value comes from:
  "group:NAME"        the text captured by (?P<NAME>...). Python forbids repeating a group name, so when alternative
                      branches capture the same column name them NAME, NAME_2, NAME_3: "group:NAME" takes whichever matched
  "const:VALUE"       a fixed value, only if every message this pattern matches has that value
  "choice"            the value isn't written in the message (e.g. category "dining" for a lunch): code picks one of
                      the column's existing values, from what the same merchant/place had before or with a classifier.
                      Prefer one general pattern with "choice" over one pattern per category.
  "date"              the date the message is about: its own date, or the day before if it says "yesterday"
  "date:group:NAME"   an explicit date in the text, e.g. 25.10.2025 or 10/25/2025
  "datetime:group:NAME" an explicit date and time in the text, e.g. 25.10.2025 18:56 or 10/25/2025 18:56
  "clock:group:NAME"  a clock time like 54:48 or 1:02:03, stored as seconds
  "hours:group:NAME"  a duration like 7h 20m, 7.5h, 7.5 hours or 7:20, stored as hours (7.33)
  "minutes:group:NAME" the same duration stored as minutes
Every column the example rows always have must get a source (use const for values that never change, e.g. the
person the row belongs to). Free-text columns (a description) may come from a group or a const.
Leave out columns the message doesn't give. Don't capture chatter such as "btw", "lol" or emojis."""


UPDATE_SCHEMA = {"type": "object", "properties": {"patterns": {"type": "array", "items": {"type": "object", "properties": {
    "regex": {"type": "string"},
    "key": {"type": "object", "properties": {"column": {"type": "string"}, "source": {"type": "string"}},
            "required": ["column", "source"], "additionalProperties": False},
    "values": {"type": "array", "items": {"type": "object", "properties": {
        "column": {"type": "string"}, "source": {"type": "string"}},
        "required": ["column", "source"], "additionalProperties": False}}},
    "required": ["regex", "key", "values"], "additionalProperties": False}}},
    "required": ["patterns"], "additionalProperties": False}

UPDATE_PROMPT = r"""You write update patterns for a database. Messages like the examples below keep arriving; each one
changes an existing row of table `{table}` ({description}). Code (not a model) must find that row and set the same new
values the examples show.
Columns: {columns}

Examples (message, the row it changed, the new values):
{examples}

Write Python regexes (case-insensitive, re.search) with named groups. For each pattern give:
- "key": the column that identifies the row and where its value is in the message, e.g. {{"column": "ticket_number",
  "source": "group:num"}} for "#1087 resolved". A key on a link column (ending in _id) captures the linked thing's
  name, e.g. the company, and code finds the one row linked to it.
- "values": for every changed column, its source, as for inserts: "group:NAME", "const:VALUE" (a fixed value, e.g.
  status "resolved" when every matched message means that), "date", "date:group:NAME", "clock:group:NAME",
  "hours:group:NAME", "minutes:group:NAME". For a link column, capture the name.
Capture names with general sub-patterns anchored by the surrounding words; never list names from the examples. Make
patterns specific to this kind of message, so they don't match messages that create new rows."""


def _num(text: str):
    t = re.sub(r"[^\d.,]", "", text or "")
    if not t:
        return None
    if "," in t and "." in t:
        t = t.replace(",", "")
    elif "," in t:
        t = t.replace(",", "" if re.search(r",\d{3}$", t) else ".")
    try:
        return float(t)
    except ValueError:
        return None


def _date(text: str):
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", text or "")
    if m:
        return f"{m[3]}-{int(m[2]):02d}-{int(m[1]):02d}"
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", text or "")
    if m:
        return f"{m[3]}-{int(m[1]):02d}-{int(m[2]):02d}"
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text or "")
    return m.group(0) if m else None


def _fold(text: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode().strip().lower()


def _hours(text: str):
    t = (text or "").lower()
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*h(?:ours?|rs?)?\s*(?:(\d+)\s*m)?", t) or re.search(r"(\d+):(\d{2})", t)
    if not m:
        n = _num(t)
        return n
    h = float(m.group(1).replace(",", "."))
    return round(h + (int(m.group(2)) / 60 if m.group(2) else 0), 4)


def _clock(text: str):
    parts = [int(p) for p in re.findall(r"\d+", text or "")]
    return sum(p * 60 ** i for i, p in enumerate(reversed(parts))) if parts else None


class CompiledWriter:
    def __init__(self, engine, model: str = "openai:gpt-6-luna"):
        self.eng, self.model = engine, model
        self.patterns: dict[str, list[dict]] = {}   # table -> kept patterns
        self.compiled_at: dict[str, int] = {}       # table -> rows it had when last compiled
        self.report: list[dict] = []
        self._vocab_cache: dict = {}
        self.jev_choices = 0
        self.planner_logs: set[int] | None = None   # log ids the LLM planner handled (set by the caller)
        self.aliases: dict = {}                     # (table, link column) -> {folded nickname: linked id}
        # writer v2.5 (LLP 0016): learn from the planner's operations, not from the rows as they are now, so rows that
        # later messages update (tickets, deals) still yield insert examples, and single-row updates yield update patterns
        self.use_ops = False
        self.planner_ops: dict[int, list] = {}      # log id -> the operations the planner applied (set by the caller)
        self.update_patterns: dict[str, list[dict]] = {}

    # ------------------------------------------------------------ examples from the planner's own writes
    def examples(self, limit_per_table: int = 400) -> dict[str, list[dict]]:
        """Clean (message -> one row) pairs, taken only from rows the LLM planner wrote (never from compiled writes)."""
        db = self.eng.db
        srcs: dict[int, list[tuple[str, dict]]] = {}
        for t in self.eng.tables():
            cols = [r[1] for r in db.execute(f"PRAGMA table_info('{t}')")]
            for row in db.execute(f"SELECT * FROM '{t}' ORDER BY id DESC").fetchall():
                d = dict(zip(cols, row))
                ids = [int(x) for x in re.findall(r"\d+", str(d.get("_src") or ""))]
                if len(ids) == 1 and (self.planner_logs is None or ids[0] in self.planner_logs) and \
                        sum(1 for x in srcs.values() for tt, _ in x if tt == t) < limit_per_table:
                    srcs.setdefault(ids[0], []).append((t, {k: v for k, v in d.items() if k not in ("id", "_src", "_ts") and v is not None}))
        out: dict[str, list[dict]] = {}
        for log_id, rows in srcs.items():
            if len(rows) != 1:
                continue  # only messages that produced exactly one row are clean examples
            ts, text = db.execute("SELECT ts, text FROM _log WHERE id = ?", (log_id,)).fetchone()
            out.setdefault(rows[0][0], []).append({"text": text, "ts": ts, "row": rows[0][1]})
        return out

    def examples_from_ops(self, kind: str) -> dict[str, list[dict]]:
        """(message -> one insert) or (message -> one update of an existing row), from what the planner did."""
        db, out = self.eng.db, {}
        for lid, ops in sorted(self.planner_ops.items()):
            ops = [o for o in ops or [] if o.get("op") in ("insert", "update", "delete", "forget")]
            if len(ops) != 1 or ops[0]["op"] != kind:
                continue
            o = ops[0]
            vals = {v["column"]: v["value"] for v in o.get("values") or [] if not str(v.get("value", "")).startswith("@")}
            if not vals or o.get("table") not in self.eng.tables():
                continue
            got = db.execute("SELECT ts, text FROM _log WHERE id = ?", (lid,)).fetchone()
            if got:
                out.setdefault(o["table"], []).append({"text": got[1], "ts": got[0], "row": vals, "row_id": o.get("row_id")})
        return out

    # ------------------------------------------------------------ compile + validate
    def compile(self, min_examples: int = 12, tables: list[str] | None = None, rounds: int = 3,
                target: float = 0.9) -> list[dict]:
        """Test-driven: each round shows the LLM the examples no kept pattern covers yet; validated patterns accumulate."""
        self._vocab_cache = {}
        ex = self.examples_from_ops("insert") if self.use_ops else self.examples()
        done = self._compile_inserts(ex, min_examples, tables, rounds, target)
        if self.use_ops:
            done += self.compile_updates(min_examples, tables, rounds, target, negatives=ex)
        return done

    def _compile_inserts(self, ex, min_examples, tables, rounds, target) -> list[dict]:
        negatives = {t: [e["text"] for e in v] for t, v in ex.items()}
        if self.use_ops:   # messages that updated a row are negatives for insert patterns too
            for t, v in self.examples_from_ops("update").items():
                negatives.setdefault(f"update:{t}", []).extend(e["text"] for e in v)
        done = []
        for t, items in sorted(ex.items(), key=lambda kv: -len(kv[1])):
            if len(items) < min_examples or (tables is not None and t not in tables):
                continue
            proposed = kept_now = 0
            feedback: list[str] = []
            for _ in range(rounds):
                todo = [e for e in items if not any(p["_re"].search(e["text"]) for p in self.patterns.get(t, []))]
                if len(todo) < max(3, (1 - target) * len(items)):
                    break
                shown = todo[:12] + todo[-18:] if len(todo) > 30 else todo   # oldest and newest phrasings
                out, _ = chat_json(self.model, self._prompt(t, shown, feedback), SCHEMA, schema_name="patterns",
                                   tag="compile", effort="low", max_tokens=6000)
                proposed += len(out["patterns"])
                feedback = []
                for p in out["patterns"]:
                    verdict = self._validate(t, p, items, [x for k, v in negatives.items() if k != t for x in v])
                    self.report.append({"table": t, "regex": p["regex"], **verdict})
                    if not verdict["keep"]:
                        why = verdict.get("why") or (f"matched {verdict['matched']} messages but only {verdict['exact']} "
                                                     f"rows came out right; wrong ones: " + " | ".join(verdict["wrong"]) if verdict["matched"]
                                                     else "matched none of the examples")
                        if verdict.get("false_hits"):
                            why += f"; it also matched {verdict['false_hits']} messages of other kinds"
                        feedback.append(f"- {p['regex']}\n  values: {json.dumps(p['values'])}\n  REJECTED: {why}")
                    if verdict["keep"] and p["regex"] not in {k["regex"] for k in self.patterns.get(t, [])}:
                        self.patterns.setdefault(t, []).append({**p, "_re": re.compile(p["regex"], re.I)})
                        kept_now += 1
            covered = sum(1 for e in items if any(p["_re"].search(e["text"]) for p in self.patterns.get(t, [])))
            self.compiled_at[t] = len(items)
            done.append({"table": t, "examples": len(items), "proposed": proposed, "kept": kept_now,
                         "coverage": round(covered / len(items), 3)})
        return done

    def compile_updates(self, min_examples, tables, rounds, target, negatives: dict) -> list[dict]:
        """Update patterns: which captured value identifies the row (the key), and the new values."""
        ex = self.examples_from_ops("update")
        neg_all = {t: [e["text"] for e in v] for t, v in negatives.items()}
        done = []
        for t, items in sorted(ex.items(), key=lambda kv: -len(kv[1])):
            if len(items) < min_examples or (tables is not None and t not in tables):
                continue
            proposed = kept_now = 0
            feedback: list[str] = []
            for _ in range(rounds):
                todo = [e for e in items if not any(p["_re"].search(e["text"]) for p in self.update_patterns.get(t, []))]
                if len(todo) < max(3, (1 - target) * len(items)):
                    break
                shown = todo[:12] + todo[-18:] if len(todo) > 30 else todo
                out, _ = chat_json(self.model, self._update_prompt(t, shown, feedback), UPDATE_SCHEMA, schema_name="patterns",
                                   tag="compile:update", effort="low", max_tokens=6000)
                proposed += len(out["patterns"])
                feedback = []
                others = [x for k, v in neg_all.items() for x in v] + [e["text"] for k, v in ex.items() if k != t for e in v]
                for p in out["patterns"]:
                    verdict = self._validate_update(t, p, items, others)
                    self.report.append({"table": t, "update": True, "regex": p["regex"], **verdict})
                    if not verdict["keep"]:
                        feedback.append(f"- {p['regex']}\n  key: {json.dumps(p['key'])}\n  REJECTED: " + (verdict.get("why") or
                                        f"matched {verdict['matched']}, right {verdict['exact']}; wrong: " + " | ".join(verdict["wrong"])))
                    elif p["regex"] not in {k["regex"] for k in self.update_patterns.get(t, [])}:
                        self.update_patterns.setdefault(t, []).append({**p, "_re": re.compile(p["regex"], re.I)})
                        kept_now += 1
            covered = sum(1 for e in items if any(p["_re"].search(e["text"]) for p in self.update_patterns.get(t, [])))
            done.append({"table": t, "update": True, "examples": len(items), "proposed": proposed, "kept": kept_now,
                         "coverage": round(covered / len(items), 3)})
        return done

    def _update_prompt(self, t: str, shown: list[dict], feedback: list[str] | None = None) -> str:
        desc = self.eng.db.execute("SELECT description FROM _tables WHERE name = ?", (t,)).fetchone()[0]
        cols = "; ".join(f"{c['name']} ({c.get('type')})" for c in self.eng.columns(t) if c["name"] not in ("id", "_src", "_ts"))
        lines = []
        for e in shown:
            row = self.eng.db.execute(f"SELECT * FROM '{t}' WHERE id = ?", (e["row_id"],)).fetchone()
            ident = {k: row[k] for k in row.keys() if k not in ("_src", "_ts") and row[k] is not None} if row else {}
            ident = {k: v for k, v in ident.items() if k not in e["row"]}
            lines.append(f"- MESSAGE (sent {e['ts'][:16]}): {json.dumps(e['text'], ensure_ascii=False)}\n  UPDATED ROW "
                         f"(its other columns): {json.dumps(ident, ensure_ascii=False, default=str)[:400]}\n  NEW VALUES: "
                         f"{json.dumps(e['row'], ensure_ascii=False, default=str)}")
        prompt = UPDATE_PROMPT.format(table=t, description=desc, columns=cols, examples="\n".join(lines))
        if feedback:
            prompt += "\n\nYour previous patterns were tested and rejected. Fix what the test found:\n" + "\n".join(feedback[:5])
        return prompt

    def _find(self, table: str, key: dict, m: re.Match) -> int | None:
        """The one row the key identifies, or None: a captured number or name matched against the key column (a link
        column matches through the linked table's names)."""
        col, g = key["column"], key["source"][6:] if key["source"].startswith("group:") else None
        raw = (m.group(g) if g and g in m.re.groupindex else None) or ""
        raw = raw.strip().lstrip("#")
        if not raw or col not in {c["name"] for c in self.eng.columns(table)}:
            return None
        if col.endswith("_id"):
            target_id = self._link(table, col, raw)
            if target_id is None:
                return None
            ids = [r[0] for r in self.eng.db.execute(f"SELECT id FROM '{table}' WHERE \"{col}\" = ?", (target_id,))]
        else:
            num = _num(raw) if re.fullmatch(r"[\d.,]+", raw) else None
            ids = [r[0] for r in self.eng.db.execute(f"SELECT id FROM '{table}' WHERE lower(CAST(\"{col}\" AS TEXT)) = lower(?) "
                                                      f"OR (\"{col}\" = ?)", (raw, num))]
        return ids[0] if len(ids) == 1 else None

    def _validate_update(self, table: str, p: dict, items: list[dict], negatives: list[str]) -> dict:
        try:
            rx = re.compile(p["regex"], re.I)
        except re.error as e:
            return {"keep": False, "why": f"bad regex: {e}", "wrong": [], "matched": 0, "exact": 0}
        check = self._validate(table, {"regex": p["regex"], "values": p["values"]}, [], [])
        if check.get("why"):
            return {"keep": False, "why": check["why"], "wrong": [], "matched": 0, "exact": 0}
        key = {c["name"] for c in self.eng.columns(table) if self._key_column(c)} | {"status", "priority", "stage"}
        matched = exact = 0
        wrong: list[str] = []
        for e in items:
            m = rx.search(e["text"])
            if not m:
                continue
            matched += 1
            rid = self._find(table, p["key"], m)
            vals = self._build(table, {"values": p["values"]}, m, e["text"], e["ts"])
            ok = rid == e["row_id"] and vals is not None and all(
                self._same(vals.get(c), v) for c, v in e["row"].items() if c in key) and set(vals or {}) >= (set(e["row"]) & key)
            exact += ok
            if not ok and len(wrong) < 3:
                wrong.append(f"{json.dumps(e['text'], ensure_ascii=False)}: row {rid} (want {e['row_id']}), values "
                             f"{json.dumps(vals, ensure_ascii=False, default=str)} (want {json.dumps(e['row'], ensure_ascii=False, default=str)})")
        false_hits = sum(1 for x in negatives if rx.search(x))
        keep = matched >= 3 and exact / matched >= 0.9 and false_hits <= max(1, len(negatives) // 200)
        return {"keep": keep, "matched": matched, "exact": exact, "false_hits": false_hits, "wrong": wrong}

    def _prompt(self, t: str, shown: list[dict], feedback: list[str] | None = None) -> str:
        desc = self.eng.db.execute("SELECT description FROM _tables WHERE name = ?", (t,)).fetchone()[0]
        cols = "; ".join(f"{c['name']} ({c.get('type')}): {c.get('description') or ''}" for c in self.eng.columns(t)
                         if c["name"] not in ("id", "_src", "_ts"))
        prompt = PROMPT.format(table=t, description=desc, columns=cols, examples="\n".join(
            f"- MESSAGE (sent {e['ts'][:16]}): {json.dumps(e['text'], ensure_ascii=False)}\n  ROW: {json.dumps(e['row'], ensure_ascii=False)}"
            for e in shown))
        if feedback:
            prompt += ("\n\nYour previous patterns for these messages were tested against the stored rows and rejected. "
                       "Fix what the test found:\n" + "\n".join(feedback[:5]))
        return prompt

    # @ref LLP 0005#pattern-validation — only key columns must match exactly; the planner's free text is inconsistent
    def _validate(self, table: str, p: dict, items: list[dict], negatives: list[str]) -> dict:
        try:
            rx = re.compile(p["regex"], re.I)
        except re.error as e:
            return {"keep": False, "why": f"bad regex: {e}", "wrong": []}
        problems = []
        for v in p["values"]:
            src = v["source"]
            if not re.fullmatch(r"group:\w+|const:.*|choice|date|(date|datetime|clock|hours|minutes):group:\w+", src, re.S):
                problems.append(f"column {v['column']}: unknown source {src!r}")
            elif "group:" in src:
                g = src.split("group:")[1]
                if g not in rx.groupindex and not any(re.fullmatch(rf"{re.escape(g)}_?\d+", x) for x in rx.groupindex):
                    problems.append(f"column {v['column']}: group {g!r} is not in the regex (its groups: {', '.join(rx.groupindex) or 'none'})")
            if v["column"] not in {c["name"] for c in self.eng.columns(table)}:
                problems.append(f"column {v['column']!r} does not exist in {table}")
        if problems:
            return {"keep": False, "why": "; ".join(problems), "wrong": [], "matched": 0, "exact": 0, "false_hits": 0}
        # the columns the planner fills for (almost) every message of this kind: a pattern must produce all of them
        counts: dict = {}
        for e in items:
            for c in e["row"]:
                counts[c] = counts.get(c, 0) + 1
        key = {c["name"] for c in self.eng.columns(table) if self._key_column(c)}
        core = {c for c, n in counts.items() if n >= 0.8 * len(items)} & key   # free text (descriptions) is optional
        self._learn_aliases(table, p, rx, items)
        matched = exact = covered = 0
        wrong: list[str] = []
        for e in items:
            m = rx.search(e["text"])
            if not m:
                continue
            matched += 1
            row = self._build(table, p, m, e["text"], e["ts"])
            if row is None:
                if len(wrong) < 3:
                    wrong.append(f"{json.dumps(e['text'], ensure_ascii=False)}: could not build a row (unknown linked name or bad capture)")
                continue
            # key columns must agree exactly; free-text columns (item, notes) only need to be filled
            same = all(self._same(v, e["row"][c]) for c, v in row.items() if c in e["row"] and c in key) and \
                (core & set(e["row"])) <= set(row)
            exact += same
            covered += len(set(row) & set(e["row"])) / max(1, len(e["row"]))
            if not same and len(wrong) < 3:
                diffs = {c: {"got": row.get(c), "want": e["row"][c]} for c in e["row"]
                         if (c in key and c in row and not self._same(row[c], e["row"][c])) or (c in core and c not in row)}
                wrong.append(f"{json.dumps(e['text'], ensure_ascii=False)}: {json.dumps(diffs, ensure_ascii=False)}")
        false_hits = sum(1 for x in negatives if rx.search(x))
        keep = matched >= 3 and exact / matched >= 0.9 and false_hits <= max(1, len(negatives) // 200)
        return {"keep": keep, "matched": matched, "exact": exact, "false_hits": false_hits,
                "coverage": round(covered / matched, 2) if matched else 0, "wrong": wrong}

    @staticmethod
    def _key_column(c: dict) -> bool:
        """Columns whose value must come out exactly: numbers, dates, currency, and the kind/identity of the row."""
        name, typ = c["name"], (c.get("type") or "").upper()
        return typ in ("REAL", "INTEGER", "DATE", "DATETIME") or "currency" in name or "date" in name or name.endswith("_id") or \
            name in ("category", "type", "kind", "merchant", "name", "title", "place", "gym", "store", "vendor", "companion")

    @staticmethod
    def _same(a, b) -> bool:
        if a is None or b is None:
            return a is None and b is None
        if isinstance(b, (int, float)) and not isinstance(b, bool):
            return isinstance(a, (int, float)) and abs(a - b) < 0.011
        from lab.systems.engine import CURRENCY
        sa, sb = str(a).strip().lower(), str(b).strip().lower()
        return sa == sb or CURRENCY.get(sa, sa) == CURRENCY.get(sb, sb) or (len(sb) >= 10 and sa[:10] == sb[:10])

    def _build(self, table: str, p: dict, m: re.Match, text: str, ts: str, jev_ok: bool = False) -> dict | None:
        row, pending = {}, []
        for v in p["values"]:
            src, col = v["source"], v["column"]
            try:
                if src.startswith("group:"):
                    name = src[6:]
                    raw = m.group(name) if name in m.re.groupindex else None
                    if raw is None:  # alternative branches: NAME_2, NAME2, ...
                        raw = next((m.group(g) for g in m.re.groupindex if g != name and re.fullmatch(
                            rf"{re.escape(name)}_?\d+", g) and m.group(g) is not None), None)
                    num = _num(raw) if raw is not None else None
                    if col.endswith("_id") and raw:
                        row[col] = self._link(table, col, raw)
                        if row[col] is None:
                            return None   # an unknown linked thing: the planner has to create it
                    elif self._numeric(table, col):
                        row[col] = num
                    else:
                        row[col] = self._canonical(table, col, raw.strip()) if raw else None
                elif src == "choice":
                    pending.append(col)
                elif src.startswith("const:"):
                    c = src[6:]
                    row[col] = _num(c) if re.fullmatch(r"[\d.,]+", c) else c
                elif src == "date":
                    d = datetime.fromisoformat(ts).date()
                    row[col] = (d - timedelta(days=1) if re.search(r"\byesterday\b", text, re.I) else d).isoformat()
                elif src.startswith("date:group:"):
                    row[col] = _date(m.group(src[11:]))
                elif src.startswith("datetime:group:"):
                    raw = m.group(src[15:]) or ""
                    day, hm = _date(raw), re.search(r"\b(\d{1,2}):(\d{2})\b", raw)
                    row[col] = f"{day}T{int(hm[1]):02d}:{hm[2]}" if day and hm else day
                elif src.startswith("clock:group:"):
                    row[col] = _clock(m.group(src[12:]))
                elif src.startswith("hours:group:"):
                    row[col] = _hours(m.group(src[12:]))
                elif src.startswith("minutes:group:"):
                    h = _hours(m.group(src[14:]))
                    row[col] = round(h * 60, 2) if h is not None else None
            except (IndexError, ValueError, TypeError, AttributeError, re.error):
                return None
        row = {k: v for k, v in row.items() if v is not None}
        for col in pending:   # derived columns: what the same merchant/place had before, else a Jev choice
            v = self._associated(table, col, row)
            if v is None and jev_ok:
                v = self._choose(table, col, text)
            if v is None and jev_ok:
                return None
            if v is not None:
                row[col] = v
        return row

    # @ref LLP 0005#choice-columns — what >= 90% of earlier rows with the same merchant have, else a Jev Choice
    def _associated(self, table: str, col: str, row: dict):
        """The value `col` had in >= 90% of earlier rows sharing this row's merchant/place/name (at least 3 rows)."""
        for anchor in ("merchant", "place", "place_id", "name", "title", "store", "vendor"):
            if anchor in row and anchor != col:
                got = self.eng.db.execute(f"SELECT \"{col}\", COUNT(*) FROM '{table}' WHERE \"{anchor}\" = ? AND \"{col}\" IS NOT NULL "
                                          f"GROUP BY 1 ORDER BY 2 DESC", (row[anchor],)).fetchall()
                total = sum(n for _, n in got)
                if got and total >= 3 and got[0][1] >= 0.9 * total:
                    return got[0][0]
        return None

    def _choose(self, table: str, col: str, text: str):
        from lab.common import jev
        vals = [r[0] for r in self.eng.db.execute(f"SELECT \"{col}\" FROM '{table}' WHERE \"{col}\" IS NOT NULL "
                                                    f"GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 25")]
        if not vals:
            return None
        opts = {f"v{i}": str(v) for i, v in enumerate(vals)}
        a = jev.ask({"message": text, "table": table}, {"pick": jev.choice(f"Which {col} of {table} fits `message`?", opts)},
                    tag="compiled:choice")["pick"]
        self.jev_choices += 1
        return vals[int(a["choice"][1:])] if a["confidence"] >= 0.6 else None

    def _canonical(self, table: str, col: str, text: str) -> str:
        """A captured name -> the known value it contains ("Note: Blue Bottle" -> "Blue Bottle"), else the capture."""
        vocab = self._vocab_cache.get(table)
        if vocab is None:
            vocab = self._vocab_cache[table] = self._vocab(table)
        values = vocab.get(col)
        if not values:
            return text
        low = _fold(text)
        if low in values:
            return values[low]
        inside = [v for k, v in values.items() if len(k) >= 3 and re.search(rf"(?<!\w){re.escape(k)}(?!\w)", low)]
        return max(inside, key=len) if inside else text

    # @ref LLP 0005#nicknames — known issue: an early planner mistake gets learned (Eva -> Lucie)
    def _learn_aliases(self, table: str, p: dict, rx: re.Pattern, items: list[dict]):
        """Nicknames from the planner's own rows: when a captured name ("Eva", "Kuba") always went with the same linked
        id in the examples (at least twice), code can link it too."""
        fk = {v["column"]: v["source"][6:] for v in p["values"] if v["column"].endswith("_id") and v["source"].startswith("group:")}
        seen: dict = {}
        for e in items:
            m = rx.search(e["text"])
            for col, g in fk.items():
                if m and g in rx.groupindex and m.group(g) and col in e["row"]:
                    seen.setdefault((col, _fold(m.group(g))), Counter())[e["row"][col]] += 1
        for (col, name), ids in seen.items():
            (best, n), total = ids.most_common(1)[0], sum(ids.values())
            if n >= 2 and n == total:
                self.aliases.setdefault((table, col), {})[name] = best

    def _link(self, table: str, col: str, text: str):
        """A captured name -> the id of the matching row in the linked table (None if unknown or ambiguous)."""
        target = self.eng.target_table(col, self.eng.tables())
        if not target:
            return text
        alias = self.aliases.get((table, col), {}).get(_fold(text))
        if alias is not None:
            return alias
        label = next((c for c in ("name", "title", "full_name") if c in [x["name"] for x in self.eng.columns(target)]), None)
        if not label:
            return None
        ids = [r[0] for r in self.eng.db.execute(f"SELECT id FROM '{target}' WHERE lower({label}) = lower(?)", (text.strip(),))]
        return ids[0] if len(ids) == 1 else None

    def _numeric(self, table: str, col: str) -> bool:
        return any(c["name"] == col and (c.get("type") or "").upper() in ("REAL", "INTEGER") for c in self.eng.columns(table))

    # ------------------------------------------------------------ runtime
    def match(self, text: str) -> tuple[str, dict, re.Match, str] | None:
        hits = []
        for kind, pats in (("insert", self.patterns), ("update", self.update_patterns)):
            for t, ps in pats.items():
                for p in ps:
                    m = p["_re"].search(text)
                    if m:
                        hits.append((t, p, m, kind))
                        break
        return hits[0] if len(hits) == 1 else None   # two kinds match: ambiguous, let the planner decide

    def plan(self, msg: dict) -> dict | None:
        """The insert or update a compiled pattern would make for this message, or None (the planner handles it)."""
        if not isinstance(msg["text"], str):
            return None
        hit = self.match(msg["text"])
        if not hit:
            return None
        t, p, m, kind = hit
        if kind == "update":
            rid = self._find(t, p["key"], m)
            vals = self._build(t, {"values": p["values"]}, m, msg["text"], msg["ts"], jev_ok=True) if rid else None
            return {"op": "update", "table": t, "row_id": rid, "row": vals} if vals else None
        row = self._build(t, p, m, msg["text"], msg["ts"], jev_ok=True)
        return {"op": "insert", "table": t, "row": row} if row else None

    def apply(self, plan: dict, log_id: int, ts: str) -> list:
        return self.eng.apply([{"op": plan.get("op", "insert"), "table": plan["table"], "description": None, "columns": [],
                                "row_id": plan.get("row_id"), "ref": None,
                                "values": [{"column": c, "value": v} for c, v in plan["row"].items()], "redact": []}], log_id, ts)

    def _vocab(self, t: str) -> dict[str, dict[str, str]]:
        out = {}
        for c in self.eng.columns(t):
            if (c.get("type") or "").upper() == "TEXT":
                vals = [r[0] for r in self.eng.db.execute(f"SELECT DISTINCT \"{c['name']}\" FROM '{t}' WHERE \"{c['name']}\" IS NOT NULL LIMIT 300")]
                if 0 < len(vals) < 300:
                    out[c["name"]] = {_fold(v): v for v in vals if isinstance(v, str)}
        return out

