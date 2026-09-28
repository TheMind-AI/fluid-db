# LLP 0010: Interfaces

**Type:** Explainer
**Status:** Draft
**Systems:** Core, Reads
**Author:** Adam Zvada / Claude
**Date:** 2026-09-25
**Related:** LLP 0006, LLP 0011

## Summary

The structure has three kinds of consumers: code, agents and people.
- **What each needs:** code needs SQL; agents need tools; people need interfaces.
- **The catalog is self-describing** (a description for every table and column; links resolved to names), so each
  interface can be generated rather than designed. [confirmed] (Adam Zvada, 2026-09-24): "it would be queryable with
  regular stuff and we could like visualize actually the content … creating some novel interfaces".

## SQL

The database is a SQLite file. Apps and dashboards query it read-only (`Engine.query` opens a read-only connection):
<1 ms at personal scale, and exact.

## MCP server

`deprecated/python/lab/mcp_server.py` exposes FluidDB over MCP stdio, so any agent can use it as memory:
- `remember`
- `ask`
- `sql`
- `schema`
- `explore`

Claude Code: `claude mcp add fluiddb -- …/.venv/bin/python …/deprecated/python/lab/mcp_server.py --db ~/fluiddb/memory.sqlite --user
"…"`. `deprecated/python/lab/mcp_demo.py` runs a full session through it (§19). It has not yet been evaluated with an agent in the loop
(LLP 0011).

## Generated explorer

`deprecated/python/lab/explorer.py` builds one static HTML page from any FluidDB database, with no knowledge of its schema:
- **How it works:** it reads the catalog, resolves links to names, and asks Jev how each table is best shown:
  timeline, cards, bars, list, or links.
- **It renders:**
  - a life timeline across tables
  - people cards gathering everything linked to each person
  - spending per currency
  - a schema map
  - every table in its chosen view
- **Jev's choices** were sensible and their confidence tracked ambiguity: 0.95-1.00 for timelines and links, 0.37-0.51
  for genuinely ambiguous tables (§11).
- **Open questions:** the explorer shows structure but not what the user asks, and it can't take corrections. Both are
  proposed in LLP 0011.

## Generated app

`deprecated/python/lab/appgen.py` generates the app from three sources:
- **The query log.** Recurring measures, sliced the ways the user slices them, become tiles.
- **The schema.** Person cards, the most frequent values per year and category, cross-tabs, and the timeline.
- **The confidence table.** The inbox of doubtful rows and undecided person pairs, each with a suggested fix.

How it's built:
- **One LLM call lays out the pages.** It refers to views by id only.
- **Code computes every number,** and every tile shows its SQL.
- **Rendering follows the dataviz rules** found in round 6: the number of series chooses the form, and each unit gets
  its own axis.

Results are in LLP 0012.000. The static app records inbox answers in the browser; applying them live needs a local
server.

## Terminal

`deprecated/python/lab/fluid.py`: type facts, `?question`, `.schema`. A quick way to try a database by hand.
