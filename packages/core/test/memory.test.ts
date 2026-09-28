import { describe, expect, test } from "bun:test"
import type { Turn } from "@fluiddb/schema"
import { Local } from "../src/local"
import { Memory } from "../src/memory"
import type { Decider, LanguageModel, Store } from "../src/port"
import { Render } from "../src/render"
import { Testing } from "../src/testing"
import { day, turn } from "./fixture"

// The whole loop on the in-memory store and the deterministic fakes: words stand in for meaning.
function setup(input: { model?: LanguageModel; decider?: Decider; options?: Memory.Deps["options"] } = {}) {
  const store = Local.store()
  const events: { name: string; data: Record<string, unknown> }[] = []
  const memory = Memory.create({
    model: input.model ?? Testing.model(),
    embedder: Testing.embedder(),
    decider: input.decider ?? Testing.decider(),
    store,
    clock: Testing.clock(),
    ids: Testing.ids(),
    logger: { event: (name, data) => events.push({ name, data }) },
    options: { assistant: "Mind", ...input.options },
  })
  return { store, memory, events }
}

const first = [
  turn("a1", "assistant", "Hi. What is on your mind today?", day("2026-03-02", "09:00")),
  turn("p1", "person", "My sister Anna moved to Berlin last spring.", day("2026-03-02", "09:01")),
  turn("a2", "assistant", "That is a big change. How are you sleeping?", day("2026-03-02", "09:02")),
  turn("p2", "person", "Badly, since the new job at the bank started.", day("2026-03-02", "09:03")),
]
const second = [
  turn("a3", "assistant", "Welcome back. How was your week?", day("2026-03-09", "18:00")),
  turn("p3", "person", "Anna, my sister, moved to Berlin last spring, so I feel alone.", day("2026-03-09", "18:01")),
]
const ask = (text: string, date = "2026-03-16") => [
  turn("a9", "assistant", "Good to see you again.", day(date, "10:00")),
  turn("p9", "person", text, day(date, "10:01")),
]

async function said(store: Store, person = "p1") {
  const windows = await store.windows.all(person)
  return store.statements.windows(
    person,
    windows.map((x) => x.id),
  )
}

describe("ingest", () => {
  test("writes windows, statements and vectors; turns already seen are skipped", async () => {
    const { store, memory } = setup()
    expect(await memory.ingest({ person: "p1", session: "s1", turns: first })).toEqual({
      turns: 2,
      windows: 1,
      statements: 2,
      linked: 0,
    })
    const [window] = await store.windows.all("p1")
    expect(window?.text).toBe(
      "From a conversation with Mind on 2026-03-02:\n" +
        "[09:01] (Mind asked: What is on your mind today?) My sister Anna moved to Berlin last spring.\n" +
        "[09:03] (Mind asked: How are you sleeping?) Badly, since the new job at the bank started.",
    )
    expect(window?.turns).toEqual(["p1", "p2"])
    expect((await said(store)).map((x) => x.text)).toEqual([
      "They said: My sister Anna moved to Berlin last spring.",
      "They said: Badly, since the new job at the bank started.",
    ])
    expect(await memory.ingest({ person: "p1", session: "s1", turns: first })).toEqual({
      turns: 0,
      windows: 0,
      statements: 0,
      linked: 0,
    })
  })

  test("a repeat in a later session links to the earlier statement, keeping both wordings", async () => {
    const { store, memory } = setup()
    await memory.ingest({ person: "p1", session: "s1", turns: first })
    expect((await memory.ingest({ person: "p1", session: "s2", turns: second })).linked).toBe(1)
    const berlin = (await said(store)).filter((x) => x.text.includes("Berlin"))
    expect(berlin.map((x) => x.session)).toEqual(["s1", "s2"])
    expect(new Set(berlin.map((x) => x.group)).size).toBe(1)
    expect((await store.groups.get("p1", [berlin[0]!.group]))[0]).toMatchObject({
      count: 2,
      first: day("2026-03-02", "09:01"),
      last: day("2026-03-09", "18:01"),
    })
  })

  test("a repeat within one session links too", async () => {
    const { store, memory } = setup({ options: { size: 1 } })
    await memory.ingest({ person: "p1", session: "s1", turns: [...first, ...second] })
    const berlin = (await said(store)).filter((x) => x.text.includes("Berlin"))
    expect(berlin.length).toBe(2)
    expect(berlin[0]!.group).toBe(berlin[1]!.group)
  })

  test("originals survive a failed model call, while derived rows remain atomic", async () => {
    const failing = Testing.model((prompt) => {
      if (prompt.includes("new job at the bank") && prompt.includes("good therapist")) throw new Error("model down")
      return undefined
    })
    const { store, memory } = setup({ model: failing, options: { size: 1 } })
    await expect(memory.ingest({ person: "p1", session: "s1", turns: first })).rejects.toThrow("model down")
    expect(await store.windows.all("p1")).toEqual([])
    expect(await store.turns.seen("p1", ["p1", "p2"])).toEqual([])
    expect(await store.log.session("p1", "s1")).toEqual(first)
  })

  test("keeps long originals and recovers the assistant question across deliveries", async () => {
    const { store, memory } = setup({
      model: Testing.model((prompt) =>
        prompt.includes("What would a good therapist")
          ? { items: [{ text: "A lasting fact from a long message.", kind: "life" }] }
          : undefined,
      ),
    })
    await memory.ingest({ person: "p1", session: "s1", turns: first.slice(0, 1) })
    const long = { ...first[1]!, text: "x".repeat(5000) + " the original ending" }
    await memory.ingest({ person: "p1", session: "s1", turns: [long] })
    expect((await store.log.session("p1", "s1"))[1]?.text).toBe(long.text)
    expect((await store.windows.all("p1"))[0]?.text).toContain("What is on your mind today?")
  })

  test("concurrent library ingests of the same turns write only once", async () => {
    const { memory } = setup()
    const results = await Promise.all([1, 2].map(() => memory.ingest({ person: "p1", session: "s1", turns: first })))
    expect(results.reduce((n, x) => n + x.turns, 0)).toBe(2)
  })

  test("rejects malformed input before touching anything", async () => {
    const { store, memory } = setup()
    const bad = [{ id: "x", role: "bot", text: "hi", at: "yesterday" }] as unknown as Turn.Info[]
    await expect(memory.ingest({ person: "p1", session: "s1", turns: bad })).rejects.toThrow()
    await expect(memory.ingest({ person: "", session: "s1", turns: first })).rejects.toThrow()
    await expect(memory.forget({ person: "p1" })).rejects.toThrow()
    expect(await store.windows.all("p1")).toEqual([])
  })
})

describe("fold", () => {
  test("the dossier takes in the windows it doesn't include yet", async () => {
    const { store, memory } = setup()
    await memory.ingest({ person: "p1", session: "s1", turns: first })
    expect(await memory.dossier("p1")).toBe(undefined)
    const dossier = await memory.fold("p1")
    expect(dossier?.updates).toBe(1)
    expect(dossier?.text).toContain("My sister Anna moved to Berlin")
    expect(await store.windows.pending("p1")).toEqual([])
    expect((await memory.fold("p1"))?.updates).toBe(1)
    await memory.ingest({ person: "p1", session: "s2", turns: second })
    const next = await memory.fold("p1")
    expect(next?.updates).toBe(2)
    expect(next?.text).toContain("so I feel alone")
    expect(next?.text).toContain("My sister Anna moved to Berlin")
  })

  test("in batches, each saved, so a failure keeps what was done", async () => {
    const calls = { dossier: 0 }
    const flaky = Testing.model((prompt) => {
      if (prompt.includes("CURRENT DOSSIER") && ++calls.dossier === 3) throw new Error("model down")
      return undefined
    })
    const { store, memory } = setup({ model: flaky, options: { size: 1, batch: 2 } })
    const turns = Array.from({ length: 5 }, (_, i) =>
      turn(`p${i}`, "person", `Message number ${i} about topic ${i}.`, day("2026-03-02", `09:0${i}`)),
    )
    await memory.ingest({ person: "p1", session: "s1", turns })
    await expect(memory.fold("p1")).rejects.toThrow("model down")
    expect((await memory.dossier("p1"))?.updates).toBe(2)
    expect((await store.windows.pending("p1")).length).toBe(1)
    expect((await memory.fold("p1"))?.updates).toBe(3)
    expect(await store.windows.pending("p1")).toEqual([])
  })
})

describe("recall", () => {
  test("picks what is relevant, notices a re-telling, and renders it", async () => {
    const { memory } = setup()
    await memory.ingest({ person: "p1", session: "s1", turns: first })
    await memory.ingest({ person: "p1", session: "s2", turns: second })
    await memory.fold("p1")
    const recall = await memory.recall({
      person: "p1",
      conversation: ask("My sister Anna moved to Berlin last spring."),
    })
    expect(recall.dossier).toContain("My sister Anna moved to Berlin")
    expect(recall.statements[0]?.text).toContain(
      "(life; the same fact recorded 2 times, first 2026-03-02, last 2026-03-09)",
    )
    expect(recall.windows.map((x) => x.at)).toEqual([day("2026-03-02", "09:01"), day("2026-03-09", "18:01")])
    expect(recall.retold?.group.count).toBe(2)
    expect(recall.retold?.statement.text).toContain("Berlin")
    const prompt = Render.render(recall, { name: "Sam" })
    expect(prompt).toContain("## What you know about Sam")
    expect(prompt).toContain("## They are repeating something they told you before")
  })

  test("the model can pick instead of the decider", async () => {
    const { memory } = setup({ options: { pick: "model", statements: { keep: 1 }, windows: { keep: 1 } } })
    await memory.ingest({ person: "p1", session: "s1", turns: first })
    const recall = await memory.recall({ person: "p1", conversation: ask("The job at the bank keeps me awake.") })
    expect(recall.statements.map((x) => x.text)).toEqual([
      "They said: Badly, since the new job at the bank started. (life; said 2026-03-02)",
    ])
    expect(recall.windows.length).toBe(1)
  })

  test("with no message from the person yet, the dossier alone", async () => {
    const { memory } = setup()
    await memory.ingest({ person: "p1", session: "s1", turns: first })
    await memory.fold("p1")
    const recall = await memory.recall({
      person: "p1",
      conversation: [turn("a9", "assistant", "Hi!", day("2026-04-01"))],
    })
    expect(recall.statements).toEqual([])
    expect(recall.dossier).toContain("Anna")
  })

  test("never crosses people", async () => {
    const { memory } = setup()
    await memory.ingest({ person: "p1", session: "s1", turns: first })
    const recall = await memory.recall({
      person: "p2",
      conversation: ask("My sister Anna moved to Berlin last spring."),
    })
    expect(recall).toEqual({ person: "p2", statements: [], windows: [] })
  })

  test("logs counts and timings, never what was said", async () => {
    const { memory, events } = setup()
    await memory.ingest({ person: "p1", session: "s1", turns: first })
    await memory.recall({ person: "p1", conversation: ask("My sister Anna moved to Berlin last spring.") })
    expect(events.map((x) => x.name)).toEqual(["ingest", "recall"])
    for (const event of events) {
      for (const value of Object.values(event.data)) expect(["number", "boolean"]).toContain(typeof value)
    }
  })
})

describe("changes", () => {
  // The fake model says a statement about Lisbon changes the one about Prague.
  const moving = Testing.model((prompt) =>
    prompt.startsWith("A new statement") && /NEW: .*Lisbon/.test(prompt) ? { relation: "change", n: 1 } : undefined,
  )
  const prague = [turn("p1", "person", "I live in Prague with my partner Tom.", day("2026-03-02", "09:01"))]
  const lisbon = [turn("p2", "person", "I live in Lisbon with my partner Tom now.", day("2026-03-09", "09:01"))]

  async function moved() {
    const out = setup({ model: moving })
    await out.memory.ingest({ person: "p1", session: "s1", turns: prague })
    await out.memory.ingest({ person: "p1", session: "s2", turns: lisbon })
    return out
  }

  test("a change ends the old fact and shows what it was", async () => {
    const { memory } = await moved()
    const recall = await memory.recall({ person: "p1", conversation: ask("Tom and I love living in Lisbon.") })
    const texts = recall.statements.map((x) => x.text)
    expect(texts).toContain(
      "They said: I live in Lisbon with my partner Tom now. (life; said 2026-03-09; earlier: They said: I live in " +
        "Prague with my partner Tom. (until 2026-03-09))",
    )
    expect(texts).toContain(
      "They said: I live in Prague with my partner Tom. (life; said 2026-03-02; changed on 2026-03-09)",
    )
  })

  test("forgetting the change: the old fact no longer ended", async () => {
    const { store, memory } = await moved()
    expect(await memory.forget({ person: "p1", session: "s2" })).toEqual({ windows: 1, statements: 1, groups: 1 })
    const [left] = await said(store)
    const [group] = await store.groups.get("p1", [left!.group])
    expect(group?.until).toBe(undefined)
    expect(group?.count).toBe(1)
  })

  test("forgetting the old fact: the change no longer replaces it", async () => {
    const { store, memory } = await moved()
    await memory.forget({ person: "p1", session: "s1" })
    const [left] = await said(store)
    expect(left?.text).toContain("Lisbon")
    expect((await store.groups.get("p1", [left!.group]))[0]?.replaces).toEqual([])
  })
})

describe("forget", () => {
  test("a session: its rows and vectors go, counts fall back, the dossier is dropped and rewritten", async () => {
    const { store, memory } = setup()
    await memory.ingest({ person: "p1", session: "s1", turns: first })
    await memory.ingest({ person: "p1", session: "s2", turns: second })
    await memory.fold("p1")
    const gone = (await store.windows.session("p1", "s2")).map((x) => x.id)
    expect(await memory.forget({ person: "p1", session: "s2" })).toEqual({ windows: 1, statements: 1, groups: 0 })
    expect(await store.windows.session("p1", "s2")).toEqual([])
    const probe = new Float32Array(256).fill(1 / 16)
    const left = await store.vectors.query("p1", probe, { kind: "window", top: 10 })
    expect(left.some((x) => gone.includes(x.id))).toBe(false)
    const berlin = (await said(store)).find((x) => x.text.includes("Berlin"))
    expect((await store.groups.get("p1", [berlin!.group]))[0]).toMatchObject({
      count: 1,
      last: day("2026-03-02", "09:01"),
    })
    expect(await memory.dossier("p1")).toBe(undefined)
    const dossier = await memory.fold("p1")
    expect(dossier?.text).not.toContain("so I feel alone")
    expect(dossier?.text).toContain("My sister Anna moved to Berlin")
    // A delayed retry cannot put forgotten words back. New events need new ids.
    expect((await memory.ingest({ person: "p1", session: "s2", turns: second })).turns).toBe(0)
    expect(await store.log.session("p1", "s2")).toEqual([])
  })

  test("the last statement of a group removes the group; forgetting everything leaves no dossier", async () => {
    const { store, memory } = setup()
    await memory.ingest({ person: "p1", session: "s1", turns: first })
    await memory.fold("p1")
    const sleep = (await said(store)).find((x) => x.text.includes("bank"))!
    expect(await memory.forget({ person: "p1", statements: [sleep.id] })).toEqual({
      windows: 1,
      statements: 2,
      groups: 2,
    })
    expect(await store.groups.get("p1", [sleep.group])).toEqual([])
    expect(await store.log.session("p1", "s1")).toEqual([])
    expect(await memory.fold("p1")).toBe(undefined)
    expect(await memory.recall({ person: "p1", conversation: ask("bank") })).toEqual({
      person: "p1",
      statements: [],
      windows: [],
    })
    await memory.forget({ person: "p1", session: "s1" })
    expect(await memory.fold("p1")).toBe(undefined)
    expect(await store.windows.all("p1")).toEqual([])
  })

  test("names that match nothing change nothing", async () => {
    const { memory } = setup()
    await memory.ingest({ person: "p1", session: "s1", turns: first })
    await memory.fold("p1")
    expect(await memory.forget({ person: "p1", statements: ["nope"] })).toEqual({
      windows: 0,
      statements: 0,
      groups: 0,
    })
    expect((await memory.dossier("p1"))?.updates).toBe(1)
  })

  test("a recall in flight cannot return a statement erased while its picker was waiting", async () => {
    const entered = Promise.withResolvers<void>()
    const release = Promise.withResolvers<void>()
    const base = Testing.decider()
    const decider: Decider = {
      ...base,
      yes: async (...args) => {
        entered.resolve()
        await release.promise
        return base.yes(...args)
      },
    }
    const { memory } = setup({ decider })
    await memory.ingest({ person: "p1", session: "s1", turns: first })
    const recall = memory.recall({ person: "p1", conversation: ask("sister Anna Berlin") })
    await entered.promise
    await memory.forget({ person: "p1", session: "s1" })
    release.resolve()
    expect(await recall).toEqual({ person: "p1", statements: [], windows: [] })
  })
})
