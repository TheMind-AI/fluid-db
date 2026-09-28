import { describe, expect, test } from "bun:test"
import { Testing } from "@fluiddb/core/testing"
import { ProviderError } from "@fluiddb/core"
import { App } from "../src/app"
import { Host } from "../src/host"
import { day, host, outage, session, turn } from "./fixture"

const token = "test-token-0123456789"
const auth = { authorization: `Bearer ${token}`, "content-type": "application/json" }

function api(model = Testing.model(), embedder = Testing.embedder()) {
  const { host: h, fire } = host({ model, embedder })
  const app = App.create({ token, people: () => Host.serve(h) })
  const call = async (method: string, path: string, body?: unknown, headers: Record<string, string> = auth) => {
    const response = await app.request(path, {
      method,
      headers,
      ...(body === undefined ? {} : { body: typeof body === "string" ? body : JSON.stringify(body) }),
    })
    return { status: response.status, body: (await response.json()) as any }
  }
  return { call, fire }
}

describe("the HTTP API", () => {
  test("health needs no token; person APIs do", async () => {
    const { call } = api()
    expect((await call("GET", "/health", undefined, {})).status).toBe(200)
    expect((await call("GET", "/v1/people/p1/status", undefined, {})).status).toBe(401)
    const wrong = await call("GET", "/v1/people/p1/status", undefined, { authorization: "Bearer nope" })
    expect(wrong).toEqual({
      status: 401,
      body: { error: { name: "Unauthorized", message: "a valid bearer token is required" } },
    })
  })

  test("feedback discovery and skills are public static content, with no host or provider calls", async () => {
    let called = false
    const app = App.create({
      token,
      people: () => {
        called = true
        throw new Error("must not read memory")
      },
    })
    const descriptor = await app.request("/.well-known/agent-feedback.json")
    expect(await descriptor.json()).toEqual({
      v: 1,
      name: "FluidDB",
      slug: "fluiddb",
      endpoint: "https://hivenet.app/v1/feedback",
    })
    const docs = await app.request("https://memory.example/llms.txt")
    expect(await docs.text()).toContain("--subject 'https://memory.example/llms.txt'")
    const skill = await app.request("/skills/fluiddb-memory/SKILL.md")
    expect(skill.headers.get("content-type")).toContain("text/markdown")
    expect(await skill.text()).toContain("fluiddb_recall")
    expect((await app.request("/skills/not-a-skill/SKILL.md")).status).toBe(404)
    expect(called).toBe(false)
  })

  test("a session in, memory out, then forgotten", async () => {
    const { call } = api()
    const ingested = await call("POST", "/v1/people/p1/sessions/s1/turns?wait=true", { turns: session })
    expect(ingested).toEqual({ status: 200, body: { turns: 2, windows: 1, statements: 2, linked: 0 } })

    const conversation = [turn("p9", "person", "My sister Anna moved to Berlin last spring.", day("2026-03-16"))]
    const answer = await call("POST", "/v1/people/p1/recall", { conversation, name: "Sam" })
    expect(answer.status).toBe(200)
    expect(answer.body.recall.retold.statement.text).toContain("Berlin")
    expect(answer.body.prompt).toContain("## They are repeating something they told you before")

    const dossier = await call("GET", "/v1/people/p1/dossier")
    expect(dossier.status).toBe(200)
    expect(dossier.body.updates).toBe(1)

    expect((await call("POST", "/v1/people/p1/forget", { session: "s1" })).body).toEqual({
      windows: 1,
      statements: 2,
      groups: 2,
    })
    expect((await call("GET", "/v1/people/p1/dossier")).status).toBe(404)
    expect(await call("DELETE", "/v1/people/p1")).toEqual({ status: 200, body: { erased: true } })
  })

  test("turns are taken in, and ingested by the alarm", async () => {
    const { call, fire } = api()
    expect(await call("POST", "/v1/people/p1/sessions/s1/turns", { turns: session })).toEqual({
      status: 202,
      body: { accepted: 4, pending: 4 },
    })
    await fire()
    expect((await call("GET", "/v1/people/p1/status")).body).toMatchObject({ statements: 2, inbox: { pending: 0 } })
  })

  test("bad requests say what is wrong, never echoing what was sent", async () => {
    const { call } = api()
    const secret = "my-therapist-secret"
    const bad = await call("POST", "/v1/people/p1/sessions/s1/turns", {
      turns: [{ id: "t", role: "bot", text: secret, at: "x" }],
    })
    expect(bad.status).toBe(400)
    expect(bad.body.error.name).toBe("InvalidRequest")
    expect(JSON.stringify(bad.body)).not.toContain(secret)
    expect((await call("POST", "/v1/people/p1/recall", "{not json")).body.error).toEqual({
      name: "InvalidRequest",
      message: "the body is not JSON",
    })
    expect((await call("GET", `/v1/people/${"x".repeat(201)}/status`)).status).toBe(400)
    expect((await call("POST", "/v1/people/p1/forget", {})).status).toBe(400)
    expect((await call("GET", "/v1/nothing")).status).toBe(404)
  })

  test("a provider failing is a 502 with its kind; the turns wait in the inbox", async () => {
    const { model, embedder, state } = outage()
    const { call } = api(model, embedder)
    state.down = true
    const out = await call("POST", "/v1/people/p1/sessions/s1/turns?wait=true", { turns: session })
    expect(out.status).toBe(502)
    expect(out.body.error.name).toBe("ProviderError")
    expect((await call("GET", "/v1/people/p1/status")).body.inbox).toEqual({ pending: 4, failed: 0 })
    const conversation = [turn("p9", "person", "Hello", day("2026-03-16"))]
    expect((await call("POST", "/v1/people/p1/recall", { conversation })).status).toBe(502)
  })

  test("provider error bodies that echo private text never reach the HTTP response", async () => {
    const secret = "a private conversation echoed by an upstream error"
    const { call } = api(
      Testing.model(() => {
        throw new ProviderError("test", 500, secret)
      }),
    )
    const out = await call("POST", "/v1/people/p1/sessions/s1/turns?wait=true", { turns: session })
    expect(out.status).toBe(502)
    expect(JSON.stringify(out.body)).not.toContain(secret)
  })
})
