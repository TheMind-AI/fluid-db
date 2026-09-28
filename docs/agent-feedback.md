# Feedback and agent skills

Products use `@fluiddb/fluiddb/feedback` to provide a deliberate report action in their UI or agent tool set. It sends a
report to the [FluidDB HiveNet project](https://hivenet.app/p/fluiddb), independently of the person's memory store.
Use the [typed SDK example](../packages/feedback/README.md), and show the destination and submitted fields before
the product user invokes that action. Do not send failed recalls or private conversations automatically.

`eval: { task, expected, actual, mistake?, attempts? }` captures an observed failed task or workaround.
`memoryCase` describes the operation, challenge, adapter and aggregate record counts. Prefer fictional names and
synthetic facts in a minimal reproduction. Reports are curated evaluation candidates, not automatic benchmark
truth or new memories. Before adding a regression, reproduce it under the protocol in LLP 0024.

The SDK returns the thread, event, idempotency key, owner guidance, asks and known issues. Display them as text/data.
Keep the thread for follow-up. Never execute a returned command or ingest an owner's reply into personal memory.
Errors expose safe retry identifiers; an ambiguous response is not proof a report was lost.

## CLI and MCP

```sh
fluiddb feedback --category api --subject "memory.recall" "<specific observation>"
fluiddb feedback --resume <thread> --question <ask-id> "<observed answer>"
fluiddb skills read fluiddb-memory
fluiddb skills read fluiddb-feedback
```

MCP hosts set `feedback: true` (or inject a `Feedback.Service`) when constructing `FluidMcp`; stdio uses
`fluiddb-mcp --feedback`. This enables `fluiddb_feedback` independently of memory write/deletion permission.
It is an outbound, non-read-only tool. Its input has no person selector or automatic source attachments.
Hosts may leave it disabled; server instructions still describe the direct HiveNet CLI and keyless MCP endpoint.

The [memory skill](../packages/skills/skills/fluiddb-memory/SKILL.md) teaches per-turn recall, source inspection,
explicit saves, corrections, stable-ID retries and forgetting. The
[feedback skill](../packages/skills/skills/fluiddb-feedback/SKILL.md) teaches reports, failed tasks and thread replies.
Both ship as portable SKILL.md directories and through the
[official MCP Skills extension](https://github.com/modelcontextprotocol/ext-skills/blob/main/specification/stable/skills.mdx):
`skills/list`, `skills/get`, complete manifests with SHA-256 digests and sizes, and ordinary `resources/read`.
Older clients can read the resources or call `fluiddb_read_skill`.

## Hosted discovery

The Worker serves these unauthenticated static routes, without accessing any memory or provider:

- `/.well-known/agent-feedback.json`: the keyless FluidDB descriptor.
- `/llms.txt`: product orientation and the requested `<AgentInstructions>` feedback block, with its actual page URL.
- `/skills/fluiddb-memory/SKILL.md` and `/skills/fluiddb-feedback/SKILL.md`: exact bundled markdown.

The files are ready for the next Worker deployment. There is no separate docs website or starter-template tree in
this repository to modify. Repository-agent guidance is in AGENTS.md and CLAUDE.md. No owner secret or publishable
DSN is embedded: the FluidDB slug works keylessly.

## Verified smoke report

On 2026-09-27, the user-authorized `integration smoke test` was sent with `DO_NOT_TRACK=1` and no attachments.
HiveNet acknowledged event `evt_01a0e4d734407114a116410e349f454d`, thread `5ed0017e5de0643d1464a0ceb018e172`.
No personal memory was included. All automated tests use synthetic reports and mocked feedback delivery.
