import { describe, expect, test } from "bun:test"
import { Detect, Extract, Link, Pick, Prompt, Vector } from "@fluiddb/core"
import { Jev } from "../src/jev"
import { OpenAI } from "../src/openai"
import { Shim } from "../src/shim"

// Against the real providers, with made-up text. Runs only with FLUID_LIVE=1 and the keys set; costs about a cent.
const live = process.env.FLUID_LIVE === "1"
const openai = process.env.OPENAI_API_KEY ?? ""
const openrouter = process.env.OPENROUTER_API_KEY ?? ""

const window =
  "From a conversation with Mind on 2026-03-02:\n" +
  "[09:01] (Mind asked: What is on your mind today?) My sister Anna moved to Berlin last spring and I miss her.\n" +
  "[09:03] (Mind asked: How are you sleeping?) Badly, since I started the new job at the bank in January."
const memory = {
  s0: "They work at a bank since January.",
  s1: "Their sister Anna moved to Berlin last spring.",
  s2: "They go running on Sunday mornings.",
}
const message = "Mind asked: How is your family? Anna is still in Berlin, I told you, and I really miss her."
const statements = Object.entries(memory).map(([id, text]) => ({
  id,
  person: "p1",
  session: "s1",
  window: "w1",
  text,
  kind: "life" as const,
  at: "2026-03-02T09:01:00.000Z",
  group: `g${id}`,
}))

describe.skipIf(!live || !openai)("live: OpenAI", () => {
  const model = OpenAI.model({ apiKey: openai, model: "gpt-6-luna" })

  test("extracts lasting statements, and links a repeat to what it repeats", async () => {
    const found = await Extract.statements(model, {
      assistant: "Mind",
      role: "therapy assistant",
      date: "2026-03-02",
      text: window,
    })
    expect(found.length).toBeGreaterThanOrEqual(2)
    expect(found.some((x) => /Anna|Berlin|sister/.test(x.text))).toBe(true)
    const verdict = await Link.decide(model, "They have a sister, Anna, who lives in Berlin now.", [
      "They work at a bank.",
      "Their sister Anna moved to Berlin last spring.",
    ])
    expect(verdict).toEqual({ relation: "same", index: 1 })
  }, 120_000)

  test("ranks a list, and answers Jev's questions through the shim", async () => {
    const items = Object.entries(memory).map(([id, text]) => ({ id, text }))
    expect(
      (await Pick.model(model, { moment: message, items, top: 1, chars: 3000, role: "therapy assistant" }))[0],
    ).toBe("s1")
    const found = await Detect.detect(Shim.decider(model), { message, statements, threshold: 0.5 })
    expect(found?.statement.id).toBe("s1")
  }, 120_000)

  test("embeds: unit vectors, a paraphrase closer than something else", async () => {
    const embedder = OpenAI.embedder({ apiKey: openai, model: "text-embedding-3-small" })
    const [a, b, c] = await embedder.embed([
      memory.s1,
      "Anna, their sister, lives in Berlin since last spring.",
      memory.s2,
    ])
    expect(a!.length).toBe(1536)
    expect(Vector.dot(a!, a!)).toBeCloseTo(1, 4)
    expect(Vector.dot(a!, b!)).toBeGreaterThan(Vector.dot(a!, c!) + 0.2)
  }, 60_000)
})

describe.skipIf(!live || !openrouter)("live: Jev", () => {
  const jev = Jev.decider({ apiKey: openrouter })

  test("picks with closed questions, and detects a re-telling", async () => {
    const excerpts = { e0: memory.s0, e1: memory.s1, e2: memory.s2 }
    const questions = Object.fromEntries([0, 1, 2].map((n) => [`r${n}`, Prompt.pick.replace("{key}", `e${n}`)]))
    const p = await jev.yes({ moment: message, excerpts }, questions)
    expect(p.r1!).toBeGreaterThan(p.r2!)
    const found = await Detect.detect(jev, { message, statements, threshold: 0.5 })
    expect(found?.statement.id).toBe("s1")
  }, 60_000)
})
