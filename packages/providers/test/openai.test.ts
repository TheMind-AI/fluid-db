import { describe, expect, test } from "bun:test"
import { Extract, Link, Memory, OutputError, Vector } from "@fluiddb/core"
import { Local } from "@fluiddb/core/local"
import { Testing } from "@fluiddb/core/testing"
import { z } from "zod"
import { OpenAI } from "../src/openai"
import { Schema } from "../src/schema"
import { completion, fake, instant, json } from "./fake"

describe("Schema.strict", () => {
  test("the lab's merge schema: no $schema, no safe-integer bounds", () => {
    expect(Schema.strict(Link.Output)).toEqual({
      type: "object",
      properties: {
        relation: { type: "string", enum: ["same", "change", "new"] },
        n: { type: "integer", description: "the number of the earlier statement it matches or changes; 0 if new" },
      },
      required: ["relation", "n"],
      additionalProperties: false,
    })
  })

  test("every object strict, every field required", () => {
    const schema = Schema.strict(Extract.Output) as any
    expect(schema.additionalProperties).toBe(false)
    expect(schema.properties.items.items.additionalProperties).toBe(false)
    expect(schema.properties.items.items.required).toEqual(["text", "kind"])
  })
})

describe("OpenAI.model", () => {
  const options = { apiKey: "sk-test", model: "gpt-6-luna", ...instant }

  test("asks for strict JSON with low reasoning effort, and parses it", async () => {
    const usage: unknown[] = []
    const { fetch, requests } = fake(() => completion(JSON.stringify({ relation: "same", n: 2 })))
    const model = OpenAI.model({ ...options, fetch, usage: (x) => void usage.push(x) })
    expect(await model.object("Is it the same?", Link.Output)).toEqual({ relation: "same", n: 2 })
    expect(requests[0]?.url).toBe("https://api.openai.com/v1/chat/completions")
    expect(requests[0]?.headers.authorization).toBe("Bearer sk-test")
    expect(requests[0]?.body).toEqual({
      model: "gpt-6-luna",
      store: false,
      messages: [{ role: "user", content: "Is it the same?" }],
      max_completion_tokens: 16_000,
      reasoning_effort: "low",
      response_format: {
        type: "json_schema",
        json_schema: { name: "output", schema: Schema.strict(Link.Output), strict: true },
      },
    })
    expect(usage).toEqual([{ model: "gpt-6-luna", input: 100, output: 20 }])
    expect(model.processors).toEqual(["openai"])
  })

  test("text, at another compatible API, without reasoning effort", async () => {
    const { fetch, requests } = fake(() => completion("  a dossier  "))
    const model = OpenAI.model({
      ...options,
      fetch,
      url: "https://gateway.test/v1",
      effort: null,
      processors: ["gateway"],
    })
    expect(await model.text("Write it.")).toBe("  a dossier  ")
    expect(requests[0]?.url).toBe("https://gateway.test/v1/chat/completions")
    expect(requests[0]?.body.reasoning_effort).toBe(undefined)
    expect(requests[0]?.body.response_format).toBe(undefined)
    expect(model.processors).toEqual(["gateway"])
  })

  test("asks again when the output doesn't match, then fails", async () => {
    const { fetch, requests } = fake((_, n) =>
      completion(n < 3 ? "not json" : JSON.stringify({ relation: "new", n: 0 })),
    )
    expect(await OpenAI.model({ ...options, fetch }).object("q", Link.Output)).toEqual({ relation: "new", n: 0 })
    expect(requests.length).toBe(3)
    const bad = fake(() => completion(JSON.stringify({ relation: "maybe", n: 0 })))
    await expect(OpenAI.model({ ...options, fetch: bad.fetch, retries: 1 }).object("q", Link.Output)).rejects.toThrow(
      OutputError,
    )
    expect(bad.requests.length).toBe(2)
  })

  test("a refusal or a cut-off answer is an error", async () => {
    const refused = fake(() =>
      json({ choices: [{ message: { content: null, refusal: "no" }, finish_reason: "stop" }] }),
    )
    await expect(OpenAI.model({ ...options, fetch: refused.fetch }).text("q")).rejects.toThrow("refused")
    const cut = fake(() => completion("partial", { finish_reason: "length" }))
    await expect(OpenAI.model({ ...options, fetch: cut.fetch }).text("q")).rejects.toThrow("cut off")
  })
})

describe("OpenAI.embedder", () => {
  test("batches, keeps the order by index, and returns unit vectors", async () => {
    const { fetch, requests } = fake((body) =>
      json({
        data: body.input.map((text: string, index: number) => ({ index, embedding: [text.length, 1, 0] })).toReversed(),
        usage: { total_tokens: 7 },
      }),
    )
    const embedder = OpenAI.embedder({
      apiKey: "sk-test",
      model: "text-embedding-3-small",
      fetch,
      batch: 2,
      ...instant,
    })
    const out = await embedder.embed(["a", "bbb", "", "x".repeat(30_000)])
    expect(requests.map((x) => x.body.input.length)).toEqual([2, 2, 2, 1])
    expect(requests[1]?.body.input[0]).toBe(" ")
    expect(
      requests
        .flatMap((x) => x.body.input)
        .slice(3)
        .join(""),
    ).toBe("x".repeat(30_000))
    expect(out.map((x) => Math.round(Vector.dot(x, x) * 1000))).toEqual([1000, 1000, 1000, 1000])
    expect(out[1]![0]! / out[1]![1]!).toBeCloseTo(3)
  })

  test("keeps Unicode intact and weights all chunks in one vector per original input", async () => {
    const text = "a".repeat(7_999) + "😀尾"
    const { fetch, requests } = fake((body) =>
      json({
        data: body.input.map((part: string, index: number) => ({
          index,
          embedding: part.startsWith("a") ? [1, 0] : [0, 1],
        })),
      }),
    )
    const [vector] = await OpenAI.embedder({ apiKey: "k", model: "m", fetch, batch: 1, ...instant }).embed([text])
    const sent = requests.flatMap((request) => request.body.input)
    expect(sent).toEqual(["a".repeat(7_999), "😀尾"])
    expect(sent.join("")).toBe(text)
    expect(vector![0]! / vector![1]!).toBeCloseTo(7_999 / 7, 2)
    expect(Vector.dot(vector!, vector!)).toBeCloseTo(1)
  })

  test("preserves a byte-order mark at the start of any chunk instead of creating an empty input", async () => {
    const { fetch, requests } = fake((body) =>
      json({
        data: body.input.map((_: string, index: number) => ({ index, embedding: [1, 0] })),
      }),
    )
    const text = "x".repeat(8_000) + "\uFEFF"
    await OpenAI.embedder({ apiKey: "k", model: "m", fetch, ...instant }).embed([text])
    const parts = requests.flatMap((request) => request.body.input)
    expect(parts).toEqual(["x".repeat(8_000), "\uFEFF"])
    expect(parts.join("")).toBe(text)
  })

  test("bounds aggregate requests as well as each input even with an oversized batch setting", async () => {
    const { fetch, requests } = fake((body) =>
      json({
        data: body.input.map((_: string, index: number) => ({ index, embedding: [1, 0] })),
      }),
    )
    const texts = Array.from({ length: 80 }, () => "界".repeat(2_666))
    const vectors = await OpenAI.embedder({ apiKey: "k", model: "m", fetch, batch: 100, ...instant }).embed(texts)
    expect(vectors).toHaveLength(texts.length)
    expect(requests).toHaveLength(3)
    for (const { body } of requests) {
      const bytes = body.input.map((text: string) => new TextEncoder().encode(text).length)
      expect(Math.max(...bytes)).toBeLessThanOrEqual(8_000)
      expect(bytes.reduce((a: number, b: number) => a + b, 0)).toBeLessThan(300_000)
    }
  })

  test("ingests and retries a long multilingual window within the provider contract", async () => {
    const text = "今天工作讓我很累，我想聊聊和同事相處的問題。".repeat(400)
    const { fetch, requests } = fake((body) => {
      if (body.input.some((value: string) => new TextEncoder().encode(value).length > 8_192))
        return json({ error: { message: "input exceeds the embedding token bound" } }, 400)
      return json({ data: body.input.map((_: string, index: number) => ({ index, embedding: [1, 0] })) })
    })
    const store = Local.store()
    const memory = Memory.create({
      store,
      model: Testing.model(() => ({ items: [] })),
      decider: Testing.decider(),
      embedder: OpenAI.embedder({ apiKey: "k", model: "text-embedding-3-small", fetch, ...instant }),
    })
    const input = {
      person: "synthetic",
      session: "s1",
      turns: [{ id: "t1", role: "person" as const, at: "2026-09-01T00:00:00.000Z", text }],
    }
    expect((await memory.ingest(input)).windows).toBe(1)
    expect((await memory.ingest(input)).turns).toBe(0)
    expect((await store.log.session(input.person, input.session))[0]?.text).toBe(text)
    const [window] = await store.windows.all(input.person)
    expect(requests.flatMap((request) => request.body.input).join("")).toBe(window!.text)
    expect(await store.turns.seen(input.person, ["t1"])).toEqual(["t1"])
  })

  test("a response without one embedding per text is an error", async () => {
    const { fetch } = fake(() => json({ data: [] }))
    const embedder = OpenAI.embedder({ apiKey: "k", model: "m", fetch, ...instant })
    await expect(embedder.embed(["a"])).rejects.toThrow(OutputError)
  })

  test("rejects repeated indices, mixed dimensions and zero vectors", async () => {
    for (const data of [
      [
        { index: 0, embedding: [1, 0] },
        { index: 0, embedding: [1, 0] },
      ],
      [
        { index: 0, embedding: [1, 0] },
        { index: 1, embedding: [1] },
      ],
      [
        { index: 0, embedding: [0, 0] },
        { index: 1, embedding: [1, 0] },
      ],
    ]) {
      const { fetch } = fake(() => json({ data }))
      await expect(OpenAI.embedder({ apiKey: "k", model: "m", fetch, ...instant }).embed(["a", "b"])).rejects.toThrow(
        OutputError,
      )
    }
  })
})

describe("OpenAI.model with zod", () => {
  test("describes fields to the model", () => {
    const schema = Schema.strict(z.strictObject({ p: z.number().describe("a probability") })) as any
    expect(schema.properties.p.description).toBe("a probability")
  })
})
