"""How sure is the database about each row it stored? Signals from code and from Jev, per row, in `_confidence`.

code  (free) checks a row against the message it came from and against the rest of its table:
        late_night_date        sent 00:00-04:59 and stored under that calendar day ("tonight" at 1 am is the evening before)
        missing_date           the table has a date column and this row's is empty
        variant                a stored name that also exists in other spellings, and this isn't the most common one
        chatter                a stored name carrying chat words ("Note:", "fyi", an emoji)
        person_not_linked      the message names a known person the row doesn't link
        linked_not_mentioned   the row links a person the message doesn't name
        amount_not_in_message  the stored amount appears nowhere in the message
        amount_outlier         far outside what this merchant usually costs (|z| > 3, >= 5 rows)
jev   (generic, untuned) three yes/no questions per row, 8 rows per request, reading the row (links shown as names)
      next to its source message and the time it was sent: faithful? right date? right people?
score  Jev's lowest probability, halved for every code signal (lower = more doubtful)
"""
# @ref LLP 0012#confidence — code signals plus a generic Jev check, one score per row
from __future__ import annotations

import json
import re
import statistics
from collections import Counter, defaultdict

from lab.common import jev
from lab.systems.derived import fold, merge_candidates
from lab.systems.query_log import numbers

DATE_COLS = ("date", "date_watched", "start_datetime", "datetime", "start_date")
CHATTER = re.compile(r"(^|\s)(note|fyi|btw|ok so|lol)\b|[☀-➿\U0001F300-\U0001FAFF]", re.I)
PEOPLE_LINKS = ("with_person_id", "person_id", "friend_id", "companion_id")
QUESTIONS = {
    "faithful": "Does `{row}` record what `{msg}` says, with the right values (what, where, how much, when, who)?",
    "date": "`{msg}` was sent at `{sent}`. Is the date stored in `{row}` the day the thing it describes happened?",
    "people": "Are the people linked in `{row}` exactly the people `{msg}` mentions (none if it mentions none)?",
}
DDL = """CREATE TABLE IF NOT EXISTS _confidence (table_name TEXT, row_id INTEGER, score REAL, jev TEXT, signals TEXT,
                                                  PRIMARY KEY (table_name, row_id))"""


def date_col(cols: list[str]) -> str | None:
    return next((c for c in DATE_COLS if c in cols), None)


class Context:
    """What the checks need to know about the whole database."""

    def __init__(self, db, user: str):
        self.db, self.user = db, user
        self.tables = [r[0] for r in db.execute("SELECT name FROM _tables")]
        self.cols = {t: [r[1] for r in db.execute(f"PRAGMA table_info('{t}')")] for t in self.tables}
        people = {r[0]: r[1] for r in db.execute("SELECT id, name FROM people")} if "people" in self.tables else {}
        self.user_id = next((i for i, n in people.items() if n == user), None)
        self.people = {i: n for i, n in people.items() if n and n != user}
        # the words a message could use for each person: the stored name and its first word
        self.person_words = {i: {fold(n), fold(n.split()[0])} for i, n in self.people.items()}
        self.log = {r[0]: (r[1], r[2]) for r in db.execute("SELECT id, ts, text FROM _log")}
        self.variants, self.money = {}, {}
        for t in self.tables:
            for c in ("merchant", "place", "store", "vendor"):
                if c in self.cols[t]:
                    counts = dict(db.execute(f"SELECT \"{c}\", COUNT(*) FROM '{t}' WHERE \"{c}\" IS NOT NULL GROUP BY 1"))
                    flagged = set()
                    for a, b in merge_candidates(counts):
                        flagged.add(min((a, b), key=lambda v: counts[v]))   # the rarer spelling of a pair
                    self.variants[(t, c)] = flagged
            if "amount" in self.cols[t] and "merchant" in self.cols[t]:
                by = defaultdict(list)
                for m, a in db.execute(f"SELECT merchant, amount FROM '{t}' WHERE amount IS NOT NULL"):
                    by[m].append(a)
                self.money[t] = {m: (statistics.mean(v), statistics.pstdev(v)) for m, v in by.items() if len(v) >= 5}

    def source(self, row: dict) -> tuple[str, str]:
        ids = [int(x) for x in re.findall(r"\d+", str(row.get("_src") or ""))]
        first = self.log.get(ids[0], ("", "")) if ids else ("", "")
        text = " | ".join(str(self.log[i][1]) for i in ids if i in self.log)[:600]
        return first[0], text

    def readable(self, table: str, row: dict) -> str:
        shown = {}
        for k, v in row.items():
            if k in ("id", "_src", "_ts") or v is None:
                continue
            if k.endswith("_id") and isinstance(v, int) and k in PEOPLE_LINKS:
                shown[k[:-3]] = self.people.get(v, self.user if v == self.user_id else f"person #{v}")
            else:
                shown[k] = v
        return json.dumps(shown, ensure_ascii=False)[:400]


def code_signals(ctx: Context, table: str, row: dict) -> list[str]:
    sent, msg = ctx.source(row)
    text = fold(msg)
    out = []
    dc = date_col(ctx.cols[table])
    if dc:
        stored = str(row.get(dc) or "")
        if not stored:
            out.append("missing_date")
        elif sent and int(sent[11:13] or 12) < 5 and stored[:10] == sent[:10]:
            out.append("late_night_date")
    for c in ("merchant", "place", "store", "vendor"):
        v = row.get(c)
        if isinstance(v, str):
            if v in ctx.variants.get((table, c), set()):
                out.append("variant")
            if CHATTER.search(v):
                out.append("chatter")
    links = [c for c in PEOPLE_LINKS if c in ctx.cols[table] and c != "person_id"]
    if links:
        linked = {row.get(c) for c in links if row.get(c) is not None}
        named = {i for i, words in ctx.person_words.items()
                 if any(len(w) >= 3 and re.search(rf"(?<!\w){re.escape(w)}(?!\w)", text) for w in words)}
        if named - linked:
            out.append("person_not_linked")
        if any(i in ctx.person_words and i not in named for i in linked):
            out.append("linked_not_mentioned")
    amount = row.get("amount")
    if isinstance(amount, (int, float)) and msg:
        if not any(abs(n - amount) <= max(0.005 * abs(amount), 0.011) for n in numbers(msg)):
            out.append("amount_not_in_message")
        mean_sd = ctx.money.get(table, {}).get(row.get("merchant"))
        if mean_sd and mean_sd[1] > 0 and abs(amount - mean_sd[0]) > 3 * mean_sd[1]:
            out.append("amount_outlier")
    return out


def suggest(ctx: Context, table: str, row: dict, signals: list[str]) -> dict:
    """A one-click fix for what the signals point at, by code: {column: new value}. Empty when code can't tell."""
    from datetime import date, timedelta
    sent, msg = ctx.source(row)
    text, out = fold(msg), {}
    dc = date_col(ctx.cols[table])
    if dc and sent and ("late_night_date" in signals or "missing_date" in signals):
        day = date.fromisoformat(sent[:10])
        out[dc] = str(day - timedelta(days=1) if int(sent[11:13] or 12) < 5 else day)
    link = next((c for c in PEOPLE_LINKS if c in ctx.cols[table] and c != "person_id"), None)
    if link and ("person_not_linked" in signals or "linked_not_mentioned" in signals):
        said = lambda w: len(w) >= 3 and re.search(rf"(?<!\w){re.escape(w)}(?!\w)", text)
        exact = [i for i, n in ctx.people.items() if said(fold(n))]             # "Eva" said and stored as "Eva"
        first = [i for i, n in ctx.people.items() if said(fold(n.split()[0]))]  # only a first name ("Nina" -> Nina Rossi)
        pick = exact if exact else first
        pick = [i for i in pick if not any(i != j and fold(ctx.people[i]) in fold(ctx.people[j]) for j in pick)]
        if len(pick) == 1:
            out[link] = pick[0]
    return out


def jev_check(ctx: Context, items: list[tuple[str, dict]], chunk: int = 8) -> dict[tuple[str, int], dict]:
    """Three Nouls per row, `chunk` rows per request. Returns {(table, id): {"faithful": p, "date": p, "people": p}}."""
    jobs, keys = [], []
    for i in range(0, len(items), chunk):
        state, qs, batch = {}, {}, []
        for j, (t, row) in enumerate(items[i:i + chunk]):
            sent, msg = ctx.source(row)
            state[f"row_{j}"], state[f"message_{j}"], state[f"sent_{j}"] = ctx.readable(t, row), msg, sent
            for q, text in QUESTIONS.items():
                if q == "people" and not any(c in ctx.cols[t] for c in PEOPLE_LINKS if c != "person_id"):
                    continue
                if q == "date" and not date_col(ctx.cols[t]):
                    continue
                qs[f"{q}_{j}"] = jev.noul(text.format(row=f"row_{j}", msg=f"message_{j}", sent=f"sent_{j}"))
            batch.append((t, row["id"]))
        jobs.append((state, qs))
        keys.append(batch)
    out: dict = {}
    for batch, answers in zip(keys, jev.ask_many(jobs, workers=8, tag="confidence:jev")):
        for j, key in enumerate(batch):
            out[key] = {q: round(answers[f"{q}_{j}"]["noul"], 4) for q in QUESTIONS if f"{q}_{j}" in answers}
    return out


def score(jev_p: dict, signals: list[str]) -> float:
    return round(min(jev_p.values(), default=1.0) * 0.5 ** len(signals), 5)


def assess(db, user: str, tables: list[str] | None = None, use_jev: bool = True) -> dict[tuple[str, int], dict]:
    """Score every row of the dated tables (the ones messages fill). Writes `_confidence`; returns the same data."""
    ctx = Context(db, user)
    tables = tables or [t for t in ctx.tables if date_col(ctx.cols[t])]
    items = [(t, dict(zip(ctx.cols[t], r))) for t in tables for r in db.execute(f"SELECT * FROM '{t}'")]
    sig = {(t, r["id"]): code_signals(ctx, t, r) for t, r in items}
    jp = jev_check(ctx, items) if use_jev else {}
    db.execute(DDL)
    out = {}
    for t, r in items:
        k = (t, r["id"])
        out[k] = {"signals": sig[k], "jev": jp.get(k, {}), "score": score(jp.get(k, {}), sig[k])}
        db.execute("INSERT OR REPLACE INTO _confidence VALUES (?, ?, ?, ?, ?)",
                   (t, r["id"], out[k]["score"], json.dumps(out[k]["jev"]), json.dumps(sig[k])))
    db.commit()
    return out


def signal_counts(conf: dict) -> Counter:
    return Counter(s for v in conf.values() for s in v["signals"])
