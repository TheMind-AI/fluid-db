# FluidDB

TypeScript conversation memory: original messages → searchable windows and statements → linked repetitions and
changes → a dossier. Every turn retrieves relevant evidence and can detect a repeated fact. The result is typed
data plus a prompt section for your assistant.

TypeScript is the main project. The implementation and its design are described in [LLP 0023](llp/0023-memory-v2.spec.md).
The earlier Python prototypes, research lab and historical evaluations are preserved in
[`deprecated/python/`](deprecated/README.md). Historical quality scores describe those Python implementations.

The [BetterMind integration assessment](llp/0023.000-bettermind-integration.research.md) maps the remaining product
contracts and the memory viewer components we can reuse. It is an assessment, not a completed backend integration.

## Try it without keys

Requires Bun 1.3.14 and Node 22 or newer (Node is used to verify packages and run Cloudflare's local runtime).

```sh
bun install --frozen-lockfile
bun run demo
bun run eval
bun run check
bun run test:runtime
bun run verify:packages
```

The demo uses deterministic fake providers and an in-memory SQLite database. The normal test suite and runtime
test cannot charge a model account. `verify:packages` builds one ESM package with TypeScript declarations,
packs it, installs the tarball outside the workspace, and tests it in Node and workerd. The tarball is in `dist/tarballs/`.
These commands do not publish packages or deploy a Worker.

## Repository

| Path                 | Purpose                                                         |
| -------------------- | --------------------------------------------------------------- |
| `packages/`          | TypeScript library, adapters, client and Cloudflare Worker      |
| `evals/`             | TypeScript conversation replay, fixtures and evaluation reports |
| `examples/`          | Runnable library examples                                       |
| `scripts/`           | Package build and distribution verification                     |
| `test/`              | Workspace boundaries and evaluation harness checks              |
| `llp/`               | Living design documents and the complete research record        |
| `deprecated/python/` | Archived Python code, datasets, results and research tools      |

## Evaluate TypeScript

```sh
bun run eval
bun run eval:compare
bun run eval --dataset path/to/conversations.json
```

The runner ingests conversations through `@fluiddb/fluiddb` and `@fluiddb/fluiddb/sqlite`, then grades recall against labelled
source turns. The included fixture covers retrieval among distractors, repeated facts, person isolation, source
deletion and delayed redelivery. It uses deterministic test providers, costs nothing, and runs in CI as part of
`bun run check`. A failed expectation exits nonzero; the report is `.cache/evals/results/latest.json`.

For model-quality evaluations, the same runner accepts real OpenAI adapters with disk caching and an explicit
budget. It defaults to cache-only; live calls require `--live`, token prices and a budget. See
[`evals/README.md`](evals/README.md) for the data format, commands and what each metric means. Synthetic regression
scores do not measure model quality, and the historical Python benchmarks have not all been ported.

The [first real-provider comparison](llp/0024.001.000-component-comparison-results.research.md) passed all 50 checks
with vector order, LLM ranking, LLM closed questions, Jev and a mixed router. That small fixture is too easy to
choose a winner; it is not the historical 392-question answer-quality benchmark.

## One package

```sh
npm install @fluiddb/fluiddb@next
```

The SDK, database adapters, MCP server, CLI, feedback, skills and evaluator ship together. Entry points load the
part a product uses; the root and Firestore REST imports are portable to Workers.

| Import                                           | Provides                                                                  |
| ------------------------------------------------ | ------------------------------------------------------------------------- |
| `@fluiddb/fluiddb`                               | Memory, Person, provider adapters, component ports, picking and rendering |
| `@fluiddb/fluiddb/sqlite`                        | SQLite storage; `/sqlite/node` and `/sqlite/bun` open local connections   |
| `@fluiddb/fluiddb/postgres`                      | PostgreSQL adapter for the host's pool                                    |
| `@fluiddb/fluiddb/firestore`                     | Firestore Node adapter; `/firestore/rest` supports Workers                |
| `@fluiddb/fluiddb/mcp`                           | Person-bound agent tools; `/mcp/http` mounts authenticated HTTP           |
| `@fluiddb/fluiddb/eval`                          | Dataset replay and paired component comparisons                           |
| `@fluiddb/fluiddb/feedback`                      | Explicit HiveNet reports and replies                                      |
| `@fluiddb/fluiddb/skills`                        | Portable memory/feedback skills                                           |
| `@fluiddb/fluiddb/client`                        | Typed client for the optional HTTP service                                |
| `@fluiddb/fluiddb/schema`                        | Runtime schemas and message/record types                                  |
| `/local`, `/testing`, `/conformance`, `/records` | Reference store, fakes and helpers for custom adapters                    |

The internal `packages/` workspaces remain private implementation modules. Their code and declarations are bundled
into one distribution, with no dependencies on separate FluidDB packages. `packages/worker/` is a private service
that a product can deploy. PostgreSQL and Firestore Node use the host's existing database clients; their optional
peer dependencies are only needed for those adapters.

Model, embedder, decider, picker and store remain replaceable interfaces. Extraction, linking, dossier and ranking
models can be overridden independently; see [components and comparisons](docs/components-and-evals.md).

## Product SDK and database choice

Use the SDK for automatic per-turn memory in a product. Choose PostgreSQL, local SQLite (Node or Bun), or
Firestore (Node or REST/Workers) at construction. MCP is an optional interface over the same engine.
See the [product integration guide](docs/product-integration.md) for storage setup, person binding, explicit saves,
corrections, inspection and deletion. The [MCP guide](packages/mcp/README.md) includes stdio and authenticated HTTP.

```ts
import { Memory, Person } from "@fluiddb/fluiddb"
import { FirestoreRest } from "@fluiddb/fluiddb/firestore/rest"

const store = FirestoreRest.create({ projectId, accessToken: getGoogleAccessToken })
const engine = Memory.create({ store, model, embedder, decider })
const memory = Person.bind(engine, authorizedPersonId)
const { prompt } = await memory.recall({ conversation: recentTurns })
```

The backend supplies identity, credentials and durable background jobs. The storage contract contains scoped
reads, atomic changes, revision checks and vector search; it does not depend on SQL, MCP or model providers.

## Embed the library

After installing the single local tarball built by `bun run pack`:

```ts
import { Memory, Render } from "@fluiddb/fluiddb"
import { OpenAI, Shim } from "@fluiddb/fluiddb"
import { Bun } from "@fluiddb/fluiddb/sqlite/bun"
import { SqlStore } from "@fluiddb/fluiddb/sqlite"

const sql = Bun.open("memory.sqlite")
const model = OpenAI.model({ apiKey, model: "gpt-6-luna" })
const memory = Memory.create({
  store: SqlStore.store(sql),
  model,
  embedder: OpenAI.embedder({ apiKey, model: "text-embedding-3-small" }),
  decider: Shim.decider(model),
  processors: ["openai"],
  options: { assistant: "Your assistant", role: "personal assistant" },
})

await memory.ingest({ person: "sam", session: "session-1", turns })
await memory.fold("sam") // Background work in a service.
const recall = await memory.recall({ person: "sam", conversation: currentTurns })
const promptSection = Render.render(recall, { name: "Sam" })
// Give this as memory context alongside the current conversation to your assistant model.
// Close the database when the process finishes: sql.close().
```

A turn is `{ id, role: "person" | "assistant", text, at }`, with an ISO timestamp. IDs must be stable, unique per
person and immutable across retries. Timestamps normalize to UTC. Ingest counts human turns; the raw log retains
both sides. Calling `ingest` again with the same IDs is harmless. One `Memory` instance serializes mutations per
person. Across processes, stale changes fail atomically with `ConflictError`; retry with the same IDs through
your durable queue. The Worker coordinates one person per Durable Object.

`ingest` persists full original messages before calling providers. Derived rows commit only when extraction,
embedding and linking succeed. Failed processing can be retried with the same IDs. `fold` saves each dossier
batch separately, so it resumes after failures. Read calls accept `{ signal }` for cancellation.

For Jev, substitute `Jev.decider({ apiKey: openRouterKey })` and explicitly allow `openrouter` and `typesafe` in
`processors`. The LLM stand-in's confidence is not calibrated. A custom endpoint must name all its processors in
its adapter options. Treat the model's extracted statements and repetition detections as evidence to check, not
as verified facts. Retelling detection had substantial false positives in the lab.

## Cloudflare and the client

See [the Worker guide](packages/worker/README.md) for local setup, configuration and deployment commands.
Rows, original messages and vectors share one per-person SQLite Durable Object; there is no D1 or Vectorize
provisioning. Alarms process a durable inbox after a quiet period and retry failed work with backoff.

```ts
import { Client } from "@fluiddb/fluiddb/client"

const memory = Client.create({ url: "http://localhost:8787", token: fluidToken })
await memory.accept("sam", "session-1", turns) // 202: stored in the inbox, processed later.
const { recall, prompt } = await memory.recall("sam", { conversation: currentTurns, name: "Sam" })
const status = await memory.status("sam")
```

Recall before accepting the current human turn when you want earlier-session memory. The service token grants
access to every person in that deployment: keep it in a trusted backend, authorize end users there, and derive the
person ID from that authorization. Use separate deployments or prefixed IDs for separate tenants. Memory text is
untrusted user content; it must not authorize tools, payments, permissions, or actions in the host assistant.

## Deletion and operational limits

`previewForget(selectors)` returns the complete shared-source deletion scope and a revision without writes or
model calls. After confirming that scope, pass the same selectors plus `revision` to `forget`; concurrent changes
require a new preview. `source({ window, offset, limit })` pages through complete evidence; follow `nextOffset`
until absent (offsets count Unicode code points). The HTTP client and MCP expose the same operations.

`forget({ session })` deletes the session's originals, windows, statements and vectors, repairs surviving group
links/counts, and discards the dossier for rebuilding. Selecting statement IDs also deletes their complete source
windows and all statements derived from those windows: otherwise the source or a later dossier could repeat the
forgotten information. This can remove neighboring details in the same window. Copies independently supplied in
other windows/sessions remain; this API is source deletion, not a natural-language search for every paraphrase.

Processed turn IDs remain as content-free tombstones so delayed deliveries cannot restore forgotten text. A new
statement of the same fact needs a new event ID. `client.erase(person)` removes all of that person's live data,
including the processing tombstones and waiting inbox. The SDK store retains a fresh content-free revision
marker to invalidate earlier in-flight work. Direct SDK users call `engine.erase(person)` or `store.erase(person)`. It does not erase a product's external transcripts, provider logs or
Cloudflare recovery snapshots; those need the product's own retention controls.

Vector search is exact and linear in each person's stored vectors. It is intended for personal-scale memories;
the Worker is not certified for unbounded histories or production traffic. Keep the embedding model/dimensions
stable for an existing store; changing it requires re-embedding. Monitor inbox failures, pending dossier windows,
usage and latency through `status` and structured logs. The service does not enforce a dollar budget; set provider
limits before enabling live calls. No original conversation text is logged by the Worker.

The schema migration preserves databases made by the earlier experimental v2 checkout, but cannot reconstruct
original text that checkout had already discarded. Import from the application's source transcripts into a fresh
store if full historical originals are required.

## Development

`bun run check` runs strict types (including Cloudflare types), unit/integration/conformance tests, the synthetic
conversation eval and formatting.
`bun run test:runtime` exercises the actual bundled Worker under workerd with fake outbound provider responses,
including a restart and background alarms. `bun run test:adapters` runs Node SQLite and, with explicit test URLs,
PostgreSQL and both Firestore adapters against a local database/emulator. `bun run verify:packages` verifies the distributable artifacts.
Run `./ref-check` when changing LLP references. Agent guidance starts in [AGENTS.md](AGENTS.md).

Real-provider checks are opt-in: set `FLUID_LIVE=1` with provider keys, then run `bun test` in `packages/providers`.
They use synthetic text and incur model charges; they are excluded from the normal test command. The live HTTP
smoke script is `packages/worker/scripts/smoke.ts`. Never use private research conversations as fixtures or include
them in a package.

## Feedback and memory skills

Products can add a deliberate feedback action using `@fluiddb/fluiddb/feedback`. Agents use `fluiddb feedback` or the
MCP `fluiddb_feedback` tool when enabled with `--feedback`. Reports support reproducible failed tasks and thread
replies through HiveNet; no memory or environment context is collected automatically.

`fluiddb skills read fluiddb-memory` teaches the memory workflow. MCP publishes this and `fluiddb-feedback`
through the Skills extension, ordinary resources and `fluiddb_read_skill`. The Worker serves agent markdown,
skills and `/.well-known/agent-feedback.json`. See [feedback integration](docs/agent-feedback.md).

## npm preview

The consolidated package is `@fluiddb/fluiddb`, with version 1 previews under the `next` tag and Apache-2.0.
Install it with `npm install @fluiddb/fluiddb@next`; `latest` remains `1.0.0-next.1`. Plain `fluiddb` is
blocked by npm's name-similarity rule against `fluid.db`. The earlier split preview packages at `1.0.0-next.0`
were published before consolidation; see [release status](docs/releasing.md) for publication and migration details.

Licensed under [Apache-2.0](LICENSE).
