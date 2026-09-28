import { expect, test } from "bun:test"
import { Memory, Pick, type Decider, type LanguageModel, type Picker } from "../src/index"
import { Local } from "../src/local"
import { Testing } from "../src/testing"
import { day, turn } from "./fixture"

const deps = (): Memory.Deps => ({
  model: Testing.model(),
  decider: Testing.decider(),
  embedder: Testing.embedder(),
  store: Local.store(),
  processors: ["test"],
  clock: Testing.clock(),
  ids: Testing.ids(),
})
const turns = [turn("a", "person", "My sister Anna lives in Berlin.", day("2026-03-02", "10:00"))]

test("each model stage and the detector can be replaced independently", async () => {
  const calls: Record<string, number> = {}
  const model = (stage: string): LanguageModel => ({
    ...Testing.model(() => {
      calls[stage] = (calls[stage] ?? 0) + 1
    }),
    id: `test:${stage}`,
  })
  const base = deps()
  const detector = Testing.decider()
  const memory = Memory.create({
    ...base,
    model: Testing.model(() => {
      throw new Error("base must not be called")
    }),
    models: { extract: model("extract"), link: model("link"), dossier: model("dossier"), pick: model("pick") },
    decider: {
      ...base.decider,
      yes: async () => {
        throw new Error("picker decider must not run")
      },
      choose: async () => {
        throw new Error("default detector must not run")
      },
    },
    detector: {
      ...detector,
      choose: async (...args) => {
        calls.detect = (calls.detect ?? 0) + 1
        return detector.choose(...args)
      },
    },
    options: { pick: "model" },
  })
  await memory.ingest({ person: "p", session: "s", turns })
  await memory.ingest({ person: "p", session: "s2", turns: [{ ...turns[0]!, id: "b" }] })
  await memory.fold("p")
  const result = await memory.recall({ person: "p", conversation: turns })
  for (const stage of ["extract", "link", "dossier", "pick", "detect"]) expect(calls[stage]).toBeGreaterThan(0)
  expect(result.retold).toBeDefined()
})

test("processor policy covers stage overrides, custom pickers, routed pickers and detectors before calls", () => {
  const foreign = { ...Testing.model(), processors: ["foreign"] }
  const picker = { ...Pick.searchOrder(), processors: ["foreign"] }
  const detector: Decider = { ...Testing.decider(), processors: ["foreign"] }
  for (const override of [
    ...["extract", "link", "dossier", "pick"].map((stage) => ({ models: { [stage]: foreign } })),
    { picker },
    { detector },
    { picker: Pick.route({ statement: Pick.searchOrder(), window: picker }) },
  ])
    expect(() => Memory.create({ ...deps(), ...override })).toThrow("foreign")
})

test("custom picking cannot inject foreign IDs, repeat IDs or exceed the keep limit", async () => {
  for (const select of [
    (_input: Parameters<Picker["pick"]>[0]) => ["foreign-person-record"],
    (input: Parameters<Picker["pick"]>[0]) => [input.items[0]!.id, input.items[0]!.id],
    (input: Parameters<Picker["pick"]>[0]) => input.items.map((item) => item.id),
  ]) {
    const memory = Memory.create({
      ...deps(),
      picker: { id: "test:bad", processors: [], pick: async (input) => select(input) },
      options: { size: 1, statements: { keep: 1 }, windows: { keep: 1 } },
    })
    await memory.ingest({
      person: "p",
      session: "s",
      turns: [...turns, { ...turns[0]!, id: "b", text: "I work in a bank." }],
    })
    await expect(memory.recall({ person: "p", conversation: turns })).rejects.toThrow()
  }
})

test("kind routing reaches both stores and gives custom pickers copies of candidate text", async () => {
  const seen: string[] = []
  const picker = (kind: "statement" | "window"): Picker => ({
    id: `test:${kind}`,
    processors: [],
    pick: async (input) => {
      expect(input.kind).toBe(kind)
      seen.push(kind)
      input.items[0]!.text = "mutated by plugin"
      return input.items.slice(0, input.top).map((item) => item.id)
    },
  })
  const memory = Memory.create({
    ...deps(),
    picker: Pick.route({ statement: picker("statement"), window: picker("window") }),
    options: { detect: false },
  })
  await memory.ingest({ person: "p", session: "s", turns })
  const result = await memory.recall({ person: "p", conversation: turns })
  expect(seen.toSorted()).toEqual(["statement", "window"])
  expect(JSON.stringify(result)).not.toContain("mutated by plugin")
  expect(JSON.stringify(result)).toContain("Berlin")
})
