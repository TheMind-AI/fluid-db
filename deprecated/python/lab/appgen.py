"""Generate an app from a FluidDB database: what the user asks about, what the schema holds, and what the database
doubts. One LLM call lays it out; code computes every number.

views
  tiles       from the query log's trusted plans. A measure is what a plan computes (sum of expenses.amount, count of
              workouts where category = run); it counts as recurring when >= 2 trusted plans compute it. Slicers are
              the filter columns whose values vary across a table's plans (occasion, category, merchant, location).
              Every recurring measure becomes one tile per slicer set the user used: values by slicer, per month,
              year and in total, one panel per currency. No model writes SQL.
  people      for every person: what the tables linked to them add up to (total, count, last date), overall and by
              each small category column of that table
  breakdowns  the most frequent values of a large column (merchants) per year and per small category, and totals
              for pairs of small category columns (category x location)
  timeline    every dated row (from the explorer)
  inbox       the least-confident rows (lab/systems/confidence.py) and person pairs dedupe left undecided
layout  one GPT-6 Luna call orders views into pages and names them, referring to views by id only

  .venv/bin/python -m lab.appgen --db lab/runs/app/db.sqlite --plans lab/runs/app/plans.json --out lab/app/index.html
"""
# @ref LLP 0012#tiles — tiles are derived by code from trusted plans; the model only lays them out
from __future__ import annotations

import argparse
import html
import json
import re
import statistics
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from lab.common.llm import chat_json
from lab.explorer import CSS as EXPLORER_CSS, PALETTE, PALETTE_DARK, DB as ExplorerDB, timeline
from lab.systems.derived import fold
from lab.systems.det_reader import DetReader

AGG = ("sum", "count", "avg", "max", "min")
GRAINS = ("month", "year", "all")


def esc(x) -> str:
    return html.escape(str(x))


def money_fmt(v: float) -> str:
    return f"{v:,.2f}".replace(".00", "") if abs(v) < 1e7 else f"{v:,.0f}"


# ------------------------------------------------------------------------------------------------------ tiles
def concepts(filters: dict) -> dict:
    """The reader's rule: the same value picked for two columns (category = coffee, item = coffee) is one concept that
    either column may match. Such columns become one key, "category|item"."""
    by_value = defaultdict(list)
    for d, v in filters.items():
        by_value[fold(v)].append(d)
    return {"|".join(sorted(ds)): next(filters[d] for d in ds) for ds in by_value.values()}


def tiles_from_plans(reader: DetReader, plans: list[tuple[str, dict]], min_plans: int = 2) -> list[dict]:
    """plans: [(question, plan)] the verifier trusted. Returns tile specs (no numbers yet)."""
    plans = [(q, {**p, "filters": concepts(p.get("filters") or {})}) for q, p in plans]
    values = defaultdict(lambda: defaultdict(set))
    for _, p in plans:
        for d, v in (p.get("filters") or {}).items():
            values[p["table"]][d].add(str(v))
    slicers = {t: {d for d, vs in dims.items() if len(vs) > 1} for t, dims in values.items()}
    measure = lambda p: (p["table"], p["op"], "__row__" if p["op"] == "count" else p["column"])
    recurring = Counter(measure(p) for _, p in plans if p.get("op") in AGG and p.get("table") in reader.meta)
    tiles: dict = {}
    for q, p in plans:
        if p.get("op") not in AGG or p.get("table") not in reader.meta or recurring[measure(p)] < min_plans:
            continue
        t, op, col = measure(p)
        f = p.get("filters") or {}
        series = tuple(sorted(d for d in f if d in slicers.get(t, set())))
        const = tuple(sorted((d, str(v)) for d, v in f.items() if d not in series and v != reader.user))
        key = (t, op, col, series, const)
        if key not in tiles:
            tiles[key] = {"id": f"tile{len(tiles) + 1}", "view": "tile", "table": t, "op": op, "column": col,
                          "series": list(series), "const": dict(const), "questions": [], "asked": []}
        tiles[key]["questions"].append(q)
        asked = [str(f[d]) for d in series]
        if asked not in tiles[key]["asked"]:
            tiles[key]["asked"].append(asked)
    return list(tiles.values())


def _where(reader: DetReader, table: str, const: dict, series: list[str], time_col: str | None):
    where, args = [], []
    dims = reader.meta[table]["dims"]
    for d, v in const.items():
        spec = dims.get(d, {})
        if "|" in d:   # a concept: either column may hold the value
            parts = d.split("|")
            where.append("(" + " OR ".join(f"\"{c}\" = ?" for c in parts) + ")")
            args += [v] * len(parts)
        elif spec.get("kind") == "link":
            ids = [i for i, lbl in reader._labels[spec["table"]].items() if lbl == v]
            where.append(f"\"{d}\" IN ({','.join('?' * len(ids)) or 'NULL'})")
            args += ids
        else:
            where.append(f"\"{d}\" = ?")
            args.append(v)
    where += ["(" + " OR ".join(f"\"{c}\" IS NOT NULL" for c in d.split("|")) + ")" for d in series]
    if time_col and time_col != "_ts":
        where.append(f"\"{time_col}\" IS NOT NULL")
    return (" WHERE " + " AND ".join(where)) if where else "", args


def tile_sql(reader: DetReader, tile: dict) -> tuple[str, list]:
    """The month-grain GROUP BY a plain SQL app would run for this tile (shown in the app, and checked against the
    numbers the app computes)."""
    t, op, col = tile["table"], tile["op"], tile["column"]
    time_col = reader.meta[t]["time"]
    money = col == "amount" and "currency" in reader.meta[t]["columns"]
    where, args = _where(reader, t, tile["const"], tile["series"], time_col)
    agg = "COUNT(*)" if op == "count" else f"{op.upper()}(\"{col}\")"
    concept = next((d for d in tile["series"] if "|" in d), None)
    if concept:   # one row may belong to a concept through either column: expand with UNION (no row counted twice)
        other = [d for d in tile["series"] if d != concept]
        base = list(dict.fromkeys(["id", time_col] + ([col] if col != "__row__" else []) + other + (["currency"] if money else [])))
        sel = ", ".join(f"\"{c}\"" for c in base)
        parts = [f"SELECT {sel}, \"{c}\" AS concept FROM \"{t}\"{where}{' AND' if where else ' WHERE'} \"{c}\" IS NOT NULL"
                 for c in concept.split("|")]
        keys = [f"substr(\"{time_col}\", 1, 7)"] + ["concept" if d == concept else f"\"{d}\"" for d in tile["series"]] + (["currency"] if money else [])
        return (f"SELECT {', '.join(keys)}, {agg} AS value, COUNT(*) AS n FROM ({' UNION '.join(parts)}) GROUP BY {', '.join(keys)}",
                args * len(parts))
    keys = [f"substr(\"{time_col}\", 1, 7)"] + [f"\"{d}\"" for d in tile["series"]] + (["currency"] if money else [])
    return f"SELECT {', '.join(keys)}, {agg} AS value, COUNT(*) AS n FROM \"{t}\"{where} GROUP BY {', '.join(keys)}", args


def compute_tile(reader: DetReader, tile: dict) -> dict:
    t, op, col = tile["table"], tile["op"], tile["column"]
    m = reader.meta[t]
    time_col = m["time"]
    money = col == "amount" and "currency" in m["columns"]
    where, args = _where(reader, t, tile["const"], tile["series"], time_col)
    parts = [c for d in tile["series"] for c in d.split("|")]
    wanted = list(dict.fromkeys(parts + ([col] if col != "__row__" else []) + [time_col] +
                                (["currency"] if money else []) + [c for c in ("merchant", "title") if c in m["columns"]]))
    rows = [dict(zip(wanted, r)) for r in reader.eng.db.execute(
        f"SELECT {', '.join(chr(34) + c + chr(34) for c in wanted)} FROM \"{t}\"{where}", args)]

    def label(d, v):
        spec = m["dims"].get(d, {})
        return reader._labels[spec["table"]].get(v, v) if spec.get("kind") == "link" else v

    import itertools
    groups = defaultdict(list)
    for r in rows:
        stamp = str(r.get(time_col) or "")
        options = [sorted({str(label(c, r[c])) for c in d.split("|") if r.get(c) is not None}) for d in tile["series"]]
        cur = (r.get("currency") or "") if money else ""
        for s in itertools.product(*options):   # a concept row belongs to each value it holds (never twice to one)
            for grain, period in (("month", stamp[:7]), ("year", stamp[:4]), ("all", "all")):
                if period:
                    groups[(grain, period, s, cur)].append(r)
    cells = {}
    for k, rs in groups.items():
        if op == "count":
            cells[k] = {"v": len(rs), "n": len(rs)}
            continue
        vals = [r[col] for r in rs if isinstance(r.get(col), (int, float))]
        if vals:
            if op in ("max", "min"):
                best = (max if op == "max" else min)((r for r in rs if isinstance(r.get(col), (int, float))), key=lambda r: r[col])
                cells[k] = {"v": best[col], "n": len(vals), "at": str(best.get(time_col) or "")[:10],
                            "what": best.get("merchant") or best.get("title") or ""}
            else:
                cells[k] = {"v": sum(vals) / len(vals) if op == "avg" else sum(vals), "n": len(vals)}
    return {"cells": cells, "rows": len(rows), "money": money}


def check_sql(reader: DetReader, tile: dict, data: dict) -> dict:
    """Run the tile's SQL; its month cells must equal the app's (avg/min/max compared at 0.01)."""
    sql, args = tile_sql(reader, tile)
    try:
        got = reader.eng.db.execute(sql, args).fetchall()
    except Exception as e:  # noqa: BLE001 - reported, not raised
        return {"ok": False, "error": str(e)}
    mism = 0
    for r in got:
        month, rest = r[0], list(r[1:])
        value, n = rest[-2], rest[-1]
        keys = rest[:-2]
        s = keys[:len(tile["series"])]
        cur = keys[len(tile["series"])] if data["money"] else ""
        m = reader.meta[tile["table"]]
        s = tuple(str(reader._labels[m["dims"][d]["table"]].get(v, v) if m["dims"].get(d, {}).get("kind") == "link" else v)
                  for d, v in zip(tile["series"], s))
        cell = data["cells"].get(("month", month, s, cur or ""))
        if value is None and not cell:   # only rows without an amount: SQL sums to NULL, the app shows no cell
            continue
        if not cell or value is None or abs(cell["v"] - value) > 0.01:
            mism += 1
    return {"ok": mism == 0, "groups": len(got), "mismatches": mism}


# ------------------------------------------------------------------------------------------------ generic views
UNITS = re.compile(r"currency|unit|status|payment|method|format|card")   # units and bookkeeping, not categories


def small_dims(reader: DetReader, table: str, limit: int = 12) -> list[str]:
    """Small category columns people slice by (not units or bookkeeping states)."""
    return [d for d, s in reader.meta[table]["dims"].items() if s["kind"] == "values" and not s.get("free_text")
            and 2 <= len(s["values"]) <= limit and not UNITS.search(d)]


def people_view(reader: DetReader) -> dict:
    """Per person: total, count and last date of every table that links to them and carries amounts, overall and by
    each small category column."""
    out = defaultdict(dict)
    for t in reader.tables:
        m = reader.meta[t]
        links = [d for d, s in m["dims"].items() if s["kind"] == "link" and s["table"] == "people" and d != "person_id"]
        if not links or "amount" not in m["columns"]:
            continue
        kinds = small_dims(reader, t)
        cols = list(dict.fromkeys(links + kinds + ["amount", "currency", m["time"], "merchant"]))
        cols = [c for c in cols if c in m["columns"] or c == m["time"]]
        for r in reader.eng.db.execute(f"SELECT {', '.join(chr(34) + c + chr(34) for c in cols)} FROM \"{t}\""):
            r = dict(zip(cols, r))
            for d in links:
                who = reader._labels["people"].get(r.get(d))
                if not who or who == reader.user:
                    continue
                for kind in [("all", "all")] + [(k, str(r[k])) for k in kinds if r.get(k) is not None]:
                    cell = out[who].setdefault((t, kind), {"total": defaultdict(float), "n": 0, "last": "", "last_what": ""})
                    if isinstance(r.get("amount"), (int, float)):
                        cell["total"][r.get("currency") or ""] += r["amount"]
                    cell["n"] += 1
                    when = str(r.get(m["time"]) or "")[:10]
                    if when > cell["last"]:
                        cell["last"], cell["last_what"] = when, r.get("merchant") or ""
    return {"id": "people", "view": "people", "people": {p: {f"{t}|{k}|{v}": {**c, "total": dict(c["total"])}
                                                           for (t, (k, v)), c in cells.items()} for p, cells in out.items()}}


def breakdown_views(reader: DetReader, big: int = 30) -> list[dict]:
    views = []
    for t in reader.tables:
        m = reader.meta[t]
        if m["time"] == "_ts" or m["rows"] < 50:
            continue
        kinds = small_dims(reader, t)
        large = [d for d, s in m["dims"].items() if s["kind"] == "values" and not s.get("free_text") and len(s["values"]) > big]
        for d in large:   # the top values of a big column, per year and small category
            cols = [d, m["time"]] + kinds
            counts = defaultdict(Counter)
            for r in reader.eng.db.execute(f"SELECT {', '.join(chr(34) + c + chr(34) for c in cols)} FROM \"{t}\" WHERE \"{d}\" IS NOT NULL"):
                r = dict(zip(cols, r))
                year = str(r.get(m["time"]) or "")[:4]
                for kind in [("all", "all")] + [(k, str(r[k])) for k in kinds if r.get(k) is not None]:
                    for period in (year, "all"):
                        if period:
                            counts[f"{kind[0]}|{kind[1]}|{period}"][r[d]] += 1
            views.append({"id": f"top_{t}_{d}", "view": "top", "table": t, "column": d,
                          "top": {k: c.most_common(5) for k, c in counts.items()}})
        if "amount" in m["columns"] and len(kinds) >= 2:   # totals for pairs of small category columns
            for i, a in enumerate(kinds):
                for b in kinds[i + 1:]:
                    tot = defaultdict(lambda: defaultdict(float))
                    for r in reader.eng.db.execute(f"SELECT \"{a}\", \"{b}\", amount, currency FROM \"{t}\" "
                                                   f"WHERE \"{a}\" IS NOT NULL AND \"{b}\" IS NOT NULL"):
                        tot[f"{r[0]}|{r[1]}"][r[3] or ""] += r[2] or 0
                    views.append({"id": f"cross_{t}_{a}_{b}", "view": "cross", "table": t, "columns": [a, b],
                                  "totals": {k: dict(v) for k, v in tot.items()}})
    return views


# ------------------------------------------------------------------------------------------------------ layout
LAYOUT_SCHEMA = {"type": "object", "properties": {
    "title": {"type": "string"},
    "pages": {"type": "array", "items": {"type": "object", "properties": {
        "title": {"type": "string"},
        "intro": {"type": "string"},
        "sections": {"type": "array", "items": {"type": "object", "properties": {
            "view": {"type": "string", "description": "a view id from the list"},
            "title": {"type": "string"},
            "note": {"type": "string", "description": "one short sentence on what it shows"}},
            "required": ["view", "title", "note"], "additionalProperties": False}}},
        "required": ["title", "intro", "sections"], "additionalProperties": False}}},
    "required": ["title", "pages"], "additionalProperties": False}

LAYOUT_PROMPT = """You design a personal app for {user} on top of their own database, which an AI built from their chat
messages. The views below already exist; each has an id, what it computes, and a preview of its numbers. Arrange them
into 3-5 pages (for example: what they keep asking about, people, places and spending, health, a timeline, and an
inbox of things to check), in the order that is most useful to them, with short plain titles and one-sentence notes.
Use every view at most once and only the ids listed. Put the views that answer their most frequent questions first.

VIEWS:
{views}"""


def describe(view: dict, data: dict | None = None) -> str:
    if view["view"] == "tile":
        const = ", ".join(f"{k} = {v}" for k, v in view["const"].items()) or "all rows"
        by = ", ".join(view["series"]) or "nothing"
        preview = ""
        if data:
            tops = sorted(((k, c["v"]) for k, c in data["cells"].items() if k[0] == "year"), key=lambda x: -x[1])[:3]
            preview = "; ".join(f"{k[1]} {'/'.join(k[2])} {k[3]}: {v:,.1f}" for k, v in tops)
        return (f"{view['op']} of {view['table']}.{view['column']} where {const}, by {by}, per month and year "
                f"({len(view['questions'])} logged questions, e.g. {view['questions'][0]!r}). Preview: {preview}")
    if view["view"] == "people":
        return f"one card per person with totals of everything linked to them ({len(view['people'])} people)"
    if view["view"] == "top":
        return f"most frequent {view['column']} of {view['table']}, per year and per category"
    if view["view"] == "cross":
        return f"total amount of {view['table']} by {view['columns'][0]} and {view['columns'][1]}"
    if view["view"] == "timeline":
        return "every dated row, month by month"
    if view["view"] == "inbox":
        return f"{view.get('n', 0)} rows the database doubts and person pairs it could not decide: ask the user"
    return view["view"]


def layout(views: list[dict], data: dict, user: str, model: str = "openai:gpt-6-luna") -> dict:
    lines = "\n".join(f"- {v['id']}: {describe(v, data.get(v['id']))}" for v in views)
    out, _ = chat_json(model, LAYOUT_PROMPT.format(user=user, views=lines), LAYOUT_SCHEMA, schema_name="app",
                       tag="appgen:layout", effort="low", max_tokens=6000)
    known = {v["id"] for v in views}
    seen = set()
    for page in out["pages"]:   # keep only real ids, each once; append anything the layout forgot
        page["sections"] = [s for s in page["sections"] if s["view"] in known and not (s["view"] in seen or seen.add(s["view"]))]
    missing = [v for v in views if v["id"] not in seen]
    if missing:
        out["pages"].append({"title": "More", "intro": "", "sections": [{"view": v["id"], "title": v["id"], "note": ""} for v in missing]})
    return out


# ------------------------------------------------------------------------------------------------------ render
def line_panel(title: str, series: dict[str, list[tuple[str, float]]], colors: dict, unit: str = "",
               integer: bool = False, zero: bool = True) -> str:
    """One small-multiple panel: a line per series over months (<= 4 series), hover titles on every point."""
    months = sorted({m for pts in series.values() for m, _ in pts})
    if not months:
        return ""
    vmax = max((v for pts in series.values() for _, v in pts), default=1) or 1
    vmin = 0.0 if zero else min(v for pts in series.values() for _, v in pts)
    pad = 0.0 if zero else max((vmax - vmin) * 0.1, abs(vmax) * 0.01)
    vmin, vmax = vmin - pad, vmax + pad   # lines of levels (weight, sleep) zoom in; totals and counts keep a zero baseline
    w, h, l, b = 560, 170, 46, 22
    x = lambda m: l + (w - l - 26) * (months.index(m) / max(1, len(months) - 1))
    y = lambda v: 10 + (h - b - 10) * (1 - (v - vmin) / ((vmax - vmin) or 1))
    tick = (lambda v: f"{round(v):,}") if integer else money_fmt
    levels = [vmin + (vmax - vmin) * f for f in (0, 0.5, 1)]
    grid = "".join(f'<line x1="{l}" x2="{w - 10}" y1="{y(v):.1f}" y2="{y(v):.1f}" class="grid"/>'
                   f'<text x="{l - 6}" y="{y(v) + 4:.1f}" text-anchor="end" class="ax">{tick(v)}</text>' for v in levels)
    ticks = "".join(f'<text x="{x(m):.1f}" y="{h - 6}" text-anchor="middle" class="ax">{esc(m[2:])}</text>'
                    for i, m in enumerate(months) if i % max(1, len(months) // 8) == 0)
    paths = []
    for name, pts in series.items():
        c = colors.get(name, 1)
        pts = sorted(pts)
        d = " ".join(f"{'M' if i == 0 else 'L'}{x(m):.1f},{y(v):.1f}" for i, (m, v) in enumerate(pts))
        dots = "".join(f'<circle cx="{x(m):.1f}" cy="{y(v):.1f}" r="4" class="dot" style="fill:var(--series-{c})">'
                       f'<title>{esc(name)} · {esc(m)}: {tick(v)} {esc(unit)}</title></circle>' for m, v in pts)
        paths.append(f'<path d="{d}" class="ln" style="stroke:var(--series-{c})"/>{dots}')
    legend = "".join(f'<span class="lg"><i style="background:var(--series-{colors.get(n, 1)})"></i>{esc(n)}</span>'
                     for n in series) if len(series) > 1 else ""
    return (f'<figure class="panel"><figcaption><b>{esc(title)}</b> {legend}</figcaption>'
            f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" aria-label="{esc(title)}">{grid}{ticks}{"".join(paths)}</svg></figure>')


def bar_panel(title: str, items: list[tuple[str, float]], unit: str = "", integer: bool = False) -> str:
    """Ranked horizontal bars (one hue: the bars are one measure), the value at the end of each bar."""
    if not items:
        return ""
    fmt = (lambda v: f"{round(v):,}") if integer else money_fmt
    vmax = max(v for _, v in items) or 1
    rows = "".join(f'<g><title>{esc(n)}: {fmt(v)} {esc(unit)}</title>'
                   f'<text x="172" y="{i * 24 + 13}" text-anchor="end" class="ax">{esc(str(n)[:24])}</text>'
                   f'<rect x="180" y="{i * 24}" width="{max(2, 300 * v / vmax):.1f}" height="16" rx="4" class="bar"/>'
                   f'<text x="{186 + 300 * v / vmax:.1f}" y="{i * 24 + 13}" class="val">{fmt(v)}</text></g>'
                   for i, (n, v) in enumerate(items))
    return (f'<figure class="panel"><figcaption><b>{esc(title)}</b></figcaption><svg viewBox="0 0 560 {len(items) * 24}" '
            f'width="560" height="{len(items) * 24}" role="img" aria-label="{esc(title)}">{rows}</svg></figure>')


def render_tile(reader: DetReader, tile: dict, data: dict, sec: dict) -> str:
    cells = data["cells"]
    integer = tile["op"] == "count"
    series_names = sorted({k[2] for k in cells}, key=lambda s: -sum(c["v"] for k, c in cells.items() if k[2] == s and k[0] == "all"))
    currencies = sorted({k[3] for k in cells})
    panels = []
    many = len(series_names) > 4 and tile["op"] in ("sum", "count")
    for cur in currencies:
        if many:   # many series: lines would tangle; rank the totals instead (top 10)
            tot = sorted(((("/".join(s) or "all"), c["v"]) for k, c in cells.items() if k[0] == "all" and k[3] == cur
                          for s in [k[2]]), key=lambda x: -x[1])[:10]
            panels.append(bar_panel(f"{cur + ' · ' if cur else ''}top {len(tot)} overall", tot, cur, integer))
            continue
        shown = [s for s in series_names if any(k[2] == s and k[3] == cur for k in cells)][:4]
        colors = {"/".join(s) or "all": i + 1 for i, s in enumerate(shown)}
        series = {"/".join(s) or "all": [(k[1], c["v"]) for k, c in cells.items() if k[0] == "month" and k[2] == s and k[3] == cur]
                  for s in shown}
        series = {n: pts for n, pts in series.items() if pts}
        means = [statistics.mean(v for _, v in pts) for pts in series.values()]
        if series and min(means) > 0 and max(means) / min(means) > 3:   # different scales (kg vs hours): one axis each
            for n, pts in series.items():
                panels.append(line_panel(f"{n}{' · ' + cur if cur else ''} per month", {n: pts}, colors, cur, integer,
                                         zero=tile["op"] in ("sum", "count")))
        elif series:
            panels.append(line_panel(f"{cur + ' ' if cur else ''}per month", series, colors, cur, integer,
                                     zero=tile["op"] in ("sum", "count")))
    years = sorted({k[1] for k in cells if k[0] == "year"})
    head = "".join(f"<th>{esc(y)}</th>" for y in years) + "<th>all</th>"
    lines = []
    for s in series_names:
        for cur in currencies:
            row = [cells.get(("year", y, s, cur)) for y in years] + [cells.get(("all", "all", s, cur))]
            if not any(row):
                continue
            num = (lambda v: f"{round(v):,}") if integer else money_fmt
            fmt = lambda c: "" if not c else (f"{num(c['v'])}" + (f" <span class='muted'>({esc(c['what'])}, {esc(c['at'])})</span>" if c.get("what") else ""))
            lines.append(f"<tr><td>{esc('/'.join(s) or 'all')}</td><td>{esc(cur)}</td>" + "".join(f"<td>{fmt(c)}</td>" for c in row) + "</tr>")
    body = "".join(lines[:15]) + (f"<tr><td colspan='9'><details><summary>all {len(lines)} rows</summary><table>"
                                  + "".join(lines[15:]) + "</table></details></td></tr>" if len(lines) > 15 else "")
    sql, args = tile_sql(reader, tile)
    asked = "; ".join(esc(q) for q in tile["questions"][:3])
    return (f'<section class="view"><h3>{esc(sec["title"])}</h3><p class="muted">{esc(sec["note"])}</p>'
            f'<div class="multiples">{"".join(panels)}</div>'
            f'<details open><summary>Per year</summary><table><tr><th>{esc(" / ".join(tile["series"]) or "")}</th><th></th>{head}</tr>{body}</table></details>'
            f'<details><summary>Why this is here, and how it is computed</summary><p class="muted">You asked: {asked}</p>'
            f'<pre>{esc(sql)}</pre><p class="muted">parameters: {esc(args)}</p></details></section>')


def render_people(view: dict, sec: dict) -> str:
    cards = []
    for person, cells in sorted(view["people"].items(), key=lambda x: -max((c["n"] for c in x[1].values()), default=0)):
        lines = []
        overall = {key.split("|")[0]: c["n"] for key, c in cells.items() if key.split("|")[1] == "all"}
        for key, c in sorted(cells.items(), key=lambda x: (x[0].split("|")[1] != "all", -x[1]["n"])):
            t, k, v = key.split("|")
            if k != "all" and c["n"] == overall.get(t):
                continue   # the same rows as the overall line (every expense with them is dining)
            what = f"{t}" if k == "all" else f"{t} · {v}"
            tot = " + ".join(f"{money_fmt(a)} {cur}" for cur, a in c["total"].items())
            lines.append(f"<li><span class='k'>{esc(what)}</span> {c['n']}× · {esc(tot)} · last {esc(c['last'])}"
                         f"{' at ' + esc(c['last_what']) if c['last_what'] else ''}</li>")
        cards.append(f'<div class="card"><h4>{esc(person)}</h4><ul>{"".join(lines[:8])}</ul></div>')
    return f'<section class="view"><h3>{esc(sec["title"])}</h3><p class="muted">{esc(sec["note"])}</p><div class="cards">{"".join(cards)}</div></section>'


def render_top(view: dict, sec: dict) -> str:
    rows = []
    for key, top in sorted(view["top"].items()):
        k, v, period = key.split("|")
        if k != "all" and period != "all":
            continue
        rows.append(f"<tr><td>{esc('everything' if k == 'all' else v)}</td><td>{esc(period)}</td><td>"
                    + ", ".join(f"{esc(name)} ({n})" for name, n in top) + "</td></tr>")
    return (f'<section class="view"><h3>{esc(sec["title"])}</h3><p class="muted">{esc(sec["note"])}</p>'
            f'<details open><summary>Top {esc(view["column"])}</summary><table><tr><th>kind</th><th>when</th><th>most frequent</th></tr>{"".join(rows[:40])}</table></details></section>')


def render_cross(view: dict, sec: dict) -> str:
    a, b = view["columns"]
    rows = "".join(f"<tr><td>{esc(k.split('|')[0])}</td><td>{esc(k.split('|')[1])}</td><td>"
                   + " + ".join(f"{money_fmt(v)} {esc(c)}" for c, v in tot.items()) + "</td></tr>"
                   for k, tot in sorted(view["totals"].items()))
    return (f'<section class="view"><h3>{esc(sec["title"])}</h3><p class="muted">{esc(sec["note"])}</p>'
            f'<table><tr><th>{esc(a)}</th><th>{esc(b)}</th><th>total</th></tr>{rows}</table></section>')


def render_inbox(view: dict, sec: dict) -> str:
    items = []
    for it in view.get("items", []):
        if it["kind"] == "row":
            flags = ", ".join(s.replace("_", " ") for s in it["signals"]) or "Jev doubts it"
            hot = set(it.get("suggest", {}))
            fields = "".join(f"<li{' class=hot' if k in hot or k + '_id' in hot else ''}><span class='k'>{esc(k.replace('_', ' '))}</span> {esc(v)}</li>"
                             for k, v in json.loads(it["row"]).items() if k != "person")
            fix = "; ".join(f"{k.replace('_id', '').replace('_', ' ')} → {v}" for k, v in it.get("suggest_text", {}).items())
            items.append(f'<div class="card inbox" data-key="{esc(it["key"])}"><p class="muted">{esc(it["sent"][:16].replace("T", " "))} · you said</p>'
                         f'<p>“{esc(it["message"][:220])}”</p><p class="muted">stored in {esc(it["table"])} as</p><ul>{fields}</ul>'
                         f'<p class="muted">why we ask: {esc(flags)}</p>'
                         + (f'<p><b>Suggested:</b> {esc(fix)}</p><p><button data-a="accept">Apply</button> ' if fix else '<p>')
                         + '<button data-a="ok">Looks right</button> <button data-a="fix">Something else is wrong</button></p></div>')
        else:
            items.append(f'<div class="card inbox" data-key="{esc(it["key"])}"><p>Are <b>{esc(it["a"])}</b> and <b>{esc(it["b"])}</b> the same person?</p>'
                         f'<p class="muted">{esc(" | ".join(it["context"])[:260])}</p><p class="muted">Jev: {it["p"]:.2f}</p>'
                         f'<p><button data-a="same">Same person</button> <button data-a="different">Different people</button></p></div>')
    script = ("<script>document.querySelectorAll('.inbox button').forEach(b=>b.onclick=()=>{const k=b.closest('.inbox').dataset.key;"
              "const s=JSON.parse(localStorage.getItem('fluiddb-inbox')||'{}');s[k]=b.dataset.a;localStorage.setItem('fluiddb-inbox',JSON.stringify(s));"
              "b.closest('.inbox').classList.add('done');});</script>")
    return (f'<section class="view"><h3>{esc(sec["title"])}</h3><p class="muted">{esc(sec["note"])}</p>'
            f'<div class="cards">{"".join(items)}</div>{script}</section>')


APP_CSS = """
.panel{margin:0}.grid{stroke:var(--line);stroke-width:1}.ln{fill:none;stroke-width:2}.dot{stroke:var(--surface-1);stroke-width:2}
.lg{font-size:12px;color:var(--text-secondary);margin-left:10px}.lg i{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:4px}
nav a{margin-right:14px;color:var(--text-secondary);text-decoration:none} nav a:hover,nav a.on{color:var(--text-primary);font-weight:600}
.view{margin:18px 0 30px} .view h3{margin:0 0 2px;font-size:15px} pre{white-space:pre-wrap;font-size:11px;background:var(--surface-2);padding:8px;border-radius:6px}
.inbox.done{opacity:.45} .inbox li.hot{background:color-mix(in srgb,var(--series-4) 22%,transparent);border-radius:4px} button{font:inherit;padding:3px 10px;border-radius:6px;border:1px solid var(--line);background:var(--surface-1);color:var(--text-primary);cursor:pointer}
"""


def page_file(i: int) -> str:
    return "index.html" if i == 0 else f"page{i + 1}.html"


def render(spec: dict, views: dict, data: dict, reader: DetReader, explorer_db: ExplorerDB, user: str, source: str) -> dict:
    """{file name: html}, one file per page, sharing one nav."""
    light = "".join(f"--series-{i + 1}:{c};" for i, c in enumerate(PALETTE))
    dark = "".join(f"--series-{i + 1}:{c};" for i, c in enumerate(PALETTE_DARK))
    files = {}
    for i, page in enumerate(spec["pages"]):
        nav = " ".join(f'<a href="{page_file(j)}"{" class=on" if j == i else ""}>{esc(p["title"])}</a>'
                       for j, p in enumerate(spec["pages"]))
        parts = [f'<h2>{esc(page["title"])}</h2><p class="muted">{esc(page["intro"])}</p>']
        for sec in page["sections"]:
            v = views[sec["view"]]
            if v["view"] == "tile":
                parts.append(render_tile(reader, v, data[v["id"]], sec))
            elif v["view"] == "people":
                parts.append(render_people(v, sec))
            elif v["view"] == "top":
                parts.append(render_top(v, sec))
            elif v["view"] == "cross":
                parts.append(render_cross(v, sec))
            elif v["view"] == "inbox":
                parts.append(render_inbox(v, sec))
            elif v["view"] == "timeline":
                colors = {t: min(j, 7) + 1 for j, t in enumerate(explorer_db.tables)}
                parts.append(f'<section class="view"><h3>{esc(sec["title"])}</h3><p class="muted">{esc(sec["note"])}</p>'
                             f'{timeline(explorer_db, colors, user)}</section>')
        files[page_file(i)] = f"""<!doctype html><html><head><meta charset="utf-8"><title>{esc(page["title"])} · {esc(spec["title"])}</title>
<style>{EXPLORER_CSS % {"light": light, "dark": dark}}{APP_CSS}</style></head><body><div class="viz-root">
<h1>{esc(spec["title"])}</h1><p class="muted">Generated from <code>{esc(Path(source).name)}</code> on {datetime.now():%Y-%m-%d}: views
from the questions you asked and from the schema; every number is computed by code from your database.</p><nav>{nav}</nav>
{"".join(parts)}</div></body></html>"""
    return files


def build(db_path: str, plans: list[tuple[str, dict]], user: str, now: datetime, inbox: dict | None = None,
          model: str = "openai:gpt-6-luna", layout_spec: dict | None = None) -> dict:
    """Everything the app needs: views, their data, the layout and the HTML. Returns timings too."""
    t0 = time.time()
    reader = DetReader(db_path, user, now)
    tiles = tiles_from_plans(reader, plans)
    views = {v["id"]: v for v in tiles}
    data = {v["id"]: compute_tile(reader, v) for v in tiles}
    for a in list(tiles):   # two tiles with the same numbers (weight by category and by category/unit): keep the simpler
        for b in tiles:
            if a is not b and a["id"] in views and b["id"] in views and (a["table"], a["op"], a["column"]) == (b["table"], b["op"], b["column"]) \
                    and len(a["series"]) > len(b["series"]) and sorted((k[0], k[1], k[3], round(c["v"], 6)) for k, c in data[a["id"]]["cells"].items()) \
                    == sorted((k[0], k[1], k[3], round(c["v"], 6)) for k, c in data[b["id"]]["cells"].items()):
                b["questions"] += a["questions"]
                del views[a["id"]]
    tiles = [t for t in tiles if t["id"] in views]
    views["people"] = people_view(reader)
    for v in breakdown_views(reader):
        if v.get("totals") or v.get("top"):
            views[v["id"]] = v
    views["timeline"] = {"id": "timeline", "view": "timeline"}
    if inbox:
        views["inbox"] = {"id": "inbox", "view": "inbox", **inbox}
    checks = {v["id"]: check_sql(reader, v, data[v["id"]]) for v in tiles}
    t_views = time.time() - t0
    spec = layout_spec or layout(list(views.values()), data, user, model)
    t1 = time.time()
    pages = render(spec, views, data, reader, ExplorerDB(db_path), user, db_path)
    return {"views": views, "data": data, "spec": spec, "pages": pages, "sql_checks": checks, "reader": reader,
            "seconds": {"views": round(t_views, 2), "render": round(time.time() - t1, 2)}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--plans", required=True, help="JSON list of [question, plan] the verifier trusted")
    ap.add_argument("--user", default="Adam Zvada")
    ap.add_argument("--now", default="2026-09-30T21:00:00")
    ap.add_argument("--out", default="lab/app/index.html")
    args = ap.parse_args()
    plans = [tuple(x) for x in json.loads(Path(args.plans).read_text())]
    app = build(args.db, plans, args.user, datetime.fromisoformat(args.now))
    out = Path(args.out).parent
    out.mkdir(parents=True, exist_ok=True)
    for name, page in app["pages"].items():
        (out / name).write_text(page)
    print(f"wrote {len(app['pages'])} pages to {out}: {len(app['views'])} views", app["seconds"])


if __name__ == "__main__":
    main()
