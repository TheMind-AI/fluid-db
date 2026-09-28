"""LLP 0017 Part B: coming back to the assistant, on real conversations. Private data only.

For each cut (ownYYMM, from `lab.datasets.private_chat --cuts`):
  gold    the statements the person made in each chat after the cut, each looked up in the history before it:
          re-told (the history already says it), changed (it says an earlier version), or new. Also: which of Mind's
          questions after the cut the history had already answered.
  lasting Jev marks the statements and questions about lasting facts; the test is on those
  score   does each memory's briefing (from `scenarios memory ownYYMM@...`) hold what the person re-told?
  pooled  the cuts together
  cue     recall on cue: a question per re-told statement, added to the cut's dataset with the statement as gold
  cue_summary  the graded answers to those questions, all cuts
Part A:
  side    every config's answers side by side, for a person to read
  schema  the database's tables, columns and row counts, and how each window was written; no values

Everything reads and writes LAB_PRIVATE_DIR, never the tracked lab.

  .venv/bin/python -m lab.bench.real_return gold own2408
  .venv/bin/python -m lab.bench.real_return lasting own2408
  .venv/bin/python -m lab.bench.real_return score own2408@v25nog
  .venv/bin/python -m lab.bench.real_return pooled own2408@v25nog own2409@v25nog own2608@v25nog
  .venv/bin/python -m lab.bench.real_return side own@v25nog
  .venv/bin/python -m lab.bench.real_return schema own@v25nog
"""
# @ref LLP 0017#part-b-coming-back — the gold is what the person re-told, verified window by window
from __future__ import annotations

import json
import os
import random
import re
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from lab.common.llm import LEDGER, chat_json
from lab.memory.stores import Index

MODEL = "openai:gpt-6-luna"
PRIVATE = Path(os.environ["LAB_PRIVATE_DIR"])
# @ref LLP 0018#processors — another user's data (u<number>) may only be processed with LAB_PROCESSORS=openai
if any(re.match(r"u\d+", a) for a in sys.argv[2:]) and os.environ.get("LAB_PROCESSORS") != "openai":
    raise SystemExit("another user's data needs LAB_PROCESSORS=openai")
LIMIT = float(os.environ.get("LAB_RETURN_BUDGET", "0.5"))
KINDS = ["people", "work", "life", "history", "health", "feelings", "practice", "plan", "preference"]

STATEMENTS = """Below are one person's messages in a conversation with Mind, a therapy assistant. "Mind asked" is the end of \
Mind's preceding turn.

List what the person says about their life that a good memory of them could hold: people and relationships, work and \
studies, where and how they live, their history and past events, health, recurring feelings and struggles, practices \
and techniques they use, plans and goals, and how they like to be talked to.
- One fact per statement, self-contained, in the third person ("The person ..."), in English.
- Skip greetings, small talk, questions to Mind, and passing moods of the moment.

MESSAGES:
{messages}"""
STATEMENTS_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["statements"], "properties": {
    "statements": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["s", "kind"],
                   "properties": {"s": {"type": "string"}, "kind": {"type": "string", "enum": KINDS}}}}}}

LOCATE = """Below are a person's earlier conversations with Mind, a therapy assistant, as numbered windows of the person's \
messages. Then statements the person made in a later conversation, and questions Mind asked in it.

For each statement: which windows already say the same thing (the same fact, possibly in other words), and which say \
something different about the same thing (an earlier version that has since changed)? Up to 3 window ids each, or none.
For each of Mind's questions: which windows already hold the person's answer to it? Up to 3 ids, or none.

EARLIER CONVERSATIONS:
{history}

STATEMENTS:
{statements}

MIND'S QUESTIONS:
{questions}"""
IDS = {"type": "array", "items": {"type": "string"}}
LOCATE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["statements", "questions"], "properties": {
    "statements": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                   "required": ["n", "same", "changed"], "properties": {"n": {"type": "integer"}, "same": IDS, "changed": IDS}}},
    "questions": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                  "required": ["n", "answered"], "properties": {"n": {"type": "integer"}, "answered": IDS}}}}}

VERIFY = """A statement about a person, from a later conversation, and windows of that person's earlier messages. For each \
window, is it:
- "same": it already says the same thing as the statement (the same fact, possibly in other words)
- "changed": it says something different about the same thing (an earlier version that has since changed); then give \
the earlier version as a short third-person statement
- "no": neither

STATEMENT: {s}

WINDOWS:
{windows}"""
VERIFY_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["verdicts"], "properties": {
    "verdicts": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                 "required": ["id", "verdict", "earlier"], "properties": {
                     "id": {"type": "string"}, "verdict": {"type": "string", "enum": ["same", "changed", "no"]},
                     "earlier": {"type": "string"}}}}}}

ANSWERED = """A question that Mind, a therapy assistant, asked a person, and windows of that person's earlier messages. For \
each window: does it already hold the person's answer to this question, so Mind could have known it? What the person \
feels or wants right now, today, can't be known from earlier messages: answer false for questions about that.

QUESTION: {q}

WINDOWS:
{windows}"""
ANSWERED_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["verdicts"], "properties": {
    "verdicts": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["id", "answered"],
                 "properties": {"id": {"type": "string"}, "answered": {"type": "boolean"}}}}}}

# The first wording ("state this fact or something that clearly entails it; a broader theme is not enough") marked
# every statement as missing, even plain paraphrases; this one is validated against hand labels (LLP 0017.000).
CONTAINS = """A briefing that an assistant's memory wrote about a person, and statements about that person. For each \
statement: would someone who read only this briefing already know what the statement says?
- true: the briefing makes the same point, in any words, even more briefly or inside a summary (for example, \
"gets AI FOMO" covers "feels a strong fear of missing out on developments in AI").
- false: the briefing only touches the general topic without that point, or doesn't mention it.

BRIEFING:
{briefing}

STATEMENTS:
{statements}"""
CONTAINS_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["verdicts"], "properties": {
    "verdicts": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["n", "contained"],
                 "properties": {"n": {"type": "integer"}, "contained": {"type": "boolean"}}}}}}


def spent() -> float:
    return sum(c.cost for c in LEDGER.calls if not c.cached)


def ask(prompt: str, schema: dict, tag: str, effort: str = "low") -> dict:
    if spent() > LIMIT:
        raise SystemExit(f"spend guard: ${spent():.3f} > ${LIMIT}")
    out, _ = chat_json(MODEL, prompt, schema, schema_name="out", effort=effort, tag=tag)
    return out


def numbered(items: list[str]) -> str:
    return "\n".join(f"{i + 1}. {x}" for i, x in enumerate(items)) or "(none)"


# ---------------------------------------------------------------------------------------------------- gold
def gold(name: str):
    pre = json.loads((PRIVATE / f"scenario_{name}.json").read_text())
    chats = json.loads((PRIVATE / f"return_chats_{name}.json").read_text())
    windows = {m["id"]: m for m in pre["messages"]}
    ids = list(windows)
    history = "\n\n".join(f"[{m['id']} {m['ts'][:10]}] {m['text']}" for m in pre["messages"])
    index = Index([m["text"] for m in pre["messages"]])
    show = lambda cands: "\n\n".join(f"[{w}] {windows[w]['text'][:3000]}" for w in cands)

    def one(c):
        msgs = "\n".join((f"(Mind asked: {t['asked']}) " if t["asked"] else "") + t["text"] for t in c["turns"])
        st = ask(STATEMENTS.format(messages=msgs), STATEMENTS_SCHEMA, "return:statements")["statements"] if msgs else []
        if not st and not c["questions"]:
            return {"id": c["id"], "statements": [], "questions": []}
        loc = ask(LOCATE.format(history=history, statements=numbered([x["s"] for x in st]),
                                questions=numbered(c["questions"])), LOCATE_SCHEMA, "return:locate")
        by_n = {x["n"]: x for x in loc["statements"]}
        out_st = []
        for n, x in enumerate(st, 1):
            named = [w for w in by_n.get(n, {}).get("same", []) + by_n.get(n, {}).get("changed", []) if w in windows]
            cands = list(dict.fromkeys(named + [ids[i] for i in index.top(x["s"], 5)]))
            v = ask(VERIFY.format(s=x["s"], windows=show(cands)), VERIFY_SCHEMA, "return:verify")["verdicts"]
            same = [y["id"] for y in v if y["verdict"] == "same" and y["id"] in windows]
            changed = [y for y in v if y["verdict"] == "changed" and y["id"] in windows]
            cls = "retold" if same else "changed" if changed else "new"
            out_st.append({**x, "class": cls, "windows": same or [y["id"] for y in changed],
                           "earlier": changed[0]["earlier"] if cls == "changed" else "",
                           "named_by_reader": sorted(set(named) & set(same)), "found_by_bm25": sorted(set(same) - set(named))})
        qa = {x["n"]: x for x in loc["questions"]}
        out_q = []
        for n, q in enumerate(c["questions"], 1):
            named = [w for w in qa.get(n, {}).get("answered", []) if w in windows]
            if not named:
                out_q.append({"q": q, "answered": []})
                continue
            v = ask(ANSWERED.format(q=q, windows=show(named)), ANSWERED_SCHEMA, "return:answered")["verdicts"]
            out_q.append({"q": q, "answered": [y["id"] for y in v if y["answered"] and y["id"] in windows]})
        return {"id": c["id"], "statements": out_st, "questions": out_q}

    with ThreadPoolExecutor(5) as pool:
        rows = list(pool.map(one, chats))
    (PRIVATE / f"return_gold_{name}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    st = [s for r in rows for s in r["statements"]]
    qs = [q for r in rows for q in r["questions"]]
    cls = Counter(s["class"] for s in st)
    print(json.dumps({
        "cut": name, "chats": len(rows), "statements": len(st), "classes": dict(cls),
        "retold_share": round(100 * cls["retold"] / max(1, len(st)), 1),
        "retold_or_changed_share": round(100 * (cls["retold"] + cls["changed"]) / max(1, len(st)), 1),
        "by_kind": {k: dict(Counter(s["class"] for s in st if s["kind"] == k)) for k in KINDS},
        "retold_found_only_by_bm25": sum(1 for s in st if s["class"] == "retold" and not s["named_by_reader"]),
        "questions": len(qs), "questions_already_answered": sum(1 for q in qs if q["answered"]),
        "spent": round(spent(), 4)}, indent=1))


# @ref LLP 0017#amendment-three-cuts — a memory is for lasting facts; the moment's mood is not a test of it
LASTING_STATEMENT = ("Is `statement` a lasting fact about the person: who is in their life, their work, circumstances, "
                     "history, health, habits and practices, plans, preferences, or a struggle that recurs? Not how they "
                     "feel at the moment or what happened just now.")
LASTING = 0.2   # Jev's p is compressed (LLP 0015, section 5); below 0.2: moods and events of the moment. Set before scoring
LASTING_QUESTION = ("Does `question` ask for a lasting fact about the person: who is in their life, their work, "
                    "circumstances, history, habits, plans or preferences? Not how they feel now, what happened "
                    "recently, or what they want to talk about now.")


def lasting(name: str):
    """Jev scores how much each statement and question is about a lasting fact, in the gold file."""
    from lab.common import jev
    path = PRIVATE / f"return_gold_{name}.json"
    rows = json.loads(path.read_text())
    items = [(x, "statement", x["s"], LASTING_STATEMENT) for r in rows for x in r["statements"]] + \
            [(x, "question", x["q"], LASTING_QUESTION) for r in rows for x in r["questions"]]
    answers = jev.ask_many([({k: text}, {"lasting": jev.noul(q)}) for _, k, text, q in items], tag="return:lasting")
    for (x, *_), a in zip(items, answers):
        x["lasting"] = round(a["lasting"]["noul"], 3)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    st = [x for r in rows for x in r["statements"]]
    qs = [x for r in rows for x in r["questions"]]
    keep = [x for x in st if x["lasting"] >= LASTING]
    lq = [q for q in qs if q["lasting"] >= LASTING]
    print(json.dumps({"cut": name, "statements": len(st), "lasting": len(keep),
                      "lasting_classes": dict(Counter(x["class"] for x in keep)),
                      "lasting_retold_share": round(100 * sum(x["class"] == "retold" for x in keep) / max(1, len(keep)), 1),
                      "questions": len(qs), "lasting_questions": len(lq),
                      "lasting_questions_answered": sum(1 for q in lq if q["answered"]),
                      "spent": round(spent(), 4)}, indent=1))


# ---------------------------------------------------------------------------------------------------- score
def boot(pairs: list[tuple[int, int]], n=10000, seed=1):
    if not pairs:
        return None
    d = [a - b for a, b in pairs]
    rng = random.Random(seed)
    bs = sorted(sum(rng.choice(d) for _ in d) / len(d) for _ in range(n))
    return round(100 * sum(d) / len(d), 1), round(100 * bs[int(0.025 * n)], 1), round(100 * bs[int(0.975 * n)], 1)


def score(name: str):
    cut = name.split("@")[0]
    rows = {r["id"]: r for r in json.loads((PRIVATE / f"return_gold_{cut}.json").read_text())}
    runs = json.loads((PRIVATE / "results" / f"scenario_{name.replace('@', '_')}_runs.json").read_text())
    jobs = []   # (config, briefing kind, chat, statements to check, their gold class)
    for cfg, res in runs.items():
        by_id = {r["id"]: r["answer"] for r in res["rows"]}
        for cid, r in rows.items():
            st = r["statements"]
            if not st:
                continue
            checks = [(s["earlier"] if s["class"] == "changed" else s["s"], s["class"], i, s.get("lasting", 1) >= LASTING)
                      for i, s in enumerate(st)]
            for kind, bid in (("opening", cid), ("generic", "r00")):
                jobs.append((cfg, kind, cid, checks, by_id[bid]))

    def one(job):
        cfg, kind, cid, checks, briefing = job
        v = ask(CONTAINS.format(briefing=briefing, statements=numbered([c[0] for c in checks])), CONTAINS_SCHEMA,
                "return:contains", effort="medium")["verdicts"]
        got = {x["n"]: x["contained"] for x in v}
        return [{"cut": cut, "cfg": cfg, "kind": kind, "chat": cid, "i": i, "class": cls, "lasting": last,
                 "hit": int(bool(got.get(n + 1)))} for n, (_, cls, i, last) in enumerate(checks)]

    with ThreadPoolExecutor(6) as pool:
        hits = [h for part in pool.map(one, jobs) for h in part]
    (PRIVATE / "results" / f"return_hits_{cut}.json").write_text(json.dumps(hits, indent=1))
    out = summarize(hits, list(runs))
    (PRIVATE / "results" / f"return_summary_{cut}.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


def pooled(names: list[str]):
    hits = [h for n in names for h in json.loads((PRIVATE / "results" / f"return_hits_{n.split('@')[0]}.json").read_text())]
    cfgs = list(dict.fromkeys(h["cfg"] for h in hits))
    out = summarize(hits, cfgs)
    (PRIVATE / "results" / "return_summary_pooled.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


def summarize(hits: list[dict], runs: list[str]) -> dict:
    """Recall per config, briefing kind and gold class, on all statements and on the lasting ones (the test)."""
    out = {}
    for subset, keep in (("lasting", lambda x: x["lasting"]), ("all", lambda x: True)):
        table = defaultdict(dict)
        for cfg in runs:
            for kind in ("opening", "generic"):
                for cls in ("retold", "changed", "new"):
                    h = [x["hit"] for x in hits if x["cfg"] == cfg and x["kind"] == kind and x["class"] == cls and keep(x)]
                    table[cfg][f"{kind}_{cls}"] = f"{100 * sum(h) / len(h):.1f}% of {len(h)}" if h else "-"
        key = lambda cfg, kind: {(x["cut"], x["chat"], x["i"]): x["hit"] for x in hits if x["cfg"] == cfg
                                 and x["kind"] == kind and x["class"] == "retold" and keep(x)}
        ref = "brain_jev_recall" if "brain_jev_recall" in runs else next(iter(runs))
        diffs = {}
        for cfg in runs:
            if cfg != ref:
                for kind in ("opening", "generic"):
                    a, b = key(ref, kind), key(cfg, kind)
                    diffs[f"{ref} - {cfg} ({kind})"] = boot([(a[k], b[k]) for k in a if k in b])
        out[subset] = {"recall": table, "paired_retold": diffs}
    out["spent"] = round(spent(), 4)
    return out


# ---------------------------------------------------------------------------------------------------- cue
CUE = """Below are statements about a person. For each, write the question the person could ask their assistant \
("What have I told you about ...?", "Do you remember ...?") whose answer is the statement. Name the topic only; don't \
give the answer away.

STATEMENTS:
{statements}"""
CUE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["questions"], "properties": {
    "questions": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["n", "q"],
                  "properties": {"n": {"type": "integer"}, "q": {"type": "string"}}}}}}


# @ref LLP 0017#amendment-recall-on-cue — asked about the topic, does the memory at the cut know the fact?
def cue(name: str, controls: int = 10):
    """Add a question per re-told or changed lasting statement, and per up to `controls` new ones, to the cut's dataset,
    with the statement (or its earlier version) as the gold. `scenarios memory` then answers and grades them."""
    path = PRIVATE / f"scenario_{name}.json"
    ds = json.loads(path.read_text())
    rows = json.loads((PRIVATE / f"return_gold_{name}.json").read_text())
    st = [(r["id"], i, x) for r in rows for i, x in enumerate(r["statements"]) if x["lasting"] >= LASTING]
    new = [t for t in st if t[2]["class"] == "new"]
    picked = [t for t in st if t[2]["class"] != "new"] + random.Random(4).sample(new, min(controls, len(new)))
    qs = ask(CUE.format(statements=numbered([x["s"] for _, _, x in picked])), CUE_SCHEMA, "return:cue")["questions"]
    by_n = {q["n"]: q["q"] for q in qs}
    gold = {"retold": lambda x: x["s"], "changed": lambda x: x["earlier"],
            "new": lambda x: "UNKNOWN: never mentioned before"}
    cues = [{"id": f"c-{cid}-{i}", "type": f"cue_{x['class']}", "question": by_n[n + 1], "answer": gold[x["class"]](x)}
            for n, (cid, i, x) in enumerate(picked) if n + 1 in by_n]
    ds["qa"] = [q for q in ds["qa"] if not q["id"].startswith("c-")] + cues
    path.write_text(json.dumps(ds, ensure_ascii=False, indent=1))
    print(json.dumps({"cut": name, "questions": len(cues), "by_type": dict(Counter(c["type"] for c in cues)),
                      "spent": round(spent(), 4)}))


def cue_summary(names: list[str]):
    rows = defaultdict(list)   # config -> graded cue rows, all cuts
    for n in names:
        runs = json.loads((PRIVATE / "results" / f"scenario_{n.replace('@', '_')}_runs.json").read_text())
        for cfg, res in runs.items():
            rows[cfg] += [dict(r, cut=n) for r in res["rows"] if r["id"].startswith("c-")]
    table = {cfg: {t: f"{100 * sum(r['score'] for r in rs if r['type'] == t) / max(1, sum(r['type'] == t for r in rs)):.1f}%"
                      f" of {sum(r['type'] == t for r in rs)}" for t in ("cue_retold", "cue_changed", "cue_new")}
             for cfg, rs in rows.items()}
    key = lambda cfg: {(r["cut"], r["id"]): r["score"] for r in rows[cfg] if r["type"] == "cue_retold"}
    ref = "brain_jev_recall"
    diffs = {f"{ref} - {cfg}": boot([(key(ref)[k], key(cfg)[k]) for k in key(ref) if k in key(cfg)])
             for cfg in rows if cfg != ref}
    per_cut = {n: {cfg: round(100 * sum(r["score"] for r in rs if r["cut"] == n and r["type"] == "cue_retold")
                              / max(1, sum(1 for r in rs if r["cut"] == n and r["type"] == "cue_retold")), 1)
                   for cfg, rs in rows.items()} for n in names}
    out = {"accuracy": table, "paired_retold": diffs, "retold_per_cut": per_cut}
    (PRIVATE / "results" / "return_cue_summary.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


# @ref LLP 0018#protocol — the shim judge is trusted only as far as it agrees with Jev on data both may see
def judge_check(names: list[str]):
    """Re-grade graded cue answers with the judge as it runs under LAB_PROCESSORS (the LLM shim) and compare with the
    stored verdicts (real Jev). Prints agreement only."""
    from lab.common.grade import jev_grade
    rows = [(n, cfg, r) for n in names
            for cfg, res in json.loads((PRIVATE / "results" / f"scenario_{n.replace('@', '_')}_runs.json").read_text()).items()
            for r in res["rows"] if r["id"].startswith("c-")]
    with ThreadPoolExecutor(8) as pool:
        new = list(pool.map(lambda x: jev_grade(x[2]["question"], x[2]["gold"], x[2]["answer"], tag="grade:shim")["score"], rows))
    old = [r["score"] for _, _, r in rows]
    same = sum(a == b for a, b in zip(old, new))
    by_cfg = defaultdict(lambda: [[], []])
    for (n, cfg, r), a in zip(rows, new):
        by_cfg[cfg][0].append(r["score"])
        by_cfg[cfg][1].append(a)
    print(json.dumps({"answers": len(rows), "exact_agreement": round(same / len(rows), 3),
                      "within_half": round(sum(abs(a - b) <= 0.5 for a, b in zip(old, new)) / len(rows), 3),
                      "mean_jev": round(sum(old) / len(old), 3), "mean_shim": round(sum(new) / len(new), 3),
                      "by_config_jev_vs_shim": {c: (round(100 * sum(v[0]) / len(v[0]), 1), round(100 * sum(v[1]) / len(v[1]), 1))
                                                for c, v in by_cfg.items()},
                      "spent": round(spent(), 4)}, indent=1))


# ---------------------------------------------------------------------------------------------------- read
def side(name: str):
    """Every config's answer to every question, side by side, for a person to read (private, like the answers)."""
    runs = json.loads((PRIVATE / "results" / f"scenario_{name.replace('@', '_')}_runs.json").read_text())
    qs = next(iter(runs.values()))["rows"]
    out = [f"# {name}: answers side by side\n"]
    for q in qs:
        out.append(f"## {q['id']} ({q['type']}): {q['question']}\n")
        for cfg, res in runs.items():
            r = next(x for x in res["rows"] if x["id"] == q["id"])
            out.append(f"### {cfg} ({r['evidence_chars']} evidence chars)\n\n{r['answer']}\n")
    path = PRIVATE / "results" / f"{name.replace('@', '_')}_side_by_side.md"
    path.write_text("\n".join(out))
    print(f"{len(qs)} questions x {len(runs)} configs -> {path}")


def schema(name: str):
    """The database's shape: tables, columns and row counts, and how each window was written. No values."""
    import sqlite3
    run = PRIVATE / "runs" / f"scenario_{name.replace('@', '_')}"
    db = sqlite3.connect(run / "db.sqlite")
    tables = [r[0] for r in db.execute("SELECT name FROM _tables")]
    shape = {t: {"rows": db.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0],
                 "columns": [c[1] for c in db.execute(f'PRAGMA table_info("{t}")') if not c[1].startswith("_")]}
             for t in tables}
    trace = json.loads((run / "ingest.json").read_text())["trace"]
    tiers = Counter(t.get("tier", "?") for t in trace)
    print(json.dumps({"windows": len(trace), "tiers": dict(tiers), "ops": sum(t.get("ops") or 0 for t in trace),
                      "ingest_cost": round(sum(t.get("cost") or 0 for t in trace), 4), "tables": shape}, indent=1))


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    {"gold": lambda: gold(args[0]), "lasting": lambda: lasting(args[0]), "score": lambda: score(args[0]),
     # LLP 0018#protocol: no control questions for other users' accounts
     "pooled": lambda: pooled(args), "cue": lambda: cue(args[0], controls=0 if re.match(r"u\d+", args[0]) else 10),
     "cue_summary": lambda: cue_summary(args),
     "judge_check": lambda: judge_check(args),
     "side": lambda: side(args[0]), "schema": lambda: schema(args[0])}[cmd]()
