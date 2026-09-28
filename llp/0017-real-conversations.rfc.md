# LLP 0017: Real conversations: rebuilding a person's memory from their chats with a therapy assistant

**Type:** RFC
**Status:** Draft
**Systems:** Writes, Reads, Research
**Author:** Adam Zvada / Claude
**Date:** 2026-09-25
**Related:** LLP 0015, LLP 0016.000, LLP 0013.000, LLP 0011, LLP 0001

## Summary

Every earlier round ran on simulated data with exact truth. This is the first round on real data (agenda E6): one
person's own conversations with Mind, a therapy-chat product, and the product's own memory of that person.
- **Part A rebuilds the memory from the whole history** (July 2024 to September 2026) and compares it with the
  product's memory on 12 open questions. There is no gold; the comparison is read, not scored.
- **Part B is a natural experiment.** The person talked with Mind from July 2024 to June 2025, stopped for 14
  months, and came back in August 2026. How much of what they said after coming back had they already told Mind,
  and which memory, built from the history before the break, would have known it?

[confirmed] (Adam Zvada, 2026-09-25), from dictation: "take some very long conversation and sessions there and try to
rebuild the memory … look at the memory system that it has and maybe if there are some learnings and ideas from it …
It's actually like live data, like the conversation would be different if the AI would have better memory".

## Consent and data handling

- **Only the maintainer's own account is used.** Other users' conversations would go to model providers beyond
  those the product's users agreed to; using them needs a separate decision.
- **Nothing real enters the tracked lab.** `LAB_PRIVATE_DIR` redirects the dataset, the databases and the results
  to a git-ignored directory in the product's repository (`.context/fluiddb-probe/`). [observed]
  `deprecated/python/lab/bench/scenarios.py` (`PRIVATE`, `paths`).
- **The model cache holds prompts** and is git-ignored (`deprecated/python/lab/.cache/`). The cost ledger holds only tags, tokens and
  dollars (`deprecated/python/lab/common/llm.py`, `Ledger`).
- **The export is read-only.** A script in the product's repository read the account's chats and memory with the
  product's own client and wrote one JSON file to the private directory. It printed counts, never identifiers.
- **This document and its results report counts, rates and kinds of information,** not what the person said.

## Data

`deprecated/python/lab/datasets/private_chat.py` turns the export into a scenario dataset.
- **The history:** 164 chats, 4,141 messages (1,876 from the person, 2,236 from Mind, 29 tool results).
- **A window is up to 6 of the person's consecutive messages in one chat,** each with the end of Mind's preceding
  turn ("Mind asked: …"). Answers like "yes, mostly at night" mean nothing without the question.
- **385 windows, 317k characters (~80k tokens).**
- **The product's memory of the person has three generations:**
  - 46 flat notes (July-August 2024)
  - 13 topic summaries (December 2024)
  - 105 session notes, one per session, with title, summary, insights and action steps; 34 also carry a mood
    rating (1-5) from the end of the session
  - since September 2026: 14 typed memories, 6 episode notes and a 3-block profile
- **The adapter** `ProductMemoryStore` (`deprecated/python/lab/memory/stores.py`) makes that memory one more swappable store, so the
  product's memory and FluidDB are read by the same answerer.

## Part A: the whole history

- **Ingest all 385 windows** with writer v2.5, without the source guard (`v25nog` in `deprecated/python/lab/systems/fluid_v2.py`).
  The person's own words are trusted input. The guard exists for forwarded documents, and would stop the person's
  own corrections.
- **Build every store** and answer the 12 probes (`PROBES` in `private_chat.py`) with:
  - `product_injected`: what the product's prompt gets at the start of a chat
  - `product`: everything the product's memory stores, searchable
  - `facts_log`, `brain_all`, `brain_jev_recall`: FluidDB
  - `full_context`: the whole history, read at once
- **Compare by reading.** Which answers are specific and dated, which are generic, which are wrong. This is a model's
  reading with no gold, and is reported as such.

## Part B: coming back

Written for the return after 14 months, and amended before any briefing was scored (below).
- **The history H:** the 349 windows before the break (July 2024 to June 2025). **The return R:** 36 windows in 15
  chats (August-September 2026).
- **Every memory is built from H alone:** a FluidDB database ingested from H (the prefix of Part A's ingestion,
  replayed from the cache), its stores, the product's memory as it stood before the return, and the whole of H for
  `full_context`.
- **The gold: what the person re-told.**
  1. An LLM lists the statements about the person's life in each return chat, from every message but the chat's
     first: people, work, circumstances, history, practices, plans, preferences.
  2. Each statement is looked up in H: an LLM reading all of H names the windows that already say it, BM25 adds its
     top 5, and each candidate is verified on its own window.
  3. A statement found and verified in H is **re-told**; one not found is **new**.
- **The briefing.** At the start of each return chat, each memory writes a briefing of at most 250 words for Mind,
  from the chat's first message only. A second, generic briefing ("I'm back after a long break") doesn't see the first
  message.
- **The measure: recall of the re-told statements.** An LLM checks whether each briefing contains each re-told
  statement.
  - The new statements are the control: a briefing built from H should contain almost none of them.
  - Differences are tested with a paired bootstrap over statements.
- **Mind's questions in R** are checked the same way: did the history already answer them?

### Amendment: three cuts

Made after the first gold for the 2026 return, before any briefing was scored.
- **The 2026 return was mostly product testing:** 150 short messages (median 30 characters) held only 37 statements
  about the person's life. That is too few to compare memories.
- **The same test runs at two more cuts,** where the chats after the cut are ordinary sessions:
  - `own2408`: the history before August 2024 (98 windows); the 14 chats of August 2024
  - `own2409`: the history before September 2024 (173 windows); the 29 chats of September-November 2024
  - `own2608`: the 14-month return above
- **Each cut has its own database, ingested from its history alone** (the prefix of Part A's ingestion, replayed from
  the cache), and the product's memory as it stood at the cut.
- **The generic briefing is worded for any return:** "We're starting a new conversation."
- **Mind's questions:** a question about the present moment ("what would you like to talk about today?") can't be
  answered by the past. The first check counted such questions; the check now excludes them.
- **Lasting facts only.** The extractor also listed moods and events of the moment, which the RFC meant to skip. Jev
  scores each statement and question for "a lasting fact about the person", and the test uses p ≥ 0.2. The threshold
  was chosen by reading the statements, before any briefing was scored; all statements are reported too.
- **The hypotheses hold per cut and pooled.** The pooled numbers are the test.

### Amendment: recall on cue

Added after the briefings were scored, which showed that no memory's briefing holds more than about a tenth of what
the person re-told (LLP 0017.000). A briefing is a summary, and what people re-tell is mostly detail. The question
"would the memory have known it?" needs a direct test.
- **For every re-told statement, an LLM writes the question the person could ask** that the statement answers,
  without giving the answer away. Each memory, built at the cut, answers it. The gold is the statement; for a changed
  statement, it is the earlier version.
- **Controls:** the same for up to 10 new statements per cut, whose gold is "not mentioned before".
- **Graded by the lab's Jev judge** (`deprecated/python/lab/common/grade.py`, `jev_grade`), as in rounds 4-9.
- **H8 (recall on cue):** the memories that search the history (`product`, `facts_log`, `brain_jev_recall`) answer
  ≥ 50% of the re-told questions right; what the product injects (`product_injected`) answers ≤ 30%.
- **H9:** `full_context` ≥ `brain_jev_recall` - 5, since each history fits in context.
- **H10 (controls):** every memory says it doesn't know on ≥ 80% of the new-statement questions.

## Hypotheses

These were written before any of the runs below.
- **H1 (the repeat burden):** ≥ 25% of the life statements in the return chats had already been said in H.
- **H2 (the product's memory then):** the product's memory, as its reader injected it before the return, holds ≤ 40%
  of the re-told statements in its briefing.
- **H3 (FluidDB):** `brain_jev_recall` built from H holds ≥ 60% of them, and ≥ 20 points more than the product's
  memory as injected.
- **H4 (reading everything):** `full_context` over H (~75k tokens) is within 5 points of `brain_jev_recall`, or
  better. These are statements of what was said, which LLP 0016.000 says can be read whole when they fit.
- **H5 (asked again):** ≥ 20% of Mind's questions in the return chats asked for something H had already answered.
- **H6 (writes on real conversation):**
  - The gate writes ≥ 70% of the windows.
  - Compiled code writes < 20% of the windows after the bootstrap: real conversation doesn't repeat templates.
  - Ingestion costs ≤ $0.50.
  - The schema grows ≥ 8 tables, among them people and moods, with no domain code.
- **H7 (whole-history probes, read):** FluidDB answers `follow_up`, `mood`, `start` and `recent` with dates and
  specifics that the product's memory lacks. The product's memory answers `themes` and `preferences` as well as
  FluidDB, since its topics and profile summarize exactly those.

## Protocol and budget

- `scenarios ingest own@v25nog`, then `scenarios memory own@v25nog` with the five configs and `LAB_PRODUCT_MEMORY`
  set to the export.
- Each cut's history is a prefix of the windows, ingested as `ownYYMM@v25nog`; its writes replay from Part A's cache.
  Then `scenarios memory ownYYMM@v25nog` with `LAB_PRODUCT_CUTOFF` at the cut, and `lab.bench.real_return` for the
  gold, the scores and the pooled summary.
- **Budget: ≤ $2 for the round,** with guards: $0.50 for the ingestion, $0.60 per memory run. Every call is cached.

## Risks

- **One person, 15 return chats.** The numbers are a pilot; each return chat counts for a lot.
- **The gold is made by an LLM** reading the history, then verified window by window. Misses make "re-told" smaller;
  wrong matches are cut by the verification.
- **The same LLM (Luna) answers for every config,** and also makes the gold. `full_context` reads the same text as the
  gold-maker, which may favour it.
- **The product's memory at the time of the return is reconstructed** from creation dates. Topics updated later can't
  be rolled back to their earlier text.
