"""Evaluate LLM-free querying: Jev routes each question to a compiled SQL template.

  .venv/bin/python -m lab.bench.jev_queries

The library is compiled once (Claude Sonnet 5, sees only the schema and sample data, never the QA);
routing calls are made live (cache off) so latencies are real.
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

from lab.bench.query_modes import DB_RUN, ROWS_JUDGE, ROWS_JUDGE_SCHEMA
from lab.common.judge import JUDGE_MODEL, SCORES
from lab.common.llm import LAB_DIR, LEDGER, chat_json
from lab.systems.engine import Engine
from lab.systems.jev_query import QueryLibrary


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--v2", action="store_true")
    ap.add_argument("--dataset", default="life_stream")
    ap.add_argument("--run", default=DB_RUN, help="run directory under lab/runs holding db.sqlite")
    ap.add_argument("--compile-model", default="anthropic:claude-sonnet-5")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    v2 = args.v2
    ds = json.loads((LAB_DIR / "datasets" / f"{args.dataset}.json").read_text())
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "db.sqlite")
    shutil.copy(LAB_DIR / "runs" / args.run / "db.sqlite", path)
    now = datetime.fromisoformat(ds["now"])
    lib = QueryLibrary(path, ds["user"], v2=v2)
    start = time.perf_counter()
    lib.compile(Engine(path).catalog(samples=3), model=args.compile_model)
    compile_s = time.perf_counter() - start
    compile_cost = sum(c.cost for c in LEDGER.calls if c.tag == "jq:compile")
    print(f"compiled {len(lib.templates)} valid templates in {compile_s:.1f}s (${compile_cost:.3f})")
    for t in lib.templates:
        print(f"  {t['id']}: {t['description'][:90]}  params={[p['kind'] for p in t['params']]}")

    items = []
    for q in ds["qa"]:
        t0 = time.perf_counter()
        try:
            res = lib.ask(q["question"], now, cache=False)
        except Exception as e:
            res = {"template": None, "error": f"{type(e).__name__}: {e}", "rows": None}
        res["wall"] = time.perf_counter() - t0
        items.append({"qid": q["id"], "question": q["question"], "gold": q["answer"], **res})
        print(f"{q['id']} {res.get('template') or 'FALLBACK':32s} conf={res.get('confidence', 0):.2f} "
              f"{res['wall']:.2f}s args={res.get('args')}", flush=True)

    covered = [i for i in items if i["template"]]

    def jr(i):
        out, _ = chat_json(JUDGE_MODEL, ROWS_JUDGE.format(question=i["question"], gold=i["gold"],
                                                          rows=json.dumps(i["rows"], ensure_ascii=False, default=str)[:20000]),
                           ROWS_JUDGE_SCHEMA, tag="judge", effort="low", max_tokens=4000)
        return out
    with ThreadPoolExecutor(8) as pool:
        verdicts = list(pool.map(jr, covered))
    for i, v in zip(covered, verdicts):
        i["verdict"], i["why"] = v["verdict"], v["reason"]

    # Jev checks whether the rows answer the question; below the threshold the question goes to the LLM path.
    from lab.common import jev
    vq = {"answers": jev.noul("Do the `rows` contain the information needed to answer `question`?")}
    checks = jev.ask_many([({"question": i["question"], "rows": (i["rows"] or [])[:40]}, vq) for i in covered], workers=16, tag="jq:verify")
    for i, c in zip(covered, checks):
        i["verify"] = c["answers"]["noul"]
    direct = [i for i in covered if i["verify"] >= 0.3]
    route_calls = [c for c in LEDGER.calls if c.tag == "jq:route"]
    walls = sorted(i["wall"] for i in items)
    report = {
        "templates": lib.templates, "compile_s": round(compile_s, 1), "compile_cost": round(compile_cost, 4),
        "coverage": round(len(covered) / len(items), 3),
        "covered_score": round(sum(SCORES[i["verdict"]] for i in covered) / len(covered), 3) if covered else None,
        "answered_directly": f"{len(direct)}/{len(items)}",
        "direct_correct": round(sum(SCORES[i["verdict"]] for i in direct) / len(direct), 3) if direct else None,
        "p50_s": round(statistics.median(walls), 3), "p90_s": round(walls[int(0.9 * (len(walls) - 1))], 3),
        "cost_per_question": round(sum(c.cost for c in route_calls) / len(items), 6),
        "jev_input_tokens": int(statistics.mean(c.input_tokens for c in route_calls)),
        "items": items,
    }
    print(json.dumps({k: v for k, v in report.items() if k not in ("templates", "items")}, indent=1))
    name = args.out or ("bench_jev_queries_v2.json" if v2 else "bench_jev_queries.json")
    (LAB_DIR / "results" / name).write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
