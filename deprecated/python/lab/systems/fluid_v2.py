"""FluidDB v2: an LLM plans typed operations, a deterministic engine applies them.

Variants (the `variant` name picks the defaults, keyword options override):
  v2      LLM does everything. The planner sees the whole database; reads use a
          multi-step SQL agent.
  v2jev   Jev (System 1) makes the fast decisions: it gates messages that need
          no write, picks which existing rows the planner must see, and picks
          which rows answer a question. The LLM (System 2) only writes the
          operations and the final answer.
"""
# @ref LLP 0005 — the write path: log first, Jev gate and source guard, planner, repair round
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from lab.common.llm import chat_json
from lab.systems import jev_layer
from lab.systems.base import MemorySystem, render_input
from lab.systems.engine import Engine, bm25_rank

COLUMN = {"type": "object", "properties": {
    "name": {"type": "string"},
    "type": {"type": "string", "enum": ["TEXT", "INTEGER", "REAL", "BOOLEAN", "DATE", "DATETIME"]},
    "description": {"type": "string"}},
    "required": ["name", "type", "description"], "additionalProperties": False}
VALUE = {"type": "object", "properties": {
    "column": {"type": "string"},
    "value": {"type": ["string", "number", "boolean", "null"]}},
    "required": ["column", "value"], "additionalProperties": False}
OP = {"type": "object", "properties": {
    "op": {"type": "string", "enum": ["create_table", "add_columns", "insert", "update", "delete"]},
    "table": {"type": "string"},
    "description": {"type": ["string", "null"]},
    "columns": {"type": "array", "items": COLUMN},
    "row_id": {"type": ["integer", "null"]},
    "ref": {"type": ["string", "null"]},
    "values": {"type": "array", "items": VALUE}},
    "required": ["op", "table", "description", "columns", "row_id", "ref", "values"], "additionalProperties": False}
OPS_SCHEMA = {"type": "object", "properties": {
    "notes": {"type": "string", "description": "One or two sentences: what the input says and how you store it."},
    "ops": {"type": "array", "items": OP}},
    "required": ["notes", "ops"], "additionalProperties": False}

OP_V22 = json.loads(json.dumps(OP))
OP_V22["properties"]["op"]["enum"] = ["create_table", "add_columns", "insert", "update", "delete", "forget"]
OP_V22["properties"]["redact"] = {"type": "array", "items": {"type": "string"}}
OP_V22["required"] = OP["required"] + ["redact"]
OPS_SCHEMA_V22 = {"type": "object", "properties": {
    "notes": OPS_SCHEMA["properties"]["notes"],
    "ops": {"type": "array", "items": OP_V22}},
    "required": ["notes", "ops"], "additionalProperties": False}

# @ref LLP 0005#the-planner — the rules each planner version added, and the failure behind each
PLANNER_SYSTEM = """You are the storage engine of FluidDB, a self-organizing database that remembers everything {user} tells their AI assistant.
For each new input (a chat message, email, receipt, calendar invite, contact card, note...) you decide how the database changes, as a list of operations that the engine applies in order.

Operations (every field is required; use null / [] when a field does not apply):
- create_table: table, description (what one row is), columns [{{name, type, description}}]. `id INTEGER PRIMARY KEY` is added for you.
- add_columns: table, columns.
- insert: table, values [{{column, value}}], ref (a short name if later ops in this batch need this row's id, else null). Unknown columns are created automatically.
- update: table, row_id, values (only the columns that change).
- delete: table, row_id.
To point a foreign-key column at a row inserted earlier in the same batch, use the value "@<ref>".

Schema design:
- One table per kind of thing (people, organizations, events, expenses, books, workouts, tasks, places, ...), plural snake_case names. Reuse an existing table or column whenever it fits; never create a table for one entity (no `david_info`) or one fact.
- Link rows with `<entity>_id` columns that hold the other row's id.
- One value per column. Dates YYYY-MM-DD, datetimes YYYY-MM-DDTHH:MM, money as a number plus a currency column, phone numbers as written with country code if known.
- Give every new table and column a short description.

Keeping the data true:
- "I", "me", "my" refer to {user}. Keep facts about them in the database like any other person.
- The EXISTING ROWS below are what the database already holds that may relate to the input. If the input is about an entity that already has a row, update that row; never insert a duplicate. A first name or nickname refers to the existing person when that is the only plausible match.
- Corrections and changes ("actually", "changed", "moved to", "sold", "finished", "renewed") update the existing row: overwrite the value, or set a status column ('cancelled', 'sold', 'done', 'finished'). Keep the old value in another column only if it may matter later.
- "Forget ..." or "delete ..." means delete those rows. A cancelled plan: delete it or set status 'cancelled'.
- Resolve relative dates and times ("tonight", "next Friday", "this morning") against the input timestamp.
- Store every durable detail (who, what, where, when, amounts, ratings) so later questions can be answered. Store nothing for greetings, thanks, small talk, or questions the user asks: return no operations.
- Never invent facts that are not in the input."""

# v2.1: fixes for the failure modes seen in the first v2 runs (a single-valued
# column overwritten by a second fact of the same kind, "forget" leaving
# mentions behind, details of semi-structured inputs dropped).
PLANNER_SYSTEM_V21 = PLANNER_SYSTEM + """
- Never overwrite a value with a different fact of the same kind. Things a person can have several of (interests, hobbies, preferences, phone numbers, emails, allergies, gift ideas) get one row each in a table such as `interests` or `contact_methods`, linked by `person_id`. Overwrite only when the input says the old value changed or was wrong.
- "Forget X" removes X everywhere: delete X's rows and update other rows whose text mentions X so the mention is gone.
- For emails, receipts, invites and contact cards keep every field that could matter later: sender, recipients, subject, key terms and numbers in the body, date and time, location, amounts and line items."""

# v2.2: the engine keeps history itself and supports a privacy "forget".
PLANNER_SYSTEM_V22 = PLANNER_SYSTEM_V21.replace(
    "- delete: table, row_id.",
    "- delete: table, row_id (for things that ended or were cancelled; the engine keeps a copy in history).\n"
    "- forget: table, row_id, redact [terms] (only when the user asks to forget or erase something: removes the row "
    "with no history and erases each term, e.g. the person's name, from the raw message log). Use one forget per row.\n"
    "`redact` is [] for every other operation.",
).replace(
    "Keep the old value in another column only if it may matter later.",
    "Just write the new value: the engine records the old one in `_history` automatically.",
).replace(
    "- \"Forget X\" removes X everywhere: delete X's rows and update other rows whose text mentions X so the mention is gone.",
    "- \"Forget X\" removes X everywhere: forget X's rows (with X's name in redact) and update other rows whose text "
    "mentions X so the mention is gone.",
)

# v2.3: consistency at scale (what a simulated year exposed).
PLANNER_SYSTEM_V23 = PLANNER_SYSTEM_V22 + """
- Keep one kind of thing in one table. Before putting a row into a general table (like events), check whether a more
  specific existing table already holds this kind of thing (another workout, another expense, another meeting).
- When rows of a table come in recurring kinds (kinds of expenses, workouts, events, contacts), give the table a
  `category` column with short lowercase values and reuse the same values every time.
- Reuse the exact spelling of existing values shown in the catalog as [existing values: ...] (merchants, places,
  categories, statuses) instead of re-typing them from the input (`Billa`, not `BILLA`). Currencies are ISO codes."""

PLANNER_USER = """DATABASE CATALOG:
{catalog}

EXISTING ROWS ({row_note}):
{rows}

NEW INPUT (received {ts}, {weekday}):
{text}"""

READ_SCHEMA = {"type": "object", "properties": {
    "thought": {"type": "string"},
    "sql_queries": {"type": "array", "items": {"type": "string"}},
    "done": {"type": "boolean"}},
    "required": ["thought", "sql_queries", "done"], "additionalProperties": False}

READER_PROMPT = """You look up data in {user}'s personal SQLite database to answer a question.
Current datetime: {now} ({weekday}).

DATABASE CATALOG (with example rows):
{catalog}

QUESTION: {question}

STEPS SO FAR:
{steps}

Write up to 4 SELECT queries for the next step. Cast a wide net: select whole rows (SELECT *), match text case-insensitively with LIKE on partial terms, and check every table that could hold relevant data. Use joins or aggregates when they help. Once the results are enough to answer, or it is clear the data is not there, return done=true with no queries."""

VARIANTS = {
    "v2": {"gate": "none", "write_ctx": "full", "read": "sql", "planner": "v2"},
    "v2jev": {"gate": "jev", "write_ctx": "jev", "read": "jev", "planner": "v2"},
    # v2.1 = improved planner prompt; "hybrid" read = Jev-picked rows + SQL agent.
    "v21": {"gate": "none", "write_ctx": "full", "read": "sql", "planner": "v21"},
    "v21jev": {"gate": "jev", "write_ctx": "jev", "read": "hybrid", "planner": "v21"},
    # v2.2 = hardened for long horizons: history, forget, ref checks, retry, scalable write context, document guard.
    "v22": {"gate": "jev", "write_ctx": "scalable", "read": "full", "planner": "v22", "guard": True,
            "retry": True, "effort": "none", "strict": True},
    # v2.3 = v2.2 + consistency rules, value vocabulary, ISO currencies, forget cascade, readable reads.
    "v23": {"gate": "jev", "write_ctx": "scalable", "read": "readable", "planner": "v23", "guard": True,
            "retry": True, "effort": "low", "strict": True, "normalize": True},
    # v2.4 (round 8, LLP 0014): the write context's retrieval reads links as names, so "lost Humongous Insurance"
    # finds the deal row that only holds organization_id = 9 (which v2.3 missed, splitting deals across rows)
    "v24": {"gate": "jev", "write_ctx": "scalable", "read": "readable", "planner": "v23", "guard": True,
            "retry": True, "effort": "low", "strict": True, "normalize": True, "link_ctx": True},
    # v2.5 (round 9, LLP 0016): v2.4 + the gate worded for any domain + compiled update patterns in tier A
    "v25": {"gate": "jev", "write_ctx": "scalable", "read": "readable", "planner": "v23", "guard": True,
            "retry": True, "effort": "low", "strict": True, "normalize": True, "link_ctx": True,
            "gate_neutral": True, "compiled_updates": True},
    # LLP 0017: the person's own conversation windows are trusted input; the document guard would block their updates.
    # v25r also keeps the rows that link to a forgotten row, with the link nulled (LLP 0004#forget)
    "v25nog": {"gate": "jev", "write_ctx": "scalable", "read": "readable", "planner": "v23", "guard": False,
               "retry": True, "effort": "low", "strict": True, "normalize": True, "link_ctx": True,
               "gate_neutral": True, "compiled_updates": True},
    "v25r": {"gate": "jev", "write_ctx": "scalable", "read": "readable", "planner": "v23", "guard": False,
             "retry": True, "effort": "low", "strict": True, "normalize": True, "link_ctx": True,
             "gate_neutral": True, "compiled_updates": True, "forget_links": "redact"},
}


class FluidV2(MemorySystem):
    def __init__(self, model: str, db_path: str, user: str, variant: str = "v2", **opts):
        super().__init__(model, db_path, user)
        self.name = variant
        self.opts = {**VARIANTS.get(variant, VARIANTS["v2"]), **opts}
        self.engine = Engine(db_path, strict=self.opts.get("strict", False), normalize=self.opts.get("normalize", False),
                             forget_links=self.opts.get("forget_links", "cascade"))

    # ------------------------------------------------------------- writing
    def remember(self, message: dict, log_id: int | None = None) -> dict:
        """Log the message (unless the caller already did and passes its `log_id`), then gate, plan and apply."""
        text = render_input(message["text"])
        ts = message["ts"]
        log_id = log_id or self.engine.log(ts, text)
        trace: dict = {"errors": []}

        if self.opts["gate"] == "jev":
            # The gate and the user-vs-document check are independent Jev calls: run them together.
            with ThreadPoolExecutor(2) as pool:
                g_future = pool.submit(jev_layer.gate, text, ts, tag=f"{self.name}:gate", neutral=self.opts.get("gate_neutral", False))
                s_future = pool.submit(jev_layer.source, text, tag=f"{self.name}:gate") if self.opts.get("guard") else None
                g = g_future.result()
                if s_future:
                    trace["source"] = s_future.result()
            trace["gate"] = g
            if not g["write"]:
                trace["skipped"] = True
                return trace

        rows, note = self._write_context(text)
        trace["context_rows"] = len(rows)
        when = datetime.fromisoformat(ts)
        prompt = PLANNER_USER.format(catalog=self.engine.catalog(), rows=rows_text(rows) or "(none)", row_note=note,
                                     ts=when.strftime("%Y-%m-%d %H:%M"), weekday=when.strftime("%A"), text=text)
        planner = {"v21": PLANNER_SYSTEM_V21, "v22": PLANNER_SYSTEM_V22, "v23": PLANNER_SYSTEM_V23}.get(self.opts["planner"], PLANNER_SYSTEM)
        schema = OPS_SCHEMA_V22 if self.opts["planner"] in ("v22", "v23") else OPS_SCHEMA
        effort = self.opts.get("effort", "low")
        plan, _ = chat_json(self.model, prompt, schema, system=planner.format(user=self.user),
                            schema_name="operations", tag=f"{self.name}:write", max_tokens=16000, effort=effort)
        trace["notes"] = plan["notes"]
        ops = plan["ops"]
        if self.opts.get("guard"):
            ops = self._guard(text, ts, ops, trace)
        trace["ops"] = ops
        trace["errors"] = self.engine.apply(ops, log_id, ts)
        if trace["errors"] and self.opts.get("retry"):
            # One repair round: the successful ops are already applied; ask only for fixes to the failed ones.
            feedback = "\n".join(f"- {json.dumps(e['op'], ensure_ascii=False)}\n  ERROR: {e['error']}" for e in trace["errors"])
            fix_prompt = (prompt + "\n\nYou already returned operations for this input. These failed and were NOT applied "
                          "(all others were applied):\n" + feedback + "\n\nCURRENT CATALOG:\n" + self.engine.catalog() +
                          "\n\nReturn corrected operations for the failed parts only.")
            fix, _ = chat_json(self.model, fix_prompt, schema, system=planner.format(user=self.user),
                               schema_name="operations", tag=f"{self.name}:write", max_tokens=16000, effort=effort)
            fix_ops = self._guard(text, ts, fix["ops"], trace) if self.opts.get("guard") else fix["ops"]
            trace["retry_ops"] = fix_ops
            trace["first_errors"] = trace["errors"]
            trace["errors"] = self.engine.apply(fix_ops, log_id, ts)
        return trace

    # @ref LLP 0005#source-guard [implements] — documents may add rows, never overwrite or delete
    def _guard(self, text: str, ts: str, ops: list[dict], trace: dict) -> list[dict]:
        """Third-party content is data, not instructions: documents may add rows or fill empty
        columns, never overwrite or delete. Deletes need the user's own "remove" intent."""
        src = trace.get("source") or jev_layer.source(text, tag=f"{self.name}:gate")
        intent = (trace.get("gate") or jev_layer.gate(text, ts, tag=f"{self.name}:gate", neutral=self.opts.get("gate_neutral", False)))["intent"]
        trace["source"] = src
        kept, blocked = [], []
        for op in ops:
            if op["op"] == "delete" and (src["source"] != "user" or intent != "remove"):
                blocked.append(op)
                continue
            if op["op"] == "update" and src["source"] == "document":
                current = next((r for r in self.engine.rows(op["table"]) if r["id"] == op.get("row_id")), {}) \
                    if op.get("table") in self.engine.tables() else {}
                fill = [v for v in op.get("values") or [] if current.get(v["column"]) in (None, "")]
                if len(fill) < len(op.get("values") or []):
                    blocked.append({**op, "values": [v for v in op["values"] if v not in fill]})
                if not fill:
                    continue
                op = {**op, "values": fill}
            kept.append(op)
        trace["blocked"] = blocked
        return kept

    # @ref LLP 0005#write-context — whole database up to ~150 rows, then BM25 + the user's row + recent rows
    def _write_context(self, text: str) -> tuple[list[dict], str]:
        records = self.engine.all_rows()
        mode = self.opts["write_ctx"]
        if mode == "full" or not records:
            return records, "the whole database"
        if mode == "scalable":
            if len(records) <= 150:
                return records, "the whole database"
            # BM25 over row text + the user's own person row + the most recent rows (for follow-ups).
            texts = [f"{r['table']} " + json.dumps(r["row"], ensure_ascii=False) for r in records]
            if self.opts.get("link_ctx"):   # @ref LLP 0014.000#writes — linked ids read as the linked row's name
                names = {(r["table"], r["row"].get("id")): next((str(r["row"][c]) for c in self.engine.LABEL_COLS
                                                                   if r["row"].get(c)), "") for r in records}
                tables = self.engine.tables()
                texts = [t + " " + " ".join(names.get((self.engine.target_table(c, tables), v), "")
                                             for c, v in r["row"].items() if c.endswith("_id"))
                         for t, r in zip(texts, records)]
            picked = [records[i] for i, sc in bm25_rank(text, texts)[:40] if sc > 0]
            first_name = self.user.split()[0].lower()
            picked += [r for r in records if r["table"] == "people" and first_name in str(r["row"].get("name", "")).lower()]
            picked += self.engine.recent_rows(12)
            seen, out = set(), []
            for r in picked:
                key = (r["table"], r["row"].get("id"))
                if key not in seen:
                    seen.add(key)
                    out.append(r)
            return out, (f"{len(out)} of {len(records)} rows: best text matches for the input, the user's own row, and "
                         "the most recently written rows; other rows exist, so check the catalog before creating tables")
        if mode == "jev":
            picked = [r for r, _ in jev_layer.select_for_write(text, records, tag=f"{self.name}:write-jev")]
            # "I"/"my" messages rarely name the user, so always show the user's own rows.
            first_name = self.user.split()[0].lower()
            for r in records:
                if r not in picked and first_name in json.dumps(r["row"], ensure_ascii=False).lower():
                    picked.append(r)
            return picked, "rows Jev judged related to the input, plus the user's own rows; other rows exist"
        raise ValueError(mode)

    # ------------------------------------------------------------- reading
    def ask(self, question: str, now_iso: str) -> dict:
        mode = self.opts["read"]
        if mode == "sql":
            return self._ask_sql(question, now_iso)
        if mode == "jev":
            return self._ask_jev(question, now_iso)
        if mode == "hybrid":
            picked = jev_layer.select_for_read(question, self.engine.all_rows(), tag=f"{self.name}:read-jev")
            return self._ask_sql(question, now_iso, seed_rows=[r for r, _ in picked])
        if mode == "full":
            return {"answer": self.answer(question, self.engine.dump_text(), now_iso), "context": "full dump"}
        if mode == "readable":
            return {"answer": self.answer(question, self.engine.readable_text(), now_iso), "context": "readable view"}
        raise ValueError(mode)

    def _ask_sql(self, question: str, now_iso: str, max_steps: int = 4, seed_rows: list[dict] | None = None) -> dict:
        now = datetime.fromisoformat(now_iso)
        steps, results = [], []
        catalog = self.engine.catalog(samples=3)
        if seed_rows is not None:
            # Hybrid: rows Jev judged relevant are shown up front; SQL fills gaps and aggregates.
            steps.append("ROWS FOUND BY SEMANTIC SEARCH (may be incomplete):\n" + (rows_text(seed_rows) or "(none)"))
            results.append({"semantic_search": [f"{r['table']}#{r['row']['id']}" for r in seed_rows],
                            "rows": [{"table": r["table"], **r["row"]} for r in seed_rows]})
        for _ in range(max_steps):
            prompt = READER_PROMPT.format(user=self.user, now=now.strftime("%Y-%m-%d %H:%M"), weekday=now.strftime("%A"),
                                          catalog=catalog, question=question,
                                          steps="\n".join(steps) if steps else "(none yet)")
            out, _ = chat_json(self.model, prompt, READ_SCHEMA, schema_name="lookup", tag=f"{self.name}:read")
            if out["done"] or not out["sql_queries"]:
                break
            for q in out["sql_queries"][:4]:
                rows, err = self.engine.query(q)
                shown = json.dumps(rows if err is None else f"ERROR {err}", ensure_ascii=False, default=str)[:6000]
                steps.append(f"SQL: {q}\nRESULT: {shown}")
                results.append({"sql": q, "rows": rows if err is None else f"ERROR {err}"})
        data = json.dumps(results, ensure_ascii=False, default=str)[:60000]
        return {"answer": self.answer(question, data, now_iso), "context": results}

    def _ask_jev(self, question: str, now_iso: str) -> dict:
        records = self.engine.all_rows()
        picked = jev_layer.select_for_read(question, records, tag=f"{self.name}:read-jev")
        chosen = [r for r, _ in picked]
        extra = {"retrieved": len(chosen)}
        if self.opts.get("abstain"):
            p = jev_layer.answerable(question, chosen, tag=f"{self.name}:read-jev")
            extra["answerable"] = p
            if p < self.opts["abstain"]:
                return {"answer": "I don't know.", "context": chosen, **extra}
        return {"answer": self.answer(question, rows_text(chosen) or "(nothing relevant found)", now_iso),
                "context": chosen, **extra}

    def dump(self) -> dict:
        return self.engine.dump()


def rows_text(records: list[dict]) -> str:
    return "\n".join(f"{r['table']}#{r['row']['id']} {json.dumps(r['row'], ensure_ascii=False)}" for r in records)
