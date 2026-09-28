# LLP 0018: Other people's conversations: does round 10 hold beyond one person?

**Type:** RFC
**Status:** Draft
**Systems:** Reads, Writes, Research
**Author:** Adam Zvada / Claude
**Date:** 2026-09-26
**Related:** LLP 0017, LLP 0017.000, LLP 0015, LLP 0001

## Summary

Round 10 (LLP 0017.000) measured, on the maintainer's own chats with a therapy assistant, how much people re-tell and
which memory would have known it. The maintainer is also the product's developer, and many of his sessions were
tests. This round repeats the recall-on-cue test on other people who used the assistant for their own lives.

[confirmed] (Adam Zvada, 2026-09-26), from dictation: "i wanna u to get also other user how had true nto tetsig
converstion get them i want it … we wont push the tarces fromt he conevrstion to the repo".

## Processors

Other people's conversations are health data, and they agreed to the product's privacy policy, not to this lab.
- **What the policy covers.** Among its purposes are "understand product usage and improve Mind". Its legal bases
  include legitimate interests in "improving Mind" and consent to processing special-category information. It names
  OpenAI as a processor for "AI text or voice responses, summaries, and memories".
- **So the conversations go to OpenAI only.**
  - `LAB_PROCESSORS=openai` makes any other provider raise before a request is made (`deprecated/python/lab/common/llm.py`,
    `check_processor`).
  - Jev's questions are answered by the same OpenAI model, in Jev's answer shape (`deprecated/python/lab/common/jev.py`, `ask_llm`).
  - The results are therefore FluidDB with an LLM deciding what Jev decides elsewhere.
- **The analyst sees aggregates only.** Claude runs the lab. Every command prints counts, rates and alias-level
  flags; logs that could hold text stay in the private directory and are not read.
- **No one reads these conversations for this round.**
  - There is no side-by-side reading and no hand labelling.
  - The judge is validated on the maintainer's own data instead.
- **Storage.** Exports, datasets, databases and results live under `LAB_PRIVATE_DIR` (git-ignored, in the product's
  repository), as in round 10. Nothing from them enters the tracked lab except aggregate numbers.

## Selection

- **Candidates:** the 40 accounts with the most chats (aliases U1-U40; the alias map is private).
- **Excluded:**
  - the maintainer's own and team accounts
  - accounts in erasure
  - accounts that used forget (their memory input has a cutoff)
  - known minors
  - incognito chats
  - [observed] `.context/fluiddb-probe/screen.ts`
- **Eligible:** at least 8 chats with 5 or more of the person's messages, active in at least 4 different weeks, and
  at least 150 of the person's messages.
- **Genuine use.** For each eligible account, the OpenAI model reads a sample of the person's messages. It answers
  whether this is a person using the assistant for their own life, or someone testing or demonstrating the product.
  Accounts with p ≥ 0.7 qualify.
- **Up to 6 accounts,** the largest that qualify. A cost cap, not a sample design: they are the heaviest users.

## Protocol

Per account, the recall-on-cue test of LLP 0017#amendment-recall-on-cue, with one cut:
- **The cut:** the history is the person's first 300 windows at most, and at most 60% of their windows. The sessions
  after it are up to 20 chats.
- **The gold:**
  - The statements the person made in the sessions after the cut.
  - Each is located in the history by the model reading all of it, plus BM25, and verified window by window.
  - Each is scored for being a lasting fact (p ≥ 0.2, as in round 10).
- **One question per re-told or changed lasting statement,** answered by six memories built from the history before
  the cut:
  - `product_injected`: what the product's prompt builder injects
  - `product`: all the product's stored memory, searched
  - `facts_log`, `brain_all`, `brain_jev_recall` (its router here is the OpenAI model)
  - `full_context`: the whole history read at once
- **The judge** is `jev_grade`, answered by the shim.
  - It is validated first against real Jev's verdicts on round 10's graded answers, the maintainer's own data.
  - They agree on at least 85%, or its numbers are reported with that caveat.
- **No briefings.** Round 10 showed that briefings hold at most a tenth of what is re-told, for every memory.
- **Controls** (new statements) are omitted: round 10 showed they don't test hallucination.

### Amendment: selection and a smaller cut

Made after the genuine-use check and before any account was exported.
- **18 of the 24 eligible accounts read as genuine use** (p ≥ 0.7). 6 read as testing (p 0.02-0.68) and are left
  out.
- **The six largest genuine accounts are U5, U1, U19, U11, U8 and U25.**
- **At 300 windows before the cut, those six would cost about $7.** The cut is now at most 150 windows and 60% of
  them, with at most 15 chats after. That brings the six to about $4.

### Amendment: all qualifying accounts

Made when the maintainer lifted the budget, after five of the six accounts had been scored.

[confirmed] (Adam Zvada, 2026-09-26): "Limits lifted, continue the work."
- **The six largest accounts remain the pre-registered test.** The hypotheses are judged on them.
- **The other 12 genuine accounts run the same protocol, as an extension:** U21, U22, U23, U26, U27, U28, U29,
  U30, U33, U36, U39 and U40. Pooled numbers are reported for the six and for all 18.
- **The spend guards are raised.** u5's gold hit its $0.30 guard and was rerun with a higher one.

## Hypotheses

These were written before any other account's data was processed.
- **H1 (re-telling generalizes):** pooled over the accounts, ≥ 20% of the lasting statements after the cut had
  already been said before it (round 10: 33%).
- **H2 (the order holds):** pooled, on the re-told statements:
  - `full_context` > `brain_jev_recall` > `product_injected`
  - `brain_jev_recall` - `product_injected` ≥ +10 points
  - `full_context` - `brain_jev_recall` ≥ +5
- **H3 (showing, not keeping):** for accounts whose product memory is the older kind (topics, notes and session
  notes), `product` ≥ `product_injected` + 5. Searching every session note beats the last ten.
- **H4 (a diary):** in each account's database, the largest table holds ≥ 50% of the rows. Compiled code writes
  < 10% of the windows.
- **H5 (languages):** pooled, `full_context` - `brain_jev_recall` is at least 10 points larger on facts first said
  in Czech than on facts first said in English. This holds if both groups have 15 or more statements; otherwise it
  is not tested.

## Budget

- ≤ $4 for the round, all OpenAI, with guards.
  - Each ingestion: $0.40.
  - Each gold: $0.30.
  - Each memory run: $0.60.
  - The round stops when the total passes $4.
- Every call is cached.

## Risks

- **An LLM in Jev's place** decides the gate, routing and labels with uncalibrated probabilities. The comparison
  between memories within this round is fair; comparison with round 10's Jev numbers is not.
- **The heaviest users are not typical users.**
- **The questions are in English;** the conversations may not be.
