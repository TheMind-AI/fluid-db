"""Run offline consolidation ("sleep") on a finished year database and report what it fixed.

  .venv/bin/python -m lab.bench.sleep --effort none
  .venv/bin/python -m lab.bench.sleep --variant v23 --model openai:gpt-6-sol --effort low
Writes runs/year__<variant>__<model>__<effort>/db_sleep.sqlite (the original db.sqlite is untouched).
"""
# @ref LLP 0002.001 — a round-2 bench
from __future__ import annotations

import argparse
import json
import shutil

from lab.bench.year import run_dir
from lab.common.llm import LAB_DIR, LEDGER
from lab.systems import consolidate
from lab.systems.fluid_v2 import FluidV2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--effort", default="none")
    ap.add_argument("--variant", default="v22")
    ap.add_argument("--model", default="openai:gpt-6-luna", help="the model that ingested the run (replans use it too)")
    args = ap.parse_args()
    d = run_dir(args.effort, args.variant, args.model.split(":")[1])
    shutil.copy(d / "db.sqlite", d / "db_sleep.sqlite")
    ds = json.loads((LAB_DIR / "datasets" / "life_year.json").read_text())
    trace = json.loads((d / "ingest.json").read_text())["trace"]
    skipped = {i + 1 for i, t in enumerate(trace) if t["skipped"]}   # log ids follow message order
    system = FluidV2(args.model, str(d / "db_sleep.sqlite"), ds["user"], variant=args.variant, effort=args.effort)
    eng = system.engine
    before = consolidate.candidate_pairs(eng)
    dropped = consolidate.dropped_messages(eng, skipped)
    print(f"dropped messages (no row or history points at them): {len(dropped)}")
    for i, ts, text in dropped[:15]:
        print(f"   #{i} {ts[:10]} {text[:110]!r}")
    n0 = len(LEDGER.calls)
    recovered = 0
    for i, ts, text in dropped:
        out = consolidate.replan(system, i, ts, text)
        recovered += bool(out.get("ops"))
    still = consolidate.dropped_messages(eng, skipped)
    migrations = consolidate.migrate_misplaced(eng, "openai:gpt-6-sol", ds["now"])
    for m in migrations:
        print(f"migration: {m['source_table']} -> {m['target_table']} where {m['where']!r}: {m.get('applied')} rows. {m['reason'][:120]}")
    merges = consolidate.dedupe(eng, ds["now"])
    report = {"dropped_before": len(dropped), "replanned_with_ops": recovered, "dropped_after": len(still),
              "migrations": migrations, "candidate_pairs": len(before), "merges": merges,
              "cost": round(sum(c.cost for c in LEDGER.calls[n0:]), 4)}
    (d / "sleep.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))
    print(json.dumps(report, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
