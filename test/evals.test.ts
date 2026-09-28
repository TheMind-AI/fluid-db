import { afterEach, expect, test } from "bun:test"
import { mkdtemp, readFile, readdir, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import path from "node:path"
import { OpenAI, Jev } from "@fluiddb/providers"
import { providers, variant } from "../evals/providers"
import { Config, Dataset } from "../evals/schema"
import { replay, synthetic } from "../evals/replay"
import { transport } from "../evals/transport"

const dirs: string[] = []
const cache = async () => {
  const dir = await mkdtemp(path.join(tmpdir(), "fluid-eval-"))
  dirs.push(dir)
  return dir
}
afterEach(async () => {
  await Promise.all(dirs.splice(0).map((dir) => rm(dir, { recursive: true, force: true })))
})
const config = Config.parse({
  model: "fixture-model",
  embedding: "fixture-embedding",
  maxOutputTokens: 8,
  prices: { input: 1, output: 1, embedding: 1 },
})
const network = (handler: () => Promise<Response>) =>
  Object.assign(handler, { preconnect: fetch.preconnect }) as typeof fetch
const completion = () =>
  Response.json({
    choices: [{ message: { content: "cached answer" }, finish_reason: "stop" }],
    usage: { prompt_tokens: 10, completion_tokens: 2 },
  })
const request = (content: string) => ({
  method: "POST",
  body: JSON.stringify({
    model: config.model,
    messages: [{ role: "user", content }],
    max_completion_tokens: config.maxOutputTokens,
  }),
})
const endpoint = "https://api.openai.com/v1/chat/completions"

test("cache-only misses never send, even with a network adapter present", async () => {
  let calls = 0
  const http = transport({
    config,
    cache: await cache(),
    live: false,
    budget: 1,
    network: network(async () => {
      calls++
      return completion()
    }),
  })
  await expect(http.fetch(endpoint, request("a"))).rejects.toThrow("cache miss")
  expect(calls).toBe(0)
  expect(http.stats.requests).toBe(0)
})

test("production OpenAI adapter replays the full request cache without keys or network", async () => {
  const dir = await cache()
  let calls = 0
  const http = transport({
    config,
    cache: dir,
    live: true,
    budget: 1,
    network: network(async () => {
      calls++
      return completion()
    }),
  })
  const model = (fetch: typeof globalThis.fetch, apiKey: string) =>
    OpenAI.model({ model: config.model, tokens: config.maxOutputTokens, apiKey, fetch, attempts: 1, retries: 0 })
  expect(await model(http.fetch, "credential-must-not-be-cached").text("same prompt")).toBe("cached answer")
  const offline = transport({
    config,
    cache: dir,
    live: false,
    budget: 0,
    network: network(async () => {
      throw new Error("Network must not run")
    }),
  })
  expect(await model(offline.fetch, "").text("same prompt")).toBe("cached answer")
  await expect(model(offline.fetch, "").text("changed prompt")).rejects.toThrow("cache miss")
  expect(calls).toBe(1)
  expect(offline.stats.cached).toBe(1)
  expect(http.stats.estimatedUsd).toBeCloseTo(0.000012)
  const [file] = await readdir(dir)
  expect(await readFile(path.join(dir, file!), "utf8")).not.toContain("credential-must-not-be-cached")
})

test("parallel calls cannot reuse the dollar reservation", async () => {
  let calls = 0
  const http = transport({
    config,
    cache: await cache(),
    live: true,
    budget: 0.006,
    network: network(async () => {
      calls++
      return completion()
    }),
  })
  const results = await Promise.allSettled([http.fetch(endpoint, request("a")), http.fetch(endpoint, request("b"))])
  expect(results.filter((result) => result.status === "fulfilled")).toHaveLength(1)
  expect(calls).toBe(1)
  expect(http.stats.reservedUsd).toBeLessThanOrEqual(0.006)
})

test("failed requests keep their reservation and do not cache provider errors", async () => {
  const dir = await cache()
  let calls = 0
  const http = transport({
    config,
    cache: dir,
    live: true,
    budget: 0.006,
    network: network(async () => {
      calls++
      return new Response("sensitive provider error", { status: 500 })
    }),
  })
  await expect(http.fetch(endpoint, request("a"))).rejects.toThrow("HTTP 500")
  await expect(http.fetch(endpoint, request("b"))).rejects.toThrow("budget exhausted")
  expect(calls).toBe(1)
  expect(http.stats.unknownUsage).toBe(1)
  expect(await readdir(dir)).toEqual([])
})

test("stopping a failed run aborts in-flight calls and refuses further calls", async () => {
  const started = Promise.withResolvers<void>()
  const upstream = Object.assign(
    async (_input: Parameters<typeof fetch>[0], init?: RequestInit) => {
      started.resolve()
      return new Promise<Response>((_resolve, reject) =>
        init!.signal!.addEventListener("abort", () => reject(new Error("aborted")), { once: true }),
      )
    },
    { preconnect: fetch.preconnect },
  ) as typeof fetch
  const http = transport({ config, cache: await cache(), live: true, budget: 1, network: upstream })
  const pending = http.fetch(endpoint, request("a"))
  const settled = Promise.allSettled([pending])
  await started.promise
  await http.close()
  expect((await settled)[0]!.status).toBe("rejected")
  await expect(http.fetch(endpoint, request("b"))).rejects.toThrow("stopped")
  expect(http.stats.requests).toBe(1)
  expect(http.stats.unknownUsage).toBe(1)
})

test("live evaluation requires prices, a finite positive budget, and no cache-only override", async () => {
  const dir = await cache()
  expect(() => transport({ config: { ...config, prices: undefined }, cache: dir, live: true, budget: 1 })).toThrow(
    "prices",
  )
  for (const budget of [0, -1, NaN, Infinity])
    expect(() => transport({ config, cache: dir, live: true, budget })).toThrow("budget")
  const before = process.env.LAB_CACHE_ONLY
  try {
    process.env.LAB_CACHE_ONLY = "1"
    expect(() => transport({ config, cache: dir, live: true, budget: 1 })).toThrow("LAB_CACHE_ONLY")
  } finally {
    if (before === undefined) delete process.env.LAB_CACHE_ONLY
    else process.env.LAB_CACHE_ONLY = before
  }
})

test("an over-budget request and an unpriced model are rejected before transmission", async () => {
  let calls = 0
  const http = transport({
    config,
    cache: await cache(),
    live: true,
    budget: 0.00001,
    network: network(async () => {
      calls++
      return completion()
    }),
  })
  await expect(http.fetch(endpoint, request("a"))).rejects.toThrow("budget exhausted")
  await expect(http.fetch(endpoint, { method: "POST", body: JSON.stringify({ model: "unpriced" }) })).rejects.toThrow(
    "priced evaluation config",
  )
  expect(calls).toBe(0)
})

test("Jev and OpenAI share the same cap and cached Jev answers replay without network", async () => {
  const dir = await cache()
  const settings = Config.parse({ ...config, jev: { model: "fixture-jev", inputPrice: 1 } })
  let calls = 0
  const http = transport({
    config: settings,
    cache: dir,
    live: true,
    budget: 0.006,
    network: network(async () => {
      calls++
      return Response.json({ answers: { relevant: { type: "noul", noul: 0.9 } }, usage: { input_tokens: 20 } })
    }),
  })
  const decider = (fetch: typeof globalThis.fetch) =>
    Jev.decider({ apiKey: "never-cached", model: "fixture-jev", fetch, attempts: 1 })
  expect(await decider(http.fetch).yes({ moment: "fictional" }, { relevant: "Is this relevant?" })).toEqual({
    relevant: 0.9,
  })
  await expect(http.fetch(endpoint, request("same cap"))).rejects.toThrow("budget exhausted")
  const offline = transport({
    config: settings,
    cache: dir,
    live: false,
    budget: 0,
    network: network(async () => {
      throw new Error("Must not send")
    }),
  })
  expect(await decider(offline.fetch).yes({ moment: "fictional" }, { relevant: "Is this relevant?" })).toEqual({
    relevant: 0.9,
  })
  expect(calls).toBe(1)
  expect(http.stats.estimatedUsd).toBeCloseTo(0.00002)
  expect(offline.stats.estimatedUsd).toBe(0)
  expect(offline.stats.estimatedUncachedUsd).toBeCloseTo(0.00002)
})

test("every configured stage must be priced and third-party variants reject private datasets", async () => {
  const settings = Config.parse({ ...config, models: { pick: { model: "ranker" } } })
  expect(() => transport({ config: settings, cache: "unused", live: true, budget: 1 })).toThrow("prices")
  for (const name of ["jev", "routed"] as const)
    expect(() => variant(name, providers(), { synthetic: false, config })).toThrow("synthetic: true")
  expect(() => variant("jev", providers(), { synthetic: true })).toThrow("real provider")
})

const fixture = () => Bun.file(path.join(import.meta.dir, "../evals/fixtures/conversations.json")).json()

test("the grader fails when real retrieval is replaced with unrelated evidence", async () => {
  const data = Dataset.parse(await fixture())
  const providers = synthetic()
  // A deliberately broken store creation path: every extracted statement is unrelated to the source message.
  providers.model = { ...providers.model, object: async (_prompt, schema) => schema.parse({ items: [] }) }
  const result = await replay(data, providers)
  expect(result.passed).toBe(false)
  expect(result.metrics["statements@3"]?.passed).toBe(0)
  expect(result.metrics["windows@2"]?.passed).toBe(10)
  expect(result.repetition.falseNegative).toBe(5)
})

test("datasets reject future labels, missing expectations, duplicate probes and changed redeliveries", async () => {
  const original = await fixture()
  for (const corrupt of [
    (data: typeof original) => {
      data.steps.find((step: { type: string }) => step.type === "probe").expected.sources = ["future-turn"]
    },
    (data: typeof original) => {
      data.steps.find((step: { type: string }) => step.type === "probe").expected = {}
    },
    (data: typeof original) => {
      data.steps.push(data.steps.find((step: { type: string }) => step.type === "probe"))
    },
    (data: typeof original) => {
      data.steps.push({ ...data.steps[0], session: "changed-session" })
    },
    (data: typeof original) => {
      data.steps = data.steps.filter((step: { type: string }) => step.type !== "probe")
    },
  ]) {
    const data = structuredClone(original)
    corrupt(data)
    expect(Dataset.safeParse(data).success).toBe(false)
  }
})
