"""Does FluidDB (and Jev) work in Czech?

Translates life_stream (messages + questions) and the gate benchmark's messages into casual Czech
with GPT-6 Luna (names, numbers, emails, dates kept exactly), then:
  * writes lab/datasets/life_stream_cs.json for run_e2e (gold answers stay in English; the judge compares meaning)
  * runs the Jev write gate on the Czech messages and compares with English

  .venv/bin/python -m lab.bench.czech translate
  .venv/bin/python -m lab.run_e2e --system v21jev --model openai:gpt-6-luna --dataset life_stream_cs
  .venv/bin/python -m lab.bench.czech gate
"""
# @ref LLP 0002.000 — a round-1 bench
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor

from lab.bench.components import binary, gate_items
from lab.common.llm import LAB_DIR, chat_json
from lab.systems import jev_layer

TR_SCHEMA = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"], "additionalProperties": False}
TR = """Translate this message into natural, casual Czech, the way a Czech person would text their personal assistant.
Keep every name, number, amount, currency, date, time, email address, phone number and URL exactly as written.
Keep the same meaning and tone; do not add anything. Return only the translation.

MESSAGE:
{text}"""


def tr(text: str) -> str:
    return chat_json("openai:gpt-6-luna", TR.format(text=text), TR_SCHEMA, schema_name="translation",
                     tag="cs:translate", effort="low", max_tokens=4000)[0]["text"]


def translate_value(v):
    if isinstance(v, str):
        return tr(v)
    out = dict(v)
    for k in ("subject", "body", "note", "met_at", "role"):
        if isinstance(out.get(k), str):
            out[k] = tr(out[k])
    return out


def translate():
    ds = json.loads((LAB_DIR / "datasets" / "life_stream.json").read_text())
    with ThreadPoolExecutor(8) as pool:
        texts = list(pool.map(lambda m: translate_value(m["text"]), ds["messages"]))
        questions = list(pool.map(lambda q: tr(q["question"]), ds["qa"]))
    cs = {**ds, "name": "life_stream_cs", "description": ds["description"] + " (Czech translation)",
          "messages": [{**m, "text": t} for m, t in zip(ds["messages"], texts)],
          "qa": [{**q, "question": t} for q, t in zip(ds["qa"], questions)]}
    (LAB_DIR / "datasets" / "life_stream_cs.json").write_text(json.dumps(cs, indent=1, ensure_ascii=False))
    for m in cs["messages"][:6]:
        print(m["id"], m["text"] if isinstance(m["text"], str) else json.dumps(m["text"], ensure_ascii=False)[:150])


def gate():
    items = gate_items()
    with ThreadPoolExecutor(8) as pool:
        cs_texts = list(pool.map(lambda it: tr(it["text"]), items))
        en = list(pool.map(lambda it: jev_layer.gate(it["text"], it["ts"], tag="bench:gate:jev"), items))
        cs = list(pool.map(lambda x: jev_layer.gate(x[1], x[0]["ts"], tag="cs:gate"), zip(items, cs_texts)))
    gold = [it["write"] for it in items]
    report = {
        "english": {"write": binary([r["write"] for r in en], gold),
                    "intent_accuracy": round(sum(r["intent"] == it["intent"] for r, it in zip(en, items)) / len(items), 4)},
        "czech": {"write": binary([r["write"] for r in cs], gold),
                  "intent_accuracy": round(sum(r["intent"] == it["intent"] for r, it in zip(cs, items)) / len(items), 4),
                  "mean_confidence": round(sum(r["confidence"] for r in cs) / len(cs), 3)},
        "czech_errors": [{"cs": t, "gold": it["intent"], "pred": r["intent"], "write": r["write"]}
                         for t, it, r in zip(cs_texts, items, cs) if r["write"] != it["write"]],
    }
    report["english"]["mean_confidence"] = round(sum(r["confidence"] for r in en) / len(en), 3)
    (LAB_DIR / "results" / "bench_czech_gate.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))
    print(json.dumps({k: v for k, v in report.items() if k != "czech_errors"}, indent=1))
    for e in report["czech_errors"]:
        print("  ", e)


if __name__ == "__main__":
    {"translate": translate, "gate": gate}[sys.argv[1]]()
