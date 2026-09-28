"""Round 7: a memory with several stores, like a brain (LLP 0013). Every experiment is a config of swappable parts.

  .venv/bin/python -m lab.bench.brain setup                 copy the round-6 database, erase what was forgotten, build every store
  .venv/bin/python -m lab.bench.brain run CONFIG [CONFIG..] answer the 94 questions, grade with Jev -> lab/results/brain_runs.json
  .venv/bin/python -m lab.bench.brain summary               accuracy by memory type and config; H1-H4
  .venv/bin/python -m lab.bench.brain deciders              H5: Jev vs GPT-6 Luna on the same closed decisions
  .venv/bin/python -m lab.bench.brain audit                 forgetting leaks in every store; the one-time build cost
  LAB_BRAIN_SET=holdout ...                                 the same on the 36 held-out questions

Spend guard: a command stops when its new (uncached) spend passes LAB_BRAIN_LIMIT (default $1.50).
"""
# @ref LLP 0013#protocol — configs, lesions, full context on a stratified subset, the decider ablation
from __future__ import annotations

import json
import os
import random
import shutil
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from lab.common.grade import jev_grade
from lab.common.llm import LEDGER
from lab.memory import configs, deciders, stores
from lab.memory.core import Context
from lab.memory.routers import TYPE_STORES

LAB = Path(__file__).resolve().parents[1]
RUN = LAB / "runs" / "brain"
DB = RUN / "db.sqlite"
HOLDOUT = os.environ.get("LAB_BRAIN_SET") == "holdout"   # the 36 questions written after the system was frozen
DS = LAB / "datasets" / ("memory_types_holdout.json" if HOLDOUT else "memory_types.json")
LIFE = LAB / "datasets" / "life_scale.json"
TAG = os.environ.get("LAB_BRAIN_TAG", "")        # e.g. "_v2": the memory as generalized in round 8 (LLP 0014)
OUT = LAB / "results" / (("brain_runs_holdout" if HOLDOUT else "brain_runs") + TAG + ".json")
MEMORY_DIR = RUN / ("memory" + TAG)
LIMIT = float(os.environ.get("LAB_BRAIN_LIMIT", "1.5"))
OWN = {t: s[0] for t, s in TYPE_STORES.items() if t not in ("metamemory", "forgotten")}   # a type's own store


def spent() -> float:
    return sum(c.cost for c in LEDGER.calls if not c.cached)


def guard():
    if spent() > LIMIT:
        raise SystemExit(f"spend guard: ${spent():.3f} > ${LIMIT} this command")


def context(decider="jev") -> Context:
    life = json.loads(LIFE.read_text())
    return Context(str(DB), life["user"], datetime.fromisoformat(life["now"]), decider=deciders.make(decider),
                   cache_dir=MEMORY_DIR)


def load_all(ctx):
    return configs.build_stores(ctx, configs.BRAIN + ["log_all"])


# ------------------------------------------------------------------------------------------------------ setup
def setup():
    RUN.mkdir(parents=True, exist_ok=True)
    fresh = not DB.exists()
    if fresh:
        shutil.copy(LAB / "runs" / "app" / "db.sqlite", DB)
    ctx = context()
    if fresh:   # erasure is part of consolidation: before any store is built, so nothing forgotten can come back
        (RUN / "forget.json").write_text(json.dumps(stores.erase_forgotten(ctx), ensure_ascii=False, indent=1))
    print("forget:", (RUN / "forget.json").read_text())
    t0 = time.time()
    built, stats = load_all(ctx)
    print(f"built in {time.time() - t0:.1f}s; spend ${spent():.4f}")
    for n, s in stats.items():
        print(f"  {n}: {s}")
    for n in ["routines", "intentions", "preferences", "periods"]:
        st = built[n]
        items = getattr(st, "statements", [])
        print(f"== {n} ({len(items)})")
        for x in items[:12]:
            print("  ", x[:220])
    labels = stores.label_messages(ctx)
    print("aspects (p >= 0.5):", {a: sum(v[a] >= 0.5 for v in labels.values()) for a in stores.ASPECTS})


# -------------------------------------------------------------------------------------------------------- run
def subset(qs):
    """Full context runs on 3 questions per type (positions 0, 3, 6 within the type)."""
    by = defaultdict(list)
    for q in qs:
        by[q["type"]].append(q)
    return [q for t in by for i, q in enumerate(by[t]) if i in (0, 3, 6)]


def run(names: list[str], workers: int = 8):
    qs = json.loads(DS.read_text())["questions"]
    ctx = context()
    built, _ = load_all(ctx)
    types = {q["question"]: q["type"] for q in qs}
    results = json.loads(OUT.read_text()) if OUT.exists() else {}
    for name in names:
        mem = configs.make(name, built, ctx, types)
        items = subset(qs) if name == "full_context" and not HOLDOUT else qs
        n0 = len(LEDGER.calls)

        def one(q):
            guard()
            out = mem.ask(q["question"])
            g = jev_grade(q["question"], q["answer"], out["answer"], tag="grade:brain")
            rl = getattr(mem.router, "latency", {}).get(q["question"])
            return {"id": q["id"], "type": q["type"], "question": q["question"], "gold": q["answer"],
                    "answer": out["answer"], "score": g["score"], "soft": g["soft"], "stores": out["stores"],
                    "route_p": getattr(mem.router, "probs", {}).get(q["question"]), "route_latency_s": rl,
                    "evidence_chars": out["evidence_chars"], "answer_tokens_in": out["answer_tokens_in"],
                    "wall_s": out["wall_s"], "path": getattr(mem.answerer, "paths", {}).get(q["question"])}

        with ThreadPoolExecutor(workers) as pool:
            rows = list(pool.map(one, items))
        calls = LEDGER.calls[n0:]
        results[name] = {"rows": rows, "n": len(rows), "accuracy": round(100 * sum(r["score"] for r in rows) / len(rows), 1),
                         "nominal_cost": round(sum(c.cost for c in calls), 5),
                         "answer_cost": round(sum(c.cost for c in calls if c.tag.startswith("memory:answer") or c.tag.startswith("memory:hybrid")
                                                  or c.tag.startswith("det")), 5),
                         "new_spend": round(sum(c.cost for c in calls if not c.cached), 5)}
        by = defaultdict(list)
        for r in rows:
            by[r["type"]].append(r["score"])
        print(f"{name:18s} {results[name]['accuracy']:5.1f}%  " + "  ".join(f"{t[:10]} {100 * sum(v) / len(v):.0f}" for t, v in by.items())
              + f"  | new spend ${results[name]['new_spend']:.4f}")
        OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1))
    print(f"total new spend this command: ${spent():.4f}")


# ---------------------------------------------------------------------------------------------------- summary
def by_type(rows) -> dict[str, float]:
    by = defaultdict(list)
    for r in rows:
        by[r["type"]].append(r["score"])
    return {t: round(100 * sum(v) / len(v), 1) for t, v in by.items()}


def summary():
    res = json.loads(OUT.read_text())
    qs = json.loads(DS.read_text())["questions"]
    tlist = list(dict.fromkeys(q["type"] for q in qs))
    names = [n for n in configs.CONFIGS if n in res]
    print(f"{'config':18s} {'all':>5s} " + " ".join(f"{t[:9]:>9s}" for t in tlist) + "   ev.chars  ans.tok")
    for n in names:
        bt = by_type(res[n]["rows"])
        ev = sum(r["evidence_chars"] for r in res[n]["rows"]) / res[n]["n"]
        tok = sum(r["answer_tokens_in"] for r in res[n]["rows"]) / res[n]["n"]
        print(f"{n:18s} {res[n]['accuracy']:5.1f} " + " ".join(f"{bt.get(t, float('nan')):9.1f}" for t in tlist) + f"   {ev:8.0f}  {tok:7.0f}")
    out = {"configs": {n: {"accuracy": res[n]["accuracy"], "by_type": by_type(res[n]["rows"]), "n": res[n]["n"],
                           "nominal_cost": res[n]["nominal_cost"]} for n in names}}
    acc = lambda n: res[n]["accuracy"] if n in res else None
    # H1: several stores beat one
    if all(n in res for n in ("brain_jev", "hybrid_r4", "log_rag")):
        out["H1"] = {"brain_jev": acc("brain_jev"), "hybrid_r4": acc("hybrid_r4"), "log_rag": acc("log_rag"),
                     "facts_only": acc("facts_only"), "facts_log": acc("facts_log"),
                     "holds": acc("brain_jev") - max(acc("hybrid_r4"), acc("log_rag")) >= 10}
    # H2: double dissociation, lesions of brain_all
    if "brain_all" in res:
        base = by_type(res["brain_all"]["rows"])
        h2 = {}
        for s in configs.BRAIN:
            n = f"lesion_{s}"
            if n not in res:
                continue
            les = by_type(res[n]["rows"])
            own = [t for t, o in OWN.items() if o == s]
            other = [t for t in base if t not in own]
            d_own = sum(les[t] - base[t] for t in own) / len(own) if own else None
            d_other = sum(abs(les[t] - base[t]) for t in other) / len(other)
            h2[s] = {"own_types": own, "own_drop": None if d_own is None else round(-d_own, 1),
                     "other_mean_abs_change": round(d_other, 1),
                     "dissociates": d_own is not None and -d_own >= 20 and d_other < 5}
        out["H2"] = {"stores": h2, "holds": sum(v["dissociates"] for v in h2.values()) >= 3}
    # H3: routing
    if all(n in res for n in ("brain_jev", "brain_llm_router", "brain_oracle")):
        lat = lambda n: sorted(r["route_latency_s"] or 0 for r in res[n]["rows"])
        med = lambda xs: xs[len(xs) // 2]
        out["H3"] = {"jev": acc("brain_jev"), "llm": acc("brain_llm_router"), "oracle": acc("brain_oracle"),
                     "jev_route_latency_median_s": round(med(lat("brain_jev")), 3),
                     "llm_route_latency_median_s": round(med(lat("brain_llm_router")), 3)}
        out["H3"]["holds"] = (acc("brain_oracle") - acc("brain_jev") <= 5 and acc("brain_llm_router") - acc("brain_jev") <= 5
                              and out["H3"]["jev_route_latency_median_s"] <= out["H3"]["llm_route_latency_median_s"] / 5)
        # routing quality against the oracle's stores
        for n in ("brain_jev", "brain_llm_router"):
            hit = [len(set(r["stores"]) & set(TYPE_STORES[r["type"]])) / len(TYPE_STORES[r["type"]]) for r in res[n]["rows"]]
            extra = [len(set(r["stores"]) - set(TYPE_STORES[r["type"]])) for r in res[n]["rows"]]
            out["H3"][f"{n}_oracle_recall"] = round(sum(hit) / len(hit), 3)
            out["H3"][f"{n}_extra_stores"] = round(sum(extra) / len(extra), 2)
    # H4: full context on the subset
    if "full_context" in res:
        ids = {r["id"] for r in res["full_context"]["rows"]}
        sub = lambda n: [r for r in res[n]["rows"] if r["id"] in ids]
        fc = res["full_context"]["rows"]
        h4 = {"n": len(fc), "full_context": round(100 * sum(r["score"] for r in fc) / len(fc), 1),
              "full_tokens_per_q": round(sum(r["answer_tokens_in"] for r in fc) / len(fc))}
        for n in ("brain_jev", "brain_all", "facts_log", "hybrid_r4"):
            if n in res:
                rows = sub(n)
                h4[n] = round(100 * sum(r["score"] for r in rows) / len(rows), 1)
                h4[f"{n}_tokens_per_q"] = round(sum(r["answer_tokens_in"] for r in rows) / len(rows))
        if "brain_jev" in h4:
            h4["holds"] = h4["full_context"] >= h4["brain_jev"] - 5 and h4["full_tokens_per_q"] >= 10 * max(1, h4["brain_jev_tokens_per_q"])
        out["H4"] = h4
    for k in ("H1", "H2", "H3", "H4"):
        if k in out:
            print(k, json.dumps(out[k], ensure_ascii=False))
    (LAB / "results" / "brain_summary.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))


# ------------------------------------------------------------------------------------------------------ audit
FORGOTTEN = ["602 555 777", "Dvořák", "Dvorak", "731 000 444", "my barber"]


def audit():
    """Forgetting: no forgotten string in any store. Build cost: every store rebuilt from the cache (free), with the
    ledger's record of what each consolidation step cost when it first ran."""
    import tempfile
    os.environ["LAB_CACHE_ONLY"] = "1"
    ctx = context()
    ctx.cache_dir = Path(tempfile.mkdtemp())   # rebuild every store: model calls replay from the call cache, and are counted
    n0 = len(LEDGER.calls)
    t0 = time.time()
    built, _ = load_all(ctx)
    wall = time.time() - t0
    calls = LEDGER.calls[n0:]
    cost = defaultdict(float)
    for c in calls:
        cost[c.tag.split(":")[1] if c.tag.startswith("memory:") else c.tag] += c.cost
    texts = {"log": [t for _, _, t in ctx.log], "facts": built["facts"].lines,
             "episodes": [e["gist"] for e in built["episodes"].episodes.values()],
             "routines": built["routines"].statements, "intentions": built["intentions"].statements,
             "preferences": built["preferences"].statements,
             "periods": built["periods"].statements + built["periods"].milestones + list(built["periods"].months.values())}
    leaks = {s: {k: [x[:120] for x in v if s.lower() in str(x).lower()] for k, v in texts.items()} for s in FORGOTTEN}
    leaks = {s: {k: v for k, v in d.items() if v} for s, d in leaks.items()}
    out = {"build_cost_by_step": {k: round(v, 4) for k, v in sorted(cost.items())}, "build_cost": round(sum(cost.values()), 4),
           "rebuild_from_cache_s": round(wall, 1), "leaks": leaks,
           "store_sizes": {k: len(v) for k, v in texts.items()}}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    (LAB / "results" / "brain_audit.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "summary"
    if cmd == "audit":
        audit()
    elif cmd == "setup":
        setup()
    elif cmd == "run":
        run(sys.argv[2:])
    elif cmd == "summary":
        summary()
    elif cmd == "deciders":
        from lab.bench.brain_deciders import main as deciders_main
        deciders_main()
