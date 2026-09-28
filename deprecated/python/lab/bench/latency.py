"""Question latency measured one question at a time, with caching off.

The end-to-end runs answer six questions concurrently, which penalizes the Jev
read path (many small requests competing). This re-opens a copy of each finished
run's database and times the same questions sequentially.

  LAB_NO_CACHE=1 .venv/bin/python -m lab.bench.latency
"""
from __future__ import annotations

import json
import os
import shutil
import statistics
import tempfile
import time

from lab.common.llm import LAB_DIR, LEDGER
from lab.systems.fluid_v2 import FluidV2

QUESTIONS = ["q02", "q07", "q10", "q13", "q17", "q20", "q24", "q28", "q33", "q37"]
CONFIGS = [
    ("v2", "openai:gpt-6-luna", {}),
    ("v2jev", "openai:gpt-6-luna", {}),
    ("v21jev", "openai:gpt-6-luna", {}),
    ("v2", "anthropic:claude-sonnet-5", {}),
    ("v2jev", "anthropic:claude-sonnet-5", {}),
    ("v21jev", "anthropic:claude-sonnet-5", {}),
]


def main():
    assert os.environ.get("LAB_NO_CACHE"), "run with LAB_NO_CACHE=1 so every call is live"
    ds = json.loads((LAB_DIR / "datasets" / "life_stream.json").read_text())
    qa = [q for q in ds["qa"] if q["id"] in QUESTIONS]
    out = {}
    for variant, model, opts in CONFIGS:
        run = LAB_DIR / "runs" / f"{variant}__{model.replace(':', '_')}__life_stream"
        tmp = tempfile.mkdtemp()
        db_path = os.path.join(tmp, "db.sqlite")
        shutil.copy(run / "db.sqlite", db_path)
        system = FluidV2(model, db_path, ds["user"], variant=variant, **opts)
        walls, costs = [], []
        for q in qa:
            n0, start = len(LEDGER.calls), time.time()
            system.ask(q["question"], ds["now"])
            walls.append(time.time() - start)
            costs.append(sum(c.cost for c in LEDGER.calls[n0:]))
        key = f"{variant} / {model.split(':')[1]}"
        out[key] = {"read": system.opts["read"], "wall_p50": round(statistics.median(walls), 2),
                    "wall_mean": round(statistics.mean(walls), 2), "cost_per_question": round(statistics.mean(costs), 5),
                    "rows_in_db": len(system.engine.all_rows())}
        print(key, out[key], flush=True)
    (LAB_DIR / "results" / "bench_latency.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
