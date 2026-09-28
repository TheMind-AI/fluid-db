# TypeScript evaluations

The runner replays ordered conversations through the TypeScript `Memory` API and a fresh SQLite store.
The same grader ships as [`@fluiddb/fluiddb/eval`](../packages/eval/README.md) for product test suites. It uses
the same extraction, embeddings, linking, dossier, picker, detector and forgetting code as the library. No Python
is involved. Protocol and limits: [LLP 0024](../llp/0024-typescript-evaluation.rfc.md).

## Free regression suite

From the repository root:

```sh
bun run eval
bun run eval --dataset evals/fixtures/conversations.json --out .cache/evals/results/regression.json
```

The default providers use word overlap and deterministic responses from `@fluiddb/fluiddb/testing`. These checks
verify the pipeline's behavior, not semantic model quality. They require no keys and make no network calls.
`bun run check` includes the fixture in CI. Unit tests separately check that broken extraction fails the grader,
cache-only never sends a request, and concurrent/failed requests cannot reuse the budget allowance.

## Real models and cached replay

Copy `evals/config.example.json` to `.context/eval-config.json` and choose models your OpenAI account supports.
The example records the research model names; it is not a promise of model availability. Replay cached requests:

```sh
bun run eval --provider openai --config .context/eval-config.json
```

This is cache-only. On a miss it stops without calling a provider, even if `.env` contains keys. A first live run
requires `OPENAI_API_KEY` in the root `.env`, an explicit budget in USD, and this additional config field with
**current prices in USD per million tokens**:

```json
"prices": { "input": 0, "output": 0, "embedding": 0 }
```

Replace all three zero placeholders with the applicable positive rates; zeros are rejected. No price defaults are
provided because a stale or unpriced model would make a budget misleading. Then:

```sh
bun run eval --provider openai --config .context/eval-config.json --live --budget 0.25
# Subsequent analysis: omit --live and --budget. The same requests replay free.
bun run eval --provider openai --config .context/eval-config.json
```

The single-run command uses only OpenAI, including the LLM decider. Every successful raw response is cached by the full request
at `.cache/evals/openai/`; changing prompts, schemas, models or token limits requires fresh cache entries. The cache
contains model responses, potentially private text; it is ignored by git and never packed. Keys are not cached.
Historical Python caches use different protocols and are not silently reused.

Before sending, the runner reserves a conservative estimate using twice the request's UTF-8 bytes plus 4,096
overhead tokens, the maximum output tokens, and your prices. Reservations remain used even for failed requests;
this may stop a run before its actual bill reaches the budget. `reservedUsd` and the token-based `estimatedUsd`
are reported separately; `unknownUsage` counts live requests without usable usage. HTTP and output retries are
disabled. This guards the configured allowance, not your provider account's billing. `LAB_CACHE_ONLY` also blocks
`--live`. No live calls are needed for the normal workflow.

## Compare components

```sh
# Free integration checks with deterministic fake providers:
bun run eval:compare
# Real models, exact cached requests only:
bun run eval:compare --provider real --config .context/eval-config.json --variants vector,model,decider
# First paid run, a single shared cap across every variant:
bun run eval:compare --provider real --config .context/eval-config.json --variants vector,model,decider,jev,routed --live --budget 3
```

Each variant starts from an empty SQLite database. Extraction, embedding, linking and dossier calls reuse the
same request cache; only changed requests can spend. The frozen fixture keeps 2 windows and 3 statements.
Both JSON and Markdown reports go to `.cache/evals/results/comparison.*` (or `--out path.json`).
The first variant is the baseline. Exit 1 means a failed variant or a regressed check against that baseline;
`complete` means execution finished, not that all quality checks passed. `bun run eval` remains the absolute
pass/fail regression gate.

| Variant   | Picker                                        | Repetition detector     |
| --------- | --------------------------------------------- | ----------------------- |
| `vector`  | Preserve vector-search order                  | OpenAI closed questions |
| `model`   | OpenAI ranks the list                         | OpenAI closed questions |
| `decider` | OpenAI answers closed questions per candidate | OpenAI closed questions |
| `jev`     | Jev answers closed questions per candidate    | Jev                     |
| `routed`  | OpenAI for statements, Jev for windows        | Jev                     |

Synthetic mode uses fake models/deciders and supports the first three variants. Jev requires `config.jev` with
`model` and `inputPrice` (USD per million tokens), and `OPENROUTER_API_KEY` for live calls. Only a dataset declaring
`synthetic: true` may run the Jev/routed variants. Unknown/private datasets default to OpenAI-only. A declared
synthetic flag is an assertion by the caller, not automatic anonymization.

Use optional `config.models.extract`, `.link`, `.dossier`, `.pick` and `.detector` to change individual OpenAI
models. Each is `{ "model": "...", "maxOutputTokens": 4096, "effort": "low", "prices": { "input": ..., "output": ... } }`.
All configured stages must have prices in live mode. `.pick` affects ranking and the closed-question picker;
`.detector` changes the OpenAI detector. Jev variants select Jev for detection explicitly.
For arbitrary routers, custom pickers or storage adapters use the [SDK comparison API](../packages/eval/README.md).

`estimatedUsd` counts new successful requests with usage; `unknownUsage` includes failed/ambiguous live requests.
`estimatedUncachedUsd` prices every returned response (including cache hits), an estimate of that variant without
the local disk cache. It uses regular input rates, not provider prompt-cache discounts. `variantUsage` separates
new requests from cache hits. Recall p50/p95 and network time are measured but mixed cache latency must not be
used to declare a provider faster. Paired reports retain every changed probe/metric and failed variants have no
score. Comparison hashes canonical parsed dataset JSON; the single-run command retains its file-byte hash.

## Dataset format

[The public dataset schema](../packages/eval/src/schema.ts) validates JSON before any provider call. See [the fixture](fixtures/conversations.json) for
a complete example. A minimal dataset:

```json
{
  "id": "my-recall-eval",
  "description": "Synthetic example",
  "keep": { "statements": 3, "windows": 2 },
  "steps": [
    {
      "type": "ingest",
      "person": "sam",
      "session": "january",
      "turns": [{ "id": "city", "role": "person", "text": "I live in Valencia.", "at": "2026-01-01T12:00:00Z" }]
    },
    {
      "type": "probe",
      "id": "city-recall",
      "person": "sam",
      "conversation": [
        { "id": "question", "role": "person", "text": "Where do I live?", "at": "2026-02-01T12:00:00Z" }
      ],
      "expected": { "sources": ["city"] }
    }
  ]
}
```

Steps run in order; a probe's current conversation is not ingested. Ingest folds the dossier. A forget step is
`{ "type": "forget", "person": "sam", "session": "january" }`; it deletes that source session and rebuilds
the dossier. Turns follow `@fluiddb/fluiddb/schema`: role is `person` or `assistant`, timestamps are ISO strings, IDs are
stable within a person. Reusing IDs with changed content or another session is rejected. Labelled sources must
already have been ingested for the probed person; IDs and gold expectations never enter provider prompts.

Expectations can combine:

| Field                 | Check                                                                                              |
| --------------------- | -------------------------------------------------------------------------------------------------- |
| `sources`             | At least one named source turn backs a selected window and a selected statement, scored separately |
| `absentSources`       | No selected window, statement or repetition detection comes from any named turn                    |
| `retold`              | Whether a repetition was detected; positive cases with source labels must identify that source     |
| `contains` / `absent` | All listed literal strings occur / none occur in the rendered memory, case-insensitively           |
| `empty: true`         | No dossier, windows, statements or repetition detection for this person                            |

Each probe needs an expectation. Literal checks suit synthetic canaries and exact facts; they do not judge the
correctness of free-form prose. For statement retrieval the source is its whole window, so a hit alone does not
establish that the statement covers the desired fact in a multi-fact window. Dossiers do not count as retrieval
hits. `windows@N` means membership in the selected set; display order is chronological. Repetition confusion
counts describe detector presence; `retoldSource` separately checks that it selected the labelled source.

Results contain per-probe booleans, source-free aggregates, config and a SHA-256 of the input file. They omit raw
conversation text and model outputs. Reports default to ignored `.cache/evals/results/`; keep private datasets and
custom detailed reports outside tracked paths. A failed expectation or interrupted/provider-failed run exits
nonzero. Only a completed run is marked `status: complete` in the report.

Historical benchmarks and their original graders remain runnable in
[`deprecated/python/lab/`](../deprecated/python/lab/README.md). This runner is the new conversation-memory
evaluation entry point; it does not claim parity with all historical SQL, LoCoMo or LongMemEval protocols.

## Original research retrieval replay

The original data is private. Set `LAB_PRIVATE_DIR` to its existing folder; do not place it in tracked fixtures.
Export exact cached inputs to an ignored directory, then run TypeScript:

```sh
LAB_CACHE_ONLY=1 LAB_PROCESSORS=openai PYTHONPATH=deprecated/python .venv/bin/python -m lab.bench.ts_replay "$PWD/.context/research-parity"
bun evals/research/replay.ts .context/research-parity
```

The exporter enforces cache-only OpenAI access. The TypeScript runner uses SQLite, real cached embeddings and
exact-prompt cached picker outputs. Labels are loaded only after retrieval, and missing prompts are reported.
It imports historical memories: this tests the read path, not new extraction or a fresh end-to-end answer score.
Only aggregate metrics go to stdout. See [the recorded results](../llp/0023.001.000-sdk-adapters-mcp.research.md).
