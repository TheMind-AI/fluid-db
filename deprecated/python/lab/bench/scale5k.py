"""5,000+ messages through the tiered writer, then reads on the result, on a small budget.

  ingest: .venv/bin/python -m lab.bench.scale5k ingest [--bootstrap 400] [--budget 1.5]
  writes: .venv/bin/python -m lab.bench.scale5k writes      (write accuracy against the simulator's truth; no model)
  passes: .venv/bin/python -m lab.bench.scale5k passes      (async passes: supersession, typed columns -> db_async.sqlite)
  reads:  .venv/bin/python -m lab.bench.scale5k reads       (both readers on every question, hybrid, Jev judge)

Writer tiers:
  A  patterns compiled by the LLM from the planner's own rows (lab/systems/compiled_writer.py), applied by code
  C  FluidDB v2.3 as before: Jev gate, then the GPT-6 Luna planner (low reasoning)
The first `bootstrap` messages all go to tier C. Patterns are compiled then, and recompiled every 500 messages for
tables that tier C kept writing to (new phrasings, the move to San Francisco). Ingestion stops at `budget` dollars.
"""
# @ref LLP 0002.003 — the round-4 bench
from __future__ import annotations

import argparse
import json
import re
import statistics
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from lab.common.grade import jev_grade
from lab.common.llm import LAB_DIR, LEDGER
from lab.systems.compiled_writer import CompiledWriter
from lab.systems.fluid_v2 import FluidV2

DS = LAB_DIR / "datasets" / "life_scale.json"
RUN = LAB_DIR / "runs" / "scale5k"
MODEL = "openai:gpt-6-luna"
VARIANT = "v23"          # the writer variant (round 8 tries "v24", link-aware write context)


def spent(include_cached: bool = False) -> float:
    """Real spend of this process (cached replays cost nothing); include_cached gives what the run would cost fresh."""
    return sum(c.cost for c in LEDGER.calls if include_cached or not c.cached)


def ingest(bootstrap: int, budget: float, recompile_every: int = 500, limit: int | None = None):
    ds = json.loads(DS.read_text())
    RUN.mkdir(parents=True, exist_ok=True)
    for f in RUN.glob("db.sqlite*"):
        f.unlink()
    system = FluidV2(MODEL, str(RUN / "db.sqlite"), ds["user"], variant=VARIANT, effort="low")
    writer = CompiledWriter(system.engine)
    writer.planner_logs = set()
    writer.use_ops = system.opts.get("compiled_updates", False)
    trace, compiles, since = [], [], Counter()
    for i, msg in enumerate(ds["messages"][:limit]):
        if spent() > budget:
            print(f"budget ${budget} reached at message {i}; stopping", flush=True)
            break
        if i >= bootstrap and (i == bootstrap or (i - bootstrap) % recompile_every == 0):
            t0 = time.time()
            tables = None if i == bootstrap else [t for t, n in since.items() if n >= 10] + \
                [t for t in system.engine.tables() if t not in writer.patterns]
            try:
                done = writer.compile(tables=tables)
            except Exception as e:   # a compile failure must never stop ingestion: the planner keeps writing
                done = [{"error": f"{type(e).__name__}: {e}"[:300]}]
            compiles.append({"at": i, "tables": done, "s": round(time.time() - t0, 1)})
            since.clear()
            print(f"[compile @{i}] {done}", flush=True)
        n0, t0 = len(LEDGER.calls), time.time()
        tier, info = "C", {}
        log_id = system.engine.log(msg["ts"], msg["text"] if isinstance(msg["text"], str) else json.dumps(msg["text"], ensure_ascii=False))
        if i >= bootstrap:
            try:
                plan = writer.plan(msg)
                if plan and not writer.apply(plan, log_id, msg["ts"]):
                    tier, info = "A", {"table": plan["table"], "op": plan.get("op", "insert")}
            except Exception as e:  # a bad pattern must never lose a message: fall back to the planner
                info = {"tier_a_error": f"{type(e).__name__}: {e}"[:200]}
        if tier == "C":
            writer.planner_logs.add(log_id)
            try:
                tr = system.remember(msg, log_id=log_id)
            except Exception as e:
                tr = {"crash": f"{type(e).__name__}: {e}"[:200]}
            if not tr.get("errors") and not tr.get("retry_ops"):
                writer.planner_ops[log_id] = tr.get("ops") or []
            tier = "C-skip" if tr.get("skipped") else "C"
            for op in tr.get("ops") or []:
                if op.get("op") == "insert":
                    since[op.get("table")] += 1
            info.update({"ops": len(tr.get("ops") or []), "crash": tr.get("crash")})
        calls = LEDGER.calls[n0:]
        trace.append({"i": i, "id": msg["id"], "tier": tier, "wall": round(time.time() - t0, 4),
                      "cost": round(sum(c.cost for c in calls), 6), **info})
        if (i + 1) % 250 == 0:
            tiers = Counter(t["tier"] for t in trace[-250:])
            print(f"{i + 1}/{len(ds['messages'])} last250 {dict(tiers)} spent ${spent():.3f}", flush=True)
    (RUN / "ingest.json").write_text(json.dumps({"trace": trace, "compiles": compiles, "patterns_report": writer.report,
                                                 "patterns": {t: [{"regex": p["regex"], "values": p["values"]} for p in ps]
                                                              for t, ps in writer.patterns.items()},
                                                 "spent": round(spent(), 4), "cost_if_fresh": round(spent(True), 4)},
                                                indent=1, ensure_ascii=False))
    tiers = Counter(t["tier"] for t in trace)
    print(json.dumps({"messages": len(trace), "tiers": tiers, "spent": round(spent(), 4), "cost_if_fresh": round(spent(True), 4)}, indent=1))


# ---------------------------------------------------------------- write accuracy (no model)
def _norm(v) -> str:
    return re.sub(r"\s+", " ", str(v).strip().lower())


def _has_value(values: list, want) -> bool:
    if isinstance(want, (int, float)) and not isinstance(want, bool):
        return any(isinstance(v, (int, float)) and not isinstance(v, bool) and abs(v - want) < 0.011 for v in values) or \
            any(isinstance(v, str) and re.search(rf"(?<![\d.]){re.escape(f'{want:g}')}(?![\d])", v) for v in values)
    w = _norm(want)
    return any(isinstance(v, str) and (w in _norm(v)) for v in values)


def _checks(truth: dict) -> dict:
    k = truth["kind"]
    c = {"date": truth["date"]}
    if k == "expense":
        c.update(amount=truth["amount"], currency=truth["currency"])
        if truth.get("merchant"):
            c["merchant"] = truth["merchant"]
        if truth.get("with"):
            from lab.datasets.simulate_year import PEOPLE
            c["with"] = [truth["with"], truth["with"].split()[0]] + PEOPLE.get(truth["with"], {}).get("alias", [])
    elif k == "climb":
        if truth.get("sent"):
            c["sent"] = truth["sent"]
    elif k == "run":
        c.update(km=truth["km"], secs=truth["secs"])
    elif k == "sleep":
        c["hours"] = truth["hours"]
    elif k == "weight":
        c["kg"] = truth["kg"]
    elif k == "movie":
        c.update(title=truth["title"], rating=truth["rating"])
    return c


def writes():
    import sqlite3
    ds = json.loads(DS.read_text())
    trace = {t["i"]: t for t in json.loads((RUN / "ingest.json").read_text())["trace"]}
    db = sqlite3.connect(RUN / "db.sqlite")
    people = {r[0]: r[1] for r in db.execute("SELECT id, name FROM people")} if db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='people'").fetchone() else {}
    rows_by_log: dict[int, list] = {}
    for (t,) in db.execute("SELECT name FROM _tables"):
        cols = [r[1] for r in db.execute(f"PRAGMA table_info('{t}')")]
        for row in db.execute(f"SELECT * FROM '{t}'"):
            d = dict(zip(cols, row))
            vals = [v for k, v in d.items() if k not in ("id", "_src", "_ts") and v is not None]
            vals += [people[v] for k, v in d.items() if k.endswith("_id") and v in people]   # a linked person, by name
            for lid in re.findall(r"\d+", str(d.get("_src") or "")):
                rows_by_log.setdefault(int(lid), []).append(vals)
    per: dict = {}
    misses = Counter()
    for i, msg in enumerate(ds["messages"]):
        if "truth" not in msg or i not in trace:
            continue
        tier = trace[i]["tier"]
        checks = _checks(msg["truth"])
        rows = rows_by_log.get(i + 1, [])   # every message is logged once, in order

        def holds(vals, k, want):
            if k == "date":
                return any(isinstance(v, str) and v[:10] == want for v in vals)
            if k == "secs":   # a run time may be stored as seconds or as the clock string
                return _has_value(vals, want) or _has_value(vals, f"{want // 60}:{want % 60:02d}")
            if k == "with":   # a companion may be stored by name, first name or the nickname the user wrote
                return any(_has_value(vals, n) for n in want)
            return _has_value(vals, want)

        best = max((sum(holds(vals, k, want) for k, want in checks.items()) for vals in rows), default=0)
        ok = best == len(checks)
        if not ok and rows:
            vals = max(rows, key=lambda v: sum(holds(v, k, w) for k, w in checks.items()))
            for k, want in checks.items():
                if not holds(vals, k, want):
                    misses[(msg["truth"]["kind"], k)] += 1
        elif not rows:
            misses[(msg["truth"]["kind"], "no row")] += 1
        key = (tier, msg["truth"]["kind"])
        per.setdefault(key, [0, 0])
        per[key][0] += ok
        per[key][1] += 1
    by_tier: dict = {}
    for (tier, kind), (ok, n) in per.items():
        by_tier.setdefault(tier, [0, 0])
        by_tier[tier][0] += ok
        by_tier[tier][1] += n
    out = {"by_tier": {t: {"correct": ok, "of": n, "accuracy": round(ok / n, 4)} for t, (ok, n) in by_tier.items()},
           "by_tier_kind": {f"{t}/{k}": {"correct": ok, "of": n, "accuracy": round(ok / n, 4)} for (t, k), (ok, n) in sorted(per.items())},
           "top_misses": {f"{k}.{f}": n for (k, f), n in misses.most_common(15)}}
    (LAB_DIR / "results" / "scale5k_writes.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


# ---------------------------------------------------------------- async passes
def passes():
    """The slow, asynchronous write-side passes on a copy of the database: supersession (Jev) and typed columns
    (regex extractors compiled once by the LLM)."""
    import shutil
    from lab.systems.det_reader import DetReader
    from lab.systems import supersede, typing as typed
    ds = json.loads(DS.read_text())
    shutil.copy(RUN / "db.sqlite", RUN / "db_async.sqlite")
    from lab.systems import consolidate
    from lab.systems.engine import Engine
    n0, t0 = len(LEDGER.calls), time.time()
    eng = Engine(str(RUN / "db_async.sqlite"), strict=True, normalize=True)
    merges = consolidate.dedupe(eng, ds["now"], all_pairs_max_rows=40)
    eng.db.commit()
    t_dedupe = time.time() - t0
    reader = DetReader(str(RUN / "db_async.sqlite"), ds["user"], datetime.fromisoformat(ds["now"]))
    t0 = time.time()
    pairs = supersede.candidate_pairs(reader)
    marked = supersede.apply(reader, pairs, supersede.judge_pairs(reader, pairs))
    t_sup = time.time() - t0
    t1 = time.time()
    extractors = [x for t, c in typed.text_columns(reader) for x in typed.compile_extractors(reader, t, c)]
    filled = typed.apply_extractors(reader, extractors)
    out = {"dedupe": {"merges": merges, "s": round(t_dedupe, 1)},
           "supersession": {"pairs": len(pairs), "marked_replaced": marked, "s": round(t_sup, 1)},
           "typed_columns": {"extractors": [f"{x['table']}.{x['source']} -> {x['column']} [{x['transform']}]" for x in extractors],
                             "filled": filled, "s": round(time.time() - t1, 1)},
           "cost": round(sum(c.cost for c in LEDGER.calls[n0:]), 5)}
    (RUN / "passes.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(json.dumps(out, indent=1, ensure_ascii=False))


# ---------------------------------------------------------------- reads
def reads(workers: int = 6, db: str = "db_async.sqlite"):
    """Both readers on every question (the deterministic one, and the GPT-6 Luna tool agent), so the hybrid can be
    compared with each alone. Grading: Jev as judge (validated against Opus in lab/bench/judge_pairs.py)."""
    from lab.systems.det_reader import DetReader
    from lab.systems.reader import ask_agent
    ds = json.loads(DS.read_text())
    now = datetime.fromisoformat(ds["now"])
    reader = DetReader(str(RUN / db), ds["user"], now)

    def run(q):
        n0, t0 = len(LEDGER.calls), time.time()
        det = reader.ask(q["question"])
        det_wall, det_cost = time.time() - t0, sum(c.cost for c in LEDGER.calls[n0:])
        n1, t1 = len(LEDGER.calls), time.time()
        agent = ask_agent(reader.eng, q["question"], now, ds["user"], MODEL, "scale5k:agent")["answer"]
        agent_wall, agent_cost = time.time() - t1, sum(c.cost for c in LEDGER.calls[n1:])
        g_det = jev_grade(q["question"], q["answer"], det["answer"])["score"]
        g_agent = jev_grade(q["question"], q["answer"], agent)["score"]
        own = (det.get("verified") or 0) >= 0.5 and det["answer"] != "I don't know."
        return {"question": q["question"], "gold": q["answer"], "category": q["category"],
                "det_answer": det["answer"][:300], "verified": det.get("verified"), "det_score": g_det,
                "det_wall_s": round(det_wall, 3), "det_cost": round(det_cost, 6),
                "agent_answer": agent[:300], "agent_score": g_agent, "agent_wall_s": round(agent_wall, 3),
                "agent_cost": round(agent_cost, 6), "hybrid_path": "deterministic" if own else "agent",
                "hybrid_score": g_det if own else g_agent,
                "hybrid_wall_s": round(det_wall + (0 if own else agent_wall), 3),
                "hybrid_cost": round(det_cost + (0 if own else agent_cost), 6)}

    with ThreadPoolExecutor(workers) as pool:
        results = list(pool.map(run, ds["qa"]))
    n = len(results)

    def dist(key):
        xs = sorted(r[key] for r in results)
        return {"p50": round(statistics.median(xs), 3), "p90": round(xs[int(0.9 * len(xs))], 3)}

    cats: dict = {}
    for r in results:
        cats.setdefault(r["category"], []).append((r["det_score"], r["agent_score"], r["hybrid_score"]))
    summary = {"questions": n,
               "deterministic": {"accuracy": round(sum(r["det_score"] for r in results) / n, 4), "latency_s": dist("det_wall_s"),
                                 "cost_per_q": round(sum(r["det_cost"] for r in results) / n, 6)},
               "agent": {"accuracy": round(sum(r["agent_score"] for r in results) / n, 4), "latency_s": dist("agent_wall_s"),
                         "cost_per_q": round(sum(r["agent_cost"] for r in results) / n, 6)},
               "hybrid": {"accuracy": round(sum(r["hybrid_score"] for r in results) / n, 4), "latency_s": dist("hybrid_wall_s"),
                          "cost_per_q": round(sum(r["hybrid_cost"] for r in results) / n, 6),
                          "answered_without_llm": sum(r["hybrid_path"] == "deterministic" for r in results)},
               "by_category_det_agent_hybrid": {c: [round(sum(x[i] for x in v) / len(v), 3) for i in range(3)] for c, v in sorted(cats.items())}}
    (LAB_DIR / "results" / "scale5k_reads.json").write_text(json.dumps({"summary": summary, "results": results}, indent=1, ensure_ascii=False))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["ingest", "writes", "passes", "reads"])
    ap.add_argument("--bootstrap", type=int, default=400)
    ap.add_argument("--budget", type=float, default=1.5)
    ap.add_argument("--limit", type=int, default=None, help="only the first N messages (smoke tests)")
    args = ap.parse_args()
    {"ingest": lambda: ingest(args.bootstrap, args.budget, limit=args.limit), "writes": writes, "passes": passes,
     "reads": reads}[args.cmd]()
