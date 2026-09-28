"""Deterministic reads on the simulated year: Jev fills a closed query form, SQL answers, no LLM on the path.

  LAB_CACHE_ONLY=llm .venv/bin/python -m lab.bench.det_read      (Jev calls only; any LLM call would raise)

Database: v2.3, Luna low, after sleep (the year's best). Questions: the 60 at 12 months. Grading: Jev as judge
(lab/common/grade.py; 96.7% agreement with the Opus judge on 300 year answers). The hybrid falls back to the cached
Luna tool agent below a confidence threshold, so its fallback answers cost nothing here.
"""
# @ref LLP 0002.002 — a round-3 bench
from __future__ import annotations

import argparse
import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from lab.bench.year import DS, run_dir
from lab.common.grade import jev_grade
from lab.common.llm import LAB_DIR, LEDGER
from lab.systems.det_reader import DetReader

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="db_sleep.sqlite", help="database file in the v2.3 Luna low run directory")
    ap.add_argument("--out", default="bench_det_read.json")
    args = ap.parse_args()
    ds = json.loads(DS.read_text())
    now, qa = datetime.fromisoformat(ds["checkpoints"]["m12"]), ds["qa"]
    reader = DetReader(str(run_dir("low", "v23", "gpt-6-luna") / args.db), ds["user"], now)
    graded = json.loads((LAB_DIR / "results" / "judge_pairs_graded.json").read_text())
    fallback = {p["question"]: p for p in graded if p["reader"] == "agent"}          # Luna tool agent
    sol = {p["question"]: p for p in graded if p["reader"] == "sol-agent"}

    def run(q):
        n0, t0 = len(LEDGER.calls), time.time()
        out = reader.ask(q["question"])
        wall = time.time() - t0
        jev_latency = max((c.latency for c in LEDGER.calls[n0:] if c.tag.startswith("det")), default=0)
        g = jev_grade(q["question"], q["answer"], out["answer"])
        return {**out, "question": q["question"], "gold": q["answer"], "category": q["category"],
                "score": g["score"], "wall_s": round(wall, 3), "jev_s": round(jev_latency, 3)}

    n0 = len(LEDGER.calls)
    with ThreadPoolExecutor(6) as pool:
        results = list(pool.map(run, qa))
    cost = sum(c.cost for c in LEDGER.calls[n0:] if c.tag.startswith("det"))
    n = len(results)
    summary = {"deterministic_accuracy": round(sum(r["score"] for r in results) / n, 4),
               "wall_p50_s": round(statistics.median(r["wall_s"] for r in results), 3),
               "wall_p90_s": round(sorted(r["wall_s"] for r in results)[int(0.9 * n)], 3),
               "cost_per_question": round(cost / n, 6),
               "speculation_hit_rate": round(sum(r["speculation_hit"] for r in results) / n, 3),
               "luna_agent_accuracy": round(sum(fallback[r["question"]]["jev"]["score"] for r in results) / n, 4),
               "sol_agent_accuracy": round(sum(sol[r["question"]]["jev"]["score"] for r in results) / n, 4),
               "by_threshold": []}
    for tau in (0.0, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8):
        # answer without an LLM when Jev is confident and the database gave an answer; otherwise fall back
        own = lambda r: r["confidence"] >= tau and r["answer"] != "I don't know."
        mine = [r for r in results if own(r)]
        hybrid = sum(r["score"] if own(r) else fallback[r["question"]]["jev"]["score"] for r in results) / n
        summary["by_threshold"].append({
            "threshold": tau, "answered_without_llm": len(mine),
            "accuracy_of_those": round(sum(r["score"] for r in mine) / len(mine), 4) if mine else None,
            "hybrid_accuracy_with_luna_agent_fallback": round(hybrid, 4)})
    # the same, gated by Jev's check of the computed answer ("does this result answer the question?")
    summary["by_verifier"] = []
    for theta in (0.5, 0.6, 0.7, 0.8, 0.9):
        own = lambda r: (r.get("verified") or 0) >= theta and r["answer"] != "I don't know."
        mine = [r for r in results if own(r)]
        hybrid = sum(r["score"] if own(r) else fallback[r["question"]]["jev"]["score"] for r in results) / n
        summary["by_verifier"].append({
            "threshold": theta, "answered_without_llm": len(mine),
            "accuracy_of_those": round(sum(r["score"] for r in mine) / len(mine), 4) if mine else None,
            "hybrid_accuracy_with_luna_agent_fallback": round(hybrid, 4)})
    cats: dict = {}
    for r in results:
        cats.setdefault(r["category"], []).append(r["score"])
    summary["by_category"] = {c: round(sum(v) / len(v), 3) for c, v in sorted(cats.items())}
    (LAB_DIR / "results" / args.out).write_text(json.dumps({"summary": summary, "results": results}, indent=1, ensure_ascii=False))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
