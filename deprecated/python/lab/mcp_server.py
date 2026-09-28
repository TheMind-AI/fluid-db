"""FluidDB as an MCP server: give any agent a self-organizing, queryable memory.

Add it to Claude Code (any MCP client works the same way):
  claude mcp add fluiddb -- /path/to/repo/.venv/bin/python /path/to/repo/lab/mcp_server.py \\
      --db ~/fluiddb/memory.sqlite --user "Adam Zvada"

Tools: remember, ask, sql, schema, explore. Model keys come from the repo's .env.
"""
# @ref LLP 0010#mcp-server — FluidDB as memory for any MCP agent
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import logging  # noqa: E402

from mcp.server.mcpserver import MCPServer  # noqa: E402

from lab.common.llm import chat  # noqa: E402
from lab.systems.base import ANSWER_PROMPT  # noqa: E402
from lab.systems.fluid_v2 import FluidV2  # noqa: E402

ARGS = argparse.Namespace(db=str(Path.home() / "fluiddb" / "memory.sqlite"), user="the user", model="openai:gpt-6-luna")
STATE: dict = {}

server = MCPServer(
    "fluiddb",
    log_level="WARNING",
    instructions=(
        "FluidDB is the user's long-term memory: a SQLite database that organizes itself. Call `remember` with the "
        "user's own words whenever they share something worth keeping (facts, plans, purchases, contacts, changes, "
        "or a request to forget something). Call `ask` to recall anything about the user's life before answering from "
        "guesswork. Use `schema` + `sql` for exact counts, sums and lists."
    ),
)


def db() -> FluidV2:
    if "system" not in STATE:
        Path(ARGS.db).parent.mkdir(parents=True, exist_ok=True)
        STATE["system"] = FluidV2(ARGS.model, ARGS.db, ARGS.user, variant="v23")
    return STATE["system"]


@server.tool()
def remember(text: str) -> str:
    """Store what the user just told you, in their own words: facts, events, plans, purchases, contacts,
    corrections ("actually it's ..."), or "forget X" requests. Forwarded emails/receipts can be passed as-is:
    they can add data but never overwrite or delete it. Returns what changed in the database."""
    now = datetime.now().replace(microsecond=0).isoformat()
    trace = db().remember({"id": "mcp", "ts": now, "text": text})
    if trace.get("skipped"):
        return f"Nothing to store (looks like {trace['gate']['intent']})."
    lines = [trace.get("notes", "")]
    for op in trace.get("ops", []):
        vals = {v["column"]: v["value"] for v in op.get("values") or []}
        target = f"{op['table']}#{op['row_id']}" if op.get("row_id") else op["table"]
        lines.append(f"- {op['op']} {target} {json.dumps(vals, ensure_ascii=False) if vals else ''}".rstrip())
    if trace.get("blocked"):
        lines.append(f"- blocked {len(trace['blocked'])} change(s) coming from a document (documents can't overwrite or delete)")
    if trace.get("errors"):
        lines.append(f"- {len(trace['errors'])} operation(s) failed: " + "; ".join(e["error"] for e in trace["errors"]))
    return "\n".join(lines)


@server.tool()
def ask(question: str) -> str:
    """Answer a question about the user from memory (current state, history of changes, and recent messages).
    Says "I don't know." when the memory has no answer."""
    system = db()
    now = datetime.now()
    log = system.engine.db.execute("SELECT ts, text FROM _log ORDER BY id DESC LIMIT 60").fetchall()
    recent = "\n".join(f"[{ts[:16].replace('T', ' ')}] {text}" for ts, text in reversed(log))
    data = (f"STRUCTURED DATABASE (current state, links shown as names; _history has earlier values):\n"
            f"{system.engine.readable_text()}\n\n"
            f"RECENT MESSAGES (newest last):\n{recent}")
    prompt = ANSWER_PROMPT.format(user=ARGS.user, now=now.strftime("%Y-%m-%d %H:%M"), weekday=now.strftime("%A"),
                                  data=data, question=question)
    return chat(ARGS.model, [{"role": "user", "content": prompt}], tag="mcp:ask", max_tokens=4000, cache=False)[0].strip()


@server.tool()
def sql(query: str) -> str:
    """Run a read-only SQL SELECT on the memory database and return up to 200 rows as JSON. Call `schema` first."""
    rows, err = db().engine.query(query)
    return f"ERROR: {err}" if err else json.dumps(rows, ensure_ascii=False, default=str)


@server.tool()
def schema() -> str:
    """Every table and column with the description FluidDB wrote for it, plus row counts and example rows."""
    return db().engine.catalog(samples=2)


@server.tool()
def explore() -> str:
    """Generate an HTML page that shows the memory (timeline, people, spending, schema) and return its path."""
    from lab.explorer import DB, render
    out = Path(ARGS.db).with_suffix(".html")
    out.write_text(render(DB(ARGS.db), ARGS.user, ARGS.db))
    return str(out)


def main():
    logging.getLogger("httpx").setLevel(logging.WARNING)
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=ARGS.db)
    ap.add_argument("--user", default=ARGS.user)
    ap.add_argument("--model", default=ARGS.model)
    ap.parse_args(namespace=ARGS)
    server.run()


if __name__ == "__main__":
    main()
