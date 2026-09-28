"""Round 5 (LLP 0003): does a schema shaped by its own query log answer future questions better and faster?

  log       the past questions on the base database, through the hybrid read path -> the query log (`_queries`)
  optimize  the schema advisor turns the log into migrations                        -> db_opt.sqlite
  compile   the past questions again on db_opt; trusted plans -> question templates
  evaluate  the future questions under base / opt / opt+compiled; the 72 scale questions on base and opt
  latency   a live sample (cache off) of each path
  physical  indexes on the columns the log filters on: SQL time at 5k rows and on a 1M-row copy (no model)

    .venv/bin/python -m lab.bench.workload log|optimize|compile|evaluate|latency|physical [--budget 2]

Gold answers come from the simulator's truth (lab/datasets/workload.py) and are used only for grading here; the advisor
and the compiler see the query log, never the gold. Every model call is cached, so re-runs replay for free.
"""
# @ref LLP 0003#protocol — conditions, readers, rule grading, budget
from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import sqlite3
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from lab.common.grade import jev_grade
from lab.common.llm import LAB_DIR, LEDGER
from lab.systems import query_log, schema_advisor
from lab.systems.compiled_reader import CompiledReader
from lab.systems.derived import fold
from lab.systems.det_reader import DetReader
from lab.systems.engine import Engine
from lab.systems.reader import ask_agent

WL = LAB_DIR / "datasets" / "workload.json"
SCALE = LAB_DIR / "datasets" / "life_scale.json"
SRC = LAB_DIR / "runs" / "scale5k" / "db_async.sqlite"
RUN = LAB_DIR / "runs" / "workload"
OUT = LAB_DIR / "results"
MODEL = "openai:gpt-6-luna"
THRESHOLD = 0.7
BUDGET = 2.0


def spent() -> float:
    return sum(c.cost for c in LEDGER.calls if not c.cached)


def guard():
    if spent() > BUDGET:
        raise SystemExit(f"budget: this run spent ${spent():.3f} > ${BUDGET}")


def workload() -> tuple[dict, datetime]:
    wl = json.loads(WL.read_text())
    return wl, datetime.fromisoformat(wl["now"])


# ------------------------------------------------------------------------------------------------ grading (no model)
ABSTAIN = re.compile(r"i don.?t know|no (record|data|information)|not (recorded|stored|available)|can.?t (tell|find)", re.I)
MON = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def grade(q: dict, answer: str) -> float:
    g, a = q["grade"], str(answer or "")
    if not a.strip() or ABSTAIN.search(a):
        return 0.0
    nums = query_log.numbers(a)
    if g["type"] == "amounts":
        hits = sum(any(abs(n - v) <= max(0.005 * abs(v), 0.51) for n in nums) for v, _ in g["values"])
        return 1.0 if hits == len(g["values"]) else 0.5 if hits else 0.0
    if g["type"] == "count":
        asked = set(query_log.numbers(q["question"]))
        return 1.0 if any(n == g["value"] and n not in asked for n in nums) else 0.0
    if g["type"] == "number":
        v = g["value"]
        return 1.0 if any(abs(n - v) <= max(g["tol"] * abs(v), 0.006) for n in nums) else 0.0
    if g["type"] == "date":
        y, m, d = g["value"].split("-")
        mon, day, text = MON[int(m) - 1], str(int(d)), fold(a)
        return 1.0 if g["value"] in a or re.search(rf"\b{mon}\w*\.? {day}\b|\b{day}\.? {mon}", text) else 0.0
    if g["type"] == "name":
        return 1.0 if fold(g["value"]) in fold(a) else 0.0
    raise ValueError(g["type"])


def trusted(out: dict) -> bool:
    return (out.get("verified") or 0) >= THRESHOLD and out["answer"] != "I don't know."


# ------------------------------------------------------------------------------------------------ batches
def det_batch(reader: DetReader, questions: list[dict], workers: int = 6) -> dict:
    def one(q):
        t0 = time.time()
        out = reader.ask(q["question"])
        return q["id"], {"out": out, "wall_s": round(time.time() - t0, 3)}
    n0 = len(LEDGER.calls)
    with ThreadPoolExecutor(workers) as pool:
        res = dict(pool.map(one, questions))
    calls = LEDGER.calls[n0:]
    guard()
    return {"by_q": res, "cost_per_q": sum(c.cost for c in calls) / max(1, len(questions)),
            "live_share": round(sum(not c.cached for c in calls) / max(1, len(calls)), 3)}


def agent_batch(eng: Engine, questions: list[dict], now: datetime, user: str, tag: str, workers: int = 6) -> dict:
    def one(q):
        t0 = time.time()
        ans = ask_agent(eng, q["question"], now, user, MODEL, tag)["answer"]
        return q["id"], {"answer": ans, "wall_s": round(time.time() - t0, 3)}
    n0 = len(LEDGER.calls)
    with ThreadPoolExecutor(workers) as pool:
        res = dict(pool.map(one, questions))
    calls = LEDGER.calls[n0:]
    guard()
    return {"by_q": res, "cost_per_q": sum(c.cost for c in calls) / max(1, len(questions)),
            "live_share": round(sum(not c.cached for c in calls) / max(1, len(calls)), 3)}


# ------------------------------------------------------------------------------------------------ steps
def log_step():
    """Ask the past questions the way a deployed system would (deterministic first, the agent when the verifier
    doubts it) and keep everything in the base database's query log."""
    wl, now = workload()
    RUN.mkdir(parents=True, exist_ok=True)
    base = RUN / "db_base.sqlite"
    shutil.copy(SRC, base)
    reader = DetReader(str(base), wl["user"], now)
    det = det_batch(reader, wl["past"])
    need = [q for q in wl["past"] if not trusted(det["by_q"][q["id"]]["out"])]
    agent = agent_batch(reader.eng, need, now, wl["user"], "workload:agent")
    rows = []
    for q in wl["past"]:
        d = det["by_q"][q["id"]]
        a = agent["by_q"].get(q["id"], {}).get("answer")
        e = {"qid": q["id"], "ts": wl["now"], "question": q["question"], "path": "agent" if a is not None else "deterministic",
             "answer": d["out"]["answer"] if a is None else a, "plan": query_log.plan_of(d["out"]),
             "verified": d["out"].get("verified"), "signals": query_log.signals(reader, q["question"], d["out"], THRESHOLD, a),
             "agent_answer": a, "latency_s": d["wall_s"]}
        query_log.record(reader.eng.db, {**e, "answer": d["out"]["answer"]})
        rows.append({**e, "det_answer": d["out"]["answer"], "template": q["template"],
                     "score_det": grade(q, d["out"]["answer"]), "score_path": grade(q, e["answer"])})
    n = len(rows)
    summary = {"questions": n, "trusted": n - len(need), "flagged": len(schema_advisor.flagged(query_log.load(reader.eng.db))),
               "det_accuracy": round(sum(r["score_det"] for r in rows) / n, 4),
               "hybrid_accuracy": round(sum(r["score_path"] for r in rows) / n, 4),
               "signals": dict(sorted(__import__("collections").Counter(s.split(":")[0] for r in rows for s in r["signals"]).items())),
               "spent": round(spent(), 4)}
    (RUN / "log.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=1, ensure_ascii=False))
    print(json.dumps(summary, indent=1))


def optimize_step(model: str):
    wl, now = workload()
    opt = RUN / "db_opt.sqlite"
    shutil.copy(RUN / "db_base.sqlite", opt)
    eng = Engine(str(opt), strict=True, normalize=True)
    n0 = len(LEDGER.calls)
    report = schema_advisor.run(eng, query_log.load(eng.db), model, ts=wl["now"], user=wl["user"], now=now)
    report["cost"] = round(sum(c.cost for c in LEDGER.calls[n0:]), 5)
    report["model"] = model
    (RUN / "migrations.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))
    for m in report["migrations"]:
        print(m["op"], m["table"], m["column"], "KEPT" if m["kept"] else f"rejected: {m.get('reason')}",
              json.dumps(m.get("stats", {}), ensure_ascii=False)[:400])
    print("cost", report["cost"], "spent", round(spent(), 4))


def compile_step():
    wl, now = workload()
    reader = DetReader(str(RUN / "db_opt.sqlite"), wl["user"], now)
    det = det_batch(reader, wl["past"])
    examples = [{"question": q["question"], "plan": query_log.plan_of(det["by_q"][q["id"]]["out"])}
                for q in wl["past"] if trusted(det["by_q"][q["id"]]["out"])]
    n0 = len(LEDGER.calls)
    cr = CompiledReader(reader, now, MODEL)
    cr.compile(examples)
    rows = [{"question": q["question"], "template": q["template"], "trusted": trusted(det["by_q"][q["id"]]["out"]),
             "score_det": grade(q, det["by_q"][q["id"]]["out"]["answer"])} for q in wl["past"]]
    out = {"examples": len(examples), "templates": cr.templates, "report": cr.report,
           "aliases": [{"table": t, "column": c, "words": w} for (t, c), w in cr.aliases.items()],
           "cost": round(sum(c.cost for c in LEDGER.calls[n0:]), 5),
           "past_on_opt": {"det_accuracy": round(sum(r["score_det"] for r in rows) / len(rows), 4),
                           "trusted": len(examples)}}
    (RUN / "templates.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(json.dumps({k: v for k, v in out.items() if k in ("examples", "cost", "past_on_opt")}, indent=1),
          f"\n{len(cr.templates)} templates kept of {len(cr.report)} proposed")


def load_compiled(reader: DetReader, now: datetime) -> CompiledReader:
    data = json.loads((RUN / "templates.json").read_text())
    cr = CompiledReader(reader, now, MODEL)
    cr.templates = data["templates"]
    cr.aliases = {(a["table"], a["column"]): a["words"] for a in data["aliases"]}
    return cr


def evaluate_step():
    wl, now = workload()
    fut = wl["future"]
    runs = {}
    for cond, db in (("base", "db_base.sqlite"), ("opt", "db_opt.sqlite")):
        reader = DetReader(str(RUN / db), wl["user"], now)
        det = det_batch(reader, fut)
        agent = agent_batch(reader.eng, fut, now, wl["user"], f"workload:agent:{cond}")
        runs[cond] = {"reader": reader, "det": det, "agent": agent}
        print(cond, "det cost/q", round(det["cost_per_q"], 6), "agent cost/q", round(agent["cost_per_q"], 6), "spent", round(spent(), 3))
    cr = load_compiled(runs["opt"]["reader"], now)
    rows = []
    for q in fut:
        r = {"id": q["id"], "group": q["group"], "template": q["template"], "question": q["question"], "gold": q["answer"]}
        for cond in ("base", "opt"):
            d, a = runs[cond]["det"]["by_q"][q["id"]], runs[cond]["agent"]["by_q"][q["id"]]
            own = trusted(d["out"])
            r[cond] = {"det": d["out"]["answer"][:200], "verified": d["out"].get("verified"),
                       "plan": d["out"].get("plan"), "agent": a["answer"][:200],
                       "score_det": grade(q, d["out"]["answer"]), "score_agent": grade(q, a["answer"]),
                       "path": "deterministic" if own else "agent"}
            r[cond]["score_hybrid"] = r[cond]["score_det"] if own else r[cond]["score_agent"]
        hit = cr.answer(q["question"])
        r["compiled"] = {"hit": hit is not None, "answer": hit["answer"][:200] if hit else None,
                         "plan": hit["text"] if hit else None, "wall_ms": round(hit["wall_s"] * 1000, 2) if hit else None,
                         "score": grade(q, hit["answer"]) if hit else None}
        r["compiled"]["score_path"] = r["compiled"]["score"] if hit else r["opt"]["score_hybrid"]
        rows.append(r)

    def summarize(rs):
        n = len(rs) or 1
        out = {"n": len(rs)}
        for cond in ("base", "opt"):
            out[cond] = {k: round(sum(r[cond][f"score_{k}"] for r in rs) / n, 4) for k in ("det", "agent", "hybrid")}
            out[cond]["answered_without_llm"] = sum(r[cond]["path"] == "deterministic" for r in rs)
        hits = [r for r in rs if r["compiled"]["hit"]]
        out["opt+compiled"] = {"hybrid": round(sum(r["compiled"]["score_path"] for r in rs) / n, 4),
                               "compiled_hits": len(hits),
                               "compiled_accuracy": round(sum(r["compiled"]["score"] for r in hits) / len(hits), 4) if hits else None,
                               "det_accuracy_on_hits": round(sum(r["opt"]["score_det"] for r in hits) / len(hits), 4) if hits else None,
                               "answered_without_llm": len(hits) + sum(r["opt"]["path"] == "deterministic" for r in rs if not r["compiled"]["hit"]),
                               "compiled_ms_p50": statistics.median([r["compiled"]["wall_ms"] for r in hits]) if hits else None}
        return out

    summary = {"all": summarize(rows)}
    for g in ("same", "reworded", "novel"):
        summary[g] = summarize([r for r in rows if r["group"] == g])
    summary["by_template"] = {t: summarize([r for r in rows if r["template"] == t]) for t in sorted({r["template"] for r in rows})}
    summary["cost_per_q"] = {c: {"det": round(runs[c]["det"]["cost_per_q"], 6), "agent": round(runs[c]["agent"]["cost_per_q"], 6)}
                             for c in runs}
    summary["regression_scale72"] = scale72(runs)
    summary["spent"] = round(spent(), 4)
    (OUT / "workload_eval.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=1, ensure_ascii=False))
    print(json.dumps({k: v for k, v in summary.items() if k != "by_template"}, indent=1))


def scale72(runs: dict) -> dict:
    """The 72 questions of round 4 on base and opt: deterministic alone, and the hybrid (the agent only when the
    verifier doubts). Graded by Jev as judge, as in round 4."""
    ds = json.loads(SCALE.read_text())
    now = datetime.fromisoformat(ds["now"])
    qs = [{"id": f"s{i:02d}", **q} for i, q in enumerate(ds["qa"])]
    out = {}
    for cond in ("base", "opt"):
        reader = runs[cond]["reader"]
        det = det_batch(reader, qs)
        need = [q for q in qs if not trusted(det["by_q"][q["id"]]["out"])]
        agent = agent_batch(reader.eng, need, now, ds["user"], "scale5k:agent")
        with ThreadPoolExecutor(8) as pool:
            s_det = list(pool.map(lambda q: jev_grade(q["question"], q["answer"], det["by_q"][q["id"]]["out"]["answer"])["score"], qs))
            s_hyb = list(pool.map(lambda q: jev_grade(q["question"], q["answer"], agent["by_q"][q["id"]]["answer"])["score"]
                                  if q["id"] in agent["by_q"] else None, qs))
        hyb = [h if h is not None else d for d, h in zip(s_det, s_hyb)]
        out[cond] = {"det": round(sum(s_det) / len(qs), 4), "hybrid": round(sum(hyb) / len(qs), 4),
                     "answered_without_llm": len(qs) - len(need)}
    return out


def latency_step(sample: int = 16, seed: int = 5):
    """Live timings (cache off) on a sample of future questions: base and opt deterministic reads, compiled hits."""
    os.environ["LAB_NO_CACHE"] = "1"
    wl, now = workload()
    qs = random.Random(seed).sample(wl["future"], sample)
    out = {}
    for cond, db in (("base", "db_base.sqlite"), ("opt", "db_opt.sqlite")):
        reader = DetReader(str(RUN / db), wl["user"], now)
        walls = []
        for q in qs:   # one at a time: the latency a user sees
            t0 = time.time()
            reader.ask(q["question"])
            walls.append(time.time() - t0)
        out[f"{cond}_deterministic"] = {"p50": round(statistics.median(walls), 3), "p90": round(sorted(walls)[int(0.9 * len(walls))], 3)}
        if cond == "opt":
            cr = load_compiled(reader, now)
            hits = [h["wall_s"] * 1000 for q in wl["future"] if (h := cr.answer(q["question"]))]
            out["compiled_ms"] = {"n": len(hits), "p50": round(statistics.median(hits), 2), "p90": round(sorted(hits)[int(0.9 * len(hits))], 2)} if hits else None
    out["spent"] = round(spent(), 4)
    (OUT / "workload_latency.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


def physical_step(copies: int = 360):
    """Is physical design (indexes) worth it? Time the plans the log keeps asking, at 5k rows and on a copy with the
    expenses table blown up `copies` times (~1M rows), three ways: the deterministic reader's own path (all rows into
    code, then filter), the same filters pushed into SQL, and pushed into SQL with indexes on the columns the log
    filters on most (chosen by counting, no model)."""
    from collections import Counter
    wl, now = workload()
    log = json.loads((RUN / "log.json").read_text())["rows"]
    use = Counter((r["plan"]["table"], d) for r in log if r["plan"].get("table") for d in r["plan"]["filters"])
    results = {"filter_use": {f"{t}.{c}": n for (t, c), n in use.most_common(8)}}
    big = RUN / "db_big.sqlite"
    shutil.copy(RUN / "db_opt.sqlite", big)
    db = sqlite3.connect(big)
    cols = [r[1] for r in db.execute("PRAGMA table_info(expenses)") if r[1] != "id"]
    db.execute(f"INSERT INTO expenses ({', '.join(cols)}) SELECT {', '.join(cols)} FROM expenses, "
               f"(WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < {copies - 1}) SELECT i FROM n)")
    db.commit()
    db.close()
    plans = [("sum", "amount", {"dining_occasion": "lunch"}, "year:2025"),
             ("count", "__row__", {"merchant": "Doubleshot"}, "year:2025"),
             ("max", "amount", {"category": "coffee", "expense_location": "san francisco"}, "all")]
    sql = [("SELECT currency, SUM(amount), COUNT(*) FROM expenses WHERE dining_occasion = ? AND date >= ? AND date < ? "
            "GROUP BY currency", ("lunch", "2025-01-01", "2026-01-01")),
           ("SELECT COUNT(*) FROM expenses WHERE merchant = ? AND date >= ? AND date < ?", ("Doubleshot", "2025-01-01", "2026-01-01")),
           ("SELECT MAX(amount) FROM expenses WHERE category = ? AND expense_location = ?", ("coffee", "san francisco"))]
    indexes = ["CREATE INDEX IF NOT EXISTS ix_occasion_date ON expenses(dining_occasion, date)",
               "CREATE INDEX IF NOT EXISTS ix_merchant_date ON expenses(merchant, date)",
               "CREATE INDEX IF NOT EXISTS ix_category_location ON expenses(category, expense_location)"]

    def ms(fn, reps=3):
        t0 = time.perf_counter()
        for _ in range(reps):
            fn()
        return round((time.perf_counter() - t0) / reps * 1000, 2)
    for name, path in (("5k", RUN / "db_opt.sqlite"), ("1M", big)):
        reader = DetReader(str(path), wl["user"], now)
        n = reader.eng.db.execute("SELECT COUNT(*) FROM expenses").fetchone()[0]
        in_code = [ms(lambda p=p: reader._apply("expenses", p[0], p[1], reader._select("expenses", p[2], p[3], p[1])), 1)
                   for p in plans]
        conn = sqlite3.connect(path)
        pushed = [ms(lambda q=q: conn.execute(*q).fetchall()) for q in sql]
        for ix in indexes:
            conn.execute(ix)
        indexed = [ms(lambda q=q: conn.execute(*q).fetchall()) for q in sql]
        for ix in ("ix_occasion_date", "ix_merchant_date", "ix_category_location"):
            conn.execute(f"DROP INDEX IF EXISTS {ix}")
        conn.close()
        results[name] = {"rows": n, "reader_in_code_ms": in_code, "sql_ms": pushed, "sql_indexed_ms": indexed}
        print(name, results[name])
    big.unlink()
    (OUT / "workload_physical.json").write_text(json.dumps(results, indent=1))
    print(json.dumps(results, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["log", "optimize", "compile", "evaluate", "latency", "physical"])
    ap.add_argument("--budget", type=float, default=BUDGET)
    ap.add_argument("--advisor-model", default=MODEL)
    args = ap.parse_args()
    BUDGET = args.budget
    {"log": log_step, "optimize": lambda: optimize_step(args.advisor_model), "compile": compile_step,
     "evaluate": evaluate_step, "latency": latency_step, "physical": physical_step}[args.cmd]()
