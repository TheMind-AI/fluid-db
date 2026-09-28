# LLP 0023: FluidDB: the conversation memory package

**Type:** Spec
**Status:** Draft
**Systems:** Core, Writes, Reads, Jev, Interfaces
**Author:** Adam Zvada / Claude
**Date:** 2026-09-27
**Revised:** 2026-09-29 (merge-review chronology and embedding bounds)
**Related:** LLP 0015, LLP 0019.000, LLP 0020.000, LLP 0021.000, LLP 0022.000, LLP 0018

## Summary

The package follows the memory that rounds 10-15 measured best on real conversations, as a TypeScript package that runs on
Cloudflare Workers. TypeScript at the repository root (`packages/`) is the primary implementation. The Python lab
(`deprecated/python/lab/`) is an archived research bench.
- **Write:** each conversation becomes windows of the person's messages, and statements (one lasting fact each,
  every wording kept) embedded for search. A statement that repeats or changes an earlier one is linked to it by
  a model's decision, with a count and dates. A dossier is kept current after each session.
- **Read, at every turn:**
  - meaning search over statements and windows
  - Jev (or an LLM in its place) picks what to show
  - Jev decides whether the person is repeating something already in memory
  - the result renders into the assistant's prompt, with the instruction to use it
- **Every provider is a port:** the language model, the embedder, the decider (Jev), the picker, and storage. SQLite, PostgreSQL, Firestore (Node and REST) and
  in-memory adapters implement the storage contract. Rows and vectors commit in the same transaction.

[observed] `packages/` packages the conversation path. The lab's dynamic relational schema, compiled SQL reader,
generated UI and other computed stores remain Python research components. This release is not a port of those
components, and the research accuracy numbers have not been remeasured on the TypeScript implementation.

[confirmed] (Adam Zvada, 2026-09-27), from dictation: "prepare it for production-ready version 2 … build it as a
package … in TypeScript … very clean code … It should run in Cloudflare and great separation of code".

[confirmed] (Adam Zvada, 2026-09-28): the public release starts at version 1, superseding the earlier version-2
wording. One public package should include the SDK, adapters, MCP and supporting tools.

## Repository layout

[confirmed] (Adam Zvada, 2026-09-27): TypeScript is primary; older implementations belong in a deprecated folder.
[observed] Root `package.json` is the Bun workspace; `packages/`, `evals/`, `examples/`, `scripts/` and `test/` are
active. `deprecated/python/` preserves the complete Python project, including the lab and historical evaluations.
`llp/` remains at the root. CI and formatting exclude the Python archive; `ref-check` still validates its references.
The archive's working directory preserves `lab.*` and `fluiddb.*` imports, while keys remain in root `.env`.

`bun run eval` uses the current TypeScript memory and SQLite store. LLP 0024 defines its protocol, explicit
provenance grading, free regression mode, and cached/budgeted OpenAI mode. This makes new evaluations possible;
it does not transfer the historical Python scores to this implementation.

## What each part is, and why

| part | does | evidence |
|---|---|---|
| windows | up to 6 of the person's consecutive messages, each with the assistant's question before it | answers mean nothing without the question (LLP 0017#data) |
| statements | one call per window writes the lasting things it says about the person | the unit of conversation: 66% recall at 1.1k tokens (LLP 0019.000) |
| linking | for each new statement, its closest earlier ones (cosine ≥ 0.55) go to the model: same fact, a change, or new | cosine alone merged under 1%; the model finds 46% repeats (LLP 0020.000) |
| every wording | a link never replaces a statement | one wording lost 3.3 points (LLP 0020.000, LLP 0021.000) |
| dossier | updated after each session from what was said since | as good as one full read (LLP 0020.000) |
| meaning search | embeddings, not words | +10 points; +24 on facts said in another language (LLP 0019.000) |
| picker | a closed question per candidate, on whole windows | +7 over meaning alone; whole windows +4.8 over cuts (LLP 0020.000, LLP 0021.000) |
| detector | one closed choice over the closest statements, or none | doubles the exact acknowledgement (LLP 0022.000) |
| directive | the prompt tells the assistant to use what it remembers | 9.5% → 45.8% referring back (LLP 0021.000) |
| no router | every turn reads the same parts | routing by guessing lost 3.2 points (LLP 0020.000) |

## Ports

Interfaces live in the private `packages/core` module and are exported from the public package root. Storage and
transport adapters use subpath imports from that same package.
- **`LanguageModel`:** text and structured JSON (a schema); adapters report usage through an optional callback.
- **`Embedder`:** texts to unit vectors.
- **`Decider`:** Jev's two closed question types.
  - yes/no with a probability
  - a choice with probabilities
  - Implemented by Jev, or by a language model in Jev's shape (LLP 0018#processors).
- **`Picker`:** selects bounded, unique IDs from retrieved candidates. Built-ins support model ranking, closed
  questions, vector order and routing by statement/window kind. Both stores are still searched (LLP 0024.001).
- **`Store`:** original messages, processed-turn IDs, windows, statements, groups and the dossier, per person.
  A write applies all changes or none. Vector search is part of the store so deletions and new evidence are atomic.
  Conditional writes validate a persisted person revision; stale changes fail atomically. Erasure and bounded
  inspection are part of the contract. Alternate stores must implement the shared conformance suite.
- **`Clock`, `Logger`:** injected, so tests are deterministic.

[observed] `Memory.Deps.models` overrides extraction, linking, dossier and model ranking independently.
`detector` overrides repetition detection without changing the picker. Every override is processor-checked.

## Processors

A deployment lists the providers a person's data may go to. Any other provider throws before a request is made. The
same rule protected other people's data in rounds 11-15 (LLP 0018#processors). Provider HTTP redirects are rejected
without forwarding or retries: a configured endpoint cannot redirect a private request body to another recipient.

## Raw log

[observed] `packages/core/src/memory.ts`, `packages/sql/src/store.ts`:
- Complete input text, from both sides of a conversation, is appended before the first model call. IDs are
  immutable; duplicate deliveries keep the original. Timestamps normalize to UTC.
- Window excerpts and derived statements can be lossy; the raw log is not. The SQLite migration adds `fluid_log`
  separately from `fluid_turns`, which records processing IDs and content-free deletion tombstones.
- A provider failure leaves the raw log intact and no partially derived rows. A retry resumes from those originals.
- Windows record their source human and assistant turn IDs. Ingestion reads the session log so an assistant
  question delivered earlier is still available as context for the next human message.
- Each `Memory` instance serializes mutations per person. Persisted revision checks reject stale writes across
  instances; the host retries conflicts with the same source IDs. The Worker additionally supplies one Durable
  Object per person. Reads do not wait for extraction, and check the persisted revision before returning.

## Temporal links

[observed] `packages/core/src/link.ts`, `memory.ts`, `packages/core/test/temporal.test.ts`:
- Source dates govern changes even when a queued session arrives after a newer saved or extracted fact.
  An older conflicting observation gets a historical group; the newer group lists it in `replaces` and stays
  current. Its historical end is the newer group's first observation if that is later than the incoming one,
  otherwise its last confirmed observation. This preserves evidence without ending a fact before it was said.
- A group that ended at or before the incoming observation is excluded from linking candidates. A return to an
  old value can then replace a current candidate, while the original historical period stays ended. The linker
  still decides meaning; the guard does not guarantee it identifies every semantic contradiction.
- Parallel link decisions are re-evaluated when an earlier change in the same batch ends one of their candidates.
  A repeat within a historical period may still join it. A change inserted between two periods carries the later
  end date and moves the successor's replacement edge, so forgetting remains consistent.
- Forgetting a repetition recomputes dates first, reverses replacement edges whose only newer evidence was
  removed, and recomputes end dates from surviving successor observations. A removed repetition cannot continue
  to make an otherwise older fact current. Deleting whole groups retains the existing direct-edge behavior.
- Pinned groups retain their existing protection from automatic supersession. Statements and source text are
  never discarded by chronology reconciliation. Explicit corrections still reject a date older than their target.

## Embedding inputs

[observed] `packages/providers/src/openai.ts`, `packages/providers/test/openai.test.ts`:
OpenAI embedding inputs are split at complete Unicode code points into at most 8,000 UTF-8 bytes. Byte-BPE token
counts cannot exceed the UTF-8 byte count, so this stays below the model's 8,192-token input limit without adding
a tokenizer dictionary to the Worker. At most 37 chunks are sent in one request, below the 300,000-token total
limit. These limits are documented in the [OpenAI embedding API](https://developers.openai.com/api/reference/resources/embeddings/methods/create)
(checked 2026-09-29).

Every chunk is embedded. For multi-chunk text, the provider takes the byte-length-weighted mean of the chunk unit
vectors and normalizes it, returning one vector per original input. Short inputs retain their existing vector.
This replaces the historical 24,000-character truncation, which could still exceed provider token limits on a
legal non-English window. Raw originals and extraction/source windows remain unchanged. Long-input vector
quality has not been remeasured; existing stored vectors remain dimensionally compatible and are not rewritten.

## Forgetting

[observed] `packages/core/src/memory.ts`, `packages/worker/src/host.ts`:
- `forget` removes a session, or source windows selected by window/statement ID. Selecting a statement also
  deletes its source window, the original turns used by that window, and all statements derived from it. Otherwise
  recall or the next dossier rebuild would resurrect the removed content. This is deliberately a source-window
  operation: neighboring details in the same window are removed too.
- All corresponding vectors are removed in the transaction. Groups that survive have their counts and dates
  recomputed; links to removed groups are cleared. Other windows/sessions, even independent repetitions of the
  same fact, remain. This is not semantic erasure of every paraphrase in the account.
- The dossier is dropped immediately; `fold` rebuilds it from remaining windows. The Worker schedules that work.
- A session's waiting inbox turns are also deleted. Their IDs, and IDs already processed, remain as content-free
  tombstones so delayed deliveries cannot restore deleted words. New events must use new IDs.
- An in-flight recall is invalidated if forgetting or erasure completes while its provider calls are pending.
- Erasure removes inbox, raw log, derived memory, vectors and processing tombstones. Every store atomically
  retains a fresh content-free revision marker, even for an absent person; prior in-flight work cannot match the
  erased generation. This fixes the first-save/null-revision race (LLP 0023.003). The host must also revoke future
  account jobs and writes. External
  product transcripts, provider retention and Cloudflare recovery snapshots are outside this operation.

## Cloudflare

- **A SQLite Durable Object per person** holds rows and vector blobs and serializes writes, because linking
  depends on order. This replaced the initial D1/Vectorize proposal: both would add distributed consistency work
  for no measured benefit at personal scale. [observed] `packages/worker/src/person.ts`.
- **Exact vector search** scans that person's vectors, cached after the first read. A failed transaction does not
  update the cache. Dimensions must match; changing embedding models requires a rebuild. This is linear search,
  not a claim of million-row scalability.
- **A durable inbox and alarm** let HTTP ingestion return before model processing. Failed sessions back off and
  are set aside after five attempts, with an explicit retry endpoint. Dossier failures retry separately. Unexpected
  alarm errors schedule another attempt; failure to schedule propagates for Cloudflare to retry.
- **The Worker** exposes the HTTP API.
- **No Node APIs** in the core: fetch, Web Crypto and plain data.
- **A deployment token** authenticates a trusted backend, not an end user. The product must authorize each person
  ID before calling the service. Logs contain counts, timings and error classes; raw provider errors are not sent
  through the public API because they may echo input.

## Packaging

[confirmed] (Adam Zvada, 2026-09-28): ship one public FluidDB package rather than requiring separate schema, core,
SDK and adapter packages. This supersedes the earlier inferred public-package split in LLP 0023.001.

[confirmed] (Adam Zvada, 2026-09-28): use `@fluiddb/fluiddb` for the single public package.

[observed] `scripts/packages.ts`, `scripts/build.ts`, `scripts/pack.ts`, `scripts/verify-packages.ts`:
- One artifact contains the SDK, schema, providers, adapters, evaluator, feedback, skills, MCP and both executables.
  Its root exports the engine and provider adapters. `/sqlite`, `/postgres`, `/firestore`, `/firestore/rest`,
  `/eval`, `/mcp`, `/feedback` and the other explicit subpaths provide the remaining surfaces. These are imports,
  not separately installed packages. The Cloudflare Worker remains a private deployable.
- Private source workspaces retain dependency boundaries and TypeScript exports. A single ESM build shares
  internal modules across entry points, including error-class identity. Declarations are emitted in dependency
  order and their private workspace references rewritten to relative paths within the artifact. Published
  JavaScript and declarations must not import any retired `@fluiddb/*` package.
- `dist/fluiddb/` is the only publishable directory; `dist/tarballs/` contains exactly one tarball. It includes
  JavaScript, declarations, README, Apache-2.0 LICENSE, NOTICE and portable skills. The published consolidated
  preview under `next` is `@fluiddb/fluiddb@1.0.0-next.4`. The thirteen already published `1.0.0-next.0` packages are recorded
  in LLP 0023.003.
- `zod` and the official MCP server SDK are external dependencies. PostgreSQL and Node Firestore clients are
  optional peers supplied by the host. Root and Firestore REST imports work without those drivers, Node APIs or
  Bun APIs; Node/Bun-specific SQLite entry points remain isolated. Bundling the root into workerd verifies this.
- Bun is the workspace tool. Private modules remain informed by
  [OpenCode's workspace](https://github.com/anomalyco/opencode): explicit dependency boundaries, shared versions
  and typed namespaces. Internal modularity does not require independent registry releases. No OpenCode runtime
  code was copied.
- Publication requires npm name access and authentication. The read-only release check validates the artifact
  and prints one command without publishing. A registry 404 is not proof a name is publishable: npm rejected
  plain `fluiddb` as too similar to the existing `fluid.db` package.

[confirmed] (Adam Zvada, 2026-09-29): publish the combined npm package automatically from GitHub release tags.
[observed] `.github/workflows/publish.yml` calls the shared verification workflow for `v1.0.0-next.*` tags.
The tag must equal the artifact version. Verification uploads one tested tarball only after package, runtime and
reference checks; the adapter job must also pass before publishing. A separate job uses npm's package-specific
GitHub OIDC trust for `TheMind-AI/fluid-db`, workflow `publish.yml`, with direct publishing allowed. No npm token
is stored in GitHub. A final job checks the exact published version with an empty npm cache. New versions still
require deliberate source-version updates and a tag; ordinary branch pushes never publish. npm trust setup and
release commands are in `docs/releasing.md`.

Feedback remains an explicit external support channel independent of memory. Portable skills share one catalog
across CLI, MCP and public Worker routes (LLP 0023.002).

## Tests

- **The core is tested with in-memory adapters and deterministic fakes:** a hashing embedder, and scripted models
  and deciders.
- **The Worker's routes are tested in-process**, including failure recovery, deletion and sanitized errors.
- **The bundled Worker is tested under workerd**, with real RPC, SQLite, a restart and alarms. Outbound provider
  calls are intercepted with synthetic responses; no provider credentials or paid calls are involved.
- **The single packed artifact is installed outside the workspace** and consumed by plain Node, TypeScript
  NodeNext and a real workerd bundle. Checks cover shared error identity, optional database clients, both CLIs,
  MCP and the absence of dependencies/imports on private FluidDB workspaces.
- **Live tests against real providers** require `FLUID_LIVE=1` and keys. Ordinary tests force them off.

The Firestore-emulator workerd test bridges outbound requests through Node fetch. It omits the incoming
`Content-Length` so fetch computes it from the buffered body. Forwarding that header failed with
`UND_ERR_INVALID_ARG` under Node 22.23.2 and Miniflare's Undici dispatcher, although Node 26 passed. The corrected
test passes save/evidence/isolation/recall/forget/erase under both Node versions; the SDK adapter is unchanged.

## Release boundary

[inferred] This is ready for integration and staging evaluation once the repository checks pass. It still needs
product-specific quality evaluation, load/capacity measurement, provider budgets, and the host application's
authorization and retention controls before a production rollout. No deployment or registry publication is implied
by building the packages. Re-telling detection has measured false positives (LLP 0022.000); confidence is not proof.

[observed] LLP 0023.000 records the BetterMind backend integration audit. The stack fits, but explicit saved-memory
semantics, complete long-input extraction, product metadata, forgetting behavior and bounded voice recall require
additional work before replacing that application's memory. The audit also identifies reusable viewer components.

## Product SDK and adapters

[observed] LLP 0023.001.000 records explicit saves/corrections, complete input coverage, inspection/evidence APIs,
persisted revision checks, native database adapters and the MCP surface. The product guide is
`docs/product-integration.md`; the therapist backend should use the SDK and Firestore REST adapter in its Worker
runtime. MCP is an optional interface to the same engine. Research retrieval parity is strong on cached inputs;
full TypeScript creation-to-answer model-quality parity remains unestablished.

## Component evaluation

[observed] LLP 0024.001.000 records a five-variant real-provider comparison through TypeScript: every variant
passed 50/50 explicit checks on the same 15-probe fictional fixture, with zero-network cache replay. This is an
integration baseline with a ceiling effect, not a full answer-quality benchmark. The package's `/eval` entry point allows products
to replay their own labelled datasets against independent SDK configurations and fresh database adapters.
