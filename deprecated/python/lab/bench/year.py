"""A simulated year: does FluidDB hold up over time?

  ingest:   .venv/bin/python -m lab.bench.year ingest --effort none      (one process per config)
  evaluate: LAB_ANTHROPIC_VIA=openrouter .venv/bin/python -m lab.bench.year evaluate

Ingestion runs FluidDB v2.2 over lab/datasets/life_year.json (817 messages, Oct 2025 - Sep 2026),
snapshots the database at the 6-month checkpoint, and records schema growth every 50 messages.
Evaluation answers the checkpoint questions (exact gold answers from the simulator) four ways:

  raw_log   every message up to the checkpoint, in the prompt (no database)
  db        the database dump, including _history
  db+log    the database dump plus FluidDB's own raw log (forget-redacted)
  sql       one LLM-written SQL query (up to 3 statements) over the catalog, rows -> answer
"""
# @ref LLP 0002.001 — a round-2 bench
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from lab.bench.baselines import answer
from lab.common.judge import judge_many
from lab.common.llm import LAB_DIR, LEDGER, chat_json
from lab.systems.base import render_input
from lab.systems.engine import Engine
from lab.systems.fluid_v2 import FluidV2
from lab.systems.reader import ask_agent

DS = LAB_DIR / "datasets" / "life_year.json"
MODEL = "openai:gpt-6-luna"
SOL = "openai:gpt-6-sol"
T2S_SCHEMA = {"type": "object", "properties": {"sql_queries": {"type": "array", "items": {"type": "string"}}},
              "required": ["sql_queries"], "additionalProperties": False}
T2S = """You translate a question into SQLite SELECT queries over {user}'s personal database.
Current datetime: {now} ({weekday}).

DATABASE CATALOG (with example rows):
{catalog}

QUESTION: {question}

Return up to 3 SELECT queries whose results together answer the question. Join tables to include readable names,
match text case-insensitively with LIKE on partial terms, use SUM/COUNT/MIN/MAX for totals and counts
(grouped by currency for money), and query _history for what something was before it changed."""


def run_dir(effort: str, variant: str = "v22", model: str = "gpt-6-luna") -> Path:
    d = LAB_DIR / "runs" / f"year__{variant}__{model}__{effort}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def stats(engine: Engine) -> dict:
    db = engine.db
    tables = engine.tables()
    return {"tables": len(tables), "columns": sum(len(engine.columns(t)) for t in tables),
            "rows": sum(db.execute(f"SELECT COUNT(*) FROM '{t}'").fetchone()[0] for t in tables),
            "history": db.execute("SELECT COUNT(*) FROM _history").fetchone()[0]}


def ingest(effort: str, variant: str = "v22", model: str = MODEL):
    ds = json.loads(DS.read_text())
    d = run_dir(effort, variant, model.split(":")[1])
    for f in d.glob("db*.sqlite*"):
        f.unlink()
    path = d / "db.sqlite"
    system = FluidV2(model, str(path), ds["user"], variant=variant, effort=effort)
    m6 = datetime.fromisoformat(ds["checkpoints"]["m6"])
    trace, growth, snap_done = [], [], False
    start_all = time.time()
    for i, msg in enumerate(ds["messages"]):
        if not snap_done and datetime.fromisoformat(msg["ts"]) > m6:
            system.engine.db.commit()
            shutil.copy(path, d / "db_m6.sqlite")
            snap_done = True
        n0, t0 = len(LEDGER.calls), time.time()
        try:
            tr = system.remember(msg)
            err = None
        except Exception as e:
            tr, err = {}, f"{type(e).__name__}: {e}"
        calls = LEDGER.calls[n0:]
        trace.append({"id": msg["id"], "kind": msg["kind"], "skipped": bool(tr.get("skipped")), "crash": err,
                      "ops": len(tr.get("ops") or []), "errors": len(tr.get("errors") or []),
                      "retried": "retry_ops" in tr, "context_rows": tr.get("context_rows"),
                      "wall": round(time.time() - t0, 2), "cost": round(sum(c.cost for c in calls), 6)})
        if (i + 1) % 50 == 0 or i == len(ds["messages"]) - 1:
            growth.append({"messages": i + 1, "ts": msg["ts"], **stats(system.engine),
                           "cost": round(sum(t["cost"] for t in trace), 4),
                           "unresolved_errors": sum(t["errors"] for t in trace), "crashes": sum(1 for t in trace if t["crash"])})
            print(f"[{variant} {effort}] {i + 1}/{len(ds['messages'])} {growth[-1]}", flush=True)
    (d / "ingest.json").write_text(json.dumps({"trace": trace, "growth": growth,
                                               "wall_total_s": round(time.time() - start_all, 1)}, indent=1))


def evaluate(efforts: list[str], checkpoints: list[str] = ("m6", "m12"), only: list[str] | None = None):
    ds = json.loads(DS.read_text())
    report = {}
    for cp, qa_key in (("m6", "qa_m6"), ("m12", "qa")):
        if cp not in checkpoints:
            continue
        now = datetime.fromisoformat(ds["checkpoints"][cp])
        qa = ds[qa_key]
        log = "\n".join(f"[{m['ts'][:16].replace('T', ' ')}] {render_input(m['text'])}"
                        for m in ds["messages"] if datetime.fromisoformat(m["ts"]) <= now)
        modes = {"raw_log": lambda q: answer(log, q["question"], now, ds["user"], f"year:{cp}:raw")}
        for spec in efforts:
            # "none", "low:sleep", "v23/low", "v23/low:sleep", "v23/gpt-6-sol/low"
            parts = spec.split("/")
            version = parts[0] if len(parts) > 1 else ""
            model_name = parts[1] if len(parts) > 2 else "gpt-6-luna"
            effort, _, variant = parts[-1].partition(":")
            if variant and cp == "m6":
                continue
            db_path = run_dir(effort, version or "v22", model_name) / ("db_m6.sqlite" if cp == "m6" else f"db_{variant}.sqlite" if variant else "db.sqlite")
            effort = spec
            eng = Engine(str(db_path), strict=True, normalize=(version == "v23"))
            dump = eng.dump_text()
            own_log = "\n".join(f"[{ts[:16].replace('T', ' ')}] {text}" for ts, text in
                                sqlite3.connect(str(db_path)).execute("SELECT ts, text FROM _log ORDER BY id"))
            catalog = eng.catalog(samples=3)
            tag = f"year:{cp}:{effort}"
            modes[f"db ({effort})"] = lambda q, dump=dump, tag=tag: answer(
                f"STRUCTURED DATABASE (current state; _history has earlier values):\n{dump}", q["question"], now, ds["user"], tag + ":db")
            modes[f"db+log ({effort})"] = lambda q, dump=dump, own_log=own_log, tag=tag: answer(
                f"STRUCTURED DATABASE (current state; _history has earlier values):\n{dump}\n\n"
                f"RAW MESSAGE LOG (what the user said, oldest first; [forgotten] marks erased text):\n{own_log}",
                q["question"], now, ds["user"], tag + ":dblog")
            modes[f"sql ({effort})"] = lambda q, eng=eng, catalog=catalog, tag=tag: sql_answer(eng, catalog, q, now, ds["user"], tag + ":sql")
            modes[f"agent ({effort})"] = lambda q, eng=eng, tag=tag: ask_agent(eng, q["question"], now, ds["user"], MODEL, tag + ":agent")["answer"]
            # the same readers with GPT-6 Sol writing the SQL / driving the tools: is the reader the bottleneck?
            modes[f"sol-sql ({effort})"] = lambda q, eng=eng, catalog=catalog, tag=tag: sql_answer(
                eng, catalog, q, now, ds["user"], tag + ":solsql", writer=SOL)
            modes[f"sol-agent ({effort})"] = lambda q, eng=eng, tag=tag: ask_agent(eng, q["question"], now, ds["user"], SOL, tag + ":solagent")["answer"]
            readable = eng.readable_text()
            modes[f"rdb ({effort})"] = lambda q, readable=readable, tag=tag: answer(
                f"STRUCTURED DATABASE (current state, links shown as names; _history has earlier values):\n{readable}",
                q["question"], now, ds["user"], tag + ":rdb")
            modes[f"rdb+log ({effort})"] = lambda q, readable=readable, own_log=own_log, tag=tag: answer(
                f"STRUCTURED DATABASE (current state, links shown as names; _history has earlier values):\n{readable}\n\n"
                f"RAW MESSAGE LOG (what the user said, oldest first; [forgotten] marks erased text):\n{own_log}",
                q["question"], now, ds["user"], tag + ":rdblog")
        report[cp] = {}
        for name, fn in modes.items():
            if only and not any(name.startswith(o) for o in only):
                continue
            n0 = len(LEDGER.calls)
            with ThreadPoolExecutor(4) as pool:
                answers = list(pool.map(fn, qa))
            calls = LEDGER.calls[n0:]
            verdicts = judge_many([(q["question"], q["answer"], a) for q, a in zip(qa, answers)])
            cats: dict = {}
            for q, v in zip(qa, verdicts):
                cats.setdefault(q["category"], []).append(v["score"])
            report[cp][name] = {
                "accuracy": round(sum(v["score"] for v in verdicts) / len(verdicts), 4),
                "by_category": {c: round(sum(s) / len(s), 3) for c, s in sorted(cats.items())},
                "input_tokens_per_q": int(sum(c.input_tokens for c in calls if c.tag.startswith("year")) / len(qa)),
                "failures": [{"q": q["question"], "gold": q["answer"], "got": a[:220], "verdict": v["verdict"]}
                             for q, a, v in zip(qa, answers, verdicts) if v["score"] < 1],
            }
            print(f"{cp} {name:14s} acc={report[cp][name]['accuracy']:.3f} tokens/q={report[cp][name]['input_tokens_per_q']} "
                  f"{report[cp][name]['by_category']}", flush=True)
            _save({cp: {name: report[cp][name]}})



def _save(part: dict):
    """Merge one mode's results into bench_year.json right away (a crash later can't lose them)."""
    out = LAB_DIR / "results" / "bench_year.json"
    old = json.loads(out.read_text()) if out.exists() else {}
    for cp, modes in part.items():
        old.setdefault(cp, {}).update(modes)
    out.write_text(json.dumps(old, indent=1, ensure_ascii=False))


def sql_answer(eng: Engine, catalog: str, q: dict, now: datetime, user: str, tag: str, writer: str = MODEL) -> str:
    out, _ = chat_json(writer, T2S.format(user=user, now=now.strftime("%Y-%m-%d %H:%M"), weekday=now.strftime("%A"),
                                         catalog=catalog, question=q["question"]), T2S_SCHEMA, schema_name="sql", tag=tag,
                       max_tokens=4000)
    results = []
    for s in out["sql_queries"][:3]:
        rows, err = eng.query(s)
        results.append({"sql": s, "rows": rows if err is None else f"ERROR {err}"})
    return answer(json.dumps(results, ensure_ascii=False, default=str)[:40000], q["question"], now, user, tag)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["ingest", "evaluate"])
    ap.add_argument("--effort", default="none")
    ap.add_argument("--variant", default="v22")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--efforts", nargs="+", default=["none", "low"])
    ap.add_argument("--checkpoints", nargs="+", default=["m6", "m12"])
    ap.add_argument("--only", nargs="+", help="evaluate only modes whose name starts with one of these")
    args = ap.parse_args()
    if args.cmd == "ingest":
        ingest(args.effort, args.variant, args.model)
    else:
        evaluate(args.efforts, args.checkpoints, args.only)
