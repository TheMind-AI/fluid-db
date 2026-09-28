"""Answerers: working memory. Assemble the routed evidence within a budget and answer (LLP 0013#design).

  LLMAnswerer   one GPT-6 Luna call over the evidence, grouped by store; the database's exact answers come first
  HybridFacts   the round-4 reader, unchanged: the deterministic answer when the checker trusts it, else the Luna
                tool agent over SQL and log search (the baseline this round has to beat)
"""
# @ref LLP 0013#design — Answerer adapters; LLP 0006 for the hybrid reader
from __future__ import annotations

from collections import defaultdict

from lab.common.llm import chat

PROMPT = """You are the long-term memory of {user}. Now is {now} ({weekday}). Answer the user's question from the
evidence below. It comes from several memory stores, each labelled.

Rules:
- A value "computed by the database" is exact when its trust is high; check that it fits the question and the rest.
- Newer statements override older ones: give the current value, and an earlier one only when asked about the past.
- A message that reports something as it happens ("we closed", "landed", "today") dates it to that message.
- The intentions store is the record of reminders and plans: "open, not done yet" means it hasn't been done.
- For what happened on a day, list everything the evidence shows for that day.
- Answer about exactly who or what was asked; a similar name (David, Dad) is someone else.
- "[forgotten]" marks something the user asked you to forget. Never reveal or reconstruct forgotten information.
- If the evidence doesn't contain the answer, say exactly "I don't know." Don't guess.
- Answer in one or two sentences with the specific facts (names, numbers, dates).

QUESTION: {question}

EVIDENCE:
{evidence}"""


ORDER = ["facts", "dossier", "dossier_inc", "statements", "statements_merged", "statements_rr", "statements_linked",
         "statements_linked_p3", "statements_linked_rr", "intentions", "preferences", "routines", "periods", "episodes",
         "log", "log_hybrid", "log_embed", "log_rr", "log_p3", "log_rr_full", "log_all"]


def assemble(evidence, budget: int | None) -> str:
    """Group by store in a fixed order (the order stores were routed in must not change the answer), exact answers
    first, and cut each store to a fair share of the budget."""
    by = defaultdict(list)
    for e in sorted(evidence, key=lambda e: (ORDER.index(e.store) if e.store in ORDER else len(ORDER), not e.exact)):
        by[e.store].append(e.text)
    if not by:
        return "(no evidence)"
    share = None if budget is None else budget // len(by)
    parts = []
    for store, texts in by.items():
        body, used = [], 0
        for t in texts:
            if share is not None and used + len(t) > share and body:
                break
            body.append(t)
            used += len(t)
        parts.append(f"## {store}\n" + "\n".join(body))
    return "\n\n".join(parts)


class LLMAnswerer:
    def __init__(self, model: str = "openai:gpt-6-luna", budget: int | None = 24000, tag: str = "memory:answer"):
        self.model, self.budget, self.tag = model, budget, tag
        self.tokens: dict[str, int] = {}

    def answer(self, question, evidence, ctx):
        prompt = PROMPT.format(user=ctx.user, now=ctx.now.strftime("%Y-%m-%d %H:%M"), weekday=ctx.now.strftime("%A"),
                               question=question, evidence=assemble(evidence, self.budget))
        text, call = chat(self.model, [{"role": "user", "content": prompt}], tag=self.tag, effort="low", max_tokens=3000)
        self.tokens[question] = call.input_tokens
        return str(text).strip()


class HybridFacts:
    """Not an evidence answerer: it ignores the routed evidence and runs the round-4 hybrid on the facts database."""

    def __init__(self, model: str = "openai:gpt-6-luna", trust: float = 0.7):
        self.model, self.trust = model, trust
        self.paths: dict[str, str] = {}

    def answer(self, question, evidence, ctx):
        from lab.systems.reader import ask_agent
        det = ctx.reader.ask(question)
        if (det.get("verified") or 0) >= self.trust and det["answer"] != "I don't know.":
            self.paths[question] = "deterministic"
            return det["answer"]
        self.paths[question] = "agent"
        return ask_agent(ctx.reader.eng, question, ctx.now, ctx.user, self.model, "memory:hybrid:agent")["answer"]
