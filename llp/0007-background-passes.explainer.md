# LLP 0007: Background passes

**Type:** Explainer
**Status:** Draft
**Systems:** Writes, Schema
**Author:** Adam Zvada / Claude
**Date:** 2026-09-25
**Related:** LLP 0004, LLP 0005, LLP 0003, LLP 0002.001, LLP 0002.002

## Summary

**Writes can be slow, so the expensive work happens after the message is stored.** None of these passes adds a model
call per message:
- they run nightly, or when a trigger fires
- each runs on the database file
- each records its changes in `_history`

[observed] §22 "Writes can be slow, so do the work there".

## Sleep

Offline consolidation (`deprecated/python/lab/systems/consolidate.py`, `deprecated/python/lab/bench/sleep.py`) does three things:
1. **Recover dropped facts.** Every logged message the gate didn't skip must leave a trace: a row whose `_src` lists
   it, or a history entry. Messages without one are re-planned.
2. **Move misplaced rows.** When the same kind of thing ended up in two tables, a model proposes typed migrations and
   the engine applies them with history.
3. **Dedupe** (below).

On the year, sleep turned the best database into the one used for deterministic reads (§14, §21).

## Dedupe

- **Code proposes look-alike entity pairs;** in small entity tables it compares every pair, with context.
- **Jev answers "same real-world entity?"** It merges at p ≥ 0.8: every link is repointed, empty fields are filled,
  and the merge is recorded.
- **Planned:** pairs at 0.5-0.8 should become questions for the user ("is Dave David Mokos?").
- **At 5k it merged "Tom" into "Tom Novak" at 0.91.** Dave/David/David Mokos scored 0.58-0.72; they are one person,
  and a first name alone can't settle it. [observed] §24, `consolidate.dedupe`.

## Supersession

- **For two rows about the same person in a state table** (contacts, memberships, jobs, leases, but not occurrences
  like meetings or purchases), Jev decides whether the newer row replaces the older. It also sees the message that
  recorded the newer row.
- **Replaced rows get `valid_to`;** nothing is deleted. Readers prefer current rows.
- **On the year it caught all 4 real replacements,** with 1 debatable false positive, for ~$0.0002. [observed] §22,
  `deprecated/python/lab/systems/supersede.py`.

## Typed columns

- **One LLM call per text column that holds numbers** compiles regex extractors: numbers, clock times to seconds, or
  ranks on an ordinal scale (6b+ < 6c < 7a).
- **Code then fills typed columns** for every row, now and on every later write.
- **"Best 10k time" and "hardest grade"** went from unanswerable to exact. [observed] §22, `deprecated/python/lab/systems/typing.py`.

## Forget propagation

- **After the planner's forget:**
  1. collect every value that only the erased rows held (names, emails, phone numbers, codes, old values)
  2. scrub those values from every remaining row, history entry and log line
- **It fully erased 8 of 8 requests** in 5-20 ms with no model. Nothing else was lost.
  - The planner alone erased 3 of 8.
  - A Jev audit erased 6-7 of 8 at ~3¢ each, and over-flagged.
  - [observed] §18, §22, `deprecated/python/lab/bench/forget.py` `propagate_forget`.

## Schema from queries

- **What it does:** derived columns and canonical values, proposed from the query log and tested on a scratch copy
  before they are applied.
- **Where it's specified:** LLP 0003; results in LLP 0003.000.

## Scheduling

| pass | trigger |
|---|---|
| writer recompile | the share of messages reaching tier C rises for a table (drift) |
| sleep (recover, move, dedupe) | nightly |
| supersession, typed columns | nightly, or when a state table changes |
| forget propagation | right after every forget |
| schema advisor | `low_trust` rises in the query log, or nightly |

[inferred] The triggers other than "after every forget" are proposals; only fixed schedules were run.
