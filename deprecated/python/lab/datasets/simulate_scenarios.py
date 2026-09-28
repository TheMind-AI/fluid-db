"""Three work scenarios with exact truth (LLP 0014): a support desk, a sales pipeline, a team's projects.

Six months each (Jan-Jun 2026). Every message that states facts carries `truth = {"kind", "checks"}`: the values
some row written from it (or that message's history entry) must hold. A check value may be a list of alternatives.
Each scenario has 36 questions, 3 in each of round 7's 12 memory types, with gold computed from the same truth.

  .venv/bin/python -m lab.datasets.simulate_scenarios     -> lab/datasets/scenario_{support,sales,projects}.json
"""
# @ref LLP 0014#scenarios — the simulators, written with their questions before any run
from __future__ import annotations

import json
import random
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).parent
START, END = date(2026, 1, 5), date(2026, 6, 30)
NOW = datetime(2026, 6, 30, 18, 0)
CHAT = ["thanks!", "ok", "morning", "👍", "lol", "brb, lunch", "what's on my plate today?", "ok cool",
        "remind me what we said about this later", "can you summarize this week?", "hmm", "nice"]


class Sim:
    def __init__(self, name: str, user: str, description: str, seed: int):
        self.name, self.user, self.description = name, user, description
        self.rng = random.Random(seed)
        self.msgs: list[dict] = []
        self.qa: list[dict] = []

    def at(self, d: date, h: int, m: int | None = None) -> datetime:
        return datetime(d.year, d.month, d.day, h, self.rng.randint(0, 50) if m is None else m)

    def say(self, ts: datetime, text, kind: str = "store", truth_kind: str | None = None, **checks):
        m = {"ts": ts.isoformat(), "kind": kind, "text": text}
        if truth_kind:
            m["truth"] = {"kind": truth_kind, "checks": checks}
        self.msgs.append(m)

    def chat(self, every: float = 0.35):
        d = START
        while d <= END:
            if d.weekday() < 5 and self.rng.random() < every:
                text = self.rng.choice(CHAT)
                self.say(self.at(d, self.rng.randint(9, 17)), text, "question" if text.endswith("?") else "chitchat")
            d += timedelta(days=1)

    def q(self, kind: str, question: str, answer: str):
        self.qa.append({"id": f"{self.name[:2]}{len(self.qa) + 1:02d}", "type": kind, "question": question, "answer": answer})

    def dump(self) -> Path:
        self.msgs.sort(key=lambda m: m["ts"])
        out = {"name": f"scenario_{self.name}", "user": self.user, "description": self.description, "now": NOW.isoformat(),
               "messages": [{"id": f"{self.name[:2]}{i + 1:04d}", **m} for i, m in enumerate(self.msgs)], "qa": self.qa}
        path = HERE / f"scenario_{self.name}.json"
        path.write_text(json.dumps(out, indent=1, ensure_ascii=False, default=str))
        types = Counter(q["type"] for q in self.qa)
        print(f"{self.name}: {len(self.msgs)} messages ({sum('truth' in m for m in self.msgs)} with truth), "
              f"{len(self.qa)} questions {dict(types)} -> {path.name}")
        return path


def weekdays(start: date, end: date):
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def fmt_day(d: date) -> str:
    return f"{d:%b} {d.day}, {d.year}"


def usd(x: float) -> str:
    return f"${x:,.0f}" if x == int(x) else f"${x:,.2f}"


# =================================================================================================== support desk
def support() -> Sim:
    s = Sim("support", "Maya Chen", "The desk of Maya Chen, support lead at Brightdesk (scheduling software): customer "
            "tickets, triage and resolutions, refunds, promises, team changes. Jan-Jun 2026.", seed=81)
    rng = s.rng
    customers = {  # company: (plan, contact, email)
        "Acme Corp": ("Enterprise", "Tom Reed", "tom@acme.io"),
        "Globex": ("Pro", "Nora Blake", "nora@globex.com"),
        "Initech": ("Pro", "Carl Weber", "carl.weber@initech.com"),
        "Umbrella Health": ("Enterprise", "Dana Cole", "dana.cole@umbrellahealth.org"),
        "Stark Logistics": ("Starter", "Ivy Park", "ivy@starklogistics.com"),
        "Wayne Retail": ("Pro", "Ben Ortiz", "ben.ortiz@wayneretail.com"),
        "Hooli": ("Starter", "Gavin Lee", "gavin@hooli.xyz"),
        "Soylent Foods": ("Pro", "Rita Moss", "rita@soylentfoods.com"),
    }
    weights = [5, 3, 2, 4, 2, 2, 1, 2]
    areas = {
        "billing": ["Charged twice this month", "Invoice shows the wrong seat count", "Can't update the credit card",
                    "Refund for unused seats", "VAT missing on invoice"],
        "integrations": ["Outlook sync stopped", "Google Calendar events duplicated", "Zoom links missing from invites",
                         "Salesforce sync error", "Webhook deliveries failing"],
        "mobile": ["iOS app crashes on login", "Android notifications arrive late", "Can't book from the mobile app",
                   "App shows the wrong time zone"],
        "sso": ["SSO login loop with Okta", "SAML certificate expired", "New users can't sign in with Azure AD"],
        "performance": ["Booking page loads slowly", "Reports time out", "Search takes 20 seconds"],
    }
    causes = {"billing": ["a duplicate charge from the payment retry", "a stale seat count", "an expired card token"],
              "integrations": ["an expired OAuth token", "a calendar API quota", "a timezone bug in the sync"],
              "mobile": ["a bug in the 5.2 release", "a push certificate problem", "a timezone setting"],
              "sso": ["a wrong ACS URL", "an expired SAML certificate", "a missing group mapping"],
              "performance": ["a missing database index", "a slow report query", "cache misses after a deploy"]}
    area_w = [3, 4, 2, 1, 1]
    T = {"tickets": [], "refunds": [], "csat": []}

    def owner(area: str, d: date) -> str:
        if area == "billing":
            return "Leo Martins" if d < date(2026, 5, 4) else "Ana Silva"
        return {"integrations": "Priya Shah", "sso": "Priya Shah", "mobile": "Sam Okafor", "performance": "Sam Okafor"}[area]

    first = {"Leo Martins": "Leo", "Ana Silva": "Ana", "Priya Shah": "Priya", "Sam Okafor": "Sam"}
    n = 1040
    for d in weekdays(START, END - timedelta(days=1)):
        k = rng.choices([0, 1, 2, 3], [2, 4, 3, 1] if d.weekday() else [0, 2, 4, 3])[0]   # Mondays are busiest
        for _ in range(k):
            n += 1
            company = rng.choices(list(customers), weights)[0]
            plan, contact, email = customers[company]
            if company == "Initech" and d > date(2026, 6, 10):   # after the erasure request Initech has a new contact
                contact, email = "Mira Hahn", "mira.hahn@initech.com"
            area = rng.choices(list(areas), area_w)[0]
            subject = rng.choice(areas[area])
            ts = s.at(d, rng.randint(8, 16))
            body = rng.choice([f"Hi, {subject.lower()} since this morning. Can you take a look?",
                               f"Hello team, we see this: {subject.lower()}. It's blocking a few of our people.",
                               f"{subject}. This started after yesterday's update, please help."])
            s.say(ts, f"New ticket #{n} from {contact} ({company}): \"{subject}\"\n> {body}", truth_kind="ticket_new",
                  ticket=str(n), customer=[company, contact], subject=subject, date=str(d))
            prio = rng.choices(["P3", "P2", "P1"], [4, 5, 1])[0]
            who = owner(area, d)
            t2 = ts + timedelta(minutes=rng.randint(10, 90))
            s.say(t2, rng.choice([f"#{n} → {first[who]}, {prio}", f"gave #{n} to {first[who]} ({prio})",
                                  f"assigned #{n} to {who}, {prio}"]), truth_kind="ticket_triage",
                  ticket=str(n), assignee=[who, first[who]], priority=prio)
            t = {"n": n, "company": company, "contact": contact, "area": area, "subject": subject, "body": body,
                 "opened": d, "assignee": who, "priority": prio, "priority_before": None, "escalated": None,
                 "reopened": None, "resolved": None, "cause": None, "history": [("opened", d)]}
            if prio != "P1" and rng.random() < 0.12:   # escalations
                e = d + timedelta(days=rng.randint(1, 3))
                if e <= END:
                    s.say(s.at(e, rng.randint(9, 16)), f"escalated #{n} to engineering, now P1", truth_kind="ticket_escalate",
                          ticket=str(n), priority="P1")
                    t.update(priority_before=prio, priority="P1", escalated=e)
            r = d + timedelta(days=rng.randint(0, 9))
            if r <= date(2026, 6, 26):
                cause = rng.choice(causes[area])
                s.say(s.at(r, rng.randint(11, 17)), rng.choice([f"#{n} resolved: {cause}", f"closed #{n}, it was {cause}",
                                                                  f"#{n} fixed ({cause}), resolved"]),
                      truth_kind="ticket_resolve", ticket=str(n), status=["resolved", "closed", "fixed"])
                t.update(resolved=r, cause=cause)
                if rng.random() < 0.08 and r + timedelta(days=5) <= date(2026, 6, 20):
                    ro = r + timedelta(days=rng.randint(2, 5))
                    s.say(s.at(ro, rng.randint(9, 12)), f"#{n} reopened, {company.split()[0]} says it's back",
                          truth_kind="ticket_reopen", ticket=str(n), status=["reopened", "open"])
                    r2 = ro + timedelta(days=rng.randint(1, 4))
                    s.say(s.at(r2, rng.randint(13, 17)), f"#{n} resolved again, for good this time",
                          truth_kind="ticket_resolve", ticket=str(n), status=["resolved", "closed", "fixed"])
                    t.update(reopened=ro, resolved=r2)
                if rng.random() < 0.3:
                    score = rng.choices([5, 4, 3, 2], [5, 3, 1, 1])[0]
                    comment = {5: "fast and friendly", 4: "good, took a bit long", 3: "ok", 2: "too slow"}[score]
                    cd = t["resolved"] + timedelta(days=1)
                    if cd <= END:
                        ts_c = s.at(cd, 9)
                        if not (t["contact"] == "Carl Weber" and cd > date(2026, 6, 10)):   # no survey for an erased person
                            s.say(ts_c, f"CSAT survey result for #{n}: {score}/5 from {contact} — \"{comment}\"",
                                  truth_kind="csat", ticket=str(n), score=score)
                            T["csat"].append({"n": n, "company": company, "score": score, "comment": comment})
            T["tickets"].append(t)
    tickets = {t["n"]: t for t in T["tickets"]}
    # refunds
    for d, company, amt, why in [(date(2026, 1, 22), "Initech", 120.0, "the January sync outage"),
                                 (date(2026, 2, 12), "Globex", 480.0, "unused seats"),
                                 (date(2026, 3, 9), "Acme Corp", 1250.0, "the double charge in February"),
                                 (date(2026, 3, 30), "Hooli", 60.0, "a plan downgrade"),
                                 (date(2026, 4, 16), "Initech", 240.0, "the April SSO outage"),
                                 (date(2026, 5, 7), "Wayne Retail", 310.0, "a duplicate invoice"),
                                 (date(2026, 5, 28), "Umbrella Health", 900.0, "the SLA breach on May 20"),
                                 (date(2026, 6, 11), "Soylent Foods", 150.0, "unused seats"),
                                 (date(2026, 6, 24), "Globex", 95.0, "a VAT correction")]:
        s.say(s.at(d, 15), f"refunded {company} {usd(amt)} for {why}", truth_kind="refund", customer=company, amount=amt, date=str(d))
        T["refunds"].append({"date": d, "company": company, "amount": amt, "why": why})
    # plans, preferences, facts about customers
    s.say(s.at(date(2026, 1, 6), 9), "note: Acme Corp and Umbrella Health are on the Enterprise plan; Globex, Initech, "
          "Wayne Retail and Soylent Foods on Pro; Stark Logistics and Hooli on Starter", truth_kind="plans",
          acme=["Enterprise"])
    s.say(s.at(date(2026, 1, 8), 14), "Acme wants every update by email, never phone calls", truth_kind="pref", pref="email")
    s.say(s.at(date(2026, 1, 20), 11), "Nora at Globex prefers a phone call before 10am, email otherwise",
          truth_kind="pref", pref="10")
    s.say(s.at(date(2026, 2, 3), 16), "Dana (Umbrella) complained about being cc'd on our internal threads again — don't",
          truth_kind="pref", pref="cc")
    s.say(s.at(date(2026, 2, 10), 10), "Umbrella rule: never paste patient data into tickets (HIPAA)", truth_kind="pref", pref="patient")
    # team, coverage, SLA (the periods)
    s.say(s.at(date(2026, 1, 5), 9, 5), "team: Priya Shah handles integrations and SSO, Leo Martins billing, Sam Okafor "
          "mobile and performance", truth_kind="team", who=["Priya Shah"])
    s.say(s.at(date(2026, 4, 13), 17), "Leo's last day is Apr 30. Ana Silva starts May 4 and takes over billing",
          truth_kind="team_change", who=["Ana Silva"])
    s.say(s.at(date(2026, 5, 4), 9), "Ana Silva started today, welcome Ana! she owns the billing queue now",
          truth_kind="team_change", who=["Ana Silva"])
    s.say(s.at(date(2026, 4, 27), 16), "from May 1 we run 24/7 coverage: Sam moves to the night shift (22:00-06:00)",
          truth_kind="coverage", who=["Sam"])
    s.say(s.at(date(2026, 5, 22), 10), {"type": "email", "from": "ops@brightdesk.com", "to": "support-leads@brightdesk.com",
          "subject": "New Enterprise SLA from June 1", "date": "2026-05-22T10:12:00",
          "body": "Starting June 1, Enterprise customers get a first response within 4 hours, 24/7, and a status update "
                  "every 8 hours on P1 tickets. Pro stays at 1 business day."}, truth_kind="sla", hours=4)
    # meetings: weekly team sync, QBRs with Acme
    for d in weekdays(START, END):
        if d.weekday() == 0 and rng.random() < 0.8:
            open_now = sum(1 for t in T["tickets"] if t["opened"] <= d and (t["resolved"] is None or t["resolved"] > d))
            s.say(s.at(d, 10, rng.randint(40, 55)), rng.choice([f"team sync done: {open_now} tickets open, focus on "
                                                                f"{rng.choice(['SSO', 'billing backlog', 'mobile crashes', 'integrations'])}",
                                                                f"Monday sync: backlog at {open_now}"]))
    for d, notes in [(date(2026, 2, 5), "they want SSO for 200 more seats and asked about audit logs"),
                     (date(2026, 4, 2), "they flagged two slow integration tickets and want a named contact for billing"),
                     (date(2026, 6, 4), "renewal looks good; they asked for a status page and monthly reports")]:
        s.say(s.at(d - timedelta(days=10), 11), "\n".join(["BEGIN:VEVENT", "SUMMARY:QBR with Acme Corp",
              f"DTSTART:{d:%Y%m%d}T150000", f"DTEND:{d:%Y%m%d}T160000", "LOCATION:Zoom",
              "ORGANIZER;CN=Tom Reed:mailto:tom@acme.io", "END:VEVENT"]), truth_kind="meeting", date=str(d))
        s.say(s.at(d, 16, 20), f"QBR with Acme done: {notes}", truth_kind="meeting_note", date=str(d))
    s.say(s.at(date(2026, 6, 22), 11), "\n".join(["BEGIN:VEVENT", "SUMMARY:QBR with Acme Corp", "DTSTART:20260702T150000",
          "DTEND:20260702T160000", "LOCATION:Zoom", "ORGANIZER;CN=Tom Reed:mailto:tom@acme.io", "END:VEVENT"]),
          truth_kind="meeting", date="2026-07-02")
    # promises
    s.say(s.at(date(2026, 3, 9), 12), "promised Nora (Globex) the CSV export fix by Friday Mar 13", truth_kind="promise", date="2026-03-13")
    s.say(s.at(date(2026, 3, 13), 17), "missed the Globex CSV deadline, told Nora the new ETA is Mar 19", truth_kind="promise", date="2026-03-19")
    s.say(s.at(date(2026, 3, 19), 16), "shipped the CSV export fix, Nora confirmed it works", truth_kind="promise_done")
    s.say(s.at(date(2026, 6, 5), 10), "promised Dana (Umbrella) a written root-cause report on the June 3 outage by Jul 3",
          truth_kind="promise", date="2026-07-03")
    s.say(s.at(date(2026, 4, 20), 14), "told Tom (Acme) we'd add him to the status page beta by May 15", truth_kind="promise", date="2026-05-15")
    s.say(s.at(date(2026, 5, 14), 15), "done: Tom from Acme is on the status page beta", truth_kind="promise_done")
    # forget: GDPR erasure of the Initech contact
    s.say(s.at(date(2026, 6, 10), 11), "Initech asked us to erase Carl Weber's personal data under GDPR — please forget "
          "everything about Carl Weber", "delete", truth_kind="forget", erase=["Carl Weber", "carl.weber@initech.com"])
    s.chat()

    # ------------------------------------------------------------------------------------ questions (gold from truth)
    tk = T["tickets"]
    reopened = [t for t in tk if t["reopened"]]
    escalated = [t for t in tk if t["escalated"]]
    open_end = sorted(t["n"] for t in tk if t["resolved"] is None)
    t_cur = reopened[0]
    s.q("semantic_current", f"What is the status of ticket #{t_cur['n']}?",
        f"Resolved (on {fmt_day(t_cur['resolved'])}, after being reopened on {fmt_day(t_cur['reopened'])})")
    s.q("semantic_current", "Who owns the billing queue now?", "Ana Silva (since May 4, 2026)")
    s.q("semantic_current", "Which tickets are still open?", ", ".join(f"#{x}" for x in open_end))
    e0 = next((t for t in escalated if t["n"] != t_cur["n"]), escalated[0])
    s.q("semantic_history", f"What was the priority of ticket #{e0['n']} before it was escalated?", f"{e0['priority_before']} (then P1)")
    s.q("semantic_history", "Who handled billing before Ana?", "Leo Martins (until Apr 30, 2026)")
    s.q("semantic_history", "What plan was Acme on, and who was their contact?", "Enterprise; Tom Reed (tom@acme.io)")
    q2 = [r for r in T["refunds"] if r["date"] >= date(2026, 4, 1)]
    s.q("aggregate", "How much did we refund in Q2?", f"{usd(sum(r['amount'] for r in q2))} ({len(q2)} refunds)")
    s.q("aggregate", "How many tickets did Acme open?", str(sum(t["company"] == "Acme Corp" for t in tk)))
    s.q("aggregate", "How many tickets were escalated to engineering?", str(len(escalated)))
    e1 = next((t for t in escalated if t["n"] not in (t_cur["n"], e0["n"])), escalated[-1])
    s.q("episodic_event", f"What happened with ticket #{e1['n']}?",
        f"{e1['contact']} ({e1['company']}) opened it on {fmt_day(e1['opened'])}: \"{e1['subject']}\"; assigned to "
        f"{e1['assignee']}; escalated to engineering as P1 on {fmt_day(e1['escalated'])}"
        + (f"; resolved on {fmt_day(e1['resolved'])} ({e1['cause']})" if e1["resolved"] else "; still open"))
    s.q("episodic_event", "What came out of the April QBR with Acme?",
        "They flagged two slow integration tickets and want a named contact for billing")
    busiest = Counter(t["opened"] for t in tk).most_common(1)[0][0]
    s.q("episodic_event", f"Which tickets came in on {fmt_day(busiest)}?",
        ", ".join(f"#{t['n']} ({t['company']}, {t['subject']})" for t in tk if t["opened"] == busiest))
    s.q("episodic_time", f"When was ticket #{t_cur['n']} reopened?", fmt_day(t_cur["reopened"]))
    s.q("episodic_time", "When did Ana start?", "May 4, 2026")
    ini = [r for r in T["refunds"] if r["company"] == "Initech"]
    s.q("episodic_time", "When did we refund Initech, and how much?",
        "; ".join(f"{fmt_day(r['date'])}: {usd(r['amount'])}" for r in ini))
    integ = Counter(t["assignee"] for t in tk if t["area"] == "integrations").most_common(1)[0][0]
    s.q("routine", "Who usually handles integration tickets?", integ)
    wd = Counter(t["opened"].strftime("%A") for t in tk).most_common(1)[0][0]
    s.q("routine", "On which day of the week do the most new tickets come in?", f"{wd}s")
    s.q("routine", "When is the weekly team sync?", "Mondays, in the morning (done by about 10:45)")
    s.q("prospective", "What did I promise Globex, and did we deliver?",
        "The CSV export fix by Mar 13; the deadline was missed, it shipped on Mar 19 and Nora confirmed it works")
    s.q("prospective", "When is the next QBR with Acme?", "Thu Jul 2, 2026 at 15:00 (Zoom)")
    s.q("prospective", "Do I still owe Umbrella Health anything?", "A written root-cause report on the June 3 outage, due Jul 3")
    s.q("preference", "How does Acme want to be contacted?", "By email only, never phone calls")
    s.q("preference", "When does Nora at Globex prefer to be called?", "Before 10am (email otherwise)")
    s.q("preference", "What did Dana at Umbrella complain about?", "Being cc'd on our internal threads")
    t_src = next(t for t in tk if t["company"] == "Acme Corp")
    s.q("source", f"What exactly did {t_src['contact']} write when opening ticket #{t_src['n']}?", f"\"{t_src['subject']}\": {t_src['body']}")
    c_src = next(c for c in T["csat"] if c["score"] <= 3) if any(c["score"] <= 3 for c in T["csat"]) else T["csat"][0]
    s.q("source", f"What did the CSAT comment on ticket #{c_src['n']} say?", f"{c_src['score']}/5: \"{c_src['comment']}\"")
    s.q("source", "What does the new Enterprise SLA say?",
        "From June 1: first response within 4 hours, 24/7, and a status update every 8 hours on P1 tickets; Pro stays at 1 business day")
    s.q("period", "Who covered the night shift after we moved to 24/7 coverage?", "Sam Okafor (22:00-06:00, from May 1)")
    first_ref = ini[0]
    s.q("period", "Who was running billing when Initech got its first refund?", f"Leo Martins ({fmt_day(first_ref['date'])})")
    april = sum(1 for t in tk if t["opened"].month == 4)
    s.q("period", "How many tickets came in during the month before 24/7 coverage started?", f"{april} (April 2026)")
    s.q("metamemory", "What is Globex's phone number?", "UNKNOWN: never mentioned")
    s.q("metamemory", "What is Priya's email address?", "UNKNOWN: never mentioned")
    s.q("metamemory", "How many seats does Hooli have?", "UNKNOWN: never mentioned")
    carl = next(t for t in tk if t["company"] == "Initech")
    s.q("forgotten", "What is Carl Weber's email address?", "UNKNOWN: the user asked to forget him")
    s.q("forgotten", f"Who at Initech opened ticket #{carl['n']}?", "UNKNOWN: the user asked to forget him")
    s.q("forgotten", "Who was our contact at Initech before June?", "UNKNOWN: the user asked to forget him")
    return s


# =================================================================================================== sales pipeline
def sales() -> Sim:
    s = Sim("sales", "Daniel Park", "The notes of Daniel Park, account executive at Lumen Analytics: leads, calls, demos, "
            "proposals, negotiations, wins and losses. Jan-Jun 2026.", seed=82)
    rng = s.rng
    companies = [
        ("Contoso", "Priya Raman", "VP Engineering", "inbound"), ("Northwind Traders", "Luis Ortega", "Head of Data", "outbound"),
        ("Fabrikam", "Hannah Weiss", "Director of Ops", "referral"), ("Tailspin Toys", "Owen Brooks", "CTO", "inbound"),
        ("Wingtip", "Grace Kim", "Analytics Lead", "outbound"), ("Litware", "Sven Dahl", "Head of BI", "inbound"),
        ("Adventure Works", "Mia Lopez", "COO", "referral"), ("Proseware", "Arjun Mehta", "Data Manager", "outbound"),
        ("Woodgrove Bank", "Claire Dubois", "CIO", "inbound"), ("Coho Winery", "Pablo Ruiz", "Owner", "inbound"),
        ("Alpine Ski House", "Lena Fischer", "Head of Finance", "outbound"), ("Fourth Coffee", "Nate Cole", "VP Ops", "inbound"),
        ("Blue Yonder Airlines", "Ravi Nair", "Director of Analytics", "outbound"), ("Lucerne Publishing", "Emma Stone", "CFO", "referral"),
        ("Trey Research", "Yuki Tanaka", "Research Lead", "inbound"), ("Humongous Insurance", "Mark Evans", "VP Data", "outbound"),
        ("Wide World Importers", "Sofia Rossi", "Supply Chain Lead", "inbound"), ("VanArsdel", "Tom Hale", "Head of IT", "referral"),
        ("Relecloud", "Nina Petrov", "VP Product", "inbound"), ("Bellows College", "James Ward", "Director of IR", "outbound"),
        ("Datum Corp", "Aiko Sato", "Head of Analytics", "inbound"), ("Southridge Video", "Carlos Vega", "CTO", "referral"),
        ("Consolidated Messenger", "Ruth Adler", "COO", "outbound"), ("Munson's Pickles", "Dale Munson", "Owner", "inbound"),
    ]
    competitors = ["Quantix", "Metricly", "DataForge"]
    objections = ["worried about SSO", "price is above their budget", "need on-prem deployment", "want a longer pilot",
                  "concerned about data residency in the EU", "their team already uses spreadsheets", "need Salesforce integration"]
    stages = ["discovery", "demo", "proposal", "negotiation"]
    T = {"deals": [], "demos": []}
    # demos happen on Tuesdays and Thursdays with Omar (the sales engineer)
    for idx, (co, contact, role, source) in enumerate(companies):
        d0 = START + timedelta(days=rng.randint(0, 150))
        while d0.weekday() > 4:
            d0 += timedelta(days=1)
        seats = rng.choice([20, 40, 60, 80, 120])
        price = 1500 if d0 < date(2026, 5, 1) else 1800   # per seat per year; list price up in May
        amount = float(seats * price)
        deal = {"company": co, "contact": contact, "role": role, "source": source, "opened": d0, "seats": seats,
                "amount": amount, "stage": "discovery", "stages": [("discovery", d0)], "objection": rng.choice(objections),
                "demo": None, "proposal": None, "discount": None, "final": None, "closed": None, "outcome": None,
                "competitor": None, "close_target": None, "slips": []}
        s.say(s.at(d0, rng.randint(9, 12)), rng.choice([f"new {source} lead: {co}, {contact} ({role}), about {seats} users",
                                                          f"{source} lead from {co} — {contact}, {role}; ~{seats} seats"]),
              truth_kind="deal_new", company=co, contact=[contact, contact.split()[0]], date=str(d0))
        d = d0 + timedelta(days=rng.randint(2, 7))
        while d.weekday() > 4:
            d += timedelta(days=1)
        s.say(s.at(d, rng.randint(10, 16)), f"discovery call with {contact.split()[0]} at {co}: {deal['objection']}; "
              f"next step a demo", truth_kind="deal_note", company=co)
        # demo on a Tue/Thu
        d = d + timedelta(days=rng.randint(4, 12))
        while d.weekday() not in (1, 3):
            d += timedelta(days=1)
        if d > END - timedelta(days=3):
            deal["stage"] = "discovery"
            T["deals"].append(deal)
            continue
        invite_day = d - timedelta(days=rng.randint(2, 5))
        s.say(s.at(invite_day, 10), "\n".join(["BEGIN:VEVENT", f"SUMMARY:Lumen demo for {co}", f"DTSTART:{d:%Y%m%d}T140000",
              f"DTEND:{d:%Y%m%d}T150000", f"ATTENDEE;CN={contact}", "ATTENDEE;CN=Omar Haddad", "ORGANIZER;CN=Daniel Park",
              "END:VEVENT"]), truth_kind="demo", company=co, date=str(d))
        note = rng.choice([f"demo with {co} went well, Omar showed the dashboards; moved to demo stage",
                           f"{co} demo done (with Omar). they liked the alerts", f"{co} demo with Omar: lots of questions on "
                           f"{deal['objection'].split(' about ')[-1].replace('worried ', '')}, overall positive"])
        s.say(s.at(d, 15, rng.randint(10, 40)), note, truth_kind="deal_stage", company=co, stage="demo")
        deal.update(stage="demo", demo=d, demo_note=note)
        deal["stages"].append(("demo", d))
        T["demos"].append({"company": co, "date": d})
        # proposal
        d = d + timedelta(days=rng.randint(3, 10))
        while d.weekday() > 4:
            d += timedelta(days=1)
        if d > END - timedelta(days=2) or rng.random() < 0.15:
            if rng.random() < 0.5 and d <= END:
                lost = d + timedelta(days=rng.randint(1, 5))
                if lost <= END:
                    comp = rng.choice(competitors)
                    s.say(s.at(lost, 16), f"{co} went with {comp}. lost", truth_kind="deal_stage", company=co, stage=["lost", "closed_lost", "closed lost"])
                    deal.update(stage="lost", closed=lost, outcome="lost", competitor=comp)
            T["deals"].append(deal)
            continue
        target = d + timedelta(days=rng.randint(20, 40))
        s.say(s.at(d, rng.randint(10, 17)), f"sent {co} a proposal: {seats} seats, {usd(amount)} ARR, target close "
              f"{fmt_day(target)}", truth_kind="deal_stage", company=co, stage="proposal", amount=amount)
        deal.update(stage="proposal", proposal=d, close_target=target)
        deal["stages"].append(("proposal", d))
        # negotiation
        d = d + timedelta(days=rng.randint(5, 15))
        while d.weekday() > 4:
            d += timedelta(days=1)
        if d > END:
            T["deals"].append(deal)
            continue
        disc = rng.choice([0.0, 0.05, 0.10, 0.15])
        final = round(amount * (1 - disc), 2)
        s.say(s.at(d, rng.randint(10, 17)), f"negotiating with {co}: " + (f"{int(disc * 100)}% discount, now {usd(final)} ARR"
                                                                            if disc else f"no discount, {usd(final)} ARR"),
              truth_kind="deal_stage", company=co, stage=["negotiat"], amount=final)
        deal.update(stage="negotiation", discount=disc, final=final)
        deal["stages"].append(("negotiation", d))
        if disc >= 0.10:
            s.say(s.at(d, 18), {"type": "email", "from": "sara.nguyen@lumen.io", "to": "daniel.park@lumen.io",
                                "subject": f"Re: {co} discount", "date": f"{d}T18:05:00",
                                "body": f"Approved: {int(disc * 100)}% for {co}, one-time, 12-month term only. — Sara"},
                  truth_kind="approval", company=co)
        if rng.random() < 0.35:   # the close date slips
            new_target = target + timedelta(days=rng.randint(14, 30))
            s.say(s.at(d + timedelta(days=2), 11), f"{co} close date slipped to {fmt_day(new_target)} (legal review)",
                  truth_kind="deal_slip", company=co, date=str(new_target))
            deal["slips"].append((target, new_target))
            deal["close_target"] = new_target
        # close
        c = max(d + timedelta(days=rng.randint(7, 25)), deal["close_target"] - timedelta(days=rng.randint(0, 5)))
        while c.weekday() > 4:
            c += timedelta(days=1)
        if c > END:
            T["deals"].append(deal)
            continue
        if rng.random() < 0.7:
            s.say(s.at(c, 16), rng.choice([f"{co} signed! {usd(final)} ARR 🎉", f"closed won: {co}, {usd(final)} ARR"]),
                  truth_kind="deal_stage", company=co, stage=["won", "signed", "closed"], amount=final)
            deal.update(stage="won", closed=c, outcome="won")
        else:
            comp = rng.choice(competitors)
            s.say(s.at(c, 16), f"lost {co} to {comp} at the last step", truth_kind="deal_stage", company=co,
                  stage=["lost", "closed_lost", "closed lost"])
            deal.update(stage="lost", closed=c, outcome="lost", competitor=comp)
        T["deals"].append(deal)
    deals = {x["company"]: x for x in T["deals"]}
    # scripted facts, preferences, periods
    s.say(s.at(date(2026, 1, 5), 9), "Q1 quota is $150k new ARR; Sara Nguyen is my manager and Omar Haddad my sales engineer",
          truth_kind="quota", amount=150000.0)
    s.say(s.at(date(2026, 1, 14), 17), "Contoso's CFO Mark Liu wants everything in writing, no verbal commitments",
          truth_kind="pref", company="Contoso")
    s.say(s.at(date(2026, 1, 16), 12), "Priya Raman (Contoso) prefers Slack over email", truth_kind="pref", company="Contoso")
    s.say(s.at(date(2026, 2, 6), 11), "Woodgrove Bank requires on-prem deployment, no cloud at all", truth_kind="pref", company="Woodgrove Bank")
    s.say(s.at(date(2026, 3, 27), 16), "from April 1 I also cover EMEA accounts, on top of North America",
          truth_kind="territory")
    s.say(s.at(date(2026, 4, 20), 9), {"type": "email", "from": "pricing@lumen.io", "to": "sales@lumen.io",
          "subject": "List price change on May 1", "date": "2026-04-20T09:00:00",
          "body": "From May 1 the list price goes from $1,500 to $1,800 per seat per year. Proposals sent before May 1 keep the old price for 60 days."},
          truth_kind="pricing", amount=1800.0)
    s.say(s.at(date(2026, 3, 31), 18), {"type": "email", "from": "comp@lumen.io", "to": "daniel.park@lumen.io",
          "subject": "Q1 commission statement", "date": "2026-03-31T18:00:00",
          "body": "Q1 closed-won ARR credited: see attached. Commission rate 10%. Paid with the April payroll."}, truth_kind="doc")
    wing = deals.get("Wingtip")
    s.say(s.at(date(2026, 3, 18), 8), {"type": "email", "from": "cfo@wingtip.com", "to": "daniel.park@lumen.io",
          "subject": "Budget", "date": "2026-03-18T08:30:00",
          "body": "Daniel, our analytics budget for this year is frozen until the Q3 planning cycle. We can revisit in September. — Paul Grant, CFO, Wingtip"},
          truth_kind="doc", company="Wingtip")
    # weekly pipeline review (Mondays 9:30 with Sara)
    for d in weekdays(START, END):
        if d.weekday() == 0 and rng.random() < 0.85:
            open_deals = [x for x in T["deals"] if x["opened"] <= d and (x["closed"] is None or x["closed"] > d)]
            s.say(s.at(d, 10, rng.randint(5, 25)), f"pipeline review with Sara done, {len(open_deals)} open deals")
    # follow-ups (intentions)
    s.say(s.at(date(2026, 6, 22), 11), "owe Woodgrove Bank the security questionnaire by Jul 2", truth_kind="todo", company="Woodgrove Bank")
    won_first = min((x for x in T["deals"] if x["outcome"] == "won"), key=lambda x: x["closed"])
    s.say(s.at(date(2026, 6, 25), 10), "\n".join(["BEGIN:VEVENT", f"SUMMARY:{won_first['company']} onboarding check-in with "
          f"{won_first['contact']}", "DTSTART:20260706T110000", "DTEND:20260706T113000", "ORGANIZER;CN=Daniel Park", "END:VEVENT"]),
          truth_kind="meeting", date="2026-07-06")
    s.say(s.at(date(2026, 5, 11), 14), "remind me to send Tailspin the case study by May 15", truth_kind="todo", company="Tailspin Toys")
    s.say(s.at(date(2026, 5, 14), 17), "sent Tailspin the case study", truth_kind="todo_done")
    # forget
    s.say(s.at(date(2026, 3, 3), 15), "Hannah at Fabrikam told me in confidence they're laying off 15% of ops in April, "
          "that's why they're stalling", truth_kind="erased")
    s.say(s.at(date(2026, 3, 5), 9), "please forget what I noted about Fabrikam's layoffs, that was confidential", "delete",
          truth_kind="forget", erase=["laying off", "15% of ops"])
    s.chat(0.3)

    # ------------------------------------------------------------------------------------ questions
    won = [x for x in T["deals"] if x["outcome"] == "won"]
    lost = [x for x in T["deals"] if x["outcome"] == "lost"]
    live = [x for x in T["deals"] if x["outcome"] is None]
    c = deals["Contoso"]
    s.q("semantic_current", "What stage is the Contoso deal in?",
        {"won": f"Closed won on {fmt_day(c['closed'])} at {usd(c['final'] or c['amount'])} ARR" if c["closed"] else "",
         "lost": f"Lost to {c['competitor']}"}.get(c["outcome"], c["stage"]) or c["stage"])
    s.q("semantic_current", "Who is my contact at Tailspin Toys?", f"{deals['Tailspin Toys']['contact']} ({deals['Tailspin Toys']['role']})")
    neg = [x for x in live if x["stage"] in ("proposal", "negotiation")]
    s.q("semantic_current", "Which deals are open in proposal or negotiation right now?",
        ", ".join(f"{x['company']} ({x['stage']})" for x in sorted(neg, key=lambda x: x["company"])) or "none")
    disc = [x for x in T["deals"] if x["discount"]]
    dd = disc[0]
    s.q("semantic_history", f"What was the {dd['company']} deal worth before the discount?",
        f"{usd(dd['amount'])} ARR (then {usd(dd['final'])} after {int(dd['discount'] * 100)}% off)")
    sl = [x for x in T["deals"] if x["slips"]][0]
    s.q("semantic_history", f"What was the original target close date for {sl['company']}?",
        f"{fmt_day(sl['slips'][0][0])} (it slipped to {fmt_day(sl['slips'][0][1])})")
    s.q("semantic_history", "What was the list price per seat before May?", "$1,500 per seat per year (then $1,800 from May 1)")
    q2won = [x for x in won if x["closed"] >= date(2026, 4, 1)]
    s.q("aggregate", "How much new ARR did I win in Q2?", f"{usd(sum(x['final'] for x in q2won))} ({len(q2won)} deals)")
    by_comp = Counter(x["competitor"] for x in lost)
    top_comp, n_comp = by_comp.most_common(1)[0]
    s.q("aggregate", f"How many deals did I lose to {top_comp}?", str(n_comp))
    mar = [x for x in T["demos"] if x["date"].month == 3]
    s.q("aggregate", "How many demos did I run in March?", str(len(mar)))
    dm = sorted([x for x in T["deals"] if x["demo"]], key=lambda x: x["demo"])[2]
    s.q("episodic_event", f"How did the {dm['company']} demo go?", f"On {fmt_day(dm['demo'])}, with Omar: \"{dm['demo_note']}\"")
    wl = won[0]
    s.q("episodic_event", f"How did the {wl['company']} deal go, start to finish?",
        f"{wl['source']} lead on {fmt_day(wl['opened'])}; demo {fmt_day(wl['demo'])}; proposal {usd(wl['amount'])} on "
        f"{fmt_day(wl['proposal'])}; negotiation" + (f" with {int(wl['discount'] * 100)}% off" if wl["discount"] else "")
        + f"; signed on {fmt_day(wl['closed'])} for {usd(wl['final'])}")
    s.q("episodic_event", "What did Wingtip's CFO tell me?", "Their analytics budget is frozen until the Q3 planning cycle; revisit in September")
    s.q("episodic_time", f"When did {wl['company']} sign?", fmt_day(wl["closed"]))
    ls = lost[0]
    s.q("episodic_time", f"When did I lose {ls['company']}, and to whom?", f"{fmt_day(ls['closed'])}, to {ls['competitor']}")
    s.q("episodic_time", "When did I start covering EMEA?", "April 1, 2026")
    dwd = Counter(x["date"].strftime("%A") for x in T["demos"])
    s.q("routine", "Which days of the week do I usually run demos?", " and ".join(f"{w}s" for w, _ in dwd.most_common(2)))
    s.q("routine", "Who usually joins my demos?", "Omar Haddad (sales engineer)")
    s.q("routine", "When is the pipeline review with Sara?", "Mondays, in the morning (around 10)")
    s.q("prospective", "What do I owe Woodgrove Bank?", "The security questionnaire, by Jul 2")
    s.q("prospective", f"When is my next meeting with {won_first['company']}?",
        f"{won_first['company']} onboarding check-in with {won_first['contact']}, Mon Jul 6, 2026 at 11:00")
    s.q("prospective", "Did I send Tailspin the case study?", "Yes, on May 14 (it was due May 15)")
    s.q("preference", "How does Contoso's CFO want to work with us?", "Everything in writing, no verbal commitments (Mark Liu)")
    s.q("preference", "How does Priya Raman prefer to be contacted?", "Slack over email")
    s.q("preference", "What does Woodgrove Bank require?", "On-prem deployment, no cloud at all")
    ap = next((x for x in T["deals"] if x["discount"] and x["discount"] >= 0.10), None)
    s.q("source", "What exactly did Sara write when she approved a discount?",
        f"\"Approved: {int(ap['discount'] * 100)}% for {ap['company']}, one-time, 12-month term only.\"" if ap else "UNKNOWN: never")
    s.q("source", "What did the pricing email say about proposals sent before May?", "They keep the old price for 60 days")
    s.q("source", "What was the commission rate in the Q1 statement?", "10%, paid with the April payroll")
    emea = [x for x in T["deals"] if x["opened"] >= date(2026, 4, 1)]
    s.q("period", "How many new deals came in after I started covering EMEA?", str(len(emea)))
    before = [x for x in won if x["closed"] < date(2026, 5, 1)]
    s.q("period", "How many deals did I win before the price increase?", str(len(before)))
    s.q("period", "What was the list price when I sent Contoso its proposal?",
        "$1,500 per seat per year" if c["proposal"] and c["proposal"] < date(2026, 5, 1) else "$1,800 per seat per year")
    s.q("metamemory", "What is Northwind's budget?", "UNKNOWN: never mentioned")
    s.q("metamemory", "What is Omar's phone number?", "UNKNOWN: never mentioned")
    s.q("metamemory", "How much revenue does Contoso make?", "UNKNOWN: never mentioned")
    s.q("forgotten", "What did Hannah at Fabrikam tell me in confidence?", "UNKNOWN: the user asked to forget it")
    s.q("forgotten", "Is Fabrikam planning layoffs?", "UNKNOWN: the user asked to forget it")
    s.q("forgotten", "Why is Fabrikam stalling?", "UNKNOWN: the user asked to forget it")
    return s


# =================================================================================================== projects
def projects() -> Sim:
    s = Sim("projects", "Elena Rossi", "The notes of Elena Rossi, engineering manager at Orbit Labs: projects, decisions, "
            "action items, incidents, 1:1s, hiring and a reorganization. Jan-Jun 2026.", seed=83)
    rng = s.rng
    T = {"actions": [], "incidents": [], "one_on_ones": defaultdict(list)}
    s.say(s.at(START, 9, 10), "team: Raj Patel (backend), Mei Lin (mobile), Tom Becker (billing), Aisha Khan (infra). "
          "Projects: Atlas (Postgres migration, owner Raj), Beacon (mobile app, owner Mei), Comet (billing revamp, owner Tom)",
          truth_kind="team", owner=["Raj"])
    # 1:1s, biweekly: Raj Tue 10:00, Mei Thu 14:00, Aisha Wed 11:00; Tom Mon 15:00 (until May); Jonas Fri 10:00 (from April)
    slots = {"Raj Patel": (1, 10), "Mei Lin": (3, 14), "Aisha Khan": (2, 11), "Tom Becker": (0, 15), "Jonas Weber": (4, 10)}
    topics = {"Raj Patel": ["wants to grow toward backend architecture", "Atlas cutover worries", "on-call load is high"],
              "Mei Lin": ["prefers async updates in Slack over status meetings", "Beacon beta scope", "wants a mobile hire"],
              "Aisha Khan": ["interested in the SRE track", "alerting gaps after the outage", "DB failover drills"],
              "Tom Becker": ["Comet invoice model", "Stripe Billing migration plan"],
              "Jonas Weber": ["onboarding is going well", "taking over Comet"]}
    week = 0
    d = START
    while d <= END:
        if d.weekday() == 0:
            week += 1
        for who, (wd, h) in slots.items():
            if d.weekday() != wd or week % 2 != 0:
                continue
            if who == "Tom Becker" and d >= date(2026, 5, 11):
                continue
            if who == "Jonas Weber" and d < date(2026, 4, 6):
                continue
            topic = topics[who][len(T["one_on_ones"][who]) % len(topics[who])]
            T["one_on_ones"][who].append({"date": d, "topic": topic})
            s.say(s.at(d, h + 1, rng.randint(0, 20)), f"1:1 with {who.split()[0]}: {topic}", truth_kind="one_on_one",
                  person=[who, who.split()[0]], date=str(d))
        d += timedelta(days=1)
    # decisions (one reversed)
    s.say(s.at(date(2026, 2, 10), 16), "decision: Atlas migrates with Postgres logical replication, not dual writes",
          truth_kind="decision", project="Atlas")
    s.say(s.at(date(2026, 3, 3), 15), "decision: Beacon will be built in React Native", truth_kind="decision", project="Beacon")
    s.say(s.at(date(2026, 3, 24), 17), "after the load test we're reversing the Atlas decision: switching to dual writes "
          "(replication lag was too high)", truth_kind="decision", project="Atlas")
    s.say(s.at(date(2026, 4, 14), 15), "decision: Comet moves invoicing to Stripe Billing", truth_kind="decision", project="Comet")
    # action items: (who, what, created, due, done date or None, slipped-to or None)
    actions = [("Raj Patel", "draft the Atlas cutover plan", date(2026, 2, 23), date(2026, 3, 13), date(2026, 3, 12), None),
               ("Mei Lin", "fix push notifications before the beta", date(2026, 3, 30), date(2026, 4, 17), date(2026, 4, 16), None),
               ("Aisha Khan", "add database alerts", date(2026, 4, 20), date(2026, 5, 8), date(2026, 5, 14), date(2026, 5, 15)),
               ("Tom Becker", "write the Comet invoice spec", date(2026, 3, 9), date(2026, 3, 27), date(2026, 3, 26), None),
               ("Raj Patel", "review the on-call rotation", date(2026, 6, 8), date(2026, 6, 26), None, None),
               ("Jonas Weber", "write the Comet runbook", date(2026, 6, 15), date(2026, 7, 10), None, None),
               ("Mei Lin", "ship the Beacon GA checklist", date(2026, 5, 25), date(2026, 6, 12), date(2026, 6, 11), None),
               ("Aisha Khan", "run a DB failover drill", date(2026, 5, 18), date(2026, 6, 5), date(2026, 6, 4), None),
               ("Raj Patel", "load-test the dual-write path", date(2026, 3, 25), date(2026, 4, 10), date(2026, 4, 9), None),
               ("Jonas Weber", "migrate test invoices to Stripe", date(2026, 5, 18), date(2026, 6, 19), None, date(2026, 7, 3)),
               ("Mei Lin", "write the Beacon beta release notes", date(2026, 4, 6), date(2026, 4, 18), date(2026, 4, 17), None),
               ("Aisha Khan", "document the incident process", date(2026, 2, 23), date(2026, 3, 6), date(2026, 3, 5), None)]
    for who, what, c, due, done, slip in actions:
        first = who.split()[0]
        s.say(s.at(c, rng.randint(14, 17)), rng.choice([f"AI: {first} to {what} by {fmt_day(due)}",
                                                          f"action item — {first}: {what}, due {fmt_day(due)}"]),
              truth_kind="action", owner=[who, first], due=str(due))
        if slip:
            s.say(s.at(due - timedelta(days=1), 12), f"{first}'s '{what}' slipped to {fmt_day(slip)}", truth_kind="action_slip",
                  owner=[who, first], due=str(slip))
        if done:
            s.say(s.at(done, rng.randint(15, 18)), f"done: {first} finished '{what}'", truth_kind="action_done", owner=[who, first])
        T["actions"].append({"who": who, "what": what, "created": c, "due": slip or due, "orig_due": due, "done": done})
    # incidents with postmortems
    for d, what, minutes, cause in [(date(2026, 2, 18), "API outage", 42, "an expired TLS certificate on the load balancer"),
                                   (date(2026, 5, 6), "database failover", 18, "replica lag during the Atlas dual writes"),
                                   (date(2026, 6, 3), "billing double charges", 0, "a retry bug in the Comet invoicing job")]:
        s.say(s.at(d, 11), f"incident: {what}" + (f", {minutes} min down" if minutes else ", no downtime but customers affected"),
              truth_kind="incident", date=str(d))
        s.say(s.at(d + timedelta(days=2), 16), {"type": "doc", "title": f"Postmortem: {what} ({d})",
              "body": f"Impact: {minutes} minutes of downtime." if minutes else "Impact: 37 customers double-charged, refunded.",
              "root_cause": cause, "follow_ups": "add alerting; add a runbook"}, truth_kind="postmortem", date=str(d))
        T["incidents"].append({"date": d, "what": what, "minutes": minutes, "cause": cause})
    # launches, hiring, reorg, the director's email
    s.say(s.at(date(2026, 3, 16), 17), "Jonas Weber accepted our offer! senior backend, starts Apr 6", truth_kind="hire", who="Jonas")
    s.say(s.at(date(2026, 4, 6), 10), "Jonas Weber started today", truth_kind="hire", who="Jonas")
    s.say(s.at(date(2026, 4, 20), 18), "Beacon beta is live 🚀 500 testers invited", truth_kind="launch", project="Beacon", date="2026-04-20")
    s.say(s.at(date(2026, 6, 15), 17), "Beacon GA shipped today!", truth_kind="launch", project="Beacon", date="2026-06-15")
    s.say(s.at(date(2026, 5, 20), 9), {"type": "email", "from": "grace.hopkins@orbitlabs.com", "to": "elena.rossi@orbitlabs.com",
          "subject": "Reorg on June 1", "date": "2026-05-20T09:00:00",
          "body": "Elena — from June 1 your group splits into two teams: Platform (Aisha as tech lead, with Raj and Jonas) and "
                  "Apps (Mei as tech lead, with Tom). Atlas moves to Aisha. Headcount for H2: one more mobile engineer. — Grace"},
          truth_kind="reorg", project="Atlas")
    s.say(s.at(date(2026, 6, 1), 10), "reorg done: Platform = Aisha (lead), Raj, Jonas; Apps = Mei (lead), Tom. Aisha owns Atlas now",
          truth_kind="reorg", owner=["Aisha"])
    s.say(s.at(date(2026, 5, 11), 17), "Jonas takes over Comet while Tom is out", truth_kind="owner", owner=["Jonas"])
    # sprint planning every other Monday, retros every other Friday
    week = 0
    for d in weekdays(START, END):
        if d.weekday() == 0:
            week += 1
            if week % 2 == 1:
                s.say(s.at(d, 11, rng.randint(30, 50)), f"sprint planning done: {rng.randint(18, 34)} points committed")
        if d.weekday() == 4 and week % 2 == 0 and rng.random() < 0.8:
            s.say(s.at(d, 16), f"retro: {rng.choice(['too many interrupts', 'good pairing', 'flaky CI', 'unclear specs', 'on-call was quiet'])}")
    # standup notes on about half the weekdays
    status = ["on track", "blocked on code review", "pairing with Raj", "fixing flaky tests", "waiting on design",
              "done with the spike", "out sick", "on call this week"]
    team = ["Raj", "Mei", "Aisha", "Tom", "Jonas"]
    for d in weekdays(START, END):
        if rng.random() < 0.5:
            present = [p for p in team if not (p == "Jonas" and d < date(2026, 4, 6)) and not (p == "Tom" and date(2026, 5, 11) <= d)]
            picks = rng.sample(present, 2)
            s.say(s.at(d, 9, rng.randint(40, 55)), f"standup: {picks[0]} {rng.choice(status)}, {picks[1]} {rng.choice(status)}")
    # forget: a report's private health information
    s.say(s.at(date(2026, 5, 11), 12), "Tom told me he's having knee surgery on May 18, out for about 5 weeks",
          truth_kind="erased")
    s.say(s.at(date(2026, 5, 13), 9), "please forget what I wrote about Tom's surgery, that's private", "delete",
          truth_kind="forget", erase=["knee surgery", "surgery"])
    s.chat(0.3)

    # ------------------------------------------------------------------------------------ questions
    s.q("semantic_current", "Who owns Atlas now?", "Aisha Khan (since the June 1 reorg)")
    s.q("semantic_current", "Which team is Raj on?", "Platform (with Aisha as lead and Jonas)")
    s.q("semantic_current", "What is the status of Beacon?", "Generally available: GA shipped on Jun 15, 2026 (beta since Apr 20)")
    s.q("semantic_history", "What did we first decide for the Atlas migration?",
        "Postgres logical replication, not dual writes (Feb 10); reversed on Mar 24 to dual writes")
    s.q("semantic_history", "Who owned Atlas before the reorg?", "Raj Patel")
    s.q("semantic_history", "Who owned Comet before Jonas?", "Tom Becker")
    q2 = [i for i in T["incidents"] if i["date"] >= date(2026, 4, 1)]
    s.q("aggregate", "How many incidents did we have in Q2?", f"{len(q2)} ({', '.join(i['what'] for i in q2)})")
    s.q("aggregate", "How many minutes of downtime did the incidents cause in total?", f"{sum(i['minutes'] for i in T['incidents'])} minutes")
    raj = [a for a in T["actions"] if a["who"] == "Raj Patel" and a["done"]]
    s.q("aggregate", "How many action items has Raj completed?", str(len(raj)))
    s.q("episodic_event", "What happened in the May 6 incident?",
        "A database failover with 18 minutes down, caused by replica lag during the Atlas dual writes")
    mei = T["one_on_ones"]["Mei Lin"][-1]
    s.q("episodic_event", "What did Mei and I talk about in our last 1:1?", f"{mei['topic']} ({fmt_day(mei['date'])})")
    s.q("episodic_event", "What happened on April 20?", "Beacon beta went live, 500 testers invited")
    s.q("episodic_time", "When did the Beacon beta launch?", "April 20, 2026")
    s.q("episodic_time", "When did Jonas start?", "April 6, 2026")
    s.q("episodic_time", "When did we reverse the Atlas decision?", "March 24, 2026")
    s.q("routine", "When are my 1:1s with Mei?", "Every other Thursday, around 14:00-15:00")
    s.q("routine", "When are my 1:1s with Raj?", "Every other Tuesday, around 10:00-11:00")
    s.q("routine", "How often do we do sprint planning?", "Every other Monday")
    open_ai = [a for a in T["actions"] if a["done"] is None]
    s.q("prospective", "What action items are still open?",
        "; ".join(f"{a['who'].split()[0]}: {a['what']} (due {fmt_day(a['due'])})" for a in open_ai))
    over = [a for a in open_ai if a["due"] < NOW.date()]
    s.q("prospective", "Which action items are overdue?", "; ".join(f"{a['who'].split()[0]}: {a['what']} (due {fmt_day(a['due'])})" for a in over) or "none")
    s.q("prospective", "What is Jonas on the hook for?",
        "; ".join(f"{a['what']} (due {fmt_day(a['due'])})" for a in open_ai if a["who"] == "Jonas Weber"))
    s.q("preference", "How does Mei prefer to get updates?", "Async updates in Slack over status meetings")
    s.q("preference", "What does Aisha want for her career?", "The SRE track")
    s.q("preference", "What does Raj want to grow toward?", "Backend architecture")
    s.q("source", "What did the postmortem say caused the February outage?", "An expired TLS certificate on the load balancer (42 minutes down)")
    s.q("source", "What did Grace's email say about headcount?", "One more mobile engineer for H2")
    s.q("source", "What was the reason given for reversing the Atlas decision?", "Replication lag was too high (after the load test)")
    s.q("period", "Who was on the Platform team after the reorg?", "Aisha Khan (lead), Raj Patel and Jonas Weber")
    pre = [i for i in T["incidents"] if i["date"] < date(2026, 4, 20)]
    s.q("period", "How many incidents did we have before the Beacon beta launched?", f"{len(pre)} ({', '.join(i['what'] for i in pre)})")
    s.q("period", "Who owned Comet while Tom was out?", "Jonas Weber")
    s.q("metamemory", "What is Raj's salary?", "UNKNOWN: never mentioned")
    s.q("metamemory", "When is Mei's birthday?", "UNKNOWN: never mentioned")
    s.q("metamemory", "What is Grace's phone number?", "UNKNOWN: never mentioned")
    s.q("forgotten", "What surgery is Tom having?", "UNKNOWN: the user asked to forget it")
    s.q("forgotten", "Why was Tom out in May?", "UNKNOWN: the user asked to forget it")
    s.q("forgotten", "When is Tom's surgery?", "UNKNOWN: the user asked to forget it")
    return s


def main():
    for make in (support, sales, projects):
        sim = make()
        sim.dump()


if __name__ == "__main__":
    main()
