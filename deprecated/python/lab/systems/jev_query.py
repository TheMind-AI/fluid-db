"""Query FluidDB without an LLM at question time.

compile (once per schema version, LLM):
    read the catalog and write a library of parameterized SQL templates. Every parameter is a
    closed set the database itself provides: a row of an entity table (person, place, ...), a
    distinct value of a column, or a time period generated from "now".
route (every question, Jev, one request):
    one Choice picks the template, and one Choice per parameter kind picks its value from the
    real options (TypeSafe's function-calling pattern: speculative fan-out, closed sets only).
execute (SQL):
    bind the chosen values and run the template. No LLM on the query path.

If Jev picks "none" or is unsure, the caller falls back to an LLM path.
"""
# @ref LLP 0006#earlier-readers — Jev-routed SQL templates (round 1), replaced by the closed query form
from __future__ import annotations

import calendar
import json
import sqlite3
from datetime import date, datetime, timedelta

from lab.common import jev
from lab.common.llm import chat_json

LIB_SCHEMA = {"type": "object", "properties": {"templates": {"type": "array", "items": {
    "type": "object", "properties": {
        "id": {"type": "string"},
        "description": {"type": "string"},
        "sql": {"type": "string"},
        "params": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"},
            "kind": {"type": "string", "description": "entity:<table> | enum:<table>.<column> | period"}},
            "required": ["name", "kind"], "additionalProperties": False}}},
    "required": ["id", "description", "sql", "params"], "additionalProperties": False}}},
    "required": ["templates"], "additionalProperties": False}

COMPILE_PROMPT = """You design the query library for a personal SQLite database that an AI assistant keeps for {user}.
At question time a small classifier will pick ONE of your templates and fill its parameters; it cannot write SQL.
So write 20-30 parameterized SELECT templates that together answer the questions a person typically asks about
their own life data: facts about a person (contacts, birthday, relationships, allergies, interests, gifts),
where they live, their plans and meetings in a time period, spending in a period (total, by merchant or kind),
activities (counts, bests, latest) in a period, books, vehicles, tasks, organizations and funding, emails.

Rules:
- Parameters use SQLite named syntax (:name). Allowed kinds, and nothing else:
    entity:<table>          the id of one row of <table> (the classifier picks the row by its name/title)
    enum:<table>.<column>   one distinct existing value of that text column
    period                  binds TWO parameters, :start and :end (ISO dates, inclusive); declare it once as
                            {{"name": "period", "kind": "period"}}
- Any parameter may be NULL, meaning "no filter": write conditions like (:person IS NULL OR x.person_id = :person).
- Always return readable columns (join names/titles), whole relevant rows, and current values only when a status
  column says a row was cancelled/sold/deleted (still return the status so a reader can tell).
- Prefer broad templates (one "everything about a person" template beats ten narrow ones) and aggregates for
  money and counts (SUM/COUNT/MIN/MAX with GROUP BY currency where amounts are involved).
- Descriptions must say plainly what question the template answers.

DATABASE CATALOG (with example rows):
{catalog}

DISTINCT VALUES OF SHORT TEXT COLUMNS:
{enums}"""


def labels_for(db: sqlite3.Connection, table: str) -> dict[str, str]:
    """{row id: readable label} for an entity table (name/title plus a little context)."""
    cols = [r[1] for r in db.execute(f"PRAGMA table_info('{table}')")]
    key = next((c for c in ("name", "title", "full_name", "gift_idea", "interest", "description") if c in cols), None)
    out = {}
    for row in db.execute(f"SELECT * FROM '{table}'"):
        d = dict(zip(cols, row))
        extra = ", ".join(f"{k}: {v}" for k, v in d.items() if k not in ("id", key, "_src", "_ts") and v is not None)
        out[str(d["id"])] = f"{d.get(key) or ''} ({extra[:120]})" if extra else str(d.get(key) or d["id"])
    return out


def periods(now: datetime, v2: bool = False) -> dict[str, tuple[str, str, str]]:
    """Time periods relative to now: {option id: (label, start, end)}."""
    today = now.date()
    monday = today - timedelta(days=today.weekday())
    this_week = (f"this week, the coming days, 'this coming week' (Mon {monday:%b %d} - Sun {monday + timedelta(6):%b %d})"
                 if v2 else f"this week (Mon {monday:%b %d} - Sun {monday + timedelta(6):%b %d})")
    next_week = (f"next week, the week after this one (Mon {monday + timedelta(7):%b %d} - Sun {monday + timedelta(13):%b %d})"
                 if v2 else f"next week (Mon {monday + timedelta(7):%b %d} - Sun {monday + timedelta(13):%b %d})")
    out = {
        "today": (f"today ({today:%a %b %d})", today, today),
        "this_week": (this_week, monday, monday + timedelta(6)),
        "next_7_days": (f"the coming 7 days ({today:%b %d} - {today + timedelta(7):%b %d})", today, today + timedelta(7)),
        "next_week": (next_week, monday + timedelta(7), monday + timedelta(13)),
        "last_7_days": (f"the last 7 days ({today - timedelta(7):%b %d} - {today:%b %d})", today - timedelta(7), today),
        "future": (f"anything from today on (after {today:%b %d})", today, date(2100, 1, 1)),
        "past": (f"anything before today ({today:%b %d})", date(1900, 1, 1), today),
    }
    for m in range(1, 13):
        last = calendar.monthrange(today.year, m)[1]
        out[f"m{m:02d}"] = (f"{calendar.month_name[m]} {today.year}", date(today.year, m, 1), date(today.year, m, last))
    return {k: (label, s.isoformat(), e.isoformat()) for k, (label, s, e) in out.items()}


def enum_values(db: sqlite3.Connection, table: str, column: str, limit: int = 60) -> list[str]:
    return [r[0] for r in db.execute(f"SELECT DISTINCT \"{column}\" FROM '{table}' WHERE \"{column}\" IS NOT NULL LIMIT {limit}")]


class QueryLibrary:
    def __init__(self, db_path: str, user: str, v2: bool = False):
        """v2: the user's own row is labelled "I/me/my", clearer week labels, several people per question
        (one Noul per entity, TypeSafe's set pattern), and the top-2 templates' rows merged."""
        self.db_path = db_path
        self.user = user
        self.v2 = v2
        self.templates: list[dict] = []

    def ro(self):
        return sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)

    # ------------------------------------------------------------ compile
    def compile(self, catalog: str, model: str = "anthropic:claude-sonnet-5", tag: str = "jq:compile") -> list[dict]:
        db = self.ro()
        enums = []
        for (t,) in db.execute("SELECT name FROM _tables"):
            for (c, ctype) in db.execute("SELECT column_name, type FROM _columns WHERE table_name = ?", (t,)):
                if ctype == "TEXT":
                    vals = enum_values(db, t, c, limit=25)
                    if vals and max(len(str(v)) for v in vals) <= 40:
                        enums.append(f"{t}.{c}: {vals}")
        out, _ = chat_json(model, COMPILE_PROMPT.format(user=self.user, catalog=catalog, enums="\n".join(enums)),
                           LIB_SCHEMA, schema_name="library", tag=tag, effort="medium", max_tokens=32000)
        self.templates = [t for t in out["templates"] if self._valid(t, db)]
        return self.templates

    def _valid(self, t: dict, db) -> bool:
        """Keep templates whose SQL prepares and whose parameter kinds point at real tables/columns."""
        try:
            args = {}
            for p in t["params"]:
                if p["kind"] == "period":
                    args.update(start="2000-01-01", end="2100-01-01")
                else:
                    args[p["name"]] = None
            db.execute(t["sql"], args).fetchall()
            return all(self._options(p, db) is not None for p in t["params"])
        except Exception:
            return False

    def _options(self, p: dict, db) -> dict | None:
        kind = p["kind"]
        if kind == "period":
            return {}
        if kind.startswith("entity:"):
            table = kind.split(":", 1)[1]
            if not db.execute("SELECT 1 FROM _tables WHERE name=?", (table,)).fetchone():
                return None
            labels = labels_for(db, table)
            if self.v2:
                me = self.user.lower()
                labels = {k: (f"{v} (this is the user: 'I', 'me', 'my')" if v.lower().startswith(me) else v) for k, v in labels.items()}
            return labels
        if kind.startswith("enum:"):
            table, column = kind.split(":", 1)[1].split(".", 1)
            try:
                return {str(i): v for i, v in enumerate(enum_values(db, table, column))}
            except Exception:
                return None
        return None

    # -------------------------------------------------------------- route
    def questions(self, now: datetime) -> tuple[dict, dict]:
        """One Jev request: template choice + one choice per distinct parameter kind."""
        db = self.ro()
        qs = {"__template__": jev.choice(
            "Which saved query answers `question`?",
            {**{t["id"]: t["description"] for t in self.templates},
             "none": "None of these queries can answer the question"})}
        options: dict = {}
        for t in self.templates:
            for p in t["params"]:
                kind = p["kind"]
                if kind in options:
                    continue
                if kind == "period":
                    per = periods(now, self.v2)
                    options[kind] = {k: (s, e) for k, (_, s, e) in per.items()}
                    qs[kind] = jev.choice("Which time period does `question` ask about?",
                                          {**{k: label for k, (label, _, _) in per.items()},
                                           "none": "No specific time period, or all time"})
                else:
                    opts = self._options(p, db) or {}
                    options[kind] = opts
                    what = kind.split(":", 1)[1]
                    qs[kind] = jev.choice(f"Which {what.replace('.', ' ')} is `question` about?",
                                          {**{f"o{k}": v for k, v in opts.items()}, "none": "None of these / not specified"})
                    if self.v2 and kind.startswith("entity:"):
                        for k, v in opts.items():
                            qs[f"{kind}::{k}"] = jev.noul(f"Does `question` ask about {v}?")
        return qs, options

    def ask(self, question: str, now: datetime, *, min_conf: float = 0.3, tag: str = "jq:route", cache: bool = True) -> dict:
        qs, options = self.questions(now)
        ans = jev.ask({"question": question, "asked_at": now.strftime("%Y-%m-%d %H:%M (%A)")}, qs, tag=tag, cache=cache)
        pick = ans["__template__"]
        if pick["choice"] == "none" or pick["confidence"] < min_conf:
            return {"template": None, "confidence": pick["confidence"], "rows": None}
        chosen = [pick["choice"]]
        if self.v2:  # also run the runner-up when it is a real contender (multi-hop questions)
            ranked = sorted(((p, k) for k, p in pick["probabilities"].items() if k not in ("none", pick["choice"])), reverse=True)
            if ranked and ranked[0][0] >= 0.2:
                chosen.append(ranked[0][1])
        db = self.ro()
        rows, runs = [], []
        for tid in chosen:
            t = next(t for t in self.templates if t["id"] == tid)
            for args in self._bindings(t, ans, options):
                cur = db.execute(t["sql"], args)
                cols = [d[0] for d in cur.description]
                for r in cur.fetchall()[:200]:
                    row = {"_query": tid, **dict(zip(cols, r))}
                    if row not in rows:
                        rows.append(row)
                runs.append({"template": tid, "args": args})
        return {"template": pick["choice"], "confidence": pick["confidence"], "runs": runs, "rows": rows,
                "args": runs[0]["args"] if runs else None}

    def _bindings(self, t: dict, ans: dict, options: dict) -> list[dict]:
        """Parameter bindings for one template; v2 runs once per selected entity (several people per question)."""
        base: dict = {}
        multi: tuple[str, list] | None = None
        for p in t["params"]:
            a = ans[p["kind"]]["choice"]
            if p["kind"] == "period":
                start, end = options["period"].get(a, (None, None))
                base.update(start=start, end=end)
                continue
            if p["kind"].startswith("entity:"):
                picked = [int(k) for k in options[p["kind"]] if self.v2 and ans.get(f"{p['kind']}::{k}", {}).get("noul", 0) >= 0.5]
                if len(picked) > 1 and multi is None:
                    multi = (p["name"], picked)
                base[p["name"]] = picked[0] if len(picked) == 1 else (None if a == "none" else int(a[1:]))
            else:
                base[p["name"]] = None if a == "none" else options[p["kind"]][a[1:]]
        if multi:
            return [{**base, multi[0]: v} for v in multi[1]]
        return [base]
