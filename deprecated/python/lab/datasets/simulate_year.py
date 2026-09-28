"""Simulate a year of one person's life as chat messages, with exact ground truth.

Every generator both writes the user's messages and records the truth, so questions can be answered
exactly at any point in time: sums over dozens of expenses, "what was X before it changed", current
values after several changes, forgotten facts, and unanswerable questions.

  .venv/bin/python -m lab.datasets.simulate_year     -> lab/datasets/life_year.json

Deterministic (seeded). Messages are rendered from templates with varied phrasing, relative dates
("yesterday"), nicknames (Ondra, Kuba, Dave), receipts, emails and calendar invites.
"""
# @ref LLP 0009#simulators — messages generated with the exact truth behind them
from __future__ import annotations

import json
import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path

SEED = 2026
START, END = date(2025, 10, 1), date(2026, 9, 30)
MOVE = date(2026, 4, 1)                      # Prague -> San Francisco
CHECKPOINTS = {"m6": datetime(2026, 3, 30, 21, 0), "m12": datetime(2026, 9, 30, 21, 0)}
USER = "Adam Zvada"

rng = random.Random(SEED)


@dataclass
class Msg:
    ts: datetime
    text: object
    kind: str                                  # store | update | delete | question | chitchat
    tags: list = field(default_factory=list)


MSGS: list[Msg] = []
T = defaultdict(list)                         # ground truth tables: name -> list of dicts
HIST = defaultdict(list)                      # (entity, attribute) -> [(date, value)]


def say(ts: datetime, text, kind="store", *tags):
    MSGS.append(Msg(ts, text, kind, list(tags)))


def setv(entity: str, attr: str, when: date, value):
    HIST[(entity, attr)].append((when, value))


def value_at(entity: str, attr: str, when: date):
    vals = [(d, v) for d, v in HIST[(entity, attr)] if d <= when]
    return vals[-1][1] if vals else None


def in_sf(d: date) -> bool:
    return d >= MOVE


def at(d: date, h: int, m: int = 0) -> datetime:
    return datetime.combine(d, time(h, m))


def logged(d: date, h: int, m: int = 0) -> tuple[datetime, str]:
    """When the user logs an event and how they refer to its date."""
    r = rng.random()
    if r < 0.18:  # logged the next morning
        return at(d + timedelta(days=1), rng.randint(8, 10), rng.randint(0, 59)), "yesterday"
    return at(d, h, m) + timedelta(minutes=rng.randint(5, 150)), rng.choice(["", "", "today", "this morning" if h < 12 else "tonight" if h >= 18 else "today"])


def money(amount: float, cur: str) -> str:
    if cur == "USD":
        return rng.choice([f"${amount:.2f}", f"{amount:.2f} USD", f"${amount:.2f}"])
    return rng.choice([f"{amount:,.0f} CZK".replace(",", " "), f"{amount:.0f} Kč", f"{amount:.0f} CZK"])


def casual(s: str) -> str:
    s = rng.choice(["", "", "", "btw ", "fyi ", "note: ", "ok so "]) + s + rng.choice(["", "", "", " 🙂", "!", " lol"])
    return s[0].lower() + s[1:] if rng.random() < 0.4 else s[0].upper() + s[1:]


def with_when(s: str, when: str) -> str:
    return f"{s} {when}".strip() if when else s


# ---------------------------------------------------------------- people & places
PEOPLE = {
    "Tom Novak": {"alias": ["Tom"], "relation": "climbing buddy"},
    "David Mokos": {"alias": ["David", "Dave"], "relation": "cofounder"},
    "Eva Nováková": {"alias": ["Eva"], "relation": "girlfriend"},
    "Ondřej Král": {"alias": ["Ondra"], "relation": "college friend"},
    "Jakub Horák": {"alias": ["Kuba"], "relation": "friend"},
    "Lucie Malá": {"alias": ["Lucie"], "relation": "friend"},
    "Klára Zvadová": {"alias": ["Klára", "my sister"], "relation": "sister"},
    "Jana Zvadová": {"alias": ["Mom"], "relation": "mother"},
    "Petr Zvada": {"alias": ["Dad"], "relation": "father"},
}
CAFES = {"prague": ["Café Savoy", "EMA Espresso Bar", "Kavárna Místo"], "sf": ["Blue Bottle", "Philz Coffee", "Sightglass"]}
LUNCH = {"prague": ["Lokál", "Bistro Monk", "Pho Vietnam"], "sf": ["Tartine Manufactory", "Souvla", "Nopalito"]}
DINNER = {"prague": ["Sasazu", "Eska", "La Degustation"], "sf": ["Nopa", "Zuni Café", "State Bird Provisions"]}
GROCERY = {"prague": ["Albert", "Billa"], "sf": ["Trader Joe's", "Whole Foods"]}
RIDE = {"prague": "Bolt", "sf": "Uber"}
GYM = {"prague": "Smíchoff", "sf": "Mission Cliffs"}


def city_key(d: date) -> str:
    return "sf" if in_sf(d) else "prague"


def cur(d: date) -> str:
    return "USD" if in_sf(d) else "CZK"


def expense(d: date, cat: str, merchant: str, amount: float, currency: str, who: str | None = None, note: str = ""):
    T["expenses"].append({"date": d, "category": cat, "merchant": merchant, "amount": round(amount, 2),
                          "currency": currency, "with": who, "note": note})


# ------------------------------------------------------------------- generators
def daily_life():
    d = START
    run_best = {5: 27 * 60 + 40, 10: 55 * 60 + 10}
    grade_ladder = ["6a+", "6b", "6b+", "6c", "6c+", "7a"]
    grade_i = 1
    while d <= END:
        ck, wd = city_key(d), d.weekday()
        travel = date(2025, 12, 20) <= d <= date(2026, 1, 2) or date(2026, 5, 22) <= d <= date(2026, 5, 26) or d in (date(2026, 3, 31),)
        if not travel:
            # coffee ~3x/week
            if wd in (0, 2, 4) and rng.random() < 0.9 or wd == 5 and rng.random() < 0.3:
                merchant = rng.choice(CAFES[ck])
                amt = rng.choice([4.5, 5.25, 5.5, 6.0, 6.75]) if ck == "sf" else rng.choice([65, 85, 95, 110, 125])
                expense(d, "coffee", merchant, amt, cur(d))
                ts, when = logged(d, 8, rng.randint(0, 50))
                if rng.random() < 0.2:
                    say(ts, receipt(d, merchant, [("Flat white" if ck == "prague" else "Oat latte", amt)], cur(d)))
                else:
                    say(ts, casual(with_when(rng.choice([f"coffee at {merchant} {money(amt, cur(d))}",
                                                          f"☕ {merchant}, {money(amt, cur(d))}",
                                                          f"{merchant} coffee {money(amt, cur(d))}"]), when)))
            # lunch ~2x/week
            if wd in (1, 3) and rng.random() < 0.85:
                place = rng.choice(LUNCH[ck])
                amt = rng.choice([14.5, 16.0, 18.75, 22.0, 26.5]) if ck == "sf" else rng.choice([189, 229, 265, 310, 385])
                expense(d, "lunch", place, amt, cur(d))
                ts, when = logged(d, 12, 30)
                say(ts, casual(with_when(rng.choice([f"lunch at {place}, {money(amt, cur(d))}", f"{place} lunch {money(amt, cur(d))}"]), when)))
            # groceries weekly (Saturday)
            if wd == 5:
                store = rng.choice(GROCERY[ck])
                amt = round(rng.uniform(45, 140), 2) if ck == "sf" else float(rng.randint(6, 18) * 100 + rng.choice([0, 40, 90]))
                expense(d, "groceries", store, amt, cur(d))
                ts, when = logged(d, 11)
                if rng.random() < 0.3:
                    say(ts, receipt(d, store, [("Groceries", amt)], cur(d)))
                else:
                    say(ts, casual(with_when(rng.choice([f"groceries at {store} {money(amt, cur(d))}", f"{store} shopping, {money(amt, cur(d))}"]), when)))
            # rides ~1.5x/week
            if wd in (4, 6) and rng.random() < 0.75:
                amt = round(rng.uniform(12, 35), 2) if ck == "sf" else float(rng.randint(12, 38) * 10)
                expense(d, "transport", RIDE[ck], amt, cur(d))
                ts, when = logged(d, 22)
                say(ts, casual(with_when(rng.choice([f"{RIDE[ck]} home {money(amt, cur(d))}", f"took a {RIDE[ck]}, {money(amt, cur(d))}"]), when)))
            # dinner with someone ~1/week (Wednesday or Saturday)
            if wd in (2, 5) and rng.random() < 0.5:
                who = rng.choice(dinner_partners(d))
                place = rng.choice(DINNER[ck])
                amt = rng.choice([68.0, 84.5, 96.8, 112.0, 135.4]) if ck == "sf" else rng.choice([890, 1240, 1560, 2140, 2480])
                T["dinners"].append({"date": d, "with": who, "place": place, "amount": amt, "currency": cur(d)})
                expense(d, "dinner", place, amt, cur(d), who)
                alias = rng.choice(PEOPLE.get(who, {"alias": [who.split()[0]]})["alias"]) if who in PEOPLE else who.split()[0]
                ts, when = logged(d, 21)
                say(ts, casual(with_when(rng.choice([f"dinner with {alias} at {place}, {money(amt, cur(d))}",
                                                      f"{place} with {alias}, {money(amt, cur(d))}",
                                                      f"had dinner at {place} with {alias} ({money(amt, cur(d))})"]), when)))
            # climbing ~1.5x/week
            if wd in (1, 4) and rng.random() < 0.72 or wd == 6 and rng.random() < 0.2:
                gym = GYM[ck]
                partner = "Tom Novak" if ck == "prague" else "Nina Rossi" if d >= date(2026, 3, 1) else None
                sent = None
                if rng.random() < 0.12 and grade_i < len(grade_ladder) - 1:
                    grade_i += 1
                    sent = grade_ladder[grade_i]
                T["climbing"].append({"date": d, "gym": gym, "partner": partner, "sent": sent})
                pal = partner.split()[0] if partner else None
                ts, when = logged(d, 19)
                bits = [f"climbing at {gym}" + (f" with {pal}" if pal and rng.random() < 0.8 else "")]
                if sent:
                    bits.append(rng.choice([f"sent my first {sent}!", f"finally sent a {sent}"]))
                say(ts, casual(with_when(", ".join(bits), when)))
            # runs ~1.3x/week
            if wd in (0, 3) and rng.random() < 0.62:
                dist = rng.choice([5, 10, 10])
                base = run_best[dist] + rng.randint(-40, 120)
                secs = max(base, int(run_best[dist] * 0.985))
                if secs < run_best[dist]:
                    run_best[dist] = secs
                T["runs"].append({"date": d, "km": dist, "secs": secs})
                ts, when = logged(d, 7)
                t = f"{secs // 60}:{secs % 60:02d}"
                pb = " (new PB!)" if T["runs"] and secs == min(r["secs"] for r in T["runs"] if r["km"] == dist) and len([r for r in T["runs"] if r["km"] == dist]) > 1 else ""
                say(ts, casual(with_when(rng.choice([f"ran {dist}k in {t}{pb}", f"{dist}k run, {t}{pb}"]), when)))
        # monthly: rent, subscriptions, gym
        if d.day == 1:
            if in_sf(d):
                expense(d, "rent", "Mr. Gupta", 3200.0, "USD")
                say(at(d, 9, rng.randint(0, 50)), casual(rng.choice(["paid rent, $3,200", "rent paid for " + d.strftime("%B") + ": $3,200"])))
            expense(d, "subscription", "Spotify", 11.99 if in_sf(d) else 169.0, cur(d))
            if rng.random() < 0.5:
                say(at(d, 10, 5), casual(f"Spotify renewed {money(11.99 if in_sf(d) else 169.0, cur(d))}"))
            gym_fee = (110.0, "USD") if in_sf(d) else (1190.0, "CZK")
            expense(d, "gym", GYM[city_key(d)], gym_fee[0], gym_fee[1])
        d += timedelta(days=1)


def dinner_partners(d: date) -> list[str]:
    """Only people the user already knows and who are in the same city that day."""
    eva_here = not in_sf(d) or d >= date(2026, 5, 20)
    out = (["Eva Nováková", "Eva Nováková"] if eva_here else []) + ["David Mokos"]
    if not in_sf(d):
        out += ["Ondřej Král", "Jakub Horák", "Lucie Malá", "Tom Novak"]
    else:
        out += ["Nina Rossi"] + (["Priya Patel"] if d >= date(2026, 4, 22) else [])
    return out


def receipt(d: date, merchant: str, items, currency: str) -> str:
    if currency == "USD":
        lines = [merchant.upper(), "San Francisco, CA", d.strftime("%m/%d/%Y") + f" {rng.randint(8, 20):02d}:{rng.randint(0, 59):02d}"]
        lines += [f"{name:<20}{amt:>8.2f}" for name, amt in items]
        lines += [f"{'TOTAL':<20}${sum(a for _, a in items):>7.2f}", "VISA ****4421"]
    else:
        lines = [merchant.upper(), "Praha", d.strftime("%d.%m.%Y") + f" {rng.randint(8, 20):02d}:{rng.randint(0, 59):02d}"]
        lines += [f"{name:<20}{amt:>8.0f} Kč" for name, amt in items]
        lines += [f"{'CELKEM':<20}{sum(a for _, a in items):>8.0f} Kč", "Karta ****4421"]
    return "\n".join(lines)


def scripted():
    """Life events on fixed dates (facts, changes, forgets, travel, work)."""
    s = lambda y, m, d, h, text, kind="store": say(at(date(y, m, d), h, rng.randint(0, 50)), text, kind)
    # --- who's who
    setv("me", "city", START, "Prague"); setv("me", "phone", START, "+420 722 238 738")
    s(2025, 10, 1, 9, "hi! I'm Adam Zvada, I live in Prague and I'm building a startup called Nebula with my cofounder David Mokos")
    s(2025, 10, 1, 9, "my number is +420 722 238 738")
    setv("David Mokos", "phone_cz", START, "+420 733 544 390"); setv("David Mokos", "email", START, "david@nebula.ai")
    s(2025, 10, 2, 11, "David's number is 733 544 390 and his email is david@nebula.ai")
    s(2025, 10, 3, 18, "Mom's birthday today (Oct 3), called her. She loves orchids")
    setv("Eva Nováková", "employer", START, "Studio Kvadrat"); setv("Eva Nováková", "relation", START, "girlfriend")
    s(2025, 10, 6, 20, "my girlfriend Eva works as an architect at Studio Kvadrat. she's allergic to peanuts")
    s(2025, 10, 7, 21, "Eva's birthday is September 28")
    setv("Tom Novak", "phone", START, "+420 777 123 456")
    s(2025, 10, 12, 19, "Tom Novak is my climbing buddy, his number is +420 777 123 456")
    T["gym_membership"].append({"gym": "Smíchoff", "start": date(2025, 10, 1), "end": date(2026, 3, 31), "fee": "1190 CZK/month"})
    s(2025, 10, 13, 9, "I have a monthly membership at Smíchoff climbing gym, 1190 CZK a month")
    s(2025, 10, 20, 20, "met Jan Dvořák, CTO at Rohlik, at a meetup. his number is +420 602 555 777")
    setv("Ondřej Král", "phone", START, "+420 606 111 222")
    s(2025, 11, 2, 17, "Ondra (Ondřej Král, college friend) has a new job at Productboard. his number: +420 606 111 222")
    T["people_met"].append({"name": "Lucas Meyer", "date": date(2025, 11, 5)})
    s(2025, 11, 5, 18, "met Lucas Meyer from Sequoia at Web Summit, lucas@sequoia.com. wants to follow up in Jan")
    s(2025, 11, 25, 18, "flu shot done ✅")
    setv("Klára Zvadová", "city", START, "Berlin")
    s(2025, 12, 2, 12, "my sister Klára's birthday today! she lives in Berlin")
    s(2025, 12, 20, 9, "taking the train to Brno for Christmas with my parents, back on Jan 2")
    s(2026, 1, 8, 10, "please forget everything about Jan Dvořák, not relevant anymore", "delete")
    s(2026, 1, 20, 9, "Marco (my barber) number is +420 731 000 444")
    setv("Tom Novak", "phone", date(2026, 2, 10), "+420 777 999 000")
    s(2026, 2, 10, 19, "Tom changed his number, new one is +420 777 999 000", "update")
    say(at(date(2026, 2, 1), 8, 41), {"type": "email", "from": "sarah@northwind.vc", "to": "adam@nebula.ai",
        "subject": "Term sheet draft", "date": "2026-02-01T08:41:00",
        "body": "Adam, attached is the draft term sheet for Nebula's seed: $3M at a $15M post-money valuation, "
                "1x non-participating preference. — Sarah Kim, Partner, Northwind Ventures"})
    T["company"].append({"event": "seed", "date": date(2026, 2, 20), "amount": "$3M", "lead": "Northwind Ventures"})
    s(2026, 2, 20, 17, "WE CLOSED THE SEED 🎉 $3M led by Northwind Ventures (Sarah Kim)")
    T["hires"].append({"name": "Nina Rossi", "role": "engineer", "date": date(2026, 3, 1), "email": "nina@nebula.ai"})
    s(2026, 3, 2, 10, "we hired Nina Rossi as our first engineer at Nebula, starts Mar 1. email nina@nebula.ai. she climbs too!")
    T["hires"].append({"name": "Eva Green", "role": "designer", "date": date(2026, 3, 15), "email": "eva.green@nebula.ai"})
    s(2026, 3, 16, 11, "Eva Green joined Nebula as our designer, eva.green@nebula.ai (not my Eva lol)")
    T["hires"].append({"name": "Oscar Lind", "role": "product manager", "date": date(2026, 1, 12), "email": "oscar@nebula.ai"})
    s(2026, 1, 12, 10, "Oscar Lind started today as Nebula's product manager, oscar@nebula.ai")
    # --- move
    T["flights"].append({"date": date(2026, 3, 31), "route": "Prague → San Francisco", "airline": "United UA 8843",
                          "amount": 18450.0, "currency": "CZK", "confirmation": "K7QX2P"})
    say(at(date(2026, 3, 10), 14, 3), {"type": "email", "from": "no-reply@united.com", "to": "adam@nebula.ai",
        "subject": "Your trip confirmation K7QX2P", "date": "2026-03-10T14:03:00",
        "body": "Confirmation K7QX2P. UA 8843 Prague (PRG) to San Francisco (SFO), Tue Mar 31 2026, dep 10:05. "
                "Total paid: 18,450 CZK."})
    expense(date(2026, 3, 10), "travel", "United", 18450.0, "CZK", note="flight PRG-SFO")
    s(2026, 3, 29, 20, "ended my Smíchoff membership, last month is March")
    say(at(date(2026, 3, 20), 16, 12), {"type": "email", "from": "raj.gupta@gmail.com", "to": "adam@nebula.ai",
        "subject": "Lease for 1450 Valencia St", "date": "2026-03-20T16:12:00",
        "body": "Hi Adam, lease starts April 1, 2026. Rent $3,200/month due on the 1st, security deposit $4,800. "
                "Call me anytime: +1 415 555 0187. — Raj Gupta"})
    setv("me", "city", MOVE, "San Francisco"); setv("me", "address", MOVE, "1450 Valencia St, San Francisco")
    s(2026, 4, 1, 21, "landed in SF! moved into my new place at 1450 Valencia St. new chapter", "update")
    setv("me", "phone", date(2026, 4, 10), "+1 415 555 0199")
    s(2026, 4, 10, 12, "got a US number: +1 415 555 0199, that's my main number now", "update")
    T["gym_membership"].append({"gym": "Mission Cliffs", "start": date(2026, 4, 3), "end": None, "fee": "$110/month"})
    s(2026, 4, 3, 18, "joined Mission Cliffs, $110/month")
    setv("David Mokos", "phone_us", date(2026, 4, 15), "+1 628 688 4994")
    s(2026, 4, 15, 10, "David's US number is +1 628 688 4994")
    setv("Eva Nováková", "employer", date(2026, 5, 4), "Gensler")
    s(2026, 5, 4, 19, "Eva got the job at Gensler in SF!! she's moving here May 20", "update")
    setv("Eva Nováková", "city", date(2026, 5, 20), "San Francisco")
    # Lisbon
    say(at(date(2026, 5, 2), 9, 30), {"type": "email", "from": "reservations@memmo.pt", "to": "adam@nebula.ai",
        "subject": "Booking confirmed: Memmo Alfama", "date": "2026-05-02T09:30:00",
        "body": "Memmo Alfama Hotel, Lisbon. Check-in May 22, check-out May 26, 2026. 2 guests. Total $980.00."})
    expense(date(2026, 5, 2), "travel", "Memmo Alfama", 980.0, "USD", note="Lisbon hotel")
    s(2026, 5, 3, 11, "booked flights SFO→LIS for Eva and me, $1,340 total. Lisbon May 22-26")
    expense(date(2026, 5, 3), "travel", "TAP Air Portugal", 1340.0, "USD", note="Lisbon flights")
    s(2026, 5, 27, 9, "back from Lisbon, amazing trip. the pastéis de nata at Manteigaria were unreal")
    setv("Klára Zvadová", "city", date(2026, 6, 10), "London")
    s(2026, 6, 10, 20, "Klára moved from Berlin to London for her new job", "update")
    T["people_met"].append({"name": "Priya Patel", "date": date(2026, 4, 22)})
    s(2026, 4, 22, 18, "met Priya Patel, engineering manager at Stripe, at a YC mixer. priya@stripe.com")
    s(2026, 5, 12, 16, "Priya introduced me to her colleague Mark Chen who runs partnerships at Stripe")
    setv("Eva Nováková", "relation", date(2026, 7, 12), "fiancée")
    s(2026, 7, 12, 22, "I PROPOSED AND SHE SAID YES 💍 Eva and I are engaged!", "update")
    s(2026, 8, 15, 11, "Oscar left Nebula today, he's joining Stripe as a PM", "update")
    setv("David Mokos", "phone_us", date(2026, 8, 20), "+1 628 688 4995")
    s(2026, 8, 20, 10, "correction: David's US number is +1 628 688 4995, not 4994", "update")
    s(2026, 9, 2, 9, "happy birthday to David today! 🎂 (Sep 2)")
    s(2026, 9, 10, 18, "forget Marco's number, I don't go to that barber anymore", "delete")


def calendar():
    """Board meetings (invites), investor meetings, dentist, with reschedules and cancellations."""
    d = date(2025, 10, 1)
    while d <= date(2026, 10, 31):
        first_thu = d + timedelta(days=(3 - d.weekday()) % 7)
        loc = "1 Mission St, San Francisco" if first_thu >= MOVE else "Nebula office, Karlín, Prague"
        T["meetings"].append({"date": first_thu, "time": "16:00", "title": "Nebula board meeting", "status": "scheduled", "location": loc})
        sent = at(first_thu - timedelta(days=10), 9, 12)
        if sent <= CHECKPOINTS["m12"]:
            say(sent, "\n".join(["BEGIN:VEVENT", "SUMMARY:Nebula board meeting", f"DTSTART:{first_thu:%Y%m%d}T160000",
                                 f"DTEND:{first_thu:%Y%m%d}T173000", f"LOCATION:{loc}",
                                 "ORGANIZER;CN=David Mokos:mailto:david@nebula.ai", "END:VEVENT"]))
        d = (d.replace(day=28) + timedelta(days=5)).replace(day=1)
    # investor meeting rescheduled
    T["meetings"].append({"date": date(2026, 1, 21), "time": "14:00", "title": "Meeting with Lucas Meyer (Sequoia)",
                          "status": "rescheduled", "location": "video call", "original": "2026-01-14 10:00"})
    say(at(date(2026, 1, 6), 11, 0), "set up a call with Lucas Meyer from Sequoia on Wed Jan 14 at 10:00")
    say(at(date(2026, 1, 12), 9, 20), "Lucas moved our call to Wed Jan 21 at 14:00", "update")
    # dentist
    T["appointments"].append({"date": date(2025, 11, 12), "with": "Dr. Horák", "status": "done"})
    say(at(date(2025, 11, 3), 10, 0), "dentist appointment with Dr. Horák on Nov 12 at 9:00")
    T["appointments"].append({"date": date(2026, 2, 18), "with": "Dr. Horák", "status": "cancelled"})
    say(at(date(2026, 2, 9), 10, 0), "booked the dentist (Dr. Horák) again for Feb 18 at 8:30")
    say(at(date(2026, 2, 16), 18, 0), "cancelled the Feb 18 dentist appointment, will do it in SF", "delete")
    T["appointments"].append({"date": date(2026, 6, 3), "with": "Dr. Lee", "status": "done"})
    say(at(date(2026, 5, 25), 10, 0), "dentist in SF: Dr. Lee at Castro Dental, June 3 at 9:30")
    # future plans after the last checkpoint
    T["meetings"].append({"date": date(2026, 10, 9), "time": "10:00", "title": "Nebula Series A kickoff with Sarah Kim",
                          "status": "scheduled", "location": "Northwind office, 2 Embarcadero Center"})
    say(at(date(2026, 9, 28), 15, 0), "Series A kickoff with Sarah Kim on Fri Oct 9 at 10:00 at the Northwind office, 2 Embarcadero Center")


BOOKS = [("Thinking, Fast and Slow", "Daniel Kahneman", 5), ("The Pragmatic Programmer", "Andrew Hunt", 4),
         ("Project Hail Mary", "Andy Weir", 5), ("The Mom Test", "Rob Fitzpatrick", 4), ("Sapiens", "Yuval Noah Harari", 3),
         ("Shoe Dog", "Phil Knight", 5), ("Deep Work", "Cal Newport", 4), ("Dune", "Frank Herbert", 5),
         ("Atomic Habits", "James Clear", 3), ("Zero to One", "Peter Thiel", 4), ("The Three-Body Problem", "Liu Cixin", None)]


def books():
    d = date(2025, 10, 4)
    for i, (title, author, rating) in enumerate(BOOKS):
        T["books"].append({"title": title, "author": author, "start": d, "finish": None, "rating": None, "status": "reading"})
        say(at(d, 22, 0), casual(rng.choice([f"started reading {title} by {author}", f"new book: {title} ({author})"])))
        dur = rng.randint(18, 34)
        fin = d + timedelta(days=dur)
        if rating is None or fin > END - timedelta(days=5):
            break
        T["books"][-1].update(finish=fin, rating=rating, status="finished")
        say(at(fin, 22, 30), casual(rng.choice([f"finished {title}, {rating}/5", f"done with {title}. {rating}/5"])), "update")
        d = fin + timedelta(days=rng.randint(1, 6))
    # one abandoned book is the last one if started early enough: The Three-Body Problem stays "reading"


TASKS = [("renew passport", date(2025, 11, 1), date(2025, 12, 15), "done", date(2025, 12, 3)),
         ("file Czech tax return", date(2026, 2, 1), date(2026, 3, 31), "done", date(2026, 3, 18)),
         ("buy an engagement ring", date(2026, 5, 30), date(2026, 7, 10), "done", date(2026, 7, 2)),
         ("get SF driver's license", date(2026, 4, 5), date(2026, 6, 30), "open", None),
         ("send Q2 investor update", date(2026, 6, 25), date(2026, 7, 5), "done", date(2026, 7, 3)),
         ("book the wedding venue", date(2026, 8, 1), date(2026, 10, 31), "open", None),
         ("cancel Czech phone plan", date(2026, 4, 12), date(2026, 5, 1), "done", date(2026, 4, 28)),
         ("plan Lisbon trip", date(2026, 4, 20), date(2026, 5, 10), "done", date(2026, 5, 3)),
         ("renew Czech ID card", date(2026, 1, 15), date(2026, 3, 1), "cancelled", date(2026, 2, 25))]


def tasks():
    for title, created, due, status, closed in TASKS:
        T["tasks"].append({"title": title, "created": created, "due": due, "status": status, "closed": closed})
        say(at(created, 9, 30), casual(f"remind me to {title} by {due.strftime('%b %d')}"))
        if status == "done" and closed <= END:
            say(at(closed, 18, 0), casual(rng.choice([f"done: {title} ✅", f"{title} — done"])), "update")
        if status == "cancelled":
            say(at(closed, 18, 0), casual(f"never mind the '{title}' reminder, not needed anymore"), "delete")


CHAT = ["lol ok", "thanks!", "good morning", "haha nice", "ok cool", "👍", "brb", "you're the best", "nvm", "hmm",
        "what's the weather like today?", "can you draft an email to Sarah?", "remind me what Tom's number is?",
        "how much did I spend this week?", "what's on my calendar tomorrow?", "translate 'thank you' to Czech",
        "any ideas for dinner tonight?", "summarize this article for me", "what time is it in Prague?", "ok"]


def chitchat():
    d = START
    while d <= END:
        if rng.random() < 0.45:
            text = rng.choice(CHAT)
            say(at(d, rng.randint(9, 22), rng.randint(0, 59)), text, "question" if text.endswith("?") or text.startswith(("can", "summarize", "translate")) else "chitchat")
        d += timedelta(days=1)


# ------------------------------------------------------------------- questions
def fmt_money(total: float, currency: str) -> str:
    return f"${total:,.2f}" if currency == "USD" else f"{total:,.0f} CZK"


def spend(cat: str | None, start: date, end: date, merchant: str | None = None):
    rows = [e for e in T["expenses"] if start <= e["date"] <= end and (cat is None or e["category"] == cat)
            and (merchant is None or e["merchant"] == merchant)]
    by = defaultdict(float)
    for e in rows:
        by[e["currency"]] += e["amount"]
    return " + ".join(fmt_money(v, c) for c, v in sorted(by.items())) or "nothing", len(rows)


def questions(cp: str) -> list[dict]:
    now = CHECKPOINTS[cp].date()
    q = []

    def add(cat, question, answer, *tags):
        q.append({"id": f"{cp}-q{len(q) + 1:02d}", "category": cat, "question": question, "answer": answer})

    me_phone = value_at("me", "phone", now)
    add("current", "What is my phone number?", me_phone)
    add("current", "What is Tom's phone number?", value_at("Tom Novak", "phone", now))
    add("current", "Where do I live?", value_at("me", "address", now) or value_at("me", "city", now))
    add("current", "Where does Eva (my partner) work?", value_at("Eva Nováková", "employer", now))
    add("current", "Where does my sister Klára live?", value_at("Klára Zvadová", "city", now))
    gym = [g for g in T["gym_membership"] if g["start"] <= now and (g["end"] is None or g["end"] >= now)]
    add("current", "Which climbing gym am I a member of, and what does it cost?", f"{gym[-1]['gym']} ({gym[-1]['fee']})" if gym else "UNKNOWN")
    reading = [b for b in T["books"] if b["start"] <= now and (b["finish"] is None or b["finish"] > now)]
    add("current", "What book am I reading right now?", reading[-1]["title"] if reading else "UNKNOWN: no book in progress")
    if cp == "m12":
        us = value_at("David Mokos", "phone_us", now)
        add("current", "What is David's US phone number?", f"{us} (corrected from +1 628 688 4994)")
        add("current", "What is my relationship with Eva now?", "Engaged: she is my fiancée (since Jul 12, 2026)")
        add("current", "Is Oscar Lind still at Nebula?", "No, he left on Aug 15, 2026 to join Stripe as a PM")
        add("current", "How much is my rent and who is my landlord?", "$3,200/month; landlord Raj Gupta (+1 415 555 0187)")
        # history
        add("history", "What was my phone number before my current one?", "+420 722 238 738")
        add("history", "What was Tom's previous phone number?", "+420 777 123 456")
        add("history", "Where did I live before San Francisco?", "Prague")
        add("history", "Where did Eva work before her current job?", "Studio Kvadrat")
        add("history", "Where did Klára live before?", "Berlin")
        add("history", "When did I move to San Francisco?", "April 1, 2026 (flew UA 8843 on Mar 31)")
        add("history", "Which gym was I a member of in Prague?", "Smíchoff (1190 CZK/month, ended March 2026)")
    else:
        add("history", "What was Tom's previous phone number?", "+420 777 123 456")
    first10 = min((r for r in T["runs"] if r["km"] == 10), key=lambda r: r["date"])
    add("history", "What was my time on the first 10k run I ever logged?", f"{first10['secs'] // 60}:{first10['secs'] % 60:02d} ({first10['date']:%b %d, %Y})")

    # aggregation (exact)
    for (cat, y, m, label) in ([("coffee", 2025, 11, "coffee"), ("groceries", 2026, 1, "groceries"), ("lunch", 2025, 12, "lunch")]
                               if cp == "m6" else [("coffee", 2025, 11, "coffee"), ("groceries", 2026, 5, "groceries"),
                                                   ("transport", 2026, 6, "rides (Uber/Bolt)"), ("lunch", 2026, 8, "lunch")]):
        start = date(y, m, 1)
        end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        total, n = spend(cat, start, end)
        add("aggregation", f"How much did I spend on {label} in {start:%B %Y}?", f"{total} ({n} purchases)")
    for (y, m) in ([(2026, 1)] if cp == "m6" else [(2026, 1), (2026, 7)]):
        n = sum(1 for c in T["climbing"] if c["date"].year == y and c["date"].month == m)
        add("aggregation", f"How many times did I go climbing in {date(y, m, 1):%B %Y}?", str(n))
    runs10 = [r for r in T["runs"] if r["km"] == 10 and r["date"] <= now]
    best = min(runs10, key=lambda r: r["secs"])
    add("aggregation", "What's my best 10k time?", f"{best['secs'] // 60}:{best['secs'] % 60:02d} ({best['date']:%b %d, %Y})")
    sends = [c for c in T["climbing"] if c["sent"] and c["date"] <= now]
    add("aggregation", "What's the hardest climbing grade I've sent?", f"{sends[-1]['sent']} ({sends[-1]['date']:%b %d, %Y})" if sends else "UNKNOWN")
    fin = [b for b in T["books"] if b["finish"] and b["finish"] <= now]
    add("aggregation", "How many books have I finished, and which did I rate 5/5?",
        f"{len(fin)} finished; 5/5: " + ", ".join(b["title"] for b in fin if b["rating"] == 5))
    evad = [x for x in T["dinners"] if x["with"] == "Eva Nováková" and x["date"] <= now and x["date"].year == 2026]
    add("aggregation", "How many dinners out did I have with Eva in 2026?", str(len(evad)))
    if cp == "m12":
        rent = sum(e["amount"] for e in T["expenses"] if e["category"] == "rent")
        add("aggregation", "How much rent have I paid in total?", f"${rent:,.0f} ({int(rent / 3200)} months × $3,200)")
        add("aggregation", "How much did the Lisbon trip cost for flights plus hotel?", "$2,320 ($1,340 flights + $980 hotel)")
        total, n = spend("coffee", date(2025, 10, 1), END, "Blue Bottle")
        add("aggregation", "How much have I spent at Blue Bottle in total?", f"{total} ({n} visits)")
        sf_climbs = sum(1 for c in T["climbing"] if c["date"] >= MOVE)
        add("aggregation", "How many times have I gone climbing since moving to SF?", str(sf_climbs))

    # temporal
    last_ondra = max((x for x in T["dinners"] if x["with"] == "Ondřej Král" and x["date"] <= now), key=lambda x: x["date"], default=None)
    add("temporal", "When did I last have dinner with Ondra?", f"{last_ondra['date']:%b %d, %Y} at {last_ondra['place']}" if last_ondra else "UNKNOWN: never")
    nxt = min((m for m in T["meetings"] if m["date"] > now and m["title"] == "Nebula board meeting"), key=lambda m: m["date"])
    add("temporal", "When and where is the next Nebula board meeting?", f"{nxt['date']:%a %b %d, %Y} at 16:00, {nxt['location']}")
    last_dent = max((a for a in T["appointments"] if a["status"] == "done" and a["date"] <= now), key=lambda a: a["date"])
    add("temporal", "When was my last dentist appointment, and with whom?", f"{last_dent['date']:%b %d, %Y} with {last_dent['with']}")
    add("temporal", "When did I first meet Lucas Meyer, and where?", "Nov 5, 2025 at Web Summit")
    if cp == "m12":
        add("temporal", "When did I get engaged?", "July 12, 2026")
        add("temporal", "What's on my calendar for October 2026?",
            "Nebula board meeting Thu Oct 1 at 16:00 (1 Mission St) and the Series A kickoff with Sarah Kim Fri Oct 9 at 10:00")
        add("temporal", "When was the call with Lucas Meyer?", "Wed Jan 21, 2026 at 14:00 (moved from Jan 14 at 10:00)")

    # relationships / semi-structured
    add("relationships", "Who led Nebula's seed round, and how much did we raise?", "Northwind Ventures (Sarah Kim), $3M" if now >= date(2026, 2, 20) else "UNKNOWN")
    add("relationships", "What's Lucas Meyer's email?", "lucas@sequoia.com")
    add("semi_structured", "What were the terms in Sarah's term sheet draft?", "$3M at a $15M post-money valuation, 1x non-participating preference")
    if cp == "m12":
        add("relationships", "Who is Eva Green?", "Nebula's designer (joined Mar 15, 2026; eva.green@nebula.ai), not my partner Eva")
        add("relationships", "Who introduced me to Mark Chen?", "Priya Patel (Stripe)")
        add("relationships", "Who are my climbing partners?", "Tom Novak (Prague) and Nina Rossi (SF)")
        add("semi_structured", "What's the confirmation code for my flight to San Francisco?", "K7QX2P (United UA 8843)")
        add("semi_structured", "How big was the security deposit for my apartment?", "$4,800")
        add("semi_structured", "Which hotel did we stay at in Lisbon?", "Memmo Alfama (May 22-26, 2026)")
    # status
    add("status", "Did I renew my passport?", "Yes, done on Dec 3, 2025")
    add("status", "What happened to the February dentist appointment?", "Cancelled (it was Feb 18 with Dr. Horák)")
    if cp == "m12":
        add("status", "Do I still have to get my SF driver's license?", "Yes, still open (was due Jun 30)")
        add("status", "Is the wedding venue booked?", "No, still open (due Oct 31)")
        add("status", "Did I finish The Three-Body Problem?", "No, still reading it" if any(b["title"] == "The Three-Body Problem" and b["finish"] is None for b in T["books"]) else "UNKNOWN")
    # forgotten / unanswerable
    add("forgotten", "Who is Jan Dvořák?", "UNKNOWN: the user asked to forget him")
    if cp == "m12":
        add("forgotten", "What's Marco's phone number?", "UNKNOWN: the user asked to forget it")
    add("unanswerable", "What's my blood type?", "UNKNOWN: never mentioned")
    add("unanswerable", "What's Ondra's email address?", "UNKNOWN: never mentioned")
    add("unanswerable", "How much does Eva earn?", "UNKNOWN: never mentioned")
    if cp == "m12":
        add("unanswerable", "What's Nina's birthday?", "UNKNOWN: never mentioned")
    return q


def main():
    daily_life(); scripted(); calendar(); books(); tasks(); chitchat()
    MSGS.sort(key=lambda m: m.ts)
    msgs = [m for m in MSGS if START <= m.ts.date() <= END]
    out = {
        "name": "life_year", "user": USER,
        "description": "A simulated year (Oct 2025 - Sep 2026) of one person's messages with exact ground truth.",
        "checkpoints": {k: v.isoformat() for k, v in CHECKPOINTS.items()},
        "now": CHECKPOINTS["m12"].isoformat(),
        "messages": [{"id": f"y{i + 1:04d}", "ts": m.ts.isoformat(), "kind": m.kind, "text": m.text} for i, m in enumerate(msgs)],
        "qa": questions("m12"),
        "qa_m6": questions("m6"),
    }
    path = Path(__file__).with_name("life_year.json")
    path.write_text(json.dumps(out, indent=1, ensure_ascii=False, default=str))
    kinds = defaultdict(int)
    for m in msgs:
        kinds[m.kind] += 1
    print(f"{len(msgs)} messages {dict(kinds)}; {len(out['qa'])} questions at m12, {len(out['qa_m6'])} at m6 -> {path}")
    print(f"expenses {len(T['expenses'])}, climbing {len(T['climbing'])}, runs {len(T['runs'])}, dinners {len(T['dinners'])}, "
          f"books {len(T['books'])}, meetings {len(T['meetings'])}")


if __name__ == "__main__":
    main()
