# LLP 0013: A memory with several stores, like a brain

**Type:** RFC
**Status:** Draft
**Systems:** Core, Reads, Writes, Jev, Research
**Author:** Adam Zvada / Claude
**Date:** 2026-09-25
**Revised:** 2026-09-25 (Design updated to what was built: multi-label Jev aspects, the intention gate, month
summaries, the router threshold; Hypotheses unchanged; Protocol extended with additions, a held-out set and a fixed
lesion base; results in LLP 0013.000)
**Related:** LLP 0000, LLP 0002, LLP 0008, LLP 0011, LLP 0013.000

## Summary

FluidDB so far is one kind of memory: facts. The evidence says one store is not enough:
- **Facts plus the raw log beat either alone:** 100% on the 7-week stream, 92% on LoCoMo.
- **Conversational detail doesn't live in tables:** LoCoMo, database alone, 80%.
- **State changes are weak:** LongMemEval knowledge updates, 50%.
- **Soft information gets dropped:** intentions and preferences.

This RFC builds a memory in the image of human memory:
- **Several stores,** each suited to one kind of question.
- **All derived from one append-only log** by background consolidation, so they stay consistent and share
  provenance, confidence and forgetting.
- **A router** decides which stores a question needs.
- **Built as swappable parts:** stores, deciders, routers and answerers are adapters, and every experiment is a
  configuration.

[confirmed] (Adam Zvada, 2026-09-25):
- "AI memory should have different kinds of databases. This is like the factual database"
- "think about how brain … what kind of memories"
- "building it … like adapter … or like Lego set so we can like switch between those things and … experiment very
  fast"
- "the point why now it makes sense is the JEV"

## Human memory, and what each part becomes

| human memory | what it does | store here | built by (consolidation) |
|---|---|---|---|
| sensory buffer | holds raw input briefly | `log`: every message, verbatim | appended on write |
| semantic (Tulving) | facts detached from when they were learned | `facts`: FluidDB tables with history (LLP 0004-0007) | planner and compiled writer |
| episodic (Tulving; event segmentation, Zacks) | experienced events: when, where, with whom, what was said | `episodes`: the log cut into days, each with a gist, people, places and message ids | code segments; one LLM gist per day |
| procedural / habits (Squire) | how things are usually done | `routines`: regularities (days, places, partners, typical amounts and times) | code, from facts |
| prospective | remembering to do things | `intentions`: reminders, plans and appointments, with due dates and status | a Jev gate (p ≥ 0.15) picks candidates; the LLM extracts; code tracks done, cancelled, moved and upcoming |
| evaluative / preferences | likes, dislikes, allergies, opinions | `preferences`: holder, stance, kind of thing, target | a Jev gate picks candidates; the LLM extracts |
| autobiographical (Conway) | life periods, general events, turning points | `periods`: where the user lived when, month summaries, and the milestones | code, from facts; milestones by Jev; months by the LLM from the day gists |
| source monitoring, metamemory | where something came from; how sure | provenance and confidence on every item (LLP 0012) | every store |
| working memory (Baddeley) | what's active for the task at hand | the evidence assembled for one question | router and answerer |
| consolidation (complementary learning systems) | replay turns episodes into knowledge | background passes over the log | LLP 0007 |

Two kinds of forgetting stay distinct:
- **Decay:** low-salience verbatim detail may fade while the gist stays.
- **Erasure:** "forget X" removes X from every store, because every store derives from the redacted log.

## Design

Code is in `deprecated/python/lab/memory/`.

**Parts:**
- **`Store`**
  - `build(ctx)`: consolidate from the log and the facts database
  - `read(question) -> [Evidence]`: text, provenance ids, confidence, the store's name
  - optional `answer(question)`: facts can answer exactly
- **`Decider`:** closed-set decisions, `choose(state, question, options)` and `yes(state, question)`.
  - `JevDecider` returns calibrated probabilities.
  - `LLMDecider` uses GPT-6 Luna with a stated confidence.
  - Swapping them tests "Jev is why now" (LLP 0008).
- **`Router`:** which stores a question needs.
  - `DeciderRouter` with Jev or an LLM: one yes/no per store, in one request; a threshold decides which to read.
  - `AllRouter`: every store.
  - `OracleRouter`: the question's labelled memory type (meant as an upper bound; it wasn't, LLP 0013.000).
- **Consolidation ("sleep"):** erase what the user asked to forget, then one Jev pass asks five yes/no questions of
  every message (intention, done, cancel, preference, milestone). Each store reads those at its own threshold.
- **`Answerer`:** assembles working memory from the routed stores within a token budget. One LLM call answers;
  exact answers from `facts` are passed along as "computed by the database".
- **`Memory`:** built from a config naming one choice for each part. An experiment is a list of configs.

**Parts added later:**
- **Rounds 10-11:** `product` and `product_injected`, another product's memory read as stores (LLP 0017).
- **Round 12 (LLP 0019):**
  - `log_embed` and `log_hybrid`: the log searched by meaning, or by words and meaning fused; the embeddings come
    from `deprecated/python/lab/common/llm.py` `embed`.
  - `statements`: lasting statements per conversation window, searched by meaning.
  - `dossier`: one full read, written once and read whole.
- **An answerer's evidence budget can be set per config** (`budget`).

## Benchmark

The benchmark (`deprecated/python/lab/datasets/memory_types.py`) has about 90 questions over the 5,050-message life (round 4), in 12
types. Gold comes from the simulator's truth or the scripted storyline:

| type | example |
|---|---|
| semantic, current | "What is Tom's phone number?" |
| semantic, history | "Where did Eva work before Gensler?" |
| aggregate | "How much did I spend on coffee in 2025?" |
| episodic, event | "Where did I meet Lucas Meyer?" |
| episodic, time | "When did we close the seed?" |
| routine | "Which days do I usually climb?" |
| prospective | "What reminders are still open?" |
| preference | "What does Mom love?" |
| source | "What was the security deposit on the Valencia St lease?" |
| period | "What was I reading when I moved to SF?" |
| metamemory | "What is Nina's phone number?" (never given) |
| forgotten | "What was Jan Dvořák's number?" (erased) |

Grading uses Jev as judge (LLP 0009), with "I don't know" as the correct answer for metamemory and forgotten
questions.

## Hypotheses

These were written before the experiment ran.

- **H1 (several stores beat one).** The multi-store memory with the Jev router scores ≥ 10 points higher than both
  single-store systems: facts only (the round-4 hybrid) and the raw log with retrieval.
- **H2 (the stores are distinct: a double dissociation).** Removing a store lowers its own question types by ≥ 20
  points and changes the other types by < 5 points on average. This must hold for at least 3 stores.
- **H3 (routing).** The Jev router is within 5 points of the oracle router and of the LLM router, at ≤ 1/5 of the LLM
  router's latency.
- **H4 (full context is the ceiling, and it costs).** Reading the whole 5k log (about 110k tokens) scores within 5
  points of the multi-store memory or above it, at ≥ 10x the tokens per question. It runs on a stratified subset.
- **H5 (Jev as the decider).** On the same closed decisions (routing, row confidence, merges, the verifier), Jev
  matches GPT-6 Luna's accuracy within 3 points, is better calibrated (AUROC ≥ Luna's), and is ≥ 3x cheaper and ≥ 3x
  faster.

## Protocol

- **Data:** the round-5/6 database (`deprecated/python/lab/runs/app/db.sqlite`: evolved schema, `_log` with every message) and
  `life_scale.json`.
- **Configs:**
  - `facts`
  - `log_rag`
  - `brain` (Jev router)
  - `brain_llm_router`
  - `brain_all`
  - `brain_oracle`
  - seven lesions of `brain_oracle`, one store removed each. Revised: lesions of `brain_all`, because lesions of the
    oracle only remove stores the oracle picked by type, which makes the test circular.
  - `full_context` on 3 questions per type
  - Added while running: `facts_only`, `facts_log`, `brain_jev_recall` (Jev at p ≥ 0.3), and additions (facts + log +
    one store); and 36 held-out questions written after the system was frozen (LLP 0013.000#development).
- **H5:** the same decision sets run with `JevDecider` and `LLMDecider`.
- **Budget:** ≤ $3 for the round, with a guard in code. Everything is cached.

## Risks

- **I wrote the benchmark knowing the stores I planned.** Types map to stores by design. The lesion test checks
  that the mapping is real, and the Jev and LLM routers are not told the types.
- **Day-level episodes are coarse.** Event segmentation within a day is left for later.
- **A single simulated person.** Real conversations make episodes, preferences and intentions far messier.
