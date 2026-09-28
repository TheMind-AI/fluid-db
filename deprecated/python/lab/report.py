"""Collect every end-to-end run into one markdown table.

  .venv/bin/python -m lab.report
"""
from __future__ import annotations

import json

from lab.common.llm import LAB_DIR

RUNS = LAB_DIR / "runs"


def load_runs():
    out = []
    for p in sorted(RUNS.glob("*/result.json")):
        if "_smoke" in p.parent.name:
            continue
        out.append(json.loads(p.read_text()))
    return out


def schema_stats(r):
    db = r["db"]
    if r["system"] == "legacy2024":
        tables = {t: v for t, v in db.items()}
        n_rows = sum(len(v["rows"]) for v in tables.values())
        n_cols = sum(len(v["columns"]) for v in tables.values())
    else:
        tables = db
        n_rows = sum(len(v["rows"]) for v in tables.values())
        n_cols = sum(len(v["columns"]) + 1 for v in tables.values())
    return len(tables), n_cols, n_rows


def label(r):
    opts = r.get("opts") or {}
    return r["system"] + (" (" + ", ".join(f"{k}={v}" for k, v in opts.items()) + ")" if opts else "")


def main():
    runs = load_runs()
    lines = ["| dataset | system | model | QA score | unanswerable | writes failed | ingest $ | ingest s/msg | QA $ | QA s/q | tables | rows |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(runs, key=lambda r: (r["dataset"], r["system"], r["model"])):
        n_tables, n_cols, n_rows = schema_stats(r)
        ing = r["ingest"]
        failed = ing["sql_errors"] + ing["crashes"]
        unans = r["by_category"].get("unanswerable")
        lines.append(
            f"| {r['dataset']} | {label(r)} | {r['model'].split(':')[1]} | **{r['accuracy']*100:.1f}%** | "
            f"{'' if unans is None else f'{unans*100:.0f}%'} | {failed} | ${ing['cost']:.3f} | {ing['wall_mean']:.1f} | "
            f"${r['qa_cost']:.3f} | {r['qa_wall_mean']:.1f} | {n_tables} | {n_rows} |")
    print("\n".join(lines))
    print()
    cats = sorted({c for r in runs for c in r["by_category"]})
    print("| dataset | system | model | " + " | ".join(cats) + " |")
    print("|---|---|---|" + "---|" * len(cats))
    for r in sorted(runs, key=lambda r: (r["dataset"], r["system"], r["model"])):
        vals = [f"{r['by_category'][c]*100:.0f}" if c in r["by_category"] else "" for c in cats]
        print(f"| {r['dataset']} | {label(r)} | {r['model'].split(':')[1]} | " + " | ".join(vals) + " |")


if __name__ == "__main__":
    main()
