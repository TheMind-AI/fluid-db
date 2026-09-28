"""A question workload over the simulated 2.5 years (life_scale.json), with gold answers computed from the truth that
every recurring message carries. No model is involved.

past     questions already asked: the query log that the schema advisor and the question compiler learn from (they
         never see its gold answers)
future   questions to evaluate on, in three groups:
           same      the log's question shapes and phrasings, with parameters the log never used
           reworded  the log's shapes in phrasings it never used
           novel     shapes the log never asked (they check that optimizing for the log doesn't hurt the rest)

    .venv/bin/python -m lab.datasets.workload      -> lab/datasets/workload.json
"""
# @ref LLP 0009#simulators — logged and future questions with gold from the truth (round 5)
from __future__ import annotations

import ast
import json
import random
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

HERE = Path(__file__).parent
rng = random.Random(505)

KINDS = {  # truth category -> how people say it
    "coffee": {"spend": ["coffee"], "verb": ["get coffee", "buy a coffee", "grab a coffee", "have a coffee out"],
               "one": "coffee"},
    "lunch": {"spend": ["lunch", "lunches out"], "verb": ["eat lunch out", "have lunch out", "buy lunch", "go out for lunch"],
              "one": "lunch"},
    "dinner": {"spend": ["dinners out", "dinner"], "verb": ["go out for dinner", "have dinner out", "eat out for dinner",
                                                            "go to a restaurant for dinner"], "one": "dinner"},
    "drinks": {"spend": ["drinks", "drinks at bars"], "verb": ["go out for drinks", "go for drinks", "have drinks at a bar",
                                                               "go to a bar"], "one": "night of drinks"},
    "groceries": {"spend": ["groceries", "grocery shopping"], "verb": ["buy groceries", "do a grocery run",
                                                                        "shop for groceries", "go grocery shopping"],
                  "one": "grocery run"},
    "transport": {"spend": ["rides", "Uber and Bolt rides"], "verb": ["take a ride", "take an Uber or a Bolt",
                                                                      "use Uber or Bolt", "take a taxi ride"], "one": "ride"},
    "shopping": {"spend": ["shopping", "things I ordered or bought in shops"], "verb": ["buy something in a shop or online",
                                                                                         "go shopping", "order something",
                                                                                         "make a shopping purchase"],
                 "one": "shopping purchase"},
}
PEOPLE = {"Tom": "Tom Novak", "Kuba": "Jakub Horák", "Eva": "Eva Nováková", "Ondra": "Ondřej Král", "Lucie": "Lucie Malá",
          "David": "David Mokos", "Nina": "Nina Rossi", "Priya": "Priya Patel"}
CITY = {"CZK": "Prague", "USD": "San Francisco"}
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November",
          "December"]

# Each template: 4 phrasings. The log uses phrasings 0-1; "reworded" uses 2-3.
TEMPLATES = {
    "spend_kind": ["How much did I spend on {spend} {period}?", "What was my total {spend} spend {period}?",
                   "{Period}, how much money went on {spend}?", "Total I paid for {spend} {period}?"],
    "count_kind": ["How many times did I {verb} {period}?", "How often did I {verb} {period}?",
                   "{Period}, how many times did I {verb}?", "Number of times I did {verb} {period}?"],
    "count_merchant": ["How many times did I go to {merchant} {period}?", "How many visits to {merchant} {period}?",
                       "How often was I at {merchant} {period}?", "{Period}, how many times was I at {merchant}?"],
    "spend_merchant": ["How much did I spend at {merchant} {period}?", "What did I spend in total at {merchant} {period}?",
                       "{Period}, how much did I pay at {merchant}?", "How much money did I leave at {merchant} {period}?"],
    "max_kind_city": ["What's the most I've spent on a single {one} in {city}?", "What was my most expensive {one} in {city}?",
                      "Priciest {one} I had in {city}?", "Biggest bill for a {one} in {city}?"],
    "avg_kind": ["What's my average {one} bill {period}?", "How much did a {one} cost me on average {period}?",
                 "{Period}, what did I typically pay for a {one}?", "Average price of a {one} {period}?"],
    "avg_sleep": ["What was my average sleep in {month}?", "How many hours did I sleep on average in {month}?",
                  "In {month}, how long did I sleep per night?", "Average hours of sleep in {month}?"],
    "min_weight": ["What was my lowest weight {period}?", "What's the least I weighed {period}?",
                   "{Period}, what was my minimum weight?", "Lightest I've been {period}?"],
    "count_runs": ["How many runs did I do {period}?", "How many times did I go running {period}?",
                   "{Period}, how many runs did I log?", "Number of runs {period}?"],
    "count_climbs": ["How many times did I go climbing {period}?", "How many climbing sessions did I have {period}?",
                     "{Period}, how often did I climb?", "Climbing sessions {period}?"],
}
NOVEL = {
    "spend_kind_person": ["How much have I spent on {spend} with {first}?"],
    "count_kind_person": ["How many times did I {verb} with {first}?"],
    "last_kind_person": ["When did I last {verb} with {first}?"],
    "spend_kind_city": ["How much have I spent on {spend} in {city} in total?"],
    "top_merchant": ["Where did I {verb} most often {period}?"],
}


def load(path: Path = HERE / "life_scale.json"):
    ds = json.loads(path.read_text())
    truth = [ast.literal_eval(m["truth"]) if isinstance(m["truth"], str) else m["truth"]
             for m in ds["messages"] if "truth" in m]
    return ds, truth


def periods(now: date) -> dict[str, tuple[str, callable]]:
    """id -> (phrase, date predicate over ISO strings)."""
    out = {"year:2024": ("in 2024", lambda d: d[:4] == "2024"), "year:2025": ("in 2025", lambda d: d[:4] == "2025"),
           "this_year": ("this year", lambda d: d[:4] == str(now.year)),
           "last_year": ("last year", lambda d: d[:4] == str(now.year - 1)),
           "last_month": ("last month", lambda d: d[:7] == f"{now.year}-{now.month - 1:02d}"),
           "this_month": ("this month", lambda d: d[:7] == f"{now.year}-{now.month:02d}"),
           "since_2026-04": ("since April 2026", lambda d: d >= "2026-04-01"),
           "all": ("ever", lambda d: True)}
    y, m = 2024, 4
    while (y, m) <= (now.year, now.month):
        ym = f"{y}-{m:02d}"
        out[f"month:{ym}"] = (f"in {MONTHS[m - 1]} {y}", lambda d, ym=ym: d[:7] == ym)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def money(totals: dict[str, float]) -> str:
    return " + ".join(f"{v:,.2f} {c}".replace(".00 ", " ") for c, v in sorted(totals.items()))


class Gold:
    def __init__(self, truth: list[dict], now: date):
        self.ex = [t for t in truth if t["kind"] == "expense"]
        self.truth, self.now, self.per = truth, now, periods(now)

    def _rows(self, kind=None, period="all", merchant=None, city=None, person=None):
        keep = self.per[period][1]
        return [e for e in self.ex if keep(e["date"]) and (kind is None or e["category"] == kind)
                and (merchant is None or e["merchant"] == merchant) and (city is None or CITY[e["currency"]] == city)
                and (person is None or e.get("with") == person)]

    def spend(self, **kw):
        rows = self._rows(**kw)
        tot: dict = defaultdict(float)
        for e in rows:
            tot[e["currency"]] += e["amount"]
        return rows, {"type": "amounts", "values": [[round(v, 2), c] for c, v in sorted(tot.items())]}, \
            f"{money(tot)} ({len(rows)} purchases)"

    def count(self, **kw):
        rows = self._rows(**kw)
        return rows, {"type": "count", "value": len(rows)}, str(len(rows))

    def extreme(self, fn, **kw):
        rows = self._rows(**kw)
        if not rows:
            return rows, None, None
        e = fn(rows, key=lambda r: r["amount"])
        return rows, {"type": "amounts", "values": [[e["amount"], e["currency"]]]}, \
            f"{e['amount']:,.2f} {e['currency']} at {e['merchant']} on {e['date']}"

    def average(self, **kw):
        rows = self._rows(**kw)
        cur = {e["currency"] for e in rows}
        if not rows or len(cur) != 1:
            return rows, None, None
        avg = sum(e["amount"] for e in rows) / len(rows)
        return rows, {"type": "number", "value": round(avg, 2), "tol": 0.01}, f"{avg:,.2f} {cur.pop()} (average of {len(rows)})"

    def last(self, **kw):
        rows = sorted(self._rows(**kw), key=lambda r: r["date"])
        if not rows:
            return rows, None, None
        return rows, {"type": "date", "value": rows[-1]["date"]}, f"{rows[-1]['date']} at {rows[-1]['merchant']}"

    def top_merchant(self, **kw):
        rows = self._rows(**kw)
        c = Counter(e["merchant"] for e in rows).most_common(2)
        if not c or (len(c) > 1 and c[0][1] == c[1][1]):
            return rows, None, None
        return rows, {"type": "name", "value": c[0][0]}, f"{c[0][0]} ({c[0][1]} times)"

    def health(self, kind, period, stat):
        keep = self.per[period][1]
        rows = [t for t in self.truth if t["kind"] == kind and keep(t["date"])]
        if kind == "climb":
            rows = [t for t in rows if t.get("gym")]
        if not rows:
            return rows, None, None
        if stat == "count":
            return rows, {"type": "count", "value": len(rows)}, str(len(rows))
        if stat == "avg_sleep":
            avg = sum(t["hours"] for t in rows) / len(rows)
            return rows, {"type": "number", "value": round(avg, 2), "tol": 0.01}, f"{avg:.2f} hours ({len(rows)} nights)"
        t = min(rows, key=lambda r: r["kg"])
        return rows, {"type": "number", "value": t["kg"], "tol": 0.001}, f"{t['kg']} kg ({t['date']})"


def build(now: date | None = None) -> dict:
    ds, truth = load()
    now = now or date.fromisoformat(ds["now"][:10])
    g = Gold(truth, now)
    per = g.per
    month_ids = [p for p in per if p.startswith("month:")]
    common_merchants = [m for m, n in Counter(e["merchant"] for e in g.ex).most_common() if n >= 25]

    def params(tid):
        """One random parameter set for a template, and its gold (None when the combination has no answer)."""
        if tid in ("spend_kind", "count_kind", "avg_kind"):
            kind = rng.choice(list(KINDS))
            period = rng.choice(["year:2024", "year:2025", "this_year", "last_month"] + rng.sample(month_ids, 3))
            if tid == "avg_kind" and kind == "shopping":
                return None
            rows, gold, text = {"spend_kind": g.spend, "count_kind": g.count, "avg_kind": g.average}[tid](kind=kind, period=period)
            return {"kind": kind, "period": period}, rows, gold, text
        if tid in ("count_merchant", "spend_merchant"):
            merchant = rng.choice(common_merchants)
            period = rng.choice(["year:2024", "year:2025", "this_year", "all"] + rng.sample(month_ids, 2))
            fn = g.count if tid == "count_merchant" else g.spend
            rows, gold, text = fn(merchant=merchant, period=period)
            return {"merchant": merchant, "period": period}, rows, gold, text
        if tid == "max_kind_city":
            kind, city = rng.choice(["coffee", "lunch", "dinner", "drinks", "groceries", "transport"]), rng.choice(list(CITY.values()))
            rows, gold, text = g.extreme(max, kind=kind, city=city)
            return {"kind": kind, "city": city}, rows, gold, text
        if tid == "avg_sleep":
            period = rng.choice(month_ids)
            return ({"period": period},) + g.health("sleep", period, "avg_sleep")
        if tid == "min_weight":
            period = rng.choice(["year:2024", "year:2025", "this_year", "last_month"] + rng.sample(month_ids, 2))
            return ({"period": period},) + g.health("weight", period, "min")
        if tid in ("count_runs", "count_climbs"):
            period = rng.choice(["year:2024", "year:2025", "this_year", "all"] + rng.sample(month_ids, 3))
            return ({"period": period},) + g.health("run" if tid == "count_runs" else "climb", period, "count")
        if tid in ("spend_kind_person", "count_kind_person", "last_kind_person"):
            kind, first = rng.choice(["dinner", "drinks"]), rng.choice(list(PEOPLE))
            fn = {"spend_kind_person": g.spend, "count_kind_person": g.count, "last_kind_person": g.last}[tid]
            rows, gold, text = fn(kind=kind, person=PEOPLE[first])
            return {"kind": kind, "first": first}, rows, gold, text
        if tid == "spend_kind_city":
            kind, city = rng.choice(["coffee", "lunch", "dinner", "drinks", "groceries", "transport"]), rng.choice(list(CITY.values()))
            rows, gold, text = g.spend(kind=kind, city=city)
            return {"kind": kind, "city": city}, rows, gold, text
        if tid == "top_merchant":
            kind, period = rng.choice(["coffee", "lunch", "dinner", "drinks", "groceries"]), rng.choice(["year:2024", "year:2025", "this_year"])
            rows, gold, text = g.top_merchant(kind=kind, period=period)
            return {"kind": kind, "period": period}, rows, gold, text
        raise KeyError(tid)

    def phrase(tid, phrasing, p):
        k = KINDS.get(p.get("kind"), {})
        per_text = per[p["period"]][0] if "period" in p else ""
        fmt = (TEMPLATES.get(tid) or NOVEL[tid])[phrasing]
        text = fmt.format(spend=rng.choice(k.get("spend", [""])), verb=k.get("verb", [""])[min(phrasing, 3)] if k else "",
                          one=k.get("one", ""), merchant=p.get("merchant", ""), city=p.get("city", ""), first=p.get("first", ""),
                          period=per_text, Period=per_text[:1].upper() + per_text[1:],
                          month=per_text.replace("in ", "", 1))
        return text.replace(" ever?", "?").replace("Ever, ", "")

    seen, out = set(), {"past": [], "future": []}

    def add(split, group, tid, phrasing, tries=60):
        for _ in range(tries):
            got = params(tid)
            if not got:
                continue
            p, rows, gold, text = got
            key = (tid, json.dumps(p, sort_keys=True))
            if gold is None or len(rows) < 2 or (group == "same" and key in seen) or (split == "past" and key in seen):
                continue
            q = phrase(tid, phrasing, p)
            if any(x["question"] == q for s in out.values() for x in s):
                continue
            seen.add(key)
            out[split].append({"id": f"{split[0]}{len(out[split]) + 1:03d}", "group": group, "template": tid,
                               "phrasing": phrasing, "params": p, "question": q, "answer": text, "grade": gold,
                               "n_rows": len(rows)})
            return
        raise RuntimeError(f"no parameters for {tid}")

    for tid in TEMPLATES:
        for i in range(8):
            add("past", "log", tid, i % 2)
    for tid in TEMPLATES:
        for i in range(4):
            add("future", "same", tid, i % 2)
        for i in range(4):
            add("future", "reworded", tid, 2 + i % 2)
    for tid in NOVEL:
        for _ in range(5):
            add("future", "novel", tid, 0)
    return {"user": ds["user"], "now": ds["now"], **out}


def main():
    wl = build()
    (HERE / "workload.json").write_text(json.dumps(wl, indent=1, ensure_ascii=False))
    print({k: len(v) for k, v in wl.items() if isinstance(v, list)},
          Counter(q["group"] for q in wl["future"]))
    for q in wl["past"][:6] + wl["future"][40:44] + wl["future"][-5:]:
        print(f"  [{q['group']}/{q['template']}] {q['question']}  ->  {q['answer']}")


if __name__ == "__main__":
    main()
