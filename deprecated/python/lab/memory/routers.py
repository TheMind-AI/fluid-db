"""Routers: which stores a question needs (LLP 0013#design).

  AllRouter      every store (the answerer sorts it out)
  OracleRouter   the stores the benchmark's labelled memory type maps to (an upper bound; needs the labels)
  DeciderRouter  one yes/no per store, asked together in one request, by any Decider (Jev or an LLM)
"""
# @ref LLP 0013#design — Router adapters; the Jev and LLM routers see store descriptions only, never the types
from __future__ import annotations

import hashlib

from lab.common.llm import LEDGER

# the benchmark's memory types and the stores that are meant to serve them (used by OracleRouter only)
TYPE_STORES = {
    "semantic_current": ["facts", "log"], "semantic_history": ["facts", "log"], "aggregate": ["facts"],
    "episodic_event": ["episodes", "log"], "episodic_time": ["episodes", "facts", "log"], "routine": ["routines"],
    "prospective": ["intentions"], "preference": ["preferences", "facts"], "source": ["log", "facts"],
    "period": ["periods", "facts", "episodes"], "metamemory": ["facts", "log"], "forgotten": ["facts", "log"],
}


class AllRouter:
    name = "all"

    def route(self, question, stores, ctx):
        return [s.name for s in stores]


class OracleRouter:
    name = "oracle"

    def __init__(self, types: dict[str, str]):
        self.types = types                       # question -> memory type

    def route(self, question, stores, ctx):
        have = {s.name for s in stores}
        return [s for s in TYPE_STORES[self.types[question]] if s in have]


class DeciderRouter:
    """Asks, for every store at once: would this store hold what the question needs? Keeps the stores with p ≥ 0.5
    (at most `cap`, most likely first), or the single most likely store when none passes."""

    def __init__(self, decider, threshold: float = 0.5, cap: int = 4):
        self.decider, self.threshold, self.cap = decider, threshold, cap
        self.name = f"{decider.name}_router"
        self.probs: dict[str, dict[str, float]] = {}
        self.latency: dict[str, float] = {}

    def route(self, question, stores, ctx):
        state = {"question": question, "now": ctx.now.strftime("%Y-%m-%d"),
                 **{f"store_{s.name}": s.description for s in stores}}
        asks = {s.name: f"Would `store_{s.name}` hold information needed to answer `question`?" for s in stores}
        tag = f"memory:route:{self.name}:{hashlib.sha1(question.encode()).hexdigest()[:10]}"
        p = self.decider.yes_many(state, asks, tag=tag)
        self.probs[question] = p
        self.latency[question] = sum(c.latency for c in LEDGER.calls if c.tag == tag)
        ranked = sorted(p, key=lambda k: -p[k])
        keep = [k for k in ranked if p[k] >= self.threshold][:self.cap]
        return keep or ranked[:1]


def make(name: str, decider=None, types=None, threshold: float = 0.5):
    if name == "all":
        return AllRouter()
    if name == "oracle":
        return OracleRouter(types)
    return DeciderRouter(decider, threshold=threshold)
