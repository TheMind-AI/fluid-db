"""The crossover (LLP 0016): one person's life at three history lengths, the same 36 questions about its last six months.

  scenario_life6m.json   messages from 2026-04-01 (1,023; ~26k tokens)
  scenario_life12m.json  messages from 2025-10-01 (2,030; ~51k tokens)
  scenario_life30m.json  all 5,050 messages (~122k tokens); its database is round 4's

Every question is about April-September 2026, so its gold (computed from the simulator's truth) holds in every window.

  .venv/bin/python -m lab.datasets.crossover
"""
# @ref LLP 0016#part-b-the-crossover — same questions, three windows
from __future__ import annotations

import json
import statistics
from collections import Counter
from datetime import date
from pathlib import Path

HERE = Path(__file__).parent
WINDOWS = {"life6m": "2026-04-01", "life12m": "2025-10-01", "life30m": "2024-04-01"}
SF = "2026-04-01"


def questions(msgs: list[dict]) -> list[dict]:
    T = lambda k: [m["truth"] for m in msgs if m.get("truth", {}).get("kind") == k and m["truth"]["date"] >= SF]
    coffee = [x for x in T("expense") if x["category"] == "coffee"]
    climbs = T("climb")
    sleep_jul = [x["hours"] for x in T("sleep") if x["date"].startswith("2026-07")]
    fives = sorted({x["title"] for x in T("movie") if x["rating"] == 5})
    wd = Counter(date.fromisoformat(x["date"]).strftime("%A") for x in climbs).most_common(3)
    Q = []

    def add(kind, q, a):
        Q.append({"id": f"cx{len(Q) + 1:02d}", "type": kind, "question": q, "answer": a})

    add("semantic_current", "What is my phone number?", "+1 415 555 0199")
    add("semantic_current", "What is David's US phone number?", "+1 628 688 4995 (corrected from +1 628 688 4994)")
    add("semantic_current", "Where does Eva work?", "Gensler (in San Francisco)")
    add("semantic_history", "What US number did I first have for David, before the correction?", "+1 628 688 4994")
    add("semantic_history", "Where did Klára live before she moved to London?", "Berlin")
    add("semantic_history", "Which book did I read right before Atomic Habits?", "Dune by Frank Herbert (finished May 11, 5/5)")
    add("aggregate", "How much did I spend on coffee since moving to San Francisco?",
        f"${sum(x['amount'] for x in coffee):,.2f} ({len(coffee)} purchases)")
    add("aggregate", "How many times did I go climbing from April through September 2026?", str(len(climbs)))
    add("aggregate", "What was my average sleep in July 2026?", f"{statistics.mean(sleep_jul):.2f} hours ({len(sleep_jul)} nights)")
    add("episodic_event", "What did I do on June 23, 2026?",
        "Coffee at Philz Coffee, lunch at Deli Board, climbing at Mission Cliffs, dinner with Eva at Flour + Water "
        "($96.80), watched Sinners (4/5), Uber home")
    add("episodic_event", "Where did I meet Priya Patel?", "At a YC mixer (Apr 22, 2026)")
    add("episodic_event", "What happened on July 12, 2026?", "I proposed to Eva and she said yes: we got engaged")
    add("episodic_time", "When did Eva move to San Francisco?", "May 20, 2026")
    add("episodic_time", "When did Oscar leave Nebula?", "August 15, 2026")
    add("episodic_time", "When did I join Mission Cliffs?", "April 3, 2026")
    add("routine", "Which days of the week do I usually go climbing?",
        f"{wd[0][0]}s and {wd[1][0]}s (sometimes {wd[2][0]}s)")
    add("routine", "Who do I usually climb with these days?", "Nina (Nina Rossi)")
    add("routine", "How much does a coffee usually cost me in San Francisco?", f"about ${statistics.median(x['amount'] for x in coffee):.2f}")
    add("prospective", "What reminders are still open?",
        "Get my SF driver's license (was due Jun 30) and book the wedding venue (due Oct 31)")
    add("prospective", "What's my next meeting?", "The Nebula board meeting on Thu Oct 1, 2026 at 16:00, 1 Mission St, San Francisco")
    add("prospective", "When and where is the Series A kickoff?",
        "Fri Oct 9, 2026 at 10:00 at the Northwind office, 2 Embarcadero Center (with Sarah Kim)")
    add("preference", "Which movies did I rate 5/5 since April?", ", ".join(fives))
    add("preference", "How did I rate Dune, the book?", "5/5")
    add("preference", "What did I love about the Lisbon trip?", "The pastéis de nata at Manteigaria (an amazing trip)")
    add("source", "What did the Lisbon hotel cost, and for which dates?", "$980, Memmo Alfama, May 22-26, 2026 (2 guests)")
    add("source", "How much were the flights to Lisbon?", "$1,340 in total, SFO to LIS for Eva and me")
    add("source", "Who introduced me to Mark Chen, and what does he do?", "Priya Patel; he runs partnerships at Stripe")
    add("period", "What book was I reading when Eva moved to San Francisco?", "Atomic Habits by James Clear")
    add("period", "Where was I living when I got engaged?", "San Francisco (1450 Valencia St)")
    add("period", "What were the big events of my first three months in San Francisco?",
        "Any three of: joined Mission Cliffs (Apr 3), got a US number (Apr 10), met Priya Patel (Apr 22), Eva got the "
        "job at Gensler (May 4) and moved to SF (May 20), the Lisbon trip (May 22-26), Klára moved to London (Jun 10)")
    add("metamemory", "What is Nina's phone number?", "UNKNOWN: never mentioned")
    add("metamemory", "What's Sarah Kim's phone number?", "UNKNOWN: never mentioned")
    add("metamemory", "What car do I drive?", "UNKNOWN: never mentioned")
    add("forgotten", "What's Marco's phone number?", "UNKNOWN: the user asked to forget it")
    add("forgotten", "What's my barber's phone number?", "UNKNOWN: the user asked to forget it")
    add("forgotten", "Who is Jan Dvořák?", "UNKNOWN: the user asked to forget him")
    return Q


def main():
    life = json.loads((HERE / "life_scale.json").read_text())
    qa = questions(life["messages"])
    for name, start in WINDOWS.items():
        msgs = [m for m in life["messages"] if m["ts"] >= start]
        out = {"name": f"scenario_{name}", "user": life["user"], "description": f"The life stream from {start}",
               "now": life["now"], "messages": msgs, "qa": qa}
        (HERE / f"scenario_{name}.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
        print(f"{name}: {len(msgs)} messages, {len(qa)} questions")
    for q in qa:
        if q["type"] in ("aggregate", "routine", "preference"):
            print(" ", q["id"], q["question"], "=>", q["answer"][:120])


if __name__ == "__main__":
    main()
