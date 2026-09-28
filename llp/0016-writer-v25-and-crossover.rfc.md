# LLP 0016: Writer v2.5, and when a memory beats reading everything

**Type:** RFC
**Status:** Draft
**Systems:** Writes, Reads, Research
**Author:** Adam Zvada / Claude
**Date:** 2026-09-25
**Related:** LLP 0014.000, LLP 0013.000, LLP 0005, LLP 0015, LLP 0011

## Summary

Round 8 left two open questions:
1. **Do its fixes, combined, make the write path general and cheap?**
2. **Where is the crossover between reading the whole history and using the memory?**
   - Round 8: full context wins by 9 points at 5-21k tokens.
   - Round 7: the memory wins by 13 points at 122k tokens.

[confirmed] (Adam Zvada, 2026-09-25): "continue with experiments, testing … do you feel like we are getting into a
state like how the memory system, this database should work?"

## Part A: writer v2.5

v2.5 is v2.4 plus two things (`deprecated/python/lab/systems/fluid_v2.py`, `deprecated/python/lab/systems/compiled_writer.py`):
1. **The gate worded for any domain** (LLP 0014.000#writes). The personal gate dropped 56 of 166 support tickets.
2. **Compiled updates.** Tier A learns from the operations the planner applied, not from the rows as they are
   later:
   - **Insert examples** also come from rows that later messages updated (tickets, deals).
   - **Update patterns** come from messages whose only operation updated one row. A pattern captures the key that
     identifies the row ("#1087" → `ticket_number`; a company name → the deal linked to it) and the new values.
   - At run time, code finds exactly one row by the key, or hands the message to the planner.
   - Validation reuses the insert tests: on the examples a pattern matches, ≥ 90% must hit the planner's row with the
     planner's key values, and it must match almost none of the other messages.

**Test:** the support desk re-ingested with v2.5, compared with v2.3 (LLP 0014.000).

## Part B: the crossover

- **The same questions** at three history lengths, on one person's life (LLP 0009 simulator):
  - the last 6 months (1,023 messages, ~26k tokens)
  - the last 12 months (2,030, ~51k)
  - all 30 months (5,050, ~122k)
- **The 36 questions** (3 per memory type) are all about the last 6 months, so their gold is the same in every
  window (`deprecated/python/lab/datasets/crossover.py`).
- **Memory per window:**
  - 6 and 12 months: a database ingested from the window alone, with the round-4 writer (v2.3), bootstrap 120.
  - 30 months: the round-4 database.
- **Configs:** `full_context`, `facts_log`, `brain_jev_recall`, `brain_all`.

## Hypotheses

These were written before the runs.
- **H1 (v2.5 fixes the support desk):** write accuracy ≥ 92% (v2.3: 82.6%), with no new-ticket message skipped.
- **H2 (compiled updates):**
  - Tier A writes ≥ 40% of the support messages after the bootstrap (v2.3: 0%).
  - Its writes are ≥ 95% correct.
  - Ingestion costs ≥ 30% less than v2.3's $0.55.
- **H3 (answers follow the writes):** on the support questions, `facts_log` improves by ≥ 5 points over v2.3's 75.0%,
  mostly on aggregates and current state.
- **H4 (the crossover exists):**
  - `full_context` ≥ `brain_all` at 6 months.
  - `brain_all` ≥ `full_context` + 5 at 30 months.
  - The crossover lies between 26k and 122k tokens.
- **H5 (the memory is flat):** `brain_all` changes by < 5 points across the three windows, while `full_context`
  drops by ≥ 10 from 6 to 30 months.

## Protocol and budget

- **Support v2.5:** `scenarios ingest support@v25`, then writes and memory with every config.
- **Windows:** `scenarios ingest life6m` and `life12m` (v2.3); `life30m` copies the round-4 database. Then memory with
  the four configs.
- **Budget:** ≤ $3, with guards: $0.45 per ingestion and $0.6 per memory run. Everything is cached.

## Risks

- **GPT-6 Luna at low effort is the only answerer.** A stronger model moves the crossover, probably later.
- **The 30-month database had extra passes** that the window databases don't: round 4's writer used bootstrap 400.
  The comparison within each window (full context vs memory) is unaffected.
- **Update patterns can write to the wrong row** when a key is reused. The row must be unique, or the planner decides.
