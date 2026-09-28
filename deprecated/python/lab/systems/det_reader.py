"""Deterministic reads: a semantic layer profiled from the database, and Jev filling a closed query form. No LLM.

profile (SQL only, redone when the schema changes): for every table, its time column, number columns, and its
    dimensions: short text columns with their values, links to other tables (shown by name), and the label column of
    small tables (so "Klára" or "Renew passport" can be picked).
route (Jev, one round trip): the question-level slots (table, operation, period) plus, for the three tables BM25
    ranks highest, the table-level slots (which column, a value or "any" for each dimension), asked in parallel.
    Every option is something the database contains; Jev never writes a value.
execute (SQL + code): filter, order by time, apply the operation, and render the answer from the rows.

When Jev is unsure (low confidence) or nothing matches, `ask` says so, and the caller falls back to an LLM reader.
"""
# @ref LLP 0006 — the deterministic reader: semantic layer, closed query form, relaxation, verifier
from __future__ import annotations

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from lab.common import jev
from lab.systems.engine import Engine, bm25_rank

OPS = {
    "count": "How many matching rows / times / things there are",
    "sum": "The total of a number column over the matching rows (e.g. how much was spent or paid)",
    "max": "The row with the largest value of a column (most expensive, longest, highest)",
    "min": "The row with the smallest value of a column (cheapest, fastest, shortest)",
    "avg": "The average of a number column over the matching rows (average sleep, typical price)",
    "latest": "The current value or the most recent matching row up to now: 'what is X's phone/address/job', "
              "'where does X live/work', 'last time', 'right now'",
    "previous": "The value before the current one: 'previous', 'before my current one', 'used to'",
    "earliest": "The first matching row: 'first time', 'when did I first', 'originally'",
    "next": "The soonest matching row after now: 'next', 'upcoming'",
    "list": "Every matching row, or every distinct value of a column: 'which', 'who are', 'what's on my calendar'",
    "profile": "Everything known about one person, organization or thing: 'who is X', 'tell me about X'",
}
META = ("id", "_src", "_ts")
TIME_NAMES = ("date", "datetime", "start_datetime", "start_date", "transaction_datetime", "due_date", "announced_date",
              "move_in_date", "departure_date", "check_in_date")
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _fold(text: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode().lower()


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _clock(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


class DetReader:
    def __init__(self, db_path: str, user: str, now: datetime, max_values: int = 30):
        self.eng = Engine(db_path, strict=True, normalize=True)
        self.user, self.now, self.max_values = user, now, max_values
        self.tables = self.eng.tables()
        self.meta = {t: self._profile(t) for t in self.tables}
        self._labels = {t: self._label_map(t) for t in self.tables}
        self._linked = {(t, d): {self._labels[spec["table"]].get(r[0]) for r in self.eng.db.execute(
            f"SELECT DISTINCT \"{d}\" FROM '{t}' WHERE \"{d}\" IS NOT NULL")} - {None}
            for t in self.tables for d, spec in self.meta[t]["dims"].items() if spec["kind"] == "link"}
        # question-independent parts of the Jev requests, built once
        self.cards = {t: self.table_card(t) for t in self.tables}
        self.periods = self._periods()
        self.key_dates = self._key_dates()
        self._lock = threading.Lock()  # one SQLite connection: SQL steps run one at a time (they take ms)

    # ------------------------------------------------------------ profile
    def _label_map(self, t: str) -> dict[int, str]:
        rows = self.eng.rows(t)
        key = next((c for c in Engine.LABEL_COLS if any(r.get(c) for r in rows)), None)
        return {r["id"]: str(r.get(key) or f"{t}#{r['id']}") for r in rows}

    def _profile(self, t: str) -> dict:
        db = self.eng.db
        n = db.execute(f"SELECT COUNT(*) FROM '{t}'").fetchone()[0]
        cols = [c for c in self.eng.columns(t) if c["name"] not in META]
        desc = db.execute("SELECT description FROM _tables WHERE name = ?", (t,)).fetchone()[0]
        out = {"rows": n, "description": desc, "columns": {}, "time": None, "numbers": [], "dims": {}}
        label = next((c for c in Engine.LABEL_COLS if c in [x["name"] for x in cols]), None)
        superseded = {c for spec in self.eng.derived.get(t, []) for c in spec.get("supersedes", [])}
        for c in cols:
            name = c["name"]
            vals = [r[0] for r in db.execute(f"SELECT \"{name}\" FROM '{t}' WHERE \"{name}\" IS NOT NULL")]
            out["columns"][name] = c.get("description") or ""
            if not vals:
                continue
            target = self.eng.target_table(name, self.tables)
            if target:
                out["dims"][name] = {"kind": "link", "table": target}
            elif name in superseded:   # a derived column now holds what questions filtered this free text for
                continue
            elif name in TIME_NAMES or all(isinstance(v, str) and ISO.match(v) for v in vals[:20]):
                out["time"] = out["time"] or name
            elif all(_is_num(v) for v in vals):
                out["numbers"].append(name)
                distinct = sorted({float(v) for v in vals})
                if len(distinct) <= 12 and len(vals) >= 2 * len(distinct):  # e.g. distance_km: 5 / 10 / 21.1
                    out["dims"][name] = {"kind": "values", "values": [f"{v:g}" for v in distinct]}
            else:
                distinct = {str(v) for v in vals}
                small = len(distinct) <= self.max_values and (len(distinct) <= max(3, len(vals) // 2) or n <= 40 or name == label)
                categorical = len(distinct) <= 400 and len(distinct) <= 0.25 * len(vals)   # merchants over thousands of rows
                if small or categorical:
                    out["dims"][name] = {"kind": "values", "values": sorted(distinct),
                                         "free_text": bool(re.search(r"desc|note|detail|comment|summary|body", name)),
                                         "sparse": len(vals) < 0.5 * n}
        out["time"] = out["time"] or "_ts"
        return out

    def table_card(self, t: str) -> str:
        m = self.meta[t]
        parts = [f"{t} ({m['rows']} rows): {m['description']}"]
        if m["dims"]:
            parts.append("filters: " + ", ".join(m["dims"]))
        if m["numbers"]:
            parts.append("numbers: " + ", ".join(m["numbers"]))
        examples = [v for d in m["dims"].values() if d["kind"] == "values" for v in d["values"][:4]][:8]
        if examples:
            parts.append("e.g. " + ", ".join(str(v)[:40] for v in examples))
        return " | ".join(parts)

    # ------------------------------------------------------------ route
    def _periods(self) -> dict[str, str]:
        start = min((v for t in self.tables for v in [self._min_time(t)] if v), default=self.now.strftime("%Y-%m"))
        y, mo = int(start[:4]), int(start[5:7])
        months = []
        while (y, mo) <= (self.now.year, self.now.month + 1 if self.now.month < 12 else 12):
            months.append(f"{y:04d}-{mo:02d}")
            y, mo = (y + 1, 1) if mo == 12 else (y, mo + 1)
        out = {"all": "Any time (no time limit)", "future": "From now on (upcoming)"}
        for ym in months:
            label = datetime.strptime(ym, "%Y-%m").strftime("%B %Y")
            out[f"in:{ym}"] = f"In {label}"
            out[f"since:{ym}"] = f"Since {label} (from the start of {label} until now)"
        for year in sorted({m[:4] for m in months}):
            out[f"year:{year}"] = f"In the year {year}"
        return out

    MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
              "november", "december"]

    def _periods_for(self, question: str) -> dict[str, str]:
        if len(self.periods) <= 40:
            return self.periods
        q = _fold(question)
        years = set(re.findall(r"\b(20\d\d)\b", q))
        months = {i + 1 for i, name in enumerate(self.MONTHS) if re.search(rf"\b{name[:3]}", q)}
        wanted = {k[-7:] for k in self.periods if k.startswith("in:") and int(k[-2:]) in months and (not years or k[-7:-3] in years)}
        wanted |= {d[:7] for d in self.key_dates}                       # "since I moved" anchors
        wanted |= {self.now.strftime("%Y-%m"), (self.now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")}
        return {k: v for k, v in self.periods.items() if not k.startswith(("in:", "since:")) or k.split(":")[1] in wanted}

    def _min_time(self, t: str) -> str | None:
        col = self.meta[t]["time"]
        v = self.eng.db.execute(f"SELECT MIN(\"{col}\") FROM '{t}' WHERE \"{col}\" LIKE '____-__-__%'").fetchone()[0]
        return v

    def _key_dates(self, limit_rows: int = 12) -> list[str]:
        """Dated rows from small tables (a lease, a funding round): anchors for 'since I moved' style periods."""
        out = []
        for t in self.tables:
            m = self.meta[t]
            if m["rows"] <= limit_rows and m["time"] != "_ts":
                for r in self.eng.rows(t):
                    if r.get(m["time"]):
                        out.append(f"{str(r[m['time']])[:10]} {t}: {self._row_text(t, r, 90)}")
        return sorted(out)[:40]

    # @ref LLP 0006#routing — a card shows a few example values; name the stored values the question contains
    def _mentions(self, question: str, limit: int = 8) -> list[str]:
        """Stored values the question names word for word ("Field", "Sightglass", "Kuba") and where they are stored:
        a table card shows only a few example values, so Jev can't know on its own that "Field" is a merchant."""
        q = " " + re.sub(r"[^a-z0-9]+", " ", _fold(question)) + " "
        out = []
        for t in self.tables:
            for d, spec in self.meta[t]["dims"].items():
                if spec.get("free_text"):
                    continue
                # names this column links to (sorted: a set's order changes between processes, and so would the request)
                values = sorted(self._linked[(t, d)]) if spec["kind"] == "link" else spec["values"]
                for v in values:
                    fv = re.sub(r"[^a-z0-9]+", " ", _fold(v)).strip()
                    if len(fv) >= 3 and f" {fv} " in q and v != self.user:
                        out.append(f"{t}.{d} = {v}")
        return out[:limit]

    def _stage1(self, question: str) -> dict:
        tables = dict(self.cards)
        tables["none"] = "None of these: the database doesn't store this"
        mentions = self._mentions(question)
        if mentions:   # (no mentions: the request is exactly the one before this was added, so it stays cached)
            qs = {"table": jev.choice("Which table holds the answer to `question`? `mentions` lists stored values that "
                                      "the question names, and the table.column that stores each.", tables),
                  "op": jev.choice("What does `question` ask for?", OPS),
                  "period": jev.choice("Which time period does `question` limit the answer to?", self._periods_for(question))}
            state = {"question": question, "now": self.now.strftime("%Y-%m-%d %H:%M (%A)"), "user": self.user,
                     "key_dates": self.key_dates, "mentions": mentions}
            return jev.ask(state, qs, tag="det:route")
        qs = {"table": jev.choice("Which table holds the answer to `question`?", tables),
              "op": jev.choice("What does `question` ask for?", OPS),
              "period": jev.choice("Which time period does `question` limit the answer to?", self._periods_for(question))}
        state = {"question": question, "now": self.now.strftime("%Y-%m-%d %H:%M (%A)"), "user": self.user,
                 "key_dates": self.key_dates}
        return jev.ask(state, qs, tag="det:route")

    def _stage2(self, question: str, t: str) -> dict:
        m = self.meta[t]
        cols = {c: (d or c) for c, d in m["columns"].items()}
        cols["__row__"] = "The whole row (several columns)"
        qs = {"column": jev.choice(f"Which column of {t} holds what `question` asks for?", cols)}
        for d, spec in m["dims"].items():
            values = self._values(t, d)
            keep = range(len(values)) if len(values) <= self.max_values else self._shortlist(question, values)
            opts = {f"v{i}": values[i] for i in keep}
            opts["any"] = f"Any {d} (the question does not restrict {d})"
            qs[f"dim:{d}"] = jev.choice(f"Which {d} of {t} does `question` restrict to?", opts)
        state = {"question": question, "user": self.user, "table": self.cards[t]}
        return jev.ask(state, qs, tag="det:slots")

    def route(self, question: str, candidates: int = 3) -> dict:
        ranked = [self.tables[i] for i, _ in bm25_rank(question, [self.cards[t] + " " + " ".join(
            str(v) for d in self.meta[t]["dims"].values() for v in d.get("values", [])) for t in self.tables])]
        named = [m.split(".")[0] for m in self._mentions(question)]   # tables holding a value the question names
        ranked = list(dict.fromkeys(named + ranked))[:candidates]
        with ThreadPoolExecutor(2 + len(ranked)) as pool:
            f1 = pool.submit(self._stage1, question)
            f2 = {t: pool.submit(self._stage2, question, t) for t in ranked}
            s1 = f1.result()
            probs = s1["table"]["probabilities"]
            order = [t for t in sorted(probs, key=probs.get, reverse=True) if t != "none"]
            table = s1["table"]["choice"]
            alternates = order[:2] if table == "none" else [t for t in order[:2] if t != table][:1]
            need = [t for t in [table] + alternates if t != "none" and t not in f2]
            f2.update({t: pool.submit(self._stage2, question, t) for t in need})
            stage2s = {t: f2[t].result() for t in [table] + alternates if t in f2}
        return {"stage1": s1, "stage2": stage2s.get(table, {}), "stage2s": stage2s, "table": table,
                "alternates": alternates if table != "none" else [], "speculated": ranked}

    # ------------------------------------------------------------ execute
    def _row_text(self, t: str, r: dict, limit: int = 300) -> str:
        shown = {}
        for k, v in r.items():
            if k in META or v is None:
                continue
            if re.search(r"(_s|_sec|_seconds)$", k) and _is_num(v):  # durations in seconds read as clock times
                v = _clock(v)
            target = self.eng.target_table(k, self.tables)
            shown[k[:-3] if target else k] = self._labels[target].get(v, v) if target and isinstance(v, int) else v
        return json.dumps(shown, ensure_ascii=False)[:limit]

    def _shortlist(self, question: str, values: list[str], k: int = 20) -> list[int]:
        """The values a question most likely refers to: word overlap, then the most frequent ones (by list order)."""
        q = set(re.findall(r"\w+", _fold(question)))
        scored = []
        for i, v in enumerate(values):
            words = set(re.findall(r"\w+", _fold(v)))
            overlap = len(q & words) / (len(words) or 1)
            scored.append((-overlap, -(_fold(v) in _fold(question)), i))
        return sorted(i for _, _, i in sorted(scored)[:k])

    def _values(self, t: str, d: str) -> list[str]:
        spec = self.meta[t]["dims"][d]
        return sorted(set(self._labels[spec["table"]].values())) if spec["kind"] == "link" else spec["values"]

    def _select(self, t: str, filters: dict, period: str, col: str) -> list[dict]:
        m = self.meta[t]
        rows = [dict(r) for r in self.eng.db.execute(f"SELECT * FROM '{t}'")]

        def matches(r, d, value):
            spec = m["dims"][d]
            if spec["kind"] == "link":
                ids = {str(i) for i, lbl in self._labels[spec["table"]].items() if lbl == value}
                return str(r.get(d)) in ids
            v = r.get(d)
            if v is None:   # a sparse column (mostly empty) doesn't know: an empty value is not a mismatch
                return spec.get("sparse", False)
            if spec.get("free_text"):   # descriptions vary ("Ride", "Ride home"): match by containment
                return _fold(value) in _fold(v)
            return (f"{v:g}" if _is_num(v) else str(v)) == value

        # the same value picked for two columns (category=coffee, item=coffee) is one concept: either may match
        groups: dict = {}
        for d, value in filters.items():
            groups.setdefault(value.lower(), []).append((d, value))
        for group in groups.values():
            rows = [r for r in rows if any(matches(r, d, v) for d, v in group)]
        when = self._when(t)
        rows.sort(key=when)
        if period.startswith("in:"):
            rows = [r for r in rows if when(r).startswith(period[3:])]
        elif period.startswith("year:"):
            rows = [r for r in rows if when(r).startswith(period[5:])]
        elif period.startswith("since:"):
            rows = [r for r in rows if when(r)[:7] >= period[6:]]
        elif period == "future":
            rows = [r for r in rows if when(r) > self.now.isoformat()]
        if col != "__row__":
            rows = [r for r in rows if r.get(col) is not None]
        return rows

    def _plan_text(self, t, op, col, filters, period, n) -> str:
        """The query behind an answer in plain words, so the verifier can check it against the question."""
        where = " and ".join(f"{d} = {v!r}" for d, v in filters.items()) or "no filter"
        per = {"all": "any time", "future": "from now on"}.get(period, period.replace("in:", "in ").replace("since:", "since ")
                                                                    .replace("year:", "in "))
        what = {"count": "count of rows", "sum": f"total of {col}", "avg": f"average of {col}", "max": f"row with the largest {col}",
                "min": f"row with the smallest {col}", "latest": f"latest {col}", "previous": f"previous {col}",
                "earliest": f"first {col}", "next": f"next {col}", "list": f"list of {col}", "profile": "everything about"}.get(op, op)
        return f"{what} from {t} where {where}, {per} ({n} matching rows)"

    def _when(self, t: str):
        tcol = self.meta[t]["time"]
        return lambda r: str(r.get(tcol) or r.get("_ts") or "")

    def _apply(self, t: str, op: str, col: str, rows: list[dict]) -> str | None:
        """The answer text, or None when the rows can't answer (so the caller relaxes or falls back)."""
        m, when, now_iso = self.meta[t], self._when(t), self.now.isoformat()
        past = [r for r in rows if when(r) <= now_iso] or rows
        if op in ("latest", "profile"):  # rows a newer row replaced (valid_to set) are history, not current
            past = [r for r in past if not r.get("valid_to")] or past

        def value(r):
            target = self.eng.target_table(col, self.tables)
            v = self._labels[target].get(r[col], r[col]) if target else r[col]
            return _clock(v) if re.search(r"(_s|_sec|_seconds)$", col) and _is_num(v) else v

        def show(r):
            head = f"{value(r)} — " if col != "__row__" and r.get(col) is not None else ""
            return f"{head}{when(r)[:16]} {self._row_text(t, r)}"

        if not rows:
            return None
        if op == "count":
            return f"{len(rows)}"
        if op in ("sum", "max", "min", "avg"):
            num = col if col in m["numbers"] else (m["numbers"][0] if m["numbers"] else None)
            cand = [r for r in rows if num and _is_num(r.get(num))]
            if not cand:
                return None
            if op == "avg":
                vals = [r[num] for r in cand]
                return f"{sum(vals) / len(vals):,.2f} (average of {len(vals)} rows)"
            if op == "sum":
                totals: dict = {}
                for r in cand:
                    totals[r.get("currency") or ""] = totals.get(r.get("currency") or "", 0) + r[num]
                return " + ".join(f"{v:,.2f} {c}".strip() for c, v in totals.items()) + f" ({len(cand)} rows)"
            return show((max if op == "max" else min)(cand, key=lambda r: r[num]))
        if op == "latest":
            return show(past[-1])
        if op == "earliest":
            return show(rows[0])
        if op == "next":
            future = [r for r in rows if when(r) > now_iso]
            return show(future[0]) if future else None
        if op == "previous":
            if len(past) >= 2:
                return show(past[-2])
            h = self.eng.db.execute("SELECT old_value FROM _history WHERE table_name = ? AND row_id = ? AND "
                                    "(column_name = ? OR ? = '__row__') ORDER BY id DESC LIMIT 1",
                                    (t, past[-1]["id"], col, col)).fetchone()
            return f"{h[0]} (before: {self._row_text(t, past[-1], 160)})" if h else None
        if op == "profile":
            r = past[-1]
            linked = [f"{t2}: {self._row_text(t2, r2, 160)}" for t2 in self.tables for r2 in self.eng.rows(t2)
                      for k, v in r2.items() if self.eng.target_table(k, self.tables) == t and str(v) == str(r["id"])]
            return "; ".join([self._row_text(t, r)] + linked[:8])
        if col != "__row__":  # list of distinct values
            vals = []
            for r in rows:
                if str(value(r)) not in vals:
                    vals.append(str(value(r)))
            return "; ".join(vals[:15])
        return "; ".join(show(r) for r in rows[:10])

    def _identity_col(self, t: str) -> str | None:
        """The column that names a row of an entity table (a person's name, a book's title). Rows of event tables
        (they have their own date: expenses, workouts) have no identity column: their description is just text."""
        if self.meta[t]["time"] not in ("_ts",) and self.meta[t]["rows"] > 60:
            return None
        return next((c for c in Engine.LABEL_COLS if c in self.meta[t]["dims"]), None)

    def _variants(self, plan: dict, out: dict) -> list[dict]:
        """For totals and counts, the same query with one kind filter dropped (category=transport vs merchant=Uber):
        the plan-aware verifier then picks whichever matches the question. Entity filters are never dropped."""
        op = plan["stage1"]["op"]["choice"]
        t = out.get("table")
        if op not in ("count", "sum", "avg", "max", "min") or t not in self.meta or len(out.get("filters") or {}) < 2:
            return []
        label_col = self._identity_col(t)
        col, per, res = out["column"], out["period"], []
        for d in out["filters"]:
            spec = self.meta[t]["dims"].get(d, {})
            if spec.get("kind") == "link" or d == label_col:
                continue
            f = {k: v for k, v in out["filters"].items() if k != d}
            rows = self._select(t, f, per, "__row__" if op == "count" else col)
            ans = self._apply(t, op, col, rows)
            if ans is not None:
                res.append({"answer": ans, "table": t, "column": col, "filters": f, "period": per, "relaxed": out["relaxed"],
                            "steps": out["steps"], "plan": self._plan_text(t, op, col, f, per, len(rows))})
        return res

    # @ref LLP 0006#relaxation [constrained-by] — never drop a filter on a specific person or thing
    def execute(self, plan: dict) -> dict:
        """Run the plan; if nothing matches, relax it step by step (drop the period for point lookups, then the least
        confident filter, then try Jev's second-choice table). Every step is recorded."""
        s1 = plan["stage1"]
        op, period = s1["op"]["choice"], s1["period"]["choice"]
        tables = [plan["table"]] + [t for t in plan.get("alternates", []) if t != plan["table"]]
        steps = []
        for t in tables:
            if t == "none" or t not in self.meta:
                continue
            s2 = plan["stage2s"].get(t)
            if s2 is None:
                continue
            col = s2.get("column", {}).get("choice", "__row__")
            filters = {k[4:]: (self._values(t, k[4:])[int(a["choice"][1:])], a["confidence"]) for k, a in s2.items()
                       if k.startswith("dim:") and a["choice"] != "any"}
            if op == "profile" and not filters:
                continue  # "who is X" needs an X
            # Relaxation never drops a filter on a specific person/thing/title (that would answer about someone
            # else); it may drop the time period of a point lookup, the implicit "me" filter, and kind filters.
            # Totals and counts only ever drop "me": a sum over loosened filters is silently wrong.
            label_col = self._identity_col(t)

            def droppable(d, v):
                spec = self.meta[t]["dims"][d]
                if spec["kind"] == "link":
                    return v == self.user
                return d != label_col and op not in ("count", "sum", "max", "min")

            attempts = [(dict(filters), period)]
            lookup = op in ("latest", "previous", "earliest", "profile")
            if lookup and period != "all":
                attempts.append((dict(filters), "all"))
            relaxed = dict(filters)
            for d, (v, _) in sorted(filters.items(), key=lambda kv: kv[1][1]):
                if droppable(d, v):
                    relaxed = {k: x for k, x in relaxed.items() if k != d}
                    attempts.append((dict(relaxed), "all" if lookup else period))
            if op == "profile":
                attempts = [(f, per) for f, per in attempts if any(not droppable(d, v) for d, (v, _) in f.items())]
            for f, per in attempts:
                # @ref LLP 0006#operations
                # a count counts the matching rows, whichever column Jev picked (not only rows where it is filled)
                rows = self._select(t, {d: v for d, (v, _) in f.items()}, per, "__row__" if op == "count" else col)
                ans = self._apply(t, op, col, rows)
                steps.append({"table": t, "filters": {d: v for d, (v, _) in f.items()}, "period": per, "rows": len(rows)})
                if ans is not None:
                    return {"answer": ans, "table": t, "column": col, "filters": steps[-1]["filters"], "period": per,
                            "relaxed": len(steps) - 1, "steps": steps,
                            "plan": self._plan_text(t, op, col, steps[-1]["filters"], per, len(rows))}
        return {"answer": "I don't know.", "table": plan["table"], "column": None, "filters": {}, "period": period,
                "relaxed": len(steps), "steps": steps}

    VERIFY = {"answers": jev.noul("`result` was computed from the user's own database for `question` by the query in "
                                  "`query`. Does it answer `question`: the right table, every thing the question names "
                                  "(all of them, not just one), the right period, and the right kind of value?")}

    # @ref LLP 0006#verifier — candidates from the top two tables and plan variants, judged with the plan in words
    def ask(self, question: str, verify: bool = True) -> dict:
        plan = self.route(question)
        with self._lock:
            out = self.execute(plan)
            # candidate answers from Jev's top-2 tables, each run on its own (SQL: milliseconds)
            cands = [out] + [self.execute({**plan, "table": t, "alternates": []}) for t in plan["alternates"]]
            cands += self._variants(plan, out)
        cands = [c for c in cands if c["answer"] != "I don't know."]
        if verify and cands:
            checks = jev.ask_many([({"question": question, "query": c.get("plan", ""), "result": c["answer"][:1500]},
                                    self.VERIFY) for c in cands], workers=len(cands), tag="det:verify")
            for c, v in zip(cands, checks):
                c["verified"] = round(v["answers"]["noul"], 3)
            best = max(cands, key=lambda c: c["verified"])
            out = best if best["verified"] > out.get("verified", -1) + 0.2 else out
        out.setdefault("verified", None)
        s1, s2 = plan["stage1"], plan["stage2s"].get(out["table"], {})
        confs = [s1["table"]["confidence"] if out["table"] == plan["table"] else 0.0, s1["op"]["confidence"],
                 s1["period"]["confidence"]]
        confs += [a["confidence"] for k, a in s2.items() if k == "column" or (k.startswith("dim:") and k[4:] in out["filters"])]
        out.update({"op": s1["op"]["choice"], "confidence": round(min(confs) * (0.8 ** out["relaxed"]), 3),
                    "speculation_hit": plan["table"] in plan["speculated"] or plan["table"] == "none"})
        return out
