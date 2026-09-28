"""Shared interface and the answer step every system uses, so QA is comparable."""
from __future__ import annotations

import json
from datetime import datetime

from lab.common.llm import chat

ANSWER_PROMPT = """You answer questions for {user} using ONLY the data retrieved from their personal database below.
Current datetime: {now} ({weekday})
If the data does not contain the answer, reply exactly "I don't know." Never guess or use outside knowledge.
Be concise: give the answer with the key details (numbers, dates, names). Do arithmetic carefully when needed.

RETRIEVED DATA:
{data}

QUESTION: {question}"""


def render_input(text) -> str:
    """Messages can be plain text or semi-structured JSON (emails, contact cards)."""
    return text if isinstance(text, str) else json.dumps(text, ensure_ascii=False)


class MemorySystem:
    name = "base"

    def __init__(self, model: str, db_path: str, user: str):
        self.model = model
        self.db_path = db_path
        self.user = user

    def remember(self, message: dict) -> dict:
        raise NotImplementedError

    def ask(self, question: str, now_iso: str) -> dict:
        raise NotImplementedError

    def dump(self) -> dict:
        raise NotImplementedError

    def answer(self, question: str, data: str, now_iso: str, model: str | None = None) -> str:
        now = datetime.fromisoformat(now_iso)
        prompt = ANSWER_PROMPT.format(user=self.user, now=now.strftime("%Y-%m-%d %H:%M"), weekday=now.strftime("%A"),
                                      data=data, question=question)
        text, _ = chat(model or self.model, [{"role": "user", "content": prompt}], tag=f"{self.name}:answer",
                       max_tokens=4000)
        return text.strip()
