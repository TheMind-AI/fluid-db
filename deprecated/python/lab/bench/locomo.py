"""LoCoMo (Snap Research, ACL 2024) under mem0's memory-benchmarks protocol: FluidDB vs Mem0 OSS vs full context.

  ingest:   .venv/bin/python -m lab.bench.locomo ingest --system fluid     (the 10 conversations run in parallel)
            .venv/bin/python -m lab.bench.locomo ingest --system mem0
  evaluate: .venv/bin/python -m lab.bench.locomo evaluate --modes full mem0:top200 fluid:rdb fluid:rdb+log fluid:agent

Protocol, from github.com/mem0ai/memory-benchmarks (prompts vendored in lab/bench/external/locomo_prompts.py):
categories 1-4 (1,540 questions); their answer prompt (context oldest first, final answer after "ANSWER:"); their
lenient binary judge without evidence, run by gpt-5 as in their published results (low reasoning effort here).
Every system answers with GPT-6 Luna. Whatever a system retrieves goes where their prompt puts the memories.

Ingestion: Mem0 gets one turn per add (as in their harness: first speaker = user, second = assistant). FluidDB gets
chunks of up to 8 turns of a session, stamped with the session's date, through the unchanged v2.3 pipeline.
"""
# @ref LLP 0002.001 — a round-2 bench
from __future__ import annotations

import argparse
import json
import re
import shutil
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from lab.bench.external.locomo_prompts import (ANSWER_GENERATION_PROMPT, CATEGORIES_TO_EVALUATE, CATEGORY_NAMES,
                                               JUDGE_SYSTEM_PROMPT, _to_human_date, get_answer_generation_prompt,
                                               get_judge_prompt, preprocess_answer)
from lab.common.llm import LAB_DIR, LEDGER, chat, chat_json
from lab.systems.engine import Engine
from lab.systems.fluid_v2 import FluidV2
from lab.systems.reader import lookup, search_log, STEP_SCHEMA

DS = LAB_DIR / "datasets" / "external" / "locomo10.json"
RUN = LAB_DIR / "runs" / "locomo"
OUT = LAB_DIR / "results" / "bench_locomo.json"
MODEL = "openai:gpt-6-luna"
JUDGE = "openai:gpt-5"
CHUNK = 8
JUDGE_SCHEMA = {"type": "object", "properties": {"reasoning": {"type": "string"},
                                                 "label": {"type": "string", "enum": ["CORRECT", "WRONG"]}},
                "required": ["reasoning", "label"], "additionalProperties": False}


def parse_date(s: str) -> datetime:
    for fmt in ("%I:%M %p on %d %B, %Y", "%I:%M %p on %d %b, %Y"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            pass
    raise ValueError(s)


def sessions(conv: dict) -> list[tuple[str, datetime, str, list]]:
    keys = [k for k in conv if re.fullmatch(r"session_\d+", k) and conv[k]]
    out = [(k, parse_date(conv[f"{k}_date_time"]), conv[f"{k}_date_time"], conv[k]) for k in keys]
    return sorted(out, key=lambda s: s[1])


def turn_text(t: dict) -> str:
    """Same rendering as their session_to_chunks (image captions inlined)."""
    text, blip, query = t.get("text", ""), t.get("blip_caption", ""), t.get("query", "")
    tag = (f"[Sharing image - query: {query}. The image shows: {blip}]" if query and blip else
           f"[Sharing image - query for: {query}]" if query else f"[Sharing image that shows: {blip}]" if blip else "")
    text = f"{text} {tag}" if text and tag else text or tag
    return f"{t['speaker']}: {text}" if text else ""


def data() -> list[dict]:
    return json.loads(DS.read_text())


# ------------------------------------------------------------------ ingestion
def ingest_fluid(i: int, sample: dict) -> dict:
    c = sample["conversation"]
    a, b = c["speaker_a"], c["speaker_b"]
    path = RUN / "fluid" / f"conv{i}.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    for f in path.parent.glob(f"conv{i}.sqlite*"):
        f.unlink()
    system = FluidV2(MODEL, str(path), f"{a} and {b}", variant="v23", effort="low")
    trace = []
    for key, when, raw, turns in sessions(c):
        lines = [x for x in map(turn_text, turns) if x]
        for j in range(0, len(lines), CHUNK):
            msg = {"id": f"{key}_c{j // CHUNK}", "kind": "chat", "ts": (when + timedelta(minutes=j // CHUNK)).isoformat(),
                   "text": f"Conversation between {a} and {b} ({raw}):\n" + "\n".join(lines[j:j + CHUNK])}
            t0 = time.time()
            try:
                tr, err = system.remember(msg), None
            except Exception as e:
                tr, err = {}, f"{type(e).__name__}: {e}"[:300]
            trace.append({"id": msg["id"], "skipped": bool(tr.get("skipped")), "ops": len(tr.get("ops") or []),
                          "errors": len(tr.get("errors") or []), "crash": err, "wall": round(time.time() - t0, 2)})
            if len(trace) % 25 == 0:
                print(f"[fluid] conv{i} {len(trace)} chunks, skipped {sum(t['skipped'] for t in trace)}, "
                      f"crashes {sum(bool(t['crash']) for t in trace)}", flush=True)
    eng = system.engine
    tables = eng.tables()
    stats = {"conv": i, "chunks": len(trace), "skipped": sum(t["skipped"] for t in trace),
             "crashes": sum(bool(t["crash"]) for t in trace), "tables": len(tables),
             "rows": sum(eng.db.execute(f"SELECT COUNT(*) FROM '{t}'").fetchone()[0] for t in tables),
             "write_p50_s": statistics.median(t["wall"] for t in trace)}
    (RUN / "fluid" / f"conv{i}.json").write_text(json.dumps({"stats": stats, "trace": trace}, indent=1))
    print(f"[fluid] conv{i} {stats}", flush=True)
    return stats


def ingest_mem0(i: int, sample: dict) -> dict:
    from lab.systems import mem0_baseline as m0
    c = sample["conversation"]
    a = c["speaker_a"]
    path = RUN / "mem0" / f"conv{i}"
    shutil.rmtree(path, ignore_errors=True)
    m = m0.memory(path, f"conv{i}")
    trace = []
    for key, when, raw, turns in sessions(c):
        for t in turns:
            text = turn_text(t)
            if not text:
                continue
            t0 = time.time()
            added, err = m0.add(m, [{"role": "user" if t["speaker"] == a else "assistant", "content": text}], when, f"conv{i}")
            trace.append({"id": t.get("dia_id"), "added": len(added), "crash": err, "wall": round(time.time() - t0, 2)})
    stats = {"conv": i, "turns": len(trace), "crashes": sum(bool(t["crash"]) for t in trace),
             "memories": len(m0.all_memories(m, f"conv{i}")), "write_p50_s": statistics.median(t["wall"] for t in trace)}
    m.vector_store.client.close()
    (RUN / "mem0" / f"conv{i}.json").write_text(json.dumps({"stats": stats, "trace": trace}, indent=1))
    print(f"[mem0] conv{i} {stats} cost so far ${m0.cost():.2f}", flush=True)
    return stats


# ------------------------------------------------------------------ reading
def prompt_with(context: str, question: str, reference_date: str) -> str:
    return ANSWER_GENERATION_PROMPT.format(memories=context, question=question, reference_date=reference_date)


def transcript(c: dict) -> str:
    """Every turn as a dated line, in their memory-line format (no top-200 cap: this is the full-context baseline)."""
    lines = ["The following memories are presented in chronological order (oldest to newest).", ""]
    for _, when, _, turns in sessions(c):
        lines += [f"({_to_human_date(when.isoformat())}) {x}" for x in map(turn_text, turns) if x]
    return "\n".join(lines)


def fluid_context(eng: Engine, with_log: bool) -> str:
    out = ("The following is a structured database of everything said in these conversations "
           "(one line per row, links shown as names; _history holds earlier values):\n" + eng.readable_text())
    if with_log:
        log = eng.db.execute("SELECT ts, text FROM _log ORDER BY id").fetchall()
        out += "\n\nRAW CONVERSATION LOG (oldest first):\n" + "\n".join(f"[{ts[:10]}] {text}" for ts, text in log)
    return out


AGENT_PROMPT = """You gather evidence from a database of conversations between {user} to answer a question, one tool
call at a time. The final answer is written later from the evidence you collect, so collect everything relevant.
Tools:
- sql: one read-only SQLite SELECT (counts, dates, filters, joins, _history).
- lookup: "table: words" -> rows of that table whose text matches the words, with links shown as names.
- search_log: words -> the conversation passages that match best (exact wording, details the tables lack).
- answer: stop gathering (put a one-line summary in the argument).

DATABASE CATALOG:
{catalog}

QUESTION: {question}

STEPS SO FAR:
{steps}"""


def agent_evidence(eng: Engine, question: str, user: str, tag: str, max_steps: int = 5) -> str:
    """Our tool-using reader, used purely as a retriever: its tool results become the context for their prompt."""
    catalog, steps = eng.catalog(samples=2), []
    for i in range(max_steps):
        p = AGENT_PROMPT.format(user=user, catalog=catalog, question=question, steps="\n".join(steps) or "(none yet)")
        out, _ = chat_json(MODEL, p, STEP_SCHEMA, schema_name="step", tag=tag, max_tokens=4000)
        act, arg = out["action"], out["argument"]
        if act == "answer":
            break
        if act == "sql":
            rows, err = eng.query(arg)
            result = f"ERROR {err}" if err else json.dumps(rows[:60], ensure_ascii=False, default=str)
        elif act == "lookup":
            result = "\n".join(lookup(eng, arg))
        else:
            result = "\n".join(search_log(eng, arg))
        steps.append(f"[{act}] {arg}\n-> {result[:5000]}")
    return "Evidence gathered from the memory database:\n\n" + "\n\n".join(steps)


def evaluate(modes: list[str], convs: list[int] | None = None, workers: int = 8):
    ds = data()
    for mode in modes:
        items = []  # (conv, qi, qa, context-builder)
        for i, sample in enumerate(ds):
            if convs is not None and i not in convs:
                continue
            c = sample["conversation"]
            ref = sessions(c)[-1][2]
            user = f"{c['speaker_a']} and {c['speaker_b']}"
            if mode.startswith("fluid"):
                eng = Engine(str(RUN / "fluid" / f"conv{i}.sqlite"), strict=True, normalize=True)
            if mode == "full":
                ctx = transcript(c)
                build = lambda q, ctx=ctx, ref=ref: prompt_with(ctx, q["question"], ref)
            elif mode in ("fluid:rdb", "fluid:rdb+log"):
                ctx = fluid_context(eng, mode.endswith("+log"))
                build = lambda q, ctx=ctx, ref=ref: prompt_with(ctx, q["question"], ref)
            elif mode == "fluid:agent":
                build = lambda q, eng=eng, ref=ref, user=user, i=i: prompt_with(
                    agent_evidence(eng, q["question"], user, f"locomo:agent:{i}"), q["question"], ref)
            elif mode.startswith("mem0:top"):
                from lab.systems import mem0_baseline as m0
                k = int(mode.split("top")[1])
                m, lock = m0.memory(RUN / "mem0" / f"conv{i}", f"conv{i}"), threading.Lock()  # one local Qdrant per conversation

                def build(q, m=m, lock=lock, k=k, i=i, ref=ref):
                    with lock:
                        hits = m0.search(m, q["question"], f"conv{i}", k)
                    return get_answer_generation_prompt(q["question"], [{"memory": r["memory"], "created_at": m0.created(r)}
                                                                         for r in hits], reference_date=ref)
            else:
                raise ValueError(mode)
            for qi, qa in enumerate(sample["qa"]):
                if qa.get("category") in CATEGORIES_TO_EVALUATE and "answer" in qa:
                    items.append((i, qi, qa, build))

        def run(item):
            i, qi, qa, build = item
            t0 = time.time()
            p = build(qa)
            t_ctx = time.time() - t0
            text, call = chat(MODEL, [{"role": "user", "content": p}], tag=f"locomo:{mode}", max_tokens=8000)
            ans = text.rsplit("ANSWER:", 1)[-1].strip() if "ANSWER:" in text else text.strip()
            gold = preprocess_answer(qa["category"], str(qa["answer"]))
            verdict, _ = chat_json(JUDGE, get_judge_prompt(qa["category"], qa["question"], gold, ans), JUDGE_SCHEMA,
                                   system=JUDGE_SYSTEM_PROMPT, schema_name="judgment", tag="locomo:judge", effort="low",
                                   max_tokens=4000)
            return {"id": f"conv{i}_q{qi}", "category": CATEGORY_NAMES[qa["category"]], "question": qa["question"],
                    "gold": gold, "answer": ans[:400], "correct": verdict["label"] == "CORRECT",
                    "context_s": round(t_ctx, 3), "prompt_tokens": call.input_tokens}

        n0 = len(LEDGER.calls)
        t0 = time.time()
        with ThreadPoolExecutor(workers) as pool:
            results = list(pool.map(run, items))
        calls = LEDGER.calls[n0:]
        cats: dict = {}
        for r in results:
            cats.setdefault(r["category"], []).append(r["correct"])
        res = {"accuracy": round(sum(r["correct"] for r in results) / len(results), 4), "n": len(results),
               "by_category": {k: round(sum(v) / len(v), 4) for k, v in sorted(cats.items())},
               "prompt_tokens_per_q": int(statistics.mean(r["prompt_tokens"] for r in results)),
               "context_p50_s": round(statistics.median(r["context_s"] for r in results), 3),
               "cost": round(sum(c.cost for c in calls), 3), "wall_s": round(time.time() - t0),
               "results": results}
        report = json.loads(OUT.read_text()) if OUT.exists() else {}  # another process may have saved meanwhile
        report[mode] = res
        OUT.write_text(json.dumps(report, indent=1, ensure_ascii=False))
        print(f"{mode:16s} acc={res['accuracy']:.4f} n={res['n']} {res['by_category']} tokens/q={res['prompt_tokens_per_q']} "
              f"ctx p50={res['context_p50_s']}s cost=${res['cost']}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["ingest", "evaluate"])
    ap.add_argument("--system", choices=["fluid", "mem0"])
    ap.add_argument("--modes", nargs="+", default=["full", "fluid:rdb", "fluid:rdb+log"])
    ap.add_argument("--convs", nargs="+", type=int)
    ap.add_argument("--workers", type=int, default=10)
    args = ap.parse_args()
    if args.cmd == "ingest":
        fn = ingest_fluid if args.system == "fluid" else ingest_mem0
        samples = [(i, s) for i, s in enumerate(data()) if args.convs is None or i in args.convs]
        with ThreadPoolExecutor(args.workers) as pool:
            stats = list(pool.map(lambda x: fn(*x), samples))
        print(json.dumps(stats, indent=1))
    else:
        evaluate(args.modes, args.convs, args.workers)
