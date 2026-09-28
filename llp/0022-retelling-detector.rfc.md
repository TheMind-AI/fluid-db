# LLP 0022: A re-telling detector

**Type:** RFC
**Status:** Draft
**Systems:** Reads, Jev, Research
**Author:** Adam Zvada / Claude
**Date:** 2026-09-27
**Related:** LLP 0021.000, LLP 0020.000, LLP 0015, LLP 0008

## Summary

LLP 0021.000 found the bottleneck has moved from recall to use.
- The memory holds 73% of what people re-tell.
- Even when told to use it, the assistant acknowledged the fact the person was repeating in only 10.2% of replies.
- It mostly brought up related things.

The missing step is knowing, at the turn, that this is a repeat. That is a closed decision.
- Jev sees the person's message and their closest statements from memory. It picks the one they are repeating, or
  none.
- On a pick, the assistant is told that the person said this before, and to acknowledge it.

This is the last research step before the memory becomes a package (LLP 0023).

[confirmed] (Adam Zvada, 2026-09-27), from dictation: "if you need to build something more, do it. But then we want
to prepare it for production-ready version 2".

## Design

- **Candidates:** the 40 linked statements closest in meaning to the person's message and the assistant's question
  before it (`statements_linked`, LLP 0021).
- **The decision:** one Jev choice over the candidates plus "none": is the person, in this message, telling the
  assistant something it already knows from earlier conversations, and which statement is it? A pick counts when
  its probability is at least 0.5.
  - The OpenAI shim answers on other people's data.
  - Real Jev answers on the maintainer's.
- **Detection, scored without a judge:**
  - **Positives:** each re-told statement F (the 295 of LLP 0021's live replay) at its turn. The pick is right when
    it comes from one of F's verified earlier windows and its cosine with F is at least 0.5, as in LLP 0020 Part A.
  - **Controls:** turns where every lasting statement the person made was new. Any pick there is a false alarm.
- **The conversation:** LLP 0021's live replay after F, with the directive prompt. On a pick, the prompt adds: "They
  told you this before (first on DATE, N times): STATEMENT. Acknowledge that you remember it; don't make them
  explain it again."
  - Measured by the strict same-fact rate, and invented memories on the same sample.

## Hypotheses

These were written before the runs.
- **D1 (detection):**
  - The detector picks F's earlier statement at ≥ 50% of re-telling turns.
  - It raises a false alarm on ≤ 20% of new-only turns.
- **D2 (the conversation):** with the detector, the strict same-fact rate is ≥ 30% (directive alone: 10.2%), and
  invented memories stay ≤ 5%.
- **D3 (Jev):** on the maintainer's data, real Jev detects within 5 points of the OpenAI model, at under 1 s.

## Budget

≤ $2. Every call cached. The rules of LLP 0018#processors hold.
