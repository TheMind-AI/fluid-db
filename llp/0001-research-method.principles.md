# LLP 0001: How research is done here

**Type:** Principles
**Status:** Draft
**Systems:** Research, Evaluation
**Author:** Adam Zvada / Claude
**Date:** 2026-09-25
**Related:** LLP 0000, LLP 0002, LLP 0003

## Summary

The rules every experiment in `evals/` or the archived `deprecated/python/lab/` follows, so results stay cheap, reproducible and honest. A round goes like this:
1. An RFC states its hypotheses first.
2. The experiment runs, cached and under a budget.
3. A Research document records the results against the hypotheses.
4. The findings ledger (LLP 0002) is updated.

## Budget

[confirmed] (Adam Zvada, 2026-09-25): "we have low budget"; round 4 was capped at about $3.
- **A cap per round, enforced in code.** Benches stop when real spend passes `--budget`, as in
  `deprecated/python/lab/bench/scale5k.py` and `deprecated/python/lab/bench/workload.py`.
- **Every model call is cached** (`deprecated/python/lab/.cache/`). A replay is free and reproduces the same numbers. [observed]
  `deprecated/python/lab/common/llm.py`, `deprecated/python/lab/common/jev.py`.
- **`LAB_CACHE_ONLY=1` makes any uncached call raise;** `=llm` still allows Jev. Use it for any re-analysis.
- **Spend is reported as measured** (OpenRouter's usage endpoint) or estimated from logged tokens (OpenAI), and
  labelled which.

The TypeScript runner follows the same rule: cache-only by default, explicit live budget and prices, and
reservations before network requests (LLP 0024#cost-and-caching). Its cache lives in `.cache/evals/`.

## Truth before judges

- **Prefer simulators that carry the exact truth of every message** (`deprecated/python/lab/datasets/simulate_*.py`, `workload.py`), so
  writes and answers are checked by code.
- **Use a model judge only where answers are free text,** and only one validated against a stronger judge: Jev as
  judge agrees with Opus on 96.7% of 300 verdicts (LLP 0002).
- **Say which grader produced a number.**

## Hypotheses first

- **An RFC states hypotheses, thresholds and the protocol before the run** (LLP 0003 is the first). Results go in a
  child Research document and are judged against those thresholds.
- **Deviations are reported, not hidden.** For example: a signal fixed after the first log run, a threshold chosen on
  the test questions.
- **Small samples are called small.** 60-100 questions means differences of 1-2 questions are noise.

## Compile, then check

The recurring design, and the recurring method:
- **An LLM compiles an artifact once:** writer patterns, typed-column extractors, question templates, schema
  migrations.
- **Code validates it against logged or trusted data** before trusting it. Rejects go back to the LLM with the
  reason.
- **Code then runs it with no model.** [observed] `deprecated/python/lab/systems/compiled_writer.py`, `deprecated/python/lab/systems/typing.py`,
  `deprecated/python/lab/systems/compiled_reader.py`, `deprecated/python/lab/systems/schema_advisor.py`.

## Label-free adaptation

Anything the system learns while deployed (patterns, migrations, templates) must learn from signals a deployed
system has: logs, verifier scores, disagreement between readers, user feedback. Never from gold answers. Gold is only
for grading. [observed] LLP 0003#signals.

## Secrets

API keys live only in the git-ignored `.env`. Tracked files are scanned for keys before any commit. [observed]
`.gitignore`.
