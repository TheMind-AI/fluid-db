# FluidDB

Composable TypeScript conversation memory. One package includes the SDK, SQLite/PostgreSQL/Firestore adapters,
MCP server, CLI, evaluation tools, portable skills and explicit HiveNet feedback.

```sh
npm install @fluiddb/fluiddb@next
```

This is the **v1 preview**, licensed under Apache-2.0. Everything ships in `@fluiddb/fluiddb`; no separate FluidDB
SDK, core, schema or adapter packages are needed.

## Product SDK

```ts
import { Memory, Person, OpenAI, Shim } from "@fluiddb/fluiddb"
import { FirestoreRest } from "@fluiddb/fluiddb/firestore/rest"

const model = OpenAI.model({ apiKey, model: "gpt-6-luna" })
const engine = Memory.create({
  store: FirestoreRest.create({ projectId, accessToken: getGoogleAccessToken }),
  model,
  embedder: OpenAI.embedder({ apiKey, model: "text-embedding-3-small" }),
  decider: Shim.decider(model),
  processors: ["openai"],
})
const memory = Person.bind(engine, authorizedPersonId)
const { prompt } = await memory.recall({ conversation: recentTurns })
```

The host supplies identity, credentials, database connections and background jobs. Store, model, embedder,
decider and picker interfaces remain replaceable. Extraction, linking, dossier and ranking models can be selected
independently; use `/eval` to compare configurations against the same labelled data.

## Entry points

| Import suffix                                    | Provides                                                                      |
| ------------------------------------------------ | ----------------------------------------------------------------------------- |
| root                                             | Memory, Person, OpenAI, Jev, Shim, component ports and errors                 |
| `/sqlite`, `/sqlite/node`, `/sqlite/bun`         | SQLite store and local connections; the store also accepts Durable Object SQL |
| `/postgres`                                      | Adapter for an existing `pg` pool                                             |
| `/firestore`, `/firestore/rest`                  | Node Firestore client and fetch-only Worker adapters                          |
| `/mcp`, `/mcp/http`                              | Person-bound MCP server over stdio or authenticated HTTP                      |
| `/eval`                                          | Dataset parsing, replay and paired component comparisons                      |
| `/feedback`, `/skills`                           | Explicit HiveNet reports and portable agent instructions                      |
| `/client`, `/schema`                             | HTTP client and runtime data schemas                                          |
| `/local`, `/testing`, `/conformance`, `/records` | Reference store, fakes and helpers for custom adapters                        |

These are paths within **one package**, with no dependencies on other FluidDB packages. The root and Firestore
REST imports run in Workers. Node database adapters use the host's clients: install `pg` (plus `@types/pg` for
TypeScript) or `@google-cloud/firestore` when using those adapters. They are optional peers and are not loaded by
the portable SDK.

## MCP and CLI

The same installation provides both executables:

```sh
npx @fluiddb/fluiddb@next skills read fluiddb-memory
npx @fluiddb/fluiddb@next feedback --help
npx --package @fluiddb/fluiddb@next fluiddb-mcp --help
```

Configure the MCP server's memory connection and authorized person as described in the
[MCP guide](https://github.com/TheMind-AI/fluid-db/blob/main/packages/mcp/README.md). Feedback is an explicit action;
no personal memory, environment or conversation context is collected automatically.

The preview preserves original messages, provenance and explicit saves/corrections. Forgetting removes selected
source windows and their derived statements, including neighboring facts from the same window; independent
repetitions remain. The host must connect account erasure and authorization to its own lifecycle controls.

See the [product guide](https://github.com/TheMind-AI/fluid-db/blob/main/docs/product-integration.md),
[component comparisons](https://github.com/TheMind-AI/fluid-db/blob/main/docs/components-and-evals.md), and
[release status](https://github.com/TheMind-AI/fluid-db/blob/main/docs/releasing.md). Broader answer-quality and
product load evaluation remain prerequisites for a production cutover.
