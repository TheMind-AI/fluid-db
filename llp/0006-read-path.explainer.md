# LLP 0006: The read path

**Type:** Explainer
**Status:** Draft
**Systems:** Reads, Jev
**Author:** Adam Zvada / Claude
**Date:** 2026-09-25
**Related:** LLP 0003, LLP 0004, LLP 0008, LLP 0002.002, LLP 0002.003

## Summary

How a question becomes an answer. Reads should be as fast as possible. [confirmed] (Adam Zvada, 2026-09-25):
"lowest latency".

```
app / dashboard ──> plain SQL                                                  <1 ms
question ──> compiled question template (recurring shapes, LLP 0003)          ~5 ms, no model
         ──> deterministic reader: Jev fills a closed query form -> SQL + code -> Jev verifies    ~1 s, $0.0004
         └─ verifier < 0.7 ──> LLM tool agent (SQL, row lookup, log search)                         ~4 s, $0.0012
every question ──> _queries (LLP 0004#query-log)
```

## Plain SQL

- **The database is a real SQLite database with a described catalog,** so apps query it directly.
- **Measured:** plain SQL answered 95% of 10 hand-written questions in under 1 ms (§8), and 10 of 11 of the year's
  aggregate questions exactly (§14). [observed]

## Semantic layer

The deterministic reader (`deprecated/python/lab/systems/det_reader.py`) profiles the database with SQL alone. It redoes this when the
schema changes. For every table:
- **The time column.**
- **Number columns.**
- **Filter dimensions:**
  - links to other tables, shown by name
  - small numeric columns
  - short or categorical text columns, flagged free-text (descriptions) or sparse (mostly empty)
- **The label column of small tables.**

Columns a derived column supersedes are not offered as filters (LLP 0003).

## Routing

- **Stage 1, one Jev request:** the table, the operation and the period.
- **Stage 2, in parallel:** the answer column, plus a value or "any" for every dimension. It runs speculatively for
  the tables BM25 ranks highest.
- **Every option is a value the database contains,** so Jev never writes one.
- **Stored values the question names word for word** ("Field", "Sightglass", "Kuba") are passed to stage 1 with the
  table and column that store them, and those tables are asked in stage 2.
  - Without this, "How many visits to Field?" was routed to `events`: a table card shows only a few example values.
  - [observed] LLP 0003.000, `DetReader._mentions`.

## Operations

- **Supported:** count, sum (per currency), avg, max, min, latest (rows without `valid_to` first), previous (falls
  back to `_history`), earliest, next, list, profile.
- **Periods:** each month, each year, "since <month>", and upcoming.
- **A count counts the rows that match the filters,** whichever answer column Jev picked. Restricting it to rows
  where that column was filled gave 7 visits to a café instead of 55. [observed] LLP 0003.000.

## Relaxation

When nothing matches, the plan is relaxed one step at a time, and every step is recorded:
- **A lookup may drop its period, the implicit "me" filter, and kind filters.**
- **Totals and counts only drop "me":** a sum over loosened filters is silently wrong.
- **Nothing ever drops a filter on a specific person, thing or title.** An early version did, and answered "What's
  Ondra's email?" with Priya's. [observed] §21, `DetReader.execute`.

## Verifier

- **Candidates:** answers from Jev's top two tables, plus variants of a total or count with one kind filter dropped.
- **One Jev question checks each:** "does `result`, computed by `query`, answer `question`?"
- **The verifier sees the plan in words,** not just the result. "124.70 USD (6 rows)" looks fine alone; "total where
  description = 'Ride'" shows that rows are missing. [observed] §25.

## Hybrid

- **The deterministic answer is used when the verifier gives ≥ 0.7;** otherwise the LLM agent answers.
- **Result:** it matches the agent's accuracy (81.9% vs 80.6% at 5k; 82.5% vs 81.7% on the year) and answers 57-64%
  of questions in about a second.
- **The threshold was chosen on the same questions.** 0.5 let too many wrong answers through at 5k. [observed] §25.

## Agent reader

- **GPT-6 Luna with tools,** one step at a time:
  - `sql` (read-only)
  - `lookup` (readable rows, with links shown as names)
  - `search_log` (BM25 over the raw log)
  - `answer`
- **Its view:** the catalog with known values.
- **Speed:** 3.2-3.7 s at the median.
- **A GPT-6 Sol agent scored 92.5% on the year at 9k tokens.** Spending on the reader beat spending on the writer
  (§14). [observed] `deprecated/python/lab/systems/reader.py`.

## Earlier readers

- **The whole database (and the raw log) in one prompt:** 93.8% at 1.2 s on the 7-week stream; the best at small
  scale (§8, §13).
- **Jev picking rows** for the LLM (§5).
- **Jev routing to compiled SQL templates:** 0.3 s. It covers the 7-week stream (60% at 96%) but not a year (half at
  72%, §19). The closed query form replaced it.
