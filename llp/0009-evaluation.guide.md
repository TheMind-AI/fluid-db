# LLP 0009: How experiments are evaluated

**Type:** Guide
**Status:** Draft
**Systems:** Evaluation, Research
**Author:** Adam Zvada / Claude
**Date:** 2026-09-25
**Related:** LLP 0001, LLP 0002

## Summary

TypeScript is evaluated from the repository root: `bun run eval` runs a labelled synthetic conversation replay
through the real Memory API and SQLite store. `evals/README.md` documents the format and cached OpenAI mode;
LLP 0024 defines grading and budget controls. The default providers are deterministic fakes, so the regression
score establishes engineering behavior, not real model quality. CI includes this replay in `bun run check`.

Historical datasets, graders, benchmarks and benches remain in `deprecated/python/lab/`, with commands in
`deprecated/python/lab/README.md` (run from `deprecated/python/`). The sections below describe that archived Python
bench. Their scores must not be attributed to TypeScript. Both follow LLP 0001.

## Simulators

Seeded generators that write a user's messages and record the exact truth behind each one, so writes and answers are
checked by code (`deprecated/python/lab/datasets/`):

| dataset | what | used in |
|---|---|---|
| `alex_rivera` | the 2024 eval, 90 questions | round 1 |
| `life_stream` (+ `life_stream_cs`, Czech) | 7 weeks with corrections, deletions, receipts and emails | round 1 |
| `life_year` (`simulate_year.py`) | 817 messages over a year: a move, number changes, forgets; 30 + 60 questions at 6 and 12 months | rounds 2-3 |
| `life_scale` (`simulate_scale.py`) | 5,050 messages over 2.5 years; per-message truth on 4,537 of them; 72 questions | round 4 |
| `workload` (`workload.py`) | 80 logged + 105 future questions over `life_scale`, with gold computed from the truth | round 5 |

What simulators can't give: the irregularity of real users. Real-data pilots are in LLP 0011.

## Graders

- **Rules against truth.** Numbers, dates, counts, names and money per currency, by code
  (`deprecated/python/lab/bench/workload.py` `grade`, `deprecated/python/lab/common/grade.py` `rule_grade`). This is the first choice wherever the truth
  is exact.
- **Jev as judge.** One Choice per answer (correct, partial or incorrect). It agrees with Claude Opus 5.5 on 96.7% of
  300 verdicts and costs $0.006 for 300 (`deprecated/python/lab/common/grade.py` `jev_grade`, validated by
  `deprecated/python/lab/bench/judge_pairs.py`, §20).
- **Claude Opus 5.5 as judge.** The reference judge for rounds 1-2 (`deprecated/python/lab/common/judge.py`); it costs about half a cent
  per answer.
- **Write accuracy.** Every stored row is compared with the message's truth (`deprecated/python/lab/bench/scale5k.py` `writes`). No
  model is involved.

## Public benchmarks

Both public benchmarks are run under their own protocols, prompts vendored in `deprecated/python/lab/bench/external/`:
- **LoCoMo, under Mem0's memory-benchmarks protocol** (`deprecated/python/lab/bench/locomo.py`).
- **LongMemEval_S, with its original prompts and judge,** on a 101-question stratified sample
  (`deprecated/python/lab/bench/longmemeval.py`).

Neither needs a memory system at 2026 context sizes: full context scores ~92-93% (§16). Published memory scores come
from harnesses that favor them (§17).

## Benches

| round | benches |
|---|---|
| 1 | `components` (Jev vs LLMs on single decisions), `scale` (retrieval with 1k and 5k distractors), `structure` (is the database correct), `query_modes` (every way to query, timed), `jev_queries` (compiled SQL templates), `injection`, `baselines` (markdown, raw log, database + log), `czech`, `schema_critic`; `deprecated/python/lab/run_e2e.py` for end-to-end runs |
| 2 | `year` (ingest with checkpoints; evaluate readers), `sleep`, `mem0_year`, `locomo`, `longmemeval`, `forget` |
| 3 | `judge_pairs`, `det_read` (the deterministic reader on the year) |
| 4 | `scale5k` (tiered ingestion, write accuracy, passes, reads) |
| 5 | `workload` (query log, advisor, compile, evaluate, latency, physical) |

## Caching and cost accounting

- **Every model call is cached** under `deprecated/python/lab/.cache/` (`llm/`, `jev/`), keyed by the full request. A re-run replays for
  free.
  - `LAB_CACHE_ONLY=1` makes any uncached call raise; `=llm` still allows Jev.
  - `LAB_NO_CACHE=1` forces live calls, for latency.
- **Jev spend is measured** from the OpenRouter key's usage endpoint. OpenAI spend is estimated from the token counts
  in the cache files, because the key can't read usage.
- **Latency comes from live runs only.** A cached replay's wall time means nothing.
- **Per-question cost in parallel runs** must be computed per batch or per tag. A shared ledger misattributes it
  across threads (§25).
