"""Scale the simulated life up to ~2.5 years and 5,000+ messages, with the exact truth of every recurring message.

Reuses the storyline of simulate_year.py (people, the move to San Francisco, phone changes, forgets, calendar,
books, tasks) and its year-end questions, recomputed from the new ground truth. The daily life is denser and wider:
coffee most days at a dozen cafés, weekday lunches, rides, groceries twice a week, dinners and drinks with friends,
online orders, sleep and weight logs, movies, plus climbing and runs. Every recurring message carries `truth`, the
facts it states (merchant, amount, currency, date, ...), so write accuracy is scored exactly, without a judge.

  .venv/bin/python -m lab.datasets.simulate_scale      -> lab/datasets/life_scale.json
"""
# @ref LLP 0009#simulators — 5,050 messages with per-message truth (round 4)
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import lab.datasets.simulate_year as sy

START = date(2024, 4, 1)
sy.START = START          # the storyline's "known since" dates and chit-chat start here too
sy.rng.seed(2027)
rng = sy.rng

CAFES = {"prague": ["Café Savoy", "EMA Espresso Bar", "Kavárna Místo", "Onesip Coffee", "Doubleshot", "Kafe Francin",
                    "Café Lounge", "Můj šálek kávy"],
         "sf": ["Blue Bottle", "Philz Coffee", "Sightglass", "Ritual Coffee", "Four Barrel", "Réveille"]}
LUNCH = {"prague": ["Lokál", "Bistro Monk", "Pho Vietnam", "Kantýna", "Mr. Bao", "Bageterie Boulevard"],
         "sf": ["Tartine Manufactory", "Souvla", "Nopalito", "Deli Board", "Mixt", "The Grove"]}
DINNER = {"prague": ["Sasazu", "Eska", "La Degustation", "Field", "Maso a Kobliha", "Cafe Imperial"],
          "sf": ["Nopa", "Zuni Café", "State Bird Provisions", "Flour + Water", "Foreign Cinema"]}
BARS = {"prague": ["U Hrocha", "Vinograf", "Hemingway Bar", "Anonymous Bar"], "sf": ["Zeitgeist", "Trick Dog", "Bar Agricole"]}
SHOPS = {"prague": [("Alza", ["headphones", "a USB-C charger", "a keyboard"]), ("Rohlik.cz", ["groceries delivery"]),
                    ("Decathlon", ["running socks", "a chalk bag", "climbing shoes"]), ("IKEA", ["a desk lamp", "shelves"])],
         "sf": [("Amazon", ["a backpack", "running shoes", "a phone case", "coffee beans"]), ("REI", ["a chalk bag", "a rain jacket"]),
                ("Target", ["kitchen stuff", "towels"]), ("IKEA", ["a desk lamp", "a bookshelf"])]}
MOVIES = [(f, r) for f, r in [
    ("Dune: Part Two", 5), ("Oppenheimer", 4), ("Past Lives", 5), ("The Holdovers", 4), ("Poor Things", 3), ("Anatomy of a Fall", 4),
    ("Perfect Days", 5), ("The Zone of Interest", 3), ("Challengers", 4), ("Inside Out 2", 4), ("Furiosa", 3), ("Civil War", 3),
    ("The Substance", 2), ("Conclave", 4), ("Anora", 4), ("The Brutalist", 3), ("Wicked", 3), ("Flow", 5), ("A Real Pain", 4),
    ("Nosferatu", 3), ("Sinners", 4), ("Mickey 17", 2), ("The Wild Robot", 5), ("Heretic", 3), ("Emilia Pérez", 2),
    ("One Battle After Another", 5), ("Weapons", 4), ("F1", 3), ("Superman", 3), ("The Phoenician Scheme", 3),
    ("Materialists", 2), ("Bugonia", 4), ("Hamnet", 5), ("Marty Supreme", 4), ("Sentimental Value", 5), ("Frankenstein", 3)]]
FRIENDS_PRAGUE = ["Ondřej Král", "Jakub Horák", "Lucie Malá", "Tom Novak", "Eva Nováková", "David Mokos"]
FRIENDS_SF = ["Nina Rossi", "David Mokos", "Priya Patel", "Eva Nováková"]
T = sy.T


def say_t(ts, text, truth):
    sy.say(ts, text)
    sy.MSGS[-1].truth = truth


def alias(who: str) -> str:
    return rng.choice(sy.PEOPLE[who]["alias"]) if who in sy.PEOPLE else who.split()[0]


def friends(d: date) -> list[str]:
    if not sy.in_sf(d):
        return FRIENDS_PRAGUE
    return [f for f in FRIENDS_SF if (f != "Priya Patel" or d >= date(2026, 4, 22)) and (f != "Eva Nováková" or d >= date(2026, 5, 20))]


def expense_msg(d, cat, merchant, amt, text_options, h, who=None, receipt_item=None):
    sy.expense(d, cat, merchant, amt, sy.cur(d), who)
    ts, when = sy.logged(d, h, rng.randint(0, 50))
    truth = {"kind": "expense", "date": d.isoformat(), "category": cat, "merchant": merchant, "amount": amt,
             "currency": sy.cur(d), "with": who}
    if receipt_item and rng.random() < 0.15:
        say_t(ts, sy.receipt(d, merchant, [(receipt_item, amt)], sy.cur(d)), truth)
    else:
        say_t(ts, sy.casual(sy.with_when(rng.choice(text_options), when)), truth)


def dense_daily_life():
    d = START
    run_best = {5: 28 * 60 + 30, 10: 57 * 60 + 40}
    grade_ladder = ["6a", "6a+", "6b", "6b+", "6c", "6c+", "7a"]
    grade_i, weight = 0, 77.4
    while d <= sy.END:
        ck, wd, cur = sy.city_key(d), d.weekday(), sy.cur(d)
        m = lambda a: sy.money(a, cur)
        travel = date(2024, 8, 3) <= d <= date(2024, 8, 17) or date(2025, 7, 12) <= d <= date(2025, 7, 20) or \
            date(2025, 12, 20) <= d <= date(2026, 1, 2) or date(2026, 5, 22) <= d <= date(2026, 5, 26) or d == date(2026, 3, 31)
        if not travel:
            for _ in range(1 + (rng.random() < 0.22)):
                if rng.random() < 0.9:
                    cafe = rng.choice(CAFES[ck])
                    amt = rng.choice([4.5, 5.25, 5.5, 6.0, 6.75]) if ck == "sf" else float(rng.choice([65, 75, 85, 95, 110, 125]))
                    expense_msg(d, "coffee", cafe, amt, [f"coffee at {cafe} {m(amt)}", f"☕ {cafe}, {m(amt)}",
                                                         f"{cafe} coffee {m(amt)}", f"flat white at {cafe}, {m(amt)}"],
                                8, receipt_item="Flat white" if ck == "prague" else "Oat latte")
            if wd < 5 and rng.random() < 0.8 or wd >= 5 and rng.random() < 0.3:
                place = rng.choice(LUNCH[ck])
                amt = rng.choice([14.5, 16.0, 18.75, 22.0, 26.5]) if ck == "sf" else float(rng.choice([189, 229, 265, 310, 385]))
                expense_msg(d, "lunch", place, amt, [f"lunch at {place}, {m(amt)}", f"{place} lunch {m(amt)}"], 12)
            if wd in (2, 5):
                store = rng.choice(sy.GROCERY[ck])
                amt = round(rng.uniform(25, 140), 2) if ck == "sf" else float(rng.randint(4, 18) * 100 + rng.choice([0, 40, 90]))
                expense_msg(d, "groceries", store, amt, [f"groceries at {store} {m(amt)}", f"{store} shopping, {m(amt)}"], 11,
                            receipt_item="Groceries")
            if rng.random() < 0.55:
                amt = round(rng.uniform(9, 35), 2) if ck == "sf" else float(rng.randint(9, 38) * 10)
                ride = sy.RIDE[ck]
                expense_msg(d, "transport", ride, amt, [f"{ride} home {m(amt)}", f"took a {ride}, {m(amt)}", f"{ride} to work {m(amt)}"], 21)
            if rng.random() < 0.28:
                who, place = rng.choice(friends(d)), rng.choice(DINNER[ck])
                amt = rng.choice([68.0, 84.5, 96.8, 112.0, 135.4]) if ck == "sf" else float(rng.choice([890, 1240, 1560, 2140, 2480]))
                T["dinners"].append({"date": d, "with": who, "place": place, "amount": amt, "currency": cur})
                a = alias(who)
                expense_msg(d, "dinner", place, amt, [f"dinner with {a} at {place}, {m(amt)}", f"{place} with {a}, {m(amt)}",
                                                      f"had dinner at {place} with {a} ({m(amt)})"], 21, who=who)
            if rng.random() < 0.14:
                who, bar = rng.choice(friends(d)), rng.choice(BARS[ck])
                amt = round(rng.uniform(18, 60), 2) if ck == "sf" else float(rng.randint(12, 45) * 10)
                a = alias(who)
                expense_msg(d, "drinks", bar, amt, [f"drinks with {a} at {bar}, {m(amt)}", f"beers at {bar} with {a} {m(amt)}"], 23, who=who)
            if rng.random() < 0.12:
                shop, items = rng.choice(SHOPS[ck])
                item = rng.choice(items)
                amt = round(rng.uniform(12, 180), 2) if ck == "sf" else float(rng.randint(3, 60) * 50 - 1)
                expense_msg(d, "shopping", shop, amt, [f"ordered {item} from {shop}, {m(amt)}", f"{shop} order: {item} {m(amt)}"], 20)
            if wd in (1, 4) and rng.random() < 0.72 or wd == 6 and rng.random() < 0.25:
                gym = sy.GYM[ck]
                partner = "Tom Novak" if ck == "prague" else "Nina Rossi" if d >= date(2026, 3, 1) else None
                sent = None
                if rng.random() < 0.06 and grade_i < len(grade_ladder) - 1:
                    grade_i += 1
                    sent = grade_ladder[grade_i]
                T["climbing"].append({"date": d, "gym": gym, "partner": partner, "sent": sent})
                ts, when = sy.logged(d, 19)
                bits = [f"climbing at {gym}" + (f" with {partner.split()[0]}" if partner and rng.random() < 0.8 else "")]
                if sent:
                    bits.append(rng.choice([f"sent my first {sent}!", f"finally sent a {sent}"]))
                say_t(ts, sy.casual(sy.with_when(", ".join(bits), when)), {"kind": "climb", "date": d.isoformat(), "gym": gym, "sent": sent})
            if wd in (0, 3) and rng.random() < 0.62:
                dist = rng.choice([5, 10, 10])
                secs = max(run_best[dist] + rng.randint(-40, 120), int(run_best[dist] * 0.985))
                run_best[dist] = min(run_best[dist], secs)
                T["runs"].append({"date": d, "km": dist, "secs": secs})
                ts, when = sy.logged(d, 7)
                t = f"{secs // 60}:{secs % 60:02d}"
                say_t(ts, sy.casual(sy.with_when(rng.choice([f"ran {dist}k in {t}", f"{dist}k run, {t}"]), when)),
                      {"kind": "run", "date": d.isoformat(), "km": dist, "secs": secs})
            if rng.random() < 0.12:
                title, rating = rng.choice(MOVIES)
                T["movies"].append({"date": d, "title": title, "rating": rating})
                say_t(sy.at(d, 23, rng.randint(0, 50)), sy.casual(rng.choice([f"watched {title}, {rating}/5", f"{title}: {rating}/5"])),
                      {"kind": "movie", "date": d.isoformat(), "title": title, "rating": rating})
        if rng.random() < 0.9:  # sleep, logged in the morning about the night before
            hours = round(rng.uniform(5.5, 8.6) * 4) / 4
            T["sleep"].append({"date": d, "hours": hours})
            h, mi = int(hours), int(round((hours % 1) * 60))
            say_t(sy.at(d, 7, rng.randint(5, 55)), sy.casual(rng.choice([f"slept {h}h {mi:02d}m", f"sleep: {hours}h", f"{hours} hours of sleep"])),
                  {"kind": "sleep", "date": d.isoformat(), "hours": hours})
        if rng.random() < 0.5:  # a noisy walk that drifts back toward ~75 kg
            weight = round(weight + 0.08 * (75.0 - weight) + rng.uniform(-0.4, 0.4), 1)
            T["weight"].append({"date": d, "kg": weight})
            say_t(sy.at(d, 7, rng.randint(0, 30)), sy.casual(rng.choice([f"weighed {weight} kg", f"weight {weight}kg this morning"])),
                  {"kind": "weight", "date": d.isoformat(), "kg": weight})
        if d.day == 1:  # monthly, as in the year simulation
            if sy.in_sf(d):
                sy.expense(d, "rent", "Mr. Gupta", 3200.0, "USD")
                say_t(sy.at(d, 9, rng.randint(0, 50)), sy.casual(rng.choice(["paid rent, $3,200", "rent paid for " + d.strftime("%B") + ": $3,200"])),
                      {"kind": "expense", "date": d.isoformat(), "category": "rent", "merchant": None, "amount": 3200.0, "currency": "USD", "with": None})
            amt = 11.99 if sy.in_sf(d) else 169.0
            sy.expense(d, "subscription", "Spotify", amt, cur)
            say_t(sy.at(d, 10, 5), sy.casual(f"Spotify renewed {sy.money(amt, cur)}"),
                  {"kind": "expense", "date": d.isoformat(), "category": "subscription", "merchant": "Spotify", "amount": amt, "currency": cur, "with": None})
            fee = (110.0, "USD") if sy.in_sf(d) else (1190.0, "CZK")
            sy.expense(d, "gym", sy.GYM[ck], fee[0], fee[1])
        d += timedelta(days=1)


def spend(cat, start, end, merchant=None, who=None):
    rows = [e for e in T["expenses"] if start <= e["date"] <= end and (cat is None or e["category"] == cat)
            and (merchant is None or e["merchant"] == merchant) and (who is None or e["with"] == who)]
    by = defaultdict(float)
    for e in rows:
        by[e["currency"]] += e["amount"]
    return " + ".join(sy.fmt_money(v, c) for c, v in sorted(by.items())) or "nothing", len(rows)


def scale_questions() -> list[dict]:
    """Questions that only make sense with a lot of data: long sums, averages, extremes, counts over years."""
    q = []

    def add(question, answer):
        q.append({"id": f"s{len(q) + 1:02d}", "category": "scale", "question": question, "answer": answer})

    total, n = spend("coffee", date(2025, 1, 1), date(2025, 12, 31))
    add("How much did I spend on coffee in 2025?", f"{total} ({n} purchases)")
    feb = [s["hours"] for s in T["sleep"] if s["date"].year == 2026 and s["date"].month == 2]
    add("What was my average sleep in February 2026?", f"{sum(feb) / len(feb):.2f} hours ({len(feb)} nights)")
    fives = sorted({m["title"] for m in T["movies"] if m["rating"] == 5})
    add("Which movies did I rate 5/5?", ", ".join(fives))
    last_w = T["weight"][-1]
    add("What did I weigh most recently?", f"{last_w['kg']} kg ({last_w['date']:%b %d, %Y})")
    low = min((w for w in T["weight"] if w["date"].year == 2025), key=lambda w: w["kg"])
    add("What was my lowest weight in 2025?", f"{low['kg']} kg ({low['date']:%b %d, %Y})")
    total, n = spend("drinks", START, sy.END, who="Jakub Horák")
    add("How much have I spent on drinks with Kuba?", f"{total} ({n} times)")
    n = sum(1 for e in T["expenses"] if e["merchant"] == "Doubleshot")
    add("How many times did I get coffee at Doubleshot?", str(n))
    top = max((e for e in T["expenses"] if e["category"] == "dinner"), key=lambda e: (e["amount"] if e["currency"] == "USD" else e["amount"] / 23))
    add("What's the most I've spent on a single dinner in San Francisco?",
        sy.fmt_money(max(e["amount"] for e in T["expenses"] if e["category"] == "dinner" and e["currency"] == "USD"), "USD"))
    n = sum(1 for e in T["expenses"] if e["merchant"] == "Uber")
    add("How many Uber rides have I taken?", str(n))
    last_amazon = max((e for e in T["expenses"] if e["merchant"] == "Amazon"), key=lambda e: e["date"])
    add("When did I last order from Amazon, and how much was it?", f"{last_amazon['date']:%b %d, %Y}, {sy.fmt_money(last_amazon['amount'], 'USD')}")
    total, n = spend("groceries", date(2024, 4, 1), date(2024, 12, 31))
    add("How much did I spend on groceries in 2024?", f"{total} ({n} purchases)")
    runs = [r for r in T["runs"] if r["date"].year == 2024 and r["km"] == 5]
    best5 = min(runs, key=lambda r: r["secs"])
    add("What was my fastest 5k in 2024?", f"{best5['secs'] // 60}:{best5['secs'] % 60:02d} ({best5['date']:%b %d, %Y})")
    return q


def main():
    dense_daily_life(); sy.scripted(); sy.calendar(); sy.books(); sy.tasks(); sy.chitchat()
    sy.MSGS.sort(key=lambda m: m.ts)
    msgs = [m for m in sy.MSGS if START <= m.ts.date() <= sy.END]
    out = {
        "name": "life_scale", "user": sy.USER,
        "description": "A simulated 2.5 years (Apr 2024 - Sep 2026) of one person's messages with exact ground truth.",
        "checkpoints": {"m12": sy.CHECKPOINTS["m12"].isoformat()},
        "now": sy.CHECKPOINTS["m12"].isoformat(),
        "messages": [{"id": f"s{i + 1:05d}", "ts": m.ts.isoformat(), "kind": m.kind, "text": m.text,
                      **({"truth": m.truth} if getattr(m, "truth", None) else {})} for i, m in enumerate(msgs)],
        "qa": sy.questions("m12") + scale_questions(),
    }
    path = Path(__file__).with_name("life_scale.json")
    path.write_text(json.dumps(out, indent=1, ensure_ascii=False, default=str))
    kinds = defaultdict(int)
    for m in out["messages"]:
        kinds[m.get("truth", {}).get("kind") or m["kind"]] += 1
    print(f"{len(msgs)} messages ({sum(1 for m in out['messages'] if 'truth' in m)} with truth) {dict(kinds)}; {len(out['qa'])} questions -> {path}")


if __name__ == "__main__":
    main()
