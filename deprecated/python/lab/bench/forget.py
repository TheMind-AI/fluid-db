"""Does "forget X" really erase X?  Planner-only forgetting vs planner + a Jev audit, on a finished year database.

  .venv/bin/python -m lab.bench.forget --variant v23 --effort low

Each request runs on a fresh copy of the year database (end of the year). Ground truth comes from the simulator:
`needles` must be gone from every row, every _history entry and the raw _log afterwards; `keeps` must still be in the
rows (nothing else of value was destroyed).

  planner      FluidDB as is: the planner emits forget ops (row delete + redact terms), the engine applies them.
  planner+propagate  then deterministic propagation: values only the erased rows held (names, emails, phone numbers,
               codes, old values) are scrubbed from every other row, history entry and log line. No model.
  planner+jev  then Jev reads every row, history entry and log line against the request (one small request each);
               flagged rows get a per-field check (whole row erased only when its identity or every field is flagged,
               else just the flagged fields), flagged history entries are dropped, flagged log lines are rewritten by
               the LLM with [forgotten] in place of the erased details.
"""
# @ref LLP 0002.001 — a round-2 bench
from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import time
from datetime import datetime

from lab.bench.year import DS, run_dir
from lab.common import jev
from lab.common.llm import LAB_DIR, LEDGER, chat
from lab.systems.engine import Engine
from lab.systems.fluid_v2 import FluidV2

REQUESTS = [
    {"text": "forget Tom's phone numbers, both the old one and the new one", "needles": ["777 123 456", "777 999 000"],
     "keeps": ["Tom", "733 544 390", "606 111 222"]},
    {"text": "please delete everything about Oscar Lind from memory", "needles": ["Oscar", "oscar@nebula.ai"],
     "keeps": ["Nina Rossi", "Eva Green"]},
    {"text": "forget David's email address", "needles": ["david@nebula.ai"], "keeps": ["733 544 390", "David"]},
    {"text": "erase my old Czech phone number, I don't use it anymore", "needles": ["722 238 738"],
     "keeps": ["415 555 0199", "733 544 390", "606 111 222"]},
    {"text": "forget everything about Raj", "needles": ["Raj", "raj.gupta@gmail.com"], "keeps": ["Lucas Meyer", "Sarah"]},
    {"text": "delete all my Blue Bottle purchases", "needles": ["Blue Bottle"], "keeps": ["Kavárna Místo"]},
    {"text": "forget the passport renewal, it's done and I don't want it stored", "needles": ["passport"],
     "keeps": ["wedding venue"]},
    {"text": "forget Lucas Meyer's email", "needles": ["lucas@sequoia.com"], "keeps": ["Lucas Meyer"]},
]

AUDIT = {"contains": jev.noul("Does `record` contain information that `request` asks to forget (the thing itself or a "
                              "detail of it)? Records that merely resemble it, or that the request does not cover, do not count.")}
REWRITE = """The user asked their memory to forget something. Rewrite the stored message so that it no longer contains any
of the information to forget: replace exactly those details with [forgotten] and keep everything else word for word.
Return only the rewritten message.

REQUEST: {request}
MESSAGE: {message}"""


def pattern(needle: str) -> re.Pattern:
    if re.fullmatch(r"[\d ]+", needle):  # phone numbers: any spacing or dashes between the digits
        return re.compile(r"[\s\-]?".join(needle.replace(" ", "")))
    if "@" in needle:
        return re.compile(re.escape(needle), re.I)
    return re.compile(rf"\b{re.escape(needle)}\b", re.I)


def records(db: sqlite3.Connection) -> list[tuple[str, str, object, str]]:
    """(kind, table, key, text) for every row, history entry and log line."""
    out = []
    for (t,) in db.execute("SELECT name FROM _tables"):
        cols = [r[1] for r in db.execute(f"PRAGMA table_info('{t}')")]
        for row in db.execute(f"SELECT * FROM '{t}'"):
            d = {c: v for c, v in zip(cols, row) if v is not None and c != "_src"}
            out.append(("row", t, d.get("id"), json.dumps(d, ensure_ascii=False)))
    for rid, t, row_id, col, old, new in db.execute(
            "SELECT rowid, table_name, row_id, column_name, old_value, new_value FROM _history"):
        out.append(("history", t, rid, f"{t}#{row_id}.{col}: {old!r} -> {new!r}"))
    for lid, ts, text in db.execute("SELECT id, ts, text FROM _log"):
        out.append(("log", "_log", lid, f"[{ts[:16]}] {text}"))
    return out


def audit_records(eng) -> list[tuple[str, str, object, str]]:
    """What Jev reads: rows as in the readable view (links shown as names, so it can tell whose phone a row holds),
    history entries with their row's context, and log lines."""
    out, view = [], {}
    for line in eng.readable_text().splitlines():
        m = re.match(r"(\w+)#(\d+) (\{.*)", line)
        if m and not line.startswith(("update ", "delete ")):
            view[(m[1], int(m[2]))] = line
            out.append(("row", m[1], int(m[2]), line))
    for rid, t, row_id, col, old, new in eng.db.execute(
            "SELECT rowid, table_name, row_id, column_name, old_value, new_value FROM _history"):
        out.append(("history", t, rid, f"{view.get((t, row_id), f'{t}#{row_id}')} | earlier {col}: {old!r} -> {new!r}"))
    for lid, ts, text in eng.db.execute("SELECT id, ts, text FROM _log"):
        out.append(("log", "_log", lid, f"[{ts[:16]}] {text}"))
    return out


def measure(path, req) -> dict:
    db = sqlite3.connect(path)
    recs = records(db)
    pats = [pattern(n) for n in req["needles"]]
    leaks = {"row": 0, "history": 0, "log": 0}
    for kind, _, _, text in recs:
        if any(p.search(text) for p in pats):
            leaks[kind] += 1
    rows_text = "\n".join(text for kind, _, _, text in recs if kind == "row")
    kept = [k for k in req["keeps"] if pattern(k).search(rows_text)]
    n_rows = sum(1 for r in recs if r[0] == "row")
    db.close()
    return {"leaks": leaks, "kept": len(kept), "keeps": len(req["keeps"]), "rows": n_rows}


IDENT = re.compile(r"@|\+?\d[\d \-]{6,}\d|\b(?=[A-Za-z]*\d)(?=\d*[A-Za-z])[A-Za-z\d]{5,}\b")  # email, phone, code


def _needle_pattern(v: str) -> re.Pattern:
    digits = re.sub(r"\D", "", v)
    if len(digits) >= 7 and not re.search(r"[A-Za-z@]", v):  # phone numbers: any spacing between digits
        return re.compile(r"\+?" + r"[\s\-]?".join(digits[-9:]))
    return re.compile(rf"(?<!\w){re.escape(v)}(?!\w)", re.I)


# @ref LLP 0007#forget-propagation — complete when every value only the erased rows held is gone
def propagate_forget(eng, before: sqlite3.Connection) -> dict:
    """Deterministic erasure: every identifying value that only the erased rows held (names/titles, emails, phone
    numbers, codes, and the old values in those rows' history) is scrubbed from all remaining rows, history entries
    and log lines. No model involved."""
    after_ids = {t: {r[0] for r in eng.db.execute(f"SELECT id FROM '{t}'")} for t in eng.tables()}
    erased: list[tuple[str, dict]] = []
    for t in eng.tables():
        cols = [r[1] for r in before.execute(f"PRAGMA table_info('{t}')")]
        for row in before.execute(f"SELECT * FROM '{t}'"):
            d = dict(zip(cols, row))
            if d["id"] not in after_ids[t]:
                erased.append((t, d))
    # rows left pointing at an erased row only existed to describe it (a forgotten person's phone): erase them too
    gone = {(t, d["id"]) for t, d in erased}
    for t in eng.tables():
        for row in eng.db.execute(f"SELECT * FROM '{t}'").fetchall():
            d = dict(zip([c[0] for c in eng.db.execute(f"SELECT * FROM '{t}' LIMIT 0").description], row))
            for c, v in d.items():
                target = eng.target_table(c, eng.tables())
                if target and v is not None and (target, int(v) if str(v).isdigit() else v) in gone:
                    eng.db.execute(f"DELETE FROM '{t}' WHERE id = ?", (d["id"],))
                    erased.append((t, d))
                    break
    values: set[str] = set()
    for t, d in erased:
        label = next((c for c in Engine.LABEL_COLS if d.get(c)), None)
        for c, v in d.items():
            if isinstance(v, str) and c not in ("_src", "_ts") and len(v) >= 3 and \
                    (c == label or IDENT.search(v) or v[:1].isupper()):
                values.add(v.strip())
        for old, new in before.execute("SELECT old_value, new_value FROM _history WHERE table_name = ? AND row_id = ?",
                                       (t, d["id"])):
            for v in (old, new):
                if isinstance(v, str) and IDENT.search(v):
                    values.add(v.strip())
    # a person's first name also identifies them in the log, unless another remaining person shares it
    people_left = [r[0] for r in eng.db.execute("SELECT name FROM people")] if "people" in eng.tables() else []
    for t, d in erased:
        if t == "people" and d.get("name") and " " in d["name"]:
            first = d["name"].split()[0]
            if len(first) >= 3 and not any(p and p.split()[0] == first for p in people_left):
                values.add(first)
    # values that remaining rows still hold are not unique to what was erased: leave them alone
    remaining_text = "\n".join(json.dumps([list(r) for r in eng.db.execute(f"SELECT * FROM '{t}'")], ensure_ascii=False)
                                for t in eng.tables())
    pats = [_needle_pattern(v) for v in sorted(values) if not _needle_pattern(v).search(remaining_text)]
    done = {"erased_rows": len(erased), "values": len(pats), "log_redacted": 0, "history_dropped": 0}
    for lid, text in eng.db.execute("SELECT id, text FROM _log").fetchall():
        new = text
        for p in pats:
            new = p.sub("[forgotten]", new)
        if new != text:
            eng.db.execute("UPDATE _log SET text = ? WHERE id = ?", (new, lid))
            done["log_redacted"] += 1
    for hid, old, new in eng.db.execute("SELECT rowid, old_value, new_value FROM _history").fetchall():
        if any(p.search(str(old)) or p.search(str(new)) for p in pats):
            eng.db.execute("DELETE FROM _history WHERE rowid = ?", (hid,))
            done["history_dropped"] += 1
    eng.db.commit()
    return done


def jev_audit(system: FluidV2, request: str, model: str) -> dict:
    eng, t0 = system.engine, time.time()
    recs = audit_records(eng)
    who = f"{system.user} (the user; \"I\", \"me\" and \"my\" in the request mean {system.user})"
    answers = jev.ask_many([({"user": who, "request": request, "record": text}, AUDIT) for _, _, _, text in recs],
                           workers=48, tag="forget:audit")
    flagged = [r for r, a in zip(recs, answers) if a["contains"]["noul"] >= 0.5]
    done = {"rows_deleted": 0, "fields_cleared": 0, "history_dropped": 0, "log_rewritten": 0, "flagged": len(flagged)}
    for kind, table, key, text in flagged:
        if kind == "row":
            row = json.loads(text.split(" ", 1)[1])
            actual = {c["name"] for c in eng.columns(table)}
            column = {k: k if k in actual else f"{k}_id" for k in row if k in actual or f"{k}_id" in actual}
            fields = [k for k in column if not k.startswith("_")]
            qs = {c: jev.noul(f"Is the `{c}` value of `record` information that `request` asks to forget?") for c in fields}
            a = jev.ask({"user": who, "request": request, "record": row}, qs, tag="forget:fields")
            hit = [c for c in fields if a[c]["noul"] >= 0.5]
            identity = [c for c in fields if c in ("name", "title", "full_name", "merchant", "person", "description")]
            if hit and (set(hit) >= set(fields) or any(c in hit for c in identity)):
                eng.apply([{"op": "forget", "table": table, "row_id": key, "redact": []}], 0, datetime.now().isoformat())
                done["rows_deleted"] += 1
            elif hit:
                eng.db.execute(f"UPDATE '{table}' SET {', '.join(f'{column[c]} = NULL' for c in hit)} WHERE id = ?", (key,))
                done["fields_cleared"] += len(hit)
        elif kind == "history":
            eng.db.execute("DELETE FROM _history WHERE rowid = ?", (key,))
            done["history_dropped"] += 1
        else:
            msg = eng.db.execute("SELECT text FROM _log WHERE id = ?", (key,)).fetchone()[0]
            new, _ = chat(model, [{"role": "user", "content": REWRITE.format(request=request, message=msg)}],
                          tag="forget:rewrite", effort="none", max_tokens=2000)
            eng.db.execute("UPDATE _log SET text = ? WHERE id = ?", (new.strip(), key))
            done["log_rewritten"] += 1
    eng.db.commit()
    done["records_checked"] = len(recs)
    done["audit_s"] = round(time.time() - t0, 1)
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", default="v23")
    ap.add_argument("--effort", default="low")
    ap.add_argument("--model", default="openai:gpt-6-luna")
    ap.add_argument("--systems", nargs="+", default=["planner", "planner+jev", "planner+propagate"])
    args = ap.parse_args()
    ds = json.loads(DS.read_text())
    src = run_dir(args.effort, args.variant, args.model.split(":")[1]) / "db.sqlite"
    work = LAB_DIR / "runs" / "forget"
    work.mkdir(parents=True, exist_ok=True)
    now = "2026-09-30T21:30:00"
    report = []
    for i, req in enumerate(REQUESTS):
        row = {"request": req["text"], "before": measure(src, req)}
        for system_name in args.systems:
            path = work / f"{args.variant}_{args.effort}_{i}_{system_name.replace('+', '_')}.sqlite"
            shutil.copy(src, path)
            before = sqlite3.connect(src)
            system = FluidV2(args.model, str(path), ds["user"], variant=args.variant, effort=args.effort)
            n0 = len(LEDGER.calls)
            t0 = time.time()
            trace = system.remember({"id": f"forget{i}", "ts": now, "text": req["text"]})
            system.engine.db.commit()
            out = {"ops": [{k: o.get(k) for k in ("op", "table", "row_id", "redact")} for o in trace.get("ops") or []],
                   "write_s": round(time.time() - t0, 2)}
            if system_name == "planner+jev":
                out["audit"] = jev_audit(system, req["text"], args.model)
            if system_name == "planner+propagate":
                t1 = time.time()
                out["audit"] = {**propagate_forget(system.engine, before), "propagate_s": round(time.time() - t1, 3)}
            before.close()
            system.engine.db.close()
            out["after"] = measure(path, req)
            out["cost"] = round(sum(c.cost for c in LEDGER.calls[n0:]), 5)
            row[system_name] = out
            a = out["after"]
            print(f"[{i}] {system_name:12s} leaks={a['leaks']} kept={a['kept']}/{a['keeps']} rows {row['before']['rows']}->{a['rows']} "
                  f"{out.get('audit', '')}", flush=True)
        report.append(row)
    summary = {}
    for s in args.systems:
        leaks = [r[s]["after"]["leaks"] for r in report]
        summary[s] = {"requests_fully_erased": sum(1 for l in leaks if not any(l.values())),
                      "requests_erased_from_rows": sum(1 for l in leaks if l["row"] == 0 and l["history"] == 0),
                      "leaking_records": {k: sum(l[k] for l in leaks) for k in ("row", "history", "log")},
                      "keeps_kept": f"{sum(r[s]['after']['kept'] for r in report)}/{sum(r[s]['after']['keeps'] for r in report)}",
                      "cost": round(sum(r[s]["cost"] for r in report), 4)}
    before_total = {k: sum(r["before"]["leaks"][k] for r in report) for k in ("row", "history", "log")}
    col = collateral(args.variant, args.effort) if "planner+jev" in args.systems else {"changed": None, "collateral": None}
    if "planner+jev" in args.systems:
        summary["planner+jev"]["audit_changed_records"] = col["changed"]
        summary["planner+jev"]["audit_collateral_records"] = col["collateral"]
    if "planner+propagate" in args.systems:
        prop = collateral(args.variant, args.effort, "planner_propagate")
        summary["planner+propagate"]["changed_records"] = prop["changed"]
        summary["planner+propagate"]["collateral_records"] = prop["collateral"]
    out = {"db": str(src.relative_to(LAB_DIR)), "records_with_needles_before": before_total, "summary": summary,
           "collateral": col, "requests": report}
    (LAB_DIR / "results" / f"forget_{args.variant}_{args.effort}.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(json.dumps({"before": before_total, **summary}, indent=1))



def collateral(variant: str = "v23", effort: str = "low", system: str = "planner_jev") -> dict:
    """Precision of the audit: records it changed or removed (vs planner-only) that held no needle at all."""
    work, out = LAB_DIR / "runs" / "forget", []
    for i, req in enumerate(REQUESTS):
        a = work / f"{variant}_{effort}_{i}_planner.sqlite"
        b = work / f"{variant}_{effort}_{i}_{system}.sqlite"
        if not (a.exists() and b.exists()):
            continue
        pats = [pattern(n) for n in req["needles"]]
        ra = {(k, t, key): txt for k, t, key, txt in audit_records(Engine(str(a), strict=True, normalize=variant == "v23"))}
        rb = {(k, t, key): txt for k, t, key, txt in audit_records(Engine(str(b), strict=True, normalize=variant == "v23"))}
        changed = [(key, ra[key], rb.get(key)) for key in ra if ra[key] != rb.get(key)]
        wrong = [(key, before, after) for key, before, after in changed if not any(p.search(before) for p in pats)]
        out.append({"request": req["text"], "changed": len(changed), "collateral": len(wrong),
                    "examples": [f"{k[0]} {k[1]}#{k[2]}: {bf[:140]} => {(af or '<removed>')[:140]}" for k, bf, af in wrong[:4]]})
    return {"changed": sum(r["changed"] for r in out), "collateral": sum(r["collateral"] for r in out), "requests": out}


if __name__ == "__main__":
    main()
