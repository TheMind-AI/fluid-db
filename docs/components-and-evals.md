# Swappable components and comparisons

`Memory.create` takes independent building blocks. Defaults retain the conversation-memory pipeline; replacing
one component does not require a new storage adapter, MCP server or fork of the engine.

| Building block               | SDK setting                                                | Default / alternatives                                                              |
| ---------------------------- | ---------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| Storage and vector search    | `store: Store`                                             | SQLite, PostgreSQL, Firestore, in-memory; custom adapters use the conformance suite |
| Embeddings                   | `embedder: Embedder`                                       | OpenAI-compatible or your own implementation                                        |
| Extraction, linking, dossier | `model`, `models.extract`, `models.link`, `models.dossier` | Omitted stages use `model`                                                          |
| Evidence selection           | `picker: Picker`                                           | `Pick.usingDecider`, `Pick.usingModel`, `Pick.searchOrder`, `Pick.route`, or custom |
| Existing picker switch       | `options.pick`                                             | `"decider"` (default) or `"model"`; model mode uses `models.pick ?? model`          |
| Repetition detection         | `detector: Decider`                                        | Uses `decider` when omitted; independently replaceable with Jev or an LLM shim      |
| Detection policy             | `options.detect`, `options.threshold`                      | Enable/disable detection and vary its probability threshold                         |

Example with two ranking methods and a separate extraction model:

```ts
import { Memory, Pick } from "@fluiddb/fluiddb"
import { Jev, Shim } from "@fluiddb/fluiddb"

const jev = Jev.decider({ apiKey: openRouterKey })
const memory = Memory.create({
  store,
  model,
  embedder,
  models: { extract: extractionModel, dossier: summaryModel },
  decider: Shim.decider(model),
  detector: jev,
  picker: Pick.route({
    statement: Pick.usingModel(rankingModel),
    window: Pick.usingDecider(jev),
  }),
  processors: ["openai", "openrouter", "typesafe"],
})
```

Use the actual processors authorized for your data. All overrides and every routed provider are checked when
the memory is constructed. The picker receives `{ kind, moment, items: [{ id, text }], top, chars }` plus an
optional abort signal. It must return at most `top` distinct IDs drawn from those candidates. It gets copies of
candidate text, with no store handle or evaluation labels. Providers are trusted application code; the SDK
validates their outputs but is not a sandbox for arbitrary JavaScript.

`Pick.route` chooses a picker by statement/window kind. Both stores are still searched. This is not a port of
the archived research engine's SQL planner or its fact/routine/period routing. Storage remains behind `Store`,
and an OpenAI-compatible gateway can be used through the provider adapter's `url` and `processors` settings.

## Verify and compare

From this repository, no keys or paid calls:

```sh
bun run eval
bun run eval:compare
bun run check
```

`eval:compare` runs vector order, model ranking and closed-question picking on the same 15-probe fixture with
deterministic fake models. It reports per-metric scores and paired improvements/regressions. These free checks
verify integration behavior, not semantic quality.

Real-model comparisons add `--provider real --config file.json`. They default to exact-request cache replay;
the first run needs `--live --budget USD`. Choose variants with `--variants vector,model,decider,jev,routed`.
Jev variants require an explicitly fictional dataset and priced Jev configuration. The first three use the same
OpenAI detector; the Jev/routed variants use Jev for detection as well as their declared ranking work.
See [evaluation commands and metrics](../evals/README.md).

For a product test suite, import `evaluate` or `compare` from [`@fluiddb/fluiddb/eval`](../packages/eval/README.md). Pass a
factory for each configuration with a fresh store. Swap one block at a time to attribute changes; the library
accepts custom providers and adapters, while the repository's paid CLI deliberately supports only its priced
OpenAI/Jev endpoints. Reports identify the chosen components, failures and the frozen dataset.

An engineering test count, a retrieval hit rate and answer accuracy measure different things. The historical
72.96% score belongs to Python's answer benchmark. The TypeScript fixture checks retrieval, repetition and
deletion; it does not yet score a therapist model's answers on that full benchmark.
