import { describe, expect, test } from "bun:test"
import { ProviderError } from "@fluiddb/core"
import { Http } from "../src/http"
import { fake, instant, json } from "./fake"

const post = (fetch: typeof globalThis.fetch, options: Http.Options = {}) =>
  Http.post(
    "test",
    "https://x.test/api",
    { headers: { authorization: "Bearer k" }, body: { a: 1 } },
    { fetch, ...instant, ...options },
  )

describe("Http.post", () => {
  test("sends JSON and returns the parsed answer", async () => {
    const { fetch, requests } = fake(() => json({ ok: true }))
    expect(await post(fetch)).toEqual({ ok: true })
    expect(requests[0]?.headers).toEqual({ "content-type": "application/json", authorization: "Bearer k" })
    expect(requests[0]?.body).toEqual({ a: 1 })
  })

  test("retries rate limits and server errors, waiting as asked", async () => {
    const waits: number[] = []
    const { fetch, requests } = fake((_, n) =>
      n === 1 ? json({}, 429, { "retry-after": "2" }) : n === 2 ? json({}, 503) : json({ ok: true }),
    )
    expect(await post(fetch, { sleep: async (ms) => void waits.push(ms) })).toEqual({ ok: true })
    expect(requests.length).toBe(3)
    expect(waits).toEqual([2000, 2000])
  })

  test("retries a dropped connection", async () => {
    const state = { n: 0 }
    const fetch = (async () => {
      if (++state.n === 1) throw new TypeError("connection reset")
      return json({ ok: true })
    }) as unknown as typeof globalThis.fetch
    expect(await post(fetch)).toEqual({ ok: true })
  })

  test("gives up after the attempts, with the status and the start of the answer", async () => {
    const { fetch, requests } = fake(() => json({ error: "busy".repeat(200) }, 500))
    const error = await post(fetch, { attempts: 3 }).catch((e: ProviderError) => e)
    expect(error).toBeInstanceOf(ProviderError)
    expect((error as ProviderError).status).toBe(500)
    expect((error as ProviderError).message.length).toBeLessThan(400)
    expect(requests.length).toBe(3)
  })

  test("does not retry a bad request", async () => {
    const { fetch, requests } = fake(() => json({ error: "bad" }, 400))
    await expect(post(fetch)).rejects.toThrow("failed with 400")
    expect(requests.length).toBe(1)
  })

  for (const status of [307, 308])
    test(`rejects ${status} without sending memory to the redirect target or retrying`, async () => {
      let gatewayCalls = 0,
        targetCalls = 0
      const target = Bun.serve({
        hostname: "127.0.0.1",
        port: 0,
        fetch: () => {
          targetCalls++
          return json({ ok: true })
        },
      })
      const gateway = Bun.serve({
        hostname: "127.0.0.1",
        port: 0,
        fetch: () => {
          gatewayCalls++
          return new Response("synthetic private provider detail", { status, headers: { location: target.url.href } })
        },
      })
      try {
        const error = await Http.post(
          "test",
          gateway.url.href,
          { headers: { authorization: "Bearer synthetic-key" }, body: { memory: "synthetic private memory" } },
          { attempts: 3, ...instant },
        ).catch((error: unknown) => error)
        expect(error).toBeInstanceOf(ProviderError)
        expect((error as ProviderError).status).toBe(status)
        expect((error as ProviderError).message).toBe(`test failed with ${status}: redirects are not permitted`)
        expect(gatewayCalls).toBe(1)
        expect(targetCalls).toBe(0)
      } finally {
        await gateway.stop(true)
        await target.stop(true)
      }
    })

  test("stops when the caller aborts", async () => {
    const controller = new AbortController()
    controller.abort()
    const fetch = (async (_: unknown, init?: RequestInit) => {
      init?.signal?.throwIfAborted()
      return json({})
    }) as unknown as typeof globalThis.fetch
    const error = await Http.post(
      "test",
      "https://x.test",
      { headers: {}, body: {}, signal: controller.signal },
      { fetch },
    ).catch((e) => e)
    expect(error).not.toBeInstanceOf(ProviderError)
  })

  test("waits: retry-after in seconds or as a date, else doubling to 30 s", () => {
    expect(Http.wait(1, "3")).toBe(3000)
    expect(Http.wait(1, new Date(Date.now() + 5000).toUTCString())).toBeGreaterThan(3000)
    expect([1, 2, 3, 6, 9].map((n) => Http.wait(n, null))).toEqual([1000, 2000, 4000, 30_000, 30_000])
  })
})
