"""LLM judge: grades a system's answer against the gold answer."""
# @ref LLP 0009#graders — the Opus reference judge of rounds 1-2
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from lab.common.llm import chat_json

JUDGE_MODEL = "anthropic:claude-opus-5-5"

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["correct", "partial", "incorrect"]},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "reason"],
    "additionalProperties": False,
}

JUDGE_PROMPT = """Grade an answer from a personal-memory assistant against the gold answer.

Rules:
- "correct": the answer contains the key facts of the gold answer and nothing that contradicts it. Extra correct detail is fine. Formatting differences (e.g. "+420 777 999 000" vs "777999000", "Sep 24" vs "2026-09-24") are fine.
- "partial": some key facts are right but others are missing or wrong (e.g. 2 of 3 items, right date but wrong time).
- "incorrect": wrong, missing the key fact, or presents an outdated value as current.
- If the gold answer starts with UNKNOWN, the only correct answer is one that says it doesn't know or has no such information. Any concrete claimed value is "incorrect".
- If the gold answer is a real fact and the answer says "I don't know", that is "incorrect".

QUESTION: {question}
GOLD ANSWER: {gold}
ANSWER TO GRADE: {answer}"""

SCORES = {"correct": 1.0, "partial": 0.5, "incorrect": 0.0}


def judge(question: str, gold: str, answer: str) -> dict:
    out, _ = chat_json(JUDGE_MODEL, JUDGE_PROMPT.format(question=question, gold=gold, answer=answer), JUDGE_SCHEMA,
                       tag="judge", effort="low", max_tokens=4000)
    out["score"] = SCORES[out["verdict"]]
    return out


def judge_many(items: list[tuple[str, str, str]], workers: int = 8) -> list[dict]:
    with ThreadPoolExecutor(workers) as pool:
        return list(pool.map(lambda it: judge(*it), items))
