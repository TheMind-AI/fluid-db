"""How do you query a FluidDB database, and how fast / accurate is each way?

Runs every life_stream question against one finished database (built by v21jev + GPT-6 Luna),
one question at a time with caching off, in these modes:

  sql            hand-written SQL, no model at all (10 questions; shows it is a real database)
  text2sql       one LLM call writes SQL from the catalog -> rows (re-running that SQL later = "compiled")
  jev_rows       Jev picks the relevant rows -> rows (no LLM)
  jev+answer     Jev picks rows -> one LLM call writes the answer
  full+answer    the whole database in one prompt -> one LLM call writes the answer
  agent+answer   multi-step SQL agent -> answer (the v2 reader)

Rows-only modes are graded on whether the rows contain the answer; answer modes on the answer.

  LAB_NO_CACHE=1 .venv/bin/python -m lab.bench.query_modes
"""
# @ref LLP 0002.000 — a round-1 bench
from __future__ import annotations

import json
import os
import shutil
import statistics
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from lab.common.judge import judge_many, SCORES, JUDGE_MODEL
from lab.common.llm import LAB_DIR, LEDGER, chat, chat_json
from lab.systems import jev_layer
from lab.systems.base import ANSWER_PROMPT
from lab.systems.fluid_v2 import FluidV2

DB_RUN = "v21jev__openai_gpt-6-luna__life_stream"
MODELS = {
    "luna-none": ("openai:gpt-6-luna", "none"),
    "luna-low": ("openai:gpt-6-luna", "low"),
    "haiku": ("anthropic:claude-haiku-4-5", None),
    "sonnet-low": ("anthropic:claude-sonnet-5", "low"),
}

# Written by hand against this database's catalog (see REPORT.md).
HANDWRITTEN = {
    "q02": "SELECT p.name, c.type, c.value FROM people p JOIN contact_methods c ON c.person_id = p.id WHERE p.name LIKE 'David%'",
    "q04": "SELECT pl.name, pl.address, pl.city, r.move_in_date FROM residences r JOIN places pl ON pl.id = r.place_id "
           "WHERE r.person_id = 1 ORDER BY r.move_in_date DESC LIMIT 1",
    "q06": "SELECT name, birthday FROM people WHERE birthday IS NOT NULL",
    "q10": "SELECT SUM(amount) AS total, currency FROM expenses WHERE restaurant_id IS NOT NULL "
           "AND date BETWEEN '2026-08-01' AND '2026-08-31' GROUP BY currency",
    "q11": "SELECT MIN(duration) AS best, date FROM activities WHERE activity_type = 'running' AND distance_km = 10",
    "q14": "SELECT a.allergen FROM allergies a JOIN people p ON p.id = a.person_id WHERE p.name LIKE 'Eva%'",
    "q15": "SELECT make, model, year, status, sold_date FROM vehicles WHERE person_id = 1",
    "q20": "SELECT COUNT(*) AS sessions FROM activities WHERE activity_type = 'climbing' AND date LIKE '2026-08-%'",
    "q29": "SELECT SUM(amount) AS total, currency FROM expenses WHERE restaurant_id IS NOT NULL AND date LIKE '2026-09-%' GROUP BY currency",
    "q33": "SELECT title, start_datetime, end_datetime, details FROM events WHERE start_datetime BETWEEN '2026-09-21' AND '2026-09-28' "
           "AND COALESCE(status, '') != 'cancelled' ORDER BY start_datetime",
}

T2S_SCHEMA = {"type": "object", "properties": {"sql_queries": {"type": "array", "items": {"type": "string"}}},
              "required": ["sql_queries"], "additionalProperties": False}
T2S_PROMPT = """You translate a question into SQLite SELECT queries over {user}'s personal database.
Current datetime: {now} ({weekday}).

DATABASE CATALOG (with example rows):
{catalog}

QUESTION: {question}

Return up to 3 SELECT queries whose results together answer the question. Join tables to include readable names,
match text case-insensitively with LIKE on partial terms, and prefer whole rows over single columns."""

ROWS_JUDGE_SCHEMA = {"type": "object", "properties": {
    "verdict": {"type": "string", "enum": ["correct", "partial", "incorrect"]}, "reason": {"type": "string"}},
    "required": ["verdict", "reason"], "additionalProperties": False}
ROWS_JUDGE = """A database query returned the rows below for a question. Grade whether the rows contain the information
needed to give the gold answer.
- "correct": every key fact of the gold answer can be read or computed from the rows (current values, not outdated ones).
- "partial": some key facts can, others are missing.
- "incorrect": the rows don't contain the answer (or are empty or an error).
- If the gold answer starts with UNKNOWN: "correct" if the rows do not state an answer to the question, else "incorrect".

QUESTION: {question}
GOLD ANSWER: {gold}
ROWS: {rows}"""


def timed(fn):
    start = time.perf_counter()
    out = fn()
    return out, time.perf_counter() - start


RAW = LAB_DIR / "results" / "query_modes_raw.json"


def main():
    import sys
    if "--judge-only" not in sys.argv:
        assert os.environ.get("LAB_NO_CACHE"), "run with LAB_NO_CACHE=1 so every call is live"
        measure()
    judge()


def measure():
    ds = json.loads((LAB_DIR / "datasets" / "life_stream.json").read_text())
    tmp = tempfile.mkdtemp()
    db_path = os.path.join(tmp, "db.sqlite")
    shutil.copy(LAB_DIR / "runs" / DB_RUN / "db.sqlite", db_path)
    system = FluidV2("openai:gpt-6-luna", db_path, ds["user"], variant="v21jev")
    engine = system.engine
    now = datetime.fromisoformat(ds["now"])
    catalog = engine.catalog(samples=3)
    records = engine.all_rows()
    dump = engine.dump_text()

    def run_sql(sqls):
        start = time.perf_counter()
        out = []
        for s in sqls:
            rows, err = engine.query(s)
            out.append({"sql": s, "rows": rows if err is None else f"ERROR {err}"})
        return out, time.perf_counter() - start

    def answer(model, effort, q, data, tag):
        prompt = ANSWER_PROMPT.format(user=ds["user"], now=now.strftime("%Y-%m-%d %H:%M"), weekday=now.strftime("%A"),
                                      data=data, question=q)
        return chat(model, [{"role": "user", "content": prompt}], effort=effort, tag=tag, max_tokens=4000)[0].strip()

    def jev_pick(q, tag):
        return [r for r, _ in jev_layer.select_for_read(q, records, tag=tag, workers=64)]

    def rows_text(rows):
        return "\n".join(f"{r['table']}#{r['row']['id']} {json.dumps(r['row'], ensure_ascii=False)}" for r in rows)

    modes = {}
    modes["sql (no model)"] = ("rows", lambda q, tag: _sql_mode(q, run_sql))
    for key, (model, effort) in MODELS.items():
        modes[f"text2sql {key}"] = ("rows", lambda q, tag, m=model, e=effort: _t2s(q, m, e, catalog, ds, now, run_sql, tag))
    modes["jev_rows (no LLM)"] = ("rows", lambda q, tag: (lambda rows: {"result": rows_text(rows), "rows": len(rows)})(jev_pick(q["question"], tag)))
    for key, (model, effort) in MODELS.items():
        modes[f"jev+answer {key}"] = ("answer", lambda q, tag, m=model, e=effort: {"result": answer(m, e, q["question"], rows_text(jev_pick(q["question"], tag)), tag)})
        modes[f"full+answer {key}"] = ("answer", lambda q, tag, m=model, e=effort: {"result": answer(m, e, q["question"], dump, tag)})
    for key in ("luna-low", "sonnet-low"):
        model = MODELS[key][0]
        agent = FluidV2(model, db_path, ds["user"], variant=f"qm-agent-{key}")
        agent.opts = {"gate": "none", "write_ctx": "full", "read": "sql", "planner": "v21"}
        modes[f"agent+answer {key}"] = ("answer", lambda q, tag, a=agent: {"result": a._ask_sql(q["question"], ds["now"])["answer"]})

    def run_mode(name):
        kind, fn = modes[name]
        tag = f"qm:{name}"
        if name.startswith("agent+answer"):
            tag = f"qm-agent-{name.split()[-1]}"   # the agent tags its own calls with its variant name
        rows = []
        for q in ds["qa"]:
            if name.startswith("sql (") and q["id"] not in HANDWRITTEN:
                continue
            try:
                out, wall = timed(lambda: fn(q, tag))
            except Exception as e:
                out, wall = {"result": f"ERROR {type(e).__name__}: {e}"}, 0.0
            rows.append({"qid": q["id"], "wall": wall, **out})
        cost = sum(c.cost for c in LEDGER.calls if c.tag.startswith(tag))
        return name, kind, rows, cost

    with ThreadPoolExecutor(len(modes)) as pool:
        results = list(pool.map(run_mode, list(modes)))
    RAW.write_text(json.dumps(results, ensure_ascii=False, default=str))


def judge():
    ds = json.loads((LAB_DIR / "datasets" / "life_stream.json").read_text())
    results = json.loads(RAW.read_text())
    qa = {q["id"]: q for q in ds["qa"]}
    report = {}
    for name, kind, rows, cost in results:
        if kind == "answer":
            verdicts = judge_many([(qa[r["qid"]]["question"], qa[r["qid"]]["answer"], r["result"]) for r in rows])
        else:
            def jr(r):
                out, _ = chat_json(JUDGE_MODEL, ROWS_JUDGE.format(question=qa[r["qid"]]["question"], gold=qa[r["qid"]]["answer"],
                                                                  rows=str(r["result"])[:20000]),
                                   ROWS_JUDGE_SCHEMA, tag="judge", effort="low", max_tokens=4000)
                out["score"] = SCORES[out["verdict"]]
                return out
            with ThreadPoolExecutor(8) as pool:
                verdicts = list(pool.map(jr, rows))
        walls = sorted(r["wall"] for r in rows)
        report[name] = {
            "kind": kind, "n": len(rows),
            "score": round(sum(v["score"] for v in verdicts) / len(verdicts), 3),
            "p50_s": round(statistics.median(walls), 3),
            "p90_s": round(walls[int(0.9 * (len(walls) - 1))], 3),
            "mean_cost": round(cost / len(rows), 5),
            "exec_ms_p50": round(statistics.median(r["exec_s"] for r in rows) * 1000, 2) if "exec_s" in rows[0] else None,
            "llm_s_p50": round(statistics.median(r["llm_s"] for r in rows), 3) if "llm_s" in rows[0] else None,
            "items": [{**r, "verdict": v["verdict"], "why": v["reason"]} for r, v in zip(rows, verdicts)],
        }
        print(f"{name:26s} score={report[name]['score']:.3f} p50={report[name]['p50_s']:.2f}s p90={report[name]['p90_s']:.2f}s "
              f"cost={report[name]['mean_cost']:.5f} exec_ms={report[name]['exec_ms_p50']} llm_s={report[name]['llm_s_p50']}", flush=True)
    (LAB_DIR / "results" / "bench_query_modes.json").write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str))


def _sql_mode(q, run_sql):
    res, exec_s = run_sql([HANDWRITTEN[q["id"]]])
    return {"result": json.dumps(res, ensure_ascii=False, default=str), "sql": HANDWRITTEN[q["id"]], "exec_s": exec_s}


def _t2s(q, model, effort, catalog, ds, now, run_sql, tag):
    start = time.perf_counter()
    out, _ = chat_json(model, T2S_PROMPT.format(user=ds["user"], now=now.strftime("%Y-%m-%d %H:%M"), weekday=now.strftime("%A"),
                                                 catalog=catalog, question=q["question"]),
                       T2S_SCHEMA, schema_name="sql", effort=effort, tag=tag, max_tokens=4000)
    llm_s = time.perf_counter() - start
    res, exec_s = run_sql(out["sql_queries"][:3])
    return {"result": json.dumps(res, ensure_ascii=False, default=str)[:20000], "sql": out["sql_queries"], "llm_s": llm_s, "exec_s": exec_s}


if __name__ == "__main__":
    main()
