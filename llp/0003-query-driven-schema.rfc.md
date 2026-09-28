# LLP 0003: A schema shaped by its queries

**Type:** RFC
**Status:** Draft
**Systems:** Schema, Reads, Writes, Research
**Author:** Adam Zvada / Claude
**Date:** 2026-09-25
**Revised:** 2026-09-25 (Design updated to what was built and tested in round 5; Hypotheses and Protocol unchanged;
results in LLP 0003.000)
**Related:** LLP 0000, LLP 0001, LLP 0002, LLP 0003.000

## Summary

So far FluidDB's schema is shaped only by what gets **written**: the planner creates tables and columns as messages
arrive. This RFC shapes it by what gets **asked** as well:
- **Log every question** in the database itself (`_queries`), with the plan that answered it and how far to trust it.
- **Evolve the schema from that log**, asynchronously:
  - derive the columns that questions keep needing (a clean `kind` where lunch, dinner and drinks share `dining`)
  - canonicalize the values they filter on ("Onesip" / "Onesip Coffee")
  - backfill from the raw message log, which FluidDB keeps for exactly this kind of replay
- **Compile recurring question shapes into code**, so they are answered in milliseconds without a model: the read-side
  twin of the compiled writer.

The idea is the user's: [confirmed] (Adam Zvada, 2026-09-25) "store the queries and then … optimize the SQL database
for the queries it does … the schema would be changing based on the needs."

## Motivation

- **The reader's weak point at scale is filter choice over messy columns, not SQL speed.** [observed]
  `deprecated/python/lab/REPORT.md` section 25 and `deprecated/python/lab/results/scale5k_reads.json` show this:
  - All 15 "drinks with Kuba" rows are stored with Kuba linked, but under `dining` next to 959 lunches and dinners.
    "Lunch/dinner/drinks" lives only in a free-text `description`.
  - "Most expensive dinner in San Francisco" failed because no column says where an expense happened.
  - Merchant variants ("Onesip", "Onesip Coffee", "Note: ☕ Onesip") split counts.
- **The write side can't know which distinctions matter; the questions can.** A planner that splits every concept
  bloats the schema, and one that doesn't loses questions. The query log tells which distinctions are worth a column.
  [inferred]
- **FluidDB can backfill what it never stored as a column.** Every row points to its source messages (`_src` →
  `_log`), so a new column can be computed from the original words. A classical database can add a column, but not
  the data that was never captured. [observed] `deprecated/python/lab/systems/engine.py`
- **Latency is dominated by model round trips, not SQL.** The deterministic reader spends ~0.8 s in Jev calls and
  milliseconds in SQL at 5k rows. [observed] `deprecated/python/lab/REPORT.md` section 21. So the latency lever is to skip the model
  for recurring questions, not to add indexes. [inferred until tested: see H4]

## Prior art

- **Physical design from a workload.** Index advisors (AutoAdmin; Chaudhuri & Narasayya, VLDB 1997), materialized-view
  selection (Agrawal et al., VLDB 2000), and self-driving databases (Pavlo et al., CIDR 2017) tune indexes and views
  to the logged queries. They change *physical* design only; the logical schema is taken as given.
- **Adaptive indexing, or database cracking** (Idreos, Kersten & Manegold, CIDR 2007). The data reorganizes itself
  as a side effect of the queries it serves. This is the closest in spirit: the structure follows the reads.
- **Query-driven data modeling** (Chebotko et al. 2015 for Cassandra; DynamoDB's access-pattern design). Designers
  shape the schema around known queries up front. Here the schema is shaped continuously, from queries observed.
- **Query logs for natural-language interfaces** (Templar; Baik, Jagadish & Li, SIGMOD 2019). SQL logs improve how
  words map to schema elements. Here the log changes the schema itself, not only the mapping.

What is new here, as far as we know: *logical* schema changes (new derived columns, canonical values) chosen from a
natural-language query log. They are decided by a model, applied and checked by code, and backfilled from a raw
message log. [inferred: no systematic literature search was done]

## Design

### Query log

- **`_queries`** holds one row per question:
  - the question and its time
  - the path that answered it (compiled, deterministic, agent)
  - the plan (table, operation, column, filters, period) and the answer
  - the verifier's score, signals, latency and cost
- **Privacy.** It is personal data like `_log`, so `forget` must purge it too (see [Risks](#risks)).

### Signals

The advisor never sees gold answers. It learns only from what a deployed system knows:
- `low_trust`: the verifier scored the plan below the threshold (0.7, LLP 0002).
- `no_answer`: the deterministic reader found nothing.
- `free_text`: the plan filtered on a free-text column (a concept buried in a description).
- `unmatched`: content words of the question that match no table, column or value.
- `relaxed`: filters had to be dropped to get an answer.
- `disagree`: when the agent ran, its numbers differ from the deterministic ones.
- `variants`: the plan filtered on one spelling of a value that the column also stores in other spellings. This is
  the only way to see a confident count that silently misses rows (LLP 0003.000).

### Migrations

One LLM call per round reads the flagged questions and the profile of the tables they touched:
- columns, with their top values and counts
- the spelling variants code finds
- sample rows, with their source messages

It proposes migrations from a closed set, each citing the logged questions it serves. Rounds (up to 3) continue on
the flagged questions no kept migration serves yet, with the reasons earlier proposals were rejected.

- **`derive_column`: a new column with a closed set of values**, over a declared `scope` (the rows it applies to;
  the rest stay empty). Filled in this order (first decision wins):
  1. Ordered regex rules over existing columns or the source message (`_message`). This is code. A rule that matches
     ≥ 98% of the rows in scope can't tell rows apart, so it is dropped.
  2. Association: the value that at least 80% of already-decided rows sharing the same merchant or place have. This
     is code.
  3. A Jev Choice among the values, reading the row and its source message, only for the rows left over.
  4. Association again, now with Jev's decisions as evidence too.

  The new column `supersedes` the free-text columns its rules read: they stop being filters in the reader's forms,
  or the reader keeps using the old path.
- **`canonicalize`: merge the variants of a value.**
  - Code proposes merge pairs: the same text up to accents and case, or one value's words contained whole in
    another's.
  - Jev confirms each pair (merge at p ≥ 0.8), reading the user's messages behind each spelling.
  - The most common spelling that isn't all caps wins.
  - Every change is written to `_history`.

**Validation.** Every migration is tried on a scratch copy of the database, and rejected if:
- a regex doesn't compile or reads a column that doesn't exist
- the new column is an existing column under the same names
- it lumps together values that an existing category column keeps apart (checked only against columns it
  re-categorizes)
- it decides a real value (not "other…" or "unknown") for < 70% of the rows in its scope
- Jev, labelling a random sample of the rule-decided rows on its own, agrees with the rules on < 85%
- the questions it claims to serve, replayed before and after, don't get healthier by ≥ 0.05

Health is the verifier's score (0 with no answer), minus 0.25 for a free-text filter and 0.25 for a filter on one of
several spellings.

Kept migrations replace the database and go to `_migrations`, with their evidence (query ids) and stats.

### Backfill from the log

Rules and Jev may read `_message`, the text of the row's source messages (`_src` → `_log`). This is what makes a
migration possible for facts that were said but never stored as a column.

### Write-time

- **Derivation specs are stored in `_derived`.** The engine applies the code parts (rules, association) on every
  insert, so new rows conform immediately.
- **Rows the code can't decide stay empty** until the asynchronous pass asks Jev. Writes stay deterministic.

### Compiled questions

This is the read-side twin of the compiled writer (LLP 0002).
- **Compile.** One LLM call reads the questions whose plans the verifier trusted (score ≥ 0.7). It writes templates:
  a full-match regex with named groups, the table, operation and column, and filters filled from a group or a
  constant. Periods are captured as text and parsed by code ("in March 2025", "last month").
- **Write general patterns.** The model is told to write patterns like `in \w+ \d{4}` and `(?P<merchant>.+?)`, never
  the values it saw.
- **Validate.** Code keeps a template only if it reproduces the logged answer (the same plan, or one with the same
  result) on at least 2 trusted questions, and on every trusted question it matches.
- **Captured names.** A captured name must be a stored value or part of exactly one ("Onesip" → "Onesip Coffee"). A
  stored value inside a longer capture doesn't count, because the capture swallowed more ("Kantýna in July 2024").
- **Nicknames.** Group texts that differ from stored values ("Uber and Bolt rides" → `transport`) are learned from
  the examples, and must map consistently.
- **At read time,** a matching template runs as SQL and code in milliseconds, with no model and no verifier.
  Anything unmatched goes through the Jev reader, as before.

### Physical design

Indexes on the (table, column) pairs the log filters on most, chosen by counting, with no model.
- **Expected:** no measurable effect at 5k rows, where Jev dominates; an effect at ≥ 1M rows once filters run in
  SQL.
- **This is a side experiment** (H4), not part of the migration loop.

## Hypotheses

These were written before the experiment ran; results go in LLP 0003.000 and are judged against these thresholds.

- **H1 (logical schema helps).** On future questions of logged shapes (groups `same` and `reworded`), the migrated
  schema raises the deterministic reader's accuracy by ≥ 10 points and the hybrid's by ≥ 5 points. It must not lose
  more than 2 points on novel shapes or on the 72 scale questions.
- **H2 (compiled questions).** Compiled templates answer ≥ 50% of the `same` group in < 20 ms, at least as accurately
  as the deterministic reader on those questions. For `reworded`, near 0% is expected: that measures how sensitive
  templates are to phrasing.
- **H3 (cheap and label-free).** The whole optimization (advisor, backfill, compile) costs < $0.10 and uses no gold.
- **H4 (physical design is secondary).** Indexes change read latency by < 5% at 5k rows.

## Protocol

- **Data:** the 5,050-message database after the async passes (`deprecated/python/lab/runs/scale5k/db_async.sqlite`).
- **Questions** (`deprecated/python/lab/datasets/workload.py`, gold from the simulator's per-message truth):
  - 80 past questions from 10 shapes: 6 about spending, 4 about health as controls. They are asked first, and
    their log is the only input to the optimizer.
  - 105 future questions: `same` (40), `reworded` (40), `novel` (25).
- **Conditions on the future questions:**
  - `base`: the database as it is.
  - `opt`: after the migrations.
  - `opt+compiled`: compiled questions first, then the `opt` path.
- **Readers:**
  - the deterministic reader alone
  - the GPT-6 Luna tool agent alone
  - the hybrid: deterministic if the verifier gives ≥ 0.7, else the agent
- **Grading:** rule-based against numbers from the truth (amounts per currency, counts, dates, names). No model
  judge, except Jev for the 72 scale questions as in round 4.
- **Budget:** ≤ $2 of real spend for the round, stopped by a guard.
- **Decision rule:**
  - If H1 holds, the query log and the advisor become part of the recommended setup (LLP 0000).
  - If H2 holds, compiled questions do too.
  - Otherwise, record why in LLP 0003.000 and mark this RFC accordingly.

## Risks

- **Overfitting to the log.** Templates and migrations tuned to 80 questions may not generalize. `reworded` and
  `novel` measure this, and migrations must cite at least 2 logged questions.
- **Silent wrong derivations.** A wrong rule changes answers without an error. The Jev spot-check, `_history`, and
  the evidence in `_migrations` make each one auditable and reversible.
- **Schema bloat.** Every column adds options to the reader's closed forms. Only migrations the log justifies are
  applied. Pruning columns nobody asks about is an open question.
- **Privacy.** The query log is personal data. `forget` must purge matching `_queries` rows. This is not implemented
  in this round.

## Open questions

1. **Trust without gold.** Should the advisor act on an unconfirmed signal (the verifier's), or wait for user
   feedback ("that's wrong")?
2. **When to re-run the advisor:** on a schedule, or when `low_trust` rises for a table (like recompiling the writer
   on drift)?
3. **Should unused columns be hidden** from the reader's forms? A smaller form is cheaper and less error-prone, but
   novel questions may need those columns.
