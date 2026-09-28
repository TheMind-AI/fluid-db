# LLP 0015: How the memory should work, as the evidence stands

**Type:** Explainer
**Status:** Draft
**Systems:** Core, Writes, Reads, Jev, Interfaces
**Author:** Adam Zvada / Claude
**Date:** 2026-09-25
**Related:** LLP 0000, LLP 0002, LLP 0004-0010, LLP 0013.000, LLP 0014.000, LLP 0016, LLP 0016.000, LLP 0017.000,
LLP 0018.000, LLP 0019.000, LLP 0020.000, LLP 0021.000

## Summary

After fifteen rounds (LLP 0002), the design has a clear shape. This document describes it as a whole, part by part,
with how sure we are of each.

The conversation path now has a TypeScript implementation under `packages/` (LLP 0023), with swappable providers,
transactional SQLite storage, durable original messages and a Cloudflare service. The package is verified with
synthetic tests; research accuracy still refers to the Python implementations unless explicitly remeasured.
- **FluidDB is a memory built as one append-only log of everything the user said or forwarded,** plus views that
  are derived from the log and can always be rebuilt:
  - a relational database of facts, with history (the semantic memory)
  - episodes, routines, intentions, preferences and life periods (the other kinds of memory)
  - an app for people
- **Writing turns messages into typed rows.** Recurring kinds are written by compiled code, and the rest by an LLM
  planner behind a Jev gate.
- **"Sleep" consolidates in the background.** It erases what the user asked to forget, dedupes, and derives the
  other stores, the schema changes and the doubts worth asking about.
- **Reading sends each question to what can answer it.**
  - Counting, summing, comparing over time and tracking state go to the facts, whatever the history's length.
    Reading everything miscounts even at 26k tokens (LLP 0016.000).
  - What was said can be read whole when it fits.
  - Jev routes to the stores that hold the answer, SQL computes what can be computed, and one LLM call answers from
    the evidence.
- **The division of labour:**
  - Jev makes the closed decisions (write or not, which found evidence to keep, which store on personal data, is
    this right, is this what to erase).
  - The LLM generates and extracts.
  - Code does everything that can be computed.

[confirmed] (Adam Zvada, 2026-09-25): "do you feel like we are getting into a state like how the memory system, this
database should work? Can you describe it to me then?"

## The picture

```
                  messages, emails, receipts, invites, tickets, notes ...
                                        │
                                        ▼
  ┌──────────────────────────── the log (_log) ────────────────────────────┐
  │ append-only, timestamped, the source of truth; erasure redacts it       │
  └──────┬──────────────────────────────────────────────────────────────────┘
         │ write (async, seconds)                       │ sleep (background)
         ▼                                              ▼
  Jev gate ─► compiled code (tier A) or LLM planner   erase what was forgotten (LLM proposes, Jev confirms,
         │     (tier C), typed ops, engine               the LLM audits paraphrases)
         │                                              statements per window, a dossier from one full read
         ▼                                            dedupe, supersede, schema from the query log
  facts: tables + _history + _src provenance ◄──────  Jev tags every message (5 yes/no aspects)
         │                                            ─► episodes (day gists), routines (code), intentions,
         │                                               preferences, periods and month summaries
         ▼                                                                  │
  read: Jev routes each question by what it needs                           │
          count / sum / compare / current state ─► facts (SQL, verified)    │
          what was said ─► the log (read whole if it fits; else by meaning) │
          conversation ─► statements + the dossier (+ the log by meaning)   │
          habits, plans, likes, life periods ─► their stores ◄──────────────┘
          ─► evidence in a fixed order ─► one LLM answer
  people: SQL, MCP tools, the generated app and the inbox of doubts
```

## 1. One log, the source of truth

- **Everything is stored raw first, before anything is interpreted** (LLP 0004#raw-log). Every derived row points
  back to the messages it came from (`_src`), and every change keeps the old value (`_history`).
- **Why:**
  - The log answers what the tables miss: conversational detail, exact wording, documents. LoCoMo scored 80.1% on
    the database alone and 91.8% with the log (LLP 0002).
  - Every store can be rebuilt when the extraction improves. Round 7 rebuilt seven stores from a cache in 3 s.
- **Status: established.**

## 2. Writing: structure out of messages

- **A Jev gate decides whether a message needs a write at all.**
  - Chit-chat and questions stop here, at 0.3 s and $0.00004.
  - Its wording must fit the domain: the personal wording dropped a third of a support desk's tickets. A neutral
    wording writes everything that carries facts (LLP 0014.000).
- **Tier A: compiled code.**
  - An LLM compiles regex patterns once, from the planner's own work, and code validates them. They then write
    matching messages in ~1 ms, with no model.
  - This covers 70% of a personal life's messages (LLP 0002.003) and 62% of a manager's notes.
  - It covers little where rows keep changing (tickets, deals) until update patterns exist (LLP 0016).
- **Tier C: the LLM planner.**
  - It writes typed operations: create a table or column, insert, update, delete, forget.
  - The engine validates and applies them with provenance and history.
  - It sees the catalog and the rows the message may touch. Those rows must be found by the names they link to, or
    updates create duplicates (writer v2.4).
  - It costs $0.0007 per message.
- **A source guard:** forwarded documents may add rows but never overwrite or delete them. It blocked all four
  injection attacks in round 1.
- **The schema grows from what is written:** a support desk grows `tickets`, a pipeline `sales_leads`, a manager's
  notes `tasks` and `incidents`, all with no domain code (LLP 0014.000).
- **Compiled updates (v2.5)** write recurring changes to existing rows in code. Each pattern finds the row by a key
  in the message ("#1087 → Priya, P2" → `ticket_number`) and falls back to the planner unless exactly one row
  matches. They wrote 37% of a support desk's messages, 92% right (LLP 0016.000).
- **Status:**
  - Established for personal data: 95% of writes right at 5k messages.
  - Established for work data with v2.5: 94-97% on the rows that exist.
  - Open: insert patterns for entities that later change; they failed on a prompt-format error in round 9.
  - Open: conversation.
    - On real therapy talk the planner wrote 158 of 176 rows as mood entries. It kept few people, plans or practices
      (LLP 0017.000).
    - Talk carries statements about a person. A statement store (one fact, a kind, provenance) recalled 66% of
      what people re-told, where facts + log recalled 58% (LLP 0019.000). Counting how often a statement recurs
      still needs a merge that works.

## 3. Consolidation: what "sleep" does

Background passes run over the log and the facts. None is on the hot path.
- **Erasure first.**
  - The LLM proposes what a "forget" request covers: whole messages, or identifying strings.
  - Jev confirms each one ("is this itself what the request asks to forget?").
  - The LLM audits the rows those messages touched for paraphrases, and the request itself is neutralized.
  - Only then are other stores built, so nothing forgotten can come back (LLP 0013.000, LLP 0014.000).
  - **The engine's forget cascades into rows that link to the person.**
    - Erasing a support contact deleted the 19 tickets he opened (LLP 0016.000).
    - On real data, a request to forget the user's own first name deleted every interest and plan linked to the user
      (LLP 0017.000).
    - `forget_links="redact"` keeps linked rows and nulls the link (writer v25r).
    - Still open: turning a request about an attribute into clearing a value, not forgetting the row.
  - Status: established on the tested cases. Without these checks, erasure leaked and over-erased.
- **Facts hygiene.**
  - Dedupe: Jev merges clear pairs and asks the user about unclear ones.
  - Supersession: Jev marks rows a newer row replaced.
  - Schema from the query log: the LLM proposes derived columns and canonical values, and code tests them on a
    scratch copy (LLP 0003.000).
  - Per-row confidence and the inbox: code checks plus Jev reach AUROC 0.99 (LLP 0012.000).
- **The other stores:**
  - Jev asks five yes/no questions of every message ($0.065 per 5k messages). The LLM extracts only from the ~3% that
    pass.
  - Episodes: a gist per day.
  - Routines: code over the facts, per period and per person.
  - Intentions, with status tracked by code.
  - Preferences.
  - Periods: the LLM turns the milestones into "what held when", with month summaries.
  - Total: $0.13 per 5k messages, once.
- **For conversation, two more passes** (LLP 0019.000). Both are cheap: $0.26 for 18 people's histories.
  - Statements: one LLM call per conversation window writes the lasting things it says about the person, embedded
    for search.
  - A dossier: one read of the whole history writes what to know before every conversation, refreshed in the
    background.
- **What matters most is computed memory.**
  - Aggregates come from SQL.
  - Routines and periods are computed over many rows.
  - The episodic, intention and preference stores are mostly cheap indexes over what the facts and the log already
    hold (LLP 0013.000).
  - Routines are only as good as the schema under them: they can split by a person the planner linked, not by one
    written into a title.
- **Status:** the passes are established. The value of each derived store is measured on simulated data only.

## 4. Reading: the right amount of memory for the question

- **The read policy: by what the question needs, not by how long the history is.**
  - Full context fails the same questions at 26k, 51k and 122k tokens: counts, sums, "what was true on that day",
    complete lists (LLP 0016.000). The memory is flat at 89-93% across those lengths.
  - Full context won on round 8's short work histories. They had few countable items, and their facts had write
    errors.
  - So: questions that aggregate or track state go to the facts at any length. Questions about what was said can
    read the log whole when it fits.
  - The whole log plus the computed answer scored between the two (86.1% at 26k). It is only as good as the
    computed answer.
- **The memory read, step by step:**
  1. **Route.** Jev answers "would this store hold what's needed?" once per store, in one request, in 0.3 s. Its
     threshold is the recall knob: at 0.3 it reads about 3 stores. It matches an LLM router's accuracy at 1/11 of the
     latency.
  2. **Compute.** The deterministic reader fills a closed query form with Jev, runs SQL, and has Jev verify the result
     (AUROC 0.95). It runs in about a second, with no LLM (LLP 0006).
  3. **Assemble.** The evidence goes in a fixed store order under a budget: computed answers first, then statements,
     then messages.
  4. **Answer.** One LLM call. It says "I don't know" when the evidence doesn't hold the answer, and never
     reconstructs anything forgotten.
- **Apps and dashboards read SQL directly,** in under 1 ms.
- **Memory must act on cue, not only at the start of a conversation** (LLP 0017.000).
  - On real conversations, a third of what a person said had been said before.
  - A briefing at the start of each chat held at most a tenth of it, from any memory: summaries keep themes, and
    re-telling is detail.
  - Asked on cue, the memory knew about half: 49-54%, against 37-38% for the product's own memory and 66% for the
    whole history read at once.
  - On 18 other people it held (LLP 0018.000): FluidDB 56-58%, the product's memory 40-44%, and the whole history
    70%.
- **For conversation, the read policy adds a case.** Therapy talk is mostly statements about the person, which are
  "what was said".
  - When one person's history fits the context (up to ~85k tokens measured), reading it all recalled the most, in 12
    of 18 accounts.
  - The memory's part is what doesn't fit, and what must be computed: a mood series, follow-up status, counts
    (LLP 0017.000, LLP 0018.000).
- **Memory for conversation: search by meaning, and read statements and a dossier** (LLP 0019.000).
  - Searching the conversation by meaning instead of words recalled +10 points (+24 on facts said in another
    language than the question).
  - Any two of three parts (statements, a dossier, the log searched by meaning) recalled as much as reading the whole
    history: 69-71% against 70%. They used a quarter to a seventh of the tokens, and still work past the context
    window.
- **Jev picks the evidence, not the search** (LLP 0020.000).
  - At every turn, meaning search finds the top 20 statements and windows, and Jev keeps the ones worth recalling:
    +7 points of hit@5 over meaning alone.
  - Real Jev picks as well as an LLM, in 0.33 s against 2.1 s, which is fast enough for every turn.
  - Choosing which search or store to use, from the question alone, didn't beat always using the best one.
  - With a dossier kept current, this recalled 72.1% of what people re-told, against 70.0% for their whole history.
  - Query rewriting and date headers didn't help.
- **The picker reads whole windows, and the memory now beats reading everything** (LLP 0021.000).
  - Cutting windows to 800 characters hid the fact: whole windows +4.8.
  - With every statement's wording kept (repeats linked by count), the best memory recalled 73.0% against 70.0% for
    the whole history, at a quarter of the tokens.
- **Recall isn't use.**
  - In a live replay the assistant rarely brought the memory up: 9.5% of replies after a re-telling.
  - A directive prompt raised that to 45.8%, mostly related detail; the same fact acknowledged, 10.2%.
  - The whole history in the prompt was used least.
  - Round 15 added a re-telling detector, a Jev closed decision per turn: is this already in memory, and which
    item? Strict acknowledgement rose to 22.0% from 10.2%, but false alarms were 30.5% with the OpenAI stand-in and
    21.0% with real Jev on the maintainer's data (LLP 0022.000). The package exposes a tunable threshold; the result
    is evidence for the assistant to check, not proof of repetition.
- **Status:**
  - Routing and the deterministic reader are established.
  - The read policy is supported on one life and three work scenarios. A router rule that sends aggregates to the
    facts even when the log fits is untested.
  - The deterministic reader's plans on schemas it hasn't seen are the current weak point: a count on the wrong
    filter; one relation stored as a link in some rows and as text in others.
  - The answer step is one Luna call; a stronger model is untested.

## 5. Jev, the LLM and code: who decides what

| decision | who | why |
|---|---|---|
| write or not; the source of a message; which found evidence to keep (every turn, 0.33 s); which store (personal data); is the computed answer right; is this what to erase; dedupe merges; message aspects | Jev | closed choices with probabilities; 4-6× cheaper and 7-33× faster than an LLM, and as accurate on these (LLP 0013.000) |
| typed operations; extraction; gists and summaries; erasure proposals; the final answer | LLM | generation |
| patterns, SQL, aggregates, routines, validation, status tracking, erasure itself | code | exact, instant, free, testable |

- **Jev is weaker where details must be compared** (does this row match its message?: AUROC 0.68 vs Luna's 0.81).
- **Its probabilities can be low even when its ranking is right,** so thresholds are chosen from data.
- **The rule:** the LLM compiles or proposes, code executes, and Jev decides the closed questions in between.

## 6. Interfaces

- **SQL** on a catalogued schema, where every table and column has a description (LLP 0004).
- **MCP tools** for agents (`remember`, `ask`, `sql`, `schema`; `deprecated/python/lab/mcp_server.py`).
- **A generated app:** tiles from the logged questions, person cards and breakdowns from the schema, and an inbox of
  doubts with one-click fixes that are written back as typed updates (LLP 0012.000).

## What is established, and what is open

| part | status | evidence |
|---|---|---|
| log + facts + history + provenance | established | rounds 1-8 |
| the write path on personal data | established (95%) | round 4 |
| the write path on work data | established with v2.5 (94-97% on existing rows) | rounds 8-9 |
| compiled writes for events | established (62-87% of messages) | rounds 4, 8, 9 |
| compiled writes for entities that change | updates work (37% of support messages, 92% right); inserts open | round 9 |
| exact aggregates by SQL | established | rounds 2-7 |
| verifiable forgetting | established on the tested cases, with Jev confirming | rounds 2, 7, 8 |
| several stores beat one, at scale | established on one simulated life (+9 held-out) | round 7 |
| read everything when it fits | only for questions that don't aggregate; full context miscounts at any length | rounds 8-9 |
| reads on schemas the reader hasn't seen | weak: plans with wrong filters | round 9 |
| erasure that redacts instead of cascading | an engine option (v25r); forgetting an attribute is still open | rounds 9-10 |
| memory for an agent doing tasks | open (E4) | – |
| real, messy data | 19 people's conversations: on cue, FluidDB 49-58%, the product's memory 37-44%, reading everything 66-70% | rounds 10-11 |
| memory for conversation: meaning search, statements and a dossier | supported on one benchmark: equals reading everything at 1/4-1/7 of the tokens | round 12 |
| Jev picking the evidence at every turn | supported: +6-12 over the best single search; as good as an LLM at 1/6 the latency | round 13 |
| routing to a search or store from the question | not supported on conversation: no gain, or -3.2 end to end | round 13 |
| every wording kept with repeat counts; a picker on whole windows | supported: the memory beats reading everything (73.0% against 70.0%) | round 14 |
| the assistant using its memory in a live conversation | weak: 9.5% of replies refer back; 45.8% with a directive; the same fact 10.2% | round 14 |
| a re-telling detector (Jev, per turn) | acknowledgement improves (10.2% → 22.0%); false alarms remain high | round 15 |

## What it is not

- **Not a vector store of chunks.** Retrieval over raw text was the weakest store alone on personal and work data
  (49-70% in rounds 7-8), where questions count and track state. On conversation, retrieval by meaning is the
  strongest single part (67.5%, LLP 0019.000). It is one part among several, and the statements and the dossier
  built from the same log do as well.
- **Not replaced by long context.** A model that reads everything still miscounts, loses dates and misses list
  items, at 26k tokens as at 122k. When a history fits, it can read the words; the counting belongs to the
  database.
- **Not finished on real data.** Two rounds on conversations with a therapy assistant: one person, then 18 others
  (LLP 0017.000, LLP 0018.000):
  - Compiled code wrote nothing. Real conversation doesn't repeat templates.
  - The planner made a mood diary of it.
  - What a person tells an assistant is statements about themselves, which the tables catch poorly.
  - Every other number here comes from simulators with exact truth.
