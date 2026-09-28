"""Mem0, the most-used open-source AI memory library, on the same simulated year: a baseline for FluidDB.

  ingest:   .venv/bin/python -m lab.bench.mem0_year ingest
  evaluate: LAB_ANTHROPIC_VIA=openrouter .venv/bin/python -m lab.bench.mem0_year evaluate

Mem0 2.2 OSS with its default pipeline (one additive extraction call per message; hybrid search), set up in
lab/systems/mem0_baseline.py. Reading: search results (with dates) -> the same answer prompt and judge as every
other year mode. k = 0 puts every memory in the prompt (oldest first), like the full-dump FluidDB modes.
"""
# @ref LLP 0002.001 — a round-2 bench
from __future__ import annotations

import argparse
import json
import shutil
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from lab.bench.baselines import answer
from lab.bench.year import DS, SOL, _save
from lab.common.judge import judge_many
from lab.common.llm import LAB_DIR, LEDGER, chat
from lab.systems import mem0_baseline as m0
from lab.systems.base import ANSWER_PROMPT, render_input

RUN = LAB_DIR / "runs" / "year__mem0__gpt-6-luna__low"


def ingest():
    ds = json.loads(DS.read_text())
    if RUN.exists():
        shutil.rmtree(RUN)
    m = m0.memory(RUN, "year")
    m6 = datetime.fromisoformat(ds["checkpoints"]["m6"])
    trace, growth, snap_done = [], [], False
    for i, msg in enumerate(ds["messages"]):
        ts = datetime.fromisoformat(msg["ts"])
        if not snap_done and ts > m6:
            shutil.copytree(RUN / "qdrant", RUN / "m6" / "qdrant")
            shutil.copy(RUN / "history.db", RUN / "m6" / "history.db")
            snap_done = True
        u0, t0 = dict(m0.USAGE), time.time()
        added, err = m0.add(m, [{"role": "user", "content": render_input(msg["text"])}], ts, "user", {"msg_id": msg["id"]})
        trace.append({"id": msg["id"], "kind": msg["kind"], "added": len(added), "crash": err, "wall": round(time.time() - t0, 2),
                      "in": m0.USAGE["in"] - u0["in"], "out": m0.USAGE["out"] - u0["out"]})
        if (i + 1) % 50 == 0 or i == len(ds["messages"]) - 1:
            growth.append({"messages": i + 1, "ts": msg["ts"], "memories": len(m0.all_memories(m, "user")),
                           "cost": round(m0.cost(), 4), "crashes": sum(1 for t in trace if t["crash"])})
            print(f"[mem0] {i + 1}/{len(ds['messages'])} {growth[-1]}", flush=True)
    walls = sorted(t["wall"] for t in trace)
    (RUN / "ingest.json").write_text(json.dumps({
        "trace": trace, "growth": growth, "usage": m0.USAGE,
        "write_p50_s": statistics.median(walls), "write_p90_s": walls[int(0.9 * len(walls))]}, indent=1))


def _line(r: dict) -> str:
    return f"[{m0.created(r)[:16].replace('T', ' ')}] {r['memory']}"


def answer_with(model: str, data: str, question: str, now: datetime, user: str, tag: str) -> str:
    prompt = ANSWER_PROMPT.format(user=user, now=now.strftime("%Y-%m-%d %H:%M"), weekday=now.strftime("%A"), data=data,
                                  question=question)
    return chat(model, [{"role": "user", "content": prompt}], tag=tag, max_tokens=4000)[0].strip()


def evaluate(checkpoints=("m6", "m12"), ks=(20, 100, 0), answer_model: str | None = None):
    """answer_model=None answers with GPT-6 Luna like every other year mode; SOL gives the Sol-reader comparison."""
    ds = json.loads(DS.read_text())
    for cp, qa_key in (("m6", "qa_m6"), ("m12", "qa")):
        if cp not in checkpoints:
            continue
        now, qa = datetime.fromisoformat(ds["checkpoints"][cp]), ds[qa_key]
        m = m0.memory(RUN / "m6" if cp == "m6" else RUN, "year")
        everything = sorted(m0.all_memories(m, "user"), key=m0.created)
        for k in ks:
            name = f"mem0 {'all' if k == 0 else f'top{k}'}" + (" (Sol answers)" if answer_model else "")
            latencies = []

            def read(q, k=k, latencies=latencies):
                t0 = time.time()
                hits = everything if k == 0 else m0.search(m, q["question"], "user", k)
                latencies.append(time.time() - t0)
                data = "MEMORIES (facts extracted from past conversations; the date each was recorded):\n" + \
                       "\n".join(_line(r) for r in hits)
                if answer_model:
                    return answer_with(answer_model, data, q["question"], now, ds["user"], f"year:{cp}:mem0sol:{k}")
                return answer(data, q["question"], now, ds["user"], f"year:{cp}:mem0:{k}")

            n0 = len(LEDGER.calls)
            with ThreadPoolExecutor(4) as pool:
                answers = list(pool.map(read, qa))
            calls = LEDGER.calls[n0:]
            verdicts = judge_many([(q["question"], q["answer"], a) for q, a in zip(qa, answers)])
            cats: dict = {}
            for q, v in zip(qa, verdicts):
                cats.setdefault(q["category"], []).append(v["score"])
            res = {"accuracy": round(sum(v["score"] for v in verdicts) / len(verdicts), 4),
                   "by_category": {c: round(sum(s) / len(s), 3) for c, s in sorted(cats.items())},
                   "input_tokens_per_q": int(sum(c.input_tokens for c in calls if c.tag.startswith("year")) / len(qa)),
                   "retrieval_p50_s": round(statistics.median(latencies), 3), "memories": len(everything),
                   "failures": [{"q": q["question"], "gold": q["answer"], "got": a[:220], "verdict": v["verdict"]}
                                for q, a, v in zip(qa, answers, verdicts) if v["score"] < 1]}
            print(f"{cp} {name:12s} acc={res['accuracy']:.3f} tokens/q={res['input_tokens_per_q']} "
                  f"search p50={res['retrieval_p50_s']}s memories={len(everything)} {res['by_category']}", flush=True)
            _save({cp: {name: res}})
        m.vector_store.client.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["ingest", "evaluate"])
    ap.add_argument("--checkpoints", nargs="+", default=["m6", "m12"])
    ap.add_argument("--k", nargs="+", type=int, default=[20, 100, 0])
    ap.add_argument("--sol", action="store_true", help="answer with GPT-6 Sol instead of Luna")
    args = ap.parse_args()
    if args.cmd == "ingest":
        ingest()
    else:
        evaluate(args.checkpoints, tuple(args.k), SOL if args.sol else None)
