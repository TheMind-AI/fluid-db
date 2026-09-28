"""Cheap, deterministic graders for gold-answer QA (alternatives to the LLM judge in judge.py).

rule_grade: code only. Takes the core facts of the gold answer (outside parentheses; details inside them are
            optional), where a fact is a phone number, a date, a clock time, a number, a yes/no polarity or a
            content word that the question itself doesn't already contain. Scores 1 / 0.5 / 0 like the judge.
jev_grade:  one Jev Choice per answer (correct / partial / incorrect) with the judge's rubric; its probabilities give
            a soft score as well.
"""
# @ref LLP 0009#graders — rules against truth first; Jev as judge where answers are free text
from __future__ import annotations

import re
import unicodedata

from lab.common import jev

MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
STOP = set("""a an the and or but of to in on at for from with by as is are was were be been it its this that these those
my me i you your he his she her they their them we our us not no yes do does did done have has had will would can could
still now then than also just only about into over after before since until when where which who whom what how why
there here per each all any some more most other such same very so up out off one two three month months year years day
days week weeks time times total so far currently current right""".split())
WEEKDAYS = {"mon", "tue", "tues", "wed", "thu", "thur", "thurs", "fri", "sat", "sun", "monday", "tuesday", "wednesday",
            "thursday", "friday", "saturday", "sunday"}
SYNONYMS = {"canceled": "cancelled", "fiance": "fiancee", "finsihed": "finished"}
ABSTAIN = re.compile(r"\b(i don.?t know|i do not know|don.?t have|no (record|information|data|mention)|not (recorded|stored|"
                     r"mentioned|available|in (the|your) (data|database|records))|asked (me )?to forget|forgotten|"
                     r"can.?t tell|cannot tell|unknown|isn.?t (recorded|stored|mentioned))\b", re.I)


def _plain(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return s.replace("’", "'").lower()


def _facts(text: str, question_words: set[str]) -> set[tuple[str, str]]:
    """Typed facts in a piece of gold (or answer) text."""
    s, out = _plain(text), set()
    for m in re.finditer(r"\+?\d[\d ]{7,}\d", s):  # phone numbers: compare the last 9 digits
        digits = re.sub(r"\D", "", m.group())
        if len(digits) >= 9:
            out.add(("phone", digits[-9:]))
            s = s.replace(m.group(), " ")
    for m in re.finditer(r"\b(\d{4})-(\d{2})-(\d{2})", s):
        out.add(("date", f"{int(m[2]):02d}-{int(m[3]):02d}"))
        s = s.replace(m.group(), " ")
    for m in re.finditer(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? (\d{1,2})\b(?:st|nd|rd|th)?,?( \d{4})?", s):
        out.add(("date", f"{MONTHS[m[1]]:02d}-{int(m[2]):02d}"))
        s = s.replace(m.group(), " ")
    for m in re.finditer(r"\b(\d{1,2}) (jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?,?( \d{4})?", s):
        out.add(("date", f"{MONTHS[m[2]]:02d}-{int(m[1]):02d}"))
        s = s.replace(m.group(), " ")
    for m in re.finditer(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\b", s):  # 2:00 pm -> 14:00
        h = int(m[1]) % 12 + (12 if m[3] == "p" else 0)
        out.add(("time", f"{h}:{m[2] or '00'}"))
        s = s.replace(m.group(), " ")
    for m in re.finditer(r"\b(\d{1,2}):(\d{2})\b", s):
        out.add(("time", f"{int(m[1])}:{m[2]}"))
        s = s.replace(m.group(), " ")
    for m in re.finditer(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)\s*(m|million|k)?\b", s):
        v = float(m[1].replace(",", ""))
        v *= {"m": 1e6, "million": 1e6, "k": 1e3}.get(m[2] or "", 1)
        out.add(("num", f"{v:.2f}"))
    if re.match(r"\s*(yes|no)\b", s):
        out.add(("polarity", re.match(r"\s*(yes|no)\b", s)[1]))
    for w in re.findall(r"[a-z][a-z0-9+/-]*[a-z0-9]|[0-9]+[a-z][a-z0-9]*", s):
        w = SYNONYMS.get(w, w)
        if len(w) >= 3 and w not in STOP and w not in WEEKDAYS and w not in question_words and w not in MONTHS:
            out.add(("word", w))
    return out


def _present(fact: tuple[str, str], answer_facts: set, answer_plain: str) -> bool:
    kind, v = fact
    if kind == "word":
        return re.search(rf"\b{re.escape(v)}", answer_plain) is not None or \
            re.search(rf"\b{re.escape(v.rstrip('s'))}", answer_plain) is not None
    if kind == "num":
        x = float(v)
        return any(k == "num" and abs(float(a) - x) <= max(0.005 * abs(x), 0.01) for k, a in answer_facts)
    return fact in answer_facts


def rule_grade(question: str, gold: str, answer: str) -> float:
    a_plain = _plain(answer)
    abstained = bool(ABSTAIN.search(a_plain))
    if gold.upper().startswith("UNKNOWN"):
        return 1.0 if abstained else 0.0
    qwords = {SYNONYMS.get(w, w) for w in re.findall(r"[a-z][a-z0-9]+", _plain(question))}
    core_text = re.sub(r"\([^)]*\)", " ", gold)
    details_text = " ".join(re.findall(r"\(([^)]*)\)", gold))
    core, details = _facts(core_text, qwords), _facts(details_text, qwords)
    if not core:
        core, details = details, set()
    a_facts = _facts(answer, set())
    core_hit = sum(_present(f, a_facts, a_plain) for f in core) / len(core)
    if ("polarity", "yes") in core and re.match(r"\s*no\b", a_plain) or ("polarity", "no") in core and re.match(r"\s*yes\b", a_plain):
        return 0.0
    if core_hit >= 0.8:  # parenthetical details are optional, as for the LLM judge
        return 1.0
    if core_hit >= 0.5 and not (abstained and core_hit < 1):
        return 0.5
    return 0.0


JEV_JUDGE = {"verdict": jev.choice(
    "Grade `answer`, from a personal-memory assistant, against `gold` for `question`.",
    {"correct": "It states the key facts of `gold` and contradicts none of them (formatting differences are fine). "
                "If `gold` starts with UNKNOWN, only an answer that says it does not know or has no such information.",
     "partial": "Some key facts of `gold` are right, but others are missing or wrong.",
     "incorrect": "Wrong, missing the key fact, gives an outdated value as current, says it doesn't know when `gold` "
                  "has a real answer, or claims a value when `gold` starts with UNKNOWN."})}


def jev_grade(question: str, gold: str, answer: str, tag: str = "grade:jev") -> dict:
    a = jev.ask({"question": question, "gold": gold, "answer": answer[:4000]}, JEV_JUDGE, tag=tag)["verdict"]
    p = a["probabilities"]
    return {"score": {"correct": 1.0, "partial": 0.5, "incorrect": 0.0}[a["choice"]],
            "soft": round(p.get("correct", 0) + 0.5 * p.get("partial", 0), 4), "confidence": a["confidence"]}
