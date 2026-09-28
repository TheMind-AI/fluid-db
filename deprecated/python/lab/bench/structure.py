"""Is the generated database itself correct? (not just the answers)

For every finished run:
  * deterministic checks on the SQLite file: broken `*_id` links (incl. unresolved "@ref"
    placeholders), values that don't match their declared DATE/DATETIME/number type,
    duplicate people, empty tables, fill rate;
  * a fact audit (life_stream only): Claude Opus 5.5 reads the whole database and checks
    53 facts that must be stored correctly plus 5 things that must NOT be there, and whether
    each fact is stored structurally (dedicated columns) or only inside free text.

  .venv/bin/python -m lab.bench.structure
"""
# @ref LLP 0002.000 — a round-1 bench
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from concurrent.futures import ThreadPoolExecutor

from lab.common.llm import LAB_DIR, chat_json

RUNS = LAB_DIR / "runs"
OUT = LAB_DIR / "results"
META = {"_log", "_tables", "_columns"}
HIDDEN = {"_src", "_ts"}


def load_db(path) -> dict:
    """{table: {"description", "columns": [(name, type, description)], "rows": [dict]}} for user tables."""
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'") if not r[0].startswith("sqlite_")]
    has_meta = "_tables" in names
    out = {}
    for t in names:
        if t in META:
            continue
        desc = ""
        col_desc = {}
        if has_meta:
            row = db.execute("SELECT description FROM _tables WHERE name=?", (t,)).fetchone()
            desc = row[0] if row else ""
            col_desc = {r[0]: (r[1], r[2]) for r in db.execute(
                "SELECT column_name, type, description FROM _columns WHERE table_name=?", (t,))}
        cols = []
        for r in db.execute(f"PRAGMA table_info('{t}')"):
            if r["name"] in HIDDEN:
                continue
            ctype, cdesc = col_desc.get(r["name"], (r["type"], ""))
            cols.append((r["name"], (ctype or r["type"] or "").upper(), cdesc or ""))
        rows = [{k: r[k] for k in r.keys() if k not in HIDDEN} for r in db.execute(f"SELECT * FROM '{t}'")]
        out[t] = {"description": desc, "columns": cols, "rows": rows}
    return out


# ------------------------------------------------------------ deterministic checks
def target_table(col: str, tables: list[str]) -> str | None:
    prefix = col[:-3].lower()
    lower = {t.lower(): t for t in tables}
    cands = [prefix, prefix + "s", prefix + "es", prefix[:-1] + "ies" if prefix.endswith("y") else None,
             "people" if prefix.endswith("person") else None,
             prefix.split("_")[-1] + "s", prefix.split("_")[-1] + "es"]
    for c in cands:
        if c and c in lower:
            return lower[c]
    return None


DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2})?)?$")


def type_ok(ctype: str, v) -> bool:
    if v is None:
        return True
    if ctype == "DATE":
        return isinstance(v, str) and bool(DATE.match(v))
    if ctype == "DATETIME":
        return isinstance(v, str) and bool(DATETIME.match(v))
    if ctype in ("INTEGER", "REAL", "NUMERIC", "FLOAT", "DECIMAL"):
        return isinstance(v, (int, float))
    if ctype == "BOOLEAN":
        return v in (0, 1, True, False)
    return True


def norm(name: str) -> str:
    name = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    name = re.sub(r"\b(dr|mr|mrs|ms)\.?\s+", "", name)
    return re.sub(r"[^a-z ]", "", name).strip()


def duplicate_people(tables: dict) -> list[str]:
    dups = []
    for t, v in tables.items():
        if not re.search(r"people|person|contact|friend", t.lower()):
            continue
        names = []
        for r in v["rows"]:
            n = r.get("name") or r.get("full_name") or " ".join(str(r.get(k) or "") for k in ("first_name", "last_name")).strip()
            if n:
                names.append((r.get("id"), norm(n)))
        for i, (id1, a) in enumerate(names):
            for id2, b in names[i + 1:]:
                if not a or not b:
                    continue
                if a == b or (" " not in a and b.split()[0] == a) or (" " not in b and a.split()[0] == b):
                    dups.append(f"{t}#{id1} '{a}' ~ {t}#{id2} '{b}'")
    return dups


def deterministic(tables: dict) -> dict:
    names = list(tables)
    n_cols = sum(len([c for c in v["columns"] if c[0] != "id"]) for v in tables.values())
    n_rows = sum(len(v["rows"]) for v in tables.values())
    cells = filled = 0
    fk_checked = fk_broken = unresolved_refs = 0
    broken_examples = []
    bad_types = 0
    bad_type_examples = []
    ids = {t: {r.get("id") for r in v["rows"]} for t, v in tables.items()}
    for t, v in tables.items():
        for r in v["rows"]:
            for (c, ctype, _) in v["columns"]:
                if c == "id":
                    continue
                cells += 1
                val = r.get(c)
                filled += val is not None
                if val is None:
                    continue
                if isinstance(val, str) and val.startswith("@"):
                    unresolved_refs += 1
                    broken_examples.append(f"{t}.{c}={val!r} (unresolved reference)")
                    continue
                if c.lower().endswith("_id"):
                    tgt = target_table(c, names)
                    if tgt:
                        fk_checked += 1
                        if not isinstance(val, int) or val not in ids[tgt]:
                            fk_broken += 1
                            broken_examples.append(f"{t}.{c}={val!r} -> no {tgt} row")
                if not type_ok(ctype, val):
                    bad_types += 1
                    if len(bad_type_examples) < 6:
                        bad_type_examples.append(f"{t}.{c} {ctype} = {val!r}")
    return {
        "tables": len(tables), "columns": n_cols, "rows": n_rows,
        "empty_tables": [t for t, v in tables.items() if not v["rows"]],
        "fill_rate": round(filled / cells, 3) if cells else None,
        "fk_checked": fk_checked, "fk_broken": fk_broken, "unresolved_refs": unresolved_refs,
        "broken_examples": broken_examples[:6],
        "type_violations": bad_types, "type_examples": bad_type_examples,
        "duplicate_people": duplicate_people(tables),
    }


# ------------------------------------------------------------------ fact audit
AUDIT_SCHEMA = {"type": "object", "properties": {
    "expect": {"type": "array", "items": {"type": "object", "properties": {
        "id": {"type": "string"},
        "status": {"type": "string", "enum": ["correct", "partial", "missing", "wrong"]},
        "form": {"type": "string", "enum": ["structured", "free_text", "none"]},
        "where": {"type": "string"},
        "note": {"type": "string"}},
        "required": ["id", "status", "form", "where", "note"], "additionalProperties": False}},
    "forbid": {"type": "array", "items": {"type": "object", "properties": {
        "id": {"type": "string"},
        "violated": {"type": "boolean"},
        "evidence": {"type": "string"}},
        "required": ["id", "violated", "evidence"], "additionalProperties": False}}},
    "required": ["expect", "forbid"], "additionalProperties": False}

AUDIT_PROMPT = """You are auditing a database that an AI system built automatically from a user's chat messages.
Below is the complete database (every table, its columns with types and descriptions, every row).

For EACH expected fact decide:
- status: "correct" = the database states it accurately and a query could find it; "partial" = some parts are stored but details are missing or slightly off; "missing" = not stored; "wrong" = stored with a wrong value, or an outdated value is still stored as if current (e.g. an old phone number in the main phone column).
- form: "structured" = the key values live in dedicated columns/rows (e.g. a phone column, a date column, an amount column, a row per session); "free_text" = the fact only appears inside a free-text column such as notes/description/details; "none" if missing.
- where: table#id (and column) where you found it, or "".
For EACH forbidden item decide whether the database violates it, citing table#id as evidence.
Be strict and literal; do not give credit for facts you cannot point to.

EXPECTED FACTS:
{expect}

FORBIDDEN:
{forbid}

DATABASE:
{db}"""


def db_text(tables: dict) -> str:
    parts = []
    for t, v in tables.items():
        parts.append(f"TABLE {t}: {v['description']}")
        parts.append("  columns: " + ", ".join(f"{c} {ty}" + (f" ({d})" if d else "") for c, ty, d in v["columns"]))
        parts.extend(f"  {t}#{r.get('id', '?')} {json.dumps(r, ensure_ascii=False, default=str)}" for r in v["rows"])
    return "\n".join(parts)


def audit(tables: dict, facts: dict) -> dict:
    prompt = AUDIT_PROMPT.format(
        expect="\n".join(f"{f['id']}: {f['fact']}" for f in facts["expect"]),
        forbid="\n".join(f"{f['id']}: {f['fact']}" for f in facts["forbid"]),
        db=db_text(tables))
    out, _ = chat_json("anthropic:claude-opus-5-5", prompt, AUDIT_SCHEMA, schema_name="audit", tag="audit",
                       effort="medium", max_tokens=32000)
    st = [e["status"] for e in out["expect"]]
    form = [e["form"] for e in out["expect"] if e["status"] in ("correct", "partial")]
    return {
        "correct": st.count("correct"), "partial": st.count("partial"), "missing": st.count("missing"),
        "wrong": st.count("wrong"), "n": len(st),
        "fact_score": round((st.count("correct") + 0.5 * st.count("partial")) / len(st), 3),
        "structured_share": round(form.count("structured") / len(form), 3) if form else None,
        "forbid_violations": [f["id"] for f in out["forbid"] if f["violated"]],
        "details": out,
    }


def main():
    facts = json.loads((LAB_DIR / "datasets" / "life_stream_facts.json").read_text())
    runs = sorted(p for p in RUNS.iterdir() if (p / "db.sqlite").exists())

    def one(run_dir):
        tables = load_db(run_dir / "db.sqlite")
        res = {"run": run_dir.name, **deterministic(tables)}
        if run_dir.name.endswith("life_stream"):
            res["audit"] = audit(tables, facts)
        return res

    with ThreadPoolExecutor(6) as pool:
        results = list(pool.map(one, runs))
    (OUT / "bench_structure.json").write_text(json.dumps(results, indent=1, ensure_ascii=False, default=str))
    print("| run | tables | rows | fill | broken links | type errors | dup people | facts ok | partial | missing | wrong | structured | forbidden hit |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        a = r.get("audit")
        broken = f"{r['fk_broken'] + r['unresolved_refs']}/{r['fk_checked'] + r['unresolved_refs']}"
        print(f"| {r['run']} | {r['tables']} | {r['rows']} | {r['fill_rate']} | {broken} | {r['type_violations']} | "
              f"{len(r['duplicate_people'])} | " + (f"{a['correct']}/{a['n']} | {a['partial']} | {a['missing']} | {a['wrong']} | "
              f"{a['structured_share']} | {','.join(a['forbid_violations']) or '-'} |" if a else "| | | | | |"))


if __name__ == "__main__":
    main()
