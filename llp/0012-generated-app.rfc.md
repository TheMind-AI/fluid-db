# LLP 0012: The database proposes its own app, and knows what it doubts

**Type:** RFC
**Status:** Draft
**Systems:** Interfaces, Reads, Writes, Research
**Author:** Adam Zvada / Claude
**Date:** 2026-09-25
**Revised:** 2026-09-25 (Design updated to what was built: suggested fixes, concept keys, chart forms, one file per page;
Hypotheses and Protocol unchanged; results in LLP 0012.000)
**Related:** LLP 0011, LLP 0010, LLP 0003, LLP 0007, LLP 0012.000

## Summary

Round 6 runs experiments E1 and E2 of the agenda (LLP 0011) together, on the round-5 database.
- **Every stored row gets a confidence.** It comes from checks in code and from Jev reading the row next to the
  message it came from.
- **The least-confident rows, and unclear duplicates, go to an inbox for the user.** The user's answers are written
  back as ordinary logged changes.
- **The same database generates its own app.** Dashboard tiles are derived from the questions the user actually asks
  (the query log). Entity pages and breakdowns come from the schema. One LLM call lays it out, and code computes
  every number.

[confirmed] (Adam Zvada, 2026-09-25): "it can generate the UI out of it because if you have a structure and you can
fetch the structure, then you can build on top of a UI"; the user asked to start round 6 and to "go test and
experiment in depth".

## Motivation

- **The data's remaining errors are silent.** 163 of 4,521 checked rows are wrong on the round-5 database (3.6%):
  - 123 dates, mostly "tonight" sent after midnight
  - 52 companions, wrong or not linked
  - a few others

  Nothing flags them; they surface only as wrong totals. [observed] `deprecated/python/lab/runs/workload/db_opt.sqlite` against the
  simulator's truth.
- **The generated explorer (LLP 0010) shows structure,** but not what the user asks, and it can't take corrections.
- **A structure that knows its doubts,** and an interface that asks about them, close the loop: the database gets
  better from a few answers instead of from more model calls. [inferred]

## Design

### Confidence

Every row that came from a message gets signals.

**From code** (free):
- a late-night message whose row carries the calendar day
- a missing date
- a stored value that has spelling variants, or that carries chatter ("Note:", "fyi")
- a person the message names who isn't linked
- a linked person the message doesn't name
- an amount that isn't in the message
- an amount far outside the merchant's usual range

These are written from round 4's error analysis, so they are in-sample (see [Risks](#risks)).

**From Jev** (generic, untuned): three yes/no questions per row. It sees the row, with links shown as names, next to
its source message and the time it was sent:
- is the row faithful to the message?
- is the date the day the event happened?
- are the people right?

Rows are sent 8 per request.

**Combined:** Jev's lowest probability, halved for every code signal. Stored in `_confidence`, one row per data row.

### Inbox

The inbox asks the user about:
- **The least-confident rows:** "you said … — we stored … — is that right?", with the suspicious field highlighted.
- **Person pairs the dedupe pass left at 0.5-0.8** (LLP 0007#dedupe).

Every row item carries a suggested fix, written by code:
- a late-night date becomes the evening before
- a person who isn't linked, or is linked wrongly, becomes the one the message names; an exact stored name beats a
  first-name match

So the user usually confirms instead of typing. The user's answers go into `_log` as messages. They are applied as
typed updates or merges through the engine, with `_history`, like any other change.

### Tiles

Tiles are derived from the query log:
1. **Cluster trusted plans** (verifier ≥ 0.7) by shape: the table, operation, column, set of filter columns, and
   time grain.
2. **Turn each shape into a pivot, by code:** the filter values that vary become the series, and the period becomes
   the x-axis (month or year).
   - Example: "how much did I spend on lunch in March 2025" → spend per month by `dining_occasion`, one chart per
     currency.
3. **No model writes SQL.** Every tile's SQL comes from a trusted plan's shape.

**Concept keys.** When a plan picks the same value in two columns, the reader treats it as one concept that either
column may match. Tiles do the same: `category|item`, with a UNION in the SQL.

### Generic views

From the schema alone:
- **Entity pages:** for each person, totals, counts and the last date of every linked table with amounts, broken
  down by that table's small category columns.
- **Breakdowns:** the top values of large categorical columns (merchants), per year and per category.
- **The timeline:** from the explorer.

### Layout

- **One LLM call** (GPT-6 Luna) receives the list of views, with previews of their numbers. It returns the pages,
  the order, the titles and the descriptions.
- **It can only reference views by id.** It never writes queries or numbers.
- **Rendering:** a static HTML app, one file per page, built with the explorer's validated palette.
  - The number of series chooses the form: up to 4 get lines; more get ranked bars (top 10).
  - Series of different scales get a panel each.
  - Levels use a zoomed axis; totals and counts keep a zero baseline.
  - Every chart has hover titles and a per-year table.

### Write-back

An answered inbox item is applied at once:
- the tiles and pages read the corrected rows
- `_derived` columns are re-derived by the engine
- the answer and the change are both on record (`_log`, `_history`)

## Hypotheses

These were written before the experiment ran. Results go in LLP 0012.000.

- **H1 (calibration, combined signals).**
  - Wrong rows are separated from right ones with AUROC ≥ 0.85.
  - Reviewing the 5% least-confident rows (≈ 226) catches ≥ 50% of the 163 wrong rows. A random review catches
    about 5%.
- **H2 (Jev alone).** Untuned, it catches ≥ 30% of wrong rows in its least-confident 5%.
- **H3 (the inbox fixes the data).** An oracle user who answers the top 100 inbox rows truthfully fixes ≥ 60 wrong
  rows: the wrong-row rate falls from 3.6% to ≤ 2.3%.
- **H4 (tiles from the log).**
  - Every tile's SQL runs.
  - ≥ 75% of the 80 future questions of logged shapes (groups `same` and `reworded`, LLP 0003) can be read off a
    generated tile exactly: an oracle lookup of the question's parameters, graded like round 5.
  - Misses are counted separately as "no tile" or "tile present, value wrong".
- **H5 (generality).** Run on the year database of rounds 2-3 with no code change:
  - the generator produces valid tiles from that database's own trusted plans
  - it answers ≥ 50% of the year's aggregation questions correctly by plan lookup
- **H6 (cost).**
  - Confidence scoring ≤ $0.15 for about 4.6k rows.
  - Layout ≤ $0.05.
  - Building the app ≤ 10 s.
  - The round ≤ $1.50.

## Protocol

- **Data.** A copy of `deprecated/python/lab/runs/workload/db_opt.sqlite` (round 5, final) in `deprecated/python/lab/runs/app/`.
- **Truth.** The simulator's per-message truth, mapped to rows through `_src`. Strings are compared without accents
  or case, and the simulator's late-night convention (the evening before) is kept.
- **Confidence strategies compared on the same rows:**
  - random
  - planner rows first
  - code signals only
  - Jev only
  - combined

  Metrics: AUROC, and the share of wrong rows caught when reviewing 1, 2, 5 and 10%.
- **Inbox.** For each strategy and N ∈ {25, 50, 100, 200}, an oracle user corrects the shown rows from the truth. It
  also answers the person-pair questions.
  - Measured: wrong rows remaining, and the tiles' agreement with gold.
  - For the combined strategy at N = 100, the future questions are also re-asked through the deterministic reader.
- **App.**
  - Tiles come from the trusted plans of the 80 logged questions, re-asked on the evolved database (cached).
  - The app is rendered and screenshotted with headless Chrome.
  - It is evaluated by oracle lookup and by plan lookup on the 105 future questions.
- **Generality.** The year database (v2.3, Luna low, after sleep), with its 60 questions.
- **Budget.** A guard in code; ≤ $1.50.

## Risks

- **The code signals are fitted to known errors.** They come from round 4's error analysis of this same data. Jev's
  numbers (H2) are the untuned baseline; a new domain (E3) is the real test.
- **The oracle user is perfect.** Real users answer some items wrongly or not at all, so H3 is an upper bound on what
  the inbox achieves.
- **Tile coverage measures whether the right number exists,** not whether a person finds it. The plan lookup and the
  screenshots are proxies; the user's own review is the real test.
- **Privacy.** The inbox shows the user their own data only. `_confidence` is derived data and is deleted with its
  row.
