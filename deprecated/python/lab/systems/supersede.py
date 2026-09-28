"""Async write-side pass: mark rows that a newer row replaced, so "current" becomes a deterministic query.

For rows about the same person in the same table (all pairs in small state tables, consecutive pairs in tables of
dated occurrences), one Jev Noul decides whether the newer row replaces the older one; it also sees the message that
recorded the newer row. Replaced rows get
`valid_to` = the time the newer row was recorded; nothing is deleted, so history stays queryable.
"""
# @ref LLP 0007#supersession — replaced rows get valid_to; nothing is deleted
from __future__ import annotations

from itertools import combinations

from lab.common import jev
from lab.systems.det_reader import DetReader

REPLACES = {"replaces": jev.noul(
    "Is `older` no longer true now that `newer` was recorded, because `newer` replaces it (a new phone number instead "
    "of the old one, a new job, gym or home instead of the old one)? Two different kinds of thing (an email and a "
    "phone), two separate occurrences (two meetings, two tasks, two purchases), or two facts that are both still true "
    "(phone numbers for two different countries) are not replacements.")}


OCCURRENCE_TIMES = ("date", "datetime", "start_datetime", "due_date", "transaction_datetime")


def candidate_pairs(reader: DetReader, state_rows: int = 30) -> list[tuple[str, dict, dict]]:
    out = []
    for t in reader.tables:
        m = reader.meta[t]
        if m["rows"] > 60 or m["time"] in OCCURRENCE_TIMES:
            continue  # rows with their own event date are separate occurrences (meetings, tasks), not states
        for fk, spec in m["dims"].items():
            if spec["kind"] != "link" or spec["table"] != "people":
                continue
            rows = [dict(x) for x in reader.eng.db.execute(f"SELECT * FROM '{t}' WHERE \"{fk}\" IS NOT NULL ORDER BY _ts")]
            groups: dict = {}
            for r in rows:
                groups.setdefault(str(r[fk]), []).append(r)
            for grp in groups.values():
                occurrences = m["time"] != "_ts" and m["rows"] > state_rows or len(grp) > 4
                pairs = zip(grp, grp[1:]) if occurrences else combinations(grp, 2)
                out += [(t, a, b) for a, b in pairs]
    return out


def _source(reader: DetReader, row: dict) -> str:
    """The message(s) that produced a row (from its provenance), so Jev sees e.g. "David's US number is ..."."""
    ids = [int(x) for x in str(row.get("_src") or "").strip("[]").split(",") if x.strip().isdigit()]
    texts = [reader.eng.db.execute("SELECT text FROM _log WHERE id = ?", (i,)).fetchone() for i in ids[:2]]
    return " | ".join(t[0][:300] for t in texts if t)


def judge_pairs(reader: DetReader, pairs) -> list[float]:
    jobs = [({"table": reader.cards[t], "older": reader._row_text(t, a, 300), "newer": reader._row_text(t, b, 300),
              "message_that_recorded_newer": _source(reader, b)}, REPLACES) for t, a, b in pairs]
    return [x["replaces"]["noul"] for x in jev.ask_many(jobs, workers=16, tag="supersede")]


def apply(reader: DetReader, pairs, scores, threshold: float = 0.5) -> int:
    db, n = reader.eng.db, 0
    for (t, a, b), p in zip(pairs, scores):
        if p < threshold:
            continue
        cols = [r[1] for r in db.execute(f"PRAGMA table_info('{t}')")]
        if "valid_to" not in cols:
            db.execute(f"ALTER TABLE '{t}' ADD COLUMN valid_to TEXT")
            db.execute("INSERT OR IGNORE INTO _columns (table_name, column_name, type, description) VALUES (?, ?, ?, ?)",
                       (t, "valid_to", "TEXT", "When this row stopped being current (a newer row replaced it)"))
        db.execute(f"UPDATE '{t}' SET valid_to = ? WHERE id = ? AND valid_to IS NULL", (b["_ts"], a["id"]))
        n += 1
    db.commit()
    return n
