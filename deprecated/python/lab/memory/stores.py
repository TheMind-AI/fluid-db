"""The stores (LLP 0013): different kinds of memory, all derived from the one log (and the facts built from it).

  log          raw messages, verbatim (sensory buffer / verbatim trace)
  log_all      every message at once (the full-context baseline)
  facts        FluidDB tables: the deterministic reader's exact answer, plus matching readable rows and history
  episodes     the log cut into days, each with a gist (episodic memory; one LLM gist per day, batched)
  routines     regularities computed by code from the facts: days, times, places, partners, amounts (procedural)
  intentions   reminders, plans and appointments with due dates and status (prospective memory)
  preferences  who likes, loves, dislikes, is allergic to or rated what (evaluative memory)
  periods      where the user lived when, month summaries, and the milestones (autobiographical memory)

Consolidation ("sleep") runs before the stores are built: `erase_forgotten` removes what the user asked to forget
from the log, history and rows; then `label_messages` has the decider answer five yes/no questions of every message
(intention, done, cancel, preference, milestone). Intentions, preferences and periods read those at their own
thresholds (`gate`), and the LLM extracts only from what passes.
"""
# @ref LLP 0013#human-memory-and-what-each-part-becomes — one store per kind of memory, all from one log
from __future__ import annotations

import json
import math
import re
import statistics
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

from lab.common.llm import chat_json
from lab.memory.core import Context, Evidence, Store, days_named
from lab.systems.derived import fold


def parallel(fn, items, workers: int = 8) -> list:
    with ThreadPoolExecutor(workers) as pool:
        return list(pool.map(fn, items))


_TOK = re.compile(r"[a-z0-9áčďéěíňóřšťúůýž@.+]+")


def stem(w: str) -> str:
    """Just enough stemming for BM25 to meet "groceries"/"grocery", "meetings"/"meeting", "works"/"work"."""
    for suf, rep in (("ings", ""), ("ing", ""), ("ies", "y"), ("ied", "y"), ("es", ""), ("ed", ""), ("s", "")):
        if w.endswith(suf) and len(w) - len(suf) >= 3 and not w.endswith("ss"):
            w = w[: -len(suf)] + rep
            break
    return w[:-1] if w.endswith("e") and len(w) > 3 else w


def terms(text: str) -> list[str]:
    return [stem(t) for t in _TOK.findall(str(text).lower()) if len(t) > 1]


class Index:
    """BM25 over stemmed terms, the documents tokenized once."""

    def __init__(self, docs: list[str], k1: float = 1.5, b: float = 0.75):
        self.tf = [Counter(terms(d)) for d in docs]
        self.lens = [sum(c.values()) for c in self.tf]
        self.avg = (sum(self.lens) / len(self.lens) if self.lens else 1) or 1
        self.df = Counter(t for c in self.tf for t in c)
        self.n, self.k1, self.b = len(docs), k1, b

    def top(self, query: str, k: int) -> list[int]:
        q = set(terms(query))
        idf = {t: math.log(1 + (self.n - self.df[t] + 0.5) / (self.df[t] + 0.5)) for t in q if t in self.df}
        scored = []
        for i, tf in enumerate(self.tf):
            s = sum(idf[t] * tf[t] * (self.k1 + 1) / (tf[t] + self.k1 * (1 - self.b + self.b * self.lens[i] / self.avg))
                    for t in idf if t in tf)
            if s > 0:
                scored.append((s, i))
        return [i for _, i in sorted(scored, key=lambda x: -x[0])[:k]]


def top(question: str, docs: list[str], k: int) -> list[int]:
    return Index(docs).top(question, k)


def when(ts: str) -> str:
    return ts[:16].replace("T", " ")


def day_of(ts: str) -> str:
    """The day a message belongs to: before 4 am it is still the evening before."""
    t = datetime.fromisoformat(ts)
    return str((t - timedelta(hours=4)).date())


# ------------------------------------------------------------------------------------------------------ log
class LogStore(Store):
    name = "log"
    description = "the user's raw messages, verbatim, with the time each was sent: exact wording, details said once"

    def build(self, ctx):
        self.texts = [f"[{when(ts)}] {text}" for _, ts, text in ctx.log]
        self.index = Index(self.texts)
        return {"messages": len(self.texts)}

    def read(self, question, ctx, k=12):
        days = set(days_named(question))
        on_day = [i for i, (_, ts, _) in enumerate(ctx.log) if day_of(ts) in days][:16]
        picked = list(dict.fromkeys(on_day + self.index.top(question, k)))
        return [Evidence("log", self.texts[i], ctx.log[i][1], [ctx.log[i][0]]) for i in sorted(picked)]


class FullLogStore(Store):
    name = "log_all"
    description = "every message the user ever sent"

    def read(self, question, ctx, k=0):
        return [Evidence("log_all", "\n".join(f"[{when(ts)}] {text}" for _, ts, text in ctx.log))]


# ---------------------------------------------------------------------------------------------------- facts
class FactsStore(Store):
    name = "facts"
    description = ("a database of facts from the messages: people with their current and earlier phone numbers, "
                   "emails, jobs, homes; expenses, workouts, sleep, weight, movies, books, events; exact counts, "
                   "totals, averages, latest and previous values")

    def build(self, ctx):
        self.lines = [ln for ln in ctx.reader.eng.readable_text().splitlines() if ln and not ln.startswith("## ")]
        self.index = Index(self.lines)
        return {"lines": len(self.lines)}

    def read(self, question, ctx, k=10):
        out = ctx.reader.ask(question)
        ev = []
        if out["answer"] != "I don't know.":
            v = out.get("verified") or 0.0
            ev.append(Evidence("facts", f"The database computed: {out['answer'][:500]} (query: {out.get('plan', '')}; "
                                        f"checker's trust {v:.2f})", confidence=v, exact=v >= 0.7))
        ev += [Evidence("facts", self.lines[i][:400]) for i in self.index.top(question, k)]
        return ev


# ------------------------------------------------------------------------------------------------- episodes
GIST_SCHEMA = {"type": "object", "properties": {"days": {"type": "array", "items": {"type": "object", "properties": {
    "date": {"type": "string"}, "gist": {"type": "string"}}, "required": ["date", "gist"], "additionalProperties": False}}},
    "required": ["days"], "additionalProperties": False}
GIST_PROMPT = """Below are one person's messages to their assistant, grouped by day. For each day write a gist of one or
two sentences: what happened, where, with whom, and anything notable (news, plans, feelings, purchases with amounts).
Keep names, places, numbers and dates exact. Don't add anything the messages don't say.

{days}"""


class EpisodeStore(Store):
    name = "episodes"
    description = ("episodes: what happened on each day (activities, places, people, trips, news, what the user said "
                   "about it), with dates")

    def build(self, ctx):
        days = defaultdict(list)
        for i, ts, text in ctx.log:
            days[day_of(ts)].append((i, ts, text))

        def gist(chunk):
            block = "\n\n".join(f"== {d}\n" + "\n".join(f"[{when(ts)[11:]}] {str(t)[:300]}" for _, ts, t in days[d])
                                for d in chunk)
            got, _ = chat_json(ctx.model, GIST_PROMPT.format(days=block), GIST_SCHEMA, schema_name="gists",
                               tag="memory:episodes", effort="low", max_tokens=4000)
            return {g["date"]: g["gist"] for g in got["days"]}

        def make():
            keys = sorted(days)
            chunks = [keys[j:j + 12] for j in range(0, len(keys), 12)]
            gists = {d: g for got in parallel(gist, chunks) for d, g in got.items()}
            return {d: {"gist": gists.get(d, ""), "ids": [i for i, _, _ in days[d]]} for d in keys}
        self.episodes = ctx.saved("episodes", make)
        self.keys = sorted(self.episodes)
        text = {i: t for i, _, t in ctx.log}
        self.index = Index([f"{d} {date.fromisoformat(d):%A %B %Y} {self.episodes[d]['gist']} "
                            + " ".join(str(text.get(i, ""))[:200] for i in self.episodes[d]["ids"]) for d in self.keys])
        return {"episodes": len(self.keys)}

    def read(self, question, ctx, k=6):
        named = [d for d in days_named(question) if d in self.episodes]
        picked = list(dict.fromkeys(named + [self.keys[i] for i in self.index.top(question, k)]))[:k + len(named)]
        text = {i: (ts, t) for i, ts, t in ctx.log}
        ev = []
        for d in sorted(picked):
            ep = self.episodes[d]
            msgs = "; ".join(f"[{when(text[i][0])[11:]}] {str(text[i][1])[:160]}" for i in ep["ids"][:16] if i in text)
            ev.append(Evidence("episodes", f"{d} ({date.fromisoformat(d):%A}): {ep['gist']} | messages: {msgs}", d, ep["ids"]))
        return ev


# ------------------------------------------------------------------------------------------------ periods
def residence_periods(ctx) -> list[dict]:
    """Where the user lived when: the user's own row (city, home, since) and its history; one period if unknown."""
    db, first, last = ctx.db, ctx.log[0][1][:10], ctx.now.strftime("%Y-%m-%d")
    has_people = db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'people'").fetchone()
    row = db.execute("SELECT * FROM people WHERE name = ?", (ctx.user,)).fetchone() if has_people else None
    periods = []
    if row:
        row = dict(row)
        cols = [c for c in row if re.search(r"city|home|address|residence", c)]
        hist = db.execute("SELECT column_name, old_value, new_value, ts FROM _history WHERE table_name = 'people' AND row_id = ? "
                          "ORDER BY id", (row["id"],)).fetchall()
        changes = [(h[3][:10], h[1], h[2]) for h in hist if h[0] in cols and h[0].startswith(("city", "home"))]
        since = next((row[c] for c in cols if "since" in c and row[c]), None)
        city_now = next((row[c] for c in cols if c.startswith("city") and row[c]), None)
        home_now = next((row[c] for c in cols if ("home" in c or "address" in c) and row[c]), None)
        old_city = next((old for _, old, _ in changes if old), None)
        start_now = since or (changes[-1][0] if changes else first)
        if old_city:
            periods.append({"place": old_city, "start": first, "end": str(date.fromisoformat(start_now[:10]) - timedelta(days=1))})
        periods.append({"place": city_now or "unknown", "home": home_now, "start": start_now[:10], "end": last})
    return periods or [{"place": "unknown", "start": first, "end": last}]


PERIOD_SCHEMA = {"type": "object", "properties": {
    "periods": {"type": "array", "items": {"type": "object", "properties": {
        "start": {"type": "string"}, "end": {"type": "string"},
        "label": {"type": "string", "description": "what held in this period, e.g. 'living in Prague', 'Leo runs billing'"}},
        "required": ["start", "end", "label"], "additionalProperties": False}},
    "breaks": {"type": "array", "items": {"type": "object", "properties": {
        "date": {"type": "string"}, "why": {"type": "string"}}, "required": ["date", "why"], "additionalProperties": False}}},
    "required": ["periods", "breaks"], "additionalProperties": False}
PERIOD_PROMPT = """Below are the milestones in the messages of {user}, from {first} to {last}{homes}.
1. Divide that time into periods: stretches when something important held (where they lived, who was on the team or
   in which role, which policy, price or schedule was in force, which phase a project was in). Periods about
   different things may overlap. Give each a start and an end (YYYY-MM-DD) and a short label saying what held.
2. Pick at most 2 break dates where the daily routine most likely changed (a move, a new job, a team change, a new
   schedule), each with the reason.
Use only what the milestones say.

{milestones}"""


def period_model(ctx) -> dict:
    """Life or work periods and routine breaks from the dated milestones (Jev aspect), one LLM call, saved."""
    labels = label_messages(ctx)
    ms = [(day_of(ts), str(t)[:240]) for i, ts, t in ctx.log if has(labels, i, "milestone", threshold=0.5)]
    homes = residence_periods(ctx) if ctx.user else []
    homes = [p for p in homes if p["place"] != "unknown"]
    first, last = ctx.log[0][1][:10], ctx.now.strftime("%Y-%m-%d")

    def make():
        note = ("; where the user lived: " + ", ".join(f"{p['place']} {p['start']} to {p['end']}" for p in homes)) if len(homes) > 1 else ""
        got, _ = chat_json(ctx.model, PERIOD_PROMPT.format(user=ctx.user, first=first, last=last, homes=note,
                           milestones="\n".join(f"{d}: {t}" for d, t in ms) or "(none)"), PERIOD_SCHEMA,
                           schema_name="periods", tag="memory:periods", effort="low", max_tokens=3000)
        return got
    model = ctx.saved("period_model", make)
    model["homes"] = homes
    return model


def routine_segments(ctx) -> list[dict]:
    """All time, plus the stretches between the routine breaks, each labelled by the period that starts closest."""
    model = period_model(ctx)
    first, last = ctx.log[0][1][:10], ctx.now.strftime("%Y-%m-%d")
    breaks = sorted({b["date"][:10] for b in model["breaks"] if first < b["date"][:10] <= last})[:2]
    if len(model["homes"]) > 1:   # the user moved: that is the break that matters for a person's routines
        breaks = sorted({p["start"] for p in model["homes"][1:]} | set(breaks[:1]))[:2]
    edges = [first] + breaks + [str(date.fromisoformat(last) + timedelta(days=1))]
    segs = [{"start": first, "end": last, "label": "all time"}]

    def label(start):
        cands = [p for p in model["periods"] if p["start"][:10] <= start <= p["end"][:10]] + \
                [{"label": f"living in {h['place']}", "start": h["start"]} for h in model["homes"] if h["start"] <= start <= h["end"]]
        return max(cands, key=lambda p: p["start"][:10])["label"] if cands else ""
    for a, b in zip(edges, edges[1:]):
        if breaks:
            segs.append({"start": a, "end": str(date.fromisoformat(b) - timedelta(days=1)), "label": label(a)})
    return segs


# ------------------------------------------------------------------------------------------------ routines
UNIT = re.compile(r"currency|unit|status|payment|method|card|format")


def amount(col: str, v: float) -> str:
    """Durations in seconds read as h:mm:ss or m:ss; everything else as a plain number."""
    if re.search("second", col):
        v = int(round(v))
        return f"{v // 3600}:{v % 3600 // 60:02d}:{v % 60:02d}" if v >= 3600 else f"{v // 60}:{v % 60:02d}"
    return f"{v:g}"


class RoutineStore(Store):
    name = "routines"
    description = ("habits and routines: what usually happens, on which days and at what times, where, with whom, "
                   "typical amounts or durations, how often, per period")
    min_rows = 8

    def build(self, ctx):
        r, db = ctx.reader, ctx.db
        log_ts = {i: ts for i, ts, _ in ctx.log}
        segments = routine_segments(ctx)
        self.statements = []
        for t in r.tables:
            m = r.meta[t]
            if m["time"] == "_ts" or m["rows"] < 12:
                continue
            kinds = [d for d, s in m["dims"].items() if s["kind"] == "values" and not s.get("free_text")
                     and 2 <= len(s["values"]) <= 12 and not UNIT.search(d)]
            links = [d for d, s in m["dims"].items() if s["kind"] == "link"]
            others = [d for d, s in m["dims"].items() if s["kind"] == "values" and len(s["values"]) > 12 and not s.get("free_text")]
            nums = [c for c in m["numbers"] if not c.endswith("_id")]
            const = [s["values"][0] for d, s in m["dims"].items() if s["kind"] == "values" and len(s["values"]) == 1]
            rows = [dict(x) for x in db.execute(f"SELECT * FROM '{t}'")]
            name = lambda c, val: r._labels[m["dims"][c]["table"]].get(val, val)
            for k in kinds or [None]:
                for v in (sorted({x[k] for x in rows if x.get(k) is not None}) if k else [None]):
                    for p in segments:
                        rs = [x for x in rows if (k is None or x.get(k) == v) and p["start"] <= str(x.get(m["time"]) or "")[:10] <= p["end"]]
                        if len(rs) < self.min_rows:
                            continue
                        label = f"{t}" + (f" ({', '.join(map(str, const))})" if const else "") + (f" where {k} = {v}" if k else "")
                        when = "over all time" if p["label"] == "all time" else f"during {p['label'] or 'the period'}"
                        self.statements.append(f"Routine — {label}, {when} ({p['start']} to {p['end']}): "
                                               + self.describe(rs, p, m, r, log_ts, nums, others, links))
                        for c in links:   # the same, per person or thing involved, when there are enough rows
                            by = Counter(x.get(c) for x in rs if x.get(c) is not None)
                            for val, n in by.items():
                                if self.min_rows <= n <= 0.9 * len(rs):
                                    sub = [x for x in rs if x.get(c) == val]
                                    self.statements.append(f"Routine — {label}, with {c.replace('_id', '')} {name(c, val)}, {when} "
                                                           f"({p['start']} to {p['end']}): "
                                                           + self.describe(sub, p, m, r, log_ts, nums, others, []))
        self.index = Index(self.statements)
        return {"statements": len(self.statements), "segments": segments}

    @staticmethod
    def describe(rs, p, m, r, log_ts, nums, others, links) -> str:
        weeks = max(1.0, (date.fromisoformat(p["end"]) - date.fromisoformat(p["start"])).days / 7)
        wd = Counter(date.fromisoformat(str(x[m["time"]])[:10]).strftime("%A") for x in rs)
        days = [d for d, n in wd.most_common(3) if n >= 0.15 * len(rs)]
        hours = Counter(int(log_ts[int(i)][11:13]) for x in rs for i in re.findall(r"\d+", str(x.get("_src") or ""))[:1]
                        if int(i) in log_ts)
        bits = [f"{len(rs)} times, about {len(rs) / weeks:.1f} per week",
                "mostly on " + ", ".join(f"{d} ({wd[d] * 100 // len(rs)}%)" for d in days)]
        if hours:
            bits.append("logged around " + " and ".join(f"{hh}:00" for hh, _ in hours.most_common(2)))
        for c in nums:
            vals = [x[c] for x in rs if isinstance(x.get(c), (int, float))]
            if len(vals) >= 5:
                cur = Counter(x.get("currency") for x in rs if x.get("currency")).most_common(1)
                bits.append(f"typical {c.replace('_', ' ')} {amount(c, statistics.median(vals))}"
                            f"{' ' + cur[0][0] if cur and not re.search('second|minute|duration', c) else ''}")
                recent = sorted((x for x in rs if isinstance(x.get(c), (int, float))), key=lambda x: str(x.get(m["time"])))[-5:]
                if re.search("second|minute|duration|time", c):
                    bits.append(f"last 5 {c.replace('_', ' ')}: " + ", ".join(amount(c, x[c]) for x in recent))
        for c in others + links:
            spec = m["dims"][c]
            lab = (lambda val: r._labels[spec["table"]].get(val, val)) if spec["kind"] == "link" else (lambda val: val)
            cnt = Counter(str(lab(x[c])) for x in rs if x.get(c) is not None)
            if cnt and cnt.most_common(1)[0][1] >= 3:
                bits.append(f"most common {c.replace('_id', '')}: " + ", ".join(f"{n} ({c2 * 100 // len(rs)}%)" for n, c2 in cnt.most_common(3)))
        return "; ".join(bits)

    def read(self, question, ctx, k=8):
        return [Evidence("routines", self.statements[i]) for i in self.index.top(question, k)]


# ------------------------------------------------------------------------------------------- consolidation
ASPECTS = {"intention": "Does it ask to be reminded of a to-do, assign or promise something to be done (an action item or "
                        "a commitment), or schedule, book or reschedule something for a later date (an appointment, "
                        "meeting, call, trip or plan; a calendar invite counts)? A report of what someone is doing or did "
                        "today or tonight does not count.",
           "done": "Does it say that a specific to-do, reminder, action item, promise or plan has been done (e.g. 'done: "
                   "renew passport')? An ordinary activity such as a run, a climb, a meal or a routine task does not count.",
           "cancel": "Does it cancel or drop a specific reminder, plan, action item, appointment or membership?",
           "preference": "Does it say what someone likes, loves, dislikes, prefers, wants or is allergic to, or rate or judge "
                         "something (a movie, a book, a place, a trip, a product, a way of working)?",
           "milestone": "Does it report a big event or change: a move, a new job or role, someone joining or leaving, a "
                        "launch, a reorganization, a new policy, price or schedule, a funding round, an engagement, a "
                        "personal record?"}

ERASE_SCHEMA = {"type": "object", "properties": {
    "whole_messages": {"type": "array", "items": {"type": "integer"},
                       "description": "ids of messages that are entirely about what must be forgotten"},
    "strings": {"type": "array", "items": {"type": "string"},
                "description": "exact strings to erase from other messages (names, numbers, emails, codes)"}},
    "required": ["whole_messages", "strings"], "additionalProperties": False}


def erase_forgotten(ctx) -> dict:
    """Forgetting across every store: for each "forget ..." request, the LLM reads the earlier messages that may be
    about it and names what to erase: whole messages ("forget everything about X") or exact strings ("forget X's
    number"). Code erases them from the log, the history and the rows (rows made only from an erased message are
    deleted) before any store is built. Every store derives from the log, so nothing forgotten can come back."""
    reqs = [(i, ts, t) for i, ts, t in ctx.log if re.search(r"\bforget\b|\berase\b", str(t), re.I)]
    tables = [r[0] for r in ctx.db.execute("SELECT name FROM _tables")]
    erased = {"requests": len(reqs), "whole_messages": [], "strings": [], "rows_deleted": [], "values_erased": 0}
    touched = set()
    for i, ts, t in reqs:
        earlier = [(j, ts2, t2) for j, ts2, t2 in ctx.log if ts2 < ts]
        about = [earlier[k] for k in top(str(t), [str(x[2]) for x in earlier], 8)]
        out, _ = chat_json(ctx.model, "The user asked: " + json.dumps(t, ensure_ascii=False) + "\n\nEarlier messages that may "
                           "be about it (id first):\n" + "\n".join(f"{j} [{when(ts2)}] {t2}" for j, ts2, t2 in about)
                           + "\n\nWhat must be erased to honour the request? (1) Whole messages that are entirely about "
                           "it. (2) The exact strings that identify it (names, emails, numbers, codes), to erase from every "
                           "other message. Strings must appear above. \"[forgotten]\" marks text already erased.",
                           ERASE_SCHEMA, schema_name="erase", tag="memory:forget", effort="low", max_tokens=1000)
        ids = {j for j, _, _ in about}
        whole = [j for j in out["whole_messages"] if j in ids]
        texts = {j: str(next(t2 for j2, _, t2 in earlier if j2 == j)) for j in whole}
        # a second opinion before deleting a whole message: is it really about what the request asks to forget?
        sure = ctx.decider.yes_many({"request": str(t), **{f"message_{j}": texts[j] for j in whole}},
                                    {f"m{j}": f"Is `message_{j}` itself about what `request` asks to forget?" for j in whole},
                                    tag="memory:forget") if whole else {}
        erased.setdefault("whole_messages_refused", [])
        gone = []
        for j in whole:
            text_j = texts[j]
            if sure.get(f"m{j}", 0.0) < 0.5:
                erased["whole_messages_refused"].append(j)
                continue
            gone.append((j, text_j))
            ctx.db.execute("UPDATE _log SET text = '[forgotten]' WHERE id = ?", (j,))
            ctx.db.execute("DELETE FROM _history WHERE log_id = ?", (j,))
            for tbl in tables:
                for (rid,) in ctx.db.execute(f"SELECT id FROM '{tbl}' WHERE _src = ?", (f"[{j}]",)).fetchall():
                    ctx.db.execute(f"DELETE FROM '{tbl}' WHERE id = ?", (rid,))
                    ctx.db.execute("DELETE FROM _history WHERE table_name = ? AND row_id = ?", (tbl, rid))
                    erased["rows_deleted"].append(f"{tbl}#{rid}")
            erased["whole_messages"].append(j)
        # rows the messages only changed, and their history, may still carry what they said (a note set, then "cleared")
        erased["values_erased"] += erase_traces(ctx, tables, str(t), gone) if gone else 0
        n_strings = len(erased["strings"])
        strings = [x.strip() for x in out["strings"] if len(x.strip()) >= 4 and "[forgotten]" not in x
                   and x.strip().lower() not in ("forget", "number")]
        # the same second opinion for strings: is it what must go ("Carl Weber", Marco's number), or only context
        # ("Fabrikam" in "forget what I noted about Fabrikam's layoffs", "Rohlik" in "forget everything about Jan")?
        ok = ctx.decider.yes_many({"request": str(t), **{f"string_{k}": x for k, x in enumerate(strings)}},
                                  {f"s{k}": f"Is `string_{k}` itself what `request` asks to forget, or a name, number or "
                                            f"email that identifies it (not just the context it belongs to)?"
                                   for k in range(len(strings))}, tag="memory:forget") if strings else {}
        erased.setdefault("strings_refused", [])
        for k, x in enumerate(strings):
            if ok.get(f"s{k}", 0.0) < 0.5:
                erased["strings_refused"].append(x)
                continue
            variants = sorted({x, re.sub(r"^\+\d+\s*", "", x)} if re.search(r"\d{3}", x) else {x}, key=len, reverse=True)
            for v in variants:
                ctx.db.execute("UPDATE _log SET text = REPLACE(text, ?, '[forgotten]') WHERE text LIKE ?", (v, f"%{v}%"))
                ctx.db.execute("DELETE FROM _history WHERE old_value LIKE ? OR new_value LIKE ?", (f"%{v}%", f"%{v}%"))
                for tbl in tables:
                    cols = [c[1] for c in ctx.db.execute(f"PRAGMA table_info('{tbl}')") if c[1] not in ("id", "_src", "_ts")]
                    for c in cols:
                        hit = ctx.db.execute(f"SELECT id FROM '{tbl}' WHERE CAST(\"{c}\" AS TEXT) LIKE ?", (f"%{v}%",)).fetchall()
                        ctx.db.execute(f"UPDATE '{tbl}' SET \"{c}\" = NULL WHERE CAST(\"{c}\" AS TEXT) LIKE ?", (f"%{v}%",))
                        touched |= {(tbl, r[0]) for r in hit}
            erased["strings"].append(x)
        if gone or len(erased["strings"]) > n_strings:   # the request itself names what it asked to forget
            ctx.db.execute("UPDATE _log SET text = '[a request to forget something]' WHERE id = ?", (i,))
    # a row the erasure left with nothing but links and a category (a phone entry without its number) goes too
    for tbl, rid in sorted(touched):
        cols = [c[1] for c in ctx.db.execute(f"PRAGMA table_info('{tbl}')")
                if c[1] not in ("id", "_src", "_ts", "category", "type", "kind") and not c[1].endswith("_id")]
        row = ctx.db.execute(f"SELECT {', '.join(chr(34) + c + chr(34) for c in cols)} FROM '{tbl}' WHERE id = ?", (rid,)).fetchone()
        if row is not None and all(v is None for v in row):
            ctx.db.execute(f"DELETE FROM '{tbl}' WHERE id = ?", (rid,))
            erased["rows_deleted"].append(f"{tbl}#{rid}")
    erased["values_erased"] += len(touched)
    ctx.db.commit()
    ctx.log = [(r[0], r[1], r[2]) for r in ctx.db.execute("SELECT id, ts, text FROM _log ORDER BY id")]
    return erased


AUDIT_SCHEMA = {"type": "object", "properties": {"erase": {"type": "array", "items": {"type": "string"},
                                                           "description": "keys of the values to erase"}},
                "required": ["erase"], "additionalProperties": False}


def erase_traces(ctx, tables: list[str], request: str, gone: list[str]) -> int:
    """The rows the erased messages touched, and their history, may still carry what they said: the planner sometimes
    "clears" a note by update, and history keeps the old text, often paraphrased. The LLM reads those values and names
    the ones that repeat what must be forgotten; code erases them (values -> NULL, history entries deleted)."""
    ids = [j for j, _ in gone]
    cands = {}
    for tbl in tables:
        cols = [c[1] for c in ctx.db.execute(f"PRAGMA table_info('{tbl}')")]
        for row in ctx.db.execute(f"SELECT * FROM '{tbl}'").fetchall():
            d = dict(zip(cols, row))
            if not set(ids) & {int(x) for x in re.findall(r"\d+", str(d.get("_src") or ""))}:
                continue
            for c, v in d.items():
                if c not in ("id", "_src", "_ts") and isinstance(v, str) and len(v) > 3:
                    cands[f"v{len(cands)}"] = ("value", tbl, d["id"], c, v)
            for hid, col, old, new in ctx.db.execute("SELECT id, column_name, old_value, new_value FROM _history "
                                                     "WHERE table_name = ? AND row_id = ?", (tbl, d["id"])).fetchall():
                cands[f"h{len(cands)}"] = ("history", tbl, hid, col, f"{old} -> {new}")
    if not cands:
        return 0
    out, _ = chat_json(ctx.model, "The user asked: " + json.dumps(request, ensure_ascii=False) + "\nThese messages are being "
                       "erased:\n" + "\n".join(f"- {t}" for _, t in gone) + "\n\nStored values from rows those messages "
                       "touched (key: table.column = value):\n" + "\n".join(f"{k}: {x[1]}.{x[3]} = {str(x[4])[:300]}"
                                                                          for k, x in cands.items())
                       + "\n\nWhich values repeat, even in other words, what must be forgotten? Only those.",
                       AUDIT_SCHEMA, schema_name="audit", tag="memory:forget", effort="low", max_tokens=1000)
    n = 0
    for k in out["erase"]:
        if k in cands:
            kind, tbl, rid, col, _ = cands[k]
            if kind == "value":
                ctx.db.execute(f"UPDATE '{tbl}' SET \"{col}\" = NULL WHERE id = ?", (rid,))
            else:
                ctx.db.execute("DELETE FROM _history WHERE id = ?", (rid,))
            n += 1
    return n


def label_messages(ctx) -> dict[str, dict]:
    """One decider pass over every message, 16 per request: {log id: {aspect: p}}. Multi-label: a birthday message
    can also carry a preference ("She loves orchids"), so every aspect gets its own yes/no."""
    return ctx.saved(f"aspects_{ctx.decider.name}", lambda: label_batches(ctx.decider, ctx.log))


def has(labels: dict, i: int, *aspects: str, threshold: float = 0.5) -> bool:
    return any(labels.get(str(i), {}).get(a, 0.0) >= threshold for a in aspects)


def label_batches(decider, msgs: list[tuple], size: int = 16) -> dict[str, dict]:
    """Aspects of (id, ts, text) messages, `size` messages per request, requests in parallel."""
    batches = [msgs[j:j + size] for j in range(0, len(msgs), size)]
    jobs = [({f"message_{n}": f"[{when(ts)}] {str(t)[:400]}" for n, (_, ts, t) in enumerate(b)},
             {f"{a}_{n}": f"`message_{n}`: {d}" for n in range(len(b)) for a, d in ASPECTS.items()}) for b in batches]
    out = {}
    for b, got in zip(batches, decider.batch(jobs, "yes_many", tag=f"memory:aspects:{decider.name}")):
        for n, (i, _, _) in enumerate(b):
            out[str(i)] = {a: round(got.get(f"{a}_{n}", 0.0), 3) for a in ASPECTS}
    return out


# ---------------------------------------------------------------------------------------------- intentions
INTENT_SCHEMA = {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {
    "id": {"type": "integer"},
    "kind": {"type": "string", "enum": ["reminder", "commitment", "appointment", "plan", "done", "cancel", "none"]},
    "title": {"type": "string", "description": "short: what is to be done or what the appointment is"},
    "owner": {"type": "string", "description": "who is to do it, if not the user; empty otherwise"},
    "when": {"type": "string", "description": "due date or start, YYYY-MM-DD or YYYY-MM-DDTHH:MM; empty if none"},
    "where": {"type": "string"}},
    "required": ["id", "kind", "title", "owner", "when", "where"], "additionalProperties": False}}},
    "required": ["items"], "additionalProperties": False}


STOP = {"the", "a", "an", "to", "my", "and", "with", "for", "of", "on", "at", "in", "by", "reminder", "remind", "me",
        "done", "cancel", "cancelled", "this", "that", "not", "needed", "anymore", "ok", "so", "btw", "fyi", "note", "lol",
        "it", "will", "do", "never", "mind", "nvm", "i"}


class IntentionStore(Store):
    name = "intentions"
    description = ("intentions and commitments: reminders, to-dos, promises and action items with owners, due dates and "
                   "whether they were done or cancelled; appointments, meetings and calls with their times; upcoming plans")
    gate = 0.15   # Jev is the cheap, high-recall gate (97% of intentions at 0.15, LLP 0013.000); the LLM extracts

    def build(self, ctx):
        labels = label_messages(ctx)
        cands = [(i, ts, t) for i, ts, t in ctx.log if has(labels, i, "intention", "done", "cancel", threshold=self.gate)]

        def extract(chunk):
            block = "\n".join(f"{i} [{when(ts)}] {str(t)[:400]}" for i, ts, t in chunk)
            out, _ = chat_json(ctx.model, "For each message (id first), extract the intention it states: a reminder or "
                               "to-do, a commitment (a promise made to someone, or an action item someone owns), an "
                               "appointment, meeting or call, or a plan, with its date resolved against the message time. "
                               "Use 'done' or 'cancel' when it closes an earlier one. Use 'none' when the message only "
                               "reports something someone is doing or did (a climb or dinner 'tonight'), or asks the "
                               "assistant for something.\n\n" + block,
                               INTENT_SCHEMA, schema_name="intentions", tag="memory:intentions", effort="low", max_tokens=4000)
            return out["items"]

        def make():
            return [x for got in parallel(extract, [cands[j:j + 12] for j in range(0, len(cands), 12)]) for x in got]
        items = sorted(ctx.saved(f"intentions_{ctx.decider.name}", make), key=lambda x: x["id"])
        ts_of = {i: ts for i, ts, _ in ctx.log}
        text_of = {i: str(t) for i, _, t in ctx.log}
        open_items = []
        content = lambda text: {w for w in re.findall(r"\w+", fold(text)) if len(w) >= 3} - STOP
        for it in items:
            it["said"] = ts_of.get(it["id"], "")[:10]
            if it["kind"] in ("reminder", "commitment", "appointment", "plan"):
                it["status"] = "open"
                words = content(it["title"])
                moved = re.search(r"\b(moved|reschedul|postpon|pushed)", text_of.get(it["id"], ""), re.I)
                for o in open_items:   # a later appointment moves an earlier one, or follows up an earlier plan
                    if o["status"] == "open" and o["kind"] in ("appointment", "plan") and len(words & content(o["title"])) >= 2:
                        if moved and o["kind"] == "appointment":
                            o["status"] = f"moved to {it['when']}"
                        elif o["kind"] == "plan" and it["kind"] == "appointment":
                            o["status"] = f"followed by: {it['title']} ({it['when']})"
                open_items.append(it)
            elif it["kind"] in ("done", "cancel"):   # close the latest earlier item sharing the most content words
                a = labels.get(str(it["id"]), {})     # done or cancelled: the decider's calibrated call, not the extractor's
                if a:
                    it["kind"] = "done" if a.get("done", 0) >= a.get("cancel", 0) else "cancel"
                words = content(text_of.get(it["id"], ""))   # the message's own words: an extracted title can be invented
                cands = [(len(words & content(o["title"])), o["id"], o) for o in open_items
                         if o["status"] == "open" and o["id"] < it["id"]]
                best = max(cands, key=lambda c: c[:2], default=(0, 0, None))
                if best[0] >= 1:
                    best[2]["status"] = ("done" if it["kind"] == "done" else "cancelled") + f" on {it['said']}"
        now = ctx.now.isoformat()
        self.statements = []
        for it in open_items:
            due = it["when"]
            state = it["status"]
            if state == "open":   # nothing closed it: as far as the memory knows, it has not been done
                future = not due or due >= now[:len(due)]
                if it["kind"] in ("reminder", "commitment"):
                    state = "open, not done yet" + ("" if future else ", past due")
                else:   # appointments and dated plans simply pass
                    state = "upcoming" if due and future else "past" if due else "open"
            elif state.startswith("cancelled"):
                state += " (dropped, not done)"
            owner = f" (owner: {it['owner']})" if it.get("owner") and it["owner"].lower() not in ("i", "me", "user", ctx.user.lower()) else ""
            self.statements.append(f"{it['kind'].capitalize()}: {it['title']}{owner}" + (f" — {due}" if due else "")
                                   + (f" at {it['where']}" if it.get("where") else "") + f" (said {it['said']}); status: {state}")
        self.index = Index(self.statements)
        return {"items": len(items), "statements": len(self.statements)}

    def read(self, question, ctx, k=10):
        live = [i for i, s in enumerate(self.statements) if "status: open" in s or "upcoming" in s]
        wants_live = re.search(r"\b(next|upcoming|open|still|due|overdue|remind|to do|todo|plan|schedule|coming)", question, re.I)
        picked = list(dict.fromkeys((live if wants_live else []) + self.index.top(question, k)))
        return [Evidence("intentions", self.statements[i]) for i in picked]


# --------------------------------------------------------------------------------------------- preferences
PREF_SCHEMA = {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {
    "id": {"type": "integer"}, "holder": {"type": "string"},
    "stance": {"type": "string", "enum": ["loves", "likes", "dislikes", "allergic to", "rated", "felt", "none"]},
    "target": {"type": "string"},
    "target_kind": {"type": "string", "enum": ["book", "movie", "food", "place", "trip", "activity", "person", "other"]},
    "detail": {"type": "string", "description": "e.g. the rating, or what exactly"}},
    "required": ["id", "holder", "stance", "target", "target_kind", "detail"], "additionalProperties": False}}},
    "required": ["items"], "additionalProperties": False}


class PreferenceStore(Store):
    name = "preferences"
    description = ("preferences and evaluations: what the user and people they know like, love, dislike or are "
                   "allergic to; ratings and opinions of books, movies, places, trips")
    gate = 0.5

    def build(self, ctx):
        labels = label_messages(ctx)
        cands = [(i, ts, t) for i, ts, t in ctx.log if has(labels, i, "preference", threshold=self.gate)]

        def extract(chunk):
            block = "\n".join(f"{i} [{when(ts)}] {str(t)[:300]}" for i, ts, t in chunk)
            out, _ = chat_json(ctx.model, "For each message (id first), extract the preference or evaluation it states. "
                               "The holder is whoever has it: the user is 'I', but it can be someone they mention ('Mom "
                               "loves orchids' has holder Mom). Use stance 'none' if there is none; thanking or praising "
                               "the assistant is none.\n\n" + block, PREF_SCHEMA,
                               schema_name="preferences", tag="memory:preferences", effort="low", max_tokens=4000)
            return out["items"]

        def make():
            return [x for got in parallel(extract, [cands[j:j + 15] for j in range(0, len(cands), 15)]) for x in got]
        ts_of = {i: ts for i, ts, _ in ctx.log}
        kind = lambda p: "" if p["target_kind"] in ("other", "person") else f"the {p['target_kind']} "
        self.statements = [f"{p['holder']} {p['stance']} {kind(p)}{p['target']}" + (f" ({p['detail']})" if p["detail"] else "")
                           + f" — said {ts_of.get(p['id'], '')[:10]}"
                           for p in ctx.saved(f"preferences_{ctx.decider.name}", make) if p["stance"] != "none"]
        self.index = Index(self.statements)
        return {"candidates": len(cands), "statements": len(self.statements)}

    def read(self, question, ctx, k=12):
        return [Evidence("preferences", self.statements[i]) for i in self.index.top(question, k)]


# ------------------------------------------------------------------------------------------------- periods
MONTH_SCHEMA = {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"],
                "additionalProperties": False}
MONTH_PROMPT = """Below are day-by-day gists of one person's {month}. Summarize the month in 3-5 sentences: the notable
events with their dates, what was ongoing (where they lived, what they were reading or working on, habits, plans),
and what changed. Skip routine purchases unless something about them changed. Keep names, numbers and dates exact.

{days}"""


def day_gists(ctx) -> dict:
    path = ctx.cache_dir / "episodes.json"
    if not path.exists():
        EpisodeStore().build(ctx)
    return json.loads(path.read_text())


class PeriodStore(Store):
    name = "periods"
    description = ("periods and milestones: what held when (where the user lived, who was on the team, which policy or "
                   "phase was in force), month-by-month summaries of what was going on, and the big events, with dates")
    gate = 0.5

    def build(self, ctx):
        labels = label_messages(ctx)
        model = period_model(ctx)
        self.periods = model["periods"]
        self.milestones = [f"Milestone {day_of(ts)}: {str(t)[:220]}" for i, ts, t in ctx.log
                           if has(labels, i, "milestone", threshold=self.gate)]
        self.statements = [f"Period: lived in {p['place']}" + (f" ({p['home']})" if p.get("home") else "")
                           + f" from {p['start']} to {p['end']}" for p in model["homes"]] + \
                          [f"Period: {p['label']}, from {p['start']} to {p['end']}" for p in self.periods]
        gists = day_gists(ctx)
        months = sorted({d[:7] for d in gists})

        def summarize(month):
            days = "\n".join(f"{d}: {gists[d]['gist']}" for d in sorted(gists) if d.startswith(month))
            got, _ = chat_json(ctx.model, MONTH_PROMPT.format(month=month, days=days), MONTH_SCHEMA, schema_name="month",
                               tag="memory:months", effort="low", max_tokens=1500)
            return month, got["summary"]
        self.months = dict(ctx.saved("months", lambda: dict(parallel(summarize, months))))
        self.month_keys = sorted(self.months)
        self.month_index = Index([f"{m} {date.fromisoformat(m + '-01'):%B %Y} {self.months[m]}" for m in self.month_keys])
        self.index = Index(self.milestones)
        return {"periods": len(self.statements), "milestones": len(self.milestones), "months": len(self.months)}

    def read(self, question, ctx, k=8):
        return [Evidence("periods", s) for s in self.statements] + \
               [Evidence("periods", self.milestones[i]) for i in self.index.top(question, k)] + \
               [Evidence("periods", f"Month {self.month_keys[i]}: {self.months[self.month_keys[i]]}")
                for i in sorted(self.month_index.top(question, 3))]


# ------------------------------------------------------------------------------------------ a product's memory
class ProductMemoryStore(Store):
    """Another system's memory, read as evidence, so the same answerer can compare it (LLP 0017). It reads an export
    with a profile, memories and episode notes, plus legacy topics, facts and session notes, from LAB_PRODUCT_MEMORY.
    LAB_PRODUCT_CUTOFF drops everything created on or after that date: the memory as it stood then."""
    name = "product"
    description = "the product's own memory of this person: profile, saved memories, session notes and episode notes"

    def load(self):
        import os
        p = json.loads(open(os.environ["LAB_PRODUCT_MEMORY"]).read())["product"]
        cut = os.environ.get("LAB_PRODUCT_CUTOFF", "9999")
        before = lambda x, *keys: str(next((x.get(k) for k in keys if x.get(k)), ""))[:10] < cut
        val = lambda v: v if not isinstance(v, dict) else json.dumps(v, ensure_ascii=False)
        prof = p.get("profile") or {}
        blocks = (prof.get("blocks") or {}) if before(prof, "created_at", "updated_at") else {}
        self.profile = [f"Profile, {k}: {val(v)}" for k, v in (blocks.items() if isinstance(blocks, dict) else [])]
        mems = [m for m in p.get("memories", []) if m.get("status", "active") == "active" and before(m, "created_at")]
        mems.sort(key=lambda m: (-(m.get("importance") or 0), str(m.get("observed_at") or m.get("created_at"))))
        self.memories = [f"Memory ({m.get('kind', '')}, {str(m.get('observed_at') or m.get('created_at'))[:10]}): "
                         f"{m.get('text') or m.get('content') or ''}" for m in mems]
        eps = sorted((e for e in p.get("episodes", []) if before(e, "started_at", "last_at")), key=lambda e: str(e.get("last_at")))
        self.episodes = [f"Episode note ({str(e.get('last_at'))[:10]}): {e.get('note')}" for e in eps]
        self.topics = [f"Topic {h.get('topic') or h.get('topic_id')}: {h.get('answer')}"
                       for h in p.get("legacy_holistic", []) if before(h, "created_at")]
        self.flat = [f"Saved note: {f.get('content')}" for f in p.get("legacy_flat", []) if before(f, "created_at")]
        sess = sorted((x for x in p.get("legacy_sessions", []) if before(x, "created_at")), key=lambda x: str(x.get("created_at")))
        self.notes = [f"Session note {str(x.get('created_at'))[:10]}: {x.get('notes') or ''}" for x in sess]
        self.sessions = [f"Session {str(x.get('created_at'))[:10]} {x.get('title') or ''} (mood {x.get('mood_before')} -> "
                         f"{x.get('mood_after')}): {x.get('notes') or x.get('summary') or ''} Insights: "
                         f"{val(x.get('key_insights'))} Actions: {val(x.get('action_steps'))}" for x in sess]

    def build(self, ctx):
        self.load()
        fixed = self.profile + self.memories + self.episodes + self.topics + self.flat
        self.fixed = [x for x in fixed if x and not x.endswith(": ") and "None" not in x[-6:]]
        self.index = Index(self.sessions)
        return {"records": len(self.fixed), "session_notes": len(self.sessions)}

    def read(self, question, ctx, k=8):
        picked = sorted(set(self.index.top(question, k)) | set(range(max(0, len(self.sessions) - 5), len(self.sessions))))
        return [Evidence("product", x[:600]) for x in self.fixed] + [Evidence("product", self.sessions[i][:700]) for i in picked]


# @ref LLP 0017#part-b-coming-back — what the product's reader puts in the prompt, not all it stores
class ProductInjectedStore(ProductMemoryStore):
    """What the product's prompt builder injects at the start of a chat (as of 2026-09): with a profile, its blocks, up
    to 40 memories (4,000 chars) by importance then recency, and the last 3 episode notes; with none, the older memory:
    20 topics, 100 saved notes and the last 10 session notes."""
    name = "product_injected"
    description = "what the product's own memory puts in the assistant's prompt at the start of a conversation"

    def build(self, ctx):
        self.load()
        if self.profile:
            mems, size = [], 0
            for m in self.memories[:40]:
                if size + len(m) > 4000:
                    break
                mems.append(m)
                size += len(m)
            self.fixed = self.profile + mems + self.episodes[-3:]
        else:
            self.fixed = self.topics[:20] + self.flat[:100] + self.notes[-10:]
        return {"records": len(self.fixed), "mode": "profile" if self.profile else "legacy"}

    def read(self, question, ctx, k=8):
        return [Evidence("product", x[:1200]) for x in self.fixed]


# ------------------------------------------------------------------------------------ memory for conversation
# @ref LLP 0019#the-new-parts — meaning search, a statement store and a dossier: the parts rounds 10-11 pointed at
class SemanticIndex:
    """Cosine over OpenAI embeddings (lab.common.llm.embed): finds paraphrases and other languages that BM25 misses."""

    def __init__(self, docs: list[str], tag: str = "memory:embed"):
        import numpy as np
        from lab.common.llm import embed
        self.embed, self.tag = embed, tag
        self.m = np.stack(embed(docs, tag=tag)) if docs else np.zeros((0, 1536), dtype=np.float32)

    def scores(self, query: str):
        return self.m @ self.embed([query], tag=self.tag)[0] if len(self.m) else self.m[:, 0]

    def top(self, query: str, k: int) -> list[int]:
        import numpy as np
        return [int(i) for i in np.argsort(-self.scores(query))[:k]] if len(self.m) else []


def fuse(rankings: list[list[int]], k: int, c: int = 60) -> list[int]:
    """Reciprocal rank fusion: a document high in either ranking comes first."""
    score: dict[int, float] = defaultdict(float)
    for ranking in rankings:
        for r, i in enumerate(ranking):
            score[i] += 1 / (c + r + 1)
    return [i for i, _ in sorted(score.items(), key=lambda x: -x[1])[:k]]


class LogEmbedStore(LogStore):
    name = "log_embed"
    description = "the user's raw messages, verbatim, found by meaning (any wording or language), with their times"

    def build(self, ctx):
        stats = super().build(ctx)
        self.semantic = SemanticIndex(self.texts)
        return stats

    def ranked(self, question, k):
        return self.semantic.top(question, k)

    def read(self, question, ctx, k=12):
        days = set(days_named(question))
        on_day = [i for i, (_, ts, _) in enumerate(ctx.log) if day_of(ts) in days][:16]
        picked = list(dict.fromkeys(on_day + self.ranked(question, k)))
        return [Evidence(self.name, self.texts[i], ctx.log[i][1], [ctx.log[i][0]]) for i in sorted(picked)]


class LogHybridStore(LogEmbedStore):
    name = "log_hybrid"
    description = "the user's raw messages, verbatim, found by their words and by their meaning, with their times"

    def ranked(self, question, k):
        return fuse([self.index.top(question, 30), self.semantic.top(question, 30)], k)


STATEMENT_KINDS = ["people", "work", "life", "health", "struggle", "practice", "plan", "preference", "event"]
STATEMENT_PROMPT = """These are one person's messages to their therapy assistant from a conversation on {date}. ("Mind \
asked" is the assistant's question they are answering.)

What would a good therapist remember about this person from them? Write each lasting thing as its own short line, in \
the third person ("They ..."): who is in their life and how things stand with them, their work or studies, where and \
how they live, their health, what they struggle with again and again, what they tried and how it went, what they plan \
or intend, and how they like to be talked to. Leave out small talk and how they feel at just this moment.

MESSAGES:
{text}"""
STATEMENT_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["items"], "properties": {
    "items": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["text", "kind"],
              "properties": {"text": {"type": "string"}, "kind": {"type": "string", "enum": STATEMENT_KINDS}}}}}}


class StatementStore(Store):
    """Lasting statements about the person, one fact each, merged across conversations: how often each was said, and
    when first and last. The unit of memory that conversation carries (LLP 0017.000, LLP 0019)."""
    name = "statements"
    description = ("lasting statements about the person from all their conversations: people, work, life, health, "
                   "recurring struggles, practices tried, plans and preferences; how often and when each was said")

    def build(self, ctx, merge_at: float = 0.88):
        def extract():
            def one(row):
                log_id, ts, text = row
                out, _ = chat_json(ctx.model, STATEMENT_PROMPT.format(date=ts[:10], text=text[:12000]), STATEMENT_SCHEMA,
                                   schema_name="statements", tag="memory:statements", effort="low")
                return [{"text": x["text"], "kind": x["kind"], "ts": ts, "src": log_id} for x in out["items"]]
            with ThreadPoolExecutor(8) as pool:
                items = [x for part in pool.map(one, ctx.log) for x in part]
            vecs = SemanticIndex([x["text"] for x in items]).m if items else []
            merged, cents = [], []
            import numpy as np
            for x, v in sorted(zip(items, vecs), key=lambda p: p[0]["ts"]):
                best = int(np.argmax(np.stack(cents) @ v)) if cents else -1
                if best >= 0 and float(cents[best] @ v) >= merge_at:
                    m = merged[best]
                    m.update(text=x["text"], count=m["count"] + 1, last=x["ts"][:10], src=m["src"] + [x["src"]])
                    c = cents[best] * (m["count"] - 1) + v
                    cents[best] = c / (np.linalg.norm(c) or 1.0)
                else:
                    merged.append({"text": x["text"], "kind": x["kind"], "count": 1, "first": x["ts"][:10],
                                   "last": x["ts"][:10], "src": [x["src"]]})
                    cents.append(v)
            return merged
        self.items = ctx.saved("statements", extract)
        self.index = SemanticIndex([x["text"] for x in self.items])
        return {"statements": len(self.items), "repeated": sum(1 for x in self.items if x["count"] > 1)}

    def read(self, question, ctx, k=25):
        out = []
        for i in self.index.top(question, k):
            x = self.items[i]
            said = f"said {x['count']} times, first {x['first']}, last {x['last']}" if x["count"] > 1 else f"said {x['first']}"
            out.append(Evidence(self.name, f"{x['text']} ({x['kind']}; {said})", x["last"], x["src"]))
        return out


DOSSIER_PROMPT = """Below is everything a person has said to their therapy assistant, in order, with dates. ("Mind \
asked" is the assistant's question they answer.) Now is {now}.

Write the assistant's dossier on this person, to be read before every future conversation so the assistant never has \
to ask again for something the person already said. Use these sections:
- People in their life, and how things stand with each
- Work and studies
- Life and circumstances (where and how they live, routines)
- Health
- What keeps coming back: recurring struggles, triggers, patterns
- Practices and techniques they tried, and how each went
- Plans and intentions, each with its status (open, done, dropped) and date
- How they like to be talked to
- Key events, with dates

Every item is specific (names, numbers, places, the person's own terms), with the date it was said, and how many \
times it came up if more than once. When something changed, say what it was until when, and what it is now. Leave \
out passing moods. Prefer many specific items to general summaries; up to about 2,500 words.

EVERYTHING THEY SAID:
{history}"""


class DossierStore(Store):
    """One full read of the history, written once as a dossier and read whole with every question: the "nightly
    full read" between reading everything each time and searching (LLP 0019)."""
    name = "dossier"
    description = "a dossier on the person written from one full read of every conversation: people, work, life, " \
                  "health, recurring struggles, practices, plans with status, preferences and key events, with dates"

    def build(self, ctx):
        def write():
            from lab.common.llm import chat
            history = "\n\n".join(f"[{when(ts)}] {text}" for _, ts, text in ctx.log)
            text, _ = chat(ctx.model, [{"role": "user", "content": DOSSIER_PROMPT.format(
                now=ctx.now.strftime("%Y-%m-%d"), history=history)}], tag="memory:dossier", effort="low", max_tokens=16000)
            return {"text": str(text).strip()}
        self.text = ctx.saved("dossier", write)["text"]
        return {"dossier_chars": len(self.text)}

    def read(self, question, ctx, k=0):
        return [Evidence(self.name, self.text)]


# ------------------------------------------------------------------------------ LLP 0020: creation and search, done properly
MERGE_PROMPT = """A new statement about a person, and earlier statements about them. Is the new statement the same \
fact as one of the earlier ones (possibly in other words), a change of one of them (the same thing, but different \
now), or something new?

NEW: {new}

EARLIER:
{earlier}"""
MERGE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["relation", "n"], "properties": {
    "relation": {"type": "string", "enum": ["same", "change", "new"]},
    "n": {"type": "integer", "description": "the number of the earlier statement it matches or changes; 0 if new"}}}


# @ref LLP 0020#part-b-creation — cosine alone merged 0.8% of paraphrases (LLP 0019.000); the model decides instead
class MergedStatementStore(StatementStore):
    """Statements merged across conversations: the model decides, for each statement and its closest earlier ones,
    whether it is the same fact (counted), a change (the old one is kept with "until"), or new. The decisions are
    independent, so they run in parallel; code then joins the "same" chains."""
    name = "statements_merged"
    description = ("lasting statements about the person, merged across conversations: how often each was said, when "
                   "first and last, and what changed")

    def build(self, ctx, candidate_at: float = 0.55, top_n: int = 5):
        import numpy as np
        base = StatementStore()
        base.build(ctx)
        items = base.items

        def consolidate():
            m = base.index.m
            order = sorted(range(len(items)), key=lambda i: (items[i]["first"], i))
            jobs = []
            for r, i in enumerate(order[1:], 1):
                earlier = order[:r]
                sims = m[earlier] @ m[i]
                best = [earlier[k] for k in np.argsort(-sims)[:top_n] if sims[k] >= candidate_at]
                if best:
                    jobs.append((i, best))

            def decide(job):
                i, cands = job
                out, _ = chat_json(ctx.model, MERGE_PROMPT.format(new=items[i]["text"], earlier="\n".join(
                    f"{n + 1}. {items[j]['text']}" for n, j in enumerate(cands))), MERGE_SCHEMA, schema_name="merge",
                    tag="memory:merge", effort="low")
                n = int(out.get("n") or 0)
                ok = out["relation"] in ("same", "change") and 1 <= n <= len(cands)
                return i, out["relation"] if ok else "new", cands[n - 1] if ok else None

            with ThreadPoolExecutor(8) as pool:
                decisions = list(pool.map(decide, jobs))
            parent = list(range(len(items)))

            def find(x):
                while parent[x] != x:
                    parent[x] = parent[parent[x]]
                    x = parent[x]
                return x

            for i, rel, j in decisions:
                if rel == "same":
                    parent[find(i)] = find(j)
            groups = defaultdict(list)
            for i in range(len(items)):
                groups[find(i)].append(i)
            out, where = [], {}
            for members in groups.values():
                members.sort(key=lambda i: (items[i]["first"], i))
                last = members[-1]
                out.append({"text": items[last]["text"], "kind": items[last]["kind"],
                            "count": sum(items[i]["count"] for i in members),
                            "first": min(items[i]["first"] for i in members), "last": max(items[i]["last"] for i in members),
                            "src": sorted({x for i in members for x in items[i]["src"]}), "was": [], "until": "",
                            "members": members})
                for i in members:
                    where[i] = len(out) - 1
            for i, rel, j in decisions:
                if rel == "change" and where[i] != where[j]:
                    out[where[i]]["was"].append(f"{out[where[j]]['text']} (until {items[i]['first']})")
                    out[where[j]]["until"] = items[i]["first"]
            return out

        self.consolidate = consolidate
        self.items = ctx.saved("statements_merged", consolidate)
        self.index = SemanticIndex([x["text"] for x in self.items])
        raw = sum(x["count"] for x in items)
        return {"items": len(self.items), "raw": raw, "repeated_items": sum(1 for x in self.items if x["count"] > 1),
                "raw_in_repeated": sum(x["count"] for x in self.items if x["count"] > 1),
                "changes": sum(len(x["was"]) for x in self.items)}

    def read(self, question, ctx, k=25):
        return [self.evidence(i) for i in self.index.top(question, k)]

    def evidence(self, i):
        x = self.items[i]
        said = f"said {x['count']} times, first {x['first']}, last {x['last']}" if x["count"] > 1 else f"said {x['first']}"
        extra = (f"; earlier: {'; '.join(x['was'])}" if x["was"] else "") + (f"; changed on {x['until']}" if x["until"] else "")
        return Evidence(self.name, f"{x['text']} ({x['kind']}; {said}{extra})", x["last"], x["src"])


def rerank(query: str, texts: list[str], limit: int = 800, tag: str = "memory:rerank") -> list[int]:
    """Indices of `texts`, most useful to recall first, by one closed question per candidate: Jev where allowed, the
    OpenAI shim for data that may not go to TypeSafe (LLP 0018#processors). Ties keep the given order."""
    from lab.common import jev
    if not texts:
        return []
    state = {"moment": query, "excerpts": {f"e{n}": t[:limit] for n, t in enumerate(texts)}}
    qs = {f"r{n}": jev.noul(f"Does `excerpts.e{n}` hold something the person said that is useful to recall at `moment`?")
          for n in range(len(texts))}
    ans = jev.ask(state, qs, tag=tag)
    p = {n: ans.get(f"r{n}", {}).get("noul", 0.0) for n in range(len(texts))}
    return sorted(range(len(texts)), key=lambda n: (-p[n], n))


class RerankedStatementStore(MergedStatementStore):
    name = "statements_rr"

    def read(self, question, ctx, k=15):
        cand = self.index.top(question, 40)
        return [self.evidence(cand[n]) for n in rerank(question, [self.items[i]["text"] for i in cand])[:k]]


class RerankedLogStore(LogEmbedStore):
    name = "log_rr"
    description = "the user's raw messages, verbatim, found by meaning and reranked, with their times"

    def ranked(self, question, k):
        cand = self.semantic.top(question, 20)
        return [cand[n] for n in rerank(question, [self.texts[i] for i in cand])[:k]]

    def read(self, question, ctx, k=6):
        return super().read(question, ctx, k)


# @ref LLP 0021#part-b-keeping-every-wording — one wording per fact lost 3.3 points; keep them all and link repeats
class LinkedStatementStore(MergedStatementStore):
    """Every statement stays its own item, in its own words. Each carries its group's count, dates and changes, from
    the model-confirmed merge."""
    name = "statements_linked"
    description = ("lasting statements about the person in their own words, each with how many times the same fact "
                   "was said, when first and last, and what changed")

    def build(self, ctx, **kw):
        super().build(ctx, **kw)
        base = StatementStore()
        base.build(ctx)
        groups = ctx.saved("statement_groups", self.consolidate)
        self.groups = groups
        self.group_of = {i: g for g, x in enumerate(groups) for i in x["members"]}
        self.raw = base.items
        self.index = base.index
        return {"statements": len(self.raw), "groups": len(groups)}

    def evidence_raw(self, i):
        x, g = self.raw[i], self.groups[self.group_of.get(i, 0)] if self.groups else None
        if g and g["count"] > 1:
            said = f"the same fact said {g['count']} times, first {g['first']}, last {g['last']}"
        else:
            said = f"said {x['first']}"
        extra = (f"; earlier: {'; '.join(g['was'])}" if g and g["was"] else "") + \
                (f"; changed on {g['until']}" if g and g["until"] else "")
        return Evidence(self.name, f"{x['text']} ({x['kind']}; {said}{extra})", x["last"], x["src"])

    def read(self, question, ctx, k=25):
        return [self.evidence_raw(i) for i in self.index.top(question, k)]


PICK_LIST = """Below are {n} excerpts from a person's memory, and a moment in their current conversation with their \
therapy assistant (or a question about them). Rank the excerpts by how useful each is to recall at this moment, and \
return the numbers of the {k} most useful, the most useful first.

MOMENT OR QUESTION: {q}

EXCERPTS:
{items}"""
PICK_LIST_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["top"], "properties": {
    "top": {"type": "array", "items": {"type": "integer"}}}}


def pick_list(query: str, texts: list[str], k: int, limit: int = 3000, tag: str = "memory:picklist") -> list[int]:
    """The LLM ranks the candidates as a list (LLP 0021 P3, the best picker of Part A); returns up to k indices."""
    if not texts:
        return []
    out, _ = chat_json("openai:gpt-6-luna", PICK_LIST.format(n=len(texts), k=k, q=query, items="\n\n".join(
        f"{n + 1}. {t[:limit]}" for n, t in enumerate(texts))), PICK_LIST_SCHEMA, schema_name="top", tag=tag, effort="low")
    order = list(dict.fromkeys(n - 1 for n in out["top"] if 1 <= n <= len(texts)))
    return (order + [n for n in range(len(texts)) if n not in order])[:k]


class LinkedStatementsListPicked(LinkedStatementStore):
    name = "statements_linked_p3"

    def read(self, question, ctx, k=15):
        cand = self.index.top(question, 40)
        return [self.evidence_raw(cand[n]) for n in pick_list(question, [self.raw[i]["text"] for i in cand], k)]


class LinkedStatementsJevPicked(LinkedStatementStore):
    name = "statements_linked_rr"

    def read(self, question, ctx, k=15):
        cand = self.index.top(question, 40)
        return [self.evidence_raw(cand[n]) for n in rerank(question, [self.raw[i]["text"] for i in cand])[:k]]


class LogListPicked(LogEmbedStore):
    name = "log_p3"
    description = "the user's raw messages, verbatim, found by meaning and ranked as a list by the model"

    def ranked(self, question, k):
        cand = self.semantic.top(question, 20)
        return [cand[n] for n in pick_list(question, [self.texts[i] for i in cand], k)]

    def read(self, question, ctx, k=6):
        return super().read(question, ctx, k)


class LogJevPickedFull(LogEmbedStore):
    name = "log_rr_full"
    description = "the user's raw messages, verbatim, found by meaning and picked by closed questions on whole windows"

    def ranked(self, question, k):
        cand = self.semantic.top(question, 20)
        return [cand[n] for n in rerank(question, [self.texts[i] for i in cand], limit=3000)][:k]

    def read(self, question, ctx, k=6):
        return super().read(question, ctx, k)


DOSSIER_UPDATE = """You keep a therapy assistant's dossier on a person, read before every conversation so the \
assistant never has to ask again for something the person already said. Below are the current dossier and what the \
person said since.

Return the complete updated dossier: add what is new, update what changed (what it was until when, and what it is \
now), update the status of plans, keep everything still true, and leave out passing moods. Sections: people in their \
life; work and studies; life and circumstances; health; what keeps coming back; practices tried and how each went; \
plans and intentions with status and date; how they like to be talked to; key events with dates. Every item \
specific, with dates and how often it came up. Up to about 2,500 words.

CURRENT DOSSIER:
{dossier}

WHAT THEY SAID SINCE ("Mind asked" is the assistant's question they answer):
{new}"""


# @ref LLP 0020#part-b-creation — kept current in batches, as a product would after each session, not from one read
class IncrementalDossierStore(DossierStore):
    name = "dossier_inc"

    def build(self, ctx, batch: int = 10):
        def write():
            from lab.common.llm import chat
            text = "(empty: nothing is known yet)"
            for start in range(0, len(ctx.log), batch):
                new = "\n\n".join(f"[{when(ts)}] {t}" for _, ts, t in ctx.log[start:start + batch])
                out, _ = chat(ctx.model, [{"role": "user", "content": DOSSIER_UPDATE.format(dossier=text, new=new)}],
                              tag="memory:dossier_inc", effort="low", max_tokens=16000)
                text = str(out).strip()
            return {"text": text, "updates": (len(ctx.log) + batch - 1) // batch}
        saved = ctx.saved("dossier_inc", write)
        self.text = saved["text"]
        return {"dossier_chars": len(self.text), "updates": saved["updates"]}


STORES = {"log": LogStore, "log_all": FullLogStore, "facts": FactsStore, "episodes": EpisodeStore, "routines": RoutineStore,
          "intentions": IntentionStore, "preferences": PreferenceStore, "periods": PeriodStore, "product": ProductMemoryStore,
          "product_injected": ProductInjectedStore, "log_embed": LogEmbedStore, "log_hybrid": LogHybridStore,
          "statements": StatementStore, "dossier": DossierStore, "statements_merged": MergedStatementStore,
          "statements_rr": RerankedStatementStore, "log_rr": RerankedLogStore, "dossier_inc": IncrementalDossierStore,
          "statements_linked": LinkedStatementStore, "statements_linked_p3": LinkedStatementsListPicked,
          "statements_linked_rr": LinkedStatementsJevPicked, "log_p3": LogListPicked, "log_rr_full": LogJevPickedFull}
