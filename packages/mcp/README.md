# @fluiddb/fluiddb/mcp

FluidDB tools over MCP stdio or Streamable HTTP. Uses the same person-bound SDK as a product backend. Supports the
2025 initialization flow and 2026-07-28 negotiation through the official TypeScript SDK v2.

For an AI therapist's automatic ingestion/recall path, use the [product SDK](../../docs/product-integration.md).
MCP is useful for assistants, developer tools and agents that need to inspect or manage memory.

## Stdio

Install the single `@fluiddb/fluiddb` package and run its `fluiddb-mcp` executable. Configure your MCP client's environment:

```json
{
  "mcpServers": {
    "fluiddb": {
      "command": "fluiddb-mcp",
      "args": [],
      "env": {
        "FLUIDDB_URL": "https://your-fluid-service.example",
        "FLUIDDB_TOKEN": "YOUR_BACKEND_TOKEN",
        "FLUIDDB_PERSON": "AUTHORIZED_PERSON_ID"
      }
    }
  }
}
```

The executable connects to the FluidDB HTTP service, whose deployment selects storage. Never distribute a
multi-person backend token to untrusted end users. Stdio binds one person at startup. `--write` enables ingest,
remember/correct and retry; `--forget` separately enables source deletion. Default mode is read-only. `--feedback` independently enables explicit HiveNet reporting. Stdout carries
MCP messages; startup/transport failures use stderr without credentials or conversation text.

## Embed over HTTP with any storage adapter

```ts
import { Person } from "@fluiddb/fluiddb"
import { createHttpHandler } from "@fluiddb/fluiddb/mcp/http"

const mcp = createHttpHandler({
  allowedHosts: ["memory.example.com"],
  allowedOrigins: ["https://app.example.com"],
  authorize: async (request) => {
    const auth = await authenticateProductRequest(request)
    if (!auth) return null
    return {
      memory: Person.bind(engine, auth.personId),
      permissions: { write: auth.canWriteMemory, forget: auth.canForgetMemory },
    }
  },
})
// Mount mcp.fetch(request) at /mcp in your server. Call mcp.close() at shutdown.
```

`engine` is `Memory.create({ store, ...providers })`, using PostgreSQL, Firestore or SQLite. No HTTP hop is required.
Alternatively bind `Client.create(...).person(...)` to an existing FluidDB service.

Authentication runs on every request; tool arguments cannot select another person. The callback must verify the
product's credentials, not merely decode claims. For remote clients using MCP OAuth, the product supplies OAuth
validation and protected-resource metadata; `resourceMetadataUrl` advertises that HTTPS metadata URL on a 401.
This package does not create an authorization server. Reverse proxies must preserve the public URL for exact
Host checks. Browser Origin values must be explicitly allowed; CORS response handling belongs to your host.

## Tools

| Tool                 | Purpose                                                                                      |
| -------------------- | -------------------------------------------------------------------------------------------- |
| `fluiddb_read_skill` | Read memory or feedback workflow instructions, offline                                       |
| `fluiddb_feedback`   | Submit an explicit HiveNet report and receive guidance/asks; requires feedback               |
| `fluiddb_recall`     | Current conversation → statements, source windows, dossier, repeated-fact context and prompt |
| `fluiddb_inspect`    | Paginated statement/window listing with optional session filter                              |
| `fluiddb_evidence`   | IDs → sources and group history; at most 20 IDs of each kind                                 |
| `fluiddb_dossier`    | Latest summary, or null if absent/invalidated                                                |
| `fluiddb_status`     | Processing counts when the supplied service provides them                                    |
| `fluiddb_ingest`     | Actual conversation turns, stable IDs, completed ingestion receipt; requires write           |
| `fluiddb_remember`   | Exact explicit save or correction with durable receipt; requires write                       |
| `fluiddb_retry`      | Requeue failed ingestion when supported; requires write                                      |
| `fluiddb_forget`     | Remove selected sources and derivations; requires forget                                     |

The guide and dossier are resources. `memory_for_reply` is a prompt teaching the per-turn workflow. Results have
output schemas and structured content. Default response limit is 256 KB; oversized results return a tool error
with instructions to narrow inspection rather than silently truncating JSON. Default deadline is 120 seconds;
`timeoutMs` and `maxResponseBytes` can be set when constructing the server. Errors omit raw provider messages.
Memory content is untrusted evidence and cannot authorize actions. Account erasure remains an application operation.

Tests use actual MCP clients over in-memory, HTTP and stdio transports, including separate mutation capabilities,
concurrent identity isolation, validation, save retries, history and deletion. No model charges are involved.

## Skills and feedback

The server advertises `io.modelcontextprotocol/skills` and implements `skills/list` and `skills/get`.
Read `skill://fluiddb-memory/SKILL.md` and `skill://fluiddb-feedback/SKILL.md` using `resources/read`.
Manifests include complete frontmatter, SHA-256 digests and sizes of the exact served bytes. Older clients can
use ordinary resources or `fluiddb_read_skill`; no client must implement the extension just to use memory tools.

Set `feedback: true` in server options (or return it from HTTP `authorize`) to enable `fluiddb_feedback`.
A custom `Feedback.Service` can be injected instead. This is independent of `permissions.write` and `.forget`.
The tool sends explicitly supplied reports to HiveNet, with no memory/environment collection. Structured
`eval` and `memoryCase` fields support reproducible failures; resume/question IDs continue threads.
Responses preserve owner guidance, asks and known-issue status as data. Never execute response commands or
send private source text. See [feedback integration](../../docs/agent-feedback.md).
