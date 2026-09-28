import { expect, test } from "bun:test"
import { Local } from "../src/local"
import { Memory } from "../src/memory"
import { Person } from "../src/person"
import { Testing } from "../src/testing"

const at = "2026-09-01T00:00:00.000Z"
const input = {
  id: "save-1",
  session: "preferences",
  text: "Please use short replies.",
  kind: "preference" as const,
  at,
}
const setup = (emptyExtraction = false) => {
  const store = Local.store()
  const memory = Memory.create({
    store,
    model: Testing.model(
      emptyExtraction
        ? (prompt) => (prompt.includes("What would a good therapist remember") ? { items: [] } : undefined)
        : undefined,
    ),
    embedder: Testing.embedder(),
    decider: Testing.decider(),
  })
  return { store, memory, person: Person.bind(memory, "sam") }
}

test("explicit save is durable, exactly retryable, sourced without fictitious conversation and person-bound", async () => {
  const { store, memory, person } = setup()
  const first = await person.remember(input)
  expect(first).toMatchObject({
    saved: true,
    duplicate: false,
    statement: { text: input.text, pinned: true, person: "sam", save: { id: input.id } },
  })
  expect((await person.remember(input)).duplicate).toBe(true)
  await expect(person.remember({ ...input, text: "different" })).rejects.toThrow("different content")
  expect(await store.log.session("sam", input.session)).toEqual([])
  const evidence = await person.evidence({ statements: [first.statement.id] })
  expect(evidence.windows[0]).toMatchObject({ origin: "explicit", turns: [], sources: [] })
  expect(await Person.bind(memory, "someone-else").evidence({ statements: [first.statement.id] })).toEqual({
    statements: [],
    windows: [],
    groups: [],
  })
  expect(
    (await person.recall({ conversation: [{ id: "q", at, role: "person", text: "short replies" }] })).recall.statements
      .length,
  ).toBe(1)
})

test("corrections invalidate stale dossiers; retries survive target deletion; forgotten saves cannot reappear", async () => {
  const { memory, person } = setup()
  const first = await person.remember(input)
  await memory.fold("sam")
  expect(await person.dossier()).toBeDefined()
  const correction = {
    ...input,
    id: "save-2",
    at: "2026-09-02T00:00:00.000Z",
    text: "Please use detailed replies.",
    replaces: first.statement.id,
  }
  const second = await person.remember(correction)
  expect(await person.dossier()).toBeUndefined()
  const old = await person.evidence({ statements: [first.statement.id] })
  expect(old.groups[0]?.until).toBe(correction.at)
  await expect(person.remember({ ...correction, id: "save-3" })).rejects.toThrow("no longer current")
  await person.forget({ statements: [first.statement.id] })
  expect((await person.remember(correction)).duplicate).toBe(true)
  await expect(person.remember(input)).rejects.toThrow("forgotten")
  expect((await person.inspect()).items.map((x) => x.id)).toEqual([second.statement.id])
})

test("inspection pages without overlap and rejects cursors from other people or filters", async () => {
  const { person, memory } = setup()
  for (let i = 0; i < 5; i++) await person.remember({ ...input, id: `item-${i}` })
  const ids: string[] = []
  let cursor: string | undefined
  do {
    const page = await person.inspect({ limit: 2, cursor })
    ids.push(...page.items.map((x) => x.id))
    cursor = page.nextCursor
    if (cursor) {
      await expect(Person.bind(memory, "other").inspect({ cursor })).rejects.toThrow("Invalid cursor")
      await expect(person.inspect({ kind: "windows", cursor })).rejects.toThrow("Invalid cursor")
    }
  } while (cursor)
  expect(new Set(ids).size).toBe(5)
  expect(ids.length).toBe(5)
  await expect(person.inspect({ cursor: "bad" })).rejects.toThrow("Invalid cursor")
})

test("cancelled saves do not commit", async () => {
  const { person } = setup()
  await expect(person.remember(input, { signal: AbortSignal.abort() })).rejects.toThrow()
  expect((await person.inspect()).items).toEqual([])
})

test("imported and proactive saves preserve pinning and provenance without inventing conversation turns", async () => {
  const { person, store } = setup()
  for (const origin of ["agent", "import"] as const) {
    const request = { ...input, id: origin, origin, pinned: false }
    const saved = await person.remember(request)
    expect(saved.statement.pinned).toBe(false)
    const evidence = await person.evidence({ statements: [saved.statement.id] })
    expect(evidence.windows[0]).toMatchObject({ origin, sources: [], turns: [] })
    expect(evidence.windows[0]?.text).not.toContain("Explicitly saved")
    expect(await store.log.session("sam", request.session)).toEqual([])
    expect((await person.remember(request)).duplicate).toBe(true)
    await expect(person.remember({ ...request, pinned: true })).rejects.toThrow("different content")
    await expect(person.remember({ ...request, origin: "explicit" })).rejects.toThrow("different content")
    await person.forget({ statements: [saved.statement.id] })
    await expect(person.remember(request)).rejects.toThrow("forgotten")
  }
})

test("erasing an empty person prevents its first pending explicit save from restoring memory", async () => {
  const store = Local.store()
  const started = Promise.withResolvers<void>()
  const release = Promise.withResolvers<void>()
  const embedder = Testing.embedder()
  const first = Memory.create({
    store,
    model: Testing.model(),
    decider: Testing.decider(),
    embedder: {
      ...embedder,
      embed: async (...args) => {
        started.resolve()
        await release.promise
        return embedder.embed(...args)
      },
    },
  })
  const second = Memory.create({ store, model: Testing.model(), decider: Testing.decider(), embedder })
  const save = first.remember({ person: "sam", ...input }).catch((error: unknown) => error)
  await started.promise
  try {
    await second.erase("sam")
  } finally {
    release.resolve()
  }
  expect(await save).toMatchObject({ name: "ConflictError" })
  expect((await second.inspect("sam")).items).toEqual([])
  expect(await store.windows.all("sam")).toEqual([])
})

test("another engine forgetting during extraction prevents stale derivations from committing", async () => {
  const store = Local.store()
  let release!: () => void, started!: () => void
  const waiting = new Promise<void>((resolve) => {
    started = resolve
  })
  const gate = new Promise<void>((resolve) => {
    release = resolve
  })
  const base = Testing.model()
  const first = Memory.create({
    store,
    embedder: Testing.embedder(),
    decider: Testing.decider(),
    model: {
      ...base,
      object: async (prompt, schema, call) => {
        started()
        await gate
        return base.object(prompt, schema, call)
      },
    },
  })
  const second = Memory.create({ store, embedder: Testing.embedder(), decider: Testing.decider(), model: base })
  const ingest = first.ingest({
    person: "sam",
    session: "pending",
    turns: [{ id: "turn", role: "person", text: "A private fact.", at }],
  })
  const rejected = ingest.catch((error: unknown) => error)
  await waiting
  await second.forget({ person: "sam", session: "pending" })
  release()
  expect(await rejected).toMatchObject({ name: "ConflictError" })
  expect(await store.windows.all("sam")).toEqual([])
  expect(await store.log.session("sam", "pending")).toEqual([])
  expect(
    (
      await first.ingest({
        person: "sam",
        session: "pending",
        turns: [{ id: "turn", role: "person", text: "A private fact.", at }],
      })
    ).turns,
  ).toBe(0)
})

test("automatic changes can supersede unpinned imports but preserve explicit pins", async () => {
  for (const pinned of [false, true]) {
    const store = Local.store()
    const memory = Memory.create({
      store,
      model: Testing.model((prompt) =>
        prompt.startsWith("A new statement about a person") ? { relation: "change", n: 1 } : undefined,
      ),
      embedder: { ...Testing.embedder(), embed: async (texts) => texts.map(() => new Float32Array([1, 0])) },
      decider: Testing.decider(),
    })
    const person = Person.bind(memory, "sam")
    const saved = await person.remember({ ...input, origin: "import", pinned })
    await person.ingest("later", [
      { id: "changed", role: "person", at: "2026-09-03T00:00:00.000Z", text: "I prefer longer replies now." },
    ])
    const [group] = await store.groups.get("sam", [saved.statement.group])
    expect(Boolean(group?.until)).toBe(!pinned)
  }
})

test("forget preview is model-free, includes shared sources and rejects a changed revision", async () => {
  const { person, store } = setup(true)
  const text = "long source ".repeat(2000)
  await person.ingest("chat", [{ id: "long", role: "person", text, at }])
  const windows = await store.windows.all("sam")
  expect(windows.length).toBeGreaterThan(1)
  const selectors = { windows: [windows[0]!.id] }
  const preview = await person.previewForget(selectors)
  expect(preview.windows.length).toBe(windows.length)
  expect(await store.windows.all("sam")).toHaveLength(windows.length)
  await person.remember(input)
  await expect(person.forget({ ...selectors, revision: preview.revision })).rejects.toThrow("Memory changed")
  const next = await person.previewForget(selectors)
  const result = await person.forget({ ...selectors, revision: next.revision })
  expect(result.windows).toBe(next.windows.length)
  expect(result.statements).toBe(next.statements.length)
  expect((await store.windows.all("sam")).every((row) => !next.windows.some((w) => w.id === row.id))).toBe(true)
})

test("source pages preserve Unicode and tail evidence and remain person-bound after forgetting", async () => {
  const { person, memory } = setup()
  const saved = await person.remember({ ...input, text: "🙂".repeat(100) + " scarlet banjo" })
  let offset = 0
  let text = ""
  while (true) {
    const page = await person.source({ window: saved.statement.window, offset, limit: 7 })
    text += page.text
    if (page.nextOffset === undefined) break
    offset = page.nextOffset
  }
  expect(text).toBe((await person.evidence({ windows: [saved.statement.window] })).windows[0]!.text)
  await expect(Person.bind(memory, "other").source({ window: saved.statement.window })).rejects.toThrow("not found")
  await person.forget({ statements: [saved.statement.id] })
  await expect(person.source({ window: saved.statement.window })).rejects.toThrow("not found")
})

test("saved/imported facts never claim to have been told in a conversation", async () => {
  const { person } = setup()
  await person.remember({ ...input, origin: "import" })
  const answer = await person.recall({
    conversation: [{ id: "q", role: "person", text: input.text, at }],
    render: true,
  })
  expect(answer.recall.retold).toBeUndefined()
  expect(answer.recall.statements[0]?.text).toContain("imported")
  expect(answer.prompt).not.toContain("They told you this before")
  expect(answer.recall.statements[0]?.text).not.toContain("said")
})

test("accepted long multipart messages retain their full tail and shared forgetting provenance", async () => {
  const { person, store } = setup(true)
  const text = Array(32).fill("x".repeat(4000)).join("\n") + " tail"
  await person.ingest("large", [{ id: "multipart", role: "person", text, at }])
  const windows = await store.windows.all("sam")
  expect(windows.at(-1)?.text).toContain("tail")
  const preview = await person.previewForget({ windows: [windows[0]!.id] })
  expect(preview.windows.length).toBe(windows.length)
  await person.forget({ windows: [windows[0]!.id], revision: preview.revision })
  expect(await store.windows.all("sam")).toEqual([])
})

test("imported dates and counts do not become claimed conversational disclosures", async () => {
  const { person } = setup()
  await person.remember({ ...input, text: "Sam plays the banjo.", origin: "import", pinned: false })
  const disclosed = "2026-09-20T00:00:00.000Z"
  await person.ingest("conversation", [{ id: "actual", role: "person", text: "Sam plays the banjo.", at: disclosed }])
  const answer = await person.recall({
    conversation: [{ id: "q", role: "person", text: "Sam plays the banjo.", at: disclosed }],
    render: true,
  })
  expect(answer.recall.retold?.group).toMatchObject({ count: 1, first: disclosed, last: disclosed })
  expect(answer.prompt).toContain("first on 2026-09-20, said once")
  expect(answer.prompt).not.toContain("said 2 times")
})
