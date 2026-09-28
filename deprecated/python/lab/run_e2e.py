"""End-to-end run: ingest a message stream into a memory system, then answer QA.

  .venv/bin/python -m lab.run_e2e --system legacy2024 --model openai:gpt-4-turbo --dataset life_stream

Writes lab/runs/<run>/result.json (per-message traces, answers, verdicts, costs,
final DB dump). Re-running is free: every model call is cached.
"""
# @ref LLP 0002.000 — end-to-end runs of round 1
from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from lab.common.judge import judge_many
from lab.common.llm import LAB_DIR, LEDGER

DATASETS = LAB_DIR / "datasets"
RUNS = LAB_DIR / "runs"


def make_system(name: str, model: str, db_path: str, user: str, **opts):
    if name == "legacy2024":
        from lab.systems.legacy2024 import Legacy2024
        return Legacy2024(model, db_path, user)
    if name.startswith("v2"):
        from lab.systems.fluid_v2 import FluidV2
        return FluidV2(model, db_path, user, variant=name, **opts)
    raise ValueError(name)


def run(system_name: str, model: str, dataset: str, limit: int | None = None, qa_limit: int | None = None,
        tag: str = "", **opts) -> dict:
    ds = json.loads((DATASETS / f"{dataset}.json").read_text())
    run_name = f"{system_name}__{model.replace(':', '_').replace('/', '_')}__{dataset}{tag}"
    run_dir = RUNS / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    db_path = run_dir / "db.sqlite"
    for suffix in ("", "-wal", "-shm"):
        Path(f"{db_path}{suffix}").unlink(missing_ok=True)
    system = make_system(system_name, model, str(db_path), ds["user"], **opts)

    messages = ds["messages"][:limit] if limit else ds["messages"]
    ingest = []
    for msg in messages:
        n0 = len(LEDGER.calls)
        start = time.time()
        try:
            trace = system.remember(msg)
            crashed = None
        except Exception as e:  # a crash loses this message but not the run
            trace, crashed = {}, f"{type(e).__name__}: {e}"
        calls = LEDGER.calls[n0:]
        ingest.append({
            "id": msg["id"], "kind": msg["kind"], "trace": trace, "crashed": crashed,
            "wall": round(time.time() - start, 3),
            "llm_latency": round(sum(c.latency for c in calls), 3),
            "cost": round(sum(c.cost for c in calls), 6),
            "calls": [f"{c.model}" for c in calls],
        })
        status = "CRASH " + crashed if crashed else f"errors={len(trace.get('errors', []))}"
        print(f"[{run_name}] {msg['id']} {status} {ingest[-1]['llm_latency']}s ${ingest[-1]['cost']:.4f}", flush=True)

    qa = ds["qa"][:qa_limit] if qa_limit else ds["qa"]

    def do_ask(q):
        n0 = time.time()
        try:
            res = system.ask(q["question"], ds["now"])
        except Exception as e:
            res = {"answer": f"ERROR {type(e).__name__}: {e}", "context": None}
        res["wall"] = round(time.time() - n0, 3)
        return res

    with ThreadPoolExecutor(6) as pool:
        answers = list(pool.map(do_ask, qa))
    verdicts = judge_many([(q["question"], q["answer"], a["answer"]) for q, a in zip(qa, answers)])

    qa_rows = []
    for q, a, v in zip(qa, answers, verdicts):
        qa_rows.append({**q, "system_answer": a["answer"], "verdict": v["verdict"], "score": v["score"],
                        "judge_reason": v["reason"], "wall": a["wall"], "context": a.get("context"),
                        "extra": {k: val for k, val in a.items() if k not in ("answer", "context", "wall")}})

    write_calls = [c for c in LEDGER.calls if not c.tag.startswith("judge") and (":write" in c.tag or ":gate" in c.tag or ":er" in c.tag or c.tag.endswith(":write-jev"))]
    result = {
        "run": run_name, "system": system_name, "model": model, "dataset": dataset, "opts": opts,
        "accuracy": round(sum(r["score"] for r in qa_rows) / len(qa_rows), 4),
        "by_category": _by_category(qa_rows),
        "ingest": {
            "messages": len(ingest),
            "crashes": sum(1 for i in ingest if i["crashed"]),
            "sql_errors": sum(len(i["trace"].get("errors", [])) for i in ingest),
            "cost": round(sum(i["cost"] for i in ingest), 4),
            "llm_latency_mean": round(sum(i["llm_latency"] for i in ingest) / len(ingest), 3),
            "wall_mean": round(sum(i["wall"] for i in ingest) / len(ingest), 3),
            "llm_calls": sum(len([c for c in i["calls"] if not c.startswith("jev")]) for i in ingest),
            "jev_calls": sum(len([c for c in i["calls"] if c.startswith("jev")]) for i in ingest),
        },
        "qa_cost": round(sum(c.cost for c in LEDGER.calls if ":read" in c.tag or ":answer" in c.tag or ":read-jev" in c.tag), 4),
        "qa_wall_mean": round(sum(r["wall"] for r in qa_rows) / len(qa_rows), 3),
        "ledger": LEDGER.summary(),
        "ingest_trace": ingest,
        "qa": qa_rows,
        "db": system.dump(),
    }
    (run_dir / "result.json").write_text(json.dumps(result, indent=1, ensure_ascii=False, default=str))
    print(json.dumps({k: result[k] for k in ("run", "accuracy", "by_category", "ingest", "qa_cost", "qa_wall_mean")}, indent=1))
    return result


def _by_category(rows):
    cats: dict = {}
    for r in rows:
        cats.setdefault(r["category"], []).append(r["score"])
    return {c: round(sum(s) / len(s), 3) for c, s in sorted(cats.items())}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--system", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--qa-limit", type=int)
    ap.add_argument("--tag", default="")
    ap.add_argument("--effort", help="planner reasoning effort (v2 systems), e.g. none / low")
    args = ap.parse_args()
    opts = {"effort": args.effort} if args.effort else {}
    run(args.system, args.model, args.dataset, args.limit, args.qa_limit, args.tag, **opts)
