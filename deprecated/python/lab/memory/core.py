"""A memory with several stores, built from swappable parts (LLP 0013).

Every part is an adapter; an experiment is a config that names one choice for each:
  Store     build(ctx) consolidates from the shared log (and the facts database); read(question) -> [Evidence]
  Decider   closed-set decisions: choose(options) and yes(), by Jev or by an LLM (lab/memory/deciders.py)
  Router    which stores a question needs (lab/memory/routers.py)
  Answerer  working memory: assemble the routed evidence, answer (lab/memory/answerers.py)
All stores derive from one append-only log, so they share provenance, confidence and forgetting.
"""
# @ref LLP 0013#design — stores, deciders, routers and answerers are adapters; a config picks one of each
from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class Evidence:
    store: str
    text: str
    when: str = ""
    source: list[int] = field(default_factory=list)   # _log ids (provenance)
    confidence: float = 1.0
    exact: bool = False                                  # computed by the database (not retrieved text)


class Store:
    name = "store"
    description = ""          # what routers read to decide whether this store is needed

    def build(self, ctx: "Context") -> dict:
        return {}

    def read(self, question: str, ctx: "Context", k: int = 8) -> list[Evidence]:
        raise NotImplementedError


class Context:
    """What every part may use: the database file (facts + _log), the user, now, the models, a cache directory."""

    def __init__(self, db_path: str, user: str, now: datetime, model: str = "openai:gpt-6-luna", decider=None,
                 cache_dir: Path | None = None):
        self.db_path, self.user, self.now, self.model, self.decider = db_path, user, now, model, decider
        self.cache_dir = Path(cache_dir or Path(db_path).parent / "memory")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.log = [(r[0], r[1], r[2]) for r in self.db.execute("SELECT id, ts, text FROM _log ORDER BY id")]
        self._reader = None

    @property
    def reader(self):
        if self._reader is None:
            from lab.systems.det_reader import DetReader
            self._reader = DetReader(self.db_path, self.user, self.now)
        return self._reader

    def saved(self, name: str, make):
        """A consolidated store's content, built once and kept on disk (rebuilt when the file is deleted)."""
        path = self.cache_dir / f"{name}.json"
        if path.exists():
            return json.loads(path.read_text())
        data = make()
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1))
        return data


class Memory:
    """One configuration: stores (already built), a router, an answerer, and optionally a lesion: stores that are
    still routed to but return nothing, like a damaged brain area (the double-dissociation test, LLP 0013)."""

    def __init__(self, name: str, stores: list[Store], router, answerer, ctx: Context, lesion: tuple = ()):
        self.name, self.stores, self.router, self.answerer, self.ctx = name, {s.name: s for s in stores}, router, answerer, ctx
        self.lesion = set(lesion)

    def ask(self, question: str) -> dict:
        t0 = time.time()
        routed = self.router.route(question, list(self.stores.values()), self.ctx) if self.stores else []
        t_route = time.time() - t0
        evidence = []
        for name in routed:
            if name in self.stores and name not in self.lesion:
                evidence += self.stores[name].read(question, self.ctx)
        t_read = time.time() - t0 - t_route
        answer = self.answerer.answer(question, evidence, self.ctx)
        return {"answer": answer, "stores": routed, "evidence": len(evidence),
                "evidence_chars": sum(len(e.text) for e in evidence), "route_s": round(t_route, 3),
                "read_s": round(t_read, 3), "wall_s": round(time.time() - t0, 3),
                "answer_tokens_in": getattr(self.answerer, "tokens", {}).get(question, 0)}


# ---------------------------------------------------------------------------------------------- shared helpers
WORD = re.compile(r"[a-z0-9áčďéěíňóřšťúůýž]+")
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]


def days_named(question: str) -> list[str]:
    """Explicit calendar days a question names: 2025-03-19, April 7, 2024, July 12 2026 -> ISO dates."""
    q = question.lower()
    out = re.findall(r"\b(20\d\d-\d\d-\d\d)\b", q)
    for m in re.finditer(r"\b(" + "|".join(x[:3] for x in MONTHS) + r")[a-z]*\.? (\d{1,2})(?:st|nd|rd|th)?,? (20\d\d)\b", q):
        out.append(f"{m.group(3)}-{[x[:3] for x in MONTHS].index(m.group(1)) + 1:02d}-{int(m.group(2)):02d}")
    return out
