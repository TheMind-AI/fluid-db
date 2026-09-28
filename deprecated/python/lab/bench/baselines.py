"""FluidDB against the memories it has to beat, on the same life_stream and questions:

  raw_log    keep every message; put the whole log in the prompt for each question
  markdown   the model maintains one markdown memory document, rewriting it after each message
             (how most assistant memories work today)
  db+log     FluidDB's structured database (v21jev + GPT-6 Luna) plus the raw log, in one prompt

All use GPT-6 Luna (low effort), like the best FluidDB runs, and the same answer prompt and judge.

  LAB_ANTHROPIC_VIA=openrouter .venv/bin/python -m lab.bench.baselines
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

from lab.common.judge import judge_many
from lab.common.llm import LAB_DIR, LEDGER, chat
from lab.systems.base import ANSWER_PROMPT, render_input
from lab.systems.engine import Engine

MODEL = "openai:gpt-6-luna"
MD_PROMPT = """You maintain a markdown memory document about {user} for their personal AI assistant.
Update it with the new message: add new facts, overwrite facts that changed, delete what the user asks to forget
or what was cancelled, and ignore greetings, thanks, small talk and questions. Keep it organized with headings
(people, events, expenses, activities, ...) and keep dates. Return the complete updated document and nothing else.

CURRENT MEMORY:
{memory}

NEW MESSAGE (received {ts}, {weekday}):
{text}"""


def answer(data: str, question: str, now: datetime, user: str, tag: str) -> str:
    prompt = ANSWER_PROMPT.format(user=user, now=now.strftime("%Y-%m-%d %H:%M"), weekday=now.strftime("%A"),
                                  data=data, question=question)
    return chat(MODEL, [{"role": "user", "content": prompt}], tag=tag, max_tokens=4000)[0].strip()


def by_category(qa, verdicts):
    cats: dict = {}
    for q, v in zip(qa, verdicts):
        cats.setdefault(q["category"], []).append(v["score"])
    return {c: round(sum(s) / len(s), 3) for c, s in sorted(cats.items())}


def main():
    ds = json.loads((LAB_DIR / "datasets" / "life_stream.json").read_text())
    now = datetime.fromisoformat(ds["now"])
    report = {}

    # raw log in context
    log = "\n".join(f"[{m['ts'][:16].replace('T', ' ')}] {render_input(m['text'])}" for m in ds["messages"])
    with ThreadPoolExecutor(6) as pool:
        answers = list(pool.map(lambda q: answer(log, q["question"], now, ds["user"], "base:raw"), ds["qa"]))
    raw_calls = [c for c in LEDGER.calls if c.tag == "base:raw"]

    # markdown memory, rewritten after every message
    memory, write_lat = "(empty)", []
    for m in ds["messages"]:
        when = datetime.fromisoformat(m["ts"])
        start = time.time()
        memory, _ = chat(MODEL, [{"role": "user", "content": MD_PROMPT.format(
            user=ds["user"], memory=memory, ts=when.strftime("%Y-%m-%d %H:%M"), weekday=when.strftime("%A"),
            text=render_input(m["text"]))}], tag="base:md-write", max_tokens=16000)
        write_lat.append(time.time() - start)
        print(f"md {m['id']} {len(memory)} chars", flush=True)
    (LAB_DIR / "results" / "markdown_memory.md").write_text(memory)
    with ThreadPoolExecutor(6) as pool:
        md_answers = list(pool.map(lambda q: answer(memory, q["question"], now, ds["user"], "base:md"), ds["qa"]))

    # structured database + raw log (layered memory)
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "db.sqlite")
    shutil.copy(LAB_DIR / "runs" / "v21jev__openai_gpt-6-luna__life_stream" / "db.sqlite", path)
    both = (f"STRUCTURED DATABASE (current state, maintained from the messages):\n{Engine(path).dump_text()}\n\n"
            f"RAW MESSAGE LOG (what the user said, oldest first):\n{log}")
    with ThreadPoolExecutor(6) as pool:
        hy_answers = list(pool.map(lambda q: answer(both, q["question"], now, ds["user"], "base:hybrid"), ds["qa"]))

    for name, ans, tag in (("raw_log", answers, "base:raw"), ("markdown", md_answers, "base:md"),
                           ("db+log", hy_answers, "base:hybrid")):
        verdicts = judge_many([(q["question"], q["answer"], a) for q, a in zip(ds["qa"], ans)])
        calls = [c for c in LEDGER.calls if c.tag == tag]
        report[name] = {
            "accuracy": round(sum(v["score"] for v in verdicts) / len(verdicts), 4),
            "by_category": by_category(ds["qa"], verdicts),
            "tokens_per_question": int(statistics.mean(c.input_tokens for c in calls)),
            "cost_per_question": round(statistics.mean(c.cost for c in calls), 6),
            "answer_latency_p50": round(statistics.median(c.latency for c in calls), 2),
            "failures": [{"q": q["question"], "gold": q["answer"], "got": a[:200], "verdict": v["verdict"]}
                         for q, a, v in zip(ds["qa"], ans, verdicts) if v["score"] < 1],
        }
    md_writes = [c for c in LEDGER.calls if c.tag == "base:md-write"]
    report["markdown"]["memory_chars"] = len(memory)
    report["markdown"]["write_latency_p50"] = round(statistics.median(write_lat), 2)
    report["markdown"]["write_cost_per_message"] = round(statistics.mean(c.cost for c in md_writes), 6)
    report["raw_log"]["log_chars"] = len(log)
    (LAB_DIR / "results" / "bench_baselines.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))
    for name, r in report.items():
        print(name, {k: v for k, v in r.items() if k != "failures"})


if __name__ == "__main__":
    main()
