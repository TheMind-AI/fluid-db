"""B5: retrieval when the memory store grows.

The 108 real memories are mixed with N synthetic distractors (other people,
past expenses, meetings, workouts... dated 2024-2025, never Aug/Sep, and never
reusing names or places from the real data). Gold evidence stays the same.

  .venv/bin/python -m lab.bench.scale --sizes 1000 5000
"""
# @ref LLP 0002.000 — a round-1 bench
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import time

from lab.bench.components import OUT, corpus, jev_rank, pmap, rank_metrics, retrieval_queries, LIST_PROMPT, LIST_SCHEMA
from lab.common.llm import LEDGER, chat_json
from lab.systems.engine import bm25_rank

NAMES = ["Karel", "Lucie", "Petr", "Jakub", "Anna", "Martina", "Olivia", "Liam", "Noah", "Emma", "Mia", "Lucas", "Sofia",
         "Mateo", "Chloe", "Ethan", "Ava", "Oliver", "Isla", "Leo", "Zoe", "Hugo", "Nora", "Felix", "Ines", "Oscar", "Lena",
         "Viktor", "Alma", "Rafael", "Greta", "Milan", "Ivana", "Pavel", "Simona", "Ondřej", "Tereza", "Filip", "Klára",
         "Ruben", "Yuki", "Kenji", "Amara", "Kofi", "Dmitri", "Sven", "Astrid", "Mateus", "Lior", "Farah"]
SURNAMES = ["Svoboda", "Dvořáková", "Černý", "Procházka", "Kučera", "Wright", "Lopez", "Nguyen", "Schmidt", "Rossi",
            "Tanaka", "Okafor", "Ivanov", "Larsen", "Costa", "Haddad", "Moreau", "Novotná", "Berg", "Fischer"]
COMPANIES = ["Acme Robotics", "Globex", "Initech", "Hooli", "Vandelay Imports", "Soylent Labs", "Cyberdyne", "Tyrell Bio",
             "Monarch Freight", "Blue Harbor Capital", "Quartz Health", "Orbit Payments", "Kite Mobility", "Fable Foods"]
RESTAURANTS = ["Pho Vietnam", "Mama Shelter", "Café Louvre", "Zlatý Klas", "Ichiban Ramen", "La Taqueria", "Nopa",
               "Zuni Café", "Burma Love", "Kitchen Story", "Bistro Monk", "U Fleků", "Maso a Kobliha", "Eska"]
GYMS = ["Boulder Bar", "Lezecké centrum Holešovice", "Dogpatch Boulders", "Planet Granite", "HangAr"]
BOOKS = ["Dune", "Sapiens", "The Lean Startup", "Atomic Habits", "Project Hail Mary", "Good Strategy Bad Strategy", "Zero to One",
         "Deep Work", "Snow Crash", "The Three-Body Problem", "Shoe Dog", "Educated", "The Hobbit", "Range"]
CITIES = ["Berlin", "Vienna", "Lisbon", "London", "Barcelona", "Tokyo", "New York", "Boston", "Seattle", "Brno", "Munich"]
STORES = ["Billa", "Lidl", "Tesco", "Trader Joe's", "Whole Foods", "Rossmann"]
AIRLINES = ["Lufthansa", "KLM", "Delta", "Air France", "Czech Airlines", "Ryanair"]
MONTHS = [1, 2, 3, 4, 5, 6, 7, 10, 11, 12]


def distractors(n: int, seed: int = 11) -> list[dict]:
    rng = random.Random(seed)

    def person():
        return f"{rng.choice(NAMES)} {rng.choice(SURNAMES)}"

    def date():
        return f"{rng.choice([2024, 2025])}-{rng.choice(MONTHS):02d}-{rng.randint(1, 28):02d}"

    templates = [
        lambda: f"{person()}'s phone number is +{rng.choice([420, 1, 49, 44])} {rng.randint(100, 999)} {rng.randint(100, 999)} {rng.randint(100, 999)}",
        lambda: f"Lunch at {rng.choice(RESTAURANTS)}, {rng.randint(150, 900)} CZK",
        lambda: f"Dinner with {person()} at {rng.choice(RESTAURANTS)}, ${rng.randint(30, 180)}.{rng.randint(0, 99):02d}",
        lambda: f"Meeting with {person()} from {rng.choice(COMPANIES)} at {rng.randint(8, 18)}:00 in {rng.choice(CITIES)}",
        lambda: f"{person()}'s birthday is {rng.choice(['January', 'March', 'May', 'June', 'November', 'December'])} {rng.randint(1, 28)}",
        lambda: f"Ran {rng.choice([5, 10, 21])}k in {rng.randint(22, 110)}:{rng.randint(0, 59):02d}",
        lambda: f"Bouldering at {rng.choice(GYMS)} with {rng.choice(NAMES)}, sent a {rng.choice(['6a', '6b', '6b+', '7a'])}",
        lambda: f"Finished reading {rng.choice(BOOKS)}, {rng.randint(2, 5)}/5",
        lambda: f"{person()} works as {rng.choice(['a designer', 'a CFO', 'an engineer', 'a lawyer', 'a nurse'])} at {rng.choice(COMPANIES)}",
        lambda: f"Booked flight {rng.choice(CITIES)} → {rng.choice(CITIES)}, {rng.choice(AIRLINES)}, {rng.randint(90, 900)} EUR",
        lambda: f"Groceries at {rng.choice(STORES)}, {rng.randint(300, 2500)} CZK",
        lambda: f"{person()} is allergic to {rng.choice(['shellfish', 'gluten', 'cats', 'pollen', 'lactose'])}",
        lambda: f"Email from {rng.choice(NAMES).lower()}@{rng.choice(COMPANIES).split()[0].lower()}.com: {rng.choice(['Invoice', 'Quarterly update', 'Offsite plan', 'Contract draft', 'Intro'])}",
        lambda: f"{rng.choice(NAMES)} recommended the book {rng.choice(BOOKS)}",
        lambda: f"Paid {rng.choice(['internet', 'electricity', 'phone', 'insurance'])} bill, {rng.randint(400, 3000)} CZK",
    ]
    return [{"id": f"x{i:05d}", "text": f"[{date()}] {rng.choice(templates)()}"} for i in range(n)]


def embed(texts: list[str]) -> list[list[float]]:
    import openai
    client = openai.OpenAI()
    out = []
    for i in range(0, len(texts), 1000):
        out.extend(e.embedding for e in client.embeddings.create(model="text-embedding-3-small", input=texts[i:i + 1000]).data)
    return out


def cosine_rank(qv, dvs):
    qn = math.sqrt(sum(x * x for x in qv))
    scores = [sum(a * b for a, b in zip(qv, d)) / (qn * math.sqrt(sum(b * b for b in d))) for d in dvs]
    return sorted(range(len(dvs)), key=lambda i: -scores[i])


def run(size: int) -> dict:
    docs = corpus() + distractors(size)
    queries, now = retrieval_queries()
    texts = [d["text"] for d in docs]
    vecs = embed(texts + [q["question"] for q in queries])
    dv, qv = vecs[:len(docs)], vecs[len(docs):]
    report = {"n_docs": len(docs), "n_queries": len(queries), "methods": {}}

    bm = [[docs[i]["id"] for i, _ in bm25_rank(q["question"], texts)] for q in queries]
    report["methods"]["bm25"] = rank_metrics(queries, bm)
    emb_idx = [cosine_rank(v, dv) for v in qv]
    emb = [[docs[i]["id"] for i in idx] for idx in emb_idx]
    report["methods"]["embeddings"] = rank_metrics(queries, emb)

    # Shortlist (embeddings top-50) -> Jev re-rank
    tag = f"bench:scale{size}:jev"
    start = time.time()
    reranked = []
    for q, idx in zip(queries, emb_idx):
        short = [docs[i] for i in idx[:50]]
        ids, _ = jev_rank(q["question"], short, tag)
        reranked.append(ids + [docs[i]["id"] for i in idx[50:]])
    jev_calls = [c for c in LEDGER.calls if c.tag == tag]
    report["methods"]["embeddings50+jev"] = {**rank_metrics(queries, reranked),
                                             "shortlist_recall@50": rank_recall(queries, emb, 50),
                                             "jev_cost_per_1k_queries": round(sum(c.cost for c in jev_calls) / len(queries) * 1000, 3),
                                             "wall_per_query": round((time.time() - start) / len(queries), 2)}

    # Wider shortlist: BM25 top-100 union embeddings top-100 -> Jev re-rank
    tag = f"bench:scale{size}:jev-union"
    start = time.time()
    by_id = {d["id"]: d for d in docs}
    union_ranked, union_recall = [], []
    for q, b, e in zip(queries, bm, emb):
        short_ids = list(dict.fromkeys(e[:100] + b[:100]))
        union_recall.append(len(set(q["evidence"]) & set(short_ids)) / len(q["evidence"]))
        ids, _ = jev_rank(q["question"], [by_id[i] for i in short_ids], tag)
        union_ranked.append(ids)
    jev_calls = [c for c in LEDGER.calls if c.tag == tag]
    report["methods"]["bm25_100+emb_100+jev"] = {**rank_metrics(queries, union_ranked),
                                                 "shortlist_recall": round(statistics.mean(union_recall), 4),
                                                 "mean_shortlist": round(statistics.mean(len(r) for r in union_ranked), 1),
                                                 "jev_cost_per_1k_queries": round(sum(c.cost for c in jev_calls) / len(queries) * 1000, 3),
                                                 "wall_per_query": round((time.time() - start) / len(queries), 2)}

    # One LLM reads every memory (only feasible for small models at this size)
    memories = "\n".join(f"{d['id']}: {d['text']}" for d in docs)
    tag = f"bench:scale{size}:luna"
    out = pmap(lambda q: chat_json("openai:gpt-6-luna", LIST_PROMPT.format(memories=memories, now=now, question=q["question"]),
                                   LIST_SCHEMA, schema_name="ids", tag=tag, max_tokens=4000)[0]["ids"], queries, workers=6)
    calls = [c for c in LEDGER.calls if c.tag == tag]
    report["methods"]["gpt-6-luna reads all"] = {**rank_metrics(queries, out),
                                                  "cost_per_1k_queries": round(sum(c.cost for c in calls) / len(queries) * 1000, 3),
                                                  "latency_mean": round(statistics.mean(c.latency for c in calls), 2),
                                                  "input_tokens_per_query": int(statistics.mean(c.input_tokens for c in calls))}
    return report


def rank_recall(queries, rankings, k):
    return round(statistics.mean(len(set(q["evidence"]) & set(r[:k])) / len(q["evidence"]) for q, r in zip(queries, rankings)), 4)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", type=int, nargs="+", default=[1000, 5000])
    args = ap.parse_args()
    results = {}
    for size in args.sizes:
        results[size] = run(size)
        print(json.dumps({size: results[size]}, indent=1))
    (OUT / "bench_scale.json").write_text(json.dumps(results, indent=1))
