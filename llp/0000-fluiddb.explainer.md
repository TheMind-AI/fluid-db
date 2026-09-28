# LLP 0000: FluidDB

**Type:** Explainer
**Status:** Draft
**Systems:** Core
**Role:** Root
**Author:** Adam Zvada / Claude
**Date:** 2026-09-25
**Revised:** 2026-09-28 (one public package, TypeScript evaluation and sole-runtime backend integration)
**Related:** LLP 0001, LLP 0002, LLP 0003, LLP 0011

## Summary

FluidDB is long-term conversation memory for an AI assistant. The maintained implementation is TypeScript:
original messages become searchable windows, statements, linked repetitions/changes and a dossier (LLP 0023).
TypeScript modules, tests and evaluation commands run from the repository root. The SDK, adapters, MCP and tools
are distributed as one public package; internal workspaces remain private (LLP 0023#packaging).

[confirmed] (Adam Zvada, 2026-09-27): TypeScript is the main implementation from now on; older code should live in
a deprecated folder, while it remains possible to evaluate the TypeScript version.

The earlier self-organizing relational database and the 2026 Python research lab are preserved under
`deprecated/python/`. Their experiments led to the current conversation design. Generated SQL schemas, compiled
readers, apps and other computed stores remain historical research components, not features of the current package.
The complete research record stays here; earlier measured quality numbers still describe Python systems.

This corpus follows [Linked Literate Programming](https://github.com/ccheever/llp): design and research decisions
live here as numbered, living documents, and code points at them with `@ref` comments.
- **Start here**, then read LLP 0015 (the design as a whole), LLP 0001 (how research is done here) and LLP 0002 (what
  we know).
- **`llp/current/`** lists what is in play.

## Documents

| LLP | type | what |
|---|---|---|
| 0000 | Explainer (root) | this map: what FluidDB is, where things live, constraints |
| 0001 | Principles | how research is done: budget, truth before judges, hypotheses first, compile then check |
| 0002 | Research | the findings ledger: what we know, with confidence |
| 0002.000 - 0002.003 | Research | the records of rounds 1-4: question, setup, results, lessons, decisions |
| 0003 / 0003.000 | RFC / Research | round 5: a schema shaped by its queries, and its results |
| 0004 | Spec | the storage engine: raw log, typed operations, catalog, provenance, history, forget |
| 0005 | Explainer | the write path: gate, source guard, planner, compiled writer, drift |
| 0006 | Explainer | the read path: SQL, the deterministic reader, verifier, hybrid, agent |
| 0007 | Explainer | background passes: sleep, dedupe, supersession, typed columns, forget propagation |
| 0008 | Explainer | Jev: what it decides, where it works and where it doesn't |
| 0009 | Guide | evaluation: simulators, graders, public benchmarks, benches, cost accounting |
| 0010 | Explainer | interfaces: SQL, MCP server, generated explorer, terminal |
| 0011 | Plan | the research agenda: what to test next and in which order |
| 0012 / 0012.000 | RFC / Research | round 6: the generated app, per-row confidence and the inbox, and their results |
| 0013 / 0013.000 | RFC / Research | round 7: a memory with several stores, like a brain, from swappable parts, and its results |
| 0014 / 0014.000 | RFC / Research | round 8: the same system on a support desk, a sales pipeline and a team's projects |
| 0015 | Explainer | how the memory should work, as the evidence stands: the whole design in one place |
| 0016 / 0016.000 | RFC / Research | round 9: writer v2.5 (compiled updates), and when a memory beats reading everything |
| 0017 / 0017.000 | RFC / Research | round 10: real conversations with a therapy assistant, kept private: what people re-tell, and which memory knew it |
| 0018 / 0018.000 | RFC / Research | round 11: the same test on 18 other people, OpenAI only, aggregates only |
| 0019 / 0019.000 | RFC / Research | round 12: memory for conversation: meaning search, a statement store and a dossier |
| 0020 / 0020.000 | RFC / Research | round 13: search and creation done properly; Jev picks the evidence, not the search |
| 0021 / 0021.000 | RFC / Research | round 14: a better picker, every wording kept, and a live replay: recall is no longer the bottleneck, use is |
| 0022 / 0022.000 | RFC / Research | round 15: the re-telling detector, its gains and false alarms |
| 0023 | Spec | TypeScript conversation-memory packages, durable storage, Cloudflare service and release boundary |
| 0023.000 | Research | BetterMind integration audit: missing product contracts, reusable memory viewer and evaluation gates |
| 0023.002 / 0023.002.000 | RFC / Research | Agent feedback through HiveNet and portable memory/feedback skills |
| 0023.001 / 0023.001.000 | RFC / Research | Product SDK, database adapters, MCP and cached retrieval parity |
| 0023.003 | Research | Merge review fixes, npm preview preparation and backend integration gates |
| 0023.004 | Research | Sole-runtime backend integration, SDK seams and behavioral regression evidence |
| 0023.005 | RFC | Communication preferences, occasional check-ins, Jev boundary and technical feedback routing |
| 0024 / 0024.000 | RFC / Research | TypeScript replay evaluation protocol and engineering verification |
| 0024.001 / 0024.001.000 | RFC / Research | Swappable SDK components, portable evaluator, and real-provider comparison |
| 0024.002 / 0024.002.000 | RFC / Research | Longer product continuity, complete legacy import and native loading recovery |

## Where things live

[observed] from the tree:

| path | what |
|---|---|
| `packages/` | primary TypeScript implementation: schema, core, providers, SQL, client, Worker |
| `evals/` | TypeScript conversation replay, labelled synthetic fixtures, cached model evaluation |
| `examples/`, `scripts/`, `test/` | runnable examples, packaging, workspace checks |
| `deprecated/python/fluiddb/`, `deprecated/python/eval/`, `deprecated/python/experiments/`, `deprecated/python/data/` | 2023–24 prototype and its data/evals |
| `deprecated/python/lab/` | 2026 Python research systems, memory stores, datasets, benches, results and local caches |
| `deprecated/python/lab/README.md`, `deprecated/python/lab/REPORT.md` | archived commands and narrative findings |
| `llp/` | living design and historical research corpus |

Run historical lab commands from `deprecated/python/`; its Python module names stay unchanged. The lab still loads
keys from the repository-root `.env`. Root `bun run check` and `bun run eval` run TypeScript only.

## Architecture

The current conversation architecture is specified in LLP 0023, with evaluation in LLP 0024. The following is the
historical relational/multi-store design from rounds 1-8, preserved for context. Evidence for each part is in LLP 0002.

**Write path** (asynchronous; seconds are fine; LLP 0005, on the engine of LLP 0004):
- **`_log` first.** Every raw message is stored before anything else.
- **Tier A: compiled patterns** write most recurring messages in about 1 ms with no model
  (`deprecated/python/lab/systems/compiled_writer.py`).
- **The Jev gate** stops chit-chat and questions. Its wording is personal; use the domain-neutral wording for other
  data (round 8, LLP 0005#the-jev-gate).
- **Tier C: the LLM planner** writes the rest as typed operations. The engine validates and applies them, with
  provenance (`_src`) and `_history` (`deprecated/python/lab/systems/engine.py`, `deprecated/python/lab/systems/fluid_v2.py`).
- **Documents may add rows but never overwrite or delete them.**

**Asynchronous passes** (LLP 0007):
- recompile writer patterns on drift
- dedupe entities: Jev merges; unclear pairs become questions for the user
- supersession
- typed columns
- deterministic forget propagation
- schema evolution from the query log (derived columns, canonical values), as LLP 0003 proposes
- per-row confidence (code checks plus Jev), feeding an inbox of questions for the user with one-click fixes; the
  answers are logged and applied as typed updates (LLP 0012)

**Read path** (fast; LLP 0006; the interfaces on top of it are in LLP 0010):
- **Apps:** plain SQL.
- **Questions:**
  1. compiled question templates (milliseconds, LLP 0003)
  2. then Jev filling a closed query form, SQL, and a Jev verifier (~0.8 s)
  3. then the LLM tool agent when the verifier is unsure (`deprecated/python/lab/systems/det_reader.py`, `deprecated/python/lab/systems/reader.py`)
- **Every question is logged** in `_queries`.
- **A generated app** (`deprecated/python/lab/appgen.py`, LLP 0010 and LLP 0012) turns the logged questions and the schema into pages
  of tiles, person cards, breakdowns, a timeline and the inbox. Code computes every number.

**Memory for an assistant** (round 7, LLP 0013, `deprecated/python/lab/memory/`): FluidDB is the semantic store of a memory with
several stores, all derived from the same log.
- **Consolidation ("sleep") builds the other stores:**
  1. Erase what the user asked to forget.
  2. One Jev pass asks five yes/no questions of every message.
  3. From those, day episodes, routines computed by code, intentions with status, preferences, and life periods
     with month summaries.
- **A question goes to the stores it needs.** Jev decides with one yes/no per store, in about 0.3 s. One LLM call
  answers from the evidence.
- **Reading every store beats the database plus log search by about 9 points,** and beats the whole log in the
  prompt at 1/30-1/76 of the tokens.
- **The stores that matter are the ones that compute:** facts, routines, periods.
- **Counting and state go to the facts, at any history length** (rounds 8-9). Reading everything miscounts even at
  26k tokens. Only what was said, over a short sparse history, is best read whole (LLP 0015#4-reading-the-right-amount-of-memory-for-the-question).

**Evaluation** (LLP 0009):
- simulators with exact truth and rule graders
- Jev as judge where answers are free text, validated against Opus
- a held-out question set written after freezing a system (round 7)

## Constraints

These must not be simplified away.

1. **The raw log is the source of truth.** Structured rows may be wrong or incomplete; `_log` keeps every word, so
   anything can be re-derived (backfills in LLP 0003 depend on it). [observed] `deprecated/python/lab/systems/engine.py` docstring.
2. **The model never writes SQL on the write path.** It returns typed operations; the engine applies them.
   [observed] `deprecated/python/lab/systems/engine.py`.
3. **Jev only decides among options that exist.** It never produces a value, so reads can't invent data. [observed]
   `deprecated/python/lab/systems/det_reader.py` docstring.
4. **Writes may be slow; reads must be fast.** [confirmed] (Adam Zvada, 2026-09-25): "for write imho we can have
   higher latency right, that can run async"; reads "lowest latency".
5. **Deterministic and cheap wherever possible.** The LLM compiles, code runs. [confirmed] (Adam Zvada, 2026-09-25):
   "deterministic cheap and great performance".
6. **A low research budget.** Every model call is cached; each round has a dollar cap. [confirmed] (Adam Zvada,
   2026-09-25): "we have low budget"; round 4 was capped at about $3.
7. **Relaxing a query never drops a filter on a specific person or thing.** Otherwise it answers about someone else.
   [observed] `deprecated/python/lab/systems/det_reader.py` `execute`, and the round-3 incident in `deprecated/python/lab/REPORT.md` section 21.
8. **Secrets stay in `.env`** (git-ignored), never in tracked files. [observed] `.gitignore`.

## Systems vocabulary

These are the values of the `**Systems:**` header:
- `Core`, `Engine`, `Writes`, `Reads`, `Jev`, `Schema`, `Evaluation`, `Research`, `Legacy`

## References

On 2026-09-25 the user asked to extend the corpus to "the previous experiments and everything we have". The
references proposed in Phase 1 are now applied, alongside module-level references.
- **Every `deprecated/python/lab/systems`, `deprecated/python/lab/common` and `deprecated/python/lab/datasets` module points at the document that governs it.**
- **Benches point at the round that used them.**
- **Function-level references** mark the decisions an agent might "simplify" away:
  - the relaxation rules
  - the verifier
  - value mentions in routing
  - count semantics
  - the source guard
  - pattern validation
  - choice columns
  - learned nicknames
  - forget
  - the replay guards

`./ref-check` validates all of them.

## Unconfirmed

- [inferred] Whether the recommended setup above is what the user wants to build as a product, or is a research
  baseline.
