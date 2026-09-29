import { Api, Id } from "@fluiddb/schema"
import { Hono, type Context } from "hono"
import type { ContentfulStatusCode } from "hono/utils/http-status"
import { Host } from "./host"
import { Feedback } from "@fluiddb/feedback"
import { Skills } from "@fluiddb/skills"
import { agentDocs } from "./agent-docs"

export interface Options {
  // The bearer token callers present.
  token: string
  // The memory of one person: their Durable Object in production.
  people: (person: string) => Host.Service
  logger?: { event(name: string, data: Record<string, number | string | boolean>): void }
}

// Compares two strings in time that doesn't depend on where they differ.
async function same(a: string, b: string) {
  const encode = (x: string) => crypto.subtle.digest("SHA-256", new TextEncoder().encode(x))
  const [x, y] = await Promise.all([encode(a), encode(b)])
  const u = new Uint8Array(x)
  const v = new Uint8Array(y)
  return u.reduce((diff, byte, i) => diff | (byte ^ v[i]!), 0) === 0
}

const fail = (c: Context, error: Host.Failure) =>
  c.json({ error: { name: error.name, message: error.message } }, error.status as ContentfulStatusCode)

const reply = <T>(c: Context, result: Host.Result<T>, status: ContentfulStatusCode = 200) =>
  result.ok ? c.json(result.value as object, status) : fail(c, result.error)

async function body(c: Context): Promise<unknown> {
  try {
    return await c.req.json()
  } catch {
    throw new Host.RequestError("the body is not JSON")
  }
}

export function create(options: Options) {
  if (!options.token.trim()) throw new Error("A nonempty API token is required")
  const app = new Hono()
  const person = (c: Context) => Id.Info.parse(c.req.param("person"))

  app.get("/health", (c) => c.json({ ok: true }))
  // @ref LLP 0023.002#surfaces — public discovery contains no person data and does not proxy submissions
  app.get("/.well-known/agent-feedback.json", (c) => c.json(Feedback.descriptor))
  app.get("/llms.txt", (c) => c.text(agentDocs(new URL("/llms.txt", c.req.url).href)))
  app.get("/skills/:name/SKILL.md", (c) => {
    const file = Skills.read(c.req.param("name"))
    return file ? c.body(file.text, 200, { "content-type": "text/markdown; charset=utf-8" }) : c.notFound()
  })

  app.use("/v1/*", async (c, next) => {
    const header = c.req.header("authorization") ?? ""
    const token = header.startsWith("Bearer ") ? header.slice("Bearer ".length) : ""
    if (!(await same(token, options.token))) {
      return fail(c, { name: "Unauthorized", message: "a valid bearer token is required", status: 401 })
    }
    await next()
  })

  // Turns of a session; ingested in the background, or before answering with ?wait=true.
  app.post("/v1/people/:person/sessions/:session/turns", async (c) => {
    const id = person(c)
    const session = Id.Info.parse(c.req.param("session"))
    const { turns } = Api.Ingest.parse(await body(c))
    const memory = options.people(id)
    if (c.req.query("wait") === "true") return reply(c, await memory.ingest(id, session, turns))
    return reply(c, await memory.accept(id, session, turns), 202)
  })

  // What to remember at this turn, and the prompt section to give the assistant.
  app.post("/v1/people/:person/recall", async (c) => {
    const id = person(c)
    const ask = Api.Ask.parse(await body(c))
    return reply(c, await options.people(id).recall(id, ask))
  })

  app.get("/v1/people/:person/dossier", async (c) => {
    const id = person(c)
    const result = await options.people(id).dossier(id)
    if (result.ok && result.value === null) return fail(c, { name: "NotFound", message: "no dossier yet", status: 404 })
    return reply(c, result)
  })

  app.post("/v1/people/:person/remember", async (c) => {
    const id = person(c)
    return reply(c, await options.people(id).remember(id, Api.Remember.parse(await body(c))))
  })
  app.post("/v1/people/:person/remember/batch", async (c) => {
    const id = person(c)
    return reply(c, await options.people(id).rememberMany(id, Api.RememberMany.parse(await body(c))))
  })
  app.post("/v1/people/:person/inspect", async (c) => {
    const id = person(c)
    return reply(c, await options.people(id).inspect(id, Api.Inspect.parse(await body(c))))
  })
  app.post("/v1/people/:person/evidence", async (c) => {
    const id = person(c)
    return reply(c, await options.people(id).evidence(id, Api.Evidence.parse(await body(c))))
  })
  app.post("/v1/people/:person/source", async (c) => {
    const id = person(c)
    return reply(c, await options.people(id).source(id, Api.Source.parse(await body(c))))
  })
  app.post("/v1/people/:person/forget/preview", async (c) => {
    const id = person(c)
    return reply(c, await options.people(id).previewForget(id, Api.Forget.parse(await body(c))))
  })
  app.post("/v1/people/:person/forget", async (c) => {
    const id = person(c)
    const request = Api.Forget.parse(await body(c))
    return reply(c, await options.people(id).forget(id, request))
  })

  app.get("/v1/people/:person/status", async (c) => {
    const id = person(c)
    return reply(c, await options.people(id).status(id))
  })

  app.post("/v1/people/:person/retry", async (c) => {
    const id = person(c)
    return reply(c, await options.people(id).retry(id), 202)
  })

  // Everything the person said, and everything made from it.
  app.delete("/v1/people/:person", async (c) => {
    const id = person(c)
    const result = await options.people(id).erase(id)
    return result.ok ? c.json({ erased: true }) : fail(c, result.error)
  })

  app.notFound((c) => fail(c, { name: "NotFound", message: "no such route", status: 404 }))
  app.onError((error, c) => {
    const out = Host.failure(error)
    if (out.status >= 500) options.logger?.event("request.failed", { error: error.name })
    return fail(c, out)
  })
  return app
}

export * as App from "./app"
