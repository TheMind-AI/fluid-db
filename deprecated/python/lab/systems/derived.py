"""Derived columns: a column computed for every row from the row itself and the messages it came from.

A spec (written by the schema advisor, stored in `_derived`):
    {"table": "expenses", "column": "kind", "description": "...", "values": ["coffee", "lunch", ...],
     "rules": [{"column": "description", "regex": "(?i)\\blunch", "value": "lunch"}, ...]}
A rule's column may be "_message": the text of the row's source messages (`_src` -> `_log`). That is what lets a new
column be filled for facts that were said but never stored.

Deciding a row's value, first decision wins:
    1. rules, in order (code)
    2. association: the value >= 80% of decided rows with the same merchant/place/name have, >= 3 of them (code)
    3. Jev: a closed Choice among the values, reading the row and its message (backfill and async pass only)
At write time the engine runs 1-2 only (`derive_row`), so writes stay deterministic; rows left empty wait for the
async pass.
"""
# @ref LLP 0003#migrations — derive_column: rules, then association, then Jev for leftovers
from __future__ import annotations

import json
import random
import re
import unicodedata
from collections import Counter, defaultdict

from lab.common import jev

ANCHORS = ("merchant", "place", "place_id", "store", "vendor", "location", "name", "title")
DDL = """
CREATE TABLE IF NOT EXISTS _derived (table_name TEXT, column_name TEXT, spec TEXT, PRIMARY KEY (table_name, column_name));
CREATE TABLE IF NOT EXISTS _migrations (id INTEGER PRIMARY KEY, ts TEXT, op TEXT, table_name TEXT, column_name TEXT,
                                        spec TEXT, evidence TEXT, stats TEXT);
"""


def fold(text) -> str:
    return unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode().lower()


def ensure(db):
    db.executescript(DDL)


def specs(db) -> dict[str, list[dict]]:
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name = '_derived'").fetchone():
        return {}
    out: dict = defaultdict(list)
    for t, spec in db.execute("SELECT table_name, spec FROM _derived"):
        out[t].append(json.loads(spec))
    return dict(out)


def message(db, src) -> str:
    ids = [int(i) for i in re.findall(r"\d+", str(src or ""))]
    if not ids:
        return ""
    rows = db.execute(f"SELECT text FROM _log WHERE id IN ({','.join('?' * len(ids))}) ORDER BY id", ids).fetchall()
    return " | ".join(str(r[0]) for r in rows)[:600]


def in_scope(spec: dict, row: dict) -> bool:
    sc = spec.get("scope")
    return not sc or (row.get(sc["column"]) is not None and re.search(sc["regex"], str(row[sc["column"]])) is not None)


def rule_value(spec: dict, row: dict, msg: str) -> str | None:
    for r in spec["rules"]:
        text = msg if r["column"] == "_message" else row.get(r["column"])
        if text is not None and re.search(r["regex"], str(text)):
            return r["value"]
    return None


def _anchor_counts(rows: list[dict], decided: dict[int, str]) -> dict:
    counts: dict = defaultdict(Counter)
    for r in rows:
        if r["id"] in decided:
            for a in ANCHORS:
                if r.get(a) is not None:
                    counts[(a, r[a])][decided[r["id"]]] += 1
    return counts


def associated(counts: dict, row: dict, column: str, min_rows: int = 3, share: float = 0.8) -> str | None:
    for a in ANCHORS:
        if a != column and row.get(a) is not None:
            c = counts.get((a, row[a]))
            if c:
                (top, n), total = c.most_common(1)[0], sum(c.values())
                if total >= min_rows and n >= share * total:
                    return top
    return None


def _row_text(row: dict, column: str) -> str:
    shown = {k: v for k, v in row.items() if k not in ("id", "_src", "_ts", column) and v is not None}
    return json.dumps(shown, ensure_ascii=False)[:300]


def jev_values(spec: dict, items: list[tuple[dict, str]], chunk: int = 8, min_conf: float = 0.5) -> dict[int, str | None]:
    """A closed Choice per row among the spec's values (or none of them), 8 rows per request."""
    opts = {f"v{i}": v for i, v in enumerate(spec["values"])}
    opts["none"] = "None of these values fits"
    jobs = []
    for i in range(0, len(items), chunk):
        state, qs = {"column": f"{spec['column']}: {spec['description']}"}, {}
        for row, msg in items[i:i + chunk]:
            key = f"row_{row['id']}"
            state[key] = f"{_row_text(row, spec['column'])} | the user's message: {msg}"
            qs[key] = jev.choice(f"Which value of `column` fits `{key}`?", opts)
        jobs.append((state, qs))
    out: dict = {}
    for answers in jev.ask_many(jobs, workers=8, tag="derive:jev"):
        for key, a in answers.items():
            ok = a["choice"] != "none" and a["confidence"] >= min_conf
            out[int(key[4:])] = opts[a["choice"]] if ok else None
    return out


def decide(db, table: str, spec: dict, use_jev: bool = True, spot_check: int = 40, seed: int = 7) -> dict:
    """Every row's value (nothing written). Returns {"values": {row_id: value}, "stats": {...}}."""
    every = [dict(r) for r in db.execute(f"SELECT * FROM '{table}'")]
    rows = [r for r in every if in_scope(spec, r)]   # rows outside the scope stay empty
    msgs = {r["id"]: message(db, r.get("_src")) for r in rows}
    by_rule = {r["id"]: v for r in rows if (v := rule_value(spec, r, msgs[r["id"]])) is not None}
    counts = _anchor_counts(rows, by_rule)
    by_assoc = {r["id"]: v for r in rows if r["id"] not in by_rule
                and (v := associated(counts, r, spec["column"])) is not None}
    left = [r for r in rows if r["id"] not in by_rule and r["id"] not in by_assoc]
    by_jev = {k: v for k, v in jev_values(spec, [(r, msgs[r["id"]]) for r in left]).items() if v} if use_jev and left else {}
    values = {**by_rule, **by_assoc, **by_jev}
    counts = _anchor_counts(rows, values)   # association again, now with Jev's decisions as evidence too
    by_assoc2 = {r["id"]: v for r in rows if r["id"] not in values and (v := associated(counts, r, spec["column"])) is not None}
    values.update(by_assoc2)
    by_assoc.update(by_assoc2)
    stats = {"rows": len(every), "in_scope": len(rows), "rules": len(by_rule), "association": len(by_assoc), "jev": len(by_jev),
             "empty": len(rows) - len(values), "distribution": dict(Counter(values.values()).most_common())}
    if use_jev and spot_check and by_rule:   # an independent second opinion on what the rules decided
        sample = random.Random(seed).sample(sorted(by_rule), min(spot_check, len(by_rule)))
        rows_by_id = {r["id"]: r for r in rows}
        second = jev_values(spec, [(rows_by_id[i], msgs[i]) for i in sample])
        agree = [second.get(i) == by_rule[i] for i in sample]
        stats["spot_check"] = {"n": len(sample), "agree": round(sum(agree) / len(sample), 3),
                               "disagreements": [{"row": _row_text(rows_by_id[i], spec["column"])[:120],
                                                  "rules": by_rule[i], "jev": second.get(i)}
                                                 for i, ok in zip(sample, agree) if not ok][:6]}
    return {"values": values, "stats": stats}


def write(eng, table: str, spec: dict, values: dict[int, str]):
    """Add the column, fill it, and register the spec so every future insert is derived too."""
    ensure(eng.db)
    col = eng.ensure_column(table, spec["column"], "TEXT", spec["description"])
    eng.db.executemany(f"UPDATE '{table}' SET \"{col}\" = ? WHERE id = ?", [(v, i) for i, v in values.items()])
    eng.db.execute("INSERT OR REPLACE INTO _derived VALUES (?, ?, ?)", (table, col, json.dumps(spec, ensure_ascii=False)))
    eng.db.commit()
    eng.derived = specs(eng.db)


def derive_row(eng, table: str, row_id: int, log_id: int):
    """Write time: rules, then association, for one new row (code only)."""
    for spec in eng.derived.get(table, []):
        row = dict(eng.db.execute(f"SELECT * FROM '{table}' WHERE id = ?", (row_id,)).fetchone())
        if row.get(spec["column"]) is not None or not in_scope(spec, row):
            continue
        msg = " | ".join(r[0] for r in eng.db.execute("SELECT text FROM _log WHERE id = ?", (log_id,)))
        value = rule_value(spec, row, msg)
        if value is None:
            for a in ANCHORS:
                if a != spec["column"] and row.get(a) is not None:
                    got = eng.db.execute(f"SELECT \"{spec['column']}\", COUNT(*) FROM '{table}' WHERE \"{a}\" = ? AND "
                                         f"\"{spec['column']}\" IS NOT NULL GROUP BY 1 ORDER BY 2 DESC", (row[a],)).fetchall()
                    total = sum(n for _, n in got)
                    if got and total >= 3 and got[0][1] >= 0.8 * total:
                        value = got[0][0]
                        break
        if value is not None:
            eng.db.execute(f"UPDATE '{table}' SET \"{spec['column']}\" = ? WHERE id = ?", (value, row_id))


# ------------------------------------------------------------------------------------------------ canonical values
def merge_candidates(counts: dict[str, int]) -> list[tuple[str, str]]:
    """Pairs of values that may name the same thing: equal up to accents/case, or one's words inside the other's."""
    vals, out = sorted(counts), []
    for i, a in enumerate(vals):
        for b in vals[i + 1:]:
            fa, fb = re.sub(r"\s+", " ", fold(a)).strip(), re.sub(r"\s+", " ", fold(b)).strip()
            short, long = sorted((fa, fb), key=len)
            if fa == fb or (len(short) >= 4 and re.search(rf"(?<![\w']){re.escape(short)}(?![\w'])", long)):
                out.append((a, b))
    return out


def canonicalize(eng, table: str, column: str, threshold: float = 0.8, examples: int = 3) -> dict:
    """Merge spelling variants of a column's values (Jev confirms each pair). Every change goes to _history."""
    db = eng.db
    counts = dict(db.execute(f"SELECT \"{column}\", COUNT(*) FROM '{table}' WHERE \"{column}\" IS NOT NULL GROUP BY 1"))
    pairs = merge_candidates(counts)
    if not pairs:
        return {"pairs": 0, "merged": []}

    def sample(v):   # the user's own words behind each spelling are the best evidence ("Onesip" came from "Onesip Coffee")
        rows = db.execute(f"SELECT _src FROM '{table}' WHERE \"{column}\" = ? LIMIT ?", (v, examples)).fetchall()
        return [message(db, r[0])[:120] for r in rows]

    same = jev.noul(f"`a` and `b` are two stored spellings of a {column}. Judging by the user's messages that produced "
                    f"them (`a_messages`, `b_messages`), do they name the same {column}?")
    checks = jev.ask_many([({"a": a, "b": b, "a_messages": sample(a), "b_messages": sample(b)}, {"same": same})
                           for a, b in pairs], workers=8, tag="canonicalize:jev")
    parent = {v: v for v in counts}

    def root(v):
        while parent[v] != v:
            v = parent[v]
        return v

    scored = []
    for (a, b), c in zip(pairs, checks):
        p = c["same"]["noul"]
        scored.append({"a": a, "b": b, "p": round(p, 3)})
        if p >= threshold:
            parent[root(a)] = root(b)
    clusters: dict = defaultdict(list)
    for v in counts:
        clusters[root(v)].append(v)
    merged = []
    for group in clusters.values():
        if len(group) < 2:
            continue
        canon = max(group, key=lambda v: (not str(v).isupper(), counts[v], -len(str(v))))
        for v in group:
            if v == canon:
                continue
            ids = [r[0] for r in db.execute(f"SELECT id FROM '{table}' WHERE \"{column}\" = ?", (v,))]
            db.executemany("INSERT INTO _history (table_name, row_id, column_name, old_value, new_value, ts, log_id, op) "
                           "VALUES (?, ?, ?, ?, ?, datetime('now'), NULL, 'canonicalize')",
                           [(table, i, column, v, canon) for i in ids])
            db.execute(f"UPDATE '{table}' SET \"{column}\" = ? WHERE \"{column}\" = ?", (canon, v))
            merged.append({"from": v, "to": canon, "rows": len(ids)})
    db.commit()
    return {"pairs": len(pairs), "checked": scored, "merged": merged}
