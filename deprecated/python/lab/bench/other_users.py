"""LLP 0018: round 10's recall-on-cue test on other people's conversations. Private data only; OpenAI only.

  genuine   is each eligible account a person using the assistant for their own life, or someone testing it?
            (the model reads a 40-message sample; prints alias and probability)
  prepare   an account's export -> its windows, the history before one cut and the chats after it
  summary   recall on cue per account and pooled, with paired CIs, from the graded answers
  tables    H4: tables, rows, the largest table's share, how windows were written (numbers only)
  compare   LLP 0019: every config, the round's pairs, the Czech subset, tokens per question (numbers only)

Every command refuses to run unless LAB_PROCESSORS=openai and LAB_PRIVATE_DIR are set, and prints counts only.

  .venv/bin/python -m lab.bench.other_users genuine U1 U5 ...
  .venv/bin/python -m lab.bench.other_users prepare U5
  .venv/bin/python -m lab.bench.other_users summary six u5 u8 ...   (the first argument labels the output)
"""
# @ref LLP 0018#processors — other people's text goes to OpenAI only, and the analyst sees aggregates only
from __future__ import annotations

import json
import os
import random
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

if os.environ.get("LAB_PROCESSORS") != "openai" or not os.environ.get("LAB_PRIVATE_DIR"):
    raise SystemExit("other users' data: set LAB_PROCESSORS=openai and LAB_PRIVATE_DIR")

from lab.common.llm import LEDGER, chat_json
from lab.datasets import private_chat

PRIVATE = Path(os.environ["LAB_PRIVATE_DIR"])
MODEL = "openai:gpt-6-luna"
GENUINE = """Below are messages one person wrote to Mind, a therapy and wellbeing assistant, sampled evenly across all \
their chats.

Is this a person using the assistant for their own life: their feelings, relationships, work, health or problems? Or is \
it someone testing, evaluating or demonstrating the product: test phrases, trying features, asking about the app \
itself, role-play to see how it reacts?

MESSAGES:
{messages}"""
GENUINE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["genuine", "probability"], "properties": {
    "genuine": {"type": "boolean"},
    "probability": {"type": "number", "description": "your probability (0-1) that this is genuine personal use"}}}


def spent() -> float:
    return sum(c.cost for c in LEDGER.calls if not c.cached)


def genuine(aliases: list[str]):
    samples = json.loads((PRIVATE / "samples.json").read_text())

    def one(a):
        out, _ = chat_json(MODEL, GENUINE.format(messages="\n".join(f"- {t}" for t in samples[a])), GENUINE_SCHEMA,
                           schema_name="genuine", tag="other:genuine", effort="low")
        p = max(0.0, min(1.0, float(out["probability"])))
        return round(p if (out["genuine"] and p >= 0.5) or (not out["genuine"] and p <= 0.5) else 1 - p, 3)

    with ThreadPoolExecutor(8) as pool:
        ps = dict(zip(aliases, pool.map(one, aliases)))
    (PRIVATE / "genuine.json").write_text(json.dumps(ps, indent=1))
    print(" ".join(f"{a}:{p}" for a, p in ps.items()), f"| spent ${spent():.4f}")


# @ref LLP 0018#amendment-selection-and-a-smaller-cut — at most 150 windows and 60% of them before it, 15 chats after
def prepare(alias: str, max_before: int = 150, share: float = 0.6, max_after: int = 15):
    export = json.loads((PRIVATE / f"{alias}_export.json").read_text())
    ds = private_chat.convert(export, "User", 6)
    n = len(ds["messages"])
    cut = ds["messages"][min(max_before, int(share * n))]["ts"]
    later = sorted({m["chat"]: min(x["at"] for x in export["messages"] if x["chat"] == m["chat"])
                    for m in export["messages"]}.items(), key=lambda kv: kv[1])
    after = [t for _, t in later if t >= cut][:max_after + 1]
    end = after[max_after] if len(after) > max_after else "9999"
    name = alias.lower()
    pre, chats = private_chat.split(export, ds, cut, end)
    pre["name"], pre["qa"] = f"scenario_{name}", []   # no briefings (LLP 0018#protocol); `real_return cue` adds questions
    (PRIVATE / f"scenario_{name}.json").write_text(json.dumps(pre, ensure_ascii=False, indent=1))
    (PRIVATE / f"return_chats_{name}.json").write_text(json.dumps(chats, ensure_ascii=False, indent=1))
    print(json.dumps({"alias": alias, "windows": n, "before_cut": len(pre["messages"]),
                      "history_chars": sum(len(m["text"]) for m in pre["messages"]), "chats_after": len(chats),
                      "later_messages": sum(len(c["turns"]) for c in chats), "cutoff": cut[:10]}))
    (PRIVATE / f"cut_{name}.txt").write_text(cut[:10])


def summary(names: list[str], label: str = "all"):
    """Pooled over the accounts, numbers only: re-telling (H1), recall on cue per memory with paired CIs (H2, H3), and
    the gap to reading everything by the language of the earlier window (H5)."""
    import re
    CZ = re.compile(r"[ěščřžýáíéůúňťď]", re.I)
    # Czech is often typed without diacritics: also count distinctly Czech function words (not English homographs)
    STOP = set("je jsem jsi jsme se na ze že ale tak jak kdyz když protoze protože moc neni není nevim nevím mam mám byl "
               "bylo jsou taky uz už jeste ještě proste prostě ted teď hodne hodně dneska vubec vůbec treba třeba fakt furt "
               "mě já který ktery bych abych mne mně".split())

    def czech(text: str) -> bool:
        words = re.findall(r"[^\W\d_]+", text.lower())
        return len(CZ.findall(text)) / max(1, len(text)) > 0.01 or sum(w in STOP for w in words) / max(1, len(words)) > 0.08
    rows, per, retell = defaultdict(list), {}, {}
    for n in names:
        gold = {r["id"]: r for r in json.loads((PRIVATE / f"return_gold_{n}.json").read_text())}
        windows = {m["id"]: m["text"] for m in json.loads((PRIVATE / f"scenario_{n}.json").read_text())["messages"]}
        lasting = [x for r in gold.values() for x in r["statements"] if x.get("lasting", 0) >= 0.2]
        retell[n] = (sum(x["class"] == "retold" for x in lasting), len(lasting))
        runs = json.loads((PRIVATE / "results" / f"scenario_{n}_v25nog_runs.json").read_text())
        per[n] = {}
        for cfg, res in runs.items():
            rs = []
            for r in res["rows"]:
                if r["type"] != "cue_retold":
                    continue
                _, cid, i = r["id"].split("-")
                st = gold[cid]["statements"][int(i)]
                text = " ".join(windows.get(w, "") for w in st["windows"])
                lang = "czech" if czech(text) else "other"
                rs.append({"acct": n, "id": r["id"], "score": r["score"], "lang": lang})
            rows[cfg] += rs
            per[n][cfg] = round(100 * sum(r["score"] for r in rs) / max(1, len(rs)), 1)
        per[n]["questions"] = sum(1 for r in rows["brain_jev_recall"] if r["acct"] == n)
    pooled = {cfg: round(100 * sum(r["score"] for r in rs) / max(1, len(rs)), 1) for cfg, rs in rows.items()}
    key = lambda cfg, lang=None: {(r["acct"], r["id"]): r["score"] for r in rows[cfg] if lang in (None, r["lang"])}

    def boot(a: str, b: str, lang=None, n=10000, seed=1):
        A, B = key(a, lang), key(b, lang)
        d = [A[k] - B[k] for k in A if k in B]
        if not d:
            return None
        rng = random.Random(seed)
        bs = sorted(sum(rng.choice(d) for _ in d) / len(d) for _ in range(n))
        return round(100 * sum(d) / len(d), 1), round(100 * bs[int(0.025 * n)], 1), round(100 * bs[int(0.975 * n)], 1), len(d)

    ref = "brain_jev_recall"
    pairs = {f"{ref} - {c}": boot(ref, c) for c in rows if c != ref}
    pairs["full_context - brain_jev_recall"] = boot("full_context", ref)
    pairs["product - product_injected"] = boot("product", "product_injected")
    langs = {lang: {"questions": sum(r["lang"] == lang for r in rows[ref]),
                    "full_context - brain_jev_recall": boot("full_context", ref, lang),
                    "full_context - brain_all": boot("full_context", "brain_all", lang)} for lang in ("czech", "other")}
    # one heavy account can dominate the pooled numbers: the mean over accounts, and in how many accounts A beats B
    cfgs = [c for c in rows]
    macro = {c: round(sum(per[n][c] for n in names) / len(names), 1) for c in cfgs}
    wins = lambda a, b: f"{sum(per[n][a] > per[n][b] for n in names)} of {len(names)} (ties {sum(per[n][a] == per[n][b] for n in names)})"
    signs = {f"{ref} > product_injected": wins(ref, "product_injected"), f"{ref} > product": wins(ref, "product"),
             f"full_context > {ref}": wins("full_context", ref), "product > product_injected": wins("product", "product_injected")}
    told = sum(a for a, _ in retell.values())
    total = sum(b for _, b in retell.values())
    out = {"accounts": len(names), "retold_lasting": [told, total, round(100 * told / max(1, total), 1)],
           "retold_per_account": {n: f"{a}/{b}" for n, (a, b) in retell.items()},
           "retold_questions": len(rows[ref]), "pooled": pooled, "macro": macro, "accounts_where": signs,
           "paired": pairs, "by_language": langs,
           "per_account": per}
    (PRIVATE / "results" / f"other_users_summary_{label}.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


# @ref LLP 0019#hypotheses — the pairs the round's hypotheses name, on round 11's graded questions
PAIRS19 = [("log_hybrid", "log_rag"), ("log_embed", "log_rag"), ("statements", "facts_log"), ("dossier", "full_context"),
           ("conv_all", "facts_log"), ("conv_all", "full_context"), ("conv_all", "brain_jev_recall"),
           ("dossier", "product_injected")]


def compare(names: list[str], label: str = "all", pairs=PAIRS19):
    """Recall on cue (re-told questions) for every config in the runs: pooled, per-account mean, paired CIs for the
    named pairs, the Czech subset, and input tokens per question. Numbers only."""
    import re
    CZ = re.compile(r"[ěščřžýáíéůúňťď]", re.I)
    STOP = set("je jsem jsi jsme se na ze že ale tak jak kdyz když protoze protože moc neni není nevim nevím mam mám byl "
               "bylo jsou taky uz už jeste ještě proste prostě ted teď hodne hodně dneska vubec vůbec treba třeba fakt furt "
               "mě já který ktery bych abych mne mně".split())
    czech = lambda t: len(CZ.findall(t)) / max(1, len(t)) > 0.01 or \
        sum(w in STOP for w in re.findall(r"[^\W\d_]+", t.lower())) / max(1, len(re.findall(r"[^\W\d_]+", t))) > 0.08
    rows, per = defaultdict(dict), defaultdict(dict)
    for n in names:
        gold = {r["id"]: r for r in json.loads((PRIVATE / f"return_gold_{n}.json").read_text())}
        windows = {m["id"]: m["text"] for m in json.loads((PRIVATE / f"scenario_{n}.json").read_text())["messages"]}
        runs = json.loads((PRIVATE / "results" / f"scenario_{n}_v25nog_runs.json").read_text())
        for cfg, res in runs.items():
            rs = [r for r in res["rows"] if r["type"] == "cue_retold"]
            for r in rs:
                _, cid, i = r["id"].split("-")
                st = gold[cid]["statements"][int(i)]
                rows[cfg][(n, r["id"])] = (r["score"], czech(" ".join(windows.get(w, "") for w in st["windows"])),
                                            r.get("answer_tokens_in") or 0)
            per[n][cfg] = round(100 * sum(r["score"] for r in rs) / max(1, len(rs)), 1)
    cfgs = [c for c in rows if all(c in per[n] for n in names)]
    pooled = {c: round(100 * sum(v[0] for v in rows[c].values()) / len(rows[c]), 1) for c in cfgs}
    macro = {c: round(sum(per[n][c] for n in names) / len(names), 1) for c in cfgs}
    tokens = {c: round(sum(v[2] for v in rows[c].values()) / len(rows[c])) for c in cfgs}

    def boot(a, b, only_czech=None, n=10000, seed=1):
        keys = [k for k in rows[a] if k in rows[b] and (only_czech is None or rows[a][k][1] == only_czech)]
        d = [rows[a][k][0] - rows[b][k][0] for k in keys]
        if not d:
            return None
        rng = random.Random(seed)
        bs = sorted(sum(rng.choice(d) for _ in d) / len(d) for _ in range(n))
        return [round(100 * sum(d) / len(d), 1), round(100 * bs[int(0.025 * n)], 1), round(100 * bs[int(0.975 * n)], 1), len(d)]

    wins = lambda a, b: f"{sum(per[n][a] > per[n][b] for n in names)} of {len(names)} (ties {sum(per[n][a] == per[n][b] for n in names)})"
    out = {"accounts": len(names), "questions": len(rows[cfgs[0]]) if cfgs else 0, "pooled": pooled, "macro": macro,
           "input_tokens_per_question": tokens,
           "paired": {f"{a} - {b}": boot(a, b) for a, b in pairs if a in cfgs and b in cfgs},
           "paired_czech": {f"{a} - {b}": boot(a, b, True) for a, b in pairs if a in cfgs and b in cfgs},
           "wins": {f"{a} > {b}": wins(a, b) for a, b in pairs if a in cfgs and b in cfgs},
           "per_account": {n: per[n] for n in names}}
    (PRIVATE / "results" / f"compare_{label}.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k != "per_account"}, indent=1))


def tables(names: list[str]):
    """H4 per account, numbers only: tables, rows, the largest table's share of rows, and how windows were written.
    Table names are derived from the person's words, so they are not printed."""
    import sqlite3
    from collections import Counter
    for n in names:
        run = PRIVATE / "runs" / f"scenario_{n}_v25nog"
        db = sqlite3.connect(run / "db.sqlite")
        rows = [db.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for (t,) in db.execute("SELECT name FROM _tables")]
        trace = json.loads((run / "ingest.json").read_text())["trace"]
        tiers = Counter(t.get("tier") for t in trace)
        print(json.dumps({"account": n, "windows": len(trace), "tiers": dict(tiers), "tables": len(rows),
                          "rows": sum(rows), "largest_share": round(max(rows) / max(1, sum(rows)), 3) if rows else 0,
                          "ingest_cost": round(sum(t.get("cost") or 0 for t in trace), 4)}))


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    {"genuine": lambda: genuine(args), "prepare": lambda: prepare(args[0]),
     "summary": lambda: summary(args[1:], label=args[0]),
     "tables": lambda: tables(args), "compare": lambda: compare(args[1:], label=args[0])}[cmd]()
