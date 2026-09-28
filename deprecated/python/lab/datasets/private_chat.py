"""A person's chats with an assistant -> a lab dataset (LLP 0017). Real data: input and output stay in a private,
git-ignored directory; nothing from it enters the tracked lab.

Each window is up to `size` of the person's consecutive messages in one chat. Every message carries the end of the
assistant's preceding turn ("Mind asked: …"), because answers like "yes, mostly at night" mean nothing alone.

  .venv/bin/python -m lab.datasets.private_chat EXPORT.json OUT_DIR --user "Name" [--size 6] [--cuts CUT:END,...]
"""
# @ref LLP 0017#data — windows with the assistant's question, private paths only
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

PROBES = [
    ("themes", "What are the main things I have been working on with you, over the whole time?"),
    ("people", "Who are the important people in my life, and what is going on with each of them?"),
    ("practices", "What practices or techniques have I tried, and how did each of them work for me?"),
    ("follow_up", "Is there anything I said I would do, or planned to try, that we never followed up on?"),
    ("mood", "How have my mood and energy changed over the time we have talked?"),
    ("preferences", "What do I like or dislike about how you talk with me?"),
    ("start", "What was going on in my life when I first started talking with you?"),
    ("recent", "What did we talk about in our most recent conversations?"),
    ("return", "I'm back after a long break. What should you remember before we continue?"),
    ("patterns", "Are there situations or triggers that keep coming back for me?"),
    ("work", "What is going on with my work, and how has it changed?"),
    ("strengths", "What has helped me most when things were hard?"),
]


def last_question(text: str, limit: int = 160) -> str:
    parts = re.split(r"(?<=[.?!])\s+", text.strip())
    q = next((p for p in reversed(parts) if p.endswith("?")), parts[-1] if parts else "")
    return q[-limit:]


def convert(export: dict, user: str, size: int) -> dict:
    by_chat = defaultdict(list)
    for m in export["messages"]:
        by_chat[m["chat"]].append(m)
    windows = []
    for chat, msgs in by_chat.items():
        msgs.sort(key=lambda m: m["at"])
        asked, lines, start = "", [], None
        for m in msgs:
            if m["role"] == "assistant":
                asked = last_question(m["text"])
                continue
            if m["role"] != "user":
                continue
            start = start or m["at"]
            lines.append(f"[{m['at'][11:16]}] " + (f"(Mind asked: {asked}) " if asked else "") + m["text"][:4000])
            asked = ""
            if len(lines) >= size:
                windows.append((start, lines))
                lines, start = [], None
        if lines:
            windows.append((start, lines))
    windows.sort(key=lambda w: w[0])
    messages = [{"id": f"w{i + 1:04d}", "ts": start[:19], "kind": "store",
                 "text": f"From a conversation with Mind on {start[:10]}:\n" + "\n".join(lines)}
                for i, (start, lines) in enumerate(windows)]
    last = max(m["at"] for m in export["messages"])
    return {"name": "scenario_own", "user": user, "description": "A person's own conversations with an assistant (private)",
            "now": last[:19], "messages": messages,
            "qa": [{"id": f"p{i + 1:02d}", "type": k, "question": q, "answer": ""} for i, (k, q) in enumerate(PROBES)]}


GENERIC = "We're starting a new conversation. In at most 250 words: what should you remember about me before we continue?"


def brief(opening: str) -> str:
    return (f"I'm starting a new conversation with you. My first message is: «{opening[:1500]}» In at most 250 words: "
            "what do you remember from our earlier conversations that is relevant to this, or worth keeping in mind "
            "as we talk?")


# @ref LLP 0017#part-b-coming-back — the history before the cut, the chats after it; only their first messages in the queries
def split(export: dict, ds: dict, cut: str, end: str) -> tuple[dict, list[dict]]:
    """The history before `cut` as its own dataset, whose questions are the briefings, and the chats in [cut, end)."""
    by_chat = defaultdict(list)
    for m in export["messages"]:
        if m["role"] in ("user", "assistant"):
            by_chat[m["chat"]].append(m)
    later = sorted((msgs for msgs in by_chat.values() if cut <= min(m["at"] for m in msgs) < end),
                   key=lambda msgs: min(m["at"] for m in msgs))
    chats = []
    for n, msgs in enumerate(later):
        msgs.sort(key=lambda m: m["at"])
        turns, asked, questions = [], "", []
        for m in msgs:
            if m["role"] == "assistant":
                asked = last_question(m["text"])
                if asked.endswith("?"):
                    questions.append(asked)
            else:
                turns.append({"asked": asked, "text": m["text"][:4000]})
                asked = ""
        if turns:
            chats.append({"id": f"r{n + 1:02d}", "start": msgs[0]["at"][:19], "opening": turns[0]["text"],
                          "turns": turns[1:], "questions": questions})
    pre = dict(ds, name=f"scenario_own{cut[2:4]}{cut[5:7]}", now=chats[0]["start"],
               messages=[m for m in ds["messages"] if m["ts"] < cut],
               qa=[{"id": c["id"], "type": "brief", "question": brief(c["opening"]), "answer": ""} for c in chats]
               + [{"id": "r00", "type": "generic", "question": GENERIC, "answer": ""}])
    return pre, chats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("export")
    ap.add_argument("out_dir")
    ap.add_argument("--user", required=True)
    ap.add_argument("--size", type=int, default=6)
    ap.add_argument("--cuts", help="CUT:END,... also write, per cut, the history before it (scenario_ownYYMM.json) and "
                                   "the chats from CUT to END (return_chats_ownYYMM.json)")
    a = ap.parse_args()
    export = json.loads(Path(a.export).read_text())
    ds = convert(export, a.user, a.size)
    out = Path(a.out_dir) / "scenario_own.json"
    out.write_text(json.dumps(ds, ensure_ascii=False, indent=1))
    chars = sum(len(m["text"]) for m in ds["messages"])
    print(f"{len(ds['messages'])} windows, {chars} characters, {len(ds['qa'])} probes -> {out}")
    for spec in (a.cuts or "").split(",") if a.cuts else []:
        cut, end = spec.split(":")
        pre, chats = split(export, ds, cut, end)
        name = pre["name"].removeprefix("scenario_")
        (Path(a.out_dir) / f"scenario_{name}.json").write_text(json.dumps(pre, ensure_ascii=False, indent=1))
        (Path(a.out_dir) / f"return_chats_{name}.json").write_text(json.dumps(chats, ensure_ascii=False, indent=1))
        print(f"{name}: {len(pre['messages'])} windows before {cut} ({sum(len(m['text']) for m in pre['messages'])} chars); "
              f"{len(chats)} chats to {end}, {sum(len(c['turns']) for c in chats)} later messages, "
              f"{sum(len(c['questions']) for c in chats)} questions")


if __name__ == "__main__":
    main()
