"""Round 6 (LLP 0012): the database proposes its own app, and knows what it doubts.

  setup      copy the round-5 database; re-ask the logged questions (cached) -> the trusted plans the app is built from
  truth      which stored rows are wrong, field by field, against the simulator's truth (no model)
  confidence code signals + Jev for every dated row -> _confidence
  calibrate  how well each strategy puts the wrong rows first (AUROC, share caught when reviewing 1/2/5/10%)
  inbox      an oracle user answers the top N inbox items of each strategy; wrong rows and app answers afterwards
  app        build the app (layout: one LLM call), write lab/app/index.html, screenshot it, check every tile's SQL
  evaluate   the 105 future questions read off the app (oracle lookup), before and after the inbox; det reader after
  year       the same generator on the year database of rounds 2-3, with its own questions

    .venv/bin/python -m lab.bench.app setup|truth|confidence|calibrate|inbox|app|evaluate|year [--budget 1.5]
"""
# @ref LLP 0012#protocol — strategies, oracle inbox, app lookup, generality, budget
from __future__ import annotations

import argparse
import ast
import json
import random
import re
import shutil
import sqlite3
import statistics
import subprocess
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from lab import appgen
from lab.bench import workload as W
from lab.bench.scale5k import _checks, _has_value
from lab.common.grade import jev_grade
from lab.common.llm import LAB_DIR, LEDGER
from lab.datasets.simulate_year import PEOPLE as SIM_PEOPLE
from lab.datasets.workload import PEOPLE as FIRST_NAMES
from lab.systems import confidence as C
from lab.systems import consolidate, query_log
from lab.systems.derived import fold
from lab.systems.det_reader import DetReader
from lab.systems.engine import Engine

RUN = LAB_DIR / "runs" / "app"
SRC = LAB_DIR / "runs" / "workload" / "db_opt.sqlite"
SCALE = LAB_DIR / "datasets" / "life_scale.json"
YEAR = LAB_DIR / "datasets" / "life_year.json"
YEAR_DB = LAB_DIR / "runs" / "year__v23__gpt-6-luna__low" / "db_typed.sqlite"
OUT = LAB_DIR / "results"
APP = LAB_DIR / "app"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
FIELD_COLS = {"date": ("date", "date_watched"), "amount": ("amount",), "currency": ("currency",), "merchant": ("merchant",),
              "with": ("with_person_id",), "km": ("distance",), "secs": ("duration_seconds",), "hours": ("value",),
              "kg": ("value",), "title": ("title",), "rating": ("rating",), "sent": ("achievement", "grade")}


def spent() -> float:
    return sum(c.cost for c in LEDGER.calls if not c.cached)


def guard(budget: float):
    if spent() > budget:
        raise SystemExit(f"budget: this run spent ${spent():.3f} > ${budget}")


def load(name: str):
    return json.loads((RUN / name).read_text())


# ------------------------------------------------------------------------------------------------------ truth
def _vals(d: dict, people: dict) -> list:
    v = [x for k, x in d.items() if k not in ("id", "_src", "_ts") and x is not None]
    return v + [people[x] for k, x in d.items() if k.endswith("_id") and x in people]


def _holds(vals: list, k: str, want) -> bool:
    folded = [fold(x) if isinstance(x, str) else x for x in vals]
    if k == "date":
        return any(isinstance(v, str) and v[:10] == want for v in vals)
    if k == "secs":
        return _has_value(vals, want) or _has_value(vals, f"{want // 60}:{want % 60:02d}")
    if k == "with":
        return any(_has_value(folded, fold(n)) for n in want)
    return _has_value(folded, fold(want)) if isinstance(want, str) else _has_value(vals, want)


def row_truth(db_path: Path) -> dict:
    """{"table|id": {"wrong": [fields], "want": {field: value}, "tier": A|C}} for every row a truth message produced."""
    ds = json.loads(SCALE.read_text())
    trace = {t["i"]: t for t in json.loads((LAB_DIR / "runs" / "scale5k" / "ingest.json").read_text())["trace"]}
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    people = {r[0]: r[1] for r in db.execute("SELECT id, name FROM people")}
    by_log = defaultdict(list)
    for (t,) in db.execute("SELECT name FROM _tables"):
        for r in db.execute(f"SELECT * FROM '{t}'"):
            d = dict(r)
            for lid in re.findall(r"\d+", str(d.get("_src") or "")):
                by_log[int(lid)].append((t, d))
    out = {}
    for i, m in enumerate(ds["messages"]):
        if "truth" not in m or i not in trace or not by_log.get(i + 1):
            continue
        truth = ast.literal_eval(m["truth"]) if isinstance(m["truth"], str) else m["truth"]
        checks = _checks(truth)
        t, d = max(by_log[i + 1], key=lambda r: sum(_holds(_vals(r[1], people), k, w) for k, w in checks.items()))
        e = out.setdefault(f"{t}|{d['id']}", {"table": t, "id": d["id"], "wrong": [], "want": {}, "tier": trace[i]["tier"],
                                               "kind": truth["kind"]})
        for k, w in checks.items():
            if not _holds(_vals(d, people), k, w) and k not in e["wrong"]:
                e["wrong"].append(k)
            e["want"].setdefault(k, w)
    return out


def truth_step():
    t = row_truth(RUN / "db.sqlite")
    (RUN / "truth.json").write_text(json.dumps(t, ensure_ascii=False))
    wrong = [e for e in t.values() if e["wrong"]]
    print(json.dumps({"rows": len(t), "wrong": len(wrong), "rate": round(len(wrong) / len(t), 4),
                      "fields": Counter(f for e in wrong for f in e["wrong"])}, indent=1))


# ------------------------------------------------------------------------------------------------------ setup
def workload():
    wl = json.loads(W.WL.read_text())
    return wl, datetime.fromisoformat(wl["now"])


def setup_step():
    RUN.mkdir(parents=True, exist_ok=True)
    shutil.copy(SRC, RUN / "db.sqlite")
    wl, now = workload()
    reader = DetReader(str(RUN / "db.sqlite"), wl["user"], now)
    det = W.det_batch(reader, wl["past"])
    plans = [(q["question"], query_log.plan_of(det["by_q"][q["id"]]["out"])) for q in wl["past"]
             if W.trusted(det["by_q"][q["id"]]["out"])]
    (RUN / "plans.json").write_text(json.dumps(plans, ensure_ascii=False, indent=1))
    print(f"{len(plans)} trusted plans of {len(wl['past'])} logged questions; live share {det['live_share']}; spent {spent():.4f}")


# ------------------------------------------------------------------------------------------------- confidence
def confidence_step(budget: float):
    wl, _ = workload()
    db = sqlite3.connect(RUN / "db.sqlite")
    n0, t0 = len(LEDGER.calls), time.time()
    conf = C.assess(db, wl["user"])
    calls = LEDGER.calls[n0:]
    guard(budget)
    out = {f"{t}|{i}": v for (t, i), v in conf.items()}
    (RUN / "confidence.json").write_text(json.dumps(out, ensure_ascii=False))
    print(json.dumps({"rows": len(out), "signals": C.signal_counts(conf), "seconds": round(time.time() - t0, 1),
                      "cost": round(sum(c.cost for c in calls), 5), "requests": len(calls), "spent": round(spent(), 4)}, indent=1))


def strategies(truth: dict, conf: dict, seed: int = 11) -> dict[str, dict[str, float]]:
    """Lower score = shown to the user first."""
    keys = [k for k in truth if k in conf]
    rnd = random.Random(seed)
    return {"random": {k: rnd.random() for k in keys},
            "planner_first": {k: (0.0 if truth[k]["tier"] != "A" else 1.0) + rnd.random() * 1e-3 for k in keys},
            "code": {k: -len(conf[k]["signals"]) + rnd.random() * 1e-3 for k in keys},
            "jev": {k: min(conf[k]["jev"].values(), default=1.0) + rnd.random() * 1e-6 for k in keys},
            "combined": {k: conf[k]["score"] + rnd.random() * 1e-9 for k in keys}}


def auroc(scores: dict[str, float], wrong: set[str]) -> float:
    """P(a wrong row scores lower than a right one), ties half."""
    order = sorted(scores, key=scores.get)
    ranks, i = {}, 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and scores[order[j + 1]] == scores[order[i]]:
            j += 1
        for k in order[i:j + 1]:
            ranks[k] = (i + j) / 2 + 1
        i = j + 1
    n_w, n_r = len(wrong), len(scores) - len(wrong)
    rank_sum = sum(ranks[k] for k in wrong)
    return round(1 - (rank_sum - n_w * (n_w + 1) / 2) / (n_w * n_r), 4)


def calibrate_step():
    truth, conf = load("truth.json"), load("confidence.json")
    keys = [k for k in truth if k in conf]
    wrong = {k for k in keys if truth[k]["wrong"]}
    out = {"rows": len(keys), "wrong": len(wrong), "strategies": {}}
    for name, sc in strategies(truth, conf).items():
        order = sorted(keys, key=sc.get)
        caught = {f"{p}%": round(len(wrong & set(order[:round(len(keys) * p / 100)])) / len(wrong), 4) for p in (1, 2, 5, 10)}
        top5 = set(order[:round(len(keys) * 0.05)])
        by_field = {f: round(sum(1 for k in wrong & top5 if f in truth[k]["wrong"]) /
                             max(1, sum(1 for k in wrong if f in truth[k]["wrong"])), 3)
                    for f in sorted({f for k in wrong for f in truth[k]["wrong"]})}
        out["strategies"][name] = {"auroc": auroc(sc, wrong) if name != "random" else 0.5, "caught": caught,
                                   "caught_at_5pct_by_field": by_field}
    sig = defaultdict(lambda: [0, 0])
    for k in keys:
        for s in conf[k]["signals"]:
            sig[s][0] += 1
            sig[s][1] += k in wrong
    out["signals"] = {s: {"flagged": n, "precision": round(w / n, 3), "recall": round(w / len(wrong), 3)}
                      for s, (n, w) in sorted(sig.items(), key=lambda x: -x[1][0])}
    jev_q = defaultdict(list)
    for k in keys:
        for q, p in conf[k]["jev"].items():
            jev_q[q].append((p, k in wrong))
    out["jev_questions"] = {q: auroc({str(i): p for i, (p, _) in enumerate(v)}, {str(i) for i, (_, w) in enumerate(v) if w})
                            for q, v in jev_q.items() if any(w for _, w in v)}
    (OUT / "app_calibration.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


# ----------------------------------------------------------------------------------------------------- oracle user
def person_id(eng: Engine, full: str) -> int | None:
    """The stored person the truth means: full name, else first name, else a known nickname."""
    names = [full, full.split()[0]] + SIM_PEOPLE.get(full, {}).get("alias", [])
    rows = eng.db.execute("SELECT id, name FROM people").fetchall()
    for n in names:
        hit = [r[0] for r in rows if r[1] and fold(r[1]) == fold(n)]
        if hit:
            return hit[0]
    return None


def oracle_fix(eng: Engine, e: dict, ts: str) -> int:
    """The user corrects every wrong field of one row. The answer is logged, and applied as a typed update."""
    cols = {c["name"] for c in eng.columns(e["table"])}
    values, ops, note = [], [], []
    for f in e["wrong"]:
        want = e["want"][f]
        col = next((c for c in FIELD_COLS.get(f, ()) if c in cols), None)
        if not col:
            continue
        if f == "with":
            full = want[0]
            pid = person_id(eng, full)
            if pid is None:
                ops.append({"op": "insert", "table": "people", "values": [{"column": "name", "value": full}], "ref": "p"})
                pid = "@p"
            values.append({"column": col, "value": pid})
            note.append(f"with {full}")
        else:
            values.append({"column": col, "value": want})
            note.append(f"{col} {want}")
    if not values:
        return 0
    log_id = eng.log(ts, f"[inbox] {e['table']} #{e['id']}: " + "; ".join(note))
    ops.append({"op": "update", "table": e["table"], "row_id": e["id"], "values": values})
    errors = eng.apply(ops, log_id, ts)
    return len(values) if not errors else 0


def truth_person(label: str) -> str:
    """Which simulated person a stored name means (by full name, first name or nickname), else the label itself."""
    for full, info in SIM_PEOPLE.items():
        if fold(label) in {fold(full), fold(full.split()[0])} | {fold(a) for a in info.get("alias", [])}:
            return full
    return label


def person_questions(eng: Engine, low: float = 0.5, high: float = 0.8) -> list[dict]:
    """Person pairs the dedupe pass would not merge on its own (0.5 <= p < 0.8): questions for the user."""
    rows = [r for r in eng.rows("people") if r.get("name")]
    import itertools
    pairs = list(itertools.combinations(rows, 2))
    ctx = {r["id"]: consolidate._sources(eng, "people", r["id"]) for r in rows}
    answers = consolidate.jev.ask_many([({"a": a, "b": b, "table": "people", "said_about_a": ctx[a["id"]],
                                          "said_about_b": ctx[b["id"]]}, consolidate.SAME) for a, b in pairs],
                                       workers=16, tag="sleep:same")
    out = []
    for (a, b), ans in zip(pairs, answers):
        p = ans["same"]["noul"]
        if low <= p < high:
            out.append({"kind": "pair", "key": f"people|{a['id']}|{b['id']}", "a": a["name"], "b": b["name"],
                        "a_id": a["id"], "b_id": b["id"], "p": round(p, 3), "context": (ctx[a["id"]] + ctx[b["id"]])[:3]})
    return sorted(out, key=lambda x: -x["p"])


def oracle_pair(eng: Engine, q: dict, ts: str) -> bool:
    same = truth_person(q["a"]) == truth_person(q["b"])
    eng.log(ts, f"[inbox] are {q['a']} and {q['b']} the same person? {'yes' if same else 'no'}")
    if not same:
        return False
    a = next((r for r in eng.rows("people") if r["id"] == q["a_id"]), None)
    b = next((r for r in eng.rows("people") if r["id"] == q["b_id"]), None)
    if not a or not b:
        return False
    keep, dup = (a, b) if len(str(a.get("name", ""))) >= len(str(b.get("name", ""))) else (b, a)   # the fuller name stays
    consolidate.merge(eng, "people", keep, dup, ts)
    return True


# --------------------------------------------------------------------------------------------------- app lookup
KIND_MEASURE = {"spend_kind": ("expenses", "sum", "amount"), "count_kind": ("expenses", "count", "__row__"),
                "avg_kind": ("expenses", "avg", "amount"), "count_merchant": ("expenses", "count", "__row__"),
                "spend_merchant": ("expenses", "sum", "amount"), "max_kind_city": ("expenses", "max", "amount"),
                "avg_sleep": ("body_measurements", "avg", "value"), "min_weight": ("body_measurements", "min", "value"),
                "count_runs": ("workouts", "count", "__row__"), "count_climbs": ("events", "count", "__row__")}
CONST_NEED = {"avg_sleep": ["sleep"], "min_weight": ["weight"], "count_runs": ["run"], "count_climbs": ["climbing"]}


def period_cells(period: str, now: datetime) -> tuple[str, list[str]] | None:
    if period.startswith("year:"):
        return "year", [period[5:]]
    if period.startswith("month:"):
        return "month", [period[6:]]
    return {"this_year": ("year", [str(now.year)]), "last_year": ("year", [str(now.year - 1)]),
            "last_month": ("month", [f"{now.year}-{now.month - 1:02d}"]), "this_month": ("month", [f"{now.year}-{now.month:02d}"]),
            "all": ("all", ["all"]),
            "since_2026-04": ("month", [f"2026-{m:02d}" for m in range(4, now.month + 1)])}.get(period)


def lookup(app: dict, q: dict, now: datetime) -> tuple[str | None, str]:
    """Read a question's answer off the app the way a person would, knowing what they asked (oracle navigation).
    Returns (answer text, how) or (None, why not)."""
    tpl, p = q["template"], q["params"]
    views, data = app["views"], app["data"]
    if tpl in KIND_MEASURE:
        table, op, col = KIND_MEASURE[tpl]
        need = [fold(x) for x in ([p["kind"]] if "kind" in p else []) + ([p["merchant"]] if "merchant" in p else [])
                + ([p["city"]] if "city" in p else [])] + CONST_NEED.get(tpl, [])
        grain_periods = period_cells(p.get("period", "all"), now)
        if not grain_periods:
            return None, "period the app doesn't show"
        grain, periods = grain_periods
        if len(periods) > 1 and op not in ("sum", "count"):
            return None, "period needs an additive measure"
        cands = []
        for v in views.values():
            if v["view"] != "tile" or (v["table"], v["op"], v["column"]) != (table, op, col):
                continue
            consts = {fold(x) for x in v["const"].values()}
            for key, cell in data[v["id"]]["cells"].items():
                if key[0] != grain or key[1] not in periods:
                    continue
                have = consts | {fold(x) for x in key[2]}
                if all(n in have for n in need):
                    cands.append((len(v["series"]) + len(v["const"]), v["id"], key, cell))
        if not cands:
            return None, "no tile"
        best = min(c[0] for c in cands)
        tile_id = min(c[1] for c in cands if c[0] == best)
        cells = [c for c in cands if c[1] == tile_id]
        series = {c[2][2] for c in cells}
        if len(series) > 1:   # a tile sliced finer than the question: add the slices up (sums and counts only)
            if op not in ("sum", "count"):
                return None, "tile sliced finer than the question"
        by_cur = defaultdict(float)
        extreme = None
        for _, _, key, cell in cells:
            if op in ("max", "min"):
                if extreme is None or (cell["v"] > extreme[1]["v"] if op == "max" else cell["v"] < extreme[1]["v"]):
                    extreme = (key, cell)
            elif op == "avg":
                by_cur[key[3]] = cell["v"]
            else:
                by_cur[key[3]] += cell["v"]
        if op in ("max", "min"):
            return f"{extreme[1]['v']} {extreme[0][3]}".strip(), tile_id
        if op == "count":
            return str(int(sum(by_cur.values()))), tile_id
        return " + ".join(f"{v:.2f} {c}".strip() for c, v in by_cur.items()), tile_id
    person = views.get("people", {}).get("people", {})
    if tpl in ("spend_kind_person", "count_kind_person", "last_kind_person"):
        full = FIRST_NAMES[p["first"]]
        name = next((n for n in person if fold(n) == fold(full)), None) or next(
            (n for n in person if fold(n) in {fold(p["first"])} | {fold(a) for a in SIM_PEOPLE.get(full, {}).get("alias", [])}), None)
        if not name:
            return None, "no person card"
        cell = next((c for k, c in person[name].items() if k.split("|")[0] == "expenses" and fold(k.split("|")[2]) == fold(p["kind"])), None)
        if not cell:
            return None, "no kind on the person card"
        if tpl == "count_kind_person":
            return str(cell["n"]), "people"
        if tpl == "last_kind_person":
            return cell["last"], "people"
        return " + ".join(f"{v:.2f} {c}" for c, v in cell["total"].items()), "people"
    if tpl == "spend_kind_city":
        for v in views.values():
            if v["view"] == "cross":
                for key, tot in v["totals"].items():
                    if {fold(x) for x in key.split("|")} == {fold(p["kind"]), fold(p["city"])}:
                        return " + ".join(f"{a:.2f} {c}" for c, a in tot.items()), v["id"]
        return None, "no cross-tab"
    if tpl == "top_merchant":
        year = period_cells(p["period"], now)
        for v in views.values():
            if v["view"] == "top":
                for key, top in v["top"].items():
                    k, val, per = key.split("|")
                    if fold(val) == fold(p["kind"]) and year and per == year[1][0] and top:
                        return top[0][0], v["id"]
        return None, "no breakdown"
    return None, "unknown shape"


def app_views(db_path: Path, plans: list, user: str, now: datetime) -> dict:
    """The app's views and numbers without the layout call (for measuring)."""
    reader = DetReader(str(db_path), user, now)
    tiles = appgen.tiles_from_plans(reader, plans)
    views = {v["id"]: v for v in tiles}
    data = {v["id"]: appgen.compute_tile(reader, v) for v in tiles}
    views["people"] = appgen.people_view(reader)
    for v in appgen.breakdown_views(reader):
        views[v["id"]] = v
    return {"views": views, "data": data, "reader": reader}


def app_score(app: dict, questions: list[dict], now: datetime) -> dict:
    rows = []
    for q in questions:
        ans, how = lookup(app, q, now)
        rows.append({"id": q["id"], "group": q["group"], "template": q["template"], "question": q["question"],
                     "gold": q["answer"], "app": ans, "how": how, "score": W.grade(q, ans) if ans else 0.0})
    summary = {"all": round(sum(r["score"] for r in rows) / len(rows), 4)}
    for g in ("same", "reworded", "novel"):
        rs = [r for r in rows if r["group"] == g]
        summary[g] = round(sum(r["score"] for r in rs) / len(rs), 4)
    logged = [r for r in rows if r["group"] in ("same", "reworded")]
    summary["logged_shapes"] = round(sum(r["score"] for r in logged) / len(logged), 4)
    summary["no_view"] = sum(1 for r in rows if r["app"] is None)
    summary["view_but_wrong"] = sum(1 for r in rows if r["app"] is not None and r["score"] < 1)
    return {"summary": summary, "rows": rows}


# ------------------------------------------------------------------------------------------------------ inbox
def inbox_step(budget: float):
    wl, now = workload()
    truth, conf, plans = load("truth.json"), load("confidence.json"), [tuple(x) for x in load("plans.json")]
    eng = Engine(str(RUN / "db.sqlite"), strict=True, normalize=True)
    pairs = person_questions(eng)
    guard(budget)
    before = app_score(app_views(RUN / "db.sqlite", plans, wl["user"], now), wl["future"], now)["summary"]
    base_wrong = sum(1 for e in truth.values() if e["wrong"])
    results = {"pairs": pairs, "before": {"wrong_rows": base_wrong, "app": before}, "runs": []}
    for name, sc in strategies(truth, conf).items():
        if name == "planner_first":
            continue
        order = sorted([k for k in truth if k in conf], key=sc.get)
        for n in (25, 50, 100, 200):
            path = RUN / f"inbox_{name}_{n}.sqlite"
            shutil.copy(RUN / "db.sqlite", path)
            e2 = Engine(str(path), strict=True, normalize=True)
            ts = wl["now"]
            fixed_rows = sum(1 for k in order[:n] if truth[k]["wrong"] and oracle_fix(e2, truth[k], ts))
            merged = sum(oracle_pair(e2, q, ts) for q in pairs) if name == "combined" else 0
            e2.db.commit()
            after = row_truth(path)
            wrong_after = sum(1 for e in after.values() if e["wrong"])
            app = app_score(app_views(path, plans, wl["user"], now), wl["future"], now)["summary"]
            results["runs"].append({"strategy": name, "n": n, "shown_wrong": sum(1 for k in order[:n] if truth[k]["wrong"]),
                                    "fixed_rows": fixed_rows, "merged_people": merged, "wrong_rows_after": wrong_after,
                                    "wrong_rate_after": round(wrong_after / len(after), 4), "app_after": app})
            print(name, n, results["runs"][-1]["shown_wrong"], "->", wrong_after, app)
            if not (name == "combined" and n == 100):
                path.unlink()
    (OUT / "app_inbox.json").write_text(json.dumps(results, indent=1, ensure_ascii=False))


# ------------------------------------------------------------------------------------------------------ app
def screenshot(html_path: Path, png: Path, width: int = 1280, height: int = 2600):
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--window-size={width},{height}",
                    f"--screenshot={png}", f"file://{html_path.resolve()}"], timeout=120, capture_output=True)


def inbox_items(eng: Engine, conf: dict, user: str, n: int = 30, pairs: list | None = None) -> dict:
    ctx = C.Context(eng.db, user)
    items = []
    for key in sorted(conf, key=lambda k: conf[k]["score"])[:n]:
        t, rid = key.split("|")
        row = eng.db.execute(f"SELECT * FROM '{t}' WHERE id = ?", (int(rid),)).fetchone()
        if row is None:
            continue
        row = dict(zip([r[1] for r in eng.db.execute(f"PRAGMA table_info('{t}')")], row))
        sent, msg = ctx.source(row)
        fix = C.suggest(ctx, t, row, conf[key]["signals"])
        items.append({"kind": "row", "key": key, "table": t, "row": ctx.readable(t, row), "message": msg, "sent": sent,
                      "signals": conf[key]["signals"], "score": conf[key]["score"], "suggest": fix,
                      "suggest_text": {k: (ctx.people.get(v, v) if k.endswith("_id") else v) for k, v in fix.items()}})
    return {"items": list(pairs or []) + items, "n": len(items) + len(pairs or [])}


def app_step(budget: float):
    wl, now = workload()
    plans, conf = [tuple(x) for x in load("plans.json")], load("confidence.json")
    eng = Engine(str(RUN / "db.sqlite"), strict=True, normalize=True)
    pairs = person_questions(eng)
    n0 = len(LEDGER.calls)
    t0 = time.time()
    app = appgen.build(str(RUN / "db.sqlite"), plans, wl["user"], now, inbox=inbox_items(eng, conf, wl["user"], 30, pairs))
    build_s = time.time() - t0
    guard(budget)
    APP.mkdir(parents=True, exist_ok=True)
    for name, page in app["pages"].items():
        (APP / name).write_text(page)
        screenshot(APP / name, APP / (name.replace(".html", ".png")))
    (RUN / "app_spec.json").write_text(json.dumps(app["spec"], indent=1, ensure_ascii=False))
    out = {"views": Counter(v["view"] for v in app["views"].values()), "tiles": [
        {"id": v["id"], "table": v["table"], "op": v["op"], "column": v["column"], "series": v["series"], "const": v["const"],
         "questions": len(v["questions"]), "sql": app["sql_checks"][v["id"]]}
        for v in app["views"].values() if v["view"] == "tile"],
        "sql_ok": all(c["ok"] for c in app["sql_checks"].values()), "pages": [p["title"] for p in app["spec"]["pages"]],
        "layout_cost": round(sum(c.cost for c in LEDGER.calls[n0:] if c.tag == "appgen:layout"), 5),
        "build_seconds": round(build_s, 2), "timings": app["seconds"],
        "html_kb": {n: round(len(h) / 1024) for n, h in app["pages"].items()}}
    (OUT / "app_build.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(json.dumps(out, indent=1, ensure_ascii=False))


def evaluate_step(budget: float):
    wl, now = workload()
    plans = [tuple(x) for x in load("plans.json")]
    out = {}
    for name, path in (("before", RUN / "db.sqlite"), ("after_inbox_100", RUN / "inbox_combined_100.sqlite")):
        out[name] = app_score(app_views(path, plans, wl["user"], now), wl["future"], now)
        by = defaultdict(list)
        for r in out[name]["rows"]:
            by[r["template"]].append(r)
        out[name]["by_template"] = {t: {"score": round(sum(r["score"] for r in rs) / len(rs), 3),
                                        "no_view": sum(r["app"] is None for r in rs)} for t, rs in sorted(by.items())}
    # the deterministic reader on the corrected database (the database got better, not just the app)
    reader = DetReader(str(RUN / "inbox_combined_100.sqlite"), wl["user"], now)
    det = W.det_batch(reader, wl["future"])
    guard(budget)
    score = [W.grade(q, det["by_q"][q["id"]]["out"]["answer"]) for q in wl["future"]]
    out["det_reader_after_inbox_100"] = {"accuracy": round(sum(score) / len(score), 4), "cost_per_q": round(det["cost_per_q"], 6)}
    (OUT / "app_eval.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(json.dumps({k: (v["summary"] if "summary" in v else v) for k, v in out.items()}, indent=1))
    print(json.dumps(out["before"]["by_template"], indent=0))


def year_step(budget: float):
    """Generality: the same generator on the year database, from that database's own trusted plans."""
    ds = json.loads(YEAR.read_text())
    now = datetime.fromisoformat(ds["checkpoints"]["m12"])
    qa = [{"id": f"y{i:02d}", **q} for i, q in enumerate(ds["qa"])]
    reader = DetReader(str(YEAR_DB), ds["user"], now)
    det = W.det_batch(reader, qa)
    guard(budget)
    plans = [(q["question"], query_log.plan_of(det["by_q"][q["id"]]["out"])) for q in qa if W.trusted(det["by_q"][q["id"]]["out"])]
    tiles = appgen.tiles_from_plans(reader, plans)
    data = {t["id"]: appgen.compute_tile(reader, t) for t in tiles}
    checks = {t["id"]: appgen.check_sql(reader, t, data[t["id"]]) for t in tiles}
    agg = [q for q in qa if q["category"] == "aggregation"]
    rows = []
    for q in agg:
        out = det["by_q"][q["id"]]["out"]
        plan = query_log.plan_of(out)
        ans = plan_lookup(reader, tiles, data, plan, now) if W.trusted(out) else None
        g = jev_grade(q["question"], q["answer"], ans)["score"] if ans else 0.0
        rows.append({"question": q["question"], "gold": q["answer"], "app": ans, "score": g,
                     "det": out["answer"][:120], "trusted": W.trusted(out)})
    guard(budget)
    res = {"trusted_plans": len(plans), "tiles": len(tiles), "sql_ok": all(c["ok"] for c in checks.values()),
           "tile_list": [{"table": t["table"], "op": t["op"], "column": t["column"], "series": t["series"], "const": t["const"],
                          "questions": len(t["questions"])} for t in tiles],
           "aggregation_questions": len(agg), "answered_from_tiles": sum(r["app"] is not None for r in rows),
           "accuracy": round(sum(r["score"] for r in rows) / max(1, len(rows)), 4), "rows": rows, "spent": round(spent(), 4)}
    (OUT / "app_year.json").write_text(json.dumps(res, indent=1, ensure_ascii=False))
    print(json.dumps({k: v for k, v in res.items() if k != "rows"}, indent=1, ensure_ascii=False))


def plan_lookup(reader: DetReader, tiles: list, data: dict, plan: dict, now: datetime) -> str | None:
    """A question's own plan -> the tile cell that holds its answer (what a search box in the app would do)."""
    op = plan.get("op")
    if op not in appgen.AGG:
        return None
    col = "__row__" if op == "count" else plan.get("column")
    f = {d: str(v) for d, v in appgen.concepts(plan.get("filters") or {}).items() if v != reader.user}
    per = plan.get("period") or "all"
    if per.startswith("in:"):
        grain, periods = "month", [per[3:]]
    elif per.startswith("year:"):
        grain, periods = "year", [per[5:]]
    elif per == "all":
        grain, periods = "all", ["all"]
    elif per.startswith("since:") and op in ("sum", "count"):
        grain = "month"
        periods = sorted({k[1] for t in tiles for k in data[t["id"]]["cells"] if k[0] == "month" and k[1] >= per[6:]})
    else:
        return None
    for t in tiles:
        if (t["table"], t["op"], t["column"]) != (plan.get("table"), op, col):
            continue
        if any(f.get(d) != v for d, v in t["const"].items()) or set(f) - set(t["const"]) - set(t["series"]):
            continue
        s = tuple(f.get(d) for d in t["series"])
        cells = [(k, c) for k, c in data[t["id"]]["cells"].items() if k[0] == grain and k[1] in periods and k[2] == s]
        if not cells:
            continue
        if op in ("max", "min"):
            k, c = (max if op == "max" else min)(cells, key=lambda kc: kc[1]["v"])
            return f"{c['v']} {k[3]} {c.get('what', '')} {c.get('at', '')}".strip()
        by = defaultdict(float)
        for k, c in cells:
            by[k[3]] = c["v"] if op == "avg" else by[k[3]] + c["v"]
        return " + ".join(f"{v:.2f} {cur}".strip() for cur, v in by.items())
    return None


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["setup", "truth", "confidence", "calibrate", "inbox", "app", "evaluate", "year"])
    ap.add_argument("--budget", type=float, default=1.5)
    args = ap.parse_args()
    W.BUDGET = args.budget
    {"setup": setup_step, "truth": truth_step, "confidence": lambda: confidence_step(args.budget), "calibrate": calibrate_step,
     "inbox": lambda: inbox_step(args.budget), "app": lambda: app_step(args.budget),
     "evaluate": lambda: evaluate_step(args.budget), "year": lambda: year_step(args.budget)}[args.cmd]()
