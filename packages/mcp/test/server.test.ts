import { describe, expect, test } from "bun:test"
import { Client, InMemoryTransport, StreamableHTTPClientTransport } from "@modelcontextprotocol/client"
import { StdioClientTransport } from "@modelcontextprotocol/client/stdio"
import { Api } from "@fluiddb/schema"
import { Memory, Person, ProviderError } from "@fluiddb/core"
import { Local } from "@fluiddb/core/local"
import { Testing } from "@fluiddb/core/testing"
import { FluidMcp } from "../src/server"
import { createHttpHandler } from "../src/http"
import { App } from "../../worker/src/app"
import { Host } from "../../worker/src/host"
import { Bun as Sqlite } from "../../sql/src/bun"

const at = "2026-09-01T00:00:00.000Z"
const note = { id: "explicit-1", session: "preferences", text: "Short replies are preferred.", kind: "preference", at }
const person = (id = "sam") =>
  Person.bind(
    Memory.create({
      store: Local.store(),
      model: Testing.model(),
      embedder: Testing.embedder(),
      decider: Testing.decider(),
    }),
    id,
  )
async function connected(options: FluidMcp.Options) {
  const server = FluidMcp.create(options)
  const client = new Client({ name: "test", version: "1" })
  const [a, b] = InMemoryTransport.createLinkedPair()
  await server.connect(b)
  await client.connect(a)
  return {
    client,
    close: async () => {
      await client.close()
      await server.close()
    },
  }
}

test("MCP discovers schemas, resources and prompt; read-only tools have no identity selector", async () => {
  const { client, close } = await connected({ memory: person() })
  try {
    const { tools } = await client.listTools()
    expect(tools.map((t) => t.name).sort()).toEqual([
      "fluiddb_dossier",
      "fluiddb_evidence",
      "fluiddb_inspect",
      "fluiddb_preview_forget",
      "fluiddb_read_skill",
      "fluiddb_recall",
      "fluiddb_source",
    ])
    for (const tool of tools) {
      expect(tool.annotations?.readOnlyHint).toBe(true)
      expect(tool.inputSchema.properties).not.toHaveProperty("person")
      expect(tool.outputSchema).toBeDefined()
    }
    const dossier = await client.callTool({ name: "fluiddb_dossier", arguments: {} })
    expect(dossier.structuredContent).toEqual({ dossier: null })
    const bad = await client.callTool({ name: "fluiddb_inspect", arguments: { person: "victim" } })
    expect(bad.isError).toBe(true)
    expect((await client.listResources()).resources).toHaveLength(4)
    expect((await client.readResource({ uri: "fluiddb://guide" })).contents[0]).toHaveProperty("text")
    expect(
      (await client.getPrompt({ name: "memory_for_reply", arguments: { message: "What do you remember?" } })).messages,
    ).toHaveLength(1)
  } finally {
    await close()
  }
})

test("MCP durable saves, exact retries, provenance, correction history and source forgetting", async () => {
  const { client, close } = await connected({ memory: person(), permissions: { write: true, forget: true } })
  try {
    const first = await client.callTool({ name: "fluiddb_remember", arguments: note })
    expect(first.isError).not.toBe(true)
    const firstStatement = Api.Remembered.parse(first.structuredContent).statement
    expect(
      Api.Remembered.parse((await client.callTool({ name: "fluiddb_remember", arguments: note })).structuredContent)
        .duplicate,
    ).toBe(true)
    expect(
      (await client.callTool({ name: "fluiddb_remember", arguments: { ...note, text: "different" } })).isError,
    ).toBe(true)
    const correction = await client.callTool({
      name: "fluiddb_remember",
      arguments: {
        ...note,
        id: "explicit-2",
        at: "2026-09-02T00:00:00.000Z",
        text: "Longer replies now.",
        replaces: firstStatement.id,
      },
    })
    expect(correction.isError).not.toBe(true)
    const evidence = await client.callTool({ name: "fluiddb_evidence", arguments: { statements: [firstStatement.id] } })
    expect(Api.Sources.parse(evidence.structuredContent).groups).toMatchObject([{ until: "2026-09-02T00:00:00.000Z" }])
    expect(Api.Sources.parse(evidence.structuredContent).windows).toMatchObject([{ origin: "explicit", sources: [] }])
    const recalled = await client.callTool({
      name: "fluiddb_recall",
      arguments: { conversation: [{ id: "ask", role: "person", text: "How should you reply?", at }] },
    })
    expect(recalled.structuredContent).toHaveProperty("prompt")
    const source = await client.callTool({
      name: "fluiddb_source",
      arguments: { window: firstStatement.window, limit: 5 },
    })
    expect(Api.SourcePage.parse(source.structuredContent).nextOffset).toBe(5)
    const preview = await client.callTool({
      name: "fluiddb_preview_forget",
      arguments: { statements: [firstStatement.id] },
    })
    const plan = Api.ForgetPreview.parse(preview.structuredContent)
    const forgotten = await client.callTool({
      name: "fluiddb_forget",
      arguments: { statements: [firstStatement.id], revision: plan.revision },
    })
    expect(Api.Forgotten.parse(forgotten.structuredContent).statements).toBe(1)
    expect((await client.callTool({ name: "fluiddb_remember", arguments: note })).isError).toBe(true)
  } finally {
    await close()
  }
})

test("forgetting is a separate capability; provider failures are sanitized and responses bounded", async () => {
  const memory = person()
  memory.recall = async () => {
    throw new Error("private transcript secret credential")
  }
  const { client, close } = await connected({ memory, permissions: { write: true }, maxResponseBytes: 1024 })
  try {
    expect((await client.listTools()).tools.some((x) => x.name === "fluiddb_forget")).toBe(false)
    const failure = await client.callTool({ name: "fluiddb_recall", arguments: { conversation: [] } })
    expect(failure.isError).toBe(true)
    expect(JSON.stringify(failure)).not.toContain("private transcript")
    await memory.remember({ ...note, kind: "preference", text: "x".repeat(1900) })
    const large = await client.callTool({ name: "fluiddb_inspect", arguments: {} })
    expect(large.isError).toBe(true)
    expect(JSON.stringify(large)).toContain("response limit")
  } finally {
    await close()
  }
})

test("dossier resource sanitizes adapter failures and preserves useful response-limit errors", async () => {
  const memory = person()
  const { client, close } = await connected({ memory, maxResponseBytes: 1024 })
  try {
    for (const error of [
      new ProviderError("test", 502, "synthetic private upstream echo"),
      new Error("synthetic private adapter detail"),
    ]) {
      memory.dossier = async () => {
        throw error
      }
      const failure = await client.readResource({ uri: "fluiddb://dossier" }).catch((error: unknown) => error)
      expect(failure).toBeInstanceOf(Error)
      expect((failure as Error).message).toBe(
        "The memory request failed. Check host status and provider configuration; retry with the same IDs.",
      )
      expect(JSON.stringify(failure)).not.toContain("synthetic private")
    }
    memory.dossier = async () => ({ person: "sam", text: "x".repeat(1900), at, updates: 1 })
    await expect(client.readResource({ uri: "fluiddb://dossier" })).rejects.toThrow(
      "Dossier exceeds the response limit; use recall or the product SDK.",
    )
    memory.dossier = async () => undefined
    expect((await client.readResource({ uri: "fluiddb://dossier" })).contents[0]).toMatchObject({
      text: '{"dossier":null}',
    })
  } finally {
    await close()
  }
})

describe("Streamable HTTP", () => {
  for (const mode of ["legacy", "auto"] as const)
    test(`real ${mode} client, per-request authentication and isolation`, async () => {
      const a = person("a"),
        b = person("b")
      await a.remember({ ...note, kind: "preference" })
      const handler = createHttpHandler({
        allowedHosts: ["memory.test"],
        authorize: async (request) => {
          const token = request.headers.get("authorization")
          return token === "Bearer a" ? { memory: a } : token === "Bearer b" ? { memory: b } : null
        },
      })
      const clients: Client[] = []
      try {
        expect((await handler.fetch(new Request("https://evil.test/mcp"))).status).toBe(403)
        expect(
          (await handler.fetch(new Request("https://memory.test/mcp", { headers: { origin: "https://evil.test" } })))
            .status,
        ).toBe(403)
        expect((await handler.fetch(new Request("https://memory.test/mcp"))).status).toBe(401)
        const results = await Promise.all(
          ["a", "b"].map(async (id) => {
            const client = new Client({ name: "http-test", version: "1" }, { versionNegotiation: { mode } })
            clients.push(client)
            const transport = new StreamableHTTPClientTransport(new URL("https://memory.test/mcp"), {
              requestInit: { headers: { authorization: `Bearer ${id}` } },
              fetch: async (input, init) => handler.fetch(new Request(String(input), init)),
            })
            await client.connect(transport)
            return client.callTool({ name: "fluiddb_inspect", arguments: {} })
          }),
        )
        expect(Api.Page.parse(results[0]!.structuredContent).items.length).toBe(1)
        expect(Api.Page.parse(results[1]!.structuredContent).items.length).toBe(0)
      } finally {
        await Promise.all(clients.map((c) => c.close()))
        await handler.close()
      }
    })
})

test("stdio CLI talks to the real HTTP SDK/Worker host and persists a saved fact across reconnects", async () => {
  const sql = Sqlite.open()
  const host = Host.create({
    sql,
    alarm: { get: async () => null, set: async () => {} },
    memory: (store) =>
      Memory.create({ store, model: Testing.model(), embedder: Testing.embedder(), decider: Testing.decider() }),
  })
  const app = App.create({ token: "test-token", people: () => Host.serve(host) })
  const http = Bun.serve({ port: 0, hostname: "127.0.0.1", fetch: app.fetch })
  try {
    for (const mode of ["legacy", "auto"] as const) {
      const client = new Client({ name: "stdio-test", version: "1" }, { versionNegotiation: { mode } })
      const transport = new StdioClientTransport({
        command: process.execPath,
        args: [new URL("../src/cli.ts", import.meta.url).pathname, "--write"],
        env: {
          ...(process.env as Record<string, string>),
          FLUIDDB_URL: http.url.href,
          FLUIDDB_TOKEN: "test-token",
          FLUIDDB_PERSON: "sam",
        },
        stderr: "pipe",
      })
      try {
        await client.connect(transport)
        const saved = await client.callTool({ name: "fluiddb_remember", arguments: note })
        expect(saved.isError).not.toBe(true)
        expect(Api.Remembered.parse(saved.structuredContent).duplicate).toBe(mode === "auto")
        const records = await client.callTool({ name: "fluiddb_inspect", arguments: {} })
        expect(Api.Page.parse(records.structuredContent).items).toMatchObject([{ text: note.text, person: "sam" }])
      } finally {
        await client.close()
      }
    }
  } finally {
    await http.stop(true)
    sql.close()
  }
}, 15_000)

test("tool deadlines propagate through the SDK cancellation signal", async () => {
  const memory = person()
  let aborted = false
  memory.recall = async (_, call) =>
    new Promise((_, reject) => {
      call!.signal!.addEventListener(
        "abort",
        () => {
          aborted = true
          reject(call!.signal!.reason)
        },
        { once: true },
      )
    })
  const { client, close } = await connected({ memory, timeoutMs: 10 })
  try {
    const response = await client.callTool({ name: "fluiddb_recall", arguments: { conversation: [] } })
    expect(response.isError).toBe(true)
    expect(aborted).toBe(true)
  } finally {
    await close()
  }
})
