"""Jev as a schema critic: which pairs of tables store the same kind of thing (merge candidates)?

  .venv/bin/python -m lab.bench.schema_critic
"""
# @ref LLP 0002.000 — a round-1 bench
from __future__ import annotations

import itertools
import json

from lab.bench.structure import load_db
from lab.common import jev
from lab.common.llm import LAB_DIR

RUNS = ["legacy2024__openai_gpt-4-turbo__alex_rivera", "legacy2024__openai_gpt-4-turbo__life_stream",
        "legacy2024__openai_gpt-6-luna__life_stream", "v2__anthropic_claude-sonnet-5__life_stream",
        "v21jev__openai_gpt-6-luna__life_stream", "v21jev__openai_gpt-6-luna__alex_rivera"]
Q = {"same": jev.noul("Do `table_a` and `table_b` store the same kind of thing, so that their rows belong in one table?")}


def describe(name, t):
    return {"name": name, "description": t["description"], "columns": [c for c, _, _ in t["columns"]],
            "example_rows": t["rows"][:3]}


def main():
    report = {}
    for run in RUNS:
        tables = load_db(LAB_DIR / "runs" / run / "db.sqlite")
        pairs = list(itertools.combinations(sorted(tables), 2))
        jobs = [({"table_a": describe(a, tables[a]), "table_b": describe(b, tables[b])}, Q) for a, b in pairs]
        ans = jev.ask_many(jobs, workers=32, tag="critic")
        scored = sorted(((a, b, x["same"]["noul"]) for (a, b), x in zip(pairs, ans)), key=lambda p: -p[2])
        flagged = [(a, b, round(s, 2)) for a, b, s in scored if s >= 0.5]
        report[run] = {"tables": len(tables), "pairs": len(pairs), "flagged": flagged}
        print(f"\n== {run}: {len(tables)} tables, {len(pairs)} pairs, flagged {len(flagged)}")
        for a, b, s in flagged[:12]:
            print(f"   {s:.2f}  {a}  <->  {b}")
    (LAB_DIR / "results" / "bench_schema_critic.json").write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
