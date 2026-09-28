"""Rebuild (question, gold, answer, Opus verdict) pairs from the cache, to validate cheaper judges. Costs nothing:
run with LAB_CACHE_ONLY=1, so any call that is not already cached raises instead of being paid for.

  LAB_CACHE_ONLY=1 .venv/bin/python -m lab.bench.judge_pairs      -> lab/results/judge_pairs.json
"""
# @ref LLP 0002.002 — a round-3 bench
from __future__ import annotations

import json
from datetime import datetime

from lab.bench.baselines import answer
from lab.bench.year import DS, MODEL, SOL, run_dir, sql_answer
from lab.common.judge import judge
from lab.common.llm import LAB_DIR
from lab.systems.engine import Engine
from lab.systems.reader import ask_agent

SPEC = "v23/low:sleep"


def main():
    ds = json.loads(DS.read_text())
    now, qa = datetime.fromisoformat(ds["checkpoints"]["m12"]), ds["qa"]
    eng = Engine(str(run_dir("low", "v23", "gpt-6-luna") / "db_sleep.sqlite"), strict=True, normalize=True)
    readable, catalog = eng.readable_text(), eng.catalog(samples=3)
    own_log = "\n".join(f"[{ts[:16].replace('T', ' ')}] {text}" for ts, text in
                        eng.db.execute("SELECT ts, text FROM _log ORDER BY id"))
    tag = f"year:m12:{SPEC}"
    readers = {
        "rdb": lambda q: answer(f"STRUCTURED DATABASE (current state, links shown as names; _history has earlier values):\n"
                                f"{readable}", q["question"], now, ds["user"], tag + ":rdb"),
        "rdb+log": lambda q: answer(f"STRUCTURED DATABASE (current state, links shown as names; _history has earlier values):\n"
                                    f"{readable}\n\nRAW MESSAGE LOG (what the user said, oldest first; [forgotten] marks erased text):\n"
                                    f"{own_log}", q["question"], now, ds["user"], tag + ":rdblog"),
        "sql": lambda q: sql_answer(eng, catalog, q, now, ds["user"], tag + ":sql"),
        "agent": lambda q: ask_agent(eng, q["question"], now, ds["user"], MODEL, tag + ":agent")["answer"],
        "sol-agent": lambda q: ask_agent(eng, q["question"], now, ds["user"], SOL, tag + ":solagent")["answer"],
    }
    pairs = []
    for name, read in readers.items():
        for q in qa:
            a = read(q)
            v = judge(q["question"], q["answer"], a)
            pairs.append({"reader": name, "category": q["category"], "question": q["question"], "gold": q["answer"],
                          "answer": a, "opus": v["score"]})
        print(name, "opus accuracy", round(sum(p["opus"] for p in pairs if p["reader"] == name) / len(qa), 4), flush=True)
    (LAB_DIR / "results" / "judge_pairs.json").write_text(json.dumps(pairs, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
