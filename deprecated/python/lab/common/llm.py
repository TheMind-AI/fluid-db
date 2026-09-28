"""One client for every model used in the lab.

Model ids carry their provider as a prefix:
  openai:gpt-6-luna          -> OpenAI API
  anthropic:claude-sonnet-5  -> Anthropic API
  openrouter:google/...      -> OpenRouter chat completions

Every call is cached on disk (keyed by the full request), so re-running an
experiment or its analysis costs nothing. Every call is also recorded in a
ledger with tokens, dollars and latency; cached replays keep the original
cost/latency so reports stay comparable.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

from dotenv import load_dotenv

LAB_DIR = Path(__file__).resolve().parents[1]
# The archive keeps its own cache, but keys remain in the repository-root .env.
load_dotenv(LAB_DIR.parents[2] / ".env")

CACHE_DIR = LAB_DIR / ".cache" / "llm"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# USD per 1M tokens: (input, output). Cached-input discounts are ignored so
# costs are an upper bound and comparable across providers.
PRICES = {
    "gpt-4-turbo": (10.0, 30.0),
    "gpt-6-luna": (0.10, 0.50),
    "gpt-6-sol": (2.0, 10.0),
    "gpt-5": (1.25, 10.0),
    "gpt-4o-2024-08-06": (2.5, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-opus-5-5": (4.0, 20.0),
    "google/gemini-3.8-flash": (0.75, 3.75),
    "deepseek/deepseek-v4.1-flash": (0.15, 0.60),
    "text-embedding-3-small": (0.02, 0.0),
}


@dataclass
class Call:
    model: str
    tag: str
    input_tokens: int
    output_tokens: int
    cost: float
    latency: float
    cached: bool


@dataclass
class Ledger:
    calls: list[Call] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, call: Call):
        with self.lock:
            self.calls.append(call)

    def summary(self, tag_prefix: str = "") -> dict:
        calls = [c for c in self.calls if c.tag.startswith(tag_prefix)]
        return {
            "calls": len(calls),
            "input_tokens": sum(c.input_tokens for c in calls),
            "output_tokens": sum(c.output_tokens for c in calls),
            "cost": round(sum(c.cost for c in calls), 6),
            "latency": round(sum(c.latency for c in calls), 3),
        }

    def to_json(self) -> list[dict]:
        return [asdict(c) for c in self.calls]


LEDGER = Ledger()


def price(model: str, input_tokens: int, output_tokens: int) -> float:
    p_in, p_out = PRICES.get(model, (0.0, 0.0))
    return (input_tokens * p_in + output_tokens * p_out) / 1e6


OPENROUTER_CLAUDE = {
    "claude-sonnet-5": "anthropic/claude-sonnet-5",
    "claude-haiku-4-5": "anthropic/claude-haiku-4.5",
    "claude-opus-5-5": "anthropic/claude-opus-5.5",
}


class LLMError(Exception):
    pass


class ProcessorNotAllowed(RuntimeError):
    pass


def check_processor(provider: str) -> None:
    """LAB_PROCESSORS lists the providers data may go to (e.g. "openai"). Other people's conversations may only go to
    the processors their privacy policy names; anything else raises before a request is made."""
    allowed = os.environ.get("LAB_PROCESSORS")
    if allowed and provider not in allowed.split(","):
        raise ProcessorNotAllowed(f"provider {provider!r} is not in LAB_PROCESSORS={allowed!r}")


_clients: dict = {}


def _client(provider: str):
    if provider not in _clients:
        if provider == "openai":
            import openai
            _clients[provider] = openai.OpenAI(max_retries=5, timeout=300)
        elif provider == "openrouter":
            import openai
            _clients[provider] = openai.OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=os.environ["OPENROUTER_API_KEY"],
                max_retries=5,
                timeout=300,
            )
        elif provider == "anthropic":
            import anthropic
            _clients[provider] = anthropic.Anthropic(max_retries=5, timeout=300)
        else:
            raise ValueError(provider)
    return _clients[provider]


def _cache_key(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _cached(key: str):
    return read_json_cache(CACHE_DIR / f"{key}.json")


def _store(key: str, value: dict):
    write_json_cache(CACHE_DIR / f"{key}.json", value)


def read_json_cache(path: Path):
    """A cache entry, or None if missing or unreadable (a torn write counts as a miss)."""
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def write_json_cache(path: Path, value) -> None:
    """Atomic: several processes may compute and store the same entry at once."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(value))
    os.replace(tmp, path)


class CacheMiss(RuntimeError):
    """Raised instead of a paid call when LAB_CACHE_ONLY=1 (replays must be free)."""


def chat(
    model: str,
    messages: list[dict],
    *,
    system: str = "",
    schema: dict | None = None,
    schema_name: str = "output",
    effort: str | None = "low",
    max_tokens: int = 8000,
    tag: str = "",
    cache: bool = True,
    cache_read: bool = True,
) -> tuple[str | dict, Call]:
    """Send a chat request. With `schema`, returns the parsed JSON object.

    messages: [{"role": "user"|"assistant", "content": str}]
    effort: reasoning effort for models that support it (ignored elsewhere).
    """
    provider, name = model.split(":", 1)
    # @ref LLP 0018#processors — the processor allowlist is checked before the cache, so no path can bypass it
    check_processor(provider)
    cache = cache and not os.environ.get("LAB_NO_CACHE")
    payload = {
        "model": model, "system": system, "messages": messages, "schema": schema,
        "effort": effort, "max_tokens": max_tokens,
    }
    key = _cache_key(payload)
    hit = _cached(key) if cache and cache_read else None
    if hit is not None:
        call = Call(model=name, tag=tag, input_tokens=hit["in"], output_tokens=hit["out"],
                    cost=price(name, hit["in"], hit["out"]), latency=hit["latency"], cached=True)
        LEDGER.add(call)
        return hit["result"], call

    # @ref LLP 0001#budget — replays must never spend
    if os.environ.get("LAB_CACHE_ONLY"):
        raise CacheMiss(f"{model} call not in cache (LAB_CACHE_ONLY is set; tag={tag!r})")
    start = time.time()
    text, tin, tout = _with_rate_limit_retry(lambda: _dispatch(provider, name, system, messages, schema, schema_name, effort, max_tokens))
    latency = time.time() - start

    result: str | dict = text
    if schema is not None:
        try:
            result = json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMError(f"{model} returned invalid JSON: {text[:300]}") from e

    call = Call(model=name, tag=tag, input_tokens=tin, output_tokens=tout,
                cost=price(name, tin, tout), latency=latency, cached=False)
    LEDGER.add(call)
    if cache:
        _store(key, {"result": result, "in": tin, "out": tout, "latency": latency})
    return result, call


def _with_rate_limit_retry(fn, max_wait: float = 180.0):
    """Provider SDKs retry briefly; under heavy parallel load we also wait out 429s for up to ~3 minutes."""
    delay, waited = 2.0, 0.0
    while True:
        try:
            return fn()
        except Exception as e:  # openai.RateLimitError / anthropic.RateLimitError / 429 via OpenRouter
            status = getattr(e, "status_code", None)
            if status != 429 and "RateLimit" not in type(e).__name__ or waited >= max_wait:
                raise
            time.sleep(delay)
            waited += delay
            delay = min(delay * 2, 30.0)


def _dispatch(provider, name, system, messages, schema, schema_name, effort, max_tokens):
    if provider == "anthropic" and os.environ.get("LAB_ANTHROPIC_VIA") == "openrouter":
        # Same Claude model, served through OpenRouter (used after the direct Anthropic account ran
        # out of credits). The cache key keeps the "anthropic:" id, so earlier results stay valid.
        via = OPENROUTER_CLAUDE[name]
        return _openai_like("openrouter", via, system, messages, schema, schema_name,
                            None if name.startswith("claude-haiku") else effort, max_tokens)
    if provider == "anthropic":
        return _anthropic(name, system, messages, schema, effort, max_tokens)
    return _openai_like(provider, name, system, messages, schema, schema_name, effort, max_tokens)


def _anthropic(name, system, messages, schema, effort, max_tokens):
    client = _client("anthropic")
    kwargs: dict = {"model": name, "max_tokens": max_tokens, "messages": messages}
    if system:
        kwargs["system"] = system
    output_config: dict = {}
    if schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    # Haiku 4.5 rejects `effort`; the Claude 5 family accepts it.
    if effort and not name.startswith("claude-haiku"):
        output_config["effort"] = effort
    if output_config:
        kwargs["output_config"] = output_config
    resp = client.messages.create(**kwargs)
    if resp.stop_reason == "refusal":
        raise LLMError(f"{name} refused: {resp.stop_details}")
    text = "".join(b.text for b in resp.content if b.type == "text")
    return text, resp.usage.input_tokens + (resp.usage.cache_read_input_tokens or 0) + (resp.usage.cache_creation_input_tokens or 0), resp.usage.output_tokens


def _openai_like(provider, name, system, messages, schema, schema_name, effort, max_tokens):
    client = _client(provider)
    msgs = ([{"role": "system", "content": system}] if system else []) + messages
    kwargs: dict = {"model": name, "messages": msgs}
    legacy = name.startswith("gpt-4") or name.startswith("gpt-3.5")
    if legacy:
        # 2024-era models: no strict structured outputs, no reasoning. Use a
        # forced function call, exactly like `instructor` did in the 2024 code.
        kwargs["temperature"] = 0
        kwargs["max_tokens"] = min(max_tokens, 4096)
        if schema is not None:
            kwargs["tools"] = [{"type": "function", "function": {"name": schema_name, "parameters": schema}}]
            kwargs["tool_choice"] = {"type": "function", "function": {"name": schema_name}}
    else:
        kwargs["max_completion_tokens"] = max_tokens
        if effort and provider == "openai":
            kwargs["reasoning_effort"] = effort
        if effort and provider == "openrouter":
            kwargs["extra_body"] = {"reasoning": {"effort": effort}}
        if schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "schema": schema, "strict": True},
            }
    resp = client.chat.completions.create(**kwargs)
    choice = resp.choices[0]
    if legacy and schema is not None:
        calls = choice.message.tool_calls or []
        if not calls:
            raise LLMError(f"{name} did not call the function: {choice.message.content!r}")
        text = calls[0].function.arguments
    else:
        text = choice.message.content or ""
    return text, resp.usage.prompt_tokens, resp.usage.completion_tokens


def chat_json(model: str, prompt: str, schema: dict, *, system: str = "", retries: int = 2, **kw) -> tuple[dict, Call]:
    """Single-turn JSON call with a couple of retries on malformed output."""
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            # Retries skip the cache read but still store their result under the same
            # key, so a re-run replays the successful answer instead of diverging.
            return chat(model, [{"role": "user", "content": prompt}], system=system, schema=schema,
                        cache_read=(attempt == 0), **kw)
        except LLMError as e:
            last = e
    raise last  # type: ignore[misc]


# ---------------------------------------------------------------------------------------------------- embeddings
EMBED_DIR = LAB_DIR / ".cache" / "embed"
EMBED_DIR.mkdir(parents=True, exist_ok=True)


# @ref LLP 0019#the-new-parts — meaning search; cached per text, behind the processor allowlist like every call
def embed(texts: list[str], model: str = "openai:text-embedding-3-small", tag: str = "embed", batch: int = 64):
    """Unit-length embedding vectors (numpy float32 rows), one per text, each cached on disk by (model, text)."""
    import base64
    import numpy as np
    provider, name = model.split(":", 1)
    check_processor(provider)
    texts = [str(t)[:24000] or " " for t in texts]
    out: list = [None] * len(texts)
    todo = []
    for i, t in enumerate(texts):
        key = _cache_key({"model": model, "text": t})
        hit = read_json_cache(EMBED_DIR / f"{key}.json")
        if hit is not None:
            out[i] = np.frombuffer(base64.b64decode(hit["v"]), dtype=np.float32)
            LEDGER.add(Call(model=name, tag=tag, input_tokens=hit["in"], output_tokens=0,
                            cost=price(name, hit["in"], 0), latency=0.0, cached=True))
        else:
            todo.append((i, t, key))
    if todo and os.environ.get("LAB_CACHE_ONLY"):
        raise CacheMiss(f"{len(todo)} embeddings not in cache (LAB_CACHE_ONLY is set; tag={tag!r})")
    for j in range(0, len(todo), batch):
        part = todo[j:j + batch]
        start = time.time()
        resp = _with_rate_limit_retry(lambda: _client(provider).embeddings.create(model=name, input=[t for _, t, _ in part]))
        tokens = resp.usage.total_tokens
        LEDGER.add(Call(model=name, tag=tag, input_tokens=tokens, output_tokens=0, cost=price(name, tokens, 0),
                        latency=time.time() - start, cached=False))
        for (i, t, key), d in zip(part, resp.data):
            v = np.asarray(d.embedding, dtype=np.float32)
            v /= (np.linalg.norm(v) or 1.0)
            out[i] = v
            write_json_cache(EMBED_DIR / f"{key}.json", {"v": base64.b64encode(v.tobytes()).decode(),
                                                         "in": max(1, tokens // len(part))})
    return out
