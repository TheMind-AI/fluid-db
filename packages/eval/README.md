# @fluiddb/fluiddb/eval

Evaluate the real FluidDB SDK with your own providers and a fresh database, or compare named component variants
on the same ordered dataset. Works with the public `@fluiddb/fluiddb` ports; no Bun, Node or provider imports.
This package is built locally with the other SDK tarballs; it has not been published by this work.

```ts
import { compare, Dataset, evaluate } from "@fluiddb/fluiddb/eval"
import { Pick } from "@fluiddb/fluiddb"
import { Local } from "@fluiddb/fluiddb/local"

const data = Dataset.parse(yourDataset)
const base = { model, embedder, decider, processors: ["openai"] }
const report = await compare(data, [
  { id: "vector", create: () => ({ deps: { ...base, store: Local.store(), picker: Pick.searchOrder() } }) },
  { id: "ranker", create: () => ({ deps: { ...base, store: Local.store(), picker: Pick.usingModel(model) } }) },
])
console.log(report.runs, report.paired)

// One configuration; you own the store and its connection lifecycle.
const single = await evaluate(data, { ...base, store: Local.store() })
```

A factory must create a fresh empty store. For SQL connections return `{ deps, close: () => sql.close() }`;
comparison calls `close` after success or failure. Never point an evaluation at a product's live database.
The dataset's keep limits apply to all variants; IDs and clock are deterministic. Inputs are cloned before replay,
and gold labels are used after recall. Factories receive no labels. Providers are still called normally: use fake
providers for free tests, or inject a cached/budgeted `fetch` into real adapters.

Minimal dataset:

```json
{
  "id": "city-v1",
  "description": "Fictional source retrieval",
  "synthetic": true,
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
      "conversation": [{ "id": "q", "role": "person", "text": "Where do I live?", "at": "2026-02-01T12:00:00Z" }],
      "expected": { "sources": ["city"] }
    }
  ]
}
```

Steps are `ingest` (also folds the dossier), `probe`, or `forget` with `person` and `session`. Expectations support
`sources`, `absentSources`, literal `contains` / `absent`, `retold` and `empty: true`. Every probe needs an
expectation. A labelled source must already have been ingested for that person. Source hits are graded at window
granularity, even for statements; they do not prove that a specific fact survived extraction.

Reports include per-probe checks, per-metric counts, repetition confusion counts and precision/recall (null when
undefined), and recall latency. Comparisons add a canonical dataset SHA-256, component IDs and paired changes
against the first variant. `complete` means every variant finished, not that all checks passed. Failed variants
are unscored and comparisons involving them are marked incomparable; provider error text is omitted.

These are retrieval and behavior evaluations. They do not generate or judge assistant answers, establish
clinical benefit, or reproduce historical Python answer-quality scores. Treat cached latency separately from
live latency and use a held-out dataset before choosing a production default.
