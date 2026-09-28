"""The query log: every question the database answered, the plan it used, and how far to trust that plan.

It lives in the database file itself (`_queries`), next to the raw message log (`_log`). The schema advisor and the
question compiler learn from it, and never from gold answers. Their only evidence is the signals below, which a
deployed system has too.
"""
# @ref LLP 0003#query-log — one row per question: path, plan, verifier score, signals
from __future__ import annotations

import json
import re

from lab.systems.derived import fold

DDL = """CREATE TABLE IF NOT EXISTS _queries (id INTEGER PRIMARY KEY, qid TEXT, ts TEXT, question TEXT, path TEXT,
    answer TEXT, plan TEXT, verified REAL, signals TEXT, agent_answer TEXT, latency_s REAL, cost REAL)"""

# words that carry no schema meaning in a question (question words, glue, time words)
GLUE = set("""a an the and or but of to in on at for from with by as is are was were be been it its this that these those
my me i you your we our not no do does did done have has had will would can could how much many what when where which
who whom why there here per each all any some more most least other such same very so up out off one about into over
after before since until spend spent spending pay paid paying cost costs money total totals times time often number
count average typical typically usually most least biggest largest smallest priciest cheapest expensive single bill
ever last first next previous this year years month months week weeks day days today yesterday tonight morning
night nights go went going get got had have take took make made log logged per did hours lowest highest fastest
slowest longest shortest least most visit visits session sessions trip trips purchase purchases order orders ordered
buy bought something things stuff""".split())
MONTHS = {"january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"}


def ensure(db):
    db.execute(DDL)
    db.commit()


def record(db, e: dict):
    ensure(db)
    db.execute("INSERT INTO _queries (qid, ts, question, path, answer, plan, verified, signals, agent_answer, latency_s, cost) "
               "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
               (e.get("qid"), e.get("ts"), e["question"], e["path"], e["answer"], json.dumps(e.get("plan") or {}, ensure_ascii=False),
                e.get("verified"), json.dumps(e.get("signals") or []), e.get("agent_answer"), e.get("latency_s"), e.get("cost")))
    db.commit()


def load(db) -> list[dict]:
    ensure(db)
    cols = [r[1] for r in db.execute("PRAGMA table_info(_queries)")]
    out = []
    for r in db.execute("SELECT * FROM _queries ORDER BY id"):
        d = dict(zip(cols, r))
        d["plan"], d["signals"] = json.loads(d["plan"] or "{}"), json.loads(d["signals"] or "[]")
        out.append(d)
    return out


def numbers(text: str) -> list[float]:
    """Numbers in a text: 34,875.00 / 1 240 / 6.98 / $84.50 (dates' parts count as numbers too)."""
    out = []
    for m in re.finditer(r"(?<![\d.])(\d{1,3}(?:[ ,]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?![\d])", str(text or "")):
        try:
            out.append(float(m.group(1).replace(",", "").replace(" ", "")))
        except ValueError:
            pass
    return out


def plan_of(out: dict) -> dict:
    return {"table": out.get("table"), "op": out.get("op"), "column": out.get("column"),
            "filters": out.get("filters") or {}, "period": out.get("period"), "text": out.get("plan")}


def unmatched_terms(reader, question: str, table: str | None) -> list[str]:
    """Content words of the question that match nothing in the table that answered it (its name, columns, stored
    values, and the names in the tables it links to), by a 4-letter stem. No table: nothing in the whole database."""
    words_of = lambda s: set(re.findall(r"[a-z]{3,}", fold(s)))
    tables = [table] if table in reader.meta else reader.tables
    vocab = set()
    for t in tables:
        m = reader.meta[t]
        vocab |= words_of(t)
        for c in m["columns"]:
            vocab |= words_of(c)
        for d in m["dims"].values():
            for v in (reader._labels[d["table"]].values() if d["kind"] == "link" else d.get("values", [])[:400]):
                vocab |= words_of(v)
    stems = {w[:4] for w in vocab}
    short = {w for w in vocab if len(w) == 3}          # "run" matches "runs", "running"
    words = [w for w in re.findall(r"[a-z]{3,}", fold(question)) if w not in GLUE and w not in MONTHS]
    return sorted({w for w in words if w[:4] not in stems and w[:3] not in short})


def has_variants(reader, table: str | None, dim: str, value: str) -> bool:
    """The plan filtered on one spelling of a value that the column also stores in other spellings ("Onesip" /
    "Onesip Coffee"), so the filter misses rows."""
    from lab.systems.derived import merge_candidates
    spec = reader.meta.get(table, {}).get("dims", {}).get(dim)
    if not spec or spec["kind"] != "values" or spec.get("free_text"):
        return False
    return any(value in pair for pair in merge_candidates({v: 1 for v in spec["values"]}))


def signals(reader, question: str, det: dict, threshold: float, agent_answer: str | None = None) -> list[str]:
    s = []
    if det["answer"] == "I don't know.":
        s.append("no_answer")
    if (det.get("verified") or 0) < threshold:
        s.append("low_trust")
    if det.get("relaxed"):
        s.append("relaxed")
    dims = reader.meta.get(det.get("table"), {}).get("dims", {})
    free = [d for d in det.get("filters") or {} if dims.get(d, {}).get("free_text")]
    if free:
        s.append("free_text:" + ",".join(free))
    varied = [d for d, v in (det.get("filters") or {}).items() if has_variants(reader, det.get("table"), d, v)]
    if varied:
        s.append("variants:" + ",".join(varied))
    terms = unmatched_terms(reader, question, det.get("table"))
    if terms:
        s.append("unmatched:" + ",".join(terms))
    if agent_answer is not None and det["answer"] != "I don't know.":
        a, b = set(numbers(det["answer"])), set(numbers(agent_answer))
        if a and b and not (a & b):
            s.append("disagree")
    return s
