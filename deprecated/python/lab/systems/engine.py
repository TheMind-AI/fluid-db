"""Deterministic storage engine for FluidDB v2.

The model never writes SQL on the write path. It returns typed operations and
this engine applies them: it creates tables and columns on demand, coerces
values, resolves references between rows inserted in the same batch, and keeps
provenance. Every raw input is kept in `_log`, so nothing is ever lost even if
the structured view gets something wrong.
"""
# @ref LLP 0004 — the storage engine: raw log, typed operations, catalog, provenance, history
from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
from collections import Counter
from functools import wraps

from lab.systems import derived

RESERVED = {"id", "_src", "_ts"}
CURRENCY = {"kč": "CZK", "kc": "CZK", "czk": "CZK", ",-": "CZK", "$": "USD", "usd": "USD", "us$": "USD", "dollar": "USD",
            "dollars": "USD", "€": "EUR", "eur": "EUR", "euro": "EUR", "euros": "EUR", "£": "GBP", "gbp": "GBP"}
TYPES = {"TEXT", "INTEGER", "REAL", "BOOLEAN", "DATE", "DATETIME"}


def ident(name: str) -> str:
    """Normalise a model-provided name into a safe snake_case SQL identifier."""
    name = re.sub(r"[^a-zA-Z0-9_]+", "_", str(name).strip()).strip("_").lower()
    if not name:
        name = "field"
    if name[0].isdigit():
        name = f"f_{name}"
    return name[:60]


def sql_type(t: str | None) -> str:
    t = (t or "TEXT").upper()
    return t if t in TYPES else "TEXT"


def locked(fn):
    """The engine's connection is shared; QA runs questions on several threads."""
    @wraps(fn)
    def wrapper(self, *args, **kwargs):
        with self.lock:
            return fn(self, *args, **kwargs)
    return wrapper


class Engine:
    def __init__(self, path: str, strict: bool = False, normalize: bool = False, forget_links: str = "cascade"):
        """strict (v2.2): keep a `_history` of overwritten/deleted values, support `forget`
        (hard delete + erase terms from the raw log), and reject dangling `@ref`s.
        forget_links: what a forget does to rows that link to the forgotten row: "cascade" deletes them (v2.3),
        "redact" (LLP 0017.000) nulls the link and keeps them."""
        self.path = path
        self.strict = strict
        self.normalize = normalize      # v2.3: ISO currency codes, value vocabulary in the catalog, forget cascade
        self.forget_links = forget_links
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS _log (id INTEGER PRIMARY KEY, ts TEXT, text TEXT);
            CREATE TABLE IF NOT EXISTS _tables (name TEXT PRIMARY KEY, description TEXT);
            CREATE TABLE IF NOT EXISTS _columns (table_name TEXT, column_name TEXT, type TEXT, description TEXT,
                                                 PRIMARY KEY (table_name, column_name));
        """)
        if strict:
            self.db.execute("""CREATE TABLE IF NOT EXISTS _history (id INTEGER PRIMARY KEY, table_name TEXT, row_id INTEGER,
                               column_name TEXT, old_value TEXT, new_value TEXT, ts TEXT, log_id INTEGER, op TEXT)""")
        self.db.commit()
        self.derived = derived.specs(self.db)   # columns the schema advisor added: filled on every insert

    # ------------------------------------------------------------------ log
    # @ref LLP 0004#raw-log — every input is logged before anything else happens
    @locked
    def log(self, ts: str, text: str) -> int:
        cur = self.db.execute("INSERT INTO _log (ts, text) VALUES (?, ?)", (ts, text))
        self.db.commit()
        return cur.lastrowid

    # -------------------------------------------------------------- catalog
    @locked
    def tables(self) -> list[str]:
        return [r["name"] for r in self.db.execute("SELECT name FROM _tables ORDER BY name")]

    @locked
    def columns(self, table: str) -> list[dict]:
        cols = {r["column_name"]: dict(r) for r in self.db.execute(
            "SELECT * FROM _columns WHERE table_name = ?", (table,))}
        out = []
        for r in self.db.execute(f"PRAGMA table_info('{table}')"):
            if r["name"] in RESERVED:
                continue
            meta = cols.get(r["name"], {})
            out.append({"name": r["name"], "type": meta.get("type") or r["type"], "description": meta.get("description")})
        return out

    @locked
    def catalog(self, tables: list[str] | None = None, samples: int = 0) -> str:
        lines = []
        for t in tables or self.tables():
            desc = self.db.execute("SELECT description FROM _tables WHERE name = ?", (t,)).fetchone()
            count = self.db.execute(f"SELECT COUNT(*) FROM '{t}'").fetchone()[0]
            lines.append(f"TABLE {t} ({count} rows): {desc['description'] if desc else ''}")
            lines.append("  id INTEGER primary key")
            for c in self.columns(t):
                vocab = ""
                if self.normalize and c["type"] == "TEXT":
                    vals = [r[0] for r in self.db.execute(
                        f"SELECT DISTINCT \"{c['name']}\" FROM '{t}' WHERE \"{c['name']}\" IS NOT NULL LIMIT 26")]
                    if 0 < len(vals) <= 25 and all(len(str(v)) <= 30 for v in vals) and count >= 2 * len(vals):
                        vocab = f" [existing values: {', '.join(map(str, vals))}]"
                lines.append(f"  {c['name']} {c['type']}" + (f" -- {c['description']}" if c['description'] else "") + vocab)
            if samples:
                for row in self.rows(t)[:samples]:
                    lines.append(f"  e.g. {json.dumps(row, ensure_ascii=False)}")
        if self.strict and not tables:
            n = self.db.execute("SELECT COUNT(*) FROM _history").fetchone()[0]
            lines.append(f"TABLE _history ({n} rows): automatic record of every overwritten or deleted value "
                         "(table_name, row_id, column_name, old_value, new_value, ts, op); query it for what something was before")
        return "\n".join(lines) if lines else "(the database is empty)"

    # ----------------------------------------------------------------- rows
    @locked
    def rows(self, table: str) -> list[dict]:
        out = []
        for r in self.db.execute(f"SELECT * FROM '{table}' ORDER BY id"):
            d = {k: r[k] for k in r.keys() if k not in ("_src", "_ts") and r[k] is not None}
            out.append(d)
        return out

    @locked
    def all_rows(self) -> list[dict]:
        """Every row as {"table", "row"} records."""
        return [{"table": t, "row": r} for t in self.tables() for r in self.rows(t)]

    @locked
    def dump_text(self, tables: list[str] | None = None) -> str:
        parts = []
        for t in tables or self.tables():
            parts.append(f"## {t}")
            parts.extend(json.dumps(r, ensure_ascii=False) for r in self.rows(t))
        if self.strict and not tables:
            hist = self.db.execute("SELECT table_name, row_id, column_name, old_value, new_value, ts, op FROM _history ORDER BY id").fetchall()
            if hist:
                parts.append("## _history (earlier values that were overwritten or deleted)")
                parts.extend(f"{h[6]} {h[0]}#{h[1]}.{h[2]}: {h[3]!s} -> {h[4]!s} (at {h[5]})" for h in hist)
        return "\n".join(parts) if parts else "(the database is empty)"

    # ------------------------------------------------------- readable view
    LABEL_COLS = ("name", "title", "full_name", "description", "activity", "activity_type", "type", "subject", "item")

    def target_table(self, col: str, tables: list[str] | None = None) -> str | None:
        """Which table an `*_id` column points to (person_id -> people, origin_place_id -> places, ...)."""
        if not col.endswith("_id") or col == "id":
            return None
        tables = tables if tables is not None else self.tables()
        parts = col[:-3].split("_")
        for i in range(len(parts)):
            stem = "_".join(parts[i:])
            for cand in (stem + "s", stem + "es", stem[:-1] + "ies" if stem.endswith("y") else "", stem,
                         "people" if stem == "person" else ""):
                if cand and cand in tables:
                    return cand
        return None

    @locked
    def readable_text(self) -> str:
        """The database for a model to read: every `*_id` link replaced by the linked row's name, so no one has
        to join by hand. History lines say which entity changed."""
        tables = self.tables()
        rows = {t: self.rows(t) for t in tables}

        def label(t, rid):
            r = next((x for x in rows.get(t, []) if x.get("id") == rid), None)
            if r is None:
                return f"{t}#{rid} (deleted)"
            key = next((c for c in self.LABEL_COLS if r.get(c)), None)
            return f"{r[key]} ({t}#{rid})" if key else f"{t}#{rid}"

        def readable(t, r):
            shown = {}
            for k, v in r.items():
                tgt = self.target_table(k, tables)
                shown[k[:-3] if tgt else k] = label(tgt, v) if tgt and isinstance(v, int) else v
            return {k: v for k, v in shown.items() if k != "id"}

        out, view = [], {}
        for t in tables:
            out.append(f"## {t}")
            for r in rows[t]:
                view[(t, r["id"])] = readable(t, r)
                out.append(f"{t}#{r['id']} " + json.dumps(view[(t, r["id"])], ensure_ascii=False))
        if self.strict:
            hist = self.db.execute("SELECT table_name, row_id, column_name, old_value, new_value, ts, op FROM _history ORDER BY id").fetchall()
            if hist:
                out.append("## _history (earlier values that were overwritten or deleted)")
                for h in hist:
                    ctx = view.get((h[0], h[1]))
                    ctx = " " + json.dumps({k: v for k, v in ctx.items() if k != h[2]}, ensure_ascii=False)[:160] if ctx else ""
                    out.append(f"{h[6]} {h[0]}#{h[1]}{ctx} | {h[2]}: {h[3]!s} -> {h[4]!s} (at {h[5]})")
        return "\n".join(out) if out else "(the database is empty)"

    @locked
    def recent_rows(self, n: int = 10) -> list[dict]:
        """The n most recently written rows across tables (helps with follow-ups like "actually it was 18:00")."""
        out = []
        for t in self.tables():
            for r in self.db.execute(f"SELECT * FROM '{t}' ORDER BY _ts DESC, id DESC LIMIT ?", (n,)):
                out.append((r["_ts"] or "", {"table": t, "row": {k: r[k] for k in r.keys() if k not in ("_src", "_ts") and r[k] is not None}}))
        out.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in out[:n]]

    # ---------------------------------------------------------------- write
    @locked
    def ensure_table(self, table: str, description: str | None = None, columns: list[dict] | None = None):
        table = ident(table)
        if table.startswith("_") or table.startswith("sqlite"):
            table = f"t_{table.lstrip('_')}"
        exists = self.db.execute("SELECT 1 FROM _tables WHERE name = ?", (table,)).fetchone()
        if not exists:
            self.db.execute(f"CREATE TABLE IF NOT EXISTS '{table}' (id INTEGER PRIMARY KEY, _src TEXT, _ts TEXT)")
            self.db.execute("INSERT INTO _tables (name, description) VALUES (?, ?)", (table, description or ""))
        elif description:
            self.db.execute("UPDATE _tables SET description = ? WHERE name = ? AND (description IS NULL OR description = '')",
                            (description, table))
        for c in columns or []:
            self.ensure_column(table, c.get("name"), c.get("type"), c.get("description"))
        return table

    @locked
    def ensure_column(self, table: str, column: str, ctype: str | None = None, description: str | None = None) -> str:
        column = ident(column)
        if column in RESERVED:
            return column
        existing = {r["name"] for r in self.db.execute(f"PRAGMA table_info('{table}')")}
        if column not in existing:
            self.db.execute(f"ALTER TABLE '{table}' ADD COLUMN '{column}' {sql_type(ctype)}")
            self.db.execute("INSERT OR REPLACE INTO _columns VALUES (?, ?, ?, ?)",
                            (table, column, sql_type(ctype), description or ""))
        elif description:
            self.db.execute("UPDATE _columns SET description = ? WHERE table_name = ? AND column_name = ? "
                            "AND (description IS NULL OR description = '')", (description, table, column))
        return column

    @locked
    def apply(self, ops: list[dict], log_id: int, ts: str) -> list[dict]:
        """Apply operations in order. Returns a list of errors (empty when all succeeded)."""
        errors, refs = [], {}
        for op in ops:
            try:
                self._apply_one(op, refs, log_id, ts)
            except Exception as e:
                errors.append({"op": op, "error": f"{type(e).__name__}: {e}"})
        # Erase forgotten terms last, so history written by later ops in the same batch is covered too.
        for op in ops:
            if op.get("op") == "forget" and self.strict:
                for term in op.get("redact") or []:
                    if term and len(term) >= 3:
                        self.db.execute("UPDATE _log SET text = REPLACE(text, ?, '[forgotten]') WHERE text LIKE ?", (term, f"%{term}%"))
                        self.db.execute("DELETE FROM _history WHERE old_value LIKE ? OR new_value LIKE ?", (f"%{term}%", f"%{term}%"))
        self.db.commit()
        return errors

    # @ref LLP 0004#normalization — ISO currencies; @refs resolve only within one batch
    def _resolve(self, value, refs, column: str = ""):
        if self.normalize and isinstance(value, str) and (column == "currency" or column.endswith("_currency")):
            value = CURRENCY.get(value.strip().lower(), value.strip().upper() if len(value.strip()) == 3 else value)
        if isinstance(value, str) and value.startswith("@") and value[1:] in refs:
            return refs[value[1:]]
        if self.strict and isinstance(value, str) and value.startswith("@") and column.endswith("_id"):
            raise ValueError(f"{column}={value!r}: '@' references only work for rows inserted earlier in the same "
                             "batch; use the existing row's numeric id")
        if isinstance(value, bool):
            return int(value)
        return value

    def _apply_one(self, op: dict, refs: dict, log_id: int, ts: str):
        kind = op["op"]
        table = ident(op.get("table") or "")
        if kind in ("create_table", "add_columns"):
            self.ensure_table(table, op.get("description"), op.get("columns") or [])
            return
        known = self.db.execute("SELECT 1 FROM _tables WHERE name = ?", (table,)).fetchone()
        if kind == "forget" and not known:
            known = True  # a forget may only redact the log
        if not known:
            if kind != "insert":
                raise ValueError(f"unknown table {table}")
            table = self.ensure_table(table, op.get("description"))
        values = {}
        for kv in op.get("values") or []:
            col = self.ensure_column(table, kv["column"], _infer_type(kv.get("value")))
            if col not in RESERVED:
                values[col] = self._resolve(kv.get("value"), refs, col)
        if kind == "insert":
            cols = list(values) + ["_src", "_ts"]
            cur = self.db.execute(
                f"INSERT INTO '{table}' ({', '.join(repr(c) for c in cols)}) VALUES ({', '.join('?' for _ in cols)})",
                [*values.values(), json.dumps([log_id]), ts])
            if op.get("ref"):
                refs[str(op["ref"]).lstrip("@")] = cur.lastrowid
            if self.derived.get(table):
                # @ref LLP 0003#write-time — new rows get derived columns by code; leftovers wait for the async pass
                derived.derive_row(self, table, cur.lastrowid, log_id)
        elif kind == "update":
            row_id = op.get("row_id")
            row = self.db.execute(f"SELECT * FROM '{table}' WHERE id = ?", (row_id,)).fetchone()
            if row is None:
                raise ValueError(f"{table} has no row {row_id}")
            if self.strict:
                for c, v in values.items():
                    old = row[c] if c in row.keys() else None
                    if old is not None and str(old) != str(v):
                        self.db.execute("INSERT INTO _history (table_name, row_id, column_name, old_value, new_value, ts, log_id, op) "
                                        "VALUES (?, ?, ?, ?, ?, ?, ?, 'update')", (table, row_id, c, str(old), str(v), ts, log_id))
            src = json.loads(row["_src"] or "[]") + [log_id]
            sets = ", ".join(f"'{c}' = ?" for c in values)
            self.db.execute(f"UPDATE '{table}' SET {sets + ', ' if sets else ''}_src = ?, _ts = ? WHERE id = ?",
                            [*values.values(), json.dumps(src), ts, row_id])
        elif kind == "delete":
            if self.strict:
                row = self.db.execute(f"SELECT * FROM '{table}' WHERE id = ?", (op.get("row_id"),)).fetchone()
                if row is not None:
                    snap = {k: row[k] for k in row.keys() if k not in ("_src", "_ts") and row[k] is not None}
                    self.db.execute("INSERT INTO _history (table_name, row_id, column_name, old_value, new_value, ts, log_id, op) "
                                    "VALUES (?, ?, '*', ?, NULL, ?, ?, 'delete')", (table, op.get("row_id"), json.dumps(snap, ensure_ascii=False), ts, log_id))
            cur = self.db.execute(f"DELETE FROM '{table}' WHERE id = ?", (op.get("row_id"),))
            if cur.rowcount == 0:
                raise ValueError(f"{table} has no row {op.get('row_id')}")
        # @ref LLP 0004#forget — no history, cascade to dependent rows, redact the log; completed by LLP 0007
        elif kind == "forget" and self.strict:
            # Privacy delete: remove the row with no history, and erase the given terms everywhere else.
            if op.get("row_id") is not None:
                self.db.execute(f"DELETE FROM '{table}' WHERE id = ?", (op["row_id"],))
                self.db.execute("DELETE FROM _history WHERE table_name = ? AND row_id = ?", (table, op["row_id"]))
                if self.normalize:  # cascade: rows that only exist to describe the forgotten one go too
                    for t in self.tables():
                        for c in self.columns(t):
                            if self.target_table(c["name"]) == table:
                                ids = [r[0] for r in self.db.execute(f"SELECT id FROM '{t}' WHERE \"{c['name']}\" = ?", (op["row_id"],))]
                                # @ref LLP 0004#forget — on real data, forgetting the user's first name cascaded into all 54 rows about the user
                                if self.forget_links == "redact":
                                    for rid in ids:
                                        self.db.execute(f"UPDATE '{t}' SET \"{c['name']}\" = NULL WHERE id = ?", (rid,))
                                        self.db.execute("DELETE FROM _history WHERE table_name = ? AND row_id = ? AND column_name = ?",
                                                        (t, rid, c["name"]))
                                    continue
                                for rid in ids:
                                    self.db.execute(f"DELETE FROM '{t}' WHERE id = ?", (rid,))
                                    self.db.execute("DELETE FROM _history WHERE table_name = ? AND row_id = ?", (t, rid))
            # (term redaction runs at the end of apply(), after every op in the batch)
        else:
            raise ValueError(f"unknown op {kind}")

    # ----------------------------------------------------------------- read
    def query(self, sql: str, limit: int = 200) -> tuple[list[dict] | None, str | None]:
        """Run a read-only query (separate read-only connection)."""
        ro = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, check_same_thread=False)
        ro.row_factory = sqlite3.Row
        try:
            cur = ro.execute(sql)
            rows = [{k: r[k] for k in r.keys() if k not in ("_src", "_ts")} for r in cur.fetchmany(limit)]
            return rows, None
        except Exception as e:
            return None, f"{type(e).__name__}: {e}"
        finally:
            ro.close()

    @locked
    def dump(self) -> dict:
        return {t: {"description": (self.db.execute("SELECT description FROM _tables WHERE name=?", (t,)).fetchone() or {"description": ""})["description"],
                    "columns": self.columns(t), "rows": self.rows(t)} for t in self.tables()}


def _infer_type(value) -> str:
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, int):
        return "INTEGER"
    if isinstance(value, float):
        return "REAL"
    return "TEXT"


# ---------------------------------------------------------------------- BM25
_TOKEN = re.compile(r"[a-z0-9áčďéěíňóřšťúůýž@.+]+")


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if len(t) > 1]


def bm25_rank(query: str, docs: list[str], k1: float = 1.5, b: float = 0.75) -> list[tuple[int, float]]:
    """Rank docs for a query with plain BM25. Returns (index, score), best first."""
    toks = [tokenize(d) for d in docs]
    if not toks:
        return []
    avg = sum(len(t) for t in toks) / len(toks) or 1
    df = Counter(term for t in toks for term in set(t))
    n = len(toks)
    q = tokenize(query)
    scores = []
    for i, t in enumerate(toks):
        tf = Counter(t)
        s = 0.0
        for term in q:
            if term not in tf:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            s += idf * tf[term] * (k1 + 1) / (tf[term] + k1 * (1 - b + b * len(t) / avg))
        scores.append((i, s))
    return sorted(scores, key=lambda x: -x[1])
