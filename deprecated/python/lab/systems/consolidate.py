"""Offline consolidation ("sleep") for a FluidDB v2.2 database.

1. recover dropped facts: every logged message that the gate did not skip must leave a trace
   (a row whose _src lists it, or a _history entry). Messages without one are re-planned.
2. move misplaced rows: when the same kind of thing ended up in two tables, a model proposes typed
   migrations (source table + condition + column mapping) and the engine applies them with history.
3. merge duplicate entities: code proposes look-alike pairs, Jev confirms "same entity?",
   and the engine merges deterministically (repoint every *_id link, fill empty fields,
   delete the duplicate, record the merge in _history).
"""
# @ref LLP 0007#sleep — recover dropped facts, move misplaced rows, merge duplicates
from __future__ import annotations

import itertools
import json
import re
import unicodedata

from lab.common import jev
from lab.systems.fluid_v2 import FluidV2

ENTITY_TABLE = re.compile(r"people|person|contact|place|organization|merchant|restaurant|venue|compan|store|gym")
SAME = {"same": jev.noul("Do `a` and `b` describe the same real-world person, place or organization?")}


def norm(s) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9 ]", " ", s).strip()


def referenced_log_ids(engine) -> set[int]:
    db = engine.db
    ids: set[int] = set()
    for t in engine.tables():
        for (src,) in db.execute(f"SELECT _src FROM '{t}'"):
            ids.update(json.loads(src or "[]"))
    ids.update(r[0] for r in db.execute("SELECT DISTINCT log_id FROM _history WHERE log_id IS NOT NULL"))
    return ids


def dropped_messages(engine, skipped_log_ids: set[int]) -> list[tuple[int, str, str]]:
    ref = referenced_log_ids(engine)
    return [(i, ts, text) for i, ts, text in engine.db.execute("SELECT id, ts, text FROM _log ORDER BY id")
            if i not in ref and i not in skipped_log_ids]


def replan(system: FluidV2, log_id: int, ts: str, text: str) -> dict:
    """Plan one logged message again (with the current database as context) without logging it twice."""
    orig_log = system.engine.log
    system.engine.log = lambda _ts, _text: log_id
    try:
        return system.remember({"id": f"replan-{log_id}", "ts": ts, "text": text})
    finally:
        system.engine.log = orig_log


def target_table(engine, col: str) -> str | None:
    if not col.endswith("_id") or col == "id":
        return None
    tables = engine.tables()
    parts = col[:-3].split("_")
    for i in range(len(parts)):
        stem = "_".join(parts[i:])
        for cand in (stem + "s", stem + "es", stem[:-1] + "ies" if stem.endswith("y") else "", stem,
                     "people" if stem == "person" else ""):
            if cand and cand in tables:
                return cand
    return None


def candidate_pairs(engine) -> list[tuple[str, dict, dict]]:
    out = []
    for t in engine.tables():
        if not ENTITY_TABLE.search(t):
            continue
        rows = [r for r in engine.rows(t) if r.get("name")]
        for a, b in itertools.combinations(rows, 2):
            na, nb = norm(a["name"]), norm(b["name"])
            if not na or not nb:
                continue
            ta, tb = na.split(), nb.split()
            if na == nb or na in nb or nb in na or ta[0] == tb[0]:
                out.append((t, a, b))
    return out


def merge(engine, table: str, keep: dict, dup: dict, ts: str) -> int:
    """Repoint every link to `dup` at `keep`, copy missing fields, delete `dup`. Returns links moved."""
    db, moved = engine.db, 0
    for t in engine.tables():
        for c in engine.columns(t):
            if target_table(engine, c["name"]) == table:
                moved += db.execute(f"UPDATE '{t}' SET '{c['name']}' = ? WHERE \"{c['name']}\" = ?", (keep["id"], dup["id"])).rowcount
    fill = {k: v for k, v in dup.items() if k != "id" and keep.get(k) in (None, "") and v not in (None, "")}
    if fill:
        sets = ", ".join(f"'{k}' = ?" for k in fill)
        db.execute(f"UPDATE '{table}' SET {sets} WHERE id = ?", [*fill.values(), keep["id"]])
    db.execute("INSERT INTO _history (table_name, row_id, column_name, old_value, new_value, ts, log_id, op) "
               "VALUES (?, ?, '*', ?, ?, ?, NULL, 'merge')", (table, dup["id"], json.dumps(dup, ensure_ascii=False), f"merged into #{keep['id']}", ts))
    db.execute(f"DELETE FROM '{table}' WHERE id = ?", (dup["id"],))
    db.commit()
    return moved


def _sources(engine, table: str, row_id: int, limit: int = 3) -> list[str]:
    """What the user said about a row: the messages it came from (for nicknames like "Kuba" or "Dave")."""
    src = engine.db.execute(f"SELECT _src FROM '{table}' WHERE id = ?", (row_id,)).fetchone()
    ids = [int(x) for x in re.findall(r"\d+", str(src[0] if src else ""))][:limit]
    # also a few messages whose rows link to this one
    for t in engine.tables():
        for c in engine.columns(t):
            if target_table(engine, c["name"]) == table and len(ids) < 2 * limit:
                for (s2,) in engine.db.execute(f"SELECT _src FROM '{t}' WHERE \"{c['name']}\" = ? LIMIT 2", (row_id,)):
                    ids += [int(x) for x in re.findall(r"\d+", str(s2 or ""))][:1]
    texts = [engine.db.execute("SELECT text FROM _log WHERE id = ?", (i,)).fetchone() for i in ids]
    return [t[0][:200] for t in texts if t]


# @ref LLP 0007#dedupe — Jev merges at >= 0.8; 0.5-0.8 should become questions for the user
def dedupe(engine, ts: str, threshold: float = 0.8, all_pairs_max_rows: int = 0) -> list[dict]:
    """Merge rows Jev says are the same entity. Small entity tables (<= all_pairs_max_rows) compare every pair, with
    the messages behind each row, so nicknames ("Kuba", "Dave") can match full names."""
    pairs = candidate_pairs(engine)
    if all_pairs_max_rows:
        seen = {(t, a["id"], b["id"]) for t, a, b in pairs}
        for t in engine.tables():
            rows = [r for r in engine.rows(t) if r.get("name")] if ENTITY_TABLE.search(t) else []
            if len(rows) <= all_pairs_max_rows:
                pairs += [(t, a, b) for a, b in itertools.combinations(rows, 2) if (t, a["id"], b["id"]) not in seen]
    ctx = {}
    if all_pairs_max_rows:
        ctx = {(t, r["id"]): _sources(engine, t, r["id"]) for t, a, b in pairs for r in (a, b) if (t, r["id"]) not in ctx}
    answers = jev.ask_many([({"a": a, "b": b, "table": t, "said_about_a": ctx.get((t, a["id"]), []),
                              "said_about_b": ctx.get((t, b["id"]), [])} if ctx else {"a": a, "b": b, "table": t}, SAME)
                            for t, a, b in pairs], workers=16, tag="sleep:same")
    merged, gone = [], set()
    for (t, a, b), ans in sorted(zip(pairs, answers), key=lambda x: -x[1]["same"]["noul"]):
        p = ans["same"]["noul"]
        if p < threshold or (t, a["id"]) in gone or (t, b["id"]) in gone:
            continue
        keep, dup = (a, b) if len([v for v in a.values() if v]) >= len([v for v in b.values() if v]) else (b, a)
        moved = merge(engine, t, keep, dup, ts)
        gone.add((t, dup["id"]))
        merged.append({"table": t, "keep": keep.get("name"), "dup": dup.get("name"), "p": round(p, 2), "links_moved": moved})
    return merged


# ------------------------------------------------------------ misplaced rows
MIGRATION_SCHEMA = {"type": "object", "properties": {"migrations": {"type": "array", "items": {
    "type": "object", "properties": {
        "reason": {"type": "string"},
        "source_table": {"type": "string"},
        "where": {"type": "string", "description": "SQL condition selecting the misplaced rows in source_table"},
        "target_table": {"type": "string"},
        "mapping": {"type": "array", "items": {"type": "object", "properties": {
            "target_column": {"type": "string"},
            "source_column": {"type": ["string", "null"]},
            "constant": {"type": ["string", "number", "null"]}},
            "required": ["target_column", "source_column", "constant"], "additionalProperties": False}}},
    "required": ["reason", "source_table", "where", "target_table", "mapping"], "additionalProperties": False}}},
    "required": ["migrations"], "additionalProperties": False}

MIGRATION_PROMPT = """You maintain a personal database that an AI builds from chat messages. Over time the same kind of
thing sometimes ends up in two tables (for example some meetings stored in `tasks` while others are in `events`).
Find rows that are stored in the wrong table and should move into the table that holds their kind of thing.
Only propose a migration when the target table is clearly the right home; do not merge tables that hold different
kinds of things. For each migration give a SQL condition that selects exactly the misplaced rows, and map every useful
source column to a target column (or give a constant, e.g. activity = 'climbing'). Return [] if nothing is misplaced.

DATABASE (catalog with example rows):
{catalog}"""


def migrate_misplaced(engine, model: str, ts: str, tag: str = "sleep:migrate") -> list[dict]:
    from lab.common.llm import chat_json
    plan, _ = chat_json(model, MIGRATION_PROMPT.format(catalog=engine.catalog(samples=6)), MIGRATION_SCHEMA,
                        schema_name="migrations", tag=tag, effort="medium", max_tokens=16000)
    done, db = [], engine.db
    for m in plan["migrations"]:
        src, dst = m["source_table"], m["target_table"]
        if src not in engine.tables() or dst not in engine.tables() or src == dst:
            done.append({**m, "applied": 0, "skipped": "unknown table"})
            continue
        try:
            rows = db.execute(f"SELECT * FROM '{src}' WHERE {m['where']}").fetchall()
        except Exception as e:
            done.append({**m, "applied": 0, "skipped": f"bad condition: {e}"})
            continue
        moved = 0
        for r in rows:
            values = {}
            for mp in m["mapping"]:
                v = r[mp["source_column"]] if mp["source_column"] and mp["source_column"] in r.keys() else mp["constant"]
                if v is not None:
                    values[engine.ensure_column(dst, mp["target_column"])] = v
            cols = list(values) + ["_src", "_ts"]
            db.execute(f"INSERT INTO '{dst}' ({', '.join(repr(c) for c in cols)}) VALUES ({', '.join('?' for _ in cols)})",
                       [*values.values(), r["_src"], r["_ts"]])
            snap = {k: r[k] for k in r.keys() if k not in ("_src", "_ts") and r[k] is not None}
            db.execute("INSERT INTO _history (table_name, row_id, column_name, old_value, new_value, ts, log_id, op) "
                       "VALUES (?, ?, '*', ?, ?, ?, NULL, 'migrate')", (src, r["id"], json.dumps(snap, ensure_ascii=False), f"moved to {dst}", ts))
            db.execute(f"DELETE FROM '{src}' WHERE id = ?", (r["id"],))
            moved += 1
        db.commit()
        done.append({**m, "applied": moved})
    return done
