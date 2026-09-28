"""Generate a browsable explorer (one static HTML file) from any FluidDB database.

Nothing here knows the schema in advance. It reads the catalog (table + column descriptions),
resolves `*_id` links to readable names, and asks Jev how each table is best shown
(timeline / cards / bars / list / links). Then it renders:

  * a life timeline of every dated row across tables
  * people cards gathering everything linked to each person
  * spending by merchant, one small chart per currency
  * a schema map of tables and their links
  * every table in the view Jev picked, with the raw rows one click away

  .venv/bin/python -m lab.explorer --db lab/runs/v21jev__openai_gpt-6-luna__life_stream/db.sqlite \\
      --user "Adam Zvada" --out lab/explorer/index.html
"""
# @ref LLP 0010#generated-explorer — an interface generated from the catalog, views picked by Jev
from __future__ import annotations

import argparse
import html
import json
import sqlite3
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from lab.common import jev

PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
PALETTE_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"]
DATE_COLS = ["date", "start_datetime", "flight_date", "sent_datetime", "start_date", "move_in_date", "sold_date",
             "occasion_date", "due_date", "booked_datetime", "event_date"]
LABEL_COLS = ["name", "title", "gift_idea", "interest", "allergen", "activity_type", "description", "subject", "role",
              "relationship", "preference", "type", "make"]
SYNONYMS = {"location": "places", "city": "places", "home_city": "places"}

VIEW = jev.choice(
    "How should the rows of `table` be shown to the person who owns this data?",
    {
        "timeline": "Each row is a dated event, entry or activity; show them in time order",
        "cards": "Each row is a person, organization, place or other thing; show one card per row",
        "bars": "Rows carry money amounts or quantities worth comparing; show bars",
        "links": "Each row connects two other rows (a relationship or membership); show who is linked to whom",
        "list": "Short facts; a simple list is enough",
    },
)


def esc(x) -> str:
    return html.escape(str(x))


class DB:
    def __init__(self, path: str):
        self.db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        self.db.row_factory = sqlite3.Row
        self.tables = [r[0] for r in self.db.execute("SELECT name FROM _tables ORDER BY name")]
        self.desc = {r[0]: r[1] for r in self.db.execute("SELECT name, description FROM _tables")}
        self.cols: dict[str, list[tuple[str, str, str]]] = {}
        for t in self.tables:
            meta = {r[0]: (r[1], r[2]) for r in self.db.execute(
                "SELECT column_name, type, description FROM _columns WHERE table_name=?", (t,))}
            self.cols[t] = [(r["name"], *meta.get(r["name"], (r["type"], ""))) for r in self.db.execute(
                f"PRAGMA table_info('{t}')") if r["name"] not in ("id", "_src", "_ts")]
        self.rows = {t: [{k: r[k] for k in r.keys() if k not in ("_src", "_ts") and r[k] is not None}
                         for r in self.db.execute(f"SELECT * FROM '{t}' ORDER BY id")] for t in self.tables}

    def target(self, col: str) -> str | None:
        if not col.endswith("_id") or col == "id":
            return None
        parts = col[:-3].split("_")
        for i in range(len(parts)):
            stem = "_".join(parts[i:])
            if stem in SYNONYMS and SYNONYMS[stem] in self.tables:
                return SYNONYMS[stem]
            for cand in (stem + "s", stem + "es", stem[:-1] + "ies" if stem.endswith("y") else "", stem,
                         "people" if stem == "person" else ""):
                if cand and cand in self.tables:
                    return cand
        return None

    def label(self, table: str, row: dict) -> str:
        key = next((c for c in LABEL_COLS if row.get(c)), None)
        if key:
            text = str(row[key])
        else:  # a pure link row: name it by what it links ("Adam Zvada → Apartment")
            linked = [self.name_of(self.target(k), v) for k, v in row.items() if self.target(k)]
            text = " → ".join(linked[:2]) if linked else f"{table} #{row['id']}"
        if table == "vehicles" and row.get("model"):
            text = f"{row.get('make', '')} {row['model']}".strip()
        return text

    def name_of(self, table: str, rid) -> str:
        row = next((r for r in self.rows.get(table, []) if r["id"] == rid), None)
        return self.label(table, row) if row else f"{table} #{rid}"

    def resolved(self, table: str, row: dict) -> dict:
        """Row with `*_id` links replaced by readable names."""
        out = {}
        for k, v in row.items():
            if k == "id":
                continue
            tgt = self.target(k)
            out[k[:-3] if tgt else k] = self.name_of(tgt, v) if tgt else v
        return out


MONEY = ("amount", "sale_amount", "monthly_fee", "latest_funding_amount")


def summary(db: DB, table: str, row: dict, skip: tuple = ()) -> str:
    """One readable line for a row; `skip` hides names the reader already knows (the owner, the card's person)."""
    r = db.resolved(table, row)
    head = db.label(table, row)
    bits = []
    for k, v in r.items():
        if k in DATE_COLS or str(v) == head or k == "currency" or k.endswith("currency") or str(v) in skip:
            continue
        if k in MONEY and isinstance(v, (int, float)):
            keys = ([k.replace("amount", "currency")] if "amount" in k else []) + [f"{k}_currency", "currency"]
            cur = next((row[c] for c in keys if row.get(c)), "")
            bits.append(f"{v:,.2f} {cur}".replace(".00 ", " ").strip())
        else:
            text = str(v)
            bits.append(f"{k.replace('_', ' ')}: {text[:110] + '…' if len(text) > 110 else text}")
    return head + (" · " + " · ".join(bits[:5]) if bits else "")


def primary_date(db: DB, table: str) -> str | None:
    names = [c for c, _, _ in db.cols[table]]
    return next((c for c in DATE_COLS if c in names), None)


# ------------------------------------------------------------------ sections
def when(value) -> str:
    return str(value)[:16].replace("T", " ").removesuffix(" 00:00")


def timeline(db: DB, colors: dict, owner: str) -> str:
    items = []
    for t in db.tables:
        dc = primary_date(db, t)
        if not dc:
            continue
        for row in db.rows[t]:
            if row.get(dc):
                items.append((when(row[dc]), t, summary(db, t, row, skip=(owner,))))
    items.sort()
    by_month = defaultdict(list)
    for stamp, t, text in items:
        by_month[stamp[:7]].append((stamp, t, text))
    long = len(items) > 150  # a year of life: one expandable line per month
    out = []
    for month, rows in sorted(by_month.items()):
        counts = defaultdict(int)
        for _, t, _ in rows:
            counts[t] += 1
        summary_line = " · ".join(f'<span class="chip" style="--c:var(--series-{colors[t]})">{n} {esc(t.replace("_", " "))}</span>'
                                  for t, n in sorted(counts.items(), key=lambda x: -x[1]))
        body = "".join(f'<div class="tl-item"><span class="tl-when">{esc(stamp)}</span>'
                       f'<span class="chip" style="--c:var(--series-{colors[t]})">{esc(t.replace("_", " "))}</span>'
                       f'<span class="tl-text">{esc(text)}</span></div>' for stamp, t, text in rows)
        if long:
            out.append(f'<details class="month-block"><summary><b>{esc(month)}</b> {summary_line}</summary>{body}</details>')
        else:
            out.append(f'<h3 class="month">{esc(month)}</h3>{body}')
    return "\n".join(out)


def people_cards(db: DB, owner: str) -> str:
    if "people" not in db.tables:
        return "<p>No people table.</p>"
    facts = defaultdict(list)
    for t in db.tables:
        if t == "people":
            continue
        for row in db.rows[t]:
            for k, v in row.items():
                if db.target(k) == "people":
                    role = k[:-3].replace("_", " ")
                    facts[v].append((t, role, row))
    cards = []
    for p in db.rows["people"]:
        me = p.get("name", "")
        lines = "".join(f'<li><span class="k">{esc(t.replace("_", " "))}</span> {esc(summary(db, t, r, skip=(owner, me)))}</li>'
                        for t, _, r in facts[p["id"]][:9])
        extra = " · ".join(f"{k.replace('_', ' ')}: {v}" for k, v in p.items() if k not in ("id", "name"))
        cards.append(f'<div class="card"><h4>{esc(p.get("name", p["id"]))}</h4>'
                     f'<p class="muted">{esc(extra)}</p><ul>{lines or "<li class=muted>no linked facts</li>"}</ul></div>')
    return f'<div class="cards">{"".join(cards)}</div>'


def spending(db: DB) -> str:
    rows = []
    for t in db.tables:
        names = [c for c, _, _ in db.cols[t]]
        if "amount" in names and "currency" in names:
            for r in db.rows[t]:
                if r.get("amount") is not None:
                    res = db.resolved(t, r)
                    who = res.get("restaurant") or r.get("merchant") or r.get("airline") or db.label(t, r)
                    rows.append((r.get("currency"), str(who), float(r["amount"]), t, r.get(primary_date(db, t) or "", "")))
    by_cur = defaultdict(list)
    for cur, who, amt, t, when in rows:
        by_cur[cur].append((who, amt, t, when))
    months = sorted({str(w)[:7] for _, _, _, _, w in rows if w})
    monthly = []
    if len(months) > 3:  # a long history: monthly totals per currency first
        for cur, items in sorted(by_cur.items()):
            tot = defaultdict(float)
            for _, amt, _, w in items:
                if w:
                    tot[str(w)[:7]] += amt
            mx = max(tot.values()) if tot else 1
            bw, gap, h = 30, 6, 150
            bars = []
            for i, m in enumerate(months):
                v = tot.get(m, 0)
                bh = (h - 30) * v / mx if mx else 0
                x = 10 + i * (bw + gap)
                bars.append(f'<g><title>{esc(m)}: {v:,.2f} {esc(cur)}</title>'
                            f'<rect x="{x}" y="{h - 20 - bh:.1f}" width="{bw}" height="{max(bh, 1):.1f}" rx="3" class="bar"/>'
                            f'<text x="{x + bw / 2}" y="{h - 6}" text-anchor="middle" class="ax">{esc(m[5:])}</text></g>')
            width = 20 + len(months) * (bw + gap)
            monthly.append(f'<figure class="multiple"><figcaption><b>{esc(cur)}</b> per month · total {sum(tot.values()):,.2f}</figcaption>'
                           f'<svg viewBox="0 0 {width} {h}" width="{width}" height="{h}" role="img" aria-label="Monthly spending in {esc(cur)}">'
                           f'{"".join(bars)}</svg></figure>')
    blocks = []
    for cur, items in sorted(by_cur.items()):
        items.sort(key=lambda x: -x[1])
        total = sum(a for _, a, _, _ in items)
        mx = max(a for _, a, _, _ in items)
        w, bar_h, gap, label_w = 520, 18, 10, 150
        h = len(items) * (bar_h + gap)
        bars = []
        for i, (who, amt, t, when) in enumerate(items):
            y = i * (bar_h + gap)
            bw = max(2, (w - label_w - 90) * amt / mx)
            bars.append(
                f'<g><title>{esc(who)}: {amt:,.2f} {esc(cur)} ({esc(t)}, {esc(when)})</title>'
                f'<text x="{label_w - 8}" y="{y + bar_h * 0.72}" text-anchor="end" class="ax">{esc(who[:22])}</text>'
                f'<rect x="{label_w}" y="{y}" width="{bw:.1f}" height="{bar_h}" rx="4" class="bar"/>'
                f'<text x="{label_w + bw + 6}" y="{y + bar_h * 0.72}" class="val">{amt:,.2f}</text></g>')
        blocks.append(f'<figure class="multiple"><figcaption><b>{esc(cur)}</b> · total {total:,.2f} {esc(cur)} · '
                      f'{len(items)} items</figcaption><svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" '
                      f'aria-label="Spending in {esc(cur)} by merchant">{"".join(bars)}</svg></figure>')
    table = "".join(f"<tr><td>{esc(when)}</td><td>{esc(who)}</td><td>{amt:,.2f}</td><td>{esc(cur)}</td><td>{esc(t)}</td></tr>"
                    for cur, who, amt, t, when in sorted(rows, key=lambda x: str(x[4])))
    if monthly:  # keep only the 8 biggest merchants per currency next to the monthly view
        blocks = []
        for cur, items in sorted(by_cur.items()):
            agg = defaultdict(float)
            for who, amt, _, _ in items:
                agg[who] += amt
            top = sorted(agg.items(), key=lambda x: -x[1])[:8]
            mx = top[0][1] if top else 1
            bars = "".join(f'<g><title>{esc(w)}: {a:,.2f} {esc(cur)}</title><text x="142" y="{i * 26 + 13}" text-anchor="end" class="ax">{esc(w[:20])}</text>'
                           f'<rect x="150" y="{i * 26}" width="{max(2, 260 * a / mx):.1f}" height="18" rx="4" class="bar"/>'
                           f'<text x="{156 + 260 * a / mx:.1f}" y="{i * 26 + 13}" class="val">{a:,.0f}</text></g>' for i, (w, a) in enumerate(top))
            blocks.append(f'<figure class="multiple"><figcaption>Top merchants · <b>{esc(cur)}</b></figcaption>'
                          f'<svg viewBox="0 0 520 {len(top) * 26}" width="520" height="{len(top) * 26}">{bars}</svg></figure>')
        blocks = monthly + blocks
    return (f'<div class="multiples">{"".join(blocks)}</div><details><summary>Table view</summary><table>'
            f'<tr><th>date</th><th>what</th><th>amount</th><th>currency</th><th>table</th></tr>{table}</table></details>')


def schema_map(db: DB) -> str:
    edges = set()
    for t in db.tables:
        for c, _, _ in db.cols[t]:
            tgt = db.target(c)
            if tgt and tgt != t:
                edges.add((t, tgt))
    hubs = sorted({b for _, b in edges}, key=lambda x: -sum(1 for _, b in edges if b == x))
    leaves = [t for t in db.tables if t not in hubs]
    row_h, w = 30, 760
    h = max(len(hubs), len(leaves)) * row_h + 20
    pos = {t: (40, 20 + i * row_h * max(1, len(leaves) / max(len(hubs), 1))) for i, t in enumerate(hubs)}
    pos.update({t: (w - 260, 20 + i * row_h) for i, t in enumerate(leaves)})
    parts = []
    for a, b in edges:
        (xa, ya), (xb, yb) = pos[a], pos[b]
        xa2, xb2 = (xa, xb + 190) if xa > xb else (xa + 190, xb)
        parts.append(f'<path d="M{xa2},{ya} C{(xa2 + xb2) / 2},{ya} {(xa2 + xb2) / 2},{yb} {xb2},{yb}" class="edge"/>')
    for t, (x, y) in pos.items():
        n = len(db.rows[t])
        parts.append(f'<g><title>{esc(t)}: {esc(db.desc.get(t, ""))}</title>'
                     f'<rect x="{x}" y="{y - 11}" width="190" height="22" rx="6" class="node{" hub" if t in hubs else ""}"/>'
                     f'<text x="{x + 10}" y="{y + 4}" class="nodet">{esc(t)} <tspan class="muted">({n})</tspan></text></g>')
    return f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="Tables and their links">{"".join(parts)}</svg>'


def table_view(db: DB, t: str, view: str, owner: str = "") -> str:
    rows = db.rows[t]
    if view == "timeline" and primary_date(db, t):
        dc = primary_date(db, t)
        items = sorted(rows, key=lambda r: str(r.get(dc, "")))
        body = "".join(f'<div class="tl-item two"><span class="tl-when">{esc(when(r.get(dc, "")))}</span>'
                       f'<span class="tl-text">{esc(summary(db, t, r, skip=(owner,)))}</span></div>' for r in items)
    elif view == "cards":
        body = '<div class="cards">' + "".join(
            f'<div class="card"><h4>{esc(db.label(t, r))}</h4><ul>' + "".join(
                f'<li><span class="k">{esc(k.replace("_", " "))}</span> {esc(v)}</li>'
                for k, v in db.resolved(t, r).items() if str(v) != db.label(t, r)) + "</ul></div>" for r in rows) + "</div>"
    elif view == "links":
        body = "<ul>" + "".join(f"<li>{esc(summary(db, t, r, skip=(owner,)))}</li>" for r in rows) + "</ul>"
    else:  # list / bars fall back to a compact list (money gets its own section)
        body = "<ul>" + "".join(f"<li>{esc(summary(db, t, r, skip=(owner,)))}</li>" for r in rows) + "</ul>"
    head = "".join(f"<th>{esc(c)}</th>" for c, _, _ in db.cols[t])
    raw = "".join("<tr>" + "".join(f"<td>{esc(r.get(c, ''))}</td>" for c, _, _ in db.cols[t]) + "</tr>" for r in rows)
    return body + f"<details><summary>Raw rows</summary><table><tr>{head}</tr>{raw}</table></details>"


def pick_views(db: DB) -> dict:
    def one(t):
        state = {"table": t, "description": db.desc.get(t, ""),
                 "columns": [f"{c} {ty}: {d}" for c, ty, d in db.cols[t]], "example_rows": db.rows[t][:3]}
        a = jev.ask(state, {"view": VIEW}, tag="explorer:view")["view"]
        return t, (a["choice"], a["confidence"])
    with ThreadPoolExecutor(16) as pool:
        return dict(pool.map(one, db.tables))


CSS = """
.viz-root{color-scheme:light;--surface-1:#fcfcfb;--surface-2:#f3f2ef;--text-primary:#0b0b0b;--text-secondary:#52514e;
 --line:#dcdad3;%(light)s}
@media (prefers-color-scheme: dark){:root:where(:not([data-theme="light"])) .viz-root{color-scheme:dark;--surface-1:#1a1a19;
 --surface-2:#252523;--text-primary:#fff;--text-secondary:#c3c2b7;--line:#3a3935;%(dark)s}}
body{margin:0;font:14px/1.45 -apple-system,BlinkMacSystemFont,"Inter",sans-serif}
.viz-root{background:var(--surface-1);color:var(--text-primary);padding:28px 40px;max-width:1180px;margin:auto}
h1{font-size:22px;margin:0 0 4px} h2{font-size:16px;margin:34px 0 10px;border-bottom:1px solid var(--line);padding-bottom:6px}
h3.month{font-size:13px;color:var(--text-secondary);margin:14px 0 4px} .muted{color:var(--text-secondary)}
.tl-item{display:grid;grid-template-columns:120px 150px 1fr;gap:10px;padding:3px 0;align-items:baseline}
.tl-when{color:var(--text-secondary);font-variant-numeric:tabular-nums}
.tl-item.two{grid-template-columns:120px 1fr}
.chip{justify-self:start;font-size:12px;padding:1px 8px 1px 18px;border-radius:10px;background:var(--surface-2);position:relative}
.chip:before{content:"";position:absolute;left:7px;top:6px;width:7px;height:7px;border-radius:50%%;background:var(--c)}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}
.card{background:var(--surface-2);border-radius:10px;padding:10px 14px} .card h4{margin:0 0 4px;font-size:14px}
.card ul{margin:4px 0 0;padding-left:16px} .card li{margin:2px 0} .k{color:var(--text-secondary);font-size:12px;margin-right:4px}
.multiples{display:flex;flex-wrap:wrap;gap:28px} figure{margin:0} figcaption{margin-bottom:6px}
.bar{fill:var(--series-1)} .ax,.val{font-size:12px;fill:var(--text-secondary)} .val{fill:var(--text-primary)}
.edge{fill:none;stroke:var(--line);stroke-width:1.5} .node{fill:var(--surface-2);stroke:var(--line)} .node.hub{stroke:var(--series-1)}
.nodet{font-size:12px;fill:var(--text-primary)} .nodet .muted{fill:var(--text-secondary)}
table{border-collapse:collapse;font-size:12px;margin-top:6px} td,th{border-bottom:1px solid var(--line);padding:3px 8px;text-align:left}
details summary{cursor:pointer;color:var(--text-secondary);margin-top:6px} .badge{font-size:12px;color:var(--text-secondary)}
.tbl{margin-bottom:22px}
.month-block{padding:4px 0;border-bottom:1px solid var(--line)} .month-block summary{cursor:pointer;color:var(--text-primary)}
.month-block .chip{margin-left:6px}
"""


def render(db: DB, user: str, source: str) -> str:
    dated = [t for t in db.tables if primary_date(db, t)]
    dated.sort(key=lambda t: -len(db.rows[t]))
    colors = {t: min(i, 7) + 1 for i, t in enumerate(dated)}
    light = "".join(f"--series-{i + 1}:{c};" for i, c in enumerate(PALETTE))
    dark = "".join(f"--series-{i + 1}:{c};" for i, c in enumerate(PALETTE_DARK))
    views = pick_views(db)
    n_rows = sum(len(v) for v in db.rows.values())
    per_table = "".join(
        f'<div class="tbl"><h3>{esc(t)} <span class="badge">· {len(db.rows[t])} rows · Jev view: <b>{esc(v)}</b> '
        f'({c:.2f})</span></h3><p class="muted">{esc(db.desc.get(t, ""))}</p>{table_view(db, t, v, user)}</div>'
        for t, (v, c) in sorted(views.items()))
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>FluidDB explorer · {esc(user)}</title>
<style>{CSS % {"light": light, "dark": dark}}</style></head><body><div class="viz-root">
<h1>FluidDB explorer · {esc(user)}</h1>
<p class="muted">Generated from <code>{esc(source)}</code>: {len(db.tables)} tables, {n_rows} rows, built by the model
from chat messages. Nothing on this page is hard-coded to the schema; each table's view was chosen by Jev.</p>
<h2 id="timeline">Life timeline</h2>{timeline(db, colors, user)}
<h2 id="people">People</h2>{people_cards(db, user)}
<h2 id="spending">Spending by merchant (one chart per currency)</h2>{spending(db)}
<h2 id="schema">Schema map</h2><p class="muted">Tables the others link to on the left, everything else on the right.</p>{schema_map(db)}
<h2 id="tables">Every table, in the view Jev picked</h2>{per_table}
</div></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--user", default="the user")
    ap.add_argument("--out", default="lab/explorer/index.html")
    args = ap.parse_args()
    db = DB(args.db)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(db, args.user, args.db))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
