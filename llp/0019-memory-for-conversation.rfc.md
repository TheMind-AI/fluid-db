# LLP 0019: Memory for conversation: meaning search, statements and a dossier

**Type:** RFC
**Status:** Draft
**Systems:** Reads, Research
**Author:** Adam Zvada / Claude
**Date:** 2026-09-26
**Related:** LLP 0018.000, LLP 0017.000, LLP 0015, LLP 0013, LLP 0011

## Summary

Rounds 10-11 measured recall on cue on 19 people's real conversations with a therapy assistant. Reading a person's
whole history knew 70% of what they later re-told, FluidDB 56-58%, and the product's memory 40-44% (LLP 0018.000).
Three findings point at new parts:
- **The facts tables hold little of what people say in conversation;** FluidDB's knowledge came from its log search.
- **Word search misses paraphrases and languages:** facts first said in Czech were found less often (LLP 0017.000).
- **What people say to a therapist is lasting statements about themselves,** and they repeat them.

This round adds four new parts to `deprecated/python/lab/memory/` and tests them on round 11's benchmark: the 18 accounts and 392
re-told questions, with the same gold, judge and processor rules.

[confirmed] (Adam Zvada, 2026-09-26), from dictation: "based on the learnings … do you want to do some changes to the
model or do some experiments with the new Lego block".

## The new parts

- **Embeddings** (`deprecated/python/lab/common/llm.py`, `embed`): OpenAI `text-embedding-3-small`, cached per text, behind the same
  processor check. OpenAI is the only provider other people's data may go to (LLP 0018#processors).
- **`log_embed`:** the conversation windows ranked by meaning (cosine over embeddings), plus the days a question
  names.
- **`log_hybrid`:** the word ranking (BM25, `log`) and the meaning ranking fused by reciprocal rank.
- **`statements`:** a statement store.
  - From every window, the LLM writes the lasting things it says about the person: one fact per line, third person,
    a kind.
  - Paraphrases are merged when their embeddings are close (cosine ≥ 0.88). A merged statement keeps its latest
    wording, how many times it was said, and when it was first and last said.
  - A question reads the 25 closest statements.
- **`dossier`:** one full read.
  - The LLM reads the whole history once and writes a dossier: people, work, circumstances, health, recurring
    struggles, practices and how they went, plans and their status, how the person likes to be talked to, and key
    events.
  - Every item carries dates and how often it came up.
  - A question reads the whole dossier.
- **Configs:**
  - `log_rag` (the word-search baseline)
  - `log_embed`, `log_hybrid`, `statements`, `dossier`
  - `conv_all`: dossier + statements + hybrid log, with a 48k-character evidence budget

## Hypotheses

These were written before any of the new parts was run on the benchmark. Pooled over the 18 accounts, on the 392
re-told questions:
- **H1 (meaning beats words):** `log_hybrid` - `log_rag` ≥ +5. On facts first said in Czech (46 questions), ≥ +10.
- **H2 (statements are the unit):** `statements` ≥ `facts_log` (58.4%).
- **H3 (one full read):**
  - `dossier` comes within 5 points of `full_context` (70.0%).
  - It uses at most a fifth of full context's input tokens per question.
- **H4 (together):** `conv_all` ≥ 65%, and ≥ `facts_log` + 7: half the gap to reading everything.

## Protocol and budget

- **The benchmark:** round 11's datasets, databases and gold (`LAB_PRIVATE_DIR`), `LAB_PROCESSORS=openai`. Jev's
  questions are answered by the shim, and grading is `jev_grade` through the shim, as in round 11.
- **The baselines are round 11's graded runs** (`facts_log`, `brain_jev_recall`, `full_context` and the product's
  memories). The new configs and `log_rag` are added to the same runs.
- **Report:**
  - pooled and per-account means, and wins per account
  - paired bootstrap CIs
  - the Czech subset
  - build cost and input tokens per question
- **Budget:** ≤ $8. Build caches per account, every call cached. The analyst sees aggregates only, as in round 11.

## Risks

- **The statement extractor resembles the gold's extractor.** The store's prompt is worded differently, but both
  write third-person statements. That may make the judge's match easier.
- **The dossier is written by the same model that answers.** A dossier that drops a fact loses it for good; the
  hybrid keeps the log as the fallback.
- **The embedding threshold (0.88) is chosen before the run,** not tuned on it.
