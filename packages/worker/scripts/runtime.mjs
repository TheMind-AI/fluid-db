import assert from "node:assert/strict"
import { mkdtemp, readFile, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import path from "node:path"
import { fileURLToPath } from "node:url"
import { Miniflare, convertV4MiniflareOptions } from "miniflare"

// Real workerd, RPC, SQLite, persistence, alarms and the production fetch adapters. All outbound requests are
// intercepted here: fixed synthetic responses test the wiring, without keys, external traffic or model charges.
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..")
const directory = await mkdtemp(path.join(tmpdir(), "fluiddb-runtime-"))
const token = "synthetic-runtime-test-token"
const fact = "Their sister Anna lives in Berlin."
const calls = { model: 0, embedding: 0 }
async function provider(request) {
  const url = new URL(request.url)
  assert.equal(url.origin, "https://api.openai.com", "unexpected outbound provider")
  const body = await request.json()
  if (url.pathname === "/v1/embeddings") {
    calls.embedding++
    return Response.json({ data: body.input.map((_, index) => ({ index, embedding: [1, 0, 0] })) })
  }
  assert.equal(url.pathname, "/v1/chat/completions")
  calls.model++
  const properties = body.response_format?.json_schema?.schema?.properties
  const prompt = body.messages[0].content
  let output = fact
  if (properties?.items) output = { items: [{ text: fact, kind: "life" }] }
  else if (properties?.relation) output = { relation: "same", n: 1 }
  else if (properties?.yes_no) {
    const state = JSON.parse(prompt.split("STATE:\n")[1].split("\n\nYES/NO QUESTIONS")[0])
    const keys = [...prompt.matchAll(/^- (\w+):/gm)].map((x) => x[1])
    output = {
      yes_no: keys.filter((key) => key !== "pick").map((key) => ({ key, yes: true, probability: 0.9 })),
      choices: keys.includes("pick")
        ? [
            {
              key: "pick",
              choice: Object.keys(state.memory)[0],
              probabilities: [
                ...Object.keys(state.memory).map((option, i) => ({ option, p: i === 0 ? 0.9 : 0 })),
                { option: "none", p: 0.1 },
              ],
            },
          ]
        : [],
    }
  }
  return Response.json({
    choices: [
      { message: { content: typeof output === "string" ? output : JSON.stringify(output) }, finish_reason: "stop" },
    ],
  })
}

const options = convertV4MiniflareOptions({
  name: "fluiddb-runtime",
  modules: true,
  scriptPath: path.join(root, ".wrangler/build/index.js"),
  compatibilityDate: "2026-09-01",
  resourcePersistencePath: directory,
  durableObjects: { PERSON: { className: "Person", useSQLite: true } },
  bindings: { FLUID_TOKEN: token, OPENAI_API_KEY: "synthetic", PROCESSORS: "openai", INGEST_DELAY: "0" },
  outboundService: provider,
})
let runtime = new Miniflare(options)
const call = async (method, route, body, status = 200) => {
  const response = await runtime.dispatchFetch(`http://fluid.test${route}`, {
    method,
    headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  })
  const value = await response.json()
  assert.equal(response.status, status, JSON.stringify(value))
  return value
}
const turns = [{ id: "p1", role: "person", text: "My sister Anna lives in Berlin.", at: "2026-03-02T09:00:00Z" }]
try {
  await call("GET", "/health")
  const descriptor = await runtime.dispatchFetch("http://fluid.test/.well-known/agent-feedback.json")
  assert.equal((await descriptor.json()).slug, "fluiddb")
  const skill = await runtime.dispatchFetch("http://fluid.test/skills/fluiddb-memory/SKILL.md")
  assert.match(await skill.text(), /fluiddb_recall/)
  const model = await runtime.dispatchFetch("http://fluid.test/skills/fluiddb-memory/references/memory-model.md")
  assert.equal(model.status, 200)
  assert.equal(
    await model.text(),
    await readFile(path.join(root, "../skills/skills/fluiddb-memory/references/memory-model.md"), "utf8"),
  )
  assert.deepEqual(calls, { model: 0, embedding: 0 })
  const docs = await runtime.dispatchFetch("http://fluid.test/llms.txt")
  assert.match(await docs.text(), /<AgentInstructions>/)
  assert.equal((await runtime.dispatchFetch("http://fluid.test/v1/people/sam/status")).status, 401)
  assert.equal((await call("POST", "/v1/people/sam/sessions/s1/turns?wait=true", { turns })).statements, 1)
  const ask = { conversation: [{ ...turns[0], id: "q1" }], name: "Sam" }
  const answer = await call("POST", "/v1/people/sam/recall", ask)
  assert.match(answer.prompt, /Anna/)
  assert.equal(answer.recall.retold.statement.text, fact)
  assert.equal((await call("GET", "/v1/people/another/status")).statements, 0)

  const batch = Array.from({ length: 20 }, (_, i) => ({
    id: `import-${i}`,
    session: `source-${i}`,
    text: `Fictional imported fact ${i}.`,
    at: turns[0].at,
    kind: "life",
    pinned: false,
    origin: "import",
  }))
  const saved = await call("POST", "/v1/people/batch/remember/batch", batch)
  assert.equal(saved.length, 20)
  assert.equal(
    saved.every((result) => !result.duplicate),
    true,
  )

  // A new runtime must read committed rows, including vector blobs, from the previous object's SQLite.
  await runtime.dispose()
  runtime = new Miniflare(options)
  assert.equal((await call("GET", "/v1/people/batch/status")).statements, 20)
  assert.equal(
    (await call("POST", "/v1/people/batch/remember/batch", batch)).every((r) => r.duplicate),
    true,
  )
  await call("POST", "/v1/people/batch/forget", { session: "source-0" })
  await call("POST", "/v1/people/batch/remember/batch", batch, 409)
  assert.equal((await call("GET", "/v1/people/batch/status")).statements, 19)
  await call("DELETE", "/v1/people/batch")
  assert.equal((await call("GET", "/v1/people/sam/status")).statements, 1)
  assert.equal((await call("POST", "/v1/people/sam/recall", ask)).recall.statements.length, 1)
  assert.equal((await call("POST", "/v1/people/sam/sessions/s1/turns?wait=true", { turns })).turns, 0)

  await call("POST", "/v1/people/sam/sessions/s2/turns", { turns: [{ ...turns[0], id: "p2" }] }, 202)
  const until = Date.now() + 15_000
  while (true) {
    const status = await call("GET", "/v1/people/sam/status")
    if (status.statements === 2 && !status.inbox.pending && !status.windows.pending) break
    assert.ok(Date.now() < until, "background alarm did not finish")
    await new Promise((resolve) => setTimeout(resolve, 100))
  }
  await call("POST", "/v1/people/sam/forget", { statements: [answer.recall.statements[0].ids[0]] })
  assert.equal((await call("GET", "/v1/people/sam/status")).statements, 1)
  await call("POST", "/v1/people/sam/forget", { session: "s2" })
  assert.equal((await call("POST", "/v1/people/sam/recall", ask)).recall.statements.length, 0)
  assert.equal((await call("POST", "/v1/people/sam/sessions/s1/turns?wait=true", { turns })).turns, 0)
  await call("DELETE", "/v1/people/sam")
  assert.deepEqual(await call("GET", "/v1/people/sam/status"), {
    inbox: { pending: 0, failed: 0 },
    windows: { total: 0, pending: 0 },
    statements: 0,
    groups: 0,
  })
  assert.ok(calls.model && calls.embedding)
  console.log(
    "Cloudflare runtime passed: auth, ingestion, recall, tenant isolation, restart, alarms, erasure and retries.",
  )
} finally {
  await runtime.dispose()
  await rm(directory, { recursive: true, force: true })
}

const feedbackRuntime = new Miniflare(
  convertV4MiniflareOptions({
    name: "fluiddb-feedback-runtime",
    modules: true,
    scriptPath: path.join(root, ".wrangler/feedback-test.js"),
    compatibilityDate: "2026-09-01",
    outboundService: async (request) => {
      assert.equal(request.url, "https://hivenet.app/v1/feedback")
      const event = await request.json()
      assert.equal(event.to, "fluiddb")
      assert.equal(event.consent.telemetry, false)
      assert.equal(event.context, undefined)
      assert.equal(event.thread.id, "runtime-thread")
      return Response.json(
        { id: "runtime-event", thread: event.thread.id, guidance: "Synthetic owner reply" },
        { status: 202 },
      )
    },
  }),
)
try {
  const response = await feedbackRuntime.dispatchFetch("http://feedback.test")
  assert.equal(response.status, 200)
  const receipt = await response.json()
  assert.equal(receipt.guidance, "Synthetic owner reply")
  assert.equal(receipt.idempotencyKey, "runtime-retry")
  console.log("Cloudflare feedback SDK passed with intercepted outbound delivery.")
} finally {
  await feedbackRuntime.dispose()
}
