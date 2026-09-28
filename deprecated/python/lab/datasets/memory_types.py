"""A benchmark by kind of memory (LLP 0013#benchmark): 94 questions over the 5,050-message life (round 4), 12 types.

Gold answers come from the simulator: its scripted storyline (lab/datasets/simulate_year.py), the per-message truth
of life_scale.json (routines, days, ratings are computed from it below), or its existing questions (by id).

  .venv/bin/python -m lab.datasets.memory_types      -> lab/datasets/memory_types.json
"""
# @ref LLP 0013#benchmark — 12 memory types; the types map to stores only through the oracle router
from __future__ import annotations

import json
import statistics
from collections import Counter
from datetime import date
from pathlib import Path

HERE = Path(__file__).parent
MOVE = "2026-04-01"


def truth(kind: str) -> list[dict]:
    ds = json.loads((HERE / "life_scale.json").read_text())
    return [m["truth"] | {"ts": m["ts"], "text": m["text"]} for m in ds["messages"] if m.get("truth", {}).get("kind") == kind]


def routine_golds() -> dict[str, str]:
    climbs, runs, expenses = truth("climb"), truth("run"), truth("expense")
    wd = lambda xs: [d for d, _ in Counter(date.fromisoformat(x["date"]).strftime("%A") for x in xs).most_common(3)]
    c = wd(climbs)
    r = wd(runs)
    sf = lambda xs: [x for x in xs if x["date"] >= MOVE]
    coffee_sf = sorted(x["amount"] for x in sf(expenses) if x["category"] == "coffee")
    groc_sf = [x for x in sf(expenses) if x["category"] == "groceries"]
    lunch_sf = Counter(x["merchant"] for x in sf(expenses) if x["category"] == "lunch").most_common(4)
    ten = [x["secs"] for x in runs if x["km"] == 10][-8:]
    uber = sf([x for x in expenses if x["merchant"] == "Uber"])
    weeks = (date(2026, 9, 30) - date(2026, 4, 1)).days / 7
    return {
        "climb_days": f"{c[0]}s and {c[1]}s (sometimes {c[2]}s)",
        "run_days": f"{r[0]}s and {r[1]}s, in the morning",
        "coffee_sf": f"about ${statistics.median(coffee_sf):.2f} (between ${coffee_sf[0]:.2f} and ${coffee_sf[-1]:.2f})",
        "groceries_sf": " and ".join(sorted({x["merchant"] for x in groc_sf})) + ", on "
                        + " and ".join(d + "s" for d in wd(groc_sf)[:2]),
        "lunch_sf": "mostly " + ", ".join(m for m, _ in lunch_sf),
        "ten_k": f"about {int(statistics.median(ten) // 60)} minutes (recent 10k runs {min(ten) // 60}:{min(ten) % 60:02d}"
                 f"-{max(ten) // 60}:{max(ten) % 60:02d})",
        "uber": f"about {len(uber) / weeks:.1f} times a week ({len(uber)} Uber rides since April 2026)",
    }


def rated(n: int) -> str:
    return ", ".join(sorted({x["title"] for x in truth("movie") if x["rating"] == n}))


def questions() -> list[dict]:
    ds = json.loads((HERE / "life_scale.json").read_text())
    old = {q["id"]: q for q in ds["qa"]}
    g = routine_golds()
    Q = []

    def add(kind, question, answer):
        Q.append({"id": f"mt{len(Q) + 1:02d}", "type": kind, "question": question, "answer": answer})

    def reuse(kind, qid):
        add(kind, old[qid]["question"], old[qid]["answer"])

    # semantic, current: facts as they are now
    for qid in ["m12-q01", "m12-q02", "m12-q04", "m12-q05", "m12-q08", "m12-q09", "m12-q42"]:
        reuse("semantic_current", qid)
    add("semantic_current", "What is Priya Patel's email?", "priya@stripe.com")
    # semantic, history: what a fact used to be
    for qid in ["m12-q12", "m12-q13", "m12-q14", "m12-q15", "m12-q16", "m12-q18"]:
        reuse("semantic_history", qid)
    add("semantic_history", "What was David's US number before I corrected it?", "+1 628 688 4994")
    add("semantic_history", "What was Oscar Lind's job at Nebula?", "Product manager (he left on Aug 15, 2026 for Stripe)")
    # aggregate: exact computation over many rows
    for qid in ["s01", "s02", "s06", "s07", "s09", "m12-q24", "m12-q29", "m12-q32"]:
        reuse("aggregate", qid)
    # episodic, event: what happened, where, with whom
    add("episodic_event", "What did I do on March 28, 2025?",
        "Coffee at Můj šálek kávy, lunch at Bistro Monk, climbing at Smíchoff with Tom, ordered shelves from IKEA, "
        "watched The Phoenician Scheme (3/5), drinks with Tom at Vinograf")
    add("episodic_event", "What did I do on June 23, 2026?",
        "Coffee at Philz Coffee, lunch at Deli Board, climbing at Mission Cliffs, dinner with Eva at Flour + Water "
        "($96.80), watched Sinners (4/5), Uber home")
    add("episodic_event", "Who did I have drinks with after climbing on March 28, 2025, and where?", "Tom, at Vinograf (320 CZK)")
    add("episodic_event", "What happened on July 12, 2026?", "I proposed to Eva and she said yes: we got engaged")
    add("episodic_event", "Where did I meet Lucas Meyer?", "At Web Summit (Nov 5, 2025)")
    add("episodic_event", "Where did I meet Priya Patel?", "At a YC mixer (Apr 22, 2026)")
    add("episodic_event", "What did I do for Christmas 2025?", "Took the train to Brno to spend Christmas with my parents, back on Jan 2")
    add("episodic_event", "What did I do on April 6, 2025?",
        "Coffee at Doubleshot, climbing at Smíchoff with Tom, dinner at Sasazu with Ondra (2,480 CZK), Bolt home, "
        "drinks with Ondra at U Hrocha")
    # episodic, time: when something happened
    add("episodic_time", "When did I get engaged?", "July 12, 2026")
    add("episodic_time", "When did we close the seed round?", "February 20, 2026")
    reuse("episodic_time", "m12-q17")
    reuse("episodic_time", "m12-q34")
    add("episodic_time", "When did I first send a 6c?", "November 12, 2024")
    add("episodic_time", "When did Eva move to San Francisco?", "May 20, 2026")
    add("episodic_time", "When did Oscar leave Nebula?", "August 15, 2026")
    add("episodic_time", "When did I get my flu shot?", "November 25, 2025")
    # routine: regularities no single message states
    add("routine", "Which days of the week do I usually go climbing?", g["climb_days"])
    add("routine", "Which days do I usually run, and at what time of day?", g["run_days"])
    add("routine", "Who do I usually climb with these days?", "Nina (Nina Rossi), since moving to SF")
    add("routine", "What time of day do I usually go climbing?", "In the evening (around 7-9 pm)")
    add("routine", "How much does a coffee usually cost me in San Francisco?", g["coffee_sf"])
    add("routine", "Which grocery stores do I use in San Francisco, and on which days?", g["groceries_sf"])
    add("routine", "Where do I usually get lunch in San Francisco?", g["lunch_sf"])
    add("routine", "What's my usual 10k time these days?", g["ten_k"])
    # prospective: intentions, appointments, what is still open
    add("prospective", "What reminders are still open?",
        "Get my SF driver's license (was due Jun 30) and book the wedding venue (due Oct 31)")
    add("prospective", "What's my next meeting?", "The Nebula board meeting on Thu Oct 1, 2026 at 16:00, 1 Mission St, San Francisco")
    add("prospective", "When and where is the Series A kickoff?",
        "Fri Oct 9, 2026 at 10:00 at the Northwind office, 2 Embarcadero Center (with Sarah Kim)")
    reuse("prospective", "m12-q51")
    add("prospective", "What happened to my reminder to renew my Czech ID card?",
        "I dropped it on Feb 25, 2026: not needed anymore")
    reuse("prospective", "m12-q53")
    reuse("prospective", "m12-q40")
    add("prospective", "Which of my reminders are overdue?", "Get my SF driver's license (it was due Jun 30, 2026)")
    # preference: likes, dislikes, allergies, ratings
    add("preference", "What does Mom love?", "Orchids")
    add("preference", "What is Eva allergic to?", "Peanuts")
    add("preference", "Which books did I rate 5/5?", "Thinking, Fast and Slow; Project Hail Mary; Shoe Dog; Dune")
    add("preference", "What did I think of The Substance?", "Not much: I rated it 2/5")
    add("preference", "What did I love about the Lisbon trip?", "The pastéis de nata at Manteigaria (it was an amazing trip)")
    add("preference", "Which movies did I rate 2/5?", rated(2))
    add("preference", "How did I rate Atomic Habits?", "3/5")
    add("preference", "Which of my friends climbs, besides Tom?", "Nina Rossi (she climbs too; my climbing partner in SF)")
    # source: details that only a document or the exact message holds
    reuse("source", "m12-q48")
    reuse("source", "m12-q47")
    add("source", "What time did my flight to San Francisco leave, and which flight was it?",
        "10:05 on Tue Mar 31, 2026, United UA 8843 (PRG to SFO)")
    reuse("source", "m12-q43")
    add("source", "How many guests was the Lisbon hotel booked for, and what did it cost?", "2 guests, $980 in total (Memmo Alfama, May 22-26)")
    add("source", "Who sends the invites for the Nebula board meetings?", "David Mokos (david@nebula.ai)")
    add("source", "Where did you learn that Eva is allergic to peanuts?",
        "From my message on Oct 6, 2025 (the one saying she works as an architect at Studio Kvadrat)")
    add("source", "According to my lease, when is rent due and how much is it?", "$3,200 a month, due on the 1st")
    # period: life chapters and what belonged to them
    add("period", "What book was I reading when I moved to San Francisco?", "Deep Work by Cal Newport")
    add("period", "Where did I live in 2025?", "Prague")
    add("period", "Who was my climbing partner when I lived in Prague?", "Tom Novak")
    add("period", "Where was I living when we closed the seed?", "Prague")
    add("period", "What were the big events of my first six months in San Francisco?",
        "Any three of: joined Mission Cliffs (Apr 3), got a US number (Apr 10), met Priya Patel (Apr 22), Eva got the "
        "job at Gensler and moved to SF (May 20), the Lisbon trip (May 22-26), got engaged to Eva (Jul 12), Oscar left "
        "Nebula (Aug 15)")
    add("period", "How long have I lived in San Francisco?", "Since April 1, 2026: about six months")
    add("period", "Where did I climb, and with whom, in each city I lived in?",
        "Prague: Smíchoff with Tom; San Francisco: Mission Cliffs with Nina")
    add("period", "Which city was I living in when I finished Thinking, Fast and Slow?", "Prague (Nov 6, 2025)")
    # metamemory: never said, so the memory has to know it doesn't know
    for qid in ["m12-q57", "m12-q58", "m12-q59", "m12-q60"]:
        reuse("metamemory", qid)
    add("metamemory", "What is Nina's phone number?", "UNKNOWN: never mentioned")
    add("metamemory", "When is Dad's birthday?", "UNKNOWN: never mentioned")
    add("metamemory", "What's Sarah Kim's phone number?", "UNKNOWN: never mentioned")
    add("metamemory", "What car do I drive?", "UNKNOWN: never mentioned")
    # forgotten: erased on request; must stay gone from every store
    reuse("forgotten", "m12-q55")
    reuse("forgotten", "m12-q56")
    add("forgotten", "What's Jan Dvořák's phone number?", "UNKNOWN: the user asked to forget him")
    add("forgotten", "What's my barber's phone number?", "UNKNOWN: the user asked to forget it")
    add("forgotten", "Who did I meet at a meetup in October 2025?", "UNKNOWN: the user asked to forget him")
    add("forgotten", "What's the phone number of the Rohlik CTO I met?", "UNKNOWN: the user asked to forget him")
    return Q


def holdout() -> list[dict]:
    """36 more questions (3 per type), written after the system was frozen (LLP 0013.000#held-out): never used to tune it."""
    ds = json.loads((HERE / "life_scale.json").read_text())
    old = {q["id"]: q for q in ds["qa"]}
    ex = truth("expense")
    lunch = [x for x in ex if x["category"] == "lunch" and x["date"].startswith("2025") and x["currency"] == "CZK"]
    movies25 = [x for x in truth("movie") if x["date"].startswith("2025")]
    runs = [x for x in truth("run") if x["date"].startswith("2026-08")]
    climbs = truth("climb")
    weeks = (date(2026, 9, 30) - date(2024, 4, 1)).days / 7
    Q = []

    def add(kind, question, answer):
        Q.append({"id": f"ho{len(Q) + 1:02d}", "type": kind, "question": question, "answer": answer})

    add("semantic_current", "What is David Mokos's email?", "david@nebula.ai")
    add("semantic_current", "Who is my landlord?", "Raj Gupta (+1 415 555 0187)")
    add("semantic_current", "Where does Ondra work?", "Productboard")
    add("semantic_history", "Where did Klára live in December 2025?", "Berlin")
    add("semantic_history", "What was Eva to me before we got engaged?", "My girlfriend")
    add("semantic_history", "What was Tom's phone number in 2025?", "+420 777 123 456 (he changed it on Feb 10, 2026)")
    add("aggregate", "How much did I spend on lunch in Prague in 2025?", f"{sum(x['amount'] for x in lunch):,.0f} CZK ({len(lunch)} lunches)")
    add("aggregate", "How many movies did I watch in 2025?", str(len(movies25)))
    add("aggregate", "How many times did I go running in August 2026?", str(len(runs)))
    add("episodic_event", "What did I do on November 29, 2024?",
        "Coffee at Doubleshot, lunch at Bistro Monk, climbing at Smíchoff with Tom, dinner at Maso a Kobliha with Tom "
        "(1,560 CZK), drinks with Tom at U Hrocha, ordered climbing shoes from Decathlon, Bolt home")
    add("episodic_event", "Who did I have dinner with on August 5, 2026, and where?", "David (David Mokos), at Zuni Café ($135.40)")
    add("episodic_event", "What happened on February 20, 2026?",
        "We closed the seed round: $3M led by Northwind Ventures (Sarah Kim); I also went climbing with Tom")
    add("episodic_time", "When did Nina Rossi start at Nebula?", "March 1, 2026")
    add("episodic_time", "When did I finish Project Hail Mary?", "December 31, 2025")
    add("episodic_time", "When did Klára move to London?", "June 2026 (I mentioned it on June 10, 2026)")
    add("routine", "Which days do I usually buy groceries?", "Wednesdays and Saturdays")
    add("routine", "Where did I usually get coffee in Prague?",
        "A rotation of cafés, most often Café Savoy, EMA Espresso Bar and Kafe Francin (also Café Lounge, Doubleshot)")
    add("routine", "How often do I go climbing?", f"About {len(climbs) / weeks:.1f} times a week (Tuesdays and Fridays, sometimes Sundays)")
    add("prospective", "Do I still need to cancel my Czech phone plan?", "No: it was done on Apr 28, 2026")
    add("prospective", "When was my dentist appointment in San Francisco?", "June 3, 2026 at 9:30, with Dr. Lee at Castro Dental")
    add("prospective", "Did I send the Q2 investor update?", "Yes, on Jul 3, 2026")
    add("preference", old["s03"]["question"], old["s03"]["answer"])
    add("preference", "How did I like Sapiens?", "3/5")
    add("preference", "What did I think of Dune: Part Two?", "Loved it: 5/5 (watched it three times)")
    add("source", "What was the post-money valuation in the term sheet?", "$15M")
    add("source", "What are the check-in and check-out dates of the Lisbon hotel?", "Check-in May 22, check-out May 26, 2026 (Memmo Alfama)")
    add("source", "What is my landlord's email address?", "raj.gupta@gmail.com")
    add("period", "Which gym was I climbing at when Nina joined Nebula?", "Smíchoff, in Prague")
    add("period", "Where was I living when Oscar joined Nebula?", "Prague (Jan 12, 2026)")
    add("period", "What was going on in my life in May 2026?",
        "Any three of: Eva got the job at Gensler (May 4) and moved to SF (May 20); the Lisbon trip with Eva (May 22-26); "
        "booked the Lisbon flights and hotel (May 2-3); finished Dune (May 11); Priya introduced me to Mark Chen (May 12)")
    add("metamemory", "What's Klára's phone number?", "UNKNOWN: never mentioned")
    add("metamemory", "Where did Mark Chen work before Stripe?", "UNKNOWN: never mentioned")
    add("metamemory", "What is my passport number?", "UNKNOWN: never mentioned")
    add("forgotten", "What's the number of the guy from Rohlik I met at a meetup?", "UNKNOWN: the user asked to forget him")
    add("forgotten", "What is Marco's number again?", "UNKNOWN: the user asked to forget it")
    add("forgotten", "Where does Jan Dvořák work?", "UNKNOWN: the user asked to forget him")
    return Q


def main():
    qs = questions()
    path = HERE / "memory_types.json"
    path.write_text(json.dumps({"name": "memory_types", "source": "life_scale.json", "questions": qs}, indent=1, ensure_ascii=False))
    print(f"{len(qs)} questions {dict(Counter(q['type'] for q in qs))} -> {path}")
    ho = holdout()
    hp = HERE / "memory_types_holdout.json"
    hp.write_text(json.dumps({"name": "memory_types_holdout", "source": "life_scale.json", "questions": ho}, indent=1, ensure_ascii=False))
    print(f"{len(ho)} held-out questions -> {hp}")
    for q in qs:
        if q["type"] in ("routine", "preference"):
            print(" ", q["id"], q["question"], "=>", q["answer"])


if __name__ == "__main__":
    main()
