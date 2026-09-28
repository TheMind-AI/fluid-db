# LLP 0014: Different scenarios: a support desk, a sales pipeline, a team's projects

**Type:** RFC
**Status:** Draft
**Systems:** Writes, Reads, Core, Research
**Author:** Adam Zvada / Claude
**Date:** 2026-09-25
**Revised:** 2026-09-25 (Hypotheses unchanged. Protocol deviations, all in LLP 0014.000#deviations:
- write-check vocabulary fixed, with the messages unchanged
- one simulator fix: the support contact after the erasure
- erasure fixed after it leaked and over-erased
- two added arms: a link-aware write context and a neutral gate wording
Results in LLP 0014.000.)
**Related:** LLP 0011, LLP 0013, LLP 0013.000, LLP 0002, LLP 0005, LLP 0009, LLP 0014.000

## Summary

Rounds 1-7 used one kind of data: one person's life. This round runs the same system on three other kinds of work
data, each simulated with exact truth:
- **A support lead's desk:** tickets whose status changes, customers, SLAs, refunds, promises.
- **An account executive's pipeline:** deals moving through stages, contacts, objections, competitors, meetings.
- **An engineering manager's notes:** projects, decisions (some reversed), action items with owners and deadlines,
  incidents, 1:1s, a reorganization.

The system is the same in all three:
- the write path (Jev gate, source guard, planner v2.3, compiled writer; LLP 0005)
- the engine
- the readers
- the memory with several stores (LLP 0013)

There is no domain code, and no prompt changes per scenario. This answers the agenda's biggest open risk (LLP 0011
E3: is it a general, self-building ETL?) and whether round 7's memory holds beyond a personal life.

[confirmed] (Adam Zvada, 2026-09-25), from dictation: "continue in more stuff and experiments … different scenarios".

## What stays fixed, and what changes once

- **Unchanged:**
  - the planner prompt (it still says "everything {user} tells their AI assistant")
  - the Jev gate and source guard
  - the compiled writer
  - the deterministic reader
  - the answerer
- **Changed once, before any scenario runs, and checked on the life data as a control** (the round-7 memory has two
  personal-only parts):
  1. **The five Jev aspects are reworded for any domain.** An intention includes promises and action items; a
     milestone includes launches, reorganizations and new policies.
  2. **Periods come from the milestones, not from the user's home city.** One LLM call turns the dated milestones
     into periods ("before the reorg", "when Leo ran billing"). Routines split by those periods, and also by the
     people they involve, when a subset has ≥ 8 rows.
- **Frozen after that.** Any later fix is reported as a deviation.

## Scenarios

`deprecated/python/lab/datasets/simulate_scenarios.py`. Each covers six months (Jan-Jun 2026), with 350-500 messages. Every message
that states facts carries `truth.checks`: the values some row written from it must hold.

| scenario | the user | what flows in | periods | erased on request |
|---|---|---|---|---|
| `support` | Maya Chen, support lead at Brightdesk | customer emails (documents), her triage and resolution notes, refunds, promises, team changes | 24/7 coverage from May; Leo leaves, Ana takes billing | a customer contact's data (GDPR) |
| `sales` | Daniel Park, account executive at Lumen Analytics | call notes, stage moves, prospect emails, meetings, pricing changes | EMEA added to his territory in April; list price up in May | a prospect's confidential layoffs |
| `projects` | Elena Rossi, engineering manager at Orbit Labs | meeting notes, decisions and reversals, action items, incidents and postmortems, 1:1s, launches | the June reorganization into two teams; the beta launch | a report's medical leave |

Each has 36 questions, 3 in each of round 7's 12 memory types:

| type | support | sales | projects |
|---|---|---|---|
| current state | the status of a ticket | a deal's stage | who owns a project |
| past state | the priority before an escalation | a deal's amount before the discount | a decision before its reversal |
| aggregates | refunds in Q2 | ARR won in Q2 | incidents in Q2 |
| routines | who handles billing tickets | the days demos happen | when the 1:1s with a report are |
| intentions | what was promised and whether it was kept | the next meeting | open action items |
| … | … | … | … |

- **Gold answers are computed from the simulator's truth.**
- **The questions are written with the simulators, before anything runs,** so all 108 are held out by construction.

## Measures

- **Writes:**
  - Write accuracy: the share of truth records whose checks a row (or that message's history entry) holds.
  - The share of messages the compiled writer handles after a 120-message bootstrap.
  - Cost per message.
- **Schema:** the tables each scenario grows into. This is reported, not scored.
- **Reads:**
  - Accuracy per memory type, by the Jev judge, for `log_rag`, `facts_log`, `hybrid_r4` (the round-4 reader),
    `brain_all`, `brain_jev_recall`, `plus_computed` (facts + log + routines + periods) and `full_context`.
  - Tokens per question.

## Hypotheses

These were written before the simulators ran.
- **H1 (the write path generalizes):** write accuracy ≥ 85% in each scenario, with no domain code. It was 95% on the
  personal life in round 4.
- **H2 (code writes a large share):** after the bootstrap, the compiled writer handles ≥ 40% of messages in each
  scenario. This is lower than personal life because work notes vary more.
- **H3 (the memory generalizes):** `brain_all` ≥ `facts_log` + 5 points pooled over the three scenarios (108
  questions; the 95% CI excludes 0), and ≥ `facts_log` in each scenario.
- **H4 (computed stores generalize):** adding routines and periods to facts + log adds ≥ 10 points on routine and
  period questions (pooled; 18 questions).
- **H5 (current state holds):** `facts_log` answers ≥ 80% of current-state and past-state questions in each scenario.
- **H6 (short histories need no memory system):** with 350-500 messages the whole log fits in about 20-30k tokens.
  `full_context` is within 5 points of `brain_all` overall, and below it on aggregates.

## Protocol

1. **Generalize the memory once** ([what changes](#what-stays-fixed-and-what-changes-once)). Re-run `brain_all` on
   the life dev set as a control: it should stay within 3 points of round 7's 94.1%.
2. **Simulate the three scenarios** (datasets + questions). No model is involved.
3. **Ingest each scenario:** tiered writer with bootstrap 120 and recompiles every 150 messages. The spend guard stops
   a scenario at $0.60.
4. **Measure write accuracy against the truth.** No model is involved.
5. **Build the memory stores** for each scenario (erase first), and run the configs on its 36 questions.
6. **Budget:** ≤ $3 for the round, with guards in code. Everything is cached.

## Risks

- **I write the simulators, so the language is regular** (as in rounds 2-7). Real work notes are messier, so H2 is an
  upper bound.
- **"No domain code" includes the planner prompt's personal framing.** If a scenario fails because of that framing,
  the fix belongs in LLP 0005 and is reported as such.
- **The source guard blocks documents from overwriting.** In the support desk, status changes come from the lead's
  notes, not from customer emails. A real help desk's system notifications are documents and would need trusted
  sources.
- **36 questions per scenario;** per-type numbers are 3 questions each.
