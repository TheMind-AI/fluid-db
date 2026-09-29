# Embed FluidDB in a product

Use the SDK in the therapist backend. It controls ingestion, recall before every reply, dossier updates and account
lifecycle. MCP exposes the same engine to an agent that decides when to call tools. It is optional; it does not
replace the backend's automatic per-turn memory workflow.

## Choose storage

All adapters implement `Store` from `@fluiddb/fluiddb`. Database objects, credentials and connection lifetime belong
to your application. Start with a separate FluidDB table/collection; these are not adapters over BetterMind's old
memory schema, and creating one does not migrate historical records.

```ts
// PostgreSQL, in Node. Reuse the application's pg pool.
import { Postgres } from "@fluiddb/fluiddb/postgres"
const store = await Postgres.create(pool) // creates fluiddb_records and its index
```

```ts
// Firestore, in Node. Reuse the server-side @google-cloud/firestore / Admin SDK client.
import { Firestore } from "@fluiddb/fluiddb/firestore"
const store = Firestore.create(db, { collection: "fluiddb_people" })
```

```ts
// Local SQLite, in Node 22.13+ (or use @fluiddb/fluiddb/sqlite/bun under Bun).
import { Node } from "@fluiddb/fluiddb/sqlite/node"
import { SqlStore } from "@fluiddb/fluiddb/sqlite"
const sql = Node.open("memory.sqlite")
const store = SqlStore.store(sql)
// Call sql.close() at application shutdown.
```

Do not send these database clients or service credentials to a browser. The adapters scope every operation by
person. The backend still authorizes which person may be accessed. Include the tenant in your stable person ID
if different tenants can have the same user ID. MongoDB can implement the same `Store`; it is not implemented yet.

For the therapist backend's Cloudflare Worker runtime, use the REST entry point instead of the Node client:

```ts
import { FirestoreRest } from "@fluiddb/fluiddb/firestore/rest"
const store = FirestoreRest.create({
  projectId,
  accessToken: getGoogleAccessToken, // host's existing OAuth/service-account token provider
})
```

This uses `fetch` and Firestore's atomic commit/precondition API; it has no Node or gRPC runtime dependency. The
host keeps token acquisition/caching and refresh. Both Firestore entry points use the same on-disk layout.

## Compose once, bind after authentication

```ts
import { Memory, Person } from "@fluiddb/fluiddb"
import { OpenAI, Shim } from "@fluiddb/fluiddb"

const model = OpenAI.model({ apiKey, model: modelName })
const engine = Memory.create({
  store,
  model,
  embedder: OpenAI.embedder({ apiKey, model: "text-embedding-3-small" }),
  decider: Shim.decider(model),
  processors: ["openai"],
  options: { assistant: "Mind", role: "therapy assistant", pick: "model" },
})

// In your authenticated request handler; never take this ID from model tool arguments.
const memory = Person.bind(engine, authorizedPersonId)
const { recall, prompt } = await memory.recall({ conversation: recentTurns }, { signal })
// Supply prompt as untrusted memory context alongside the actual conversation before generating the reply.
// Preserve the directive to use supported memories explicitly and acknowledge relevant previous disclosures.

// After a completed turn/session, through your durable job mechanism:
await memory.ingest(sessionId, actualTurns, { signal })
await engine.fold(authorizedPersonId)
```

Retrieve before storing the current human turn when the purpose is recalling earlier conversations. Use stable,
immutable turn/session IDs and actual timestamps. A retry of already processed IDs does not duplicate memories.
`fold` belongs in a durable background job, not a fire-and-forget promise in a short-lived HTTP request. The
Cloudflare service already supplies its own durable inbox, alarms and retries; direct SDK users supply those.

A hosted deployment uses the same person-bound surface:

```ts
import { Client } from "@fluiddb/fluiddb/client"
const memory = Client.create({ url: serviceUrl, token: backendToken, timeoutMs: 120_000 }).person(authorizedPersonId)
await memory.accept!(sessionId, actualTurns) // Accepted into the durable inbox; extraction is still pending.
const { prompt } = await memory.recall({ conversation: recentTurns }, { signal })
```

The direct SDK and HTTP client support `AbortSignal`. The client has a 120-second default deadline; use a smaller
host deadline for latency-sensitive voice recall. Cancellation can race with a commit; retry with the same IDs.
The HTTP timeout stops the caller's request, but does not promise rollback of a job already accepted by the service.

## Explicit memories, corrections and inspection

```ts
const saved = await memory.remember({
  id: saveRequestId,
  session: sessionId,
  text: "They prefer short replies.",
  kind: "preference",
  at,
})
// saved.saved is returned only after the exact fact and its vector commit.
const correction = await memory.remember({
  id: nextRequestId,
  session: sessionId,
  text: "They now prefer detailed replies.",
  kind: "preference",
  at: later,
  replaces: saved.statement.id,
})
const page = await memory.inspect({ kind: "statements", limit: 20 })
const sources = await memory.evidence({ statements: [saved.statement.id] })
```

Explicit saves are pinned and have explicit source provenance; they never fabricate a chat turn. Automatic
extraction cannot mark their group superseded. Corrections mark the previous group historical and invalidate the
old dossier. Call `fold` afterwards. This protects stored state; a model-generated dossier is still a summary to
check, not an authority over an explicit fact. Reuse a save ID only for the identical request. Reusing a forgotten
save ID is rejected. A new authorized save needs a new ID.

For imports, `await memory.rememberMany([firstFact, secondFact])` accepts 1–20 independent saves with distinct
stable IDs. Each item uses the same fields as `remember`, except `replaces`; preserve original sessions, dates,
pinning and `origin: "import"`. New facts share embedding work and commit atomically. Mixed saved/new retries are
supported; changed or forgotten IDs reject the batch. Provider adapters may split inputs into bounded HTTP
requests. Advance the host's import cursor only after the save returns, and reuse the same IDs on retry.

These inspection/evidence methods are the server-side data surface for a memory viewer: page through records,
show source windows, distinguish inferred statements from explicit saves, and show correction dates. BetterMind's
viewer can consume this through an application mapper; its current internal records are not structurally identical.

`forget` removes selected source windows and all their derivations, including neighboring statements in a shared
window. It invalidates the dossier and retains content-free processing tombstones. It is not semantic deletion
of every independent paraphrase. `engine.erase(person)` / `store.erase(person)` removes all source/derived content, vectors and processing tombstones,
retaining only a fresh content-free revision marker to invalidate earlier in-flight work. With the hosted service, use `client.erase(person)` so pending inbox jobs are deleted too.
Stop/revoke that person's jobs before erasure; the product owns external transcripts and later deliveries.

## Adapter contract and concurrency

A `Store` provides scoped originals, processed IDs, windows, statements, groups, dossier, exact vector search,
atomic `write`, `revision`, and full `erase`. Models, HTTP, authorization and MCP do not belong in this port.
`@fluiddb/fluiddb/conformance` exercises the same behavior against every adapter.

Every commit and every erase changes a revision token, even when erasing a person without existing records. A conditional write with a stale token throws `ConflictError` and changes
nothing. Core extraction, explicit saves, forgetting and dossier updates use this guard. An in-flight recall
returns no evidence if its revision changed while providers were running, including deletion by another instance.
A single `Memory` serializes local writes; across processes, retry conflicts with the same IDs through the host's
queue. Do not retry arbitrary provider errors in an unbounded loop. The adapter transaction contains no model calls.
SQLite refreshes its vector cache when another instance's revision changes.

Vectors share the source database and transaction, so a deletion cannot leave a separately indexed copy behind.
Search is exact and linear per person. PostgreSQL uses an isolated JSONB table with native transactions; Firestore
uses per-person documents and native transactions. Firestore scans one person's requested record kind for listing
and exact search, so a small response limit does not cap database reads. This is a correctness baseline for personal
histories, not an ANN or large-corpus performance claim. Native Firestore request/document limits apply; oversized
writes/erasure fail atomically rather than committing partial batches. Keep the embedding model/dimensions stable.

Firestore conformance runs under Node against the official emulator. Bun remains the workspace tool; its gRPC
query behavior did not pass this integration run, so use Node for the Firestore adapter.

## BetterMind integration target

The inspected checkout is `therapist-backend/backend-simplify`. Integrate behind its existing memory service and
per-turn composition boundary, using its authorized account identity and Firestore REST authentication provider. Its current `MemoryFirestore`
interface is not the Node Admin SDK and must not be passed to `Firestore.create`; use `FirestoreRest.create`. Keep its durable
transcripts and processing jobs, distinguish requested saves from proactive unpinned notes, and choose a truthful
forget mapping before replacing tools. Adapt the viewer to the inspection responses. Start shadow evaluation with
fictional data; before real-account writes, join the host's atomic erasure guard and register FluidDB's separate
storage tree in account deletion. See [the integration gates](releasing.md#first-therapist-backend-integration). No production data migration,
backend replacement, provider rollout or deployment has been performed by this repository change.

## Product feedback and agent instructions

Use `@fluiddb/fluiddb/feedback` for a deliberate product report action. The report is addressed to FluidDB's HiveNet
project and has no connection to a person's Store. Structured failed-task reports carry task/expected/actual,
optional attempts and memory-case descriptors. Present the fields and destination before submission; use
synthetic examples and never attach therapist conversations automatically. Returned thread/guidance/ask data
belongs to the support UI, not conversation memory. See [the integration guide](agent-feedback.md).

Agents can load `fluiddb-memory` and `fluiddb-feedback` from `@fluiddb/fluiddb/skills`, the CLI, or MCP's Skills extension.
These instructions explain the same SDK workflow; they do not change the host's authorization or capabilities.
