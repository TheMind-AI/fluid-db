"""Try FluidDB v2 from the terminal.

  .venv/bin/python -m lab.fluid                      # interactive: type facts, "?question", ".schema", ".quit"
  .venv/bin/python -m lab.fluid remember "Tom's number is +420 777 999 000"
  .venv/bin/python -m lab.fluid ask "What's Tom's number?"
  .venv/bin/python -m lab.fluid schema

State lives in lab/playground.sqlite (use --db to change). Default variant is
v21jev: Jev gates and selects rows, the LLM writes operations and answers.
"""
# @ref LLP 0010#terminal
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime

from lab.common.llm import LAB_DIR, LEDGER
from lab.systems.fluid_v2 import FluidV2


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def remember(db: FluidV2, text: str):
    n0, start = len(LEDGER.calls), time.time()
    trace = db.remember({"id": "cli", "ts": now_iso(), "kind": "?", "text": text})
    spent = sum(c.cost for c in LEDGER.calls[n0:])
    if trace.get("skipped"):
        print(f"  (nothing to store: Jev says '{trace['gate']['intent']}' at {trace['gate']['confidence']:.2f}) "
              f"[{time.time() - start:.1f}s, ${spent:.5f}]")
        return
    print(f"  {trace.get('notes', '')}")
    for op in trace.get("ops", []):
        vals = {v["column"]: v["value"] for v in op.get("values") or []}
        target = f"{op['table']}#{op['row_id']}" if op.get("row_id") else op["table"]
        print(f"    {op['op']:<12} {target} {json.dumps(vals, ensure_ascii=False) if vals else ''}")
    for err in trace.get("errors", []):
        print(f"    ! {err['error']}")
    print(f"  [{time.time() - start:.1f}s, ${spent:.5f}]")


def ask(db: FluidV2, question: str):
    n0, start = len(LEDGER.calls), time.time()
    res = db.ask(question, now_iso())
    spent = sum(c.cost for c in LEDGER.calls[n0:])
    print(f"  {res['answer']}\n  [{time.time() - start:.1f}s, ${spent:.5f}]")


def schema(db: FluidV2):
    print(db.engine.catalog(samples=0))
    for t in db.engine.tables():
        for row in db.engine.rows(t):
            print(f"  {t}#{row['id']} {json.dumps(row, ensure_ascii=False)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", nargs="?", choices=["remember", "ask", "schema"])
    ap.add_argument("text", nargs="?")
    ap.add_argument("--db", default=str(LAB_DIR / "playground.sqlite"))
    ap.add_argument("--model", default="openai:gpt-6-luna")
    ap.add_argument("--variant", default="v21jev")
    ap.add_argument("--user", default="the user")
    args = ap.parse_args()
    db = FluidV2(args.model, args.db, args.user, variant=args.variant)

    if args.command == "remember":
        return remember(db, args.text)
    if args.command == "ask":
        return ask(db, args.text)
    if args.command == "schema":
        return schema(db)

    print("FluidDB v2. Type facts to remember, '?question' to ask, '.schema' to inspect, '.quit' to exit.")
    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        if line in (".quit", ".exit"):
            break
        if line == ".schema":
            schema(db)
        elif line.startswith("?"):
            ask(db, line[1:].strip())
        else:
            remember(db, line)


if __name__ == "__main__":
    main()
