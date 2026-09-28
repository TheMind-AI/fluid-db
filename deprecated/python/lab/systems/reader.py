"""Agentic reader: answer a question with tools instead of reading the whole database.

Each step the model picks one action:
  sql         a read-only SELECT (exact counts, sums, filters, _history)
  lookup      readable rows of one table matching some text (links shown as names)
  search_log  the raw messages that best match some words (BM25 over _log), for wording and history
  answer      the final answer
It sees the catalog (with the known values of short columns) and every earlier result.
"""
# @ref LLP 0006#agent-reader — the LLM tool agent, the fallback when the verifier is unsure
from __future__ import annotations

import json
from datetime import datetime

from lab.common.llm import chat_json
from lab.systems.engine import Engine, bm25_rank

STEP_SCHEMA = {"type": "object", "properties": {
    "thought": {"type": "string"},
    "action": {"type": "string", "enum": ["sql", "lookup", "search_log", "answer"]},
    "argument": {"type": "string", "description": "SQL for sql; 'table: words' for lookup; words for search_log; the answer for answer"}},
    "required": ["thought", "action", "argument"], "additionalProperties": False}

PROMPT = """You answer questions for {user} from their personal database, using tools one step at a time.
Current datetime: {now} ({weekday}).

Tools:
- sql: one read-only SQLite SELECT. Best for exact counts, sums (group money by currency), date ranges, and _history
  (earlier values: table_name, row_id, column_name, old_value, new_value, ts, op).
- lookup: "table: words" -> rows of that table whose text matches the words, with links shown as names.
- search_log: words -> the user's original messages that match best (use it for wording, context, or when the
  database lacks something).
- answer: your final answer. Be concise and include key numbers, dates and names. If the data does not contain the
  answer, answer exactly "I don't know." Never guess.

DATABASE CATALOG:
{catalog}

QUESTION: {question}

STEPS SO FAR:
{steps}"""


def lookup(engine: Engine, arg: str, limit: int = 25) -> list[dict]:
    table, _, words = arg.partition(":")
    table = table.strip()
    if table not in engine.tables():
        return [f"ERROR: no table {table}; tables are: {', '.join(engine.tables())}"]
    rows = engine.rows(table)
    view = engine.readable_text()  # resolved names for every row
    lines = {ln.split(" ", 1)[0]: ln for ln in view.splitlines() if ln.startswith(f"{table}#")}
    texts = [lines.get(f"{table}#{r['id']}", json.dumps(r, ensure_ascii=False)) for r in rows]
    ranked = bm25_rank(words or table, texts)
    picked = [texts[i] for i, s in ranked if s > 0][:limit] or texts[:limit]
    return picked


def search_log(engine: Engine, words: str, limit: int = 12) -> list[str]:
    log = engine.db.execute("SELECT ts, text FROM _log ORDER BY id").fetchall()
    texts = [f"[{ts[:16].replace('T', ' ')}] {text}" for ts, text in log]
    return [texts[i] for i, s in bm25_rank(words, texts)[:limit] if s > 0]


def ask_agent(engine: Engine, question: str, now: datetime, user: str, model: str, tag: str, max_steps: int = 6) -> dict:
    catalog = engine.catalog(samples=2)
    steps: list[str] = []
    for i in range(max_steps):
        prompt = PROMPT.format(user=user, now=now.strftime("%Y-%m-%d %H:%M"), weekday=now.strftime("%A"), catalog=catalog,
                               question=question, steps="\n".join(steps) if steps else "(none yet)")
        if i == max_steps - 1:
            prompt += "\n\nThis is the last step: you must answer now."
        out, _ = chat_json(model, prompt, STEP_SCHEMA, schema_name="step", tag=tag, max_tokens=4000)
        act, arg = out["action"], out["argument"]
        if act == "answer":
            return {"answer": arg, "steps": steps}
        if act == "sql":
            rows, err = engine.query(arg)
            result = f"ERROR {err}" if err else json.dumps(rows[:60], ensure_ascii=False, default=str)
        elif act == "lookup":
            result = "\n".join(lookup(engine, arg))
        else:
            result = "\n".join(search_log(engine, arg))
        steps.append(f"[{act}] {arg}\n-> {result[:5000]}")
    return {"answer": "I don't know.", "steps": steps}
