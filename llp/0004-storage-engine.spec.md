# LLP 0004: The storage engine

**Type:** Spec
**Status:** Draft
**Systems:** Engine
**Author:** Adam Zvada / Claude
**Date:** 2026-09-25
**Related:** LLP 0000, LLP 0005, LLP 0007, LLP 0003

## Summary

What must be true of a FluidDB database file, whoever writes to it: the planner, compiled patterns, asynchronous
passes, or a migration.
- **One SQLite file per memory.** It holds the user's tables and a few system tables that start with `_`.
- **The engine is deterministic.** Models never touch the file directly: they return typed operations, and the
  engine validates and applies them (`deprecated/python/lab/systems/engine.py`).

## Raw log

- **Every input is appended to `_log` (id, ts, text) before anything else happens,** and the append returns
  immediately. [observed] `Engine.log`, `FluidV2.remember`.
- **`_log` is the source of truth.** The user's tables are a view built from it and may be wrong or incomplete.
  - Anything can be re-derived from the log: replays in the sleep pass, backfills of new columns (LLP 0003).
  - Reads may include the unprocessed tail of the log.
  - [observed] §3 "a raw log, so nothing is lost"; §10.
- **The only edit allowed to `_log` is redaction by `forget`.**

## Typed operations

The write vocabulary is a closed set of operations (`deprecated/python/lab/systems/fluid_v2.py` `OP`):
- `create_table`
- `add_columns`
- `insert` (with an optional `ref`, so later operations in the same batch can point at the new row)
- `update`
- `delete`
- `forget`

The engine rules:
- **It never runs model-written SQL on the write path.** [observed] In round 1 the 2024 pipeline let the model
  write SQL, and 10 writes failed (NOT NULL traps, quoting). v2 has zero SQL errors by construction. §2-3.
- **Names are made safe identifiers.** Table and column names are normalized, and names starting with `_` or `sqlite`
  are rewritten.
- **Unknown columns are created on insert,** with a type inferred from the value.
- **Operations apply in order.** A failing operation is reported, not fatal; the planner gets one repair round
  (LLP 0005).

## Catalog

- **`_tables` and `_columns` hold a description** of every table and column. The planner must give one when it
  creates them.
- **The catalog is how models see the database.** It lists every table, column, type and description; with
  `normalize` on, it also lists the existing values of short text columns, which is how spellings get reused.
  [observed] `Engine.catalog`.

## Provenance

- **Every row has `_src`,** the ids of the `_log` messages that created or changed it, and `_ts`, the time of its
  last change.
- **Provenance is what makes forgetting complete and backfills possible.** Every row can be traced to its words.
  [observed] §22, LLP 0003.

## History

With `strict` on (v2.2 and later):
- **Every overwritten value and every deleted row goes to `_history`** (table, row, column, old, new, ts, log id,
  op).
- **Readers can answer "what was X before" from it.** The planner simply writes the new value.
- **Merges and canonicalization record their changes here too,** so they can be reversed.

## Normalization

With `normalize` on (v2.3 and later):
- currency columns hold ISO codes (Kč → CZK, $ → USD)
- `@ref` values resolve only to rows inserted earlier in the same batch; otherwise the operation fails with an
  explanation the planner can act on

## Forget

`forget` is the privacy operation:
- **Delete the row with no history.**
- **Cascade** to rows that exist only to describe it: rows whose `*_id` points at it.
- **Redact** the given terms from `_log` and `_history`.

Planner forgets alone are incomplete. Completeness is enforced by the propagation pass (LLP 0007#forget-propagation).
[observed] §18, §22.

**The cascade goes too far for work data** (round 9, LLP 0016.000).
- "Rows whose `*_id` points at it" includes records that belong to someone else. Erasing a support contact under
  GDPR deleted the 19 tickets he had opened, which are the company's records.
- **Proposed:** cascade only into rows that describe the person (contact methods, relationships, preferences). In
  rows that merely reference the person, null the link and redact the name.

**On real data it deleted the user** (round 10, LLP 0017.000).
- A request to forget the person's own first name became a forget of the user's own row. The cascade took the 54
  rows linked to it: every interest and career plan.
- **The round-9 proposal wouldn't have saved them:** interests do "describe the person". The problem is upstream:
  - A request about an attribute should clear the value, not forget the row.
  - The memory's subject should never be forgotten by one message.
- **Implemented as an option:** `Engine(forget_links="redact")` nulls the link in every linked row and keeps the row
  (writer `v25r`). Removal of rows that only describe the forgotten thing is left to the verified background pass
  (LLP 0007#forget-propagation), where the LLM proposes and Jev confirms (LLP 0013.000).
  - A replay from the cache kept the 54 rows.
  - The default stays `cascade`, so earlier rounds reproduce.

## Derived columns

Columns added by the schema advisor (LLP 0003):
- **The spec is stored in `_derived`,** and the migration is recorded in `_migrations` with the questions that
  justified it and its stats.
- **On every insert into such a table,** the engine fills the derived column by code (rules, then association).
  Rows it can't decide wait for the asynchronous pass.

## Query log

- **`_queries` records every question the database answers:** plan, path, verifier score, signals (LLP 0003).
- **It is personal data like `_log`.** `forget` must purge matching entries (not implemented yet).

## Invariants

1. Every input is logged before it is processed, and the log is only ever redacted.
2. Models never write SQL to the database; only the engine and code-validated migrations change it.
3. Every user row can be traced to `_log` through `_src`.
4. With `strict`, no value disappears without a `_history` entry, except through `forget`.
5. A forgotten value is gone from rows, history and log; LLP 0007 checks this.
6. System tables start with `_` and are never shown to models as user tables.
