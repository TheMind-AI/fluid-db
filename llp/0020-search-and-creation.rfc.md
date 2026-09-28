# LLP 0020: Search and creation, done properly

**Type:** RFC
**Status:** Draft
**Systems:** Reads, Research
**Author:** Adam Zvada / Claude
**Date:** 2026-09-27
**Related:** LLP 0019.000, LLP 0018.000, LLP 0015, LLP 0013, LLP 0011

## Summary

LLP 0019.000 found that meaning search, statements and a dossier recall as much as reading a person's whole
history. This round maps the options for searching and for creating memory, and tests the ones still untested on
conversation:
- reranking, query rewriting and context headers
- merging repeated statements, and keeping a dossier current incrementally
- recall at every turn of a real conversation, not only when asked

[confirmed] (Adam Zvada, 2026-09-27), from dictation: "what kind of searches and … creations … do we have if we
would like do it properly? … explore and tell me … the best way … and maybe this makes sense to do now?"

## The options

[inferred: from rounds 1-12 and prior art named in LLP 0011, not a systematic search]

**Search: how to find what's relevant**
- **Words (BM25):** exact names, numbers and rare terms; misses paraphrases and other languages. 57% on
  conversation (LLP 0019.000).
- **Meaning (embeddings):** paraphrases and other languages; weak on exact tokens. 67.5%.
- **Hybrid (fused ranks):** helps when the question shares words with the data. 65.7% on conversation.
- **Reranking:** a model reads the top candidates with the question and reorders them. Jev where allowed, an LLM
  otherwise.
- **Query rewriting:** paraphrases, a translation, or a guessed answer (HyDE) as extra queries.
- **Filters:** time, kind, person.
- **SQL over typed facts:** counts, sums and state (LLP 0006).
- **Routing:** which stores a question needs (LLP 0013.000).
- **Graph traversal:** entity-centric questions.
- **Agentic search:** several steps.
- **Reading everything:** the upper bound while it fits.
- **When to search:** at the start of a chat, at every turn, or when the model calls a tool.

**Creation: what the memory is made of**
- the raw log
- windows of conversation, with a context header or without
- statements (one fact each)
- consolidation: merged paraphrases with counts, superseded values, plan status
- summaries at levels: per session, per period, and a dossier
- reflections
- an entity graph
- typed tables
- salience (importance, recurrence, recency)
- forgetting

**Tested here:** reranking, rewriting, headers, statement merging, an incremental dossier, and every-turn recall.

## Part A: recall at every turn, scored without a judge

For each of LLP 0018.000's 392 re-told statements F (18 accounts), the turn of the later conversation where the
person said it is found by embedding similarity. Three moments ask the memory, built from the history before the
cut:
- **cue:** the question from LLP 0018 ("what have I told you about …?")
- **reactive:** the turn where the person says F (the assistant's question and their message)
- **proactive:** the moment before: the assistant's question that leads to F, and the person's previous message

**Hit@k.** The memory surfaces, among its top k, what the person said earlier: one of F's verified earlier windows.
- For statements, the statement must come from one of those windows and be about the same thing: cosine with F
  ≥ 0.5.
- No model grades this.

**Creations:**
- windows (`W`)
- windows with a context header, the day's gist from the episode store (`WC`)
- statements (`S`)
- merged statements (`SM`, Part B)

**Searches:**
- words
- meaning
- hybrid
- meaning, then an LLM reranking the top 20
- the LLM rewriting the moment into three queries, fused, then meaning

k is 5 and 10 for windows, and 10 and 25 for statements.

## Part B: creation

- **Merged statements (`statements_merged`).** Statements are taken in time order.
  - Each one's closest earlier statements are the candidates: cosine ≥ 0.55, at most 5.
  - The LLM decides whether it is the same fact, a change of one of them, or new.
  - Same: the count goes up and the dates move. Change: the old one is kept as "was … until".
- **An incremental dossier (`dossier_inc`).** The dossier is updated after every 10 windows of history, instead of
  written from one full read.

## Part C: the end-to-end test

Recall on cue (LLP 0018.000's protocol and judge) for:
- `statements_merged`
- `dossier_inc`
- `conv_best`: the incremental dossier, merged statements, and windows by meaning, both reranked

## Part D: Jev picks the search

Added after Part A's results, before any run of it.

[confirmed] (Adam Zvada, 2026-09-27), from dictation: "Jeff actually would be the router to which kind of search we
should use … we will have multiple types of searches … and Jeff would be the router that will pick up what is the
best … do it".
- **Why.** In Part A, an oracle that picks, for each moment, a first-stage search that finds the earlier mention
  hits 80.2% at 5 windows. The best single search hits 61.4%, and meaning plus rerank 67.7%. Word search alone finds
  12.4% of what meaning search misses.
- **The menu** (first-stage searches over windows):
  - words
  - meaning
  - words and meaning fused
  - rewritten queries by meaning
  - meaning over windows with a date-and-topic header
- **Router (the search is picked):** Jev sees the moment and the menu, with a line on what each search is good at,
  and picks one; hit@5 of that search.
- **Router, two picks:** the two searches Jev rates highest, fused.
- **Picker (the evidence is picked):** the top 5 of every search on the menu are pooled (up to 25 windows), and Jev
  picks the 5 most useful.
- **Real Jev and the OpenAI model, on the maintainer's own data.** His three cuts (LLP 0017, 69 statements, 207
  moments) run the router and the picker twice: with Jev (TypeSafe), and with the OpenAI model in Jev's place.
  Other people's data stays OpenAI-only (LLP 0018#processors).
- **End to end:** `conv_router`, a Jev router (threshold 0.3) over the new stores (the incremental dossier, merged
  statements reranked, windows reranked, windows by meaning, windows by words), against `conv_best`.

**Hypotheses** (written before Part D ran):
- **D1 (picking evidence):** the picker's hit@5 ≥ meaning + rerank hit@5 + 5, pooled over the three moments.
- **D2 (picking the search):** the router's hit@5 ≥ the best single first-stage search + 3.
- **D3 (seeing beats guessing):** the picker ≥ the router + 5.
- **D4 (Jev as the chooser):** on the maintainer's data, Jev's picker hit@5 is within 3 points of the OpenAI
  model's, at ≤ a fifth of its latency.
- **D5 (routing stores end to end):** `conv_router` ≥ `conv_best` - 1, with fewer input tokens.

## Hypotheses

These were written before the runs. Pooled over the 392 statements.
- **R1 (meaning, at every moment):** on windows, meaning hit@10 ≥ words hit@10 + 10, for cue and proactive
  moments.
- **R2 (reranking):** on windows, meaning then rerank hit@5 ≥ meaning hit@5 + 5, pooled over the three moments.
- **R3 (rewriting):** on windows, rewrite + meaning hit@10 ≥ meaning hit@10 + 3.
- **R4 (headers):** `WC` meaning hit@10 ≥ `W` meaning hit@10 + 3.
- **R5 (anticipation is hard):** proactive hit@10 ≤ 0.6 × reactive hit@10 (windows, meaning).
- **R6 (statements are compact):** `S` meaning hit@25 ≥ `W` meaning hit@5, with at most a quarter of the characters.
- **C1 (repeats exist):** after the merge, ≥ 15% of the raw statements sit in items said more than once.
- **C2 (merging doesn't hurt):** `statements_merged` ≥ `statements` - 1, end to end.
- **C3 (keeping current):** `dossier_inc` ≥ `dossier` - 3, end to end.
- **E1 (the best way):** `conv_best` ≥ max(`conv_all`, `full_context`) - 1, at ≤ a quarter of full context's tokens.

## Protocol and budget

- Round 11's benchmark and rules: `LAB_PROCESSORS=openai`, `LAB_PRIVATE_DIR`, aggregates only. The rerankers and
  rewriters are the OpenAI model.
- `deprecated/python/lab/bench/replay.py` (Part A); the new stores and configs in `deprecated/python/lab/memory/` (Parts B and C).
- **Budget:** ≤ $6. Every call is cached.

## Risks

- **The turn of a statement is found by similarity** and may be off by a turn. That blurs reactive and proactive
  moments.
- **Hit@k counts the earlier window, not whether the answer used it.** Part C checks the end to end.
- **The LLM that reranks and rewrites is the one that answers.** Jev, where allowed, is the cheaper reranker, and
  untested here.
