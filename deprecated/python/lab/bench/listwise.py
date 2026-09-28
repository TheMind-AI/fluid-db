"""Jev listwise retrieval: one Choice over all candidates instead of one Noul per pair.

Pointwise Nouls saturate when many near-duplicates look relevant ("X's phone number is ..."
for "what is my phone number?"). A single Choice sees every candidate at once, so it can
compare them. It is also one request per question instead of one per candidate.

  .venv/bin/python -m lab.bench.listwise
"""
from __future__ import annotations

import json
import statistics
import time

from lab.bench.components import OUT, corpus, rank_metrics, retrieval_queries
from lab.bench.scale import cosine_rank, distractors, embed
from lab.common import jev
from lab.common.llm import LEDGER
from lab.systems.engine import bm25_rank


def jev_choice_rank(question: str, cands: list[dict], tag: str) -> list[str]:
    options = {f"o{i}": c["text"][:400] for i, c in enumerate(cands)}
    options["none"] = "None of these memories helps answer the question"
    q = {"pick": jev.choice("Which memory contains the information needed to answer `question`?", options)}
    probs = jev.ask({"question": question}, q, tag=tag)["pick"]["probabilities"]
    order = sorted((k for k in probs if k != "none"), key=lambda k: -probs[k])
    return [cands[int(k[1:])]["id"] for k in order]


def run(size: int) -> dict:
    docs = corpus() + (distractors(size) if size else [])
    queries, _ = retrieval_queries()
    texts = [d["text"] for d in docs]
    tag = f"bench:listwise{size}"
    out = {"n_docs": len(docs)}
    ranked, short_recall = [], []
    start = time.time()
    if len(docs) <= 250:
        for q in queries:
            ranked.append(jev_choice_rank(q["question"], docs, tag))
        out["shortlist"] = "all memories"
    else:
        vecs = embed(texts + [q["question"] for q in queries])
        dv, qv = vecs[:len(docs)], vecs[len(docs):]
        for q, v in zip(queries, qv):
            emb = [docs[i]["id"] for i in cosine_rank(v, dv)[:100]]
            bm = [docs[i]["id"] for i, _ in bm25_rank(q["question"], texts)[:100]]
            ids = list(dict.fromkeys(emb + bm))[:250]
            by_id = {d["id"]: d for d in docs}
            short_recall.append(len(set(q["evidence"]) & set(ids)) / len(q["evidence"]))
            ranked.append(jev_choice_rank(q["question"], [by_id[i] for i in ids], tag))
        out["shortlist"] = "bm25 top-100 + embeddings top-100"
        out["shortlist_recall"] = round(statistics.mean(short_recall), 4)
    calls = [c for c in LEDGER.calls if c.tag == tag]
    out.update(rank_metrics(queries, ranked))
    out["jev_cost_per_1k_queries"] = round(sum(c.cost for c in calls) / len(queries) * 1000, 3)
    out["jev_latency_mean"] = round(statistics.mean(c.latency for c in calls), 2)
    out["input_tokens_per_query"] = int(statistics.mean(c.input_tokens for c in calls))
    return out


if __name__ == "__main__":
    results = {size: run(size) for size in (0, 1000, 5000)}
    for k, v in results.items():
        print(k, json.dumps(v))
    (OUT / "bench_listwise.json").write_text(json.dumps(results, indent=1))
