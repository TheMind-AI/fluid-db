# LLP 0002: What we know so far

**Type:** Research
**Status:** Draft
**Systems:** Research, Writes, Reads, Jev, Evaluation
**Author:** Adam Zvada / Claude
**Date:** 2026-09-25
**Related:** LLP 0000, LLP 0001, LLP 0003

## Summary

This is the findings ledger of the 2026 revisit, one line of evidence per finding. The narrative, the tables and every
caveat are in `deprecated/python/lab/REPORT.md`, cited by section (§). Each finding has a confidence:
- **high:** exact truth, or several runs agree
- **medium:** one run, a judge in the loop, or a small sample
- **low:** suggestive only

Update a finding when a later round changes it. Mark it superseded; don't delete it.

## TypeScript engineering verification

**High confidence, engineering scope only:** the root TypeScript project passes 140 tests, a 15-probe replay with
50 explicit checks, local Cloudflare runtime verification, and external package consumption. Its synthetic replay
uses the production memory and SQLite code with deterministic providers. Cached OpenAI mode refuses a cache miss
without sending a request; live mode requires prices and a reserved budget. Python is archived intact and its
report still regenerates cache-only. [observed] LLP 0024.000, `evals/`, `test/evals.test.ts`.

No real-model quality claim follows from these checks. Findings below continue to describe the Python experiments
unless a later record explicitly remeasures them on TypeScript.

## SDK, adapters and research retrieval parity

**High confidence, bounded scope:** the real cached 18-account/392-question export is available without spending.
The TypeScript SQLite read path selects exactly the Python statement/window order on all 391 completed questions.
One near-tied candidate-order difference is an exact-prompt cache miss. Full evidence text is byte-identical on
315 completed cases; creation and answer/grader replay are not fully established. The historical 73.0% is still
Python's score, not a newly measured TypeScript score. [observed] LLP 0023.001.000.

**High confidence, engineering scope:** the SDK now provides explicit saves, corrections, inspection, source
lookup, complete long-input extraction and persisted revision checks. PostgreSQL, Node/Bun SQLite and Firestore
(Node and fetch-only REST) share storage conformance. The MCP server uses the same person-bound service.
This resolves the input-prefix and basic explicit-write gaps below; BetterMind deployment, record migration,
viewer UI adaptation and production quality/latency validation remain separate. [observed] LLP 0023.001.000.

## Agent feedback and memory skills

**High confidence, engineering scope:** explicit HiveNet feedback works through a portable SDK, CLI and opt-in
MCP tool. Structured failures preserve replayable task criteria and thread replies without collecting memory
or environment context. Portable skills work through the official MCP Skills extension and older resource/tool
paths; manifests match their exact served bytes. A live, user-authorized smoke report was acknowledged. This
does not establish new memory-quality results. [observed] LLP 0023.002.000.

## BetterMind integration audit

**Bounded product continuity evidence (2026-09-28):** the published TypeScript SDK's tools retain a saved fact
across 30 live conversational messages and a fresh-session probe, return readable source evidence, and remove
selected forgotten sources. Complete legacy migration now passes 155-record and real Firestore 105-note
regressions. OpenAI credits explained the reproduced provider-readiness failure; app loading required separate
retry handling. The planned 20 intervening user turns was not completed under the conservative $2 reservation
cap. This is not a comparative accuracy or native voice reliability result. [observed] LLP 0024.002.000.

**High confidence, engineering scope only:** BetterMind and FluidDB share a compatible TypeScript/Cloudflare
architecture, but their explicit-write, metadata, forget and input-processing contracts differ. A deterministic
5,968-character input probe showed Fluid retaining the complete raw message while omitting its tail from both the
extraction prompt and derived window, then marking the turn processed. The existing backend's selected 55 tests
passed; its viewer was inspected using fictional records. [observed] LLP 0023.000. The proposed integration and
viewer adaptation remain unimplemented, and no comparative model-quality result is claimed.

## Rounds

| round | record | budget |
|---|---|---|
| 1: does it work now? | LLP 0002.000 | ~$32 |
| 2: a year, Mem0, public benchmarks, forgetting | LLP 0002.001 | ~$80 |
| 3: deterministic, cheap and fast | LLP 0002.002 | ~$0.08 |
| 4: 5,000+ memories | LLP 0002.003 | ~$1.00 |
| 5: a schema shaped by its queries | LLP 0003 (RFC), LLP 0003.000 (results) | ~$1.03 |
| 6: the generated app and the inbox | LLP 0012 (RFC), LLP 0012.000 (results) | ~$0.12 |
| 7: a memory with several stores, like a brain | LLP 0013 (RFC), LLP 0013.000 (results) | ~$1.6 |
| 8: different scenarios: support, sales, projects | LLP 0014 (RFC), LLP 0014.000 (results) | ~$2.45 |
| 9: writer v2.5, and when a memory beats reading everything | LLP 0016 (RFC), LLP 0016.000 (results) | ~$2.25 |
| 10: real conversations with a therapy assistant (private data) | LLP 0017 (RFC), LLP 0017.000 (results) | ~$2.37 |
| 11: 18 other people's conversations (private, OpenAI only) | LLP 0018 (RFC), LLP 0018.000 (results) | ~$7.66 |
| 12: memory for conversation: meaning search, statements, a dossier | LLP 0019 (RFC), LLP 0019.000 (results) | ~$3.55 |
| 13: search and creation, done properly; Jev picks | LLP 0020 (RFC), LLP 0020.000 (results) | ~$5.20 |
| 14: a better picker, every wording kept, and a live replay | LLP 0021 (RFC), LLP 0021.000 (results) | ~$11.08 |
| 15: the re-telling detector | LLP 0022 (RFC), LLP 0022.000 (results) | ~$0.60 |
| next | LLP 0011 (agenda) | – |

## Writes

- **The LLM planner builds a sensible, queryable schema from raw messages.**
  - The simulated year settles at 17 tables around message 450. [high] §14
  - Plain SQL over it answers 10 of 11 checked aggregates exactly. [high] §14
- **Most writes can be done by code the LLM compiled.** At 5,050 messages over 2.5 years:
  - regex patterns compiled test-driven from the planner's own rows wrote 70% of messages (77-85% in steady state)
    in about 1 ms
  - those writes were 95.5% right against the truth; on the same kinds of message the planner scores the same
    (expenses: 92.3% vs 92.2%)
  - the run cost $0.85, against about $3.50 all-LLM
  - [high] §24, `deprecated/python/lab/results/scale5k_writes.json`
- **Where the wrong writes come from.** 235 wrong writes out of 4,537 checked, by cause:
  - 106 are late-night "tonight" messages filed under the next day
  - 60 are merchant spellings, 28 of them accents only
  - 52 are companions: 17 from one nickname learned wrong (the planner's early "Eva" → Lucie link, then copied by
    code)
  - [high] §24
- **Drift is real.** A move to another city (new currency, new shops) dropped code's share to 33% until the patterns
  were recompiled. [high] §24
- **Duplicate people from early nicknames.** Jev dedupe merges clear cases (p = 0.91). The unclear ones (0.58-0.72)
  should be asked. [medium] §24

## Reads

- **A deterministic reader works:** Jev fills a closed query form, and SQL and code compute the answer.
  - On the year it answers 77% of questions without an LLM in ~0.8 s for $0.0004, at 86%. [medium] §21
  - At 5k it scores 66.7% alone. [medium] §25
- **The hybrid of the two readers:** deterministic when the verifier gives ≥ 0.7, else the LLM agent.
  - It matches the agent: 81.9% vs 80.6% at 5k, and 82.5% vs 81.7% on the year.
  - It answers 57-64% of questions in under a second.
  - [medium: the threshold was picked on the same questions; the gaps are 1-2 questions] §25
- **The verifier must see the plan,** not just the result. [medium] §25
- **At scale, the weak point is filter choice over messy columns,** not the stored data and not SQL speed. [high]
  §25; addressed by LLP 0003.
- **Routing needs the stored values the question names.** Round 5 found that "visits to Field" went to the wrong
  table: a table card shows only a few example values. Passing value mentions to Jev fixed it.
  - [medium] LLP 0003.000
  - The round 3-4 numbers above were measured before this change.
- **Physical design is not the bottleneck at personal scale.** SQL takes milliseconds; model round trips take the
  rest. [medium] §21; the round-5 measurement is in LLP 0003.000.

## Schema from queries (round 5)

Details in LLP 0003.000.
- **A schema evolved from a question log answers future questions better.**
  - The hybrid rose from 61.0% to 70.5% on 105 unseen questions (run B; run A: 58.1% to 68.6%).
  - Novel question shapes rose by 20 points.
  - There was no regression elsewhere.
  - No gold answers were used, and the optimization cost < $0.10.
  - [medium: one simulated workload, two runs]
- **Code-side tests keep kept migrations correct.**
  - The derived lunch/dinner/drinks column is within 1% of the truth.
  - Location columns had 0 errors where filled.
  - The verifier's replay alone had accepted a column that filed coffee under "drinks"; data checks are needed too.
  - [high for the kept ones]
- **The advisor's proposals vary between runs:** whether a concept is proposed, and its scope. [medium]
- **Compiled questions answer recurring shapes in ~4 ms** instead of ~1 s:
  - 14-24 of 40 recurring questions, at 88-100% accuracy
  - none of the new phrasings
  - [medium]
- **Physical design matters only at about a million rows,** and only once the reader pushes filters into SQL:
  - the reader's current path takes 3.4-5.3 s at 1M rows
  - SQL takes 68-121 ms
  - SQL with indexes counted from the log takes 1.6-38 ms
  - at 5k rows all of it is below 20 ms
  - [high for the timings]
- **Reading the log exposes reader bugs too:** routing that needs value mentions, and counts restricted by the answer
  column. [medium]

## Doubt, the inbox and the generated app (round 6)

Details in LLP 0012.000.
- **The database can know which of its rows are wrong.**
  - Code checks plus a generic Jev check rank the wrong rows first with AUROC 0.988. Reviewing the 5% least-confident
    rows catches 80% of them; Jev alone catches 46%.
  - This cost $0.049 for 4,571 rows.
  - [high on this data; the code signals are in-sample]
- **A small inbox fixes much of the data.**
  - Answering 100 items (2% of rows) fixes 71 wrong rows: 3.6% → 2.0%. A random 100 fixes 4.
  - Code suggests a fix for 99 of the 100 items, and every suggested value is right.
  - [high, with a perfect oracle user]
- **An app can be generated from the query log and the schema.**
  - Every tile's SQL agrees with the numbers shown.
  - It reads 73.75% of future questions of logged shapes exactly (75% after the inbox), and 44% of novel ones.
  - The misses are data and schema gaps.
  - [medium]
- **A short log yields few tiles.** The year database's 60 questions gave 5 tiles and 43% of its aggregation
  questions, so tiles should also come from the schema. [medium]
- **Views must match the reader's rules,** such as the same value picked for two columns counting as one concept.
  [high]

## Several stores (round 7)

Details in LLP 0013.000: 94 development questions in 12 memory types, plus 36 held out.
- **Several stores beat one by about 9 points when all are read.**
  - All seven stores vs the round-4 reader: +9.7 held-out (95% CI +1.4 to +19.4), +9.0 untuned.
  - With Jev routing to about 2 stores: +4.2 and +4.3, not significant.
  - Tuning on the development set doubled the apparent gain (+16.5).
  - [medium: one simulated person, 36 held-out questions]
- **The stores that matter compute.**
  - Facts: aggregates 75% → 12.5% without them.
  - Routines: routine questions 81% → 44% without them; +9.6 when added alone to facts + log.
  - Periods: +6.9 when added alone.
  - Episodes, intentions and preferences mostly repeat facts + log here: -0.5 to +2.6 when added alone.
  - The lesion test found one clean dissociation (routines), not three.
  - [medium]
- **Full context loses to the memory.** The whole 5k log (122k tokens) scores 78.6% vs 91.4% for the Jev-routed
  memory (1.6k tokens), on 35 questions. It miscounts, mixes up days and keeps stale state. [medium]
- **Consolidation is cheap as a cascade.**
  - Jev asks five yes/no questions of every message: 25k answers for $0.065.
  - The LLM reads only the 3% that pass (the intention gate at p ≥ 0.15 keeps 97%).
  - The whole build cost $0.13 for 5,050 messages.
  - Multi-label beats a single label: one label per message dropped "She loves orchids" inside a birthday message.
  - [high on this data]

## Other kinds of data (round 8)

Details in LLP 0014.000: a support desk, a sales pipeline and a team's projects (197-675 messages each; 108
questions written before any run). No domain code.
- **The write path generalizes to work data.**
  - Write accuracy: 96.8% (projects), 94.1% (sales), 82.6% (support). Each scenario grew a fitting schema (tickets,
    sales leads, tasks and incidents).
  - Two fixes it needs:
    - **Gate wording for any domain.** The personal wording dropped 56 of 166 support tickets; the neutral wording
      writes 100%.
    - **Link-aware write context (v2.4).** Split deals went from 9 of 24 to 3 of 24.
  - [high: exact truth]
- **The compiled writer covers event streams, not stateful entities:** 62% / 8% / 0% of messages after the
  bootstrap. [high]
- **Histories that fit in context need no memory system.**
  - Full context scores 91.2% pooled (5-21k tokens) vs 81.9% for the best memory, and wins on aggregates too.
  - The memory wins at scale (round 7: 5,050 messages).
  - [medium: 36 questions per scenario]
  - **Refined by round 9: the deciding variable is the question, not the length.** On one life with many countable
    events, full context miscounts at 26k tokens as at 122k, and the memory is ahead at every length (LLP 0016.000).
- **The multi-store memory's gains over facts + log shrink here:** +5.6 pooled (CI 0 to +11.6). Computed stores are
  only as good as the schema under them: routines can't split by a person the planner stored as text. [medium]

## Writer v2.5 and the crossover (round 9)

Details in LLP 0016.000.
- **Compiled update patterns work.**
  - Tier A now writes changes to existing rows: 37% of a support desk's messages after the bootstrap (v2.3: 0%), 92%
    right.
  - With the neutral gate and link-aware context: 97.3% of writes right on the tickets that exist (v2.3: 88.3%), at
    20% lower cost.
  - [high: exact truth]
- **Better writes didn't move the answers** (+1.4). The misses moved to the reader: count plans with the wrong
  filter on a new schema, and a relation stored as a link in some rows and as text in others. [medium]
- **No single crossover length.**
  - The same 36 questions at 26k, 51k and 122k tokens of history: the Jev-routed memory 90.3 / 93.1 / 88.9; full
    context 83.3 / 90.3 / 83.3.
  - Full context fails counts, sums, intervals and complete lists at every length.
  - Aggregating and state questions belong to the facts, whatever the length.
  - [medium: 36 questions per point, CIs include 0 at each]
- **Erasing a person cascades into records that belong to someone else:** 19 support tickets. It should redact
  instead. [high]

## Real conversations (round 10)

Details in LLP 0017.000. One person's own chats with a therapy assistant: the first real data, kept private.
- **People re-tell a lot.**
  - 33% of the lasting things said in a session had been said in earlier sessions (69 of 208), 38% after a 14-month
    break.
  - Most: how they want to be talked to, practices, work.
  - [medium: one person, LLM-extracted and window-verified gold]
- **A briefing at the start of a chat doesn't prevent it.** Every memory's briefing held ≤ 10% of what was then
  re-told, reading everything included. Summaries keep themes; re-telling is detail. [medium: judge checked against
  40 hand labels, 38 agree]
- **Asked on cue, the memories built before each session knew:**
  - the product's own memory: 37-38%
  - FluidDB: 49-54% (+16, CI 6 to 27)
  - the whole history read at once: 66% (+12 over FluidDB, CI 3 to 22)
  - [medium: 69 questions, Jev judge]
- **FluidDB's gap to reading everything is mostly cross-language:** 27% on facts first said in Czech against 60% in
  English, because the log is searched by words. [low: 13 statements]
- **The write path turned therapy talk into a mood diary.**
  - 158 of 176 rows are mood entries, and people are nearly absent.
  - No pattern compiled: real conversation doesn't repeat templates.
  - [high]
- **Forgetting the user's own first name deleted 54 rows** through the forget cascade.
  - `forget_links="redact"` (writer v25r) keeps them.
  - A request about an attribute must not forget the row. [high]
- **All of this holds for other people** (round 11, LLP 0018.000). 18 accounts, OpenAI only, with an OpenAI model
  in Jev's place:
  - 36.5% re-told
  - on cue, the product's memory 40-44%, FluidDB 56-58% (+12 to +17), the whole history 70% (+14 over FluidDB)
  - FluidDB ahead of the product's memory in 15 of 18 accounts
  - [medium: 392 questions; the shim judge matches Jev on 80.5%]
- **For one person's history up to ~85k tokens, reading it all recalls the most** (12 of 18 accounts). Beyond the
  context window, searching the raw conversation beats a store of summaries. [medium]
- **Three new parts close the gap to reading everything** (round 12, LLP 0019.000; 392 questions, 18 accounts):
  - searching the conversation by meaning instead of words: +10.1 (+23.9 on facts said in Czech)
  - statements extracted per conversation: 66.2% at 1,112 tokens per question
  - dossier + statements + the log: 70.3% against 70.0% for the whole history, at a quarter of the tokens
  - any two of the three do as well
  - [medium: one benchmark, the OpenAI shim as judge]
- **Jev should pick the evidence, not the search** (round 13, LLP 0020.000):
  - Picking among found candidates: +6-12 over the best single search.
  - Picking the search from the question: no gain (-1.5). Routing stores end to end: -3.2.
  - Real Jev picks as well as an LLM (+1.0) at a sixth of the latency (0.33 s).
  - [medium: judge-free replay of 1,176 moments; 207 with real Jev]
- **The best conversation memory so far:** a dossier kept current, plus statements and windows picked by Jev.
  - 72.1% against 70.0% for the whole history, at a quarter of the tokens.
  - Merging repeated statements finds them (46%) but loses detail if one wording replaces the others (-3.3).
  - [medium]
- **The picker must see the fact:** whole windows +4.8 over 800-character cuts. Real Jev picks as well as the
  OpenAI model at 0.33 s. [medium]
- **A memory now beats reading the whole history:** 73.0% against 70.0% (+2.9, CI 0.9 to 5.0), at a quarter of the
  tokens (round 14, LLP 0021.000). [medium: the OpenAI shim as judge]
- **In a live conversation, recall isn't the bottleneck; use is.**
  - With the best memory, the assistant referred back to the fact in 9.5% of replies after it was re-told.
  - A directive prompt raised that to 45.8%, mostly related detail.
  - Strictly the same fact: 10.2%.
  - Reading everything was used least (2.0% strict).
  - [medium: model judges, 29 checked by hand]
- **A memory that shows only recent material forgets by drift.**
  - The product's prompt gets memories chosen by recency and a profile that recent chats rewrote.
  - At a 14-month return it knew 15% of what was re-told.
  - [medium]

## Memory quality

- **A detector improves acknowledgement but is not a reliable fact-equivalence classifier.**
  - Existing round-15 results (LLP 0022.000): strict acknowledgement rose from 10.2% to 22.0%, with 0 invented
    memories in the 54 detector-condition replies. The OpenAI stand-in raised false alarms on 30.5% of new-only
    turns. Real Jev took 0.32 s on the maintainer's data, with 21.0% false alarms.
  - [medium: model judges and a small reply sample; no new run in the TypeScript packaging work]
  - The v2 package exposes the threshold and the evidence, and does not treat the detection as authorization.

- **Structured memory beats fact lists on aggregates and state.**
  - Mem0 on the year: 71% on aggregates; FluidDB: 93%.
  - Forgotten numbers remain in Mem0.
  - [medium] §15
- **Keep the raw log next to the database.**
  - LoCoMo: database only 80.1%, database + raw log 91.8%; full context 93.0%; Mem0 92.5%.
  - LongMemEval: database only 70.3%, database + haystack 93.1%.
  - Both benchmarks fit in a context window, so they don't need a memory system.
  - [medium] §16
- **Forgetting can be verified and made complete.**
  - The planner alone fully erases 3 of 8 requests; with a Jev audit, 6-7 of 8.
  - Deterministic propagation erases 8 of 8 in 5-20 ms, with nothing left behind and nothing else lost.
  - [high] §18, §22
  - **Round 7 correction: this was not applied at scale.** The round-4 database, which rounds 5-6 reused, still held
    both forgotten phone numbers in its log and Marco's number as a row. Erasure as the first consolidation step fixed
    it; an audit finds none of the forgotten strings in any store (LLP 0013.000). [high]
  - **Round 8: erasure needs a second opinion** (LLP 0014.000#forgetting).
    - On work data it both leaked and over-erased:
      - a confidential note "cleared" by update survived as a paraphrase in history
      - the forget request itself named the secret
      - the erasure LLM proposed unrelated messages, and later a whole account
    - Now: Jev confirms every whole-message and string erasure, the LLM audits the erased message's rows for
      paraphrases, and the request is neutralized.
    - After that, no forgotten content appears in 1,008 answers. [high on these cases]

## Jev

Jev answers closed questions only (choice, score, yes/no) with calibrated probabilities, at $0.042 per 1M input tokens
and ~0.3 s per call.
- **As a judge** it agrees with Opus on 96.7% of 300 verdicts, for $0.006. [high] §20
- **Good at:** write gating, entity resolution, closed slot filling, verifying plans, and confirming merges when it
  sees the user's own messages (LLP 0003.000).
  - For merges, messages beat rows: 0.84-0.96 vs 0.56-0.76 for the same true variants; 0.05-0.08 for different
    merchants.
- **Weak at:** judging schema design. [medium] §12
- **As a router it matches an LLM at 1/11 of the latency:** 0.28 s vs 3.1 s per question, with equal accuracy on 130
  questions. Its probability threshold is a recall knob (0.5 → 0.3: +2-3 points). [medium] (LLP 0013.000)
- **As the decider for consolidation** (the same decisions, same batches as GPT-6 Luna):
  - Equal on memory aspects (AUROC 0.997-1.0 vs 0.990-1.0) and answer verification (0.952 vs 0.932).
  - Worse on row faithfulness (0.68 vs 0.81) and routing (0.89 vs 0.92).
  - 4-6× cheaper and 7-33× faster.
  - Its scale is conservative for some questions: at p ≥ 0.5 it finds 69% of intentions, yet ranks them near
    perfectly. Choose thresholds from data.
  - [medium] (LLP 0013.000)

## Evaluation

- **First-save erasure needs a generation even before any memory exists.**
  - [observed] LLP 0023.003: independently reproduced a save reading revision=null, waiting for embeddings,
    then committing after another instance erased the empty person. Every adapter now retains a fresh
    content-free revision on erase. Shared conformance catches the empty-person and repeated-erase cases.
  - PostgreSQL, SQLite and both Firestore adapters pass the updated tests. Host account revocation remains
    necessary to prevent newly started post-erasure writes. [high: deterministic race reproduction]

- **The TypeScript SDK now has a live-tested, portable component comparison.**
  - [observed] `evals/results/2026-09-28-components.json`, LLP 0024.001.000: all five variants (vector, LLM
    ranking, LLM closed questions, Jev, mixed router) pass 50/50 checks on 15 fictional probes. Source-window
    and source-statement hits each 10/10; repetition 5 TP, 5 TN, 0 FP, 0 FN.
  - 154 real requests, estimated $0.01460 using standard token rates; cache replay reproduces every check with
    zero requests. Every component uses the public TypeScript SDK.
  - [inferred] This fixture is too easy to distinguish configurations. No new production default or general
    accuracy claim follows. Historical Python answer accuracy is still not a TypeScript end-to-end score.
  - [high for regression behavior; insufficient for comparative model quality]

- **Published memory scores come from harnesses that favor them.** Mem0's harness uses answer prompts with
  test-specific rules and a lenient judge. [high] §17
- **Tuning on the questions you score inflates gains.**
  - Two passes of generic fixes on the development set doubled the memory's apparent gain.
  - The untuned first run and 36 held-out questions written after freezing agree with each other (+9.0 and +9.7).
  - Keep a held-out set, and report the untuned run. [high] (LLP 0013.000)
- **Check the checker.** Round 8's first write-accuracy numbers (support 5%, sales 75%) were errors in the check:
  - ticket numbers stored as integers
  - "negotiating" for "negotiation", "closed" for "resolved"
  - values a later message changed
  After the fixes: 82.6% and 94.1%. The messages and the system were unchanged. [high] (LLP 0014.000)
- **Check how a baseline's answers are parsed.**
  - GPT-6 Luna renamed the keys of 64 of 69 answers, and reported confidence in its own yes/no instead of P(yes).
  - The first parse made its verifier AUROC 0.54 instead of 0.93, and made Jev look far better than it is.
  - [high] (LLP 0013.000)
