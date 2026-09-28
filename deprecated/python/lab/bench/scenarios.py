"""Round 8: the same system on different scenarios (LLP 0014): a support desk, a sales pipeline, a team's projects.

  .venv/bin/python -m lab.bench.scenarios ingest support      tiered writer (bootstrap 120, recompile every 150); guard $0.60
  .venv/bin/python -m lab.bench.scenarios writes support      write accuracy against the simulator's truth (no model)
  .venv/bin/python -m lab.bench.scenarios memory support [CONFIG ...]   build the stores (erase first), answer, grade
  .venv/bin/python -m lab.bench.scenarios summary             every scenario: writes, tiers, accuracy by type, H1-H6

Nothing here is specific to a scenario: the dataset names the user and "now"; the engine, prompts, writer, readers and
memory are the ones of rounds 4-7 (the memory as generalized in LLP 0014#what-stays-fixed-and-what-changes-once).
"""
# @ref LLP 0014#protocol — ingest, writes, memory, summary; no domain code
from __future__ import annotations

import json
import os
import random
import re
import shutil
import sqlite3
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from lab.bench import scale5k
from lab.common.grade import jev_grade
from lab.common.llm import LAB_DIR, LEDGER
from lab.memory import configs, deciders, stores
from lab.memory.core import Context

SCENARIOS = ["support", "sales", "projects"]
CONFIGS = ["log_rag", "facts_log", "hybrid_r4", "plus_computed", "brain_jev_recall", "brain_all", "full_context"]
# Real data (LLP 0017) never goes into the tracked lab: LAB_PRIVATE_DIR holds its dataset, database and results.
PRIVATE = Path(os.environ["LAB_PRIVATE_DIR"]) if os.environ.get("LAB_PRIVATE_DIR") else None
RES = PRIVATE / "results" if PRIVATE else LAB_DIR / "results"
RES.mkdir(parents=True, exist_ok=True)


def paths(name: str):
    """`sales` or `sales@v24`: the dataset is the part before @, the run directory keeps the variant."""
    # @ref LLP 0018#processors — another user's data (u<number>) may only be processed with LAB_PROCESSORS=openai
    if re.match(r"u\d+", name) and os.environ.get("LAB_PROCESSORS") != "openai":
        raise SystemExit(f"{name}: another user's data needs LAB_PROCESSORS=openai")
    if PRIVATE:
        return PRIVATE / f"scenario_{name.split('@')[0]}.json", PRIVATE / "runs" / f"scenario_{name.replace('@', '_')}"
    return LAB_DIR / "datasets" / f"scenario_{name.split('@')[0]}.json", LAB_DIR / "runs" / f"scenario_{name.replace('@', '_')}"


def spent() -> float:
    return sum(c.cost for c in LEDGER.calls if not c.cached)


# ---------------------------------------------------------------------------------------------------- ingest
def ingest(name: str, budget: float = 0.60):
    ds, run = paths(name)
    scale5k.DS, scale5k.RUN = ds, run          # the round-4 tiered ingestion, pointed at this scenario
    scale5k.VARIANT = name.split("@")[1] if "@" in name else "v23"
    scale5k.ingest(bootstrap=120, budget=budget, recompile_every=150)


# ---------------------------------------------------------------------------------------------------- writes
def holds(values: list, key: str, want) -> bool:
    if isinstance(want, list):
        return any(holds(values, key, w) for w in want)
    if key in ("date", "due") or (isinstance(want, str) and re.fullmatch(r"\d{4}-\d\d-\d\d", want)):
        return any(isinstance(v, str) and want in v for v in values)
    if isinstance(want, str) and want.isdigit():   # a ticket number may be stored as a number or inside text
        return scale5k._has_value(values, int(want)) or scale5k._has_value(values, want)
    return scale5k._has_value(values, want)


def target_table(col: str, tables) -> str | None:
    """The engine's rule: assigned_person_id -> people, organization_id -> organizations (LLP 0004)."""
    parts = col[:-3].split("_")
    for i in range(len(parts)):
        stem = "_".join(parts[i:])
        for cand in (stem + "s", stem + "es", stem[:-1] + "ies" if stem.endswith("y") else "", stem,
                     "people" if stem == "person" else ""):
            if cand and cand in tables:
                return cand
    return None


def writes(name: str) -> dict:
    """Each truth record: does some row written or updated by that message (or its history entry) hold every check?
    Records of messages the user later asked to forget must be gone instead."""
    ds_path, run = paths(name)
    ds = json.loads(ds_path.read_text())
    trace = {t["i"]: t for t in json.loads((run / "ingest.json").read_text())["trace"]}
    db = sqlite3.connect(run / "db.sqlite")
    labels: dict = {}
    tables = [r[0] for r in db.execute("SELECT name FROM _tables")]
    name_of = {}
    for t in tables:
        key = next((c for c in ("name", "title", "subject") if c in [r[1] for r in db.execute(f"PRAGMA table_info('{t}')")]), None)
        if key:
            name_of[t] = {r[0]: r[1] for r in db.execute(f"SELECT id, \"{key}\" FROM '{t}'")}
    for t in tables:   # a linked row counts by all its values (a survey linked to ticket #1087 holds 1087)
        cols_t = [r[1] for r in db.execute(f"PRAGMA table_info('{t}')")]
        labels[t] = {row[0]: [v for c, v in zip(cols_t, row) if c not in ("id", "_src", "_ts") and v is not None and not c.endswith("_id")]
                     for row in db.execute(f"SELECT * FROM '{t}'")}
    by_log: dict[int, list] = defaultdict(list)
    for t in tables:
        cols = [r[1] for r in db.execute(f"PRAGMA table_info('{t}')")]
        for row in db.execute(f"SELECT * FROM '{t}'"):
            d = dict(zip(cols, row))
            vals = [v for k, v in d.items() if k not in ("id", "_src", "_ts") and v is not None]
            vals += [k for k, v in d.items() if k not in ("id", "_src", "_ts") and v is not None]   # proposal_sent_date: a proposal
            for k, v in d.items():   # a linked row counts by its values
                if k.endswith("_id") and v is not None:
                    tt = target_table(k, labels)
                    # a link the naming rule can't resolve (assignee_id): any table's row of that id, by its name
                    vals += labels[tt].get(v, []) if tt else [n for t2, names in name_of.items() if (n := names.get(v))]
            # what this row held before later messages changed it (a triage's P2 before an escalation to P1)
            vals += [h[0] for h in db.execute("SELECT old_value FROM _history WHERE table_name = ? AND row_id = ? AND "
                                              "old_value IS NOT NULL", (t, d.get("id")))]
            for lid in re.findall(r"\d+", str(d.get("_src") or "")):
                by_log[int(lid)].append(vals)
    for lid, new in db.execute("SELECT log_id, new_value FROM _history WHERE new_value IS NOT NULL"):
        by_log[lid].append([new])
    log_text = {r[0]: r[1] for r in db.execute("SELECT id, text FROM _log")}
    per, misses, erased = defaultdict(lambda: [0, 0]), Counter(), []
    for i, msg in enumerate(ds["messages"]):
        tr = msg.get("truth")
        if not tr or i not in trace:
            continue
        lid = i + 1   # every message is logged once, in order
        if tr["kind"] == "forget":   # the request itself may name what is to go; everything else must not
            left = [w for w in tr["checks"]["erase"]
                    if any(w.lower() in str(t).lower() for j, t in log_text.items() if j != lid)
                    or any(w.lower() in str(v).lower() for j, rows_ in by_log.items() if j != lid for vals in rows_ for v in vals)]
            erased.append({"request": msg["text"][:80], "left_behind": left})
            continue
        if tr["kind"] == "erased":
            continue
        rows = by_log.get(lid, [])
        checks = tr["checks"]
        best = max((sum(holds(v, k, w) for k, w in checks.items()) for v in rows), default=-1)
        ok = bool(rows) and best == len(checks)
        if not ok:
            if not rows:
                misses[f"{tr['kind']}: no row"] += 1
            else:
                vals = max(rows, key=lambda v: sum(holds(v, k, w) for k, w in checks.items()))
                for k, w in checks.items():
                    if not holds(vals, k, w):
                        misses[f"{tr['kind']}.{k}"] += 1
        key = (trace[i]["tier"], tr["kind"])
        per[key][0] += ok
        per[key][1] += 1
    tiers = Counter(t["tier"] for t in trace.values())
    post = [t for i, t in trace.items() if i >= 120]
    ok_all = sum(v[0] for v in per.values())
    n_all = sum(v[1] for v in per.values())
    out = {"scenario": name, "messages": len(trace), "truth_records": n_all, "write_accuracy": round(ok_all / n_all, 4),
           "tiers": dict(tiers), "tier_a_after_bootstrap": round(sum(t["tier"] == "A" for t in post) / max(1, len(post)), 4),
           "by_kind": {f"{k}": {"ok": v[0], "of": v[1], "acc": round(v[0] / v[1], 3)} for k, v in
                       sorted(((k[1], v) for k, v in per.items()), key=lambda x: x[0])},
           "by_tier": {t: round(sum(v[0] for k, v in per.items() if k[0] == t) / max(1, sum(v[1] for k, v in per.items() if k[0] == t)), 4)
                       for t in {k[0] for k in per}},
           "top_misses": dict(misses.most_common(12)), "forget": erased,
           "tables": {t: db.execute(f"SELECT COUNT(*) FROM '{t}'").fetchone()[0] for t in tables},
           "ingest_spent": json.loads((run / "ingest.json").read_text())["spent"]}
    kinds = defaultdict(lambda: [0, 0])
    for (tier, kind), v in per.items():
        kinds[kind][0] += v[0]
        kinds[kind][1] += v[1]
    out["by_kind"] = {k: {"ok": v[0], "of": v[1], "acc": round(v[0] / v[1], 3)} for k, v in sorted(kinds.items())}
    (RES / f"scenario_{name.replace('@', '_')}_writes.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(json.dumps({k: out[k] for k in ("scenario", "messages", "truth_records", "write_accuracy", "tiers",
                                          "tier_a_after_bootstrap", "top_misses", "forget", "tables")}, ensure_ascii=False))
    return out


# ---------------------------------------------------------------------------------------------------- memory
def context(name: str, fresh: bool = False) -> Context:
    ds_path, run = paths(name)
    ds = json.loads(ds_path.read_text())
    mem_db = run / "memory.sqlite"
    first = fresh or not mem_db.exists()
    if first:
        shutil.copy(run / "db.sqlite", mem_db)
    ctx = Context(str(mem_db), ds["user"], datetime.fromisoformat(ds["now"]), decider=deciders.make("jev"),
                  cache_dir=run / "memory")
    if first:   # erasure first, before any store is built (LLP 0013.000#forgetting)
        (run / "memory" / "forget.json").write_text(json.dumps(stores.erase_forgotten(ctx), ensure_ascii=False, indent=1))
    return ctx


def memory(name: str, names: list[str], limit: float = 0.6):
    ds_path, run = paths(name)
    qs = json.loads(ds_path.read_text())["qa"]
    ctx = context(name)
    extra = ["product", "product_injected"] if os.environ.get("LAB_PRODUCT_MEMORY") else []
    wanted = [st for c in (names or CONFIGS) for st in configs.CONFIGS[c]["stores"]]
    built, stats = configs.build_stores(ctx, list(dict.fromkeys(configs.BRAIN + ["log_all"] + extra + wanted)))
    print(name, {k: v for k, v in stats.items() if v}, flush=True)
    types = {q["question"]: q["type"] for q in qs}
    out_path = RES / f"scenario_{name.replace('@', '_')}_runs.json"
    results = json.loads(out_path.read_text()) if out_path.exists() else {}
    for cfg in names or CONFIGS:
        mem = configs.make(cfg, built, ctx, types)
        n0 = len(LEDGER.calls)

        def one(q):
            if spent() > limit:
                raise SystemExit(f"spend guard: ${spent():.3f} > ${limit}")
            out = mem.ask(q["question"])
            # real data has no gold: answers are kept for review, not graded
            g = jev_grade(q["question"], q["answer"], out["answer"], tag="grade:scenario") if q["answer"] else {"score": 0.0}
            return {"id": q["id"], "type": q["type"], "question": q["question"], "gold": q["answer"], "answer": out["answer"],
                    "score": g["score"], "stores": out["stores"], "evidence_chars": out["evidence_chars"],
                    "answer_tokens_in": out["answer_tokens_in"], "route_latency_s": getattr(mem.router, "latency", {}).get(q["question"])}

        with ThreadPoolExecutor(8) as pool:
            rows = list(pool.map(one, qs))
        calls = LEDGER.calls[n0:]
        results[cfg] = {"rows": rows, "n": len(rows), "accuracy": round(100 * sum(r["score"] for r in rows) / len(rows), 1),
                        "new_spend": round(sum(c.cost for c in calls if not c.cached), 5)}
        by = defaultdict(list)
        for r in rows:
            by[r["type"]].append(r["score"])
        print(f"{name:9s} {cfg:17s} {results[cfg]['accuracy']:5.1f}%  " + " ".join(f"{t[:6]} {100 * sum(v) / len(v):.0f}" for t, v in by.items())
              + f" | ${results[cfg]['new_spend']:.4f}", flush=True)
        out_path.write_text(json.dumps(results, ensure_ascii=False, indent=1))
    print(f"spent this command: ${spent():.4f}")


# ---------------------------------------------------------------------------------------------------- summary
def paired(rows_a, rows_b, n=10000, seed=1):
    A = {x["id"]: x["score"] for x in rows_a}
    B = {x["id"]: x["score"] for x in rows_b}
    d = [A[i] - B[i] for i in sorted(set(A) & set(B))]
    rng = random.Random(seed)
    boots = sorted(sum(rng.choice(d) for _ in d) / len(d) for _ in range(n))
    return round(100 * sum(d) / len(d), 1), round(100 * boots[int(0.025 * n)], 1), round(100 * boots[int(0.975 * n)], 1)


def summary():
    out = {"writes": {}, "reads": {}}
    pooled = defaultdict(list)
    for name in SCENARIOS:
        w = RES / f"scenario_{name}_writes.json"
        if w.exists():
            wj = json.loads(w.read_text())
            out["writes"][name] = {k: wj[k] for k in ("messages", "truth_records", "write_accuracy", "tier_a_after_bootstrap",
                                                     "ingest_spent", "tables")}
        r = RES / f"scenario_{name}_runs.json"
        if not r.exists():
            continue
        res = json.loads(r.read_text())
        out["reads"][name] = {}
        for cfg, v in res.items():
            by = defaultdict(list)
            for x in v["rows"]:
                by[x["type"]].append(x["score"])
                pooled[cfg].append(dict(x, id=f"{name}:{x['id']}"))
            out["reads"][name][cfg] = {"accuracy": v["accuracy"], "by_type": {t: round(100 * sum(s) / len(s), 1) for t, s in by.items()},
                                       "answer_tokens": round(sum(x["answer_tokens_in"] for x in v["rows"]) / v["n"])}
    acc = lambda rows: round(100 * sum(x["score"] for x in rows) / len(rows), 1) if rows else None
    out["pooled"] = {cfg: acc(rows) for cfg, rows in pooled.items()}
    if "brain_all" in pooled and "facts_log" in pooled:
        out["H3"] = {"brain_all_minus_facts_log": paired(pooled["brain_all"], pooled["facts_log"]),
                     "per_scenario": {n: [out["reads"][n].get("brain_all", {}).get("accuracy"), out["reads"][n].get("facts_log", {}).get("accuracy")]
                                      for n in out["reads"]}}
    if "plus_computed" in pooled and "facts_log" in pooled:
        sel = lambda rows: [x for x in rows if x["type"] in ("routine", "period")]
        out["H4"] = {"routine_period_plus_computed": acc(sel(pooled["plus_computed"])), "routine_period_facts_log": acc(sel(pooled["facts_log"]))}
    if "facts_log" in pooled:
        out["H5"] = {n: acc([x for x in pooled["facts_log"] if x["id"].startswith(n) and x["type"] in ("semantic_current", "semantic_history")])
                     for n in SCENARIOS}
    if "full_context" in pooled and "brain_all" in pooled:
        agg = lambda rows: [x for x in rows if x["type"] == "aggregate"]
        out["H6"] = {"full_context": out["pooled"]["full_context"], "brain_all": out["pooled"]["brain_all"],
                     "full_context_aggregate": acc(agg(pooled["full_context"])), "brain_all_aggregate": acc(agg(pooled["brain_all"]))}
    print(json.dumps(out, indent=1, ensure_ascii=False))
    (RES / "scenarios_summary.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "ingest":
        ingest(args[0], float(os.environ.get("LAB_SCENARIO_BUDGET", "0.60")))
    elif cmd == "writes":
        writes(args[0])
    elif cmd == "memory":
        memory(args[0], args[1:], float(os.environ.get("LAB_SCENARIO_LIMIT", "0.6")))
    elif cmd == "summary":
        summary()


# ------------------------------------------------------------------------------------------------ the write gate
GATE_NEUTRAL = {
    "intent": {"type": "choice", "instructions": "What is `message` mainly doing?", "criteria": {
        "share": "Gives new information: a fact, event, plan, request, purchase, contact, preference or document",
        "change": "Corrects or updates something shared before, or reports that its status changed",
        "remove": "Asks to forget or delete information, or says a plan or appointment was cancelled",
        "ask": "The user asks their assistant a question or asks it to look something up. A message from someone "
               "else (an email, a ticket, a form) is not this",
        "chat": "Small talk, a greeting, a reaction or thanks, with no information to keep"}},
    "worth_remembering": {"type": "noul", "instructions": "Does `message` contain information worth keeping: a fact, "
                          "event, request, commitment, change or document about people, organizations, work or life?"},
}


def gate_test():
    """The round-4 Jev gate vs a domain-neutral wording, on every support message and 600 life messages: how many
    messages that carry facts it lets through, and how much chit-chat it stops. Jev only."""
    from lab.common import jev
    from lab.systems import jev_layer
    sets = {"support": json.loads((LAB_DIR / "datasets" / "scenario_support.json").read_text())["messages"],
            "life": random.Random(8).sample(json.loads((LAB_DIR / "datasets" / "life_scale.json").read_text())["messages"], 600)}
    out = {}
    for name, msgs in sets.items():
        text = lambda m: m["text"] if isinstance(m["text"], str) else json.dumps(m["text"], ensure_ascii=False)
        facts = [m for m in msgs if m.get("truth") or m["kind"] in ("store", "update")]
        chat = [m for m in msgs if m["kind"] in ("chitchat", "question")]

        def decide(m, questions):
            a = jev.ask({"message": text(m), "sent_at": m["ts"]}, questions, tag="gate:test")
            i, w = a["intent"], a["worth_remembering"]["noul"]
            return not (i["choice"] in ("ask", "chat") and i["confidence"] >= 0.5 and w < 0.5)

        res = {}
        for label, qs in (("round4", jev_layer.GATE_QUESTIONS), ("neutral", GATE_NEUTRAL)):
            with ThreadPoolExecutor(8) as pool:
                f = list(pool.map(lambda m: decide(m, qs), facts))
                c = list(pool.map(lambda m: decide(m, qs), chat))
            res[label] = {"facts_written": round(sum(f) / len(f), 4), "facts": len(f),
                          "chat_stopped": round(1 - sum(c) / len(c), 4), "chat": len(c)}
        out[name] = res
    print(json.dumps(out, indent=1))
    (RES / "scenarios_gate.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__" and sys.argv[1] == "gate":
    gate_test()


# ------------------------------------------------------------------------------------------------- the crossover
def crossover():
    """LLP 0016 part B: the same 36 questions at 6, 12 and 30 months of history; full context vs the memory."""
    out, cfgs = {}, ["full_context", "facts_log", "brain_jev_recall", "brain_all"]
    for w, tokens in (("life6m", "~26k"), ("life12m", "~51k"), ("life30m", "~122k")):
        f = RES / f"scenario_{w}_runs.json"
        if not f.exists():
            continue
        r = json.loads(f.read_text())
        out[w] = {"tokens": tokens}
        for c in cfgs:
            if c in r:
                rows = r[c]["rows"]
                agg = [x["score"] for x in rows if x["type"] == "aggregate"]
                out[w][c] = {"accuracy": r[c]["accuracy"], "aggregate": round(100 * sum(agg) / len(agg), 1),
                             "answer_tokens": round(sum(x["answer_tokens_in"] for x in rows) / len(rows))}
        if "full_context" in r and "brain_all" in r:
            out[w]["brain_all_minus_full"] = paired(r["brain_all"]["rows"], r["full_context"]["rows"])
    print(json.dumps(out, indent=1))
    (RES / "crossover_summary.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__" and sys.argv[1] == "crossover":
    crossover()
