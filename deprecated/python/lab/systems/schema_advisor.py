"""Schema advisor: change the schema from the query log.

evidence  (code)      the logged questions that went badly, by their signals; the tables their plans used, with
                      value counts, sample rows with their messages, and spelling variants code finds
propose   (one LLM)   migrations from a closed set, each citing the logged questions it serves
test      (code, Jev) on a scratch copy: decide every row (rules, association, Jev for leftovers); reject a column
                      that duplicates another or leaves most rows undecided; spot-check the rules with Jev; replay
                      the questions the migration claims to serve, before and after, and keep it only if they got
                      healthier (the verifier's trust, without free-text matching)
apply     (code)      write it, and record the migration, its evidence and its stats in `_migrations`
Gold answers are never used: a deployed system doesn't have them.
"""
# @ref LLP 0003#migrations — the closed migration set and its validation
from __future__ import annotations

import json
import random
import re
import shutil
import statistics
from collections import Counter
from pathlib import Path

from lab.common.llm import chat_json
from lab.systems import derived
from lab.systems.engine import ident

SCHEMA = {"type": "object", "properties": {"migrations": {"type": "array", "items": {"type": "object", "properties": {
    "op": {"type": "string", "enum": ["derive_column", "canonicalize"]},
    "table": {"type": "string"},
    "column": {"type": "string", "description": "derive_column: the new column's name; canonicalize: the column to clean"},
    "description": {"type": "string", "description": "derive_column: what the column means, one line"},
    "values": {"type": "array", "items": {"type": "string"}, "description": "derive_column: the closed set of values"},
    "scope": {"type": "object", "properties": {
        "column": {"type": "string"}, "regex": {"type": "string"}}, "required": ["column", "regex"], "additionalProperties": False,
        "description": "derive_column: the rows it applies to (a regex on one existing column); both empty for every row"},
    "rules": {"type": "array", "items": {"type": "object", "properties": {
        "column": {"type": "string", "description": "an existing column, or _message for the user's original message"},
        "regex": {"type": "string", "description": "Python regex; (?i) for case-insensitive"},
        "value": {"type": "string"}}, "required": ["column", "regex", "value"], "additionalProperties": False}},
    "serves": {"type": "array", "items": {"type": "string"}, "description": "ids of the flagged questions it helps"},
    "why": {"type": "string"}},
    "required": ["op", "table", "column", "description", "values", "scope", "rules", "serves", "why"], "additionalProperties": False}}},
    "required": ["migrations"], "additionalProperties": False}

PROMPT = """You maintain the schema of a personal database that an assistant queries in plain language. A question is
answered by filling a closed query form: a table, an operation (count, sum, avg, max, min, latest, list, ...), a column,
a time period, and filters, where every filter is one value that exists in a column. So a question can only be
answered exactly when what it asks about is a clean value of some column.

Below are the tables that logged questions used (columns with their most common values and counts, and sample rows
with the user's original message), then the logged questions that went badly, with the plan that answered them and
why they were flagged (low_trust: a checker doubted the plan; no_answer; free_text: the plan had to match inside a
free-text column; unmatched: words of the question that match nothing in the table; disagree: a second reader got
different numbers; variants: the plan filtered on one spelling of a value the column also stores in other spellings).

Propose schema migrations so that questions like these become one filter on a clean column. Allowed operations:
1. derive_column: add a NEW column with a small closed set of values. Each row's value is decided by your ordered
   rules (the first rule whose regex matches the given column decides); a rule may read an existing column or
   `_message`, the user's original message for that row. Rows no rule decides are filled by association (the value
   most rows with the same merchant or place have) and then by a classifier that reads the row and its message. Use
   it when a concept the questions ask about (a kind of thing, a place, an occasion) is buried in free text, lumped
   into a broader value of another column, or implied by other columns.
2. canonicalize: merge spelling variants of the values of an existing column (a filter matches one exact value, so
   variants split counts and totals). Code finds the candidate variants; you only name the table and column.
Rules:
- Only propose a migration that serves at least two of the flagged questions; list their ids in `serves`.
- Don't add a column that duplicates an existing clean one. Lowercase values, except proper names.
- `scope` says which rows the column applies to (e.g. column category, regex ^dining$); leave both empty when it
  applies to every row. Rows outside the scope stay empty: don't invent catch-all values like "other" for them. The
  values must cover every row inside the scope.
- For canonicalize, leave description, values and rules empty.
- If nothing is worth changing, return no migrations.

TABLES:
{profiles}

FLAGGED QUESTIONS (id | question | plan | signals | answer):
{flagged}"""

FLAGS = ("low_trust", "no_answer", "free_text", "unmatched", "disagree", "variants")


def profile(eng, table: str, top: int = 25, samples: int = 8, seed: int = 3) -> str:
    db = eng.db
    n = db.execute(f"SELECT COUNT(*) FROM '{table}'").fetchone()[0]
    desc = (db.execute("SELECT description FROM _tables WHERE name = ?", (table,)).fetchone() or [""])[0]
    lines = [f"TABLE {table} ({n} rows): {desc}"]
    for c in eng.columns(table):
        name = c["name"]
        vals = db.execute(f"SELECT \"{name}\", COUNT(*) FROM '{table}' WHERE \"{name}\" IS NOT NULL GROUP BY 1 "
                          f"ORDER BY 2 DESC").fetchall()
        filled = sum(k for _, k in vals)
        target = eng.target_table(name)
        if target:
            key = next((k for k in ("name", "title", "full_name") if k in [x["name"] for x in eng.columns(target)]), None)
            names = {r[0]: r[1] for r in db.execute(f"SELECT id, \"{key}\" FROM '{target}'")} if key else {}
            shown = ", ".join(f"{names.get(v, v)} ({k})" for v, k in vals[:top])
            lines.append(f"  {name}: link to {target}; {filled} filled; {shown}")
        elif vals and all(isinstance(v, (int, float)) for v, _ in vals[:50]):
            nums = [v for v, _ in vals]
            lines.append(f"  {name} ({c['type']}): {filled} filled; {min(nums)} .. {max(nums)}")
        elif vals and all(isinstance(v, str) and re.match(r"^\d{4}-\d{2}-\d{2}", v) for v, _ in vals[:50]):
            lines.append(f"  {name} (dates): {filled} filled; {min(v for v, _ in vals)} .. {max(v for v, _ in vals)}")
        else:
            shown = ", ".join(f"{str(v)[:40]!r} ({k})" for v, k in vals[:top])
            more = f", ... {len(vals) - top} more values" if len(vals) > top else ""
            lines.append(f"  {name}: {filled} filled, {len(vals)} distinct; {shown}{more}")
            counts = {v: k for v, k in vals if isinstance(v, str)}
            if 3 <= len(counts) <= 400:   # code-found spelling variants: evidence for canonicalize
                pairs = derived.merge_candidates(counts)[:12]
                if pairs:
                    lines.append(f"    possible spelling variants in {name}: " + "; ".join(
                        f"{a!r} ({counts[a]}) ~ {b!r} ({counts[b]})" for a, b in pairs))
    rows = [dict(r) for r in db.execute(f"SELECT * FROM '{table}'")]
    for r in random.Random(seed).sample(rows, min(samples, len(rows))):
        shown = {k: v for k, v in r.items() if k not in ("id", "_src", "_ts") and v is not None}
        lines.append(f"  e.g. {json.dumps(shown, ensure_ascii=False)[:220]}  <- message: {derived.message(db, r['_src'])[:160]!r}")
    return "\n".join(lines)


def flagged(queries: list[dict]) -> list[dict]:
    return [q for q in queries if any(s.split(":")[0] in FLAGS for s in q["signals"])]


def _line(q: dict) -> str:
    plan = q["plan"].get("text") or json.dumps({k: q["plan"].get(k) for k in ("table", "op", "column", "filters", "period")})
    agent = f" | a second reader answered: {q['agent_answer'][:100]!r}" if q.get("agent_answer") else ""
    return f"{q['qid']} | {q['question']} | {plan} | {', '.join(q['signals'])} | answer: {str(q['answer'])[:100]!r}{agent}"


def propose(eng, queries: list[dict], model: str, effort: str = "medium", feedback: str = "") -> tuple[list[dict], str]:
    bad = flagged(queries)
    tables = sorted({q["plan"].get("table") for q in bad if q["plan"].get("table") in eng.tables()})
    prompt = PROMPT.format(profiles="\n\n".join(profile(eng, t) for t in tables), flagged="\n".join(_line(q) for q in bad))
    if feedback:
        prompt += ("\n\nMIGRATIONS YOU PROPOSED BEFORE THAT TESTS REJECTED (propose fixed versions if the need is real, "
                   "or drop them; migrations that were kept are already applied):\n" + feedback)
    out, _ = chat_json(model, prompt, SCHEMA, schema_name="migrations", tag="advisor:propose", effort=effort, max_tokens=12000)
    return out["migrations"], prompt


def validate(eng, m: dict, qids: set[str]) -> str | None:
    if m["table"] not in eng.tables():
        return f"no table {m['table']}"
    cols = {c["name"] for c in eng.columns(m["table"])}
    serves = [q for q in m["serves"] if q in qids]
    if len(serves) < 2:
        return f"serves {len(serves)} flagged questions (needs 2)"
    if m["op"] == "canonicalize":
        return None if m["column"] in cols else f"no column {m['column']}"
    if ident(m["column"]) in cols:
        return f"column {m['column']} exists"
    if not 2 <= len(m["values"]) <= 30:
        return f"{len(m['values'])} values"
    if m["scope"]["column"] and m["scope"]["column"] not in cols:
        return f"scope reads unknown column {m['scope']['column']}"
    for r in m["rules"] + ([m["scope"]] if m["scope"]["column"] else []):
        if r is m["scope"]:
            try:
                re.compile(r["regex"])
            except re.error as e:
                return f"bad scope regex {r['regex']!r}: {e}"
            continue
        if r["column"] != "_message" and r["column"] not in cols:
            return f"rule reads unknown column {r['column']}"
        if r["value"] not in m["values"]:
            return f"rule value {r['value']!r} is not one of the values"
        try:
            re.compile(r["regex"])
        except re.error as e:
            return f"bad regex {r['regex']!r}: {e}"
    return None


PLACEHOLDER = {"other", "unknown", "none", "n/a", "na", "misc", "unspecified"}
FREE_TEXT = re.compile(r"desc|note|detail|comment|summary|body")   # as the deterministic reader decides free text
PROBES = ("", "x", "qq zz", "12345", "Ω")


def catch_all(rule: dict, scoped: list[dict] | None = None, db=None) -> bool:
    """A rule that can't tell rows apart ((?s).* -> "meal", or category ^dining$ -> "other_dining" when every row in
    scope is dining) would decide every leftover row before association and Jev, which know better (a dinner
    place's "With Kuba" is a dinner). Such rules are dropped."""
    if all(re.search(rule["regex"], p) for p in PROBES):
        return True
    if not scoped:
        return False
    texts = [derived.message(db, r.get("_src")) if rule["column"] == "_message" else r.get(rule["column"]) for r in scoped]
    return sum(t is not None and re.search(rule["regex"], str(t)) is not None for t in texts) >= 0.98 * len(scoped)


def placeholder(value: str) -> bool:
    return str(value).lower() in PLACEHOLDER or str(value).lower().startswith(("other", "unknown", "misc"))


def duplicate_of(db, table: str, column: str, values: dict[int, str]) -> str | None:
    """An existing column whose values are the derived ones under the same names (then the new column adds nothing;
    a bijection onto new words, like currency -> city, does add a name questions use)."""
    rows = {r[0]: dict(r) for r in db.execute(f"SELECT * FROM '{table}'")}
    for c in rows[next(iter(rows))].keys() if rows else []:
        if c in ("id", "_src", "_ts", column):
            continue
        pairs = {(str(rows[i].get(c)), v) for i, v in values.items() if rows[i].get(c) is not None}
        same_words = all(derived.fold(a).strip() == derived.fold(b).strip() for a, b in pairs)   # a new name is not a copy
        if len(pairs) >= 2 and same_words and len({a for a, _ in pairs}) == len(pairs) == len({b for _, b in pairs}) \
                and sum(rows[i].get(c) is not None for i in values) >= 0.95 * len(values):
            return c
    return None


# @ref LLP 0003.000#what-had-to-be-learned — the verifier alone accepted 949 coffees filed under "drinks"
def merges(db, table: str, column: str, values: dict[int, str], min_rows: int = 10) -> str | None:
    """A derived value that lumps together values an existing small category column keeps apart (coffee and dining
    rows both called "drinks"): refining or renaming existing categories is fine, recombining them is not. Only checked
    against columns the derived one re-categorizes (most of its values come from one value of that column)."""
    rows = {r["id"]: dict(r) for r in db.execute(f"SELECT * FROM '{table}'")}
    if not rows:
        return None
    for c in next(iter(rows.values())):
        if c in ("id", "_src", "_ts", column) or c.endswith("_id") or FREE_TEXT.search(c):
            continue
        kinds = {r[c] for r in rows.values() if isinstance(r.get(c), str)}
        if not 2 <= len(kinds) <= 30:
            continue
        by_value: dict = {}
        for i, v in values.items():
            if isinstance(rows[i].get(c), str) and not placeholder(v):
                by_value.setdefault(v, Counter())[rows[i][c]] += 1
        big = [src for src in by_value.values() if sum(src.values()) >= min_rows]
        pure = [src for src in big if src.most_common(1)[0][1] >= 0.9 * sum(src.values())]
        if len(pure) < 0.5 * len(big):   # an orthogonal attribute (a city spans every category), not a re-categorization
            continue
        for v, src in by_value.items():
            n = sum(src.values())
            big = [(k, m) for k, m in src.most_common() if m >= 0.05 * n]
            if n >= min_rows and src.most_common(1)[0][1] < 0.9 * n and len(big) >= 2:
                return f"{column}={v!r} merges values that {c} keeps apart: " + ", ".join(f"{c}={k!r} ({m})" for k, m in big)
    return None


def health(reader, out: dict) -> float:
    """How far a deployed system would trust an answer: the verifier's score (0 without an answer), minus penalties
    for matching inside free text and for filtering on one spelling of a value stored in several."""
    if out["answer"] == "I don't know.":
        return 0.0
    from lab.systems.query_log import has_variants
    dims = reader.meta.get(out.get("table"), {}).get("dims", {})
    free = any(dims.get(d, {}).get("free_text") for d in out.get("filters") or {})
    varied = any(has_variants(reader, out.get("table"), d, v) for d, v in (out.get("filters") or {}).items())
    return (out.get("verified") or 0.0) - (0.25 if free else 0.0) - (0.25 if varied else 0.0)


def replay(path: Path, questions: list[dict], user: str, now) -> list[float]:
    from concurrent.futures import ThreadPoolExecutor
    from lab.systems.det_reader import DetReader
    reader = DetReader(str(path), user, now)
    with ThreadPoolExecutor(6) as pool:
        return list(pool.map(lambda q: health(reader, reader.ask(q["question"])), questions))


def run(eng, queries: list[dict], model: str, ts: str, user: str, now, rounds: int = 3, **kw) -> dict:
    """Up to `rounds` rounds of propose, test, apply. Each round sees only the flagged questions no kept migration
    serves yet (on the schema as it is now), and the reasons earlier proposals were rejected."""
    report, feedback, prompt_chars, served = [], "", 0, set()
    for rnd in range(rounds):
        todo = [q for q in queries if q["qid"] not in served]
        if len(flagged(todo)) < 2:
            break
        proposals, prompt = propose(eng, todo, model, feedback=feedback)
        prompt_chars += len(prompt)
        got = test_and_apply(eng, proposals, todo, ts, user, now, **kw)
        report += [{"round": rnd, **e} for e in got]
        served |= {q for e in got if e["kept"] for q in e["serves"]}
        rejected = [e for e in got if not e["kept"]]
        if not proposals:
            break
        feedback = "\n".join(f"- {e['op']} {e['table']}.{e['column']}: {e['reason']}" + (
            "; rows no rule decided, e.g. " + "; ".join(e.get("undecided", [])[:5]) if e.get("undecided") else "")
            for e in rejected)
    return {"flagged": len(flagged(queries)), "served": sorted(served), "migrations": report, "prompt_chars": prompt_chars}


# @ref LLP 0003#migrations [implements] — every guard here answers a failure seen in round 5
def test_and_apply(eng, proposals: list[dict], queries: list[dict], ts: str, user: str, now, min_agree: float = 0.85,
                   min_cover: float = 0.7, min_gain: float = 0.05) -> list[dict]:
    derived.ensure(eng.db)
    qids = {q["qid"] for q in flagged(queries)}
    by_id = {q["qid"]: q for q in queries}
    path = Path(eng.path)
    scratch = path.with_name(path.stem + ".scratch.sqlite")
    report = []
    for m in proposals:
        entry = {"op": m["op"], "table": m["table"], "column": m["column"], "serves": m["serves"], "why": m["why"]}
        err = validate(eng, m, qids)
        if err:
            report.append({**entry, "kept": False, "reason": err})
            continue
        eng.db.commit()
        shutil.copy(path, scratch)
        from lab.systems.engine import Engine
        trial = Engine(str(scratch), strict=True, normalize=True)
        reason = None
        if m["op"] == "derive_column":
            scope = m["scope"] if m["scope"]["column"] else None
            scoped = [dict(r) for r in trial.db.execute(f"SELECT * FROM '{m['table']}'") if derived.in_scope({"scope": scope}, dict(r))]
            entry["dropped_rules"] = [r for r in m["rules"] if catch_all(r, scoped, trial.db)]
            spec = {"table": m["table"], "column": ident(m["column"]), "description": m["description"], "values": m["values"],
                    "rules": [r for r in m["rules"] if r not in entry["dropped_rules"]], "scope": scope,
                    # the free-text columns it reads stop being filters: the clean column answers those questions
                    "supersedes": sorted({r["column"] for r in m["rules"] if FREE_TEXT.search(r["column"])})}
            d = derived.decide(trial.db, m["table"], spec)
            st = d["stats"]
            left = [r for r in trial.db.execute(f"SELECT * FROM '{m['table']}'") if r["id"] not in d["values"]
                    or placeholder(d["values"][r["id"]])][:5]
            entry["undecided"] = [derived._row_text(dict(r), spec["column"])[:140] for r in left]
            real = sum(n for v, n in st["distribution"].items() if not placeholder(v))
            st["rows"] = st["in_scope"]   # coverage counts the rows the column applies to
            dup = duplicate_of(trial.db, m["table"], spec["column"], d["values"])
            mixed = merges(trial.db, m["table"], spec["column"], d["values"])
            agree = st.get("spot_check", {}).get("agree", 0.0)
            if dup:
                reason = f"duplicates column {dup}"
            elif mixed:
                reason = mixed
            elif real < min_cover * st["rows"]:
                reason = f"decides a real value for {real / st['rows']:.0%} of rows (< {min_cover:.0%})"
            elif agree < min_agree:
                reason = f"rules agree with Jev on {agree:.0%} of a sample (< {min_agree:.0%})"
            else:
                derived.write(trial, m["table"], spec, d["values"])
            entry.update({"spec": spec, "stats": st})
        else:
            st = derived.canonicalize(trial, m["table"], m["column"])
            reason = None if st["merged"] else "nothing to merge"
            entry["stats"] = st
        trial.db.close()
        if reason is None:   # test-driven: the questions it claims to serve must get healthier
            served = [by_id[q] for q in m["serves"] if q in by_id]
            before, after = replay(path, served, user, now), replay(scratch, served, user, now)
            gain = statistics.mean(a - b for a, b in zip(after, before))
            entry["replay"] = {"questions": len(served), "health_before": round(statistics.mean(before), 3),
                               "health_after": round(statistics.mean(after), 3), "gain": round(gain, 3),
                               "worse": sum(a < b - 0.2 for a, b in zip(after, before)),
                               "per_question": [{"q": q["question"], "before": round(b, 3), "after": round(a, 3)}
                                                for q, b, a in zip(served, before, after)]}
            if gain < min_gain:
                reason = f"replaying the {len(served)} questions it serves: health {gain:+.2f} (needs +{min_gain})"
        entry["kept"], entry["reason"] = reason is None, reason
        if entry["kept"]:   # the scratch copy passed: it becomes the database
            eng.db.close()
            shutil.copy(scratch, path)
            eng.db = __import__("sqlite3").connect(str(path), check_same_thread=False)
            eng.db.row_factory = __import__("sqlite3").Row
            eng.derived = derived.specs(eng.db)
        scratch.unlink(missing_ok=True)
        if entry["kept"]:
            eng.db.execute("INSERT INTO _migrations (ts, op, table_name, column_name, spec, evidence, stats) VALUES (?, ?, ?, ?, ?, ?, ?)",
                           (ts, m["op"], m["table"], entry["column"], json.dumps(entry.get("spec") or {}, ensure_ascii=False),
                            json.dumps(m["serves"]), json.dumps(entry["stats"], ensure_ascii=False)))
            eng.db.commit()
        report.append(entry)
    return report
