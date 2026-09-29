import { expect, test } from "bun:test"
import { Client, InMemoryTransport, StreamableHTTPClientTransport } from "@modelcontextprotocol/client"
import { z } from "zod"
import { Memory, Person } from "@fluiddb/core"
import { Local } from "@fluiddb/core/local"
import { Testing } from "@fluiddb/core/testing"
import { Feedback, FeedbackError } from "@fluiddb/feedback"
import { FluidMcp } from "../src/server"
import { createHttpHandler } from "../src/http"

const memory = () =>
  Person.bind(
    Memory.create({
      store: Local.store(),
      model: Testing.model(),
      embedder: Testing.embedder(),
      decider: Testing.decider(),
    }),
    "private-person-never-in-feedback",
  )
const Entry = z.object({
  uri: z.string(),
  frontmatter: z.object({ name: z.string(), description: z.string() }),
  resources: z.array(z.object({ uri: z.string(), digest: z.string(), size: z.number() })),
})
const Catalog = z.object({
  skills: z.array(Entry),
  resultType: z.literal("complete").optional(),
  ttlMs: z.number().optional(),
  cacheScope: z.string().optional(),
})

for (const mode of ["legacy", "auto"] as const)
  test(`${mode} HTTP client discovers and verifies skill manifests, resources and read-tool fallback`, async () => {
    const handler = createHttpHandler({ allowedHosts: ["memory.test"], authorize: async () => ({ memory: memory() }) })
    const client = new Client({ name: "skill-test", version: "1" }, { versionNegotiation: { mode } })
    const wireResults: unknown[] = []
    try {
      await client.connect(
        new StreamableHTTPClientTransport(new URL("https://memory.test/mcp"), {
          fetch: async (input, init) => {
            const request = new Request(String(input), init)
            const params =
              request.method === "POST" ? ((await request.clone().json()) as { method?: string }) : undefined
            const response = await handler.fetch(request)
            if (params?.method === "skills/list" && response.ok) {
              const body = await response.clone().text()
              const json = response.headers.get("content-type")?.includes("text/event-stream")
                ? body
                    .split("\n")
                    .find((line) => line.startsWith("data:"))!
                    .slice(5)
                : body
              wireResults.push(JSON.parse(json).result)
            }
            return response
          },
        }),
      )
      if (mode === "auto")
        expect(client.getServerCapabilities()?.extensions?.["io.modelcontextprotocol/skills"]).toEqual({})
      const catalog = await client.request({ method: "skills/list", params: {} }, Catalog)
      expect(catalog.skills).toHaveLength(2)
      if (mode === "auto")
        expect(wireResults[0]).toMatchObject({ resultType: "complete", ttlMs: 300_000, cacheScope: "public" })
      for (const entry of catalog.skills) {
        const got = await client.request(
          { method: "skills/get", params: { uri: entry.uri } },
          z.object({ skill: Entry }),
        )
        expect(got.skill).toEqual(entry)
        for (const resource of entry.resources) {
          const result = await client.readResource({ uri: resource.uri })
          const text = (result.contents[0] as { text: string }).text
          const bytes = new TextEncoder().encode(text)
          expect(bytes.byteLength).toBe(resource.size)
          expect(`sha256:${new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)).toHex()}`).toBe(resource.digest)
          const fallback = await client.callTool({
            name: "fluiddb_read_skill",
            arguments: {
              name: entry.frontmatter.name,
              ...(resource.uri === entry.uri
                ? {}
                : { path: resource.uri.slice(`skill://${entry.frontmatter.name}/`.length) }),
            },
          })
          expect(fallback.isError).not.toBe(true)
          expect((fallback.structuredContent as { text: string }).text).toBe(text)
        }
      }
      const resources = (await client.listResources()).resources
      expect(new Set(resources.map((r) => r.name)).size).toBe(resources.length)
      expect(resources.map((r) => r.uri)).toContain("skill://fluiddb-memory/references/memory-model.md")
      const tools = (await client.listTools()).tools
      expect(tools.find((tool) => tool.name === "fluiddb_read_skill")?.annotations).toMatchObject({
        readOnlyHint: true,
        destructiveHint: false,
        openWorldHint: false,
      })
      expect(tools.some((tool) => tool.name === "fluiddb_remember")).toBe(false)
      for (const path of ["../../.env", "../fluiddb-feedback/SKILL.md", "https://example.com/skill.md", "missing.md"])
        expect(
          (await client.callTool({ name: "fluiddb_read_skill", arguments: { name: "fluiddb-memory", path } })).isError,
        ).toBe(true)
      await expect(
        client.request({ method: "skills/get", params: { uri: "file:///private" } }, z.object({})),
      ).rejects.toBeDefined()
      await expect(
        client.request({ method: "skills/list", params: { cursor: "invalid" } }, Catalog),
      ).rejects.toBeDefined()
    } finally {
      await client.close()
      await handler.close()
    }
  })

test("feedback is independent of memory write permission, carries only supplied input and surfaces replies", async () => {
  const requests: Feedback.Report[] = []
  const m = memory()
  await m.remember({
    id: "secret",
    session: "personal",
    text: "Private memory must never leave this store.",
    kind: "life",
    at: "2026-09-01T00:00:00Z",
  })
  const server = FluidMcp.create({
    memory: m,
    feedback: {
      submit: async (report) => {
        requests.push(report)
        return {
          id: "event",
          thread: "thread-test",
          idempotencyKey: "retry-test",
          guidance: "Observed reply",
          ask: { id: "q1", prompt: "Did this work?", command: "untrusted command" },
          known_issue: { title: "Synthetic issue", reports: 1, status: "open", hint: "Recorded" },
        }
      },
    },
  })
  const client = new Client({ name: "feedback-test", version: "1" })
  const [a, b] = InMemoryTransport.createLinkedPair()
  await server.connect(b)
  await client.connect(a)
  try {
    const tools = (await client.listTools()).tools
    expect(tools.some((t) => t.name === "fluiddb_remember")).toBe(false)
    expect(tools.find((t) => t.name === "fluiddb_feedback")?.annotations).toMatchObject({
      readOnlyHint: false,
      destructiveHint: false,
      idempotentHint: false,
      openWorldHint: true,
    })
    const input: Feedback.Report = {
      feedback: "Synthetic correction check",
      subject: "fluiddb_recall",
      eval: { task: "Recall a corrected synthetic fact", expected: "New fact", actual: "Old fact" },
      memoryCase: { operation: "recall", challenge: "correction", adapter: "firestore" },
    }
    const result = await client.callTool({ name: "fluiddb_feedback", arguments: input })
    expect(result.isError).not.toBe(true)
    expect(result.structuredContent).toMatchObject({
      thread: "thread-test",
      guidance: "Observed reply",
      ask: { id: "q1" },
      known_issue: { reports: 1 },
    })
    expect(requests).toEqual([{ ...input, category: "mcp" }])
    expect(JSON.stringify(requests)).not.toContain("private-person")
    expect(JSON.stringify(requests)).not.toContain("Private memory")
    expect(
      (await client.callTool({ name: "fluiddb_feedback", arguments: { ...input, person: "private-person" } })).isError,
    ).toBe(true)
    expect(requests).toHaveLength(1)
  } finally {
    await client.close()
    await server.close()
  }
})

test("feedback failure returns retry identity without leaking upstream content", async () => {
  const server = FluidMcp.create({
    memory: memory(),
    feedback: {
      submit: async () => {
        throw new FeedbackError("Delivery unconfirmed", "thread-test", "retry-test", 503)
      },
    },
  })
  const client = new Client({ name: "feedback-test", version: "1" })
  const [a, b] = InMemoryTransport.createLinkedPair()
  await server.connect(b)
  await client.connect(a)
  try {
    const result = await client.callTool({ name: "fluiddb_feedback", arguments: { feedback: "Synthetic report" } })
    expect(result.isError).toBe(true)
    expect(JSON.stringify(result)).toContain("retry-test")
  } finally {
    await client.close()
    await server.close()
  }
})
