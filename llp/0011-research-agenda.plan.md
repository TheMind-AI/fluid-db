# LLP 0011: Research agenda: a new kind of database

**Type:** Plan
**Status:** Draft
**Systems:** Research, Core
**Author:** Adam Zvada / Claude
**Date:** 2026-09-25
**Related:** LLP 0000, LLP 0001, LLP 0002, LLP 0003, LLP 0010

## Summary

What to test next, and in which order, to find out whether FluidDB is a new kind of database and not just a good
memory for one simulated person.
- **Each experiment below is a sketch.** When one starts, it becomes an RFC with its hypotheses written first, and
  then a Research document with the results (LLP 0001).
- **Costs are estimated** from the prices observed in rounds 4-5: the Luna planner ~$0.0007 per message, a
  deterministic read ~$0.0004, an agent answer ~$0.0012.

## The thesis

[confirmed] (Adam Zvada, 2026-09-25), from dictation: FluidDB is "extracting … structure from non-standard,
non-deterministic data", it "can serve as a memory for AI agents", and it "can generate the UI out of it because if
you have a structure … you can build on top of a UI … this is like a new kind of database."

In the terms of this corpus:
- **A database that builds its own structure** from whatever it is given, and keeps the raw input as the source of
  truth (LLP 0004#raw-log).
- **The structure is a living artifact,** shaped by:
  - what is written: the planner and compiled patterns (LLP 0005)
  - what is asked: the query log and migrations (LLP 0003)
  - time: consolidation, supersession, forgetting (LLP 0007)
- **One structure, three consumers:** code reads it with SQL, agents through memory tools, and people through
  generated interfaces (LLP 0010).

**What rounds 1-5 established** on one simulated life of up to 5,050 messages (LLP 0002):
- it works
- it is correct as a database
- it can be read deterministically in about a second
- it can be written mostly by compiled code
- it can safely change its own schema

**What they did not establish** is what the thesis needs next:
1. **Generality:** other domains, real people's data, messier input.
2. **Agents:** does it help an agent do tasks, not just answer quiz questions?
3. **Interfaces:** can the generated interface be something people use and correct?
4. **Trust:** does the database know which of its values are doubtful, and can the user fix them?
5. **Scale and cost:** is extracting everything eagerly the right model at 100k-10M items?

## Experiments

### E1 The database proposes its own app

- **Question:** from the catalog, the value profiles and the query log, can FluidDB generate an interface that shows
  what the user actually asks about, and lets the user correct it?
- **Design:**
  1. One LLM call compiles an app spec from the schema and the logged question shapes:
     - dashboard tiles (each a SQL query, a chart type and a title)
     - entity pages (a person with everything linked to them)
     - a timeline
     - an **inbox**: dedupe pairs at 0.5-0.8, doubtful values (E2), and migrations that need a human
  2. Code validates every tile: its SQL runs and reproduces the trusted logged answer.
  3. The spec renders as static HTML, extending `deprecated/python/lab/explorer.py`.
  4. Answers in the inbox are written back as typed operations: a confirmed merge, a relabel.
- **Hypotheses:**
  - ≥ 80% of the recurring question shapes get a correct tile
  - the inbox surfaces at least 3 of the 4 known data problems at 5k (nickname duplicates, the Eva → Lucie link,
    merchant variants, late-night dates), with ≤ 20% false alarms
  - under $0.20
- **Decides:** whether "UI from structure" works end to end, including the write-back loop.

### E2 Uncertainty as data

- **Question:** does every stored value know how sure it is?
- **Design:**
  - Store a confidence per extracted value: its source (compiled pattern, planner or Jev choice) and, where there is
    one, Jev's probability.
  - Calibrate against the 5k truth: how well confidence separates wrong values from right ones (AUROC), and how many
    errors a review of the least-confident values catches.
- **Hypothesis:** reviewing the 5% least-confident values catches ≥ 50% of wrong values, for about $0.30.
- **Decides:** what the inbox (E1) shows, and whether answers can carry ranges ("2,400-2,800 Kč").

### E3 Other kinds of messy data

- **Question:** is the same engine a general, self-building ETL?
- **Design:**
  - Run the same engine and prompts, with no domain code, on 2-3 new simulators with exact truth:
    - support tickets whose status changes
    - sales notes with deals and stages
    - meeting notes with decisions, owners and deadlines
  - Add one public benchmark with gold tables (receipts or text-to-table), compared with published document-ETL
    systems.
- **Metrics:** field accuracy, schema stability over time, the share written by compiled patterns, question accuracy,
  and cost per item.
- **Hypothesis:** ≥ 90% field accuracy and ≥ 60% of items written by code after the bootstrap, for $1-3.
- **Decides:** generality, the biggest open risk to the thesis.

### E4 Memory for agents, in the loop

- **Question:** does an agent do its tasks better with FluidDB as memory?
- **Design:**
  - A simulated user works with an assistant agent over simulated weeks, sending facts and requests that need
    memory: repeat last month's booking, what did I promise Tom, how much of this month's budget is left, who was at
    that dinner.
  - The agent uses FluidDB through MCP (`remember`, `ask`, `sql`), against Mem0, a markdown memory file, and full
    context while the history still fits.
- **Metrics:** task success against the truth, tokens, latency, cost, and stale-fact errors.
- **Hypothesis:** FluidDB ≥ Mem0 overall and ≥ +15 points on tasks that need aggregation or current state, with fewer
  stale facts, for $2-5.
- **Decides:** whether FluidDB is a better agent memory, which the public benchmarks can no longer show (LLP
  0002.001).

### E5 Lazy structure

- **Question:** does everything have to be extracted when it is written?
- **Design:**
  - Keep only the log (plus cheap indexes).
  - Extract when a question needs it, keep what was extracted, and backfill whole columns once a question shape recurs
    (the machinery of LLP 0003). This is database cracking applied to meaning.
  - Compare with eager extraction (round 4) and a hybrid over the same workload.
- **Metrics:** cumulative cost, accuracy, and the latency of the first answer.
- **Hypothesis:** when questions touch few shapes, lazy extraction costs under 30% of eager at equal accuracy, and the
  hybrid is best overall; about $1.
- **Decides:** the cost model at 100k-10M items.

### E6 A real-data pilot (done as round 10)

- **Question:** does it hold on a real person's data?
- **Design:** the user's own notes, chats or calendar, only with explicit consent, because their content goes to the
  model providers. There is no gold, so the user reviews the result and writes the questions.
- **Done:** LLP 0017 and 0017.000, on the maintainer's own chats with a therapy assistant, ~$2.37. The gold came from
  the data itself: what the person re-told, verified against the earlier sessions.
  - Asked on cue, FluidDB knew 49-54% of it, the product's memory 37-38%, and the whole history read at once 66%.
  - The write path made therapy talk a mood diary, and a forget request deleted 54 rows.
- **Follow-ups (E9):**
  - A statement store with recurrence counts and status.
  - Retrieval that crosses languages.
  - Recall per turn inside a conversation.
  - A nightly dossier from a full read.

### E9 Memory for conversation

- **Question:** what does a memory need for talk, where the content is statements about a person rather than
  transactions?
- **Design:** replay real sessions turn by turn; at each turn the memory recalls what it knows about what was just
  said.
  - Compare four memories: a statement store (third person, one fact, a kind, provenance, recurrence count, status),
    FluidDB's stores, the product's memory, and a nightly dossier written from a full read.
  - Retrieval: words, Jev relevance, and a translation of each statement into one language.
- **Metrics:** recall of what the person later re-tells, and of what the assistant asks again; tokens and latency
  per turn.
- **Hypothesis:** the statement store with Jev relevance comes within 5 points of reading everything, at a fraction
  of the tokens per turn; about $2.
- **Data:** the private data of rounds 10 and 11. Other people's conversations only go to providers their privacy
  policy names (`LAB_PROCESSORS`, LLP 0018#processors).
- **Round 11 sharpened the target:**
  - Reading the whole history (up to ~85k tokens) knew 14 points more than FluidDB.
  - A memory for conversation has to close that gap per turn, or know when to read everything.

### E7 A stable schema advisor

- **Question:** can the advisor's variance from run to run (LLP 0003.000) be removed?
- **Design:**
  - Take 3-5 proposals per round (or one stronger-model call) and keep every one that passes the tests.
  - Give it dependencies found by code as hints (currency determines city).
  - Repeat 5 times.
- **Hypothesis:** the same migrations are kept in at least 4 of 5 runs, and the location column covers every row;
  about $0.50.

### E8 A memory with several stores (done as round 7)

- **Question:** should AI memory be several kinds of store, like human memory, rather than one database?
  [confirmed] (Adam Zvada, 2026-09-25): "the AI memory should have a different kind of databases. This is like the
  factual database".
- **Done:** LLP 0013 and 0013.000.
  - Reading every store beats the database plus log search by about 9 points.
  - It beats the whole log in the prompt, at 1/30-1/76 of the tokens.
  - The stores that matter compute (facts, routines, periods).
  - Jev routes as well as an LLM at 1/11 of the latency.
- **Follow-ups:**
  - Segment events within a day.
  - Let verbatim detail decay while gists stay.
  - Give an agent the stores as tools (E4).
  - Test whether routines and periods generalize to other domains (E3): regularities per actor and period, for
    tickets or sales.

## Order

My recommendation; the order is the author's decision.
1. **Round 6: E1 + E2. Done: LLP 0012 and 0012.000,** ~$0.12.
   - The inbox and the confidence ranking worked.
   - The app reads 73.75-75% of future questions of logged shapes.
   - A short log yields few tiles.
   - Follow-ups: a local server for live inbox answers, and tiles from the schema too.
2. **Round 7: E8, a memory with several stores. Done: LLP 0013 and 0013.000,** ~$1.6 (the user chose it over E3).
   - Reading every store: +9 points over the database plus log search.
   - Jev routing: +4 points at half the tokens.
   - Full context loses.
3. **Round 8: E3. Done: LLP 0014 and 0014.000,** ~$2.45. Three simulated work scenarios, no domain code.
   - Writes 83-97% correct, after two fixes: gate wording, and a link-aware write context.
   - The compiled writer covers event streams only.
   - Short histories are best read whole.
   - Erasure now needs Jev's confirmation.
   - Still open: a public benchmark with gold tables, and real messy data (E6).
4. **Round 9: writer v2.5 and the crossover. Done: LLP 0016 and 0016.000,** ~$2.25.
   - Compiled updates work: 37% of support messages, 92% right.
   - No single crossover length: counting belongs to the facts at every length.
   - Better writes moved the misses to the reader's plans.
5. **Round 10: E6, real conversations. Done: LLP 0017 and 0017.000,** ~$2.37. The user asked for it before E4.
   - People re-tell a third of what they say, and briefings don't prevent it.
   - On cue: FluidDB +16 over the product's memory; reading everything +12 over FluidDB.
   - The erasure fix (redact links) is implemented as an option.
6. **Round 11: E6 on other people. Done: LLP 0018 and 0018.000,** ~$7.66. 18 accounts that used the assistant for
   their own lives, OpenAI only, aggregates only.
   - Round 10's order holds: the whole history (70%) > FluidDB (56-58%) > the product's memory (40-44%).
   - People re-tell about a third of what they say.
7. **Round 12: E9, memory for conversation. Done: LLP 0019 and 0019.000,** ~$3.55.
   - Meaning search: +10 over word search.
   - Statements: 66% at 1.1k tokens per question.
   - Dossier + statements + the log match reading the whole history, at a quarter of the tokens.
8. **Round 13: E9, part two, and Jev picking the search. Done: LLP 0020 and 0020.000,** ~$5.20.
   - Rerank is the biggest search gain.
   - Jev should pick the evidence, not the search.
   - The dossier can be kept current.
   - `conv_best` recalls 72.1%, against 70.0% for the whole history.
9. **Round 14: E9, part three. Done: LLP 0021 and 0021.000,** ~$11.08.
   - Whole windows for the picker.
   - Every wording kept.
   - A memory that beats reading everything: 73.0% against 70.0%.
   - In a live replay, the memory is rarely used, until the prompt says to use it.
10. **Round 15: E9, part four. Done: LLP 0022 and 0022.000,** ~$0.60.
   - The detector raised strict acknowledgement from 10.2% to 22.0%; the false-alarm target was not met.
   - The conversation memory is packaged in TypeScript with a Cloudflare service (LLP 0023).
11. **Next:**
   - Evaluate the TypeScript package on staging: quality parity, detector thresholds, capacity, latency and recovery.
     Local synthetic runtime and distribution checks do not establish those product properties.
   - **E4** (~$3-5), the decisive test of memory for agents, with the stores as tools. Two fixes should come before
     it: the reader's plans on new schemas, and bare examples for insert patterns.
12. **Then E5,** with SQL pushdown and indexes at 100k+ rows (~$1).
13. **E7** alongside the next round that uses the advisor.

## Prior art

[inferred: not a systematic search]
- **Structure from documents:**
  - Evaporate (Arora et al., 2023) has an LLM write extraction functions, much like the compiled writer.
  - DocETL (Shankar et al., 2024), LOTUS (Patel et al., 2024) and Palimpzest (Liu et al., 2024) build LLM-powered
    document pipelines.
  - All of these start from a schema or query the user gives. FluidDB invents and evolves the schema from a stream,
    and keeps provenance and a raw log.
- **Agent memory:** MemGPT/Letta (Packer et al., 2023), Mem0, Zep/Graphiti (a temporal knowledge graph, 2025),
  A-MEM (2025). FluidDB is relational: exact aggregates, SQL, history, and deletion that can be verified.
- **Generated interfaces:** schema-driven admin pages (such as Django's admin), visualization generation (LIDA,
  2023). FluidDB would choose views from what the user asks, and write corrections back.
- **Uncertain and adaptive data:** probabilistic databases (Trio, MayBMS); adaptive indexing and database cracking;
  self-driving databases.
