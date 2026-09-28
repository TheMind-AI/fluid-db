"""Deciders: the closed-set decisions a memory makes all the time (is this a preference? which store? same
person? does this answer the question?). Two interchangeable implementations, so "is Jev the why-now" is one swap:

  JevDecider  TypeSafe's Jev: typed questions over a JSON state, calibrated probabilities, output tokens free
  LLMDecider  GPT-6 Luna with structured output and a stated confidence (how LLM confidences are usually obtained)

Both batch many independent questions over one state into a single request.
"""
# @ref LLP 0013#design — Decider adapters; LLP 0008 for what Jev is and isn't good at
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from lab.common import jev
from lab.common.llm import chat_json


class JevDecider:
    name = "jev"

    def choose_many(self, state: dict, questions: dict[str, tuple[str, dict[str, str]]], tag: str) -> dict[str, dict]:
        """questions: {key: (instruction, {option: description})} -> {key: {"choice", "probabilities"}}."""
        ans = jev.ask(state, {k: jev.choice(q, opts) for k, (q, opts) in questions.items()}, tag=tag)
        return {k: {"choice": a["choice"], "probabilities": a["probabilities"]} for k, a in ans.items()}

    def yes_many(self, state: dict, questions: dict[str, str], tag: str) -> dict[str, float]:
        ans = jev.ask(state, {k: jev.noul(q) for k, q in questions.items()}, tag=tag)
        return {k: a["noul"] for k, a in ans.items()}

    def batch(self, jobs: list, fn: str, tag: str, workers: int = 8) -> list:
        with ThreadPoolExecutor(workers) as pool:
            return list(pool.map(lambda j: getattr(self, fn)(*j, tag=tag), jobs))


YES_SCHEMA = {"type": "object", "properties": {"answers": {"type": "array", "items": {"type": "object", "properties": {
    "key": {"type": "string"}, "yes": {"type": "boolean"},
    "probability": {"type": "number", "description": "your probability (0-1) that the answer is yes"}},
    "required": ["key", "yes", "probability"], "additionalProperties": False}}},
    "required": ["answers"], "additionalProperties": False}
CHOOSE_SCHEMA = {"type": "object", "properties": {"answers": {"type": "array", "items": {"type": "object", "properties": {
    "key": {"type": "string"}, "choice": {"type": "string"},
    "confidence": {"type": "number", "description": "your probability (0-1) that the choice is right"}},
    "required": ["key", "choice", "confidence"], "additionalProperties": False}}},
    "required": ["answers"], "additionalProperties": False}


def match(keys: list[str], answers: list[dict]) -> dict[str, dict]:
    """The model's answers by question key: exact key, then case/space-insensitive, then position (models often
    renumber the keys: 64 of 69 verifier answers came back as "q1" and the like, LLP 0013.000)."""
    norm = lambda k: str(k).strip().strip("`").lower()
    by = {norm(a.get("key", "")): a for a in answers}
    out = {k: by[norm(k)] for k in keys if norm(k) in by}
    if len(out) < len(keys) and len(answers) == len(keys):
        out.update({k: a for k, a in zip(keys, answers) if k not in out})
    return out


def p_yes(a: dict | None) -> float:
    """P(yes) from {yes, probability}: models often report their confidence in their own answer ("no", 1.0), so the
    probability is made to agree with the stated answer. A missing answer counts as 0.5, not as a confident no."""
    if a is None:
        return 0.5
    p = max(0.0, min(1.0, float(a.get("probability", 0.5))))
    return p if (a.get("yes") and p >= 0.5) or (not a.get("yes") and p <= 0.5) else 1 - p


class LLMDecider:
    name = "llm"

    def __init__(self, model: str = "openai:gpt-6-luna"):
        self.model = model

    def yes_many(self, state: dict, questions: dict[str, str], tag: str) -> dict[str, float]:
        import json
        prompt = ("Answer each yes/no question about the STATE below, with your probability that the answer is yes.\n\n"
                  f"STATE:\n{json.dumps(state, ensure_ascii=False, indent=1)}\n\nQUESTIONS (key: question):\n"
                  + "\n".join(f"- {k}: {q}" for k, q in questions.items()))
        out, _ = chat_json(self.model, prompt, YES_SCHEMA, schema_name="answers", tag=tag, effort="low", max_tokens=4000)
        return {k: p_yes(a) for k, a in match(list(questions), out["answers"]).items()}

    def choose_many(self, state: dict, questions: dict[str, tuple[str, dict[str, str]]], tag: str) -> dict[str, dict]:
        import json
        prompt = ("Answer each multiple-choice question about the STATE below: pick one option key, and give your "
                  f"probability that it is right.\n\nSTATE:\n{json.dumps(state, ensure_ascii=False, indent=1)}\n\nQUESTIONS:\n"
                  + "\n".join(f"- {k}: {q}\n  options: " + "; ".join(f"{o} = {d}" for o, d in opts.items())
                              for k, (q, opts) in questions.items()))
        out, _ = chat_json(self.model, prompt, CHOOSE_SCHEMA, schema_name="answers", tag=tag, effort="low", max_tokens=4000)
        res = {}
        found = match(list(questions), out["answers"])
        for k, (q, opts) in questions.items():
            a = found.get(k)
            choice = a["choice"] if a and a["choice"] in opts else next(iter(opts))
            conf = max(0.0, min(1.0, float(a["confidence"]))) if a else 0.0
            rest = (1 - conf) / max(1, len(opts) - 1)
            res[k] = {"choice": choice, "probabilities": {o: (conf if o == choice else rest) for o in opts}}
        return res

    def batch(self, jobs: list, fn: str, tag: str, workers: int = 8) -> list:
        with ThreadPoolExecutor(workers) as pool:
            return list(pool.map(lambda j: getattr(self, fn)(*j, tag=tag), jobs))


def make(name: str):
    return {"jev": JevDecider, "llm": LLMDecider}[name]()
