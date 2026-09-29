import { expect, test } from "bun:test"
import { Local } from "../src/local"
import { Memory } from "../src/memory"
import { Person } from "../src/person"
import { Testing } from "../src/testing"

const inputs = Array.from({ length: 20 }, (_, i) => ({
  id: `import-${i}`,
  session: `original-session-${i}`,
  text: `Fictional fact ${i}.`,
  kind: "life" as const,
  at: "2026-09-01T00:00:00.000Z",
  pinned: i === 0,
  origin: "import" as const,
}))
function setup() {
  const store = Local.store()
  const base = Testing.embedder()
  const calls: string[][] = []
  const writes: unknown[] = []
  const write = store.write.bind(store)
  store.write = async (...args) => {
    writes.push(args[1])
    await write(...args)
  }
  const memory = Memory.create({
    store,
    model: Testing.model(),
    decider: Testing.decider(),
    embedder: {
      ...base,
      embed: async (texts, call) => {
        calls.push(texts)
        return base.embed(texts, call)
      },
    },
  })
  return { store, memory, person: Person.bind(memory, "sam"), calls, writes }
}

test("twenty independent saves share embedding and atomic persistence without losing source sessions", async () => {
  const { store, memory, person, calls, writes } = setup()
  const results = await person.rememberMany(inputs)
  expect(calls).toHaveLength(1)
  expect(calls[0]).toHaveLength(40)
  expect(writes).toHaveLength(1)
  expect(results.map((result) => result.statement.session)).toEqual(inputs.map((input) => input.session))
  expect(results.map((result) => result.statement.pinned)).toEqual(inputs.map((input) => input.pinned))
  expect(
    await store.turns.seen(
      "sam",
      results.map((result) => result.statement.id),
    ),
  ).toHaveLength(20)
  expect(
    await store.turns.seen(
      "someone-else",
      results.map((result) => result.statement.id),
    ),
  ).toEqual([])
  expect((await person.rememberMany(inputs)).every((result) => result.duplicate)).toBe(true)
  expect(calls).toHaveLength(1)
  expect(writes).toHaveLength(1)
  await memory.forget({ person: "sam", session: inputs[0]!.session })
  expect((await person.inspect()).items).toHaveLength(19)
  await expect(person.rememberMany(inputs)).rejects.toThrow("forgotten")
  expect((await person.inspect()).items).toHaveLength(19)
})

test("mixed old single saves and new batch saves retry exactly; changed IDs fail before any write", async () => {
  const { person, calls, writes } = setup()
  const old = await person.remember(inputs[0]!)
  calls.length = 0
  writes.length = 0
  const result = await person.rememberMany(inputs.slice(0, 3))
  expect(result[0]).toEqual({ ...old, duplicate: true })
  expect(result.slice(1).every((item) => !item.duplicate)).toBe(true)
  expect(calls[0]).toHaveLength(4)
  expect(writes).toHaveLength(1)
  await expect(person.rememberMany([inputs[3]!, { ...inputs[0]!, text: "Changed content" }])).rejects.toThrow(
    "different content",
  )
  expect((await person.inspect()).items).toHaveLength(3)
  expect(writes).toHaveLength(1)
})

test("batch failure, cancellation and concurrent erasure cannot leave partial saves", async () => {
  for (const failure of ["provider", "cancel", "erase"] as const) {
    const store = Local.store()
    const ready = Promise.withResolvers<void>()
    const release = Promise.withResolvers<void>()
    const controller = new AbortController()
    const base = Testing.embedder()
    const first = Memory.create({
      store,
      model: Testing.model(),
      decider: Testing.decider(),
      embedder: {
        ...base,
        embed: async (...args) => {
          ready.resolve()
          await release.promise
          if (failure === "provider") throw new Error("synthetic provider unavailable")
          return base.embed(...args)
        },
      },
    })
    const second = Memory.create({ store, model: Testing.model(), decider: Testing.decider(), embedder: base })
    const result = first
      .rememberMany({ person: "sam", memories: inputs }, { signal: controller.signal })
      .catch((error: unknown) => error)
    await ready.promise
    if (failure === "cancel") controller.abort()
    if (failure === "erase") await second.erase("sam")
    release.resolve()
    expect(await result).toBeInstanceOf(Error)
    expect((await second.inspect("sam")).items).toEqual([])
    expect(await store.windows.all("sam")).toEqual([])
  }
})

test("batches reject repeated IDs, corrections and oversized requests before calling a provider", async () => {
  const { person, calls, writes } = setup()
  for (const invalid of [
    [],
    [...inputs, { ...inputs[0]!, id: "extra" }],
    [inputs[0]!, inputs[0]!],
    [{ ...inputs[0]!, replaces: "existing" }],
  ]) {
    await expect(person.rememberMany(invalid)).rejects.toThrow()
  }
  expect(calls).toEqual([])
  expect(writes).toEqual([])
})
