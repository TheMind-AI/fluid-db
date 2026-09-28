# LLP 0024: Evaluating the TypeScript memory

**Type:** RFC
**Status:** Draft
**Systems:** Evaluation, Research, Core
**Author:** Adam Zvada / Codex
**Date:** 2026-09-27
**Related:** LLP 0001, LLP 0009, LLP 0020, LLP 0023

## Purpose

[confirmed] (Adam Zvada, 2026-09-27): TypeScript is the main implementation from now on; move older code into a
deprecated folder and keep it possible to evaluate the TypeScript implementation.

The Python experiments remain reproducible under `deprecated/python/`. Their accuracy numbers do not establish
the TypeScript package's quality. This protocol establishes a replay entry point and engineering regressions;
a model-quality research round must add its own hypotheses, frozen dataset and thresholds before spending.

## Hypotheses and thresholds

- **H1:** A synthetic conversation replay exercises the real TypeScript memory and SQLite adapter, with all
  labelled retrieval, repetition, isolation and forgetting checks passing using deterministic test providers.
- **H2:** Missing cache entries cannot cause network calls in cache-only mode. A live request whose conservative
  reservation would exceed the configured dollar allowance is rejected before transmission, including parallel
  calls and failures. Transport tests must pass with a stubbed network.
- **H3:** Moving the project preserves the free package, Worker-runtime and distribution checks, and the archived
  Python report can be regenerated with `LAB_CACHE_ONLY=1`.

These are engineering thresholds, not hypotheses that fake providers predict real model quality.

## Replay protocol

`evals/schema.ts` defines ordered ingest, forget and probe steps. Each run starts with a fresh in-memory SQLite
store. Ingest uses the public Memory API and folds the dossier; probes call recall before any later messages are
ingested. Two people can share the store, allowing isolation checks. Stable IDs and a deterministic clock make
the same provider requests reusable from cache. There is no Python subprocess or import in the runner.

Gold labels belong only to the grader. Provider calls receive conversation input and previously derived memory,
never the expected source IDs, text checks or repetition labels. Labelled source turns must already have appeared
for that person; duplicate probe IDs and inconsistent redeliveries are rejected before running.

For a positive probe, score whether any labelled earlier source turn supports a returned statement and a returned
window, separately, at the dataset's configured keep limits. Windows are presented chronologically, so these are
hits within the selected set, not its chronological first k. Statement provenance maps through its source window.
This is a **source-window hit**, coarser than LLP 0020's statement-plus-cosine grader: it does not prove that a
statement from a multi-fact window is about the desired fact. Dossiers are not counted as retrieval hits.

Also report repetition true positives / false positives / false negatives / true negatives. Positive repetition
checks with source labels must identify a statement from a labelled window. Empty-person and forbidden-source /
literal-text checks test isolation and deletion. All checks are explicit; an empty or unlabelled dataset fails.
Reports contain counts, identifiers and booleans, without conversation text or model outputs.

## Cost and caching

The default `bun run eval` uses the existing deterministic testing providers and makes no network requests. The
OpenAI mode uses the production adapters, an OpenAI model as decider, and a fetch wrapper cached by endpoint and
the entire serialized request (including model, prompts, schema and token limit). Credentials are never cached.
Cached responses are private artifacts under ignored `.cache/evals/`.

OpenAI mode defaults to cache-only. `--live` additionally requires a positive dollar budget, explicit model and
embedding token prices, and an API key. Before each request the wrapper reserves an upper estimate based on twice
the UTF-8 request bytes plus 4,096 overhead tokens and the maximum completion tokens. Reservations are synchronous
and retained for the whole run, including failures, so parallel calls cannot reuse an allowance. This conservative
allowance uses caller-supplied rates; it is not a provider-enforced billing limit. Report actual token-based cost
estimates separately; unknown usage stays unknown. HTTP and structured-output retries are disabled in this mode.

All successful responses are cached, including responses later rejected by the production adapter. Re-analysis
must stay cache-only, including when `LAB_CACHE_ONLY` is set. No live calls or private datasets are required for
this cleanup. The initial live adapter permits only OpenAI; adding Jev requires a separate processor decision.

## Results

Record the engineering verification in LLP 0024.000 after running, and link it from the findings ledger. Any future
quality comparison must identify dataset, provider settings, cache provenance, grader and sample size explicitly.
