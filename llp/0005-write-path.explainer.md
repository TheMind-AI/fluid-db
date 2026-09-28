# LLP 0005: The write path

**Type:** Explainer
**Status:** Draft
**Systems:** Writes, Jev
**Author:** Adam Zvada / Claude
**Date:** 2026-09-25
**Related:** LLP 0004, LLP 0007, LLP 0008, LLP 0002.000, LLP 0002.003

## Summary

How a message becomes rows. Writes are asynchronous, so seconds are fine; the goal is correct structure at low
cost. [confirmed] (Adam Zvada, 2026-09-25): "for write imho we can have higher latency right, that can run async".

```
message -> _log (1 ms)
        -> tier A: compiled patterns                              ~1 ms, no model      (~70-85% of messages)
        -> Jev gate + source guard                                ~0.3 s
        -> tier C: LLM planner -> typed ops -> engine             ~2 s, ~$0.0007
```

## Log first

The message is appended to `_log` before any decision (LLP 0004#raw-log). If every later step fails, nothing is lost,
and the sleep pass re-plans messages that left no trace (LLP 0007#sleep).

## The Jev gate

- **One Jev request asks two things:** the message's intent (share, change, remove, ask or chat) and whether it is
  worth remembering.
- **Messages that are questions or chat, and not worth remembering, get no write.**
- **Measured:**
  - write gate recall 100%, precision 97.6%, at 0.28 s (§4)
  - on LoCoMo it skipped 86 of 848 chunks, which lost the evidence for only 3 of 1,540 questions (§19)
  - it works the same in Czech (§19)
  - [observed] `deprecated/python/lab/systems/jev_layer.py` `gate`
- **Its wording assumes a personal assistant** (round 8, LLP 0014.000#writes).
  - "Worth remembering" is asked as "a fact about the user's life … that a personal assistant should remember", and
    "ask" is skippable. So a customer's support ticket ("we see this: can't update the credit card…") reads as a
    question to the assistant: 56 of 166 new tickets got no row.
  - A domain-neutral wording writes 100% of fact-bearing messages on both the support desk and the personal life,
    and stops 70-80% of chit-chat instead of 98-100%.
  - [observed] `deprecated/python/lab/bench/scenarios.py` `GATE_NEUTRAL`, `deprecated/python/lab/results/scenarios_gate.json`
  - It is not yet the default.

## Source guard

- **A second Jev question, run alongside the gate:** "did the user write this, or is it a pasted document (email,
  receipt, invite, card)?"
- **Documents may insert rows and fill empty columns, but never overwrite or delete.** A delete also needs the user's
  own "remove" intent.
- **Why:** without it, both a GPT-6 Luna and a Claude Sonnet 5 planner followed instructions hidden in a receipt
  (overwrote a phone number) and in a fake contact card (deleted six rows). With it, all four attacks were blocked
  and both legitimate edits went through. [observed] §10, `FluidV2._guard`.

## The planner

An LLM (GPT-6 Luna, low reasoning, is the default) returns typed operations for one message. It sees:
- the catalog
- the existing rows most related to the message
- the message's timestamp

Rules were added version by version, each for a failure the experiments showed ([observed] `deprecated/python/lab/systems/fluid_v2.py`):
- **v2**
  - one table per kind of thing
  - reuse tables and columns
  - `<entity>_id` links
  - one value per column
  - ISO dates
  - update existing rows instead of inserting duplicates
  - resolve relative dates against the message time
  - never invent facts
- **v2.1**
  - never overwrite a fact with another fact of the same kind (several phone numbers get several rows)
  - "forget" removes every mention
  - keep every field of documents
- **v2.2**
  - the engine keeps history, so just write the new value
  - privacy `forget` with redaction
- **v2.3** (what a simulated year exposed, §14)
  - one kind of thing in one table
  - a `category` column with reused lowercase values
  - reuse the exact existing spellings
  - ISO currencies

Known gaps:
- **Kinds that live inside a free-text description** (lunch, dinner and drinks under one `dining`). The query-driven
  advisor (LLP 0003) now derives the column.
- **The late-night date rule** (§24).

## Write context

The planner must see the rows a message may update, so it doesn't insert duplicates.
- **Up to ~150 rows:** the whole database.
- **Beyond that:** BM25 matches for the message, the user's own row, and the most recent rows (for follow-ups like
  "actually it was 18:00").
- **Asking Jev about every row cost as much as the LLM's tokens** and doubled write latency at ~60 rows. [observed]
  §5, `FluidV2._write_context`.
- **Links must be read as names** (writer v2.4, round 8, LLP 0014.000#writes).
  - A deal row holds `organization_id: 9`, not "Humongous Insurance". So "lost Humongous Insurance" matched the
    organization, not the deal, and the planner inserted a second deal. 9 of 24 deals were split across rows.
  - v2.4 appends each linked row's name to the text BM25 reads: 3 of 24.
  - Personal data hid this, because people rows carry their own names. [observed] `FluidV2._write_context` (`link_ctx`)

## Repair round

- **Operations that fail** (a dangling `@ref`, an unknown row) go back to the planner with the error. The successful
  ones are already applied.
- **Only one repair round.** [observed] `FluidV2.remember`.

## Compiled writer

Tier A, since round 4 ([observed] `deprecated/python/lab/systems/compiled_writer.py`, §23-24):
- **Compile.** After a bootstrap of planner-written rows, one LLM call per busy table reads the planner's own
  (message → row) examples. It writes regex patterns that give every column a source: a captured group, a
  constant, the message's date or time, a duration, or a choice.
- **Apply.** A message that exactly one table's patterns match is written by code in ~1 ms, with no model.
- **Result.** 70% of 5,050 messages (77-85% in steady state), 95.5% right against the truth. On the same kinds of
  message the planner scores the same.
- **It only covers event streams** (round 8, LLP 0014.000#writes). Clean examples are rows that exactly one message
  produced and no later message touched. On work data:
  - 62% of an engineering manager's notes (1:1s and standups are events)
  - 8% of a sales pipeline
  - 0% of a support desk: tickets and deals are updated by every later message
  - Stateful entities need update patterns ("#(\d+) resolved: (.*)" → update the ticket with that number).
- **Update patterns (writer v2.5, round 9, LLP 0016.000).**
  - The writer now learns from the planner's operations: `planner_ops`, recorded per message.
  - A message whose only operation updated one row becomes an update example. The LLM writes a regex whose key
    group finds the row (a number, or a name through a link), and code validates it on those examples: the same
    row, the same key values.
  - On the support desk, update patterns wrote 37% of the messages after the bootstrap (203 updates), 92% right.
  - Insert patterns for the tickets themselves failed: the LLM copied the prompt's "MESSAGE (sent …)" framing into
    its regexes. Show the examples bare.

### Pattern validation

- **Code checks every pattern against the planner's rows before trusting it.** Rejected patterns go back to the LLM
  with the reason (up to 3 rounds). With this feedback and the other fixes in §23, coverage of the planner's expense
  examples went from 32% to 92-98%.
- **Only key columns must match exactly:** numbers, dates, currency, kind and merchant. The planner's own free-text
  descriptions are too inconsistent to demand. [observed] `CompiledWriter._validate`.

### Choice columns

A derived value like `category` is often not in the message. It comes from what ≥ 90% of earlier rows with the same
merchant have, or else from a Jev Choice among the column's existing values. [observed]
`CompiledWriter._associated`, `_choose`.

### Nicknames

- **The words a pattern captures for a link column** ("Ondra") are learned as nicknames of the row the planner linked
  them to, when that happens consistently at least twice.
- **Known issue.** The planner's early mistake ("Eva" linked to Lucie before Eva had a row) was learned and repeated
  17 times.
- **Planned:** require ≥ 90% consistency, and let an exact name beat a learned one (LLP 0011). [observed] §24,
  `CompiledWriter._learn_aliases`.

## Recompiling on drift

- **Patterns are recompiled every 500 messages** for tables that tier C kept writing to.
- **The move to San Francisco** (dollars, new shops) cut tier A to 33% of messages until the next recompile.
- **Better:** trigger on a spike in tier-C messages. [observed] §24.

## Known issues

These are where wrong writes came from at 5k messages (235 of 4,537 checked, §24):
- **106 late-night dates.** "Tonight" sent after midnight is filed under the calendar day; a 4 am day boundary would
  fix it.
- **60 merchant spellings,** 28 of them accents only; canonicalization now merges them (LLP 0003).
- **52 companions:** 17 from the wrong nickname, and 35 not linked at all.
- **Duplicate people from early nicknames.** The Jev dedupe merges clear cases and should ask the user about unclear
  ones (LLP 0007#dedupe).
