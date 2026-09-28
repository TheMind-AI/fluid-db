"""Jev as FluidDB's System 1: the fast yes/no and pick-one decisions.

Following TypeSafe's own recipes (rerank / RAG-passage cookbooks): one small
request per (message or question, record) pair, several atomic Nouls each,
thresholds in code. Jev never generates the data; the LLM does that.
"""
# @ref LLP 0008 — Jev decides closed questions; the LLM writes the data
from __future__ import annotations

from lab.common import jev

# --------------------------------------------------------------------- gate
INTENT = jev.choice(
    "What is `message` mainly doing?",
    {
        "share": "Tells the assistant new information: a fact, event, plan, purchase, contact, preference or document",
        "change": "Corrects or updates something shared before, or reports that its status changed (moved, finished, sold, renewed)",
        "remove": "Asks to forget or delete information, or says a plan or appointment was cancelled",
        "ask": "Asks the assistant a question or asks it to look something up",
        "chat": "Small talk, a greeting, a reaction or thanks, with no information to keep",
    },
)
GATE_QUESTIONS = {
    "intent": INTENT,
    "worth_remembering": jev.noul(
        "Does `message` contain a fact about the user's life, people, plans, purchases, activities or preferences "
        "that a personal assistant should remember?"),
}


# the same gate worded for any domain (round 8, LLP 0014.000#writes): a customer's ticket is information, not a question
GATE_QUESTIONS_NEUTRAL = {
    "intent": jev.choice("What is `message` mainly doing?", {
        "share": "Gives new information: a fact, event, plan, request, purchase, contact, preference or document",
        "change": "Corrects or updates something shared before, or reports that its status changed",
        "remove": "Asks to forget or delete information, or says a plan or appointment was cancelled",
        "ask": "The user asks their assistant a question or asks it to look something up. A message from someone "
               "else (an email, a ticket, a form) is not this",
        "chat": "Small talk, a greeting, a reaction or thanks, with no information to keep"}),
    "worth_remembering": jev.noul("Does `message` contain information worth keeping: a fact, event, request, "
                                  "commitment, change or document about people, organizations, work or life?"),
}


# @ref LLP 0005#the-jev-gate
def gate(text: str, ts: str, tag: str = "jev:gate", neutral: bool = False) -> dict:
    """Decide whether a message needs a write at all."""
    ans = jev.ask({"message": text, "sent_at": ts}, GATE_QUESTIONS_NEUTRAL if neutral else GATE_QUESTIONS, tag=tag)
    intent = ans["intent"]
    worth = ans["worth_remembering"]["noul"]
    write = not (intent["choice"] in ("ask", "chat") and intent["confidence"] >= 0.5 and worth < 0.5)
    return {"write": write, "intent": intent["choice"], "confidence": intent["confidence"],
            "probabilities": intent["probabilities"], "worth": worth}


SOURCE = {"source": jev.choice(
    "Who wrote `message`?",
    {
        "user": "The user, writing to their assistant in their own words",
        "document": "A forwarded or pasted document written by someone else or by a system: email, receipt, invoice, "
                    "calendar invite, contact card, web page",
    },
)}


# @ref LLP 0005#source-guard
def source(text: str, tag: str = "jev:source") -> dict:
    """Is this the user's own words, or third-party content (which must not be able to delete/overwrite data)?"""
    ans = jev.ask({"message": text}, SOURCE, tag=tag)["source"]
    return {"source": ans["choice"], "confidence": ans["confidence"]}


# ------------------------------------------------------------ write context
WRITE_CTX_QUESTIONS = {
    "same_subject": jev.noul("Is `record` about a person, organization, place, object or event that `message` mentions?"),
    "changes_record": jev.noul("Does `message` add to, change, correct or cancel information stored in `record`?"),
}


def select_for_write(text: str, records: list[dict], *, threshold: float = 0.3, cap: int = 40,
                     tag: str = "jev:write") -> list[tuple[dict, float]]:
    """Pick the existing rows the planner must see (possible duplicates, rows to update or link)."""
    jobs = [({"message": text, "record": {"table": r["table"], **r["row"]}}, WRITE_CTX_QUESTIONS) for r in records]
    answers = jev.ask_many(jobs, workers=16, tag=tag)
    scored = [(r, max(a["same_subject"]["noul"], a["changes_record"]["noul"])) for r, a in zip(records, answers)]
    scored.sort(key=lambda x: -x[1])
    return [(r, s) for r, s in scored if s >= threshold][:cap]


# ------------------------------------------------------------- read context
READ_QUESTIONS = {
    "is_relevant": jev.noul("Is `record` about the subject of `question`?"),
    "has_evidence": jev.noul("Does `record` contain information that could be used to answer `question`?"),
}


def select_for_read(question: str, records: list[dict], *, threshold: float = 0.2, cap: int = 60,
                    tag: str = "jev:read", workers: int = 16) -> list[tuple[dict, float]]:
    jobs = [({"question": question, "record": {"table": r["table"], **r["row"]}}, READ_QUESTIONS) for r in records]
    answers = jev.ask_many(jobs, workers=workers, tag=tag)
    scored = [(r, max(a["is_relevant"]["noul"], a["has_evidence"]["noul"])) for r, a in zip(records, answers)]
    scored.sort(key=lambda x: -x[1])
    return [(r, s) for r, s in scored if s >= threshold][:cap]


ANSWERABLE = {
    "answerable": jev.noul("Do the `records` contain the information needed to answer `question`?"),
}


def answerable(question: str, records: list[dict], tag: str = "jev:answerable") -> float:
    ans = jev.ask({"question": question, "records": records}, ANSWERABLE, tag=tag)
    return ans["answerable"]["noul"]
