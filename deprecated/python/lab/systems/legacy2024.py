"""The 2024 FluidDB SQL pipeline, ported as-is so only the model changes.

Prompts are copied verbatim from fluiddb/functions/update_sql_memory_function.py
(the version the 2024 evals ran). Differences from the original, all needed to
run it as an experiment:
  * "Current datetime" is the message timestamp instead of datetime.now().
  * The user request is the message text, not the repr of a messages list.
  * A failing SQL statement is recorded and skipped instead of crashing the run.
  * `ask()` completes the unfinished 2024 fetch path: generate SELECTs with the
    2024 retrieval prompt, run them, answer from the rows.
"""
# @ref LLP 0002.000 — the 2024 pipeline, round 1's baseline
from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from lab.common.llm import chat_json
from lab.systems.base import ANSWER_PROMPT, MemorySystem, render_input

SQL_SCHEMA = {
    "type": "object",
    "properties": {
        "reasoning": {"type": "string", "description": "Full step by step reasoning, max 250 characters"},
        "sql_queries": {"type": "array", "items": {"type": "string"}, "description": "SQL Queries to execute"},
    },
    "required": ["reasoning", "sql_queries"],
    "additionalProperties": False,
}


def system_prompt(now: datetime) -> str:
    return f"""You're a helpful assistant and a friend. Never mention the structured memory; if you say "structured memory," you die.
    Instead, be helpful and engage in conversation to learn more himself so you can help better.

    Today's date is {now.strftime('%Y-%m-%d')}, using YYYY-MM-DD format
    Time now: {now.strftime('%I:%M %p')}
    """


def retrieve_prompt(user_message: str, memory_schema: str, now: datetime, prev_requests: str = None) -> str:
    return f"""
        You are a senior SQL master, AI that generates SQL Queries from natural language. You're using SQL for sqlite3 to query the database.

        Current datetime is {now.strftime("%Y-%m-%d %H:%M")}

        For the given request, return the list of SQL SELECT queries that retrieve the most relevant information from the sqlite database.
        You don't know what's in the data, write multiple queries to get as much relevant info as possible.
        ALWAYS write SELECT queries that support the SQL TABLES SCHEMA, never make educated guesses.
        NEVER SELECT columns that do not exist in SQL TABLES SCHEMA, such query would kill innocent people.
        ALWAYS fetch the whole row (*) with the SELECT statement, not just a single column.
        When filtering using strings, use LIKE to maximize chances of finding the data. More data is always better.
        If the data you're asked for are clearly not in the schema, return an empty string.

        Always run an internal dialogue before returning the query.

        ---

        PREVIOUS USER REQUESTS:
        {prev_requests if prev_requests else "None"}

        SQL TABLES SCHEMA:
        {memory_schema if memory_schema else "There are no tables in the DB."}

        USER REQUEST: {user_message}
        """


def update_prompt(user_message: str, memory_schema: str, now: datetime, fetched_data=None, prev_reasoning: str = None, prev_requests: str = None) -> str:
    return f"""
        You are a senior SQL database architect, AI that creates the best schema for data provided using SQL for sqlite3.

        Current datetime is {now.strftime("%Y-%m-%d %H:%M")}

        For the given user request you will return the list of SQL queries that store the information to sqlite database.
        ALWAYS think step by step. Run an internal dialogue before returning the queries.

        You receive the user request. First, think about how to store the data based on the database schema.
        If the data conform to the schema simply insert the new data using INSERT INTO statement.
        If you need new columns make sure to create them first using ALTER TABLE ADD COLUMN. Then make sure to INSERT the data in the next query. The order of queries matters!
        NEVER make educated guesses, ONLY INSERT data if the columns exist in the SQL TABLES SCHEMA, otherwise create them first.
        You can't INSERT or UPDATE a column that does not exist yet. First you must ALTER TABLE ADD COLUMN. Otherwise an error will occur and innocent people will die.
        If the data needs a new table make sure to create the new table first using CREATE TABLE. Then make sure to INSERT the data in the next query. The order of queries matters!
        Make sure to keep the relationships between the tables using the correct ids.
        If you don't get relevant data in the prompt assume there are none and INSERT all data as they're new. If you have relevant data you can update the existing data using UPDATE queries.

        ALWAYS remember to insert the data if you created new table or added columns. If you don't store the data in one of the queries the data will be lost forever!

        ---
        PREVIOUS USER REQUESTS:
        {prev_requests if prev_requests else "None"}

        SQL TABLES SCHEMA:
        {memory_schema if memory_schema else "No tables yet in the DB."}

        {f"Initial thoughts: {prev_reasoning}" if prev_reasoning else ""}

        {f"RELEVANT DATA FROM sqlite DB: {fetched_data}" if fetched_data else ""}

        USER REQUEST: {user_message}
        """


class Legacy2024(MemorySystem):
    name = "legacy2024"

    def __init__(self, model: str, db_path: str, user: str):
        super().__init__(model, db_path, user)
        self.db = sqlite3.connect(db_path, check_same_thread=False)

    # --- the 2024 SQLEngine -------------------------------------------------
    def schema(self, db: sqlite3.Connection | None = None) -> str:
        out = ""
        cur = (db or self.db).cursor()
        tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]
        for t in tables:
            if "sqlite_" in t:
                continue
            out += f"{t}\n"
            for r in cur.execute(f"PRAGMA table_info('{t}')").fetchall():
                out += f"  {r[1]}: {r[2]} {'pk' if r[5] else ''}\n"
        return out

    def run_sql(self, query: str, db: sqlite3.Connection | None = None, named: bool = False):
        db = db or self.db
        cur = db.cursor()
        try:
            rows = cur.execute(query).fetchall()
            if named and cur.description:
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, r)) for r in rows]
            db.commit()
            return rows, None
        except Exception as e:  # recorded, not fatal
            db.rollback()
            return None, f"{type(e).__name__}: {e}"

    # --- write path ----------------------------------------------------------
    def remember(self, message: dict) -> dict:
        now = datetime.fromisoformat(message["ts"])
        text = render_input(message["text"])
        trace: dict = {"errors": [], "llm_calls": 0}
        schema = self.schema()
        prev_reasoning, fetched_data = "", ""
        if schema:
            fetch, _ = chat_json(self.model, retrieve_prompt(text, schema, now), SQL_SCHEMA, system=system_prompt(now),
                                 schema_name="SQLQueryModel", tag=f"{self.name}:write")
            trace["llm_calls"] += 1
            prev_reasoning = fetch["reasoning"]
            fetched_data = []
            for q in fetch["sql_queries"]:
                rows, err = self.run_sql(q)
                if err:
                    trace["errors"].append({"phase": "fetch", "sql": q, "error": err})
                fetched_data.append(rows)
            schema = self.schema()
        update, _ = chat_json(self.model, update_prompt(text, schema, now, fetched_data, prev_reasoning), SQL_SCHEMA,
                              system=system_prompt(now), schema_name="SQLQueryModel", tag=f"{self.name}:write")
        trace["llm_calls"] += 1
        trace["sql"] = update["sql_queries"]
        for q in update["sql_queries"]:
            _, err = self.run_sql(q)
            if err:
                trace["errors"].append({"phase": "update", "sql": q, "error": err})
        return trace

    # --- read path -----------------------------------------------------------
    def ask(self, question: str, now_iso: str) -> dict:
        now = datetime.fromisoformat(now_iso)
        # Each question gets its own read-only connection: questions run on several
        # threads, and a stray UPDATE can't change the data.
        ro = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, check_same_thread=False)
        schema = self.schema(ro)
        fetch, _ = chat_json(self.model, retrieve_prompt(question, schema, now), SQL_SCHEMA, system=system_prompt(now),
                             schema_name="SQLQueryModel", tag=f"{self.name}:read")
        results = []
        for q in fetch["sql_queries"]:
            rows, err = self.run_sql(q, ro, named=True)
            results.append({"sql": q, "rows": rows if err is None else f"ERROR {err}"})
        ro.close()
        data = json.dumps(results, ensure_ascii=False, default=str)[:60000]
        answer = self.answer(question, data, now_iso)
        return {"answer": answer, "context": results}

    def dump(self) -> dict:
        cur = self.db.cursor()
        out = {}
        for (t,) in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            if t.startswith("sqlite_"):
                continue
            cols = [r[1] for r in cur.execute(f"PRAGMA table_info('{t}')").fetchall()]
            out[t] = {"columns": cols, "rows": cur.execute(f"SELECT * FROM '{t}'").fetchall()}
        return out
