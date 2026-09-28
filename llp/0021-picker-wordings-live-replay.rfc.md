# LLP 0021: A better picker, keeping every wording, and a live replay

**Type:** RFC
**Status:** Draft
**Systems:** Reads, Jev, Research
**Author:** Adam Zvada / Claude
**Date:** 2026-09-27
**Related:** LLP 0020.000, LLP 0019.000, LLP 0018.000, LLP 0015, LLP 0008

## Summary

LLP 0020.000 left three open questions.
- **The picker finds 67.5% at 5 windows, where the pool holds the earlier mention 80.4% of the time.** The picker
  reads each window cut to 800 characters, and many windows are longer. It may simply not see the fact.
- **Merging repeated statements found them (46%), but keeping one wording cost 3.3 points.**
- **Every test so far asked the memory a question.** None let the assistant talk. Would the conversation actually
  be different?

This round tests the three on round 11's benchmark, under the same rules: other people's data goes to OpenAI only,
the analyst sees aggregates, and real Jev runs only on the maintainer's own data.

[confirmed] (Adam Zvada, 2026-09-27): "okey experiment more".

## Part A: a better picker

The replay of LLP 0020 (`deprecated/python/lab/bench/replay.py`), on windows: meaning search's top 20, then a picker keeps 5.
- **P0:** today's picker. One closed question per candidate ("does it hold something useful to recall now?"), on
  the first 800 characters.
- **P1:** the same question on the whole window (up to 3,000 characters).
- **P2:** the same question on a card: the statements the store extracted from that window, with the window's
  date and first 300 characters.
- **P3 (reference):** the LLM ranks the 20 whole windows as a list and returns its top 5.
- **Measured:** hit@5 over the 1,176 moments of the 18 accounts (the OpenAI model in Jev's place), and over the
  maintainer's 207 moments with real Jev (P0-P2), with the pool's ceiling (hit@20).

## Part B: keeping every wording

- **`statements_linked`:** every statement stays its own item. Each is labelled with its group from LLP 0020's
  merge: how many times the fact was said, when first and last, and what changed.
- **`conv_best2`:** the kept-current dossier, linked statements, and windows, both picked by Part A's best picker
  (chosen by the rule "highest pooled hit@5", before Part C runs).
- **Recall on cue,** end to end, as in LLP 0018.000.

## Part C: a live replay

- **The facts.** For each re-told statement F (at most 30 per account, 295 in all), the assistant writes Mind's
  next message at two real moments of that later conversation:
  - **before:** the person is about to re-tell F. The conversation so far is the person's messages and Mind's
    questions up to that point.
  - **after:** the person has just re-told F.
- **The assistant** is an OpenAI model with a fixed prompt standing in for Mind: warm, concise, use what you
  remember when it helps, never invent memories.
- **What it remembers,** injected as text:
  - nothing
  - the product's memory as its prompt builder injects it (`product_injected`)
  - `conv_best2`: the dossier, plus that turn's statements and windows picked from the latest message
  - the whole history
- **Judged by the model, per message:**
  - **Knows:** before F, does the message show it already knows F?
  - **Remembers:** after F, does the reply show it remembers the person said it before?
  - **Invents:** does the message claim to remember something about the person that its memory and the
    conversation don't hold? Judged on a sample of 100 messages per memory.
- **Judge check.** The judges are checked by hand on the maintainer's own data: one of his cuts, the same
  protocol, 30 judgments.

### Amendment: telling the assistant to use its memory

Added after Part C's results, before this part ran; exploratory.
- **Why.** With the best memory injected, the assistant showed it already knew the fact in 5.1% of messages before
  the person re-told it, and referred back in 9.5% after. Yet the same memory knows 73% of these facts when asked.
  The prompt's "use it when it helps" may leave the memory unused.
- **The test.** The same replay with a directive prompt:
  - before replying, check the memory for anything the person said before that relates to this moment
  - if there is something, say so explicitly ("you mentioned in August that …"), so they don't have to repeat
    themselves
  - never claim more than the memory holds
- **Arms:** the product's injected memory, and `conv_best2`, each with the directive prompt.
- **C5 (exploratory):** with the directive, `conv_best2` refers back after F in ≥ 20% of replies and shows it knew
  before F in ≥ 10% of messages, with invented memories ≤ 5%.

## Hypotheses

These were written before the runs.
- **A1 (seeing the fact):** P1 hit@5 ≥ P0 + 5.
- **A2 (cards):** P2 hit@5 ≥ P0 + 3.
- **A3 (real Jev, whole windows):** on the maintainer's data, Jev P1 ≥ Jev P0 + 3, at under 1 s per decision.
- **A4 (closed questions are enough):** the best of P0-P2 ≥ P3 - 3.
- **B1 (keep the wordings):** `statements_linked` ≥ `statements` - 1, and ≥ `statements_merged` + 2.
- **B2 (the new best):** `conv_best2` ≥ `conv_best` + 2.
- **C1 (the conversation changes):** before F, the message shows it knows F:
  - with `conv_best2`: ≥ 25%
  - with nothing: ≤ 5%
  - with the product's injected memory: ≤ 12%
- **C2 (continuity):** after F, the reply shows it remembers: ≥ 40% with `conv_best2`, ≤ 5% with nothing.
- **C3 (against reading everything):** `conv_best2` within 5 points of the whole history on C1 and C2, at ≤ a third
  of the input tokens per message.
- **C4 (no invented memories):** with `conv_best2`, ≤ 5% of messages invent a memory.

## Protocol and budget

- `deprecated/python/lab/bench/replay.py` (Part A: `pick`; Part C: `live`), the new store and configs in `deprecated/python/lab/memory/` (Part B).
- **Budget:** ≤ $12, as the maintainer lifted the per-round limit. Guards per command; every call cached.

## Risks

- **The live replay's conversation is reconstructed.** It is the person's messages and the last question of each
  of Mind's messages, not Mind's full turns.
- **An LLM judges the messages;** the hand check covers 30.
- **The stand-in assistant is not Mind's prompt.** The comparison is between memories under one prompt.
