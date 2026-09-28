"""Jev vs LLMs on the individual decisions a fluid database makes.

  .venv/bin/python -m lab.bench.components gate|er|retrieval|answerable|all

B1 gate        does this incoming message need a database write?
B2 er          which known person is this message about (or nobody)?
B3 retrieval   which stored memories answer this question?
B4 answerable  do these retrieved memories contain the answer at all?
"""
# @ref LLP 0002.000 — a round-1 bench
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from concurrent.futures import ThreadPoolExecutor

from lab.common import jev
from lab.common.llm import LAB_DIR, LEDGER, chat_json
from lab.systems import jev_layer
from lab.systems.base import render_input
from lab.systems.engine import bm25_rank

LLMS = ["openai:gpt-6-luna", "anthropic:claude-haiku-4-5", "anthropic:claude-sonnet-5"]
DS = LAB_DIR / "datasets"
OUT = LAB_DIR / "results"
OUT.mkdir(exist_ok=True)


def load(name):
    return json.loads((DS / f"{name}.json").read_text())


def pmap(fn, items, workers=8):
    with ThreadPoolExecutor(workers) as pool:
        return list(pool.map(fn, items))


def cost_latency(tag: str, n_items: int) -> dict:
    calls = [c for c in LEDGER.calls if c.tag == tag]
    lat = [c.latency for c in calls]
    return {"cost_per_1k": round(sum(c.cost for c in calls) / max(n_items, 1) * 1000, 4),
            "latency_p50": round(statistics.median(lat), 3) if lat else None,
            "latency_mean": round(statistics.mean(lat), 3) if lat else None,
            "calls": len(calls)}


# ------------------------------------------------------------------ B1 gate
KIND_TO_INTENT = {"store": "share", "update": "change", "delete": "remove", "question": "ask", "chitchat": "chat",
                  "ask": "ask", "chat": "chat"}
GATE_PROMPT = """Classify a message a user sent to their personal AI assistant, which keeps a database of everything worth remembering about the user's life.

intent (pick one):
- share: tells the assistant new information: a fact, event, plan, purchase, contact, preference or document
- change: corrects or updates something shared before, or reports that its status changed (moved, finished, sold, renewed)
- remove: asks to forget or delete information, or says a plan or appointment was cancelled
- ask: asks the assistant a question or asks it to do something
- chat: small talk, a greeting, a reaction or thanks, with no information to keep

needs_write: true if the database should be changed because of this message (even if the message is also a request).

MESSAGE (sent {ts}): {text}"""
GATE_SCHEMA = {"type": "object", "properties": {
    "intent": {"type": "string", "enum": ["share", "change", "remove", "ask", "chat"]},
    "needs_write": {"type": "boolean"}}, "required": ["intent", "needs_write"], "additionalProperties": False}


def gate_items():
    items = []
    for m in load("life_stream")["messages"]:
        items.append({"text": render_input(m["text"]), "ts": m["ts"], "intent": KIND_TO_INTENT[m["kind"]], "src": "life"})
    for m in load("alex_rivera")["messages"]:
        items.append({"text": m["text"], "ts": m["ts"], "intent": "share", "src": "alex"})
    for m in load("components")["gate_extra"]:
        items.append({"text": m["text"], "ts": "2026-09-21T10:00:00", "intent": KIND_TO_INTENT[m["kind"]], "src": "extra"})
    for it in items:
        it["write"] = it["intent"] in ("share", "change", "remove")
    return items


def binary(pred, gold):
    tp = sum(p and g for p, g in zip(pred, gold))
    fp = sum(p and not g for p, g in zip(pred, gold))
    fn = sum(g and not p for p, g in zip(pred, gold))
    tn = sum(not p and not g for p, g in zip(pred, gold))
    prec = tp / (tp + fp) if tp + fp else 0
    rec = tp / (tp + fn) if tp + fn else 0
    return {"accuracy": round((tp + tn) / len(gold), 4), "precision": round(prec, 4), "recall": round(rec, 4),
            "f1": round(2 * prec * rec / (prec + rec), 4) if prec + rec else 0, "false_negatives": fn,
            "false_positives": fp}


def bench_gate():
    items = gate_items()
    gold_intent = [i["intent"] for i in items]
    gold_write = [i["write"] for i in items]
    report = {"n": len(items), "n_write": sum(gold_write), "methods": {}}

    res = pmap(lambda it: jev_layer.gate(it["text"], it["ts"], tag="bench:gate:jev"), items, workers=16)
    report["methods"]["jev"] = {
        "intent_accuracy": round(sum(r["intent"] == g for r, g in zip(res, gold_intent)) / len(items), 4),
        "write": binary([r["write"] for r in res], gold_write),
        **cost_latency("bench:gate:jev", len(items)),
        "calibration": calibration([(r["confidence"], r["intent"] == g) for r, g in zip(res, gold_intent)]),
        "errors": [{"text": it["text"][:90], "gold": it["intent"], "pred": r["intent"], "conf": r["confidence"],
                    "worth": round(r["worth"], 2), "write": r["write"]}
                   for it, r in zip(items, res) if r["intent"] != it["intent"] or r["write"] != it["write"]],
    }
    for model in LLMS:
        tag = f"bench:gate:{model}"
        out = pmap(lambda it: chat_json(model, GATE_PROMPT.format(ts=it["ts"], text=it["text"]), GATE_SCHEMA,
                                        schema_name="gate", tag=tag, max_tokens=2000)[0], items)
        report["methods"][model] = {
            "intent_accuracy": round(sum(o["intent"] == g for o, g in zip(out, gold_intent)) / len(items), 4),
            "write": binary([o["needs_write"] for o in out], gold_write),
            **cost_latency(tag, len(items)),
            "errors": [{"text": it["text"][:90], "gold": it["intent"], "pred": o["intent"], "write": o["needs_write"]}
                       for it, o in zip(items, out) if o["intent"] != it["intent"] or o["needs_write"] != it["write"]],
        }
    return report


def calibration(pairs, bins=(0.0, 0.5, 0.7, 0.9, 1.01)):
    out = []
    for lo, hi in zip(bins, bins[1:]):
        sel = [ok for c, ok in pairs if lo <= c < hi]
        if sel:
            out.append({"confidence": f"{lo:.1f}-{min(hi, 1):.1f}", "n": len(sel), "accuracy": round(sum(sel) / len(sel), 3)})
    return out


# -------------------------------------------------------------------- B2 ER
ER_PROMPT = """{user} sent this message to their personal AI assistant. Which person from the contact list is the message about?
Return the id of that person, or null if the message is about someone who is not in the list (or about nobody in it).

CONTACTS:
{people}

MESSAGE: {text}"""
ER_SCHEMA = {"type": "object", "properties": {"person_id": {"type": ["integer", "null"]}},
             "required": ["person_id"], "additionalProperties": False}


def person_line(p):
    return "; ".join(f"{k}: {v}" for k, v in p.items() if k != "id")


def bench_er():
    comp = load("components")
    people, cases = comp["er_people"], comp["er_cases"]
    report = {"n": len(cases), "methods": {}}

    # Jev A: one Choice over all known people + "none"
    options = {f"p{p['id']}": person_line(p) for p in people}
    options["none"] = "Someone who is not in this list, or no specific person"
    q = {"who": jev.choice("Which person does `message` refer to? The message was written by Adam Zvada.", options)}
    res = pmap(lambda c: jev.ask({"message": c["text"]}, q, tag="bench:er:jev-choice")["who"], cases, workers=16)
    preds = [None if r["choice"] == "none" else int(r["choice"][1:]) for r in res]
    report["methods"]["jev-choice"] = er_metrics(cases, preds, [r["confidence"] for r in res], "bench:er:jev-choice")

    # Jev B: one Noul per (message, person) pair, pick the best above 0.5
    pair_q = {"same": jev.noul("Is `person` someone that `message` talks about? The message was written by Adam Zvada.")}
    jobs = [({"message": c["text"], "person": {k: v for k, v in p.items() if k != "id"}}, pair_q) for c in cases for p in people]
    answers = jev.ask_many(jobs, workers=16, tag="bench:er:jev-pairs")
    preds, confs = [], []
    for i, c in enumerate(cases):
        scores = [answers[i * len(people) + j]["same"]["noul"] for j in range(len(people))]
        best = max(range(len(people)), key=lambda j: scores[j])
        preds.append(people[best]["id"] if scores[best] >= 0.5 else None)
        confs.append(scores[best])
    report["methods"]["jev-pairs"] = er_metrics(cases, preds, confs, "bench:er:jev-pairs")

    listing = "\n".join(f"{p['id']}. {person_line(p)}" for p in people)
    for model in LLMS:
        tag = f"bench:er:{model}"
        out = pmap(lambda c: chat_json(model, ER_PROMPT.format(user="Adam Zvada", people=listing, text=c["text"]),
                                       ER_SCHEMA, schema_name="match", tag=tag, max_tokens=2000)[0], cases)
        report["methods"][model] = er_metrics(cases, [o["person_id"] for o in out], None, tag)
    return report


def er_metrics(cases, preds, confs, tag):
    ok = [p == c["gold"] for p, c in zip(preds, cases)]
    m = {"accuracy": round(sum(ok) / len(ok), 4),
         "accuracy_new_person": _sub(ok, [c["gold"] is None for c in cases]),
         "accuracy_ambiguous": _sub(ok, [c.get("ambiguous", False) for c in cases]),
         **cost_latency(tag, len(cases)),
         "errors": [{"text": c["text"], "gold": c["gold"], "pred": p, **({"conf": round(cf, 2)} if confs else {})}
                    for c, p, cf, good in zip(cases, preds, confs or [None] * len(cases), ok) if not good]}
    if confs:
        m["calibration"] = calibration(list(zip(confs, ok)))
        m["mean_conf_clear"] = round(statistics.mean(cf for cf, c in zip(confs, cases) if not c.get("ambiguous")), 3)
        m["mean_conf_ambiguous"] = round(statistics.mean(cf for cf, c in zip(confs, cases) if c.get("ambiguous")), 3)
    return m


def _sub(ok, mask):
    sel = [o for o, m in zip(ok, mask) if m]
    return round(sum(sel) / len(sel), 4) if sel else None


# -------------------------------------------------------------- B3 retrieval
LIST_PROMPT = """Below are memories a personal AI assistant stored for its user (Adam Zvada), one per line with an id and the date it was recorded.
List the ids of every memory needed to answer the question, most important first. Include memories that were later corrected or that contain changes, since the answer may depend on them. Return at most 15 ids.

MEMORIES:
{memories}

QUESTION (asked {now}): {question}"""
LIST_SCHEMA = {"type": "object", "properties": {"ids": {"type": "array", "items": {"type": "string"}}},
               "required": ["ids"], "additionalProperties": False}
RETRIEVE_Q = {
    "is_relevant": jev.noul("Is `memory` about the subject of `question`?"),
    "has_evidence": jev.noul("Does `memory` contain information that could be used to answer `question`?"),
}


def corpus():
    docs = []
    for m in load("life_stream")["messages"]:
        docs.append({"id": m["id"], "text": f"[{m['ts'][:10]}] {render_input(m['text'])}"})
    for m in load("alex_rivera")["messages"]:
        docs.append({"id": m["id"], "text": f"[{m['ts'][:10]}] {m['text']}"})
    return docs


def retrieval_queries():
    life = load("life_stream")
    return [q for q in life["qa"] if q["evidence"] and q["category"] != "deleted"], life["now"]


def jev_rank(question, docs, tag):
    jobs = [({"question": question, "memory": d["text"]}, RETRIEVE_Q) for d in docs]
    ans = jev.ask_many(jobs, workers=16, tag=tag)
    scores = [max(a["is_relevant"]["noul"], a["has_evidence"]["noul"]) for a in ans]
    order = sorted(range(len(docs)), key=lambda i: -scores[i])
    return [docs[i]["id"] for i in order], [scores[i] for i in order]


def bench_retrieval():
    docs = corpus()
    queries, now = retrieval_queries()
    report = {"n_queries": len(queries), "n_docs": len(docs), "methods": {}}
    rankings = {}

    rankings["bm25"] = [[docs[i]["id"] for i, _ in bm25_rank(q["question"], [d["text"] for d in docs])] for q in queries]
    report["methods"]["bm25"] = rank_metrics(queries, rankings["bm25"])

    try:
        rankings["embeddings"] = embed_rank(queries, docs)
        report["methods"]["openai-embeddings"] = rank_metrics(queries, rankings["embeddings"])
    except Exception as e:  # embeddings are a reference point, not essential
        report["methods"]["openai-embeddings"] = {"error": str(e)[:200]}

    jr = [jev_rank(q["question"], docs, "bench:retrieval:jev") for q in queries]
    rankings["jev"] = [r[0] for r in jr]
    report["methods"]["jev"] = {**rank_metrics(queries, rankings["jev"]),
                                **cost_latency("bench:retrieval:jev", len(queries)),
                                "note": "one request per (question, memory) pair; latency is per request, all pairs run concurrently"}
    # Retrieval-with-threshold view: how many memories pass 0.5, and their recall
    report["methods"]["jev"]["threshold_0.5"] = threshold_metrics(queries, jr, 0.5)
    report["methods"]["jev"]["threshold_0.2"] = threshold_metrics(queries, jr, 0.2)

    memories = "\n".join(f"{d['id']}: {d['text']}" for d in docs)
    for model in ["openai:gpt-6-luna", "anthropic:claude-sonnet-5"]:
        tag = f"bench:retrieval:{model}"
        out = pmap(lambda q: chat_json(model, LIST_PROMPT.format(memories=memories, now=now, question=q["question"]),
                                       LIST_SCHEMA, schema_name="ids", tag=tag, max_tokens=4000)[0]["ids"], queries)
        rankings[model] = out
        report["methods"][model] = {**rank_metrics(queries, out), **cost_latency(tag, len(queries)),
                                    "mean_returned": round(statistics.mean(len(o) for o in out), 2)}
    (OUT / "retrieval_rankings.json").write_text(json.dumps(rankings))
    return report


def embed_rank(queries, docs):
    import openai
    client = openai.OpenAI()
    emb = client.embeddings.create(model="text-embedding-3-small", input=[d["text"] for d in docs] + [q["question"] for q in queries])
    vecs = [e.embedding for e in emb.data]
    dv, qv = vecs[:len(docs)], vecs[len(docs):]

    def cos(a, b):
        return sum(x * y for x, y in zip(a, b)) / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))
    return [[docs[i]["id"] for i in sorted(range(len(docs)), key=lambda i: -cos(v, dv[i]))] for v in qv]


def rank_metrics(queries, rankings):
    r5, r10, all10, mrr = [], [], [], []
    for q, ranked in zip(queries, rankings):
        ev = set(q["evidence"])
        r5.append(len(ev & set(ranked[:5])) / len(ev))
        r10.append(len(ev & set(ranked[:10])) / len(ev))
        all10.append(float(ev <= set(ranked[:10])))
        first = next((i for i, d in enumerate(ranked) if d in ev), None)
        mrr.append(1 / (first + 1) if first is not None else 0)
    return {"recall@5": round(statistics.mean(r5), 4), "recall@10": round(statistics.mean(r10), 4),
            "all_evidence@10": round(statistics.mean(all10), 4), "mrr": round(statistics.mean(mrr), 4)}


def threshold_metrics(queries, jr, t):
    rec, sizes = [], []
    for q, (ids, scores) in zip(queries, jr):
        kept = {i for i, s in zip(ids, scores) if s >= t}
        rec.append(len(set(q["evidence"]) & kept) / len(q["evidence"]))
        sizes.append(len(kept))
    return {"recall": round(statistics.mean(rec), 4), "mean_kept": round(statistics.mean(sizes), 2)}


# ------------------------------------------------------------- B4 answerable
ANS_PROMPT = """A personal AI assistant retrieved these memories to answer its user's question.
Can the question be answered from these memories alone? Answer false if the needed information is missing.

MEMORIES:
{memories}

QUESTION (asked {now}): {question}"""
ANS_SCHEMA = {"type": "object", "properties": {"answerable": {"type": "boolean"}},
              "required": ["answerable"], "additionalProperties": False}


def bench_answerable():
    docs = corpus()
    by_id = {d["id"]: d for d in docs}
    life = load("life_stream")
    now = life["now"]
    rng = random.Random(7)
    items = []
    # Answerable: gold evidence plus top-ranked distractors (isolates the judgment from retrieval quality)
    for q in life["qa"]:
        if q["category"] == "deleted":
            continue
        ranked = [docs[i]["id"] for i, _ in bm25_rank(q["question"], [d["text"] for d in docs])]
        ctx = list(q["evidence"])
        for d in ranked:
            if len(ctx) >= 8:
                break
            if d not in ctx:
                ctx.append(d)
        rng.shuffle(ctx)
        items.append({"question": q["question"], "answerable": bool(q["evidence"]), "ctx": ctx})
    for question in load("components")["unanswerable_extra"]:
        ranked = [docs[i]["id"] for i, _ in bm25_rank(question, [d["text"] for d in docs])][:8]
        items.append({"question": question, "answerable": False, "ctx": ranked})
    gold = [it["answerable"] for it in items]
    report = {"n": len(items), "n_answerable": sum(gold), "methods": {}}

    def jev_one(it):
        mem = [by_id[i]["text"] for i in it["ctx"]]
        return jev.ask({"question": it["question"], "memories": mem},
                       {"answerable": jev.noul("Do the `memories` contain the information needed to answer `question`?")},
                       tag="bench:answerable:jev")["answerable"]["noul"]
    scores = pmap(jev_one, items, workers=16)
    report["methods"]["jev"] = {"auroc": auroc(scores, gold), **{f"at_{t}": binary([s >= t for s in scores], gold) for t in (0.3, 0.5, 0.7)},
                                **cost_latency("bench:answerable:jev", len(items)),
                                "unanswerable_scores": sorted(round(s, 2) for s, g in zip(scores, gold) if not g),
                                "answerable_scores_low": sorted(round(s, 2) for s, g in zip(scores, gold) if g)[:10]}
    for model in LLMS:
        tag = f"bench:answerable:{model}"
        out = pmap(lambda it: chat_json(model, ANS_PROMPT.format(
            memories="\n".join(by_id[i]["text"] for i in it["ctx"]), now=now, question=it["question"]),
            ANS_SCHEMA, schema_name="answerable", tag=tag, max_tokens=2000)[0]["answerable"], items)
        report["methods"][model] = {**binary(out, gold), **cost_latency(tag, len(items))}
    return report


def auroc(scores, gold):
    pos = [s for s, g in zip(scores, gold) if g]
    neg = [s for s, g in zip(scores, gold) if not g]
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return round(wins / (len(pos) * len(neg)), 4)


BENCHES = {"gate": bench_gate, "er": bench_er, "retrieval": bench_retrieval, "answerable": bench_answerable}

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("which", choices=[*BENCHES, "all"])
    args = ap.parse_args()
    for name in (BENCHES if args.which == "all" else [args.which]):
        report = BENCHES[name]()
        (OUT / f"bench_{name}.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))
        slim = {m: {k: v for k, v in r.items() if k not in ("errors", "calibration", "unanswerable_scores", "answerable_scores_low")}
                for m, r in report["methods"].items()}
        print(f"\n=== {name} ===")
        print(json.dumps({**{k: v for k, v in report.items() if k != "methods"}, "methods": slim}, indent=1))
