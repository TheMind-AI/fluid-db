# npm release and backend integration

The newest published preview is **one public package, @fluiddb/fluiddb at 1.0.0-next.4**. SDK, adapters, MCP, evaluation,
feedback and skills are entry points of that package. The internal workspaces stay private. The maintainer
selected this name on 2026-09-28. Local tests do not publish or reserve names.

Install the newest complete preview with `npm install @fluiddb/fluiddb@next`, or pin `1.0.0-next.4` as the backend
does. `next` resolves to `1.0.0-next.4`; `latest` remains `1.0.0-next.1`. Publication succeeded after the maintainer's
fresh passkey check. Registry tarball integrity matches the verified artifact, and a fresh install
with an empty npm cache passed the external Node/NodeNext, MCP/CLI and workerd consumer checks. The backend also
passes its API checks with the exact registry dependency. Receipts and consumer checks are in
`.context/releases/1.0.0-next.4/`; backend evidence is in its `.context/next4-registry-api.log`. Initial registry metadata delays cleared
after processing.

## Names and existing previews

On 2026-09-28, thirteen split packages were published at `1.0.0-next.0`: `@fluiddb/schema`, `@fluiddb/core`,
`@fluiddb/eval`, `@fluiddb/providers`, `@fluiddb/sql`, `@fluiddb/client`, `@fluiddb/postgres`, `@fluiddb/firestore`,
`@fluiddb/feedback`, `@fluiddb/skills`, `@fluiddb/mcp`, `@fluiddb/cli` and `@fluiddb/sdk`. Their registry integrity
values matched the tested tarballs. The maintainer then requested one public distribution. The old previews
are retired in favor of the combined package; registry cleanup is in progress. No version of the combined
package depends on them. LLP 0023.003 records the original release history.

`npm whoami` confirmed `zvada`, and `npm org ls fluiddb --json` confirmed ownership of the free `@fluiddb`
organization. The unscoped `fluiddb` publish was rejected because it is too similar to the existing `fluid.db`.
Under [npm's punctuation-collision rules](https://blog.npmjs.org/post/168978377570/new-package-moniker-rules.html),
`fluid-db` and `fluid_db` have the same conflict. New names cannot contain uppercase letters. Registry 404s for
other names mean no public exact-name package was found; they do not guarantee publication will be accepted.

The maintainer selected **Apache-2.0** and **version 1**. Root `LICENSE` contains the full license; `NOTICE`
records attribution. Both are included in the single artifact. The preview version is independent of older
research implementations' internal v2 names.

## Verify the release

```sh
bun install --frozen-lockfile
bun run check
bun run test:runtime
bun run verify:packages
./ref-check
bun run release:check
```

Only `dist/fluiddb/` contains a publishable manifest. The build bundles private modules into ESM JavaScript,
rewrites declarations to local references and includes the README, license and portable skills. PostgreSQL and
Node Firestore clients are optional peers supplied by the host; the root and Firestore REST imports do not need
them. No source database, cache, secret or Python archive is packed.

`verify:packages` installs exactly one tarball into an external directory. It executes Node consumers, both CLIs
and an MCP client, checks shared error identity and NodeNext declarations, and bundles the SDK into real workerd
without Node compatibility. It verifies portable imports before installing the optional database clients and
rejects imports or dependencies on the retired public FluidDB modules.

`release:check` validates the artifact and prints one publication command. After the name and release are
settled, use that command to publish the verified files. npm may require a fresh browser passkey check even when
the CLI is logged in. Do not publish the root, a source workspace or an empty name-reservation placeholder.
Re-run verification after any source or metadata change.

After publication, run `bun run verify:registry` to repeat the external consumer checks using the exact published
version. Verify the `next` dist-tag and tarball integrity too. The first split release was invoked with `--tag
next`, but the registry also exposed `latest`; inspect actual tags instead of assuming that flag is the entire
registry state. Keep integration documentation explicit about preview versions. Broader answer-quality checks
and the backend gates below remain separate from package-install correctness.

## Publishing from GitHub tags

`.github/workflows/publish.yml` publishes only the combined package when a `v1.0.0-next.*` tag is pushed.
The shared verification workflow runs the repository, runtime, package-consumer and database-adapter checks.
It rejects a tag that differs from the package version and uploads the verified tarball. The publishing job
downloads that exact artifact and uses npm OIDC authentication. A final job waits for registry processing and
checks published consumers with an empty npm cache. No `NPM_TOKEN` secret is needed.

Configure the existing npm package once, using a maintainer login with package write access:

```sh
npm trust github @fluiddb/fluiddb --repo TheMind-AI/fluid-db --file publish.yml --allow-publish --yes
npm trust list @fluiddb/fluiddb
```

npm requires an interactive passkey check for this trust configuration. The publisher was created on
2026-09-29 with publish permission. Its first tagged release still needs end-to-end verification. The authorization is bound to the
repository and workflow filename above. Do not treat a merged workflow as proof that npm trust is configured;
verify the trust listing. See the official [npm trust command](https://docs.npmjs.com/cli/v11/commands/npm-trust/)
and [trusted publishing guide](https://docs.npmjs.com/trusted-publishers/).

For a release, update the private package versions and runtime version strings, rebuild and verify, merge the
change, then tag the matching commit. For example, after preparing version `1.0.0-next.5`:

```sh
git tag v1.0.0-next.5 <verified-commit>
git push origin v1.0.0-next.5
```

The workflow keeps prereleases on `next`. Stable publication remains a deliberate future change to the release
guard and dist-tag policy. Normal branch pushes run CI without publishing. Never republish an existing version.

## Therapist-backend integration

The backend workspace now consumes the SDK directly for text and voice memory; MCP remains an optional agent
surface. The integration uses the public `Store`/`Records` contract over BetterMind's existing Firestore client,
so every write joins `_erasure_jobs/{uid}` atomically. All data, metadata, pending jobs and abandoned imports stay
under the existing user subtree. It does not instantiate the stand-alone Firestore REST adapter's independent tree.

The integration preview `1.0.0-next.2` adds `remember({ pinned, origin })`: the existing default remains explicit
and pinned; hosts can preserve unpinned imported/proactive notes with truthful source provenance. Exact save
retries include those options. OpenAI chat-completion requests explicitly opt out of response storage. Synthetic
regressions cover pin preservation, automatic supersession of unpinned imports, and forget/retry behavior.

BetterMind now runs FluidDB exclusively. Its bounded data importer must complete before live memory mutations;
while copying, context reports unavailability and tools return a retryable receipt. No legacy runtime fallback is
used. Current facts and eligible summaries are imported without fabricating conversation messages. Its tools retain
product metadata, preview collateral source deletion, and advance both FluidDB and legacy-import forget boundaries.
Pending chats survive incomplete migration and are resumed by bounded background work. The old package and records
are retained for import, shared schemas/policy helpers and explicitly requested historical comparisons.
The simulator, XO CLI and ordinary evaluations default to FluidDB.

The backend's `docs/memory-and-skills.md` documents selection, provider permissions, migration, the operator command,
cron and viewer. Validation uses fictional data and a local Firestore emulator. Production deployment is a
separate product operation; passing behavioral checks is not evidence of superior therapeutic recall.

## Sole-runtime release: 1.0.0-next.3

The published preview adds revision-bound `previewForget` and complete paginated `source` reads across SDK, HTTP and MCP.
Save/import provenance no longer invents conversation dates or repetition counts. Turns accept 200,000 characters
and retain complete splitting/forgetting provenance. Backend regressions cover sole-engine selection, pending/raw
forgetting, omitted source details, person filters, pin promotion, bounded IDs, transcript links, feedback redaction,
text automatic retrieval and the simulator's clock. LLP 0023.004 records the cutover. The backend's
`.context/fluid-cutover-registry-api-check.log` records 522 passing tests (12 skipped), types, lint and a
232.86 KiB gzip Worker using the exact registry version. Its full root and viewer checks also pass; the local
Firestore emulator passes 10 integration checks. No deployment or production data migration was performed.

The later product feedback changes remove automatic scrubbing and the memory-read dependency. Two explicit
tools route Mind product feedback to `mind` and memory-engine feedback to `fluiddb` through the existing
durable Workflow, preserving each report's destination on retry (LLP 0023.005#technical-feedback-routing).
This is a product change and does not require a new SDK release.

## Merge-review release: 1.0.0-next.4

This published preview fixes three deterministic merge-review findings: older queued extraction replacing newer
facts, a returning fact joining a historical ended group, and legal multilingual windows exceeding the embedding
provider's token limit. LLP 0023#temporal-links and LLP 0023#embedding-inputs describe the corrections. Regression
tests also cover deleting the later repetition that supported a history link, same-batch state changes,
historical insertion, Unicode chunk boundaries and embedding request totals.

The backend adds tests through its actual checkpoint and current-fact projection. Both fail with the installed
`1.0.0-next.3`, reproducing the reported behavior. The published artifact passed external consumer checks and a
fresh backend registry install. The final backend API check passed 553 tests (13 skipped), types, lint and a
234.29 KiB gzip bundle guard. Its additional migration fixes preserve Unicode chunk boundaries, safely restart
incomplete older chunk formats, and report source drift during read-only inspection. Do not merge the backend
against an unpublished version or a local tarball dependency. Preview publication and code merge do not deploy
the API, migrate production accounts, or upload TestFlight.

## Batch-save release candidate: 1.0.0-next.5

This candidate adds bounded independent `rememberMany` saves across the SDK and HTTP service. A batch preserves
individual source sessions, dates, pinning and retry identity while sharing embedding work and one atomic
revision-guarded commit. Built-in stores support processed-ID batches; custom Store implementations must support
the `Changes.turns` list form before using this API. Corrections continue to use single-save `remember`.

Local verification covers mixed new/duplicate saves, provider failure, cancellation and erasure races,
forgotten IDs, all database adapters, HTTP/Worker persistence across restart, and the packaged Node consumer.
BetterMind's companion change batches migration pages and advances the cursor only after commit. A synthetic
first-page test with 100 ms database round trips and 500 ms embedding latency falls from 35.54 s to about 3 s.
That measures one embedder invocation; the OpenAI adapter can split a batch into concurrent HTTP requests.
The result is not a production latency measurement or a claim about retrieval quality.
