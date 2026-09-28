"""Mem0 OSS (mem0ai 2.x) as a baseline memory system, wired for replaying dated conversations.

- Same model as FluidDB (GPT-6 Luna, low reasoning), OpenAI text-embedding-3-small, local Qdrant per store,
  hybrid search (vectors + BM25 via fastembed + spaCy entity boosts), telemetry off.
- Mem0 OSS cannot take a message's date, so the extraction prompt's Observation Date is patched per thread to the
  replayed message's timestamp (otherwise "yesterday" resolves to the real today) and every memory keeps that
  timestamp as created_at.
- Mem0's OpenAI adapter silently switches to OpenRouter whenever OPENROUTER_API_KEY is set; the key is hidden from it
  after our own OpenRouter client (used by judges) has been built.
"""
# @ref LLP 0002.001 — the Mem0 baseline of round 2
from __future__ import annotations

import os

os.environ["MEM0_TELEMETRY"] = "False"  # read by mem0 at import time

import threading
import time
from datetime import datetime
from pathlib import Path

from lab.common.llm import PRICES, _client

_client("openrouter")
os.environ.pop("OPENROUTER_API_KEY", None)

import mem0.memory.main as _m0main  # noqa: E402
from mem0 import Memory  # noqa: E402

USAGE = {"calls": 0, "in": 0, "out": 0}
_usage_lock = threading.Lock()
_observation = threading.local()
_extraction_prompt = _m0main.generate_additive_extraction_prompt


def _dated_prompt(*args, **kwargs):
    when = getattr(_observation, "date", None)
    kwargs["timestamp"] = kwargs.get("timestamp") or when
    kwargs["current_date"] = kwargs.get("current_date") or when
    return _extraction_prompt(*args, **kwargs)


_m0main.generate_additive_extraction_prompt = _dated_prompt


def _count(_llm, response, _params):
    with _usage_lock:
        USAGE["calls"] += 1
        USAGE["in"] += response.usage.prompt_tokens
        USAGE["out"] += response.usage.completion_tokens


def cost() -> float:
    p_in, p_out = PRICES["gpt-6-luna"]
    return USAGE["in"] * p_in / 1e6 + USAGE["out"] * p_out / 1e6


def memory(path: Path, collection: str = "mem") -> Memory:
    path.mkdir(parents=True, exist_ok=True)
    return Memory.from_config({
        "llm": {"provider": "openai", "config": {"model": "gpt-6-luna", "reasoning_effort": "low", "is_reasoning_model": True,
                                                 "max_tokens": 8000, "response_callback": _count}},
        "embedder": {"provider": "openai", "config": {"model": "text-embedding-3-small"}},
        "vector_store": {"provider": "qdrant", "config": {"collection_name": collection, "path": str(path / "qdrant"),
                                                          "embedding_model_dims": 1536, "on_disk": True}},
        "history_db_path": str(path / "history.db"),
    })


def add(m: Memory, messages: list[dict], when: datetime, user_id: str, metadata: dict | None = None) -> tuple[list, str | None]:
    """Add dated messages; retries rate limits (nothing is stored before the extraction call succeeds)."""
    _observation.date = when.strftime("%Y-%m-%d (%A) %H:%M")
    err = None
    for attempt in range(6):
        try:
            out = m.add(messages, user_id=user_id, metadata={"created_at": when.isoformat(), **(metadata or {})})
            return (out.get("results", []) if isinstance(out, dict) else out or []), None
        except Exception as e:
            err = f"{type(e).__name__}: {e}"[:300]
            time.sleep(2 * 2 ** attempt)
    return [], err


def all_memories(m: Memory, user_id: str) -> list[dict]:
    return m.get_all(filters={"user_id": user_id}, top_k=100000)["results"]


def search(m: Memory, query: str, user_id: str, k: int) -> list[dict]:
    return m.search(query, filters={"user_id": user_id}, top_k=k)["results"]


def created(r: dict) -> str:
    return r.get("created_at") or (r.get("metadata") or {}).get("created_at") or ""
