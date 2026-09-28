"""Jev (TypeSafe's System One model) through OpenRouter's /systemone endpoint.

Jev does not generate text. You send a `state` plus typed questions (choice,
score, noul) and get calibrated probabilities back. Output tokens are free and
input costs $0.042 per 1M tokens.
"""
# @ref LLP 0008 — the Jev client, cached like every model call
from __future__ import annotations

import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

from lab.common.llm import LAB_DIR, LEDGER, CacheMiss, Call, chat_json, read_json_cache, write_json_cache

JEV_MODEL = "typesafe/jev-1.13"
JEV_PRICE_PER_M = 0.042
URL = "https://openrouter.ai/api/v1/systemone"
CACHE_DIR = LAB_DIR / ".cache" / "jev"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

_http = httpx.Client(timeout=120)


def noul(instructions, criteria: dict | None = None) -> dict:
    q = {"type": "noul", "instructions": instructions}
    if criteria:
        q["criteria"] = criteria
    return q


def choice(instructions, options: dict) -> dict:
    return {"type": "choice", "instructions": instructions, "criteria": options}


def score(instructions, levels: list) -> dict:
    return {"type": "score", "instructions": instructions, "criteria": levels}


def ask(state, questions: dict, *, tag: str = "jev", cache: bool = True) -> dict:
    """Evaluate every question against `state` in one request. Returns answers by id."""
    # @ref LLP 0018#processors — without TypeSafe in LAB_PROCESSORS, an allowed LLM answers in Jev's shape instead
    allowed = os.environ.get("LAB_PROCESSORS")
    if allowed and "typesafe" not in allowed.split(","):
        return ask_llm(state, questions, tag=tag)
    cache = cache and not os.environ.get("LAB_NO_CACHE")
    payload = {"model": JEV_MODEL, "state": state, "questions": questions}
    key = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    path = CACHE_DIR / f"{key}.json"
    hit = read_json_cache(path) if cache else None
    if hit is not None:
        LEDGER.add(Call(model="jev-1.13", tag=tag, input_tokens=hit["in"], output_tokens=0,
                        cost=hit["in"] * JEV_PRICE_PER_M / 1e6, latency=hit["latency"], cached=True))
        return hit["answers"]

    # @ref LLP 0001#budget — replays must never spend
    if os.environ.get("LAB_CACHE_ONLY") and os.environ.get("LAB_CACHE_ONLY") != "llm":
        raise CacheMiss(f"Jev call not in cache (LAB_CACHE_ONLY is set; tag={tag!r})")
    headers = {"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}", "Content-Type": "application/json"}
    delay = 1.0
    for attempt in range(8):
        start = time.time()
        resp = _http.post(URL, headers=headers, json=payload)
        latency = time.time() - start
        if resp.status_code == 429 or resp.status_code >= 500:
            time.sleep(delay)
            delay = min(delay * 2, 30)
            continue
        if resp.status_code != 200:
            raise RuntimeError(f"Jev error {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        tin = data["usage"]["input_tokens"]
        LEDGER.add(Call(model="jev-1.13", tag=tag, input_tokens=tin, output_tokens=0,
                        cost=tin * JEV_PRICE_PER_M / 1e6, latency=latency, cached=False))
        if cache:
            write_json_cache(path, {"answers": data["answers"], "in": tin, "latency": latency})
        return data["answers"]
    raise RuntimeError("Jev: too many retries")


def ask_many(jobs: list[tuple], *, workers: int = 8, tag: str = "jev") -> list[dict]:
    """Run several independent (state, questions) requests concurrently."""
    with ThreadPoolExecutor(workers) as pool:
        return list(pool.map(lambda job: ask(job[0], job[1], tag=tag), jobs))


def chunked_nouls(state, questions: dict, *, chunk: int = 64, tag: str = "jev") -> dict:
    """Split a large question set across requests (keeps each request small)."""
    items = list(questions.items())
    jobs = [(state, dict(items[i:i + chunk])) for i in range(0, len(items), chunk)]
    merged: dict = {}
    for answers in ask_many(jobs, tag=tag):
        merged.update(answers)
    return merged



# ---------------------------------------------------------------------------------------------------- the LLM shim
SHIM_MODEL = os.environ.get("LAB_JEV_SHIM", "openai:gpt-6-luna")
SHIM_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["yes_no", "choices"], "properties": {
    "yes_no": {"type": "array", "items": {"type": "object", "additionalProperties": False,
               "required": ["key", "yes", "probability"], "properties": {
                   "key": {"type": "string"}, "yes": {"type": "boolean"},
                   "probability": {"type": "number", "description": "your probability (0-1) that the answer is yes"}}}},
    "choices": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                "required": ["key", "choice", "probabilities"], "properties": {
                    "key": {"type": "string"}, "choice": {"type": "string"},
                    "probabilities": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                      "required": ["option", "p"], "properties": {
                                          "option": {"type": "string"}, "p": {"type": "number"}}}}}}}}}


def _p_yes(a: dict | None) -> float:
    """The probability made to agree with the stated answer: LLMs often report confidence in their own answer."""
    if a is None:
        return 0.5
    p = max(0.0, min(1.0, float(a.get("probability", 0.5))))
    return p if (a.get("yes") and p >= 0.5) or (not a.get("yes") and p <= 0.5) else 1 - p


def ask_llm(state, questions: dict, *, tag: str = "jev") -> dict:
    """Jev's noul and choice questions answered by an LLM (LAB_JEV_SHIM), in Jev's answer shape. Used when the data
    may not go to TypeSafe; the LLM's probabilities are stated, not calibrated like Jev's (LLP 0013.000)."""
    yn = {k: q for k, q in questions.items() if q.get("type") == "noul"}
    ch = {k: q for k, q in questions.items() if q.get("type") == "choice"}
    if len(yn) + len(ch) < len(questions):
        raise ValueError("the Jev shim answers only noul and choice questions")
    crit = lambda q: "" if not q.get("criteria") else " (" + "; ".join(f"{a}: {b}" for a, b in q["criteria"].items()) + ")"
    prompt = ("Answer typed questions about the JSON STATE below, as a careful classifier. Names in backticks refer to "
              "fields of STATE.\n- Yes/no questions: answer yes or no, with your probability (0-1) that the answer is "
              "yes.\n- Choice questions: choose exactly one listed option, copying its name exactly, and give a "
              "probability for every option (they sum to 1).\n\nSTATE:\n" + json.dumps(state, ensure_ascii=False, indent=1)
              + "\n\nYES/NO QUESTIONS (key: question):\n" + ("\n".join(f"- {k}: {q['instructions']}{crit(q)}" for k, q in yn.items()) or "(none)")
              + "\n\nCHOICE QUESTIONS (key: question; then its options as name: description):\n"
              + ("\n".join(f"- {k}: {q['instructions']}\n" + "\n".join(f"    - {o}: {d}" for o, d in (q.get("criteria") or {}).items())
                           for k, q in ch.items()) or "(none)"))
    out, _ = chat_json(SHIM_MODEL, prompt, SHIM_SCHEMA, schema_name="answers", tag=f"{tag}:shim", effort="low",
                       max_tokens=16000)
    norm = lambda x: str(x).strip().strip("`").lower()

    def by_key(keys, items):   # exact key, then case-insensitive, then position (LLMs renumber keys, LLP 0013.000)
        got = {norm(a.get("key", "")): a for a in items}
        res = {k: got[norm(k)] for k in keys if norm(k) in got}
        if len(res) < len(keys) and len(items) == len(keys):
            res.update({k: a for k, a in zip(keys, items) if k not in res})
        return res

    answers = {k: {"type": "noul", "noul": round(_p_yes(a), 4)} for k, a in by_key(list(yn), out["yes_no"]).items()}
    answers.update({k: {"type": "noul", "noul": 0.5} for k in yn if k not in answers})
    got = by_key(list(ch), out["choices"])
    for k, q in ch.items():
        opts = list(q.get("criteria") or {})
        a = got.get(k) or {}
        probs = {o: 0.0 for o in opts}
        for x in a.get("probabilities") or []:
            o = next((o for o in opts if norm(o) == norm(x.get("option"))), None)
            if o is not None:
                probs[o] += max(0.0, float(x.get("p") or 0))
        total = sum(probs.values())
        probs = {o: round(p / total, 4) for o, p in probs.items()} if total > 0 else {o: round(1 / len(opts), 4) for o in opts}
        choice = next((o for o in opts if norm(o) == norm(a.get("choice"))), None) or max(probs, key=probs.get)
        answers[k] = {"type": "choice", "choice": choice, "probabilities": probs, "confidence": probs[choice]}
    return answers
