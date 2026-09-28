"""H5 (LLP 0013): Jev vs GPT-6 Luna as the decider, on the same closed decisions, in the same batches.

  aspects   is this message an intention / done / cancel / preference / milestone?  (a labelled sample of the log)
  rows      does this stored row record its message faithfully?                  (round-6 truth: 163 wrong rows)
  verifier  does the database's computed answer answer the question?              (the 94 benchmark questions, graded)
  routing   which stores does a question need?                                    (from the brain runs, vs the oracle)

Metrics: AUROC of the probability, accuracy at 0.5, cost and latency per request (as first measured; replays are free).

  .venv/bin/python -m lab.bench.brain deciders       -> lab/results/brain_deciders.json
"""
# @ref LLP 0013#hypotheses — H5, the decider ablation behind "Jev is why now" (LLP 0008)
from __future__ import annotations

import json
import random
import re
import sqlite3
from pathlib import Path

from lab.common.grade import jev_grade
from lab.common.llm import LEDGER
from lab.memory import deciders
from lab.memory.routers import TYPE_STORES
from lab.memory.stores import ASPECTS, label_batches

LAB = Path(__file__).resolve().parents[1]
OUT = LAB / "results" / "brain_deciders.json"


def auroc(scores: list[float], labels: list[bool]) -> float | None:
    pos = [s for s, y in zip(scores, labels) if y]
    neg = [s for s, y in zip(scores, labels) if not y]
    if not pos or not neg:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return round(wins / (len(pos) * len(neg)), 4)


def calls_since(n0: int, prefix: str) -> dict:
    cs = [c for c in LEDGER.calls[n0:] if c.tag.startswith(prefix)]
    lat = sorted(c.latency for c in cs)
    return {"requests": len(cs), "cost": round(sum(c.cost for c in cs), 5), "new_spend": round(sum(c.cost for c in cs if not c.cached), 5),
            "input_tokens": sum(c.input_tokens for c in cs), "output_tokens": sum(c.output_tokens for c in cs),
            "latency_median_s": round(lat[len(lat) // 2], 3) if lat else None}


# ------------------------------------------------------------------------------------------------- aspects
def aspect_truth(text: str, kind: str, truth: dict | None) -> dict[str, bool | None]:
    """Hand rules over the simulator's messages: True / False / None (ambiguous, not scored)."""
    t = text if isinstance(text, str) else json.dumps(text)
    y = {a: False for a in ASPECTS}
    if truth:   # generated daily life
        k = truth.get("kind")
        if k == "movie":
            y["preference"] = True
        if k == "climb" and truth.get("sent"):
            y["milestone"] = True
        if k == "run":
            y["milestone"] = None          # a fast run may be a personal record
        if k == "climb" and "tonight" in t:
            y["intention"] = None          # "climbing tonight" can read as a plan
        return y
    if kind in ("chitchat", "question"):
        if t.strip().lower() == "nvm":
            y["cancel"] = None
        return y
    rules = {
        "intention": r"VEVENT|[Rr]emind me to|appointment with|set up a call|moved our call|booked the dentist|dentist in SF|"
                     r"kickoff|booked flights|Booking confirmed|trip confirmation|train to Brno",
        "done": r"— done|[Dd]one: |flu shot done",
        "cancel": r"cancelled the|never mind the|ended my",
        "preference": r"loves orchids|allergic|\d/5|pastéis",
        "milestone": r"new job|started today|CLOSED THE SEED|hired|joined Nebula|landed in SF|got the job|moved from|"
                     r"PROPOSED|left Nebula|sent my first|finally sent",
    }
    for a, rx in rules.items():
        y[a] = bool(re.search(rx, t))
    if re.search(r"[Dd]one with|[Ff]inished", t):
        y["done"] = None                   # finishing a book: a done "to-do"? ambiguous
    if re.search(r"follow up|I'm Adam|US number|joined Mission|met |Term sheet|back from Lisbon|climbs too|membership", t):
        for a in ("intention", "milestone", "preference"):
            if not y[a]:
                y[a] = None
    return y


def aspects_sample(seed: int = 7) -> list[dict]:
    ds = json.loads((LAB / "datasets" / "life_scale.json").read_text())
    db = sqlite3.connect(LAB / "runs" / "brain" / "db.sqlite")
    ids = {r[1]: r[0] for r in db.execute("SELECT id, ts FROM _log")}   # log id by timestamp (same order as the dataset)
    log_ids = [r[0] for r in db.execute("SELECT id FROM _log ORDER BY id")]
    msgs = ds["messages"]
    rng = random.Random(seed)
    story = [i for i, m in enumerate(msgs) if "truth" not in m and m["kind"] not in ("chitchat", "question")]
    movies = [i for i, m in enumerate(msgs) if m.get("truth", {}).get("kind") == "movie"]
    sends = [i for i, m in enumerate(msgs) if m.get("truth", {}).get("sent")]
    plain = [i for i, m in enumerate(msgs) if m.get("truth") and m["truth"].get("kind") != "movie" and not m["truth"].get("sent")]
    chat = [i for i, m in enumerate(msgs) if m["kind"] in ("chitchat", "question")]
    pick = sorted(set(story + rng.sample(movies, 30) + sends + rng.sample(plain, 150) + rng.sample(chat, 30)))
    texts = {r[0]: (r[1], r[2]) for r in db.execute("SELECT id, ts, text FROM _log")}
    out = []
    for i in pick:
        m = msgs[i]
        lid = log_ids[i]
        ts, text = texts[lid]
        if text == "[forgotten]":
            continue
        out.append({"id": lid, "ts": ts, "text": text, "y": aspect_truth(m["text"], m["kind"], m.get("truth"))})
    return out


def run_aspects(sample, decider) -> dict:
    n0 = len(LEDGER.calls)
    got = label_batches(decider, [(s["id"], s["ts"], s["text"]) for s in sample])
    res = {"calls": calls_since(n0, f"memory:aspects:{decider.name}")}
    for a in ASPECTS:
        scored = [(got[str(s["id"])][a], s["y"][a]) for s in sample if s["y"][a] is not None]
        ps, ys = [p for p, _ in scored], [y for _, y in scored]
        res[a] = {"n": len(ys), "positives": sum(ys), "auroc": auroc(ps, ys),
                  "accuracy": round(sum((p >= 0.5) == y for p, y in scored) / len(ys), 4),
                  "recall": round(sum(p >= 0.5 for p, y in scored if y) / max(1, sum(ys)), 4),
                  "precision": round(sum(y for p, y in scored if p >= 0.5) / max(1, sum(p >= 0.5 for p, _ in scored)), 4)}
    return res


# ---------------------------------------------------------------------------------------------------- rows
def run_rows(decider, n_right: int = 337, seed: int = 7) -> dict:
    from lab.systems import confidence as cf
    db = sqlite3.connect(LAB / "runs" / "app" / "db.sqlite")
    truth = json.loads((LAB / "runs" / "app" / "truth.json").read_text())
    ctx = cf.Context(db, json.loads((LAB / "datasets" / "life_scale.json").read_text())["user"])
    wrong = [k for k, v in truth.items() if v["wrong"]]
    right = random.Random(seed).sample([k for k, v in truth.items() if not v["wrong"]], n_right)
    keys = sorted(wrong + right, key=lambda k: (k.split("|")[0], int(k.split("|")[1])))
    items = []
    for k in keys:
        t, rid = k.split("|")
        row = db.execute(f"SELECT * FROM '{t}' WHERE id = ?", (int(rid),)).fetchone()
        if row:
            items.append((t, dict(zip(ctx.cols[t], row)), k))
    jobs, batches = [], []
    for i in range(0, len(items), 8):   # the round-6 layout: 8 rows per request, up to 3 questions per row
        state, qs, batch = {}, {}, []
        for j, (t, row, k) in enumerate(items[i:i + 8]):
            sent, msg = ctx.source(row)
            state[f"row_{j}"], state[f"message_{j}"], state[f"sent_{j}"] = ctx.readable(t, row), msg, sent
            for q, text in cf.QUESTIONS.items():
                if q == "people" and not any(c in ctx.cols[t] for c in cf.PEOPLE_LINKS if c != "person_id"):
                    continue
                if q == "date" and not cf.date_col(ctx.cols[t]):
                    continue
                qs[f"{q}_{j}"] = text.format(row=f"row_{j}", msg=f"message_{j}", sent=f"sent_{j}")
            batch.append(k)
        jobs.append((state, qs))
        batches.append(batch)
    n0 = len(LEDGER.calls)
    answers = decider.batch(jobs, "yes_many", tag=f"h5:rows:{decider.name}")
    ps, ys = [], []
    for batch, ans in zip(batches, answers):
        for j, k in enumerate(batch):
            p = [v for q, v in ans.items() if q.endswith(f"_{j}")]
            ps.append(1 - min(p, default=1.0))          # higher = more likely wrong
            ys.append(bool(truth[k]["wrong"]))
    top = sorted(zip(ps, ys), key=lambda x: -x[0])[:max(1, len(ps) // 10)]
    return {"calls": calls_since(n0, f"h5:rows:{decider.name}"), "n": len(ys), "wrong": sum(ys), "auroc": auroc(ps, ys),
            "wrong_in_top_10pct": sum(y for _, y in top), "top_10pct": len(top)}


# ------------------------------------------------------------------------------------------------ verifier
VERIFY_Q = ("`result` was computed from the user's own database for `question` by the query in `query`. Does it answer "
            "`question`: the right table, every thing the question names (all of them, not just one), the right period, "
            "and the right kind of value?")


def verifier_items() -> list[dict]:
    """The deterministic reader's chosen answer for each benchmark question, graded against the gold (Jev judge)."""
    from lab.bench.brain import context
    ctx = context()
    qs = json.loads((LAB / "datasets" / "memory_types.json").read_text())["questions"]
    out = []
    for q in qs:
        det = ctx.reader.ask(q["question"])
        if det["answer"] == "I don't know.":
            continue
        g = jev_grade(q["question"], q["answer"], det["answer"], tag="h5:verifier:grade")
        out.append({"question": q["question"], "query": det.get("plan", ""), "result": det["answer"][:1500], "right": g["score"] == 1.0})
    return out


def run_verifier(items, decider) -> dict:
    n0 = len(LEDGER.calls)
    got = decider.batch([({"question": x["question"], "query": x["query"], "result": x["result"]}, {"answers": VERIFY_Q})
                         for x in items], "yes_many", tag=f"h5:verify:{decider.name}")
    ps, ys = [g["answers"] for g in got], [x["right"] for x in items]
    return {"calls": calls_since(n0, f"h5:verify:{decider.name}"), "n": len(ys), "right": sum(ys), "auroc": auroc(ps, ys),
            "accuracy": round(sum((p >= 0.5) == y for p, y in zip(ps, ys)) / len(ys), 4)}


# ------------------------------------------------------------------------------------------------- routing
def routing() -> dict:
    res = json.loads((LAB / "results" / "brain_runs.json").read_text())
    out = {}
    for n, dec in (("brain_jev", "jev"), ("brain_llm_router", "llm")):
        if n not in res:
            continue
        rows = res[n]["rows"]
        ps, ys = [], []
        for r in rows:
            need = set(TYPE_STORES[r["type"]])
            for s, p in (r["route_p"] or {}).items():
                ps.append(p)
                ys.append(s in need)
        lat = sorted(r["route_latency_s"] or 0 for r in rows)
        out[dec] = {"accuracy_of_config": res[n]["accuracy"], "auroc_vs_oracle_stores": auroc(ps, ys),
                    "accuracy_at_0.5": round(sum((p >= 0.5) == y for p, y in zip(ps, ys)) / len(ys), 4),
                    "route_latency_median_s": lat[len(lat) // 2]}
    return out


def main():
    jev, llm = deciders.make("jev"), deciders.make("llm")
    out = {}
    sample = aspects_sample()
    out["aspects"] = {"n_messages": len(sample), "jev": run_aspects(sample, jev), "llm": run_aspects(sample, llm)}
    print("aspects", json.dumps({d: {a: out["aspects"][d][a]["auroc"] for a in ASPECTS} | {"calls": out["aspects"][d]["calls"]}
                                 for d in ("jev", "llm")}))
    out["rows"] = {"jev": run_rows(jev), "llm": run_rows(llm)}
    print("rows", json.dumps(out["rows"]))
    items = verifier_items()
    out["verifier"] = {"jev": run_verifier(items, jev), "llm": run_verifier(items, llm)}
    print("verifier", json.dumps(out["verifier"]))
    out["routing"] = routing()
    print("routing", json.dumps(out["routing"]))
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1))
