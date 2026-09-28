# FluidDB on Cloudflare

The Worker authenticates a trusted backend with `FLUID_TOKEN` and routes each person to a separate SQLite Durable
Object. That object owns the inbox, raw log, derived rows, vectors and an alarm. It requires no D1 database or
Vectorize index. The shared token is not end-user authentication; keep it out of browsers and mobile clients.

## Local setup

Install dependencies from the repository root, then in `packages/worker/` create the git-ignored `.dev.vars`:

```dotenv
FLUID_TOKEN=replace-with-a-long-random-service-token
OPENAI_API_KEY=your-openai-key
```

```sh
bun run dev
```

The service is at `http://localhost:8787`. Local rows persist in `.wrangler/state`. Calls to this development
server use real model providers and are billed. For a no-key/no-cost check, use `bun run test:runtime`: it runs
the production bundle under workerd with all outbound model requests intercepted, then removes its temporary DB.

For a synthetic end-to-end check with real providers, set `FLUID_TOKEN` in your shell and run:

```sh
bunx wrangler dev --var INGEST_DELAY:1
# In another terminal, with the same token:
bun scripts/smoke.ts
```

Set `INGEST_DELAY` through `.dev.vars` or `wrangler dev --var INGEST_DELAY:1` for that smoke test. The script sends
only its made-up person's text and removes that person's data at the end. It does not read the lab's `.env` or any
private conversations.

## Configuration

| Variable             | Default                               | Meaning                                                                           |
| -------------------- | ------------------------------------- | --------------------------------------------------------------------------------- |
| `FLUID_TOKEN`        | Required secret                       | Backend bearer token; at least 16 characters                                      |
| `OPENAI_API_KEY`     | Required secret                       | Extraction, linking, dossiers, embeddings, and the model decider                  |
| `PROCESSORS`         | `openai` in wrangler.jsonc            | Explicit comma-separated recipients allowed for conversation data                 |
| `DECIDER`            | `model`                               | `model` for OpenAI in Jev's place; `jev` for TypeSafe through OpenRouter          |
| `OPENROUTER_API_KEY` | Required with Jev                     | Jev credentials                                                                   |
| `PICK`               | `decider`                             | Closed-question picker; `model` ranks a list instead                              |
| `MODEL`              | `gpt-6-luna`                          | OpenAI-compatible text model used in the lab; configure one your account supports |
| `EMBEDDING_MODEL`    | `text-embedding-3-small`              | Keep stable while a store contains vectors                                        |
| `ASSISTANT` / `ROLE` | `the assistant` / `therapy assistant` | Name and context used in memory prompts                                           |
| `THRESHOLD`          | `0.5`                                 | Repetition detector's minimum confidence; tune for your product                   |
| `INGEST_DELAY`       | `300`                                 | Quiet seconds before processing; maximum initial wait is one hour                 |

To enable Jev, set `DECIDER=jev`, allow `openai,openrouter,typesafe`, and configure the OpenRouter secret. A
deployment whose data may only reach OpenAI must keep `DECIDER=model`. There is no automatic provider failover.

## API

All `/v1` routes require `Authorization: Bearer <FLUID_TOKEN>`. IDs are scoped to a person. JSON is validated at
the boundary. Errors contain a kind and message, without the raw provider response or conversation text.

| Method | Path                                         | Body / result                                                 |
| ------ | -------------------------------------------- | ------------------------------------------------------------- |
| GET    | `/health`                                    | `{ ok: true }`; no token, configuration must be valid         |
| POST   | `/v1/people/:person/sessions/:session/turns` | `{ turns }`; 202 once stored in the inbox                     |
| POST   | Same path with `?wait=true`                  | Process now; returns ingestion counts                         |
| POST   | `/v1/people/:person/recall`                  | `{ conversation, name?, render? }`; evidence and prompt       |
| GET    | `/v1/people/:person/dossier`                 | Current dossier, or 404 when absent                           |
| GET    | `/v1/people/:person/status`                  | Inbox, windows, statements, groups, dossier progress          |
| POST   | `/v1/people/:person/retry`                   | Retry inbox turns set aside after repeated failures           |
| POST   | `/v1/people/:person/forget`                  | `{ session? , statements?, windows? }`; at least one selector |
| DELETE | `/v1/people/:person`                         | Erase all live data and pending work for that person          |

Use the typed `@fluiddb/fluiddb/client` from your backend. Each turn needs a globally unique ID within that person's
memory. New events get new IDs; deliveries retried after forgetting cannot restore old content. The `forget`
semantics and source-window deletion scope are described in the [package README](../../README.md).

An inbox session is attempted five times, with increasing backoff, then set aside until `retry`. Dossier failures
remain pending and retry independently. A `wait=true` request can return successful ingestion while the dossier
is still pending; check `status.windows.pending`. Logs contain only operation names, error classes, counts,
timings and provider token usage. Configure budget/rate controls in the calling product and provider accounts.

## Deployment

Choose the Worker name in `wrangler.jsonc`, authenticate Wrangler to the intended Cloudflare account, then:

```sh
bunx wrangler secret put FLUID_TOKEN
bunx wrangler secret put OPENAI_API_KEY
# Only if using Jev:
# bunx wrangler secret put OPENROUTER_API_KEY
bun run deploy
```

`wrangler.jsonc` includes the SQLite Durable Object migration. Change neither its class name nor migration tags
on an existing deployment without a migration plan. SQL migrations are applied transactionally on object
construction. Back up and test migration/recovery on staging before using existing production data.

Run `bun run test:runtime` before deployment. It covers RPC, SQLite vector blobs, auth, isolation, deduplication,
process restart, background alarms and forgetting. It does not establish provider quality, production latency,
large-history capacity or Cloudflare snapshot retention. Begin with a staging account and synthetic data.
