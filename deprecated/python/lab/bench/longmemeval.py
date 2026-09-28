"""LongMemEval_S (Wu et al., ICLR 2025) on a stratified sample, with the benchmark's ORIGINAL prompts and judge.

  sample:   .venv/bin/python -m lab.bench.longmemeval sample --n 100
  ingest:   .venv/bin/python -m lab.bench.longmemeval ingest            (one FluidDB per question, 10 in parallel)
  evaluate: .venv/bin/python -m lab.bench.longmemeval evaluate --modes full fluid:rdb fluid:rdb+log fluid:agent

Every question has its own haystack (~48 sessions, ~500 turns, ~115k tokens). FluidDB v2.3 ingests each haystack
in chunks of up to 8 turns (session date as the timestamp). Answers come from GPT-6 Luna with the benchmark's own
templates (run_generation.py: chat history / facts / history + facts, step by step) and are judged by gpt-4o with
the original type-specific answer checks (evaluate_qa.py), as in the paper. Mem0's published numbers use a
different, test-tuned prompt and judge (see REPORT.md), so they are not comparable to these.
"""
# @ref LLP 0002.001 — a round-2 bench
from __future__ import annotations

import argparse
import json
import random
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from lab.bench.external.longmemeval_prompts import FACTS_COT, HISTORY_AND_FACTS_COT, HISTORY_COT, get_anscheck_prompt
from lab.bench.locomo import AGENT_PROMPT
from lab.common.llm import LAB_DIR, LEDGER, chat, chat_json
from lab.systems.engine import Engine
from lab.systems.fluid_v2 import FluidV2
from lab.systems.reader import STEP_SCHEMA, lookup, search_log

DS = LAB_DIR / "datasets" / "external" / "longmemeval_s_cleaned.json"
RUN = LAB_DIR / "runs" / "longmemeval"
SAMPLE = LAB_DIR / "results" / "longmemeval_sample.json"
OUT = LAB_DIR / "results" / "bench_longmemeval.json"
MODEL = "openai:gpt-6-luna"
JUDGE = "openai:gpt-4o-2024-08-06"
CHUNK = 8


def parse_date(s: str) -> datetime:
    return datetime.strptime(s, "%Y/%m/%d (%a) %H:%M")


def sample(n: int, seed: int = 0) -> list[str]:
    """Proportional stratified sample over question types (abstention questions included in proportion)."""
    ds = json.loads(DS.read_text())
    rng = random.Random(seed)
    by_type: dict = {}
    for q in ds:
        by_type.setdefault(q["question_type"], []).append(q["question_id"])
    picked = []
    for t, ids in sorted(by_type.items()):
        k = round(n * len(ids) / len(ds))
        picked += rng.sample(sorted(ids), k)
    SAMPLE.write_text(json.dumps(picked, indent=1))
    return picked


def questions() -> list[dict]:
    ids = set(json.loads(SAMPLE.read_text()))
    return [q for q in json.loads(DS.read_text()) if q["question_id"] in ids]


def sessions(q: dict):
    out = sorted(zip(q["haystack_dates"], q["haystack_session_ids"], q["haystack_sessions"]), key=lambda s: parse_date(s[0]))
    return [(parse_date(d), sid, turns) for d, sid, turns in out]


def history_text(q: dict) -> str:
    """The benchmark's own history format (json turns per session, with its date)."""
    return "\n\n".join(f"### Session {i + 1}:\nSession Date: {when.strftime('%Y/%m/%d (%a) %H:%M')}\nSession Content:\n"
                       f"{json.dumps([{'role': t['role'], 'content': t['content']} for t in turns])}"
                       for i, (when, _, turns) in enumerate(sessions(q)))


# ------------------------------------------------------------------ ingestion
def ingest_one(q: dict) -> dict:
    path = RUN / f"{q['question_id']}.sqlite"
    if (RUN / f"{q['question_id']}.json").exists():
        return json.loads((RUN / f"{q['question_id']}.json").read_text())["stats"]
    for f in RUN.glob(f"{q['question_id']}.sqlite*"):
        f.unlink()
    system = FluidV2(MODEL, str(path), "the user", variant="v23", effort="low")
    trace = []
    for when, sid, turns in sessions(q):
        lines = [f"{'User' if t['role'] == 'user' else 'Assistant'}: {t['content']}" for t in turns if t.get("content")]
        for j in range(0, len(lines), CHUNK):
            msg = {"id": f"{sid}_c{j // CHUNK}", "kind": "chat", "ts": (when + timedelta(minutes=j // CHUNK)).isoformat(),
                   "text": f"Chat between the user and their AI assistant ({when.strftime('%Y/%m/%d %H:%M')}):\n"
                           + "\n".join(lines[j:j + CHUNK])}
            t0 = time.time()
            try:
                tr, err = system.remember(msg), None
            except Exception as e:
                tr, err = {}, f"{type(e).__name__}: {e}"[:300]
            trace.append({"id": msg["id"], "skipped": bool(tr.get("skipped")), "ops": len(tr.get("ops") or []),
                          "crash": err, "wall": round(time.time() - t0, 2)})
    eng = system.engine
    tables = eng.tables()
    stats = {"qid": q["question_id"], "type": q["question_type"], "chunks": len(trace),
             "skipped": sum(t["skipped"] for t in trace), "crashes": sum(bool(t["crash"]) for t in trace),
             "tables": len(tables), "rows": sum(eng.db.execute(f"SELECT COUNT(*) FROM '{t}'").fetchone()[0] for t in tables),
             "write_p50_s": statistics.median(t["wall"] for t in trace)}
    eng.db.close()
    (RUN / f"{q['question_id']}.json").write_text(json.dumps({"stats": stats, "trace": trace}, indent=1))
    print(f"[fluid] {stats}", flush=True)
    return stats


# ------------------------------------------------------------------ reading
def agent_evidence(eng: Engine, question: str, tag: str, max_steps: int = 5) -> str:
    catalog, steps = eng.catalog(samples=2), []
    for _ in range(max_steps):
        p = AGENT_PROMPT.format(user="the user and their AI assistant", catalog=catalog, question=question,
                                steps="\n".join(steps) or "(none yet)")
        out, _ = chat_json(MODEL, p, STEP_SCHEMA, schema_name="step", tag=tag, max_tokens=4000)
        act, arg = out["action"], out["argument"]
        if act == "answer":
            break
        if act == "sql":
            rows, err = eng.query(arg)
            result = f"ERROR {err}" if err else json.dumps(rows[:60], ensure_ascii=False, default=str)
        elif act == "lookup":
            result = "\n".join(lookup(eng, arg))
        else:
            result = "\n".join(search_log(eng, arg))
        steps.append(f"[{act}] {arg}\n-> {result[:5000]}")
    return "\n\n".join(steps) or "(nothing found)"


def build_prompt(mode: str, q: dict) -> str:
    date = q["question_date"]
    if mode == "full":
        return HISTORY_COT.format(history_text(q), date, q["question"])
    eng = Engine(str(RUN / f"{q['question_id']}.sqlite"), strict=True, normalize=True)
    if mode == "fluid:rdb":
        return FACTS_COT.format(eng.readable_text(), date, q["question"])
    if mode == "fluid:rdb+log":
        return HISTORY_AND_FACTS_COT.format(f"{history_text(q)}\n\nUser facts (FluidDB):\n{eng.readable_text()}", date, q["question"])
    if mode == "fluid:agent":
        return FACTS_COT.format(agent_evidence(eng, q["question"], f"lme:agent:{q['question_id']}"), date, q["question"])
    raise ValueError(mode)


def evaluate(modes: list[str], workers: int = 8):
    qs = questions()
    for mode in modes:
        def run(q):
            t0 = time.time()
            p = build_prompt(mode, q)
            t_ctx = time.time() - t0
            text, call = chat(MODEL, [{"role": "user", "content": p}], tag=f"lme:{mode}", max_tokens=8000)
            abstention = q["question_id"].endswith("_abs")
            check = get_anscheck_prompt(q["question_type"], q["question"], q["answer"], text, abstention=abstention)
            verdict, _ = chat(JUDGE, [{"role": "user", "content": check}], tag="lme:judge", max_tokens=10)
            return {"qid": q["question_id"], "type": q["question_type"], "abstention": abstention,
                    "correct": "yes" in verdict.lower(), "answer": text[-400:], "gold": str(q["answer"])[:300],
                    "prompt_tokens": call.input_tokens, "context_s": round(t_ctx, 3)}

        n0, t0 = len(LEDGER.calls), time.time()
        with ThreadPoolExecutor(workers) as pool:
            results = list(pool.map(run, qs))
        cats: dict = {}
        for r in results:
            cats.setdefault(r["type"], []).append(r["correct"])
        res = {"accuracy": round(sum(r["correct"] for r in results) / len(results), 4), "n": len(results),
               "by_type": {k: round(sum(v) / len(v), 4) for k, v in sorted(cats.items())},
               "abstention": round(statistics.mean(r["correct"] for r in results if r["abstention"]), 4)
               if any(r["abstention"] for r in results) else None,
               "prompt_tokens_per_q": int(statistics.mean(r["prompt_tokens"] for r in results)),
               "cost": round(sum(c.cost for c in LEDGER.calls[n0:]), 3), "wall_s": round(time.time() - t0),
               "results": results}
        report = json.loads(OUT.read_text()) if OUT.exists() else {}  # another process may have saved meanwhile
        report[mode] = res
        OUT.write_text(json.dumps(report, indent=1, ensure_ascii=False))
        print(f"{mode:14s} acc={res['accuracy']:.4f} n={res['n']} {res['by_type']} abst={res['abstention']} "
              f"tokens/q={res['prompt_tokens_per_q']} cost=${res['cost']}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["sample", "ingest", "evaluate"])
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--modes", nargs="+", default=["full", "fluid:rdb", "fluid:rdb+log"])
    ap.add_argument("--workers", type=int, default=10)
    args = ap.parse_args()
    if args.cmd == "sample":
        ids = sample(args.n)
        print(len(ids), "questions")
    elif args.cmd == "ingest":
        RUN.mkdir(parents=True, exist_ok=True)
        with ThreadPoolExecutor(args.workers) as pool:
            stats = list(pool.map(ingest_one, questions()))
        print(json.dumps({"questions": len(stats), "crashes": sum(s["crashes"] for s in stats),
                          "rows_mean": statistics.mean(s["rows"] for s in stats)}, indent=1))
    else:
        evaluate(args.modes, args.workers)
