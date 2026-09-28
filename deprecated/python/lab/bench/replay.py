"""LLP 0020 Part A: recall at every turn of a replayed conversation, scored without a judge. Private data, OpenAI only.

For every re-told statement F of LLP 0018.000, three moments ask the memory built before the cut: the cue question,
the turn where the person says F (reactive), and the moment before it (proactive: the assistant's question that
leads to F and the person's previous message). A hit is the earlier mention of F among the top k: one of F's verified
earlier windows, or for statements, a statement from such a window about the same thing (cosine with F >= 0.5).

  .venv/bin/python -m lab.bench.replay run u5 u1 ...       per account; saves private hits, prints counts
  .venv/bin/python -m lab.bench.replay summary u5 u1 ...   pooled hit@k per creation x search x moment, the RFC's pairs
  .venv/bin/python -m lab.bench.replay route u5 ...          Part D: the router and the picker (Jev, or the OpenAI shim)
  .venv/bin/python -m lab.bench.replay route_summary shim u5 ...   (own* accounts may run with real Jev: mode jev)
  .venv/bin/python -m lab.bench.replay pick u5 ...           LLP 0021 Part A: four pickers over meaning's top 20
  .venv/bin/python -m lab.bench.replay pick_summary shim u5 ...
  .venv/bin/python -m lab.bench.replay live u5 ...           LLP 0021 Part C: the assistant writes, with 4 memories
  .venv/bin/python -m lab.bench.replay live_summary u5 ...   (needs LAB_PRODUCT_MEMORY and LAB_PRODUCT_CUTOFF per account)
"""
# @ref LLP 0020#part-a-recall-at-every-turn-scored-without-a-judge — provenance, not a model, decides what counts
from __future__ import annotations

import json
import os
import random
import re
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# @ref LLP 0018#processors — other people's accounts (u<number>) are OpenAI-only; the maintainer's own (own*) may use Jev
_accounts = [x for x in sys.argv[2:] if re.match(r"(u\d+|own)", x)]
if not os.environ.get("LAB_PRIVATE_DIR") or (os.environ.get("LAB_PROCESSORS") != "openai"
                                             and not (_accounts and all(a.startswith("own") for a in _accounts))):
    raise SystemExit("other users' data: set LAB_PROCESSORS=openai and LAB_PRIVATE_DIR")

import numpy as np

from lab.bench.scenarios import context
from lab.common.llm import LEDGER, chat_json, embed
from lab.memory.core import Evidence
from lab.memory.stores import (EpisodeStore, Index, LogStore, MergedStatementStore, SemanticIndex, StatementStore,
                               day_of, fuse, rerank, when)

PRIVATE = Path(os.environ["LAB_PRIVATE_DIR"])
MODEL = "openai:gpt-6-luna"
MOMENTS = ["cue", "reactive", "proactive"]
REWRITE = """Write 3 short search queries to find, in a person's past conversations with their therapy assistant, \
what is relevant to this moment or question: a close paraphrase, one naming the likely specific topic, and {third}.

MOMENT OR QUESTION: {q}"""
REWRITE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["queries"], "properties": {
    "queries": {"type": "array", "items": {"type": "string"}}}}
CZ = re.compile(r"[ěščřžýáíéůúňťď]", re.I)


def spent() -> float:
    return sum(c.cost for c in LEDGER.calls if not c.cached)


def facts(a: str) -> list[dict]:
    """The lasting re-told statements of an account, with the log ids of their verified earlier windows."""
    out = []
    for r in json.loads((PRIVATE / f"return_gold_{a}.json").read_text()):
        for i, st in enumerate(r["statements"]):
            if st["class"] == "retold" and st.get("lasting", 0) >= 0.2:
                out.append({"chat": r["id"], "i": i, "s": st["s"], "logs": {int(w[1:]) for w in st["windows"]}})
    return out


def moments(a: str, fs: list[dict]) -> None:
    """Adds each fact's three moments: the cue question, its turn (by similarity), and the moment before it."""
    chats = {c["id"]: c for c in json.loads((PRIVATE / f"return_chats_{a}.json").read_text())}
    cue = {q["id"]: q["question"] for q in json.loads((PRIVATE / f"scenario_{a}.json").read_text())["qa"]}
    for cid in {f["chat"] for f in fs}:
        turns = chats[cid]["turns"]
        tv = np.stack(embed([f"{t['asked']} {t['text']}" for t in turns], tag="replay:turns"))
        mine = [f for f in fs if f["chat"] == cid]
        fv = np.stack(embed([f["s"] for f in mine], tag="replay:facts"))
        for f, v in zip(mine, fv):
            t = int(np.argmax(tv @ v))
            prev = turns[t - 1]["text"] if t > 0 else chats[cid]["opening"]
            f.update(vec=v, turn=t, cue=cue[f"c-{cid}-{f['i']}"], reactive=f"{turns[t]['asked']} {turns[t]['text']}"[:3000],
                     proactive=f"{turns[t]['asked']} {prev}"[:3000])


class Creation:
    """Items to search (text, provenance log ids), with their word index, meaning index and item vectors."""

    def __init__(self, texts: list[str], src: list[set], vecs=None):
        self.texts, self.src = texts, src
        self.words = Index(texts)
        self.meaning = SemanticIndex(texts)
        self.vecs = self.meaning.m if vecs is None else vecs


def creations(a: str, which: tuple = ("W", "WC", "S", "SM")) -> tuple[dict, str]:
    ctx = context(f"{a}@v25nog")
    log = LogStore()
    log.build(ctx)
    ep = EpisodeStore()
    ep.build(ctx)
    gist = {d: e.get("gist", "") for d, e in ep.episodes.items()}
    ids = [i for i, _, _ in ctx.log]
    header = [f"Conversation on {day_of(ts)}, about: {gist.get(day_of(ts), '')}\n" for _, ts, _ in ctx.log]
    czech = sum(1 for _, _, t in ctx.log if len(CZ.findall(t)) / max(1, len(t)) > 0.01) / max(1, len(ctx.log))
    out = {"W": Creation(log.texts, [{i} for i in ids]),
           "WC": Creation([h + t for h, t in zip(header, log.texts)], [{i} for i in ids])}
    if "S" in which:
        st = StatementStore()
        st.build(ctx)
        out["S"] = Creation([x["text"] for x in st.items], [set(x["src"]) for x in st.items])
    if "SM" in which:
        sm = MergedStatementStore()
        sm.build(ctx)
        out["SM"] = Creation([x["text"] for x in sm.items], [set(x["src"]) for x in sm.items])
    return out, ("a translation into Czech" if czech >= 0.3 else "one more paraphrase")


def search(c: Creation, how: str, q: str, k: int, third: str) -> list[int]:
    if how == "words":
        return c.words.top(q, k)
    if how == "meaning":
        return c.meaning.top(q, k)
    if how == "hybrid":
        return fuse([c.words.top(q, 30), c.meaning.top(q, 30)], k)
    if how == "rerank":
        cand = c.meaning.top(q, 20)
        return [cand[n] for n in rerank(q, [c.texts[i] for i in cand], tag="replay:rerank")][:k]
    if how == "rewrite":
        out, _ = chat_json(MODEL, REWRITE.format(q=q, third=third), REWRITE_SCHEMA, schema_name="queries",
                           tag="replay:rewrite", effort="low")
        return fuse([c.meaning.top(x, 30) for x in [q] + out["queries"][:3]], k)
    raise ValueError(how)


PLAN = [("W", "words"), ("W", "meaning"), ("W", "hybrid"), ("W", "rerank"), ("W", "rewrite"), ("WC", "meaning"),
        ("S", "words"), ("S", "meaning"), ("S", "rerank"), ("S", "rewrite"), ("SM", "meaning"), ("SM", "rerank")]
KS = {"W": (5, 10), "WC": (5, 10), "S": (10, 25), "SM": (10, 25)}


def run(a: str):
    fs = facts(a)
    moments(a, fs)
    cs, third = creations(a)

    def one(job):
        f, (cname, how), moment = job
        c = cs[cname]
        k_small, k_big = KS[cname]
        top = search(c, how, f[moment], k_big, third)

        def hit(k):
            for i in top[:k]:
                if c.src[i] & f["logs"] and (cname in ("W", "WC") or float(c.vecs[i] @ f["vec"]) >= 0.5):
                    return 1
            return 0
        return {"account": a, "chat": f["chat"], "i": f["i"], "creation": cname, "search": how, "moment": moment,
                f"hit@{k_small}": hit(k_small), f"hit@{k_big}": hit(k_big), "n": len(c.texts),
                "m": sum(1 for x in c.src if x & f["logs"]) if cname in ("W", "WC") else None,
                "chars@small": sum(len(c.texts[i]) for i in top[:k_small]), "chars@big": sum(len(c.texts[i]) for i in top[:k_big])}

    jobs = [(f, p, m) for f in fs for p in PLAN for m in MOMENTS]
    with ThreadPoolExecutor(8) as pool:
        rows = list(pool.map(one, jobs))
    (PRIVATE / "results" / f"replay_{a}.json").write_text(json.dumps(rows))
    turn_share = sum(1 for f in fs if f["turn"] > 0) / max(1, len(fs))
    print(json.dumps({"account": a, "facts": len(fs), "rows": len(rows), "facts_not_on_first_turn": round(turn_share, 2),
                      "spent": round(spent(), 4)}))


def summary(names: list[str]):
    rows = [r for a in names for r in json.loads((PRIVATE / "results" / f"replay_{a}.json").read_text())]
    table = {}
    for cname, how in PLAN:
        for m in MOMENTS:
            rs = [r for r in rows if r["creation"] == cname and r["search"] == how and r["moment"] == m]
            ks = KS[cname]
            table[f"{cname}/{how}/{m}"] = {f"hit@{k}": round(100 * sum(r[f"hit@{k}"] for r in rs) / max(1, len(rs)), 1)
                                           for k in ks} | {"n": len(rs), "chars@small": round(sum(r["chars@small"] for r in rs) / max(1, len(rs))),
                                                           "chars@big": round(sum(r["chars@big"] for r in rs) / max(1, len(rs)))}
    key = lambda r: (r["account"], r["chat"], r["i"], r["moment"])

    def paired(a: tuple, b: tuple, ka: str, kb: str, only: list[str] = MOMENTS, n=10000, seed=1):
        A = {key(r): r[ka] for r in rows if (r["creation"], r["search"]) == a and r["moment"] in only}
        B = {key(r): r[kb] for r in rows if (r["creation"], r["search"]) == b and r["moment"] in only}
        d = [A[x] - B[x] for x in A if x in B]
        rng = random.Random(seed)
        bs = sorted(sum(rng.choice(d) for _ in d) / len(d) for _ in range(n))
        return [round(100 * sum(d) / len(d), 1), round(100 * bs[int(0.025 * n)], 1), round(100 * bs[int(0.975 * n)], 1), len(d)]

    tests = {
        "R1 cue: W meaning@10 - W words@10": paired(("W", "meaning"), ("W", "words"), "hit@10", "hit@10", ["cue"]),
        "R1 proactive: W meaning@10 - W words@10": paired(("W", "meaning"), ("W", "words"), "hit@10", "hit@10", ["proactive"]),
        "R1 reactive: W meaning@10 - W words@10": paired(("W", "meaning"), ("W", "words"), "hit@10", "hit@10", ["reactive"]),
        "R2: W rerank@5 - W meaning@5": paired(("W", "rerank"), ("W", "meaning"), "hit@5", "hit@5"),
        "R3: W rewrite@10 - W meaning@10": paired(("W", "rewrite"), ("W", "meaning"), "hit@10", "hit@10"),
        "R4: WC meaning@10 - W meaning@10": paired(("WC", "meaning"), ("W", "meaning"), "hit@10", "hit@10"),
        "R6: S meaning@25 - W meaning@5": paired(("S", "meaning"), ("W", "meaning"), "hit@25", "hit@5"),
        "S rerank@10 - S meaning@10": paired(("S", "rerank"), ("S", "meaning"), "hit@10", "hit@10"),
        "SM meaning@25 - S meaning@25": paired(("SM", "meaning"), ("S", "meaning"), "hit@25", "hit@25"),
        "W hybrid@10 - W meaning@10": paired(("W", "hybrid"), ("W", "meaning"), "hit@10", "hit@10"),
    }
    from math import comb
    wrows = [r for r in rows if r["creation"] == "W" and r["search"] == "words" and r["moment"] == "cue"]
    chance = {f"hit@{k}": round(100 * sum(1 - comb(r["n"] - r["m"], k) / comb(r["n"], k) for r in wrows) / max(1, len(wrows)), 1)
              for k in (5, 10)}
    r5 = {m: table[f"W/meaning/{m}"]["hit@10"] for m in MOMENTS}
    out = {"facts": len({(r["account"], r["chat"], r["i"]) for r in rows}), "windows_by_chance": chance,
           "table": table, "tests": tests,
           "R5 proactive/reactive (W meaning@10)": round(r5["proactive"] / max(1e-9, r5["reactive"]), 2)}
    (PRIVATE / "results" / "replay_summary.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


# ------------------------------------------------------------------------------------------ Part D: Jev picks
# @ref LLP 0020#part-d-jev-picks-the-search — pick the search from the moment, or pick the evidence from the pool
MENU = {"words": "finds past messages that share exact words with the moment: names, places, numbers, rare terms",
        "meaning": "finds past messages with the same meaning, in any wording or language",
        "hybrid": "exact words and meaning combined",
        "rewrite": "rewrites the moment into several queries (paraphrases, the likely topic, a translation) and "
                   "searches by meaning",
        "headers": "searches by meaning with each message's date and that day's topic attached: good for when "
                   "something happened"}
FIRST = {"words": ("W", "words"), "meaning": ("W", "meaning"), "hybrid": ("W", "hybrid"), "rewrite": ("W", "rewrite"),
         "headers": ("WC", "meaning")}


def route(a: str):
    from lab.common import jev
    mode = "shim" if os.environ.get("LAB_PROCESSORS") == "openai" else "jev"
    fs = facts(a)
    moments(a, fs)
    cs, third = creations(a, which=("W", "WC"))
    ids = [next(iter(x)) for x in cs["W"].src]

    def one(job):
        f, moment = job
        q = f[moment]
        tops = {m: [ids[i] for i in search(cs[c], how, q, 5, third)] for m, (c, how) in FIRST.items()}
        pick = jev.ask({"moment": q, "searches": MENU}, {"pick": jev.choice(
            "Which search is most likely to find, in the person's past conversations, what is useful to recall at "
            "`moment`?", MENU)}, tag="replay:route")["pick"]
        two = sorted(pick["probabilities"], key=lambda m: -pick["probabilities"][m])[:2]
        pooled = list(dict.fromkeys(i for m in MENU for i in tops[m]))
        text = {i: t for i, t in zip(ids, cs["W"].texts)}
        picked = [pooled[n] for n in rerank(q, [text[i] for i in pooled], tag="replay:pool")][:5]
        hit = lambda got: int(bool(set(got) & f["logs"]))
        row = {"account": a, "chat": f["chat"], "i": f["i"], "moment": moment, "mode": mode, "choice": pick["choice"],
               "router": hit(tops[pick["choice"]]), "router2": hit(fuse([tops[m] for m in two], 5)),
               "picker": hit(picked), "pool_size": len(pooled), "pool_any": hit(pooled)}
        row.update({f"single_{m}": hit(tops[m]) for m in MENU})
        return row

    with ThreadPoolExecutor(8) as pool:
        rows = list(pool.map(one, [(f, m) for f in fs for m in MOMENTS]))
    lat = {t: [c.latency for c in LEDGER.calls if c.tag.startswith(t)] for t in ("replay:route", "replay:pool")}
    out = {"rows": rows, "latency_s": {t: round(sum(v) / len(v), 3) if v else None for t, v in lat.items()}}
    (PRIVATE / "results" / f"route_{a}_{mode}.json").write_text(json.dumps(out))
    print(json.dumps({"account": a, "mode": mode, "queries": len(rows), "latency_s": out["latency_s"],
                      "spent": round(spent(), 4)}))


def route_summary(names: list[str], mode: str):
    data = [json.loads((PRIVATE / "results" / f"route_{a}_{mode}.json").read_text()) for a in names]
    rows = [r for d in data for r in d["rows"]]
    strategies = [f"single_{m}" for m in MENU] + ["router", "router2", "picker", "pool_any"]
    table = {m: {st: round(100 * sum(r[st] for r in rows if r["moment"] == m) / max(1, sum(1 for r in rows if r["moment"] == m)), 1)
                 for st in strategies} for m in MOMENTS + ["all"] if m != "all"}
    table["all"] = {st: round(100 * sum(r[st] for r in rows) / len(rows), 1) for st in strategies}
    oracle = round(100 * sum(max(r[f"single_{m}"] for m in MENU) for r in rows) / len(rows), 1)
    choices = {m: sum(1 for r in rows if r["choice"] == m) for m in MENU}

    def paired(a, b, n=10000, seed=1):
        d = [r[a] - r[b] for r in rows]
        rng = random.Random(seed)
        bs = sorted(sum(rng.choice(d) for _ in d) / len(d) for _ in range(n))
        return [round(100 * sum(d) / len(d), 1), round(100 * bs[int(0.025 * n)], 1), round(100 * bs[int(0.975 * n)], 1), len(d)]

    best_single = max((f"single_{m}" for m in MENU), key=lambda st: table["all"][st])
    out = {"mode": mode, "queries": len(rows), "table": table, "oracle_single": oracle, "router_choices": choices,
           "best_single": best_single, "tests": {
               "D2 router - best single": paired("router", best_single),
               "router2 - best single": paired("router2", best_single),
               "D3 picker - router": paired("picker", "router"),
               "picker - best single": paired("picker", best_single)},
           "latency_s": {t: round(sum(d["latency_s"][t] or 0 for d in data) / len(data), 3) for t in ("replay:route", "replay:pool")}}
    (PRIVATE / "results" / f"route_summary_{mode}_{'own' if names[0].startswith('own') else 'users'}.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


# ------------------------------------------------------------------------------ LLP 0021 Part A: a better picker
LISTWISE = """Below are 20 excerpts from a person's past conversations with their therapy assistant, and a moment in \
their current conversation (or a question about them). Rank the excerpts by how useful each is to recall at this \
moment, and return the numbers of the 5 most useful, the most useful first.

MOMENT OR QUESTION: {q}

EXCERPTS:
{items}"""
LISTWISE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["top"], "properties": {
    "top": {"type": "array", "items": {"type": "integer"}}}}


# @ref LLP 0021#part-a-a-better-picker — does the picker fail because it can't see the fact? whole windows, cards, a list
def pick(a: str):
    mode = "shim" if os.environ.get("LAB_PROCESSORS") == "openai" else "jev"
    fs = facts(a)
    moments(a, fs)
    cs, _ = creations(a, which=("W", "S"))
    W, S = cs["W"], cs["S"]
    ids = [next(iter(x)) for x in W.src]
    by_window = {}
    for text, src in zip(S.texts, S.src):
        for lid in src:
            by_window.setdefault(lid, []).append(text)
    date = {i: t[1:11] for i, t in zip(ids, W.texts)}
    card = lambda i: (f"Conversation on {date[i]}. What it says about the person: " + "; ".join(by_window.get(i, [])[:25])
                      + f" | It begins: {W.texts[ids.index(i)][:300]}")[:3000]

    def one(job):
        f, moment = job
        q = f[moment]
        pool = W.meaning.top(q, 20)
        texts = [W.texts[i] for i in pool]
        top = lambda order: [ids[pool[n]] for n in order[:5]]
        p0 = top(rerank(q, texts, limit=800, tag="replay:p0"))
        p1 = top(rerank(q, texts, limit=3000, tag="replay:p1"))
        p2 = top(rerank(q, [card(ids[i]) for i in pool], limit=3000, tag="replay:p2"))
        out, _ = chat_json(MODEL, LISTWISE.format(q=q, items="\n\n".join(f"{n + 1}. {t[:3000]}" for n, t in enumerate(texts))),
                           LISTWISE_SCHEMA, schema_name="top", tag="replay:p3", effort="low")
        order = list(dict.fromkeys(n - 1 for n in out["top"] if 1 <= n <= len(pool)))
        p3 = top(order + [n for n in range(len(pool)) if n not in order])
        hit = lambda got: int(bool(set(got) & f["logs"]))
        return {"account": a, "chat": f["chat"], "i": f["i"], "moment": moment, "mode": mode, "p0": hit(p0),
                "p1": hit(p1), "p2": hit(p2), "p3": hit(p3), "ceiling": hit([ids[i] for i in pool]),
                "meaning5": hit([ids[i] for i in pool[:5]])}

    with ThreadPoolExecutor(8) as pool_:
        rows = list(pool_.map(one, [(f, m) for f in fs for m in MOMENTS]))
    lat = {t: [c.latency for c in LEDGER.calls if c.tag.startswith(t)] for t in ("replay:p0", "replay:p1", "replay:p2", "replay:p3")}
    out = {"rows": rows, "latency_s": {t: round(sum(v) / len(v), 3) if v else None for t, v in lat.items()}}
    (PRIVATE / "results" / f"pick_{a}_{mode}.json").write_text(json.dumps(out))
    print(json.dumps({"account": a, "mode": mode, "queries": len(rows), "latency_s": out["latency_s"], "spent": round(spent(), 4)}))


def pick_summary(names: list[str], mode: str):
    data = [json.loads((PRIVATE / "results" / f"pick_{a}_{mode}.json").read_text()) for a in names]
    rows = [r for d in data for r in d["rows"]]
    cols = ["meaning5", "p0", "p1", "p2", "p3", "ceiling"]
    table = {m: {c: round(100 * sum(r[c] for r in rows if r["moment"] == m) / max(1, sum(1 for r in rows if r["moment"] == m)), 1)
                 for c in cols} for m in MOMENTS}
    table["all"] = {c: round(100 * sum(r[c] for r in rows) / len(rows), 1) for c in cols}

    def paired(a, b, n=10000, seed=1):
        d = [r[a] - r[b] for r in rows]
        rng = random.Random(seed)
        bs = sorted(sum(rng.choice(d) for _ in d) / len(d) for _ in range(n))
        return [round(100 * sum(d) / len(d), 1), round(100 * bs[int(0.025 * n)], 1), round(100 * bs[int(0.975 * n)], 1), len(d)]

    best = max(("p0", "p1", "p2"), key=lambda c: table["all"][c])
    out = {"mode": mode, "queries": len(rows), "table": table, "best_closed_question_picker": best,
           "tests": {"A1 p1 - p0": paired("p1", "p0"), "A2 p2 - p0": paired("p2", "p0"),
                     f"A4 {best} - p3": paired(best, "p3"), "p0 - meaning5": paired("p0", "meaning5")},
           "latency_s": {t: round(sum(d["latency_s"][t] or 0 for d in data) / len(data), 3)
                         for t in ("replay:p0", "replay:p1", "replay:p2", "replay:p3")}}
    (PRIVATE / "results" / f"pick_summary_{mode}_{'own' if names[0].startswith('own') else 'users'}.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


# ------------------------------------------------------------------------------ LLP 0021 Part C: a live replay
MIND = """You are Mind, a warm and concise therapy assistant, continuing a conversation with a person. What you \
remember about them from earlier conversations is below; it may be empty. Use it when it helps them: don't make them \
repeat themselves, and connect to what they told you before. Never claim to remember anything that isn't in your \
memory or in this conversation.

WHAT YOU REMEMBER:
{memory}

THE CONVERSATION SO FAR (Mind's turns are shortened to their last question):
{transcript}

Write Mind's next message, in 2-4 sentences."""
# The first judges counted echoes of the current conversation as memory (checked by hand on the maintainer's data,
# LLP 0021.000); these see the conversation, and count only what could not come from it.
MIND_ACTIVE = """You are Mind, a warm and concise therapy assistant, continuing a conversation with a person. What \
you remember about them from earlier conversations is below; it may be empty.

Before you reply, check your memory for anything the person told you before that relates to this moment. If there \
is, say so explicitly and specifically ("you mentioned in August that ..."), so they don't have to repeat themselves, \
and connect it to what they are saying now. Never claim to remember anything that isn't in your memory or in this \
conversation.

WHAT YOU REMEMBER:
{memory}

THE CONVERSATION SO FAR (Mind's turns are shortened to their last question):
{transcript}

Write Mind's next message, in 2-4 sentences."""
KNOWS = """A person told their therapy assistant this in earlier conversations: «{fact}»

Below are the current conversation so far and the assistant's next message, written before the person brought that \
up again. Does the message show that the assistant knows it from an earlier conversation: it states it, asks about it \
as already known, or refers to it, in a way the current conversation alone doesn't explain? Answer false if the \
conversation so far already mentions it.

THE CONVERSATION SO FAR:
{transcript}

MESSAGE: {message}"""
REMEMBERS = """Earlier, a person had told their therapy assistant: «{fact}»
In a new conversation, the person has just said it again. Below are that conversation so far and the assistant's reply.

Does the reply show that the assistant remembers this from an earlier conversation: it says so ("you mentioned", \
"last time", "as before"), or it adds a detail about it that is in the earlier conversations but not in this one? \
Replying to what the person just said, in any words, is not enough.

THE CONVERSATION SO FAR:
{transcript}

REPLY: {message}"""
INVENTS = """Below is what a therapy assistant remembered about a person, the conversation so far, and the \
assistant's next message. Does the message claim to remember something about the person's past (before this \
conversation) that neither the memory nor the conversation supports?

WHAT IT REMEMBERED:
{memory}

THE CONVERSATION SO FAR:
{transcript}

MESSAGE: {message}"""
YES = lambda key: {"type": "object", "additionalProperties": False, "required": [key], "properties": {key: {"type": "boolean"}}}
ARMS = ["none", "product", "conv_best2", "full"]


# @ref LLP 0021#part-c-a-live-replay — does the conversation change? the assistant writes, the memory is injected
def live(a: str, cap: int = 30, arms: list = ARMS, prompt: str = "MIND", out_name: str = "live"):
    import zlib
    from lab.common.llm import chat
    from lab.memory.answerers import assemble
    from lab.memory.stores import IncrementalDossierStore, LinkedStatementsListPicked, LogListPicked, ProductInjectedStore
    fs = facts(a)
    if len(fs) > cap:
        fs = sorted(random.Random(7).sample(fs, cap), key=lambda f: (f["chat"], f["i"]))
    moments(a, fs)
    chats = {c["id"]: c for c in json.loads((PRIVATE / f"return_chats_{a}.json").read_text())}
    ctx = context(f"{a}@v25nog")
    product = ProductInjectedStore()
    product.build(ctx)
    product_text = "\n".join(e.text for e in product.read("", ctx)) or "(nothing)"
    dossier = IncrementalDossierStore()
    dossier.build(ctx)
    st, lg = LinkedStatementsListPicked(), LogListPicked()
    st.build(ctx)
    lg.build(ctx)
    full_text = "\n\n".join(f"[{when(ts)}] {t}" for _, ts, t in ctx.log)

    def transcript(c, upto):
        lines = [f"Person: {c['opening']}"]
        for t in c["turns"][:upto]:
            if t["asked"]:
                lines.append(f"Mind: {t['asked']}")
            lines.append(f"Person: {t['text']}")
        return "\n".join(lines)

    def one(job):
        f, moment, arm = job
        c = chats[f["chat"]]
        t = f["turn"]
        upto = t if moment == "before" else t + 1
        convo = transcript(c, upto)[-12000:]
        last = c["turns"][t - 1]["text"] if (moment == "before" and t > 0) else c["opening"] if moment == "before" else \
            f"{c['turns'][t]['asked']} {c['turns'][t]['text']}"
        base = arm.removesuffix("_active")
        if base == "none":
            memory = "(nothing)"
        elif base == "product":
            memory = product_text
        elif base == "full":
            memory = full_text
        else:
            ev = [Evidence("dossier_inc", dossier.text)] + st.read(last[:3000], ctx) + lg.read(last[:3000], ctx)
            memory = assemble(ev, 48000)
        template = MIND_ACTIVE if prompt == "MIND_ACTIVE" else MIND
        text, call = chat(MODEL, [{"role": "user", "content": template.format(memory=memory, transcript=convo)}],
                          tag=f"live:{arm}", effort="low", max_tokens=800)
        message = str(text).strip()
        if moment == "before":
            v = chat_json(MODEL, KNOWS.format(fact=f["s"], transcript=convo, message=message), YES("knows"),
                          schema_name="v", tag="live:judge", effort="low")[0]["knows"]
        else:
            v = chat_json(MODEL, REMEMBERS.format(fact=f["s"], transcript=convo, message=message), YES("remembers"),
                          schema_name="v", tag="live:judge", effort="low")[0]["remembers"]
        sampled = zlib.crc32(f"{a}|{f['chat']}|{f['i']}|{moment}".encode()) % 6 == 0
        inv = chat_json(MODEL, INVENTS.format(memory=memory, transcript=convo, message=message), YES("invents"),
                        schema_name="v", tag="live:invents", effort="low")[0]["invents"] if sampled else None
        return {"account": a, "chat": f["chat"], "i": f["i"], "moment": moment, "arm": arm, "hit": int(bool(v)),
                "invents": None if inv is None else int(bool(inv)), "tokens": call.input_tokens, "message": message}

    jobs = [(f, m, arm) for f in fs for m in ("before", "after") for arm in arms]
    with ThreadPoolExecutor(8) as pool:
        rows = list(pool.map(one, jobs))
    (PRIVATE / "results" / f"{out_name}_{a}.json").write_text(json.dumps(rows, ensure_ascii=False))
    print(json.dumps({"account": a, "facts": len(fs), "messages": len(rows), "spent": round(spent(), 4)}))


SAME_FACT = """Earlier, a person had told their therapy assistant: «{fact}»
In a new conversation, they have just said it again. Does the assistant's reply below refer to that same fact as \
something the person said in an earlier conversation (for example "you mentioned before that ..." followed by the \
same thing)? A different or merely related earlier detail is not enough, and replying to what they just said is not \
enough.

REPLY: {message}"""


# @ref LLP 0021#amendment-telling-the-assistant-to-use-its-memory — the strict reading: the same fact, not a related one
def strict(names: list[str]):
    for a in names:
        facts_ = {r["id"]: r for r in json.loads((PRIVATE / f"return_gold_{a}.json").read_text())}
        rows = json.loads((PRIVATE / "results" / f"live_{a}.json").read_text())
        path = PRIVATE / "results" / f"live_active_{a}.json"
        rows += json.loads(path.read_text()) if path.exists() else []
        after = [r for r in rows if r["moment"] == "after"]

        def one(r):
            v = chat_json(MODEL, SAME_FACT.format(fact=facts_[r["chat"]]["statements"][r["i"]]["s"], message=r["message"]),
                          YES("same_fact"), schema_name="v", tag="live:strict", effort="low")[0]["same_fact"]
            return {"account": a, "chat": r["chat"], "i": r["i"], "arm": r["arm"], "same_fact": int(bool(v))}

        with ThreadPoolExecutor(8) as pool:
            out = list(pool.map(one, after))
        (PRIVATE / "results" / f"live_strict_{a}.json").write_text(json.dumps(out))
    rows = [r for a in names for r in json.loads((PRIVATE / "results" / f"live_strict_{a}.json").read_text())]
    arms = list(dict.fromkeys(r["arm"] for r in rows))
    table = {arm: round(100 * sum(r["same_fact"] for r in rows if r["arm"] == arm) / max(1, sum(1 for r in rows if r["arm"] == arm)), 1)
             for arm in arms}
    key = lambda r: (r["account"], r["chat"], r["i"])

    def paired(x, y, n=10000, seed=1):
        A = {key(r): r["same_fact"] for r in rows if r["arm"] == x}
        B = {key(r): r["same_fact"] for r in rows if r["arm"] == y}
        d = [A[k] - B[k] for k in A if k in B]
        rng = random.Random(seed)
        bs = sorted(sum(rng.choice(d) for _ in d) / len(d) for _ in range(n))
        return [round(100 * sum(d) / len(d), 1), round(100 * bs[int(0.025 * n)], 1), round(100 * bs[int(0.975 * n)], 1), len(d)]

    pairs = [("conv_best2_active", "product_active"), ("conv_best2_active", "conv_best2"), ("conv_best2", "product"),
             ("conv_best2", "none"), ("full", "conv_best2")]
    out = {"same_fact_after": table, "tests": {f"{x} - {y}": paired(x, y) for x, y in pairs if x in arms and y in arms},
           "spent": round(spent(), 4)}
    (PRIVATE / "results" / f"live_strict_summary_{'own' if names[0].startswith('own') else 'users'}.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


# ------------------------------------------------------------------------------ LLP 0022: a re-telling detector
DETECT_Q = ("Is the person, in `message`, telling the assistant something it already knows from earlier "
            "conversations? If so, which statement in `memory` is it?")
RETOLD_NOTE = ("\n\n## They are repeating something they told you before\nThey told you this before (first on {first}, "
               "{times}): {text}. Acknowledge that you remember it; don't make them explain it again.")


# @ref LLP 0022#design — the step between "the memory has it" and "the conversation changes": a closed decision per turn
def detect(a: str, cap: int = 30, converse: bool = True):
    import zlib
    from lab.common import jev
    from lab.common.llm import chat
    from lab.memory.answerers import assemble
    from lab.memory.stores import IncrementalDossierStore, LinkedStatementStore, LinkedStatementsListPicked, LogListPicked
    mode = "shim" if os.environ.get("LAB_PROCESSORS") == "openai" else "jev"
    fs = facts(a)
    if len(fs) > cap:
        fs = sorted(random.Random(7).sample(fs, cap), key=lambda f: (f["chat"], f["i"]))
    moments(a, fs)
    chats = {c["id"]: c for c in json.loads((PRIVATE / f"return_chats_{a}.json").read_text())}
    gold = json.loads((PRIVATE / f"return_gold_{a}.json").read_text())
    ctx = context(f"{a}@v25nog")
    st = LinkedStatementStore()
    st.build(ctx)
    m = st.index.m

    # controls: turns whose lasting statements are all new
    controls = []
    for r in gold:
        lasting = [x for x in r["statements"] if x.get("lasting", 0) >= 0.2]
        if not lasting or r["id"] not in chats:
            continue
        turns = chats[r["id"]]["turns"]
        tv = np.stack(embed([f"{t['asked']} {t['text']}" for t in turns], tag="replay:turns"))
        sv = np.stack(embed([x["s"] for x in lasting], tag="replay:facts"))
        where = defaultdict(list)
        for x, v in zip(lasting, sv):
            where[int(np.argmax(tv @ v))].append(x["class"])
        controls += [(r["id"], t) for t, cls in where.items() if all(c == "new" for c in cls)]

    def decide(q):
        idx = st.index.top(q, 40)
        memory = {f"s{n}": st.raw[i]["text"] for n, i in enumerate(idx)}
        options = dict(memory, none="none of these: the person is saying something new, or nothing about themselves")
        ans = jev.ask({"message": q, "memory": memory}, {"pick": jev.choice(DETECT_Q, options)}, tag="detect")["pick"]
        c = ans["choice"]
        if c == "none" or ans["probabilities"].get(c, 0) < 0.5 or c not in memory:
            return None
        return idx[int(c[1:])]

    def positive(f):
        c = chats[f["chat"]]
        q = f"{c['turns'][f['turn']]['asked']} {c['turns'][f['turn']]['text']}"[:3000]
        i = decide(q)
        ok = i is not None and bool(set(st.raw[i]["src"]) & f["logs"]) and float(m[i] @ f["vec"]) >= 0.5
        return {"fact": f, "q": q, "pick": i, "hit": int(ok), "fired": int(i is not None)}

    def control(job):
        cid, t = job
        turn = chats[cid]["turns"][t]
        return int(decide(f"{turn['asked']} {turn['text']}"[:3000]) is not None)

    with ThreadPoolExecutor(8) as pool:
        pos = list(pool.map(positive, fs))
        ctl = list(pool.map(control, controls))
    out = {"account": a, "mode": mode, "positives": len(pos), "detected": sum(p["hit"] for p in pos),
           "fired_on_positives": sum(p["fired"] for p in pos), "controls": len(ctl), "false_alarms": sum(ctl)}

    if converse:   # LLP 0021's live replay after F, directive prompt, conv_best2 memory; the detector's note on a pick
        dossier = IncrementalDossierStore()
        dossier.build(ctx)
        sp, lg = LinkedStatementsListPicked(), LogListPicked()
        sp.build(ctx)
        lg.build(ctx)
        groups, group_of = st.groups, st.group_of

        def talk(p):
            f = p["fact"]
            c = chats[f["chat"]]
            t = f["turn"]
            lines = [f"Person: {c['opening']}"]
            for x in c["turns"][:t + 1]:
                if x["asked"]:
                    lines.append(f"Mind: {x['asked']}")
                lines.append(f"Person: {x['text']}")
            convo = "\n".join(lines)[-12000:]
            ev = [Evidence("dossier_inc", dossier.text)] + sp.read(p["q"], ctx) + lg.read(p["q"], ctx)
            memory = assemble(ev, 48000)
            if p["pick"] is not None:
                g = groups[group_of[p["pick"]]]
                times = f"said {g['count']} times" if g["count"] > 1 else "said once"
                memory += RETOLD_NOTE.format(first=g["first"], times=times, text=st.raw[p["pick"]]["text"])
            text, call = chat(MODEL, [{"role": "user", "content": MIND_ACTIVE.format(memory=memory, transcript=convo)}],
                              tag="live:detect", effort="low", max_tokens=800)
            message = str(text).strip()
            same = chat_json(MODEL, SAME_FACT.format(fact=f["s"], message=message), YES("same_fact"), schema_name="v",
                             tag="live:strict", effort="low")[0]["same_fact"]
            sampled = zlib.crc32(f"{a}|{f['chat']}|{f['i']}|after".encode()) % 6 == 0
            inv = chat_json(MODEL, INVENTS.format(memory=memory, transcript=convo, message=message), YES("invents"),
                            schema_name="v", tag="live:invents", effort="low")[0]["invents"] if sampled else None
            return {"chat": f["chat"], "i": f["i"], "fired": p["fired"], "same_fact": int(bool(same)),
                    "invents": None if inv is None else int(bool(inv)), "tokens": call.input_tokens}

        with ThreadPoolExecutor(8) as pool:
            conv = list(pool.map(talk, pos))
        out["conversation"] = conv
    lat = [c.latency for c in LEDGER.calls if c.tag.startswith("detect")]
    out["latency_s"] = round(sum(lat) / len(lat), 3) if lat else None
    out["spent"] = round(spent(), 4)
    (PRIVATE / "results" / f"detect_{a}_{mode}.json").write_text(json.dumps(out))
    print(json.dumps({k: v for k, v in out.items() if k != "conversation"}))


def detect_summary(names: list[str], mode: str):
    data = [json.loads((PRIVATE / "results" / f"detect_{a}_{mode}.json").read_text()) for a in names]
    pos = sum(d["positives"] for d in data)
    out = {"mode": mode, "positives": pos, "detected": round(100 * sum(d["detected"] for d in data) / pos, 1),
           "fired_on_positives": round(100 * sum(d["fired_on_positives"] for d in data) / pos, 1),
           "controls": sum(d["controls"] for d in data),
           "false_alarms": round(100 * sum(d["false_alarms"] for d in data) / max(1, sum(d["controls"] for d in data)), 1),
           "latency_s": round(sum(d["latency_s"] or 0 for d in data) / len(data), 3)}
    conv = [(d["account"], r) for d in data for r in d.get("conversation", [])]
    if conv:
        strict_rows = {(a, r["chat"], r["i"]): r["same_fact"] for a in names
                       for r in json.loads((PRIVATE / "results" / f"live_strict_{a}.json").read_text())
                       if r["arm"] == "conv_best2_active"}
        pairs = [(r["same_fact"], strict_rows[(a, r["chat"], r["i"])]) for a, r in conv if (a, r["chat"], r["i"]) in strict_rows]
        d = [x - y for x, y in pairs]
        rng = random.Random(1)
        bs = sorted(sum(rng.choice(d) for _ in d) / len(d) for _ in range(10000))
        inv = [r["invents"] for _, r in conv if r["invents"] is not None]
        out.update({"same_fact_with_detector": round(100 * sum(r["same_fact"] for _, r in conv) / len(conv), 1),
                    "same_fact_directive_only": round(100 * sum(y for _, y in pairs) / len(pairs), 1),
                    "difference": [round(100 * sum(d) / len(d), 1), round(100 * bs[250], 1), round(100 * bs[9750], 1), len(d)],
                    "invents": f"{sum(inv)} of {len(inv)}",
                    "same_fact_when_fired": round(100 * sum(r["same_fact"] for _, r in conv if r["fired"]) / max(1, sum(1 for _, r in conv if r["fired"])), 1)})
    (PRIVATE / "results" / f"detect_summary_{mode}_{'own' if names[0].startswith('own') else 'users'}.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


def live_summary(names: list[str]):
    rows = [r for a in names for r in json.loads((PRIVATE / "results" / f"live_{a}.json").read_text())]
    rows += [r for a in names if (PRIVATE / "results" / f"live_active_{a}.json").exists()
             for r in json.loads((PRIVATE / "results" / f"live_active_{a}.json").read_text())]
    arms = list(dict.fromkeys(r["arm"] for r in rows))
    out = {"facts": len({(r["account"], r["chat"], r["i"]) for r in rows}), "arms": {}}
    for arm in arms:
        rs = [r for r in rows if r["arm"] == arm]
        b = [r["hit"] for r in rs if r["moment"] == "before"]
        af = [r["hit"] for r in rs if r["moment"] == "after"]
        inv = [r["invents"] for r in rs if r["invents"] is not None]
        out["arms"][arm] = {"knows_before": round(100 * sum(b) / len(b), 1), "remembers_after": round(100 * sum(af) / len(af), 1),
                            "invents": f"{round(100 * sum(inv) / max(1, len(inv)), 1)}% of {len(inv)}",
                            "input_tokens": round(sum(r["tokens"] for r in rs) / len(rs))}
    key = lambda r: (r["account"], r["chat"], r["i"], r["moment"])

    def paired(a, b, moment, n=10000, seed=1):
        A = {key(r): r["hit"] for r in rows if r["arm"] == a and r["moment"] == moment}
        B = {key(r): r["hit"] for r in rows if r["arm"] == b and r["moment"] == moment}
        d = [A[k] - B[k] for k in A if k in B]
        rng = random.Random(seed)
        bs = sorted(sum(rng.choice(d) for _ in d) / len(d) for _ in range(n))
        return [round(100 * sum(d) / len(d), 1), round(100 * bs[int(0.025 * n)], 1), round(100 * bs[int(0.975 * n)], 1), len(d)]

    pairs = [("conv_best2", "none"), ("conv_best2", "product"), ("conv_best2", "full"), ("full", "product"),
             ("conv_best2_active", "conv_best2"), ("product_active", "product"), ("conv_best2_active", "product_active")]
    out["tests"] = {f"{m}: {a} - {b}": paired(a, b, m) for m in ("before", "after") for a, b in pairs
                    if a in arms and b in arms}
    (PRIVATE / "results" / f"live_summary_{'own' if names[0].startswith('own') else 'users'}.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    {"run": lambda: [run(a) for a in args], "summary": lambda: summary(args),
     "route": lambda: [route(a) for a in args],
     "route_summary": lambda: route_summary(args[1:], args[0]),
     "pick": lambda: [pick(a) for a in args], "pick_summary": lambda: pick_summary(args[1:], args[0]),
     "live": lambda: [live(a) for a in args], "live_summary": lambda: live_summary(args),
     "strict": lambda: strict(args),
     "detect": lambda: [detect(a, converse=not a.startswith("own")) for a in args],
     "detect_summary": lambda: detect_summary(args[1:], args[0]),
     "live_active": lambda: [live(a, arms=["product_active", "conv_best2_active"], prompt="MIND_ACTIVE",
                                  out_name="live_active") for a in args]}[cmd]()
