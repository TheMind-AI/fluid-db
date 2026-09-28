"""Async write-side pass: turn numbers buried in text into typed columns, with the LLM as a one-time compiler.

compile (one LLM call per text column that holds numbers): the model sees sample values and returns extractors, each
    a regex with one named group `v`, a type, and an optional transform (a clock time to seconds, or an ordered list of
    values for ordinal scales such as climbing grades).
apply (code, every row, now and at every future write): run the regexes, fill the new typed columns. Deterministic.
"""
# @ref LLP 0007#typed-columns — the LLM compiles extractors once; code fills columns forever
from __future__ import annotations

import re

from lab.common.llm import chat_json

SCHEMA = {"type": "object", "properties": {"extractors": {"type": "array", "items": {"type": "object", "properties": {
    "column": {"type": "string"},
    "description": {"type": "string"},
    "regex": {"type": "string", "description": "Python regex with exactly one named group (?P<v>...)"},
    "transform": {"type": "string", "enum": ["number", "clock_to_seconds", "ordinal"]},
    "ordinal_values": {"type": "array", "items": {"type": "string"}, "description": "lowest to highest, for ordinal"}},
    "required": ["column", "description", "regex", "transform", "ordinal_values"], "additionalProperties": False}}},
    "required": ["extractors"], "additionalProperties": False}

PROMPT = """Column `{table}.{column}` of a personal database stores free text. Sample values:
{samples}

Propose typed columns that a regex can extract from these values, so they can be summed, compared or sorted in SQL
(distances, durations, amounts, counts, scores, levels). For each: a short snake_case column name with its unit
(e.g. distance_km, duration_s), a one-line description, a Python regex with exactly one named group (?P<v>...), and a
transform: "number" (the group is a number), "clock_to_seconds" (the group is a clock time like 54:48 or 1:02:03), or
"ordinal" (the group is a level on an ordered scale; list every level of that scale from lowest to highest in
ordinal_values, and the stored value becomes its rank). Return an empty list if nothing numeric is worth extracting."""


def text_columns(reader, min_share: float = 0.2) -> list[tuple[str, str]]:
    out = []
    for t in reader.tables:
        m = reader.meta[t]
        for c in m["columns"]:
            if c in m["numbers"] or c == m["time"] or m["dims"].get(c, {}).get("kind") == "link":
                continue
            vals = [r[0] for r in reader.eng.db.execute(f"SELECT \"{c}\" FROM '{t}' WHERE \"{c}\" IS NOT NULL")]
            texts = [v for v in vals if isinstance(v, str)]
            if len(texts) >= 10 and sum(bool(re.search(r"\d", v)) for v in texts) / len(texts) >= min_share \
                    and not all(re.match(r"^\d{4}-\d{2}-\d{2}", v) for v in texts[:20]):
                out.append((t, c))
    return out


def compile_extractors(reader, table: str, column: str, model: str = "openai:gpt-6-luna") -> list[dict]:
    vals = [r[0] for r in reader.eng.db.execute(f"SELECT DISTINCT \"{column}\" FROM '{table}' WHERE \"{column}\" IS NOT NULL LIMIT 60")]
    out, _ = chat_json(model, PROMPT.format(table=table, column=column, samples="\n".join(f"- {v}" for v in vals)),
                       SCHEMA, schema_name="extractors", tag="typing:compile", effort="low", max_tokens=4000)
    good = []
    for x in out["extractors"]:
        try:
            if "v" in re.compile(x["regex"]).groupindex:
                good.append({**x, "table": table, "source": column})
        except re.error:
            pass
    return good


def _convert(x: dict, raw: str):
    if x["transform"] == "clock_to_seconds":
        parts = [int(p) for p in raw.split(":")]
        return sum(p * 60 ** i for i, p in enumerate(reversed(parts)))
    if x["transform"] == "ordinal":
        levels = [v.lower() for v in x["ordinal_values"]]
        return levels.index(raw.lower()) if raw.lower() in levels else None
    try:
        return float(raw.replace(",", "."))
    except ValueError:
        return None


def apply_extractors(reader, extractors: list[dict]) -> dict:
    db, filled = reader.eng.db, {}
    for x in extractors:
        t, col = x["table"], x["column"]
        if col not in [r[1] for r in db.execute(f"PRAGMA table_info('{t}')")]:
            db.execute(f"ALTER TABLE '{t}' ADD COLUMN \"{col}\" REAL")
            db.execute("INSERT OR IGNORE INTO _columns (table_name, column_name, type, description) VALUES (?, ?, ?, ?)",
                       (t, col, "REAL", x["description"] + (" (rank on the scale " + ", ".join(x["ordinal_values"]) + ")"
                                                           if x["transform"] == "ordinal" else "")))
        pat, n = re.compile(x["regex"], re.I), 0
        for rid, text in db.execute(f"SELECT id, \"{x['source']}\" FROM '{t}'").fetchall():
            m = pat.search(str(text or ""))
            v = _convert(x, m.group("v")) if m else None
            if v is not None:
                db.execute(f"UPDATE '{t}' SET \"{col}\" = ? WHERE id = ?", (v, rid))
                n += 1
        filled[f"{t}.{col}"] = n
    db.commit()
    return filled
