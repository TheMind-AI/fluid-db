import { describe, expect, test } from "bun:test"
import { Local } from "../src/local"
import { Memory } from "../src/memory"
import { Testing } from "../src/testing"
import { day, turn } from "./fixture"

const at = (date: string) => day(`2026-09-${date}`)
const text = (city: string) => `They said: Sam lives in ${city}.`

function setup() {
  const store = Local.store()
  const memory = Memory.create({
    store,
    ids: Testing.ids(),
    options: { size: 1 },
    decider: Testing.decider(),
    embedder: { ...Testing.embedder(), embed: async (texts) => texts.map(() => new Float32Array([1, 0])) },
    model: Testing.model((prompt) => {
      if (!prompt.startsWith("A new statement")) return undefined
      const value = /NEW: (.*)/.exec(prompt)![1]
      const prior = prompt
        .split("EARLIER:\n")[1]!
        .split("\n")
        .map((line) => line.replace(/^\d+\. /, ""))
      const same = prior.indexOf(value!)
      const changed = prior.indexOf(text("Prague"))
      return same >= 0 ? { relation: "same", n: same + 1 } : { relation: "change", n: Math.max(0, changed) + 1 }
    }),
  })
  const save = (city: string, date: string, replaces?: string) =>
    memory.remember({
      person: "p1",
      session: `saved-${date}`,
      id: `${city}-${date}`,
      text: text(city),
      kind: "life",
      at: at(date),
      pinned: false,
      origin: "agent",
      ...(replaces ? { replaces } : {}),
    })
  const ingest = (city: string, date: string) =>
    memory.ingest({
      person: "p1",
      session: `chat-${date}`,
      turns: [turn(`t-${date}`, "person", `Sam lives in ${city}.`, at(date))],
    })
  const facts = async () => {
    const statements = await store.statements.windows(
      "p1",
      (await store.windows.all("p1")).map((w) => w.id),
    )
    const groups = await store.groups.get(
      "p1",
      statements.map((s) => s.group),
    )
    return statements.map((statement) => ({ statement, group: groups.find((g) => g.id === statement.group)! }))
  }
  return { store, memory, save, ingest, facts }
}

describe("source chronology", () => {
  test("a delayed older change stays historical and cannot end a newer saved fact", async () => {
    const { save, ingest, facts } = setup()
    await save("Berlin", "05")
    await ingest("Prague", "01")
    const rows = await facts()
    const berlin = rows.find((row) => row.statement.text === text("Berlin"))!
    const prague = rows.find((row) => row.statement.text === text("Prague"))!
    expect(berlin.group).toMatchObject({ first: at("05"), last: at("05"), replaces: [prague.group.id] })
    expect(berlin.group.until).toBeUndefined()
    expect(prague.group).toMatchObject({ first: at("01"), last: at("01"), until: at("05"), replaces: [] })
  })

  test("an older change between repeated observations ends at the newer confirmation", async () => {
    const { save, ingest, facts } = setup()
    await save("Berlin", "01")
    await ingest("Berlin", "10")
    await ingest("Prague", "05")
    const rows = await facts()
    expect(rows.find((row) => row.statement.text === text("Prague"))?.group.until).toBe(at("10"))
    for (const row of rows.filter((row) => row.statement.text === text("Berlin"))) {
      expect(row.group.until).toBeUndefined()
      expect(row.group).toMatchObject({ first: at("01"), last: at("10"), count: 2 })
    }
  })

  for (const forgotten of ["historical", "newer"] as const) {
    test(`forgetting the ${forgotten} side keeps the reversed history edge consistent`, async () => {
      const { memory, save, ingest, facts } = setup()
      await save("Berlin", "05")
      await ingest("Prague", "01")
      await memory.forget({ person: "p1", session: forgotten === "historical" ? "chat-01" : "saved-05" })
      const [remaining] = await facts()
      expect(remaining?.group.until).toBeUndefined()
      expect(remaining?.group.replaces).toEqual([])
      expect(remaining?.statement.text).toBe(text(forgotten === "historical" ? "Berlin" : "Prague"))
    })
  }

  test("forgetting a later repetition reverses history when only the older observation remains", async () => {
    const { memory, save, ingest, facts } = setup()
    await save("Berlin", "01")
    await ingest("Berlin", "10")
    await ingest("Prague", "05")
    await memory.forget({ person: "p1", session: "chat-10" })
    const rows = await facts()
    const berlin = rows.find((row) => row.statement.text === text("Berlin"))!.group
    const prague = rows.find((row) => row.statement.text === text("Prague"))!.group
    expect(berlin).toMatchObject({ first: at("01"), last: at("01"), until: at("05"), replaces: [] })
    expect(prague.replaces).toEqual([berlin.id])
    expect(prague.until).toBeUndefined()
  })

  test("forgetting one repetition retains a later successor and uses its earliest surviving observation", async () => {
    const { memory, save, ingest, facts } = setup()
    await save("Berlin", "01")
    await ingest("Berlin", "08")
    await ingest("Berlin", "10")
    await ingest("Prague", "05")
    await memory.forget({ person: "p1", session: "chat-10" })
    const rows = await facts()
    const berlin = rows.find((row) => row.statement.text === text("Berlin"))!.group
    const prague = rows.find((row) => row.statement.text === text("Prague"))!.group
    expect(berlin).toMatchObject({ first: at("01"), last: at("08"), count: 2, replaces: [prague.id] })
    expect(berlin.until).toBeUndefined()
    expect(prague.until).toBe(at("08"))
  })

  for (const sameBatch of [false, true]) {
    test(`returning to an earlier value creates a current period (${sameBatch ? "one batch" : "separate ingests"})`, async () => {
      const { memory, save, ingest, facts } = setup()
      await save("Prague", "01")
      if (sameBatch) {
        await memory.ingest({
          person: "p1",
          session: "return",
          turns: [
            turn("b", "person", "Sam lives in Berlin.", at("05")),
            turn("a", "person", "Sam lives in Prague.", at("10")),
          ],
        })
      } else {
        await ingest("Berlin", "05")
        await ingest("Prague", "10")
      }
      const rows = await facts()
      const first = rows.find((row) => row.statement.at === at("01"))!
      const middle = rows.find((row) => row.statement.at === at("05"))!
      const last = rows.find((row) => row.statement.at === at("10"))!
      expect(first.group).toMatchObject({ count: 1, first: at("01"), last: at("01"), until: at("05") })
      expect(middle.group).toMatchObject({ until: at("10"), replaces: [first.group.id] })
      expect(last.group.replaces).toEqual([middle.group.id])
      expect(last.group.until).toBeUndefined()
      expect(last.group.id).not.toBe(first.group.id)
      expect(rows.filter((row) => !row.group.until).map((row) => row.statement.text)).toEqual([text("Prague")])
    })
  }

  test("a historical repeat inside an ended period still joins that period", async () => {
    const { save, ingest, facts } = setup()
    const old = await save("Prague", "01")
    await save("Berlin", "10", old.statement.id)
    await ingest("Prague", "05")
    const rows = (await facts()).filter((row) => row.statement.text === text("Prague"))
    expect(rows).toHaveLength(2)
    expect(rows[0]!.group.id).toBe(rows[1]!.group.id)
    expect(rows[0]!.group).toMatchObject({ count: 2, first: at("01"), last: at("05"), until: at("10") })
  })

  test("inserting a historical change preserves the later replacement and its forget edge", async () => {
    const { memory, save, ingest, facts } = setup()
    const old = await save("Prague", "01")
    await save("Berlin", "10", old.statement.id)
    await ingest("Vienna", "05")
    const rows = await facts()
    const oldGroup = rows.find((row) => row.statement.at === at("01"))!.group
    const middle = rows.find((row) => row.statement.at === at("05"))!.group
    const latest = rows.find((row) => row.statement.at === at("10"))!.group
    expect(oldGroup.until).toBe(at("05"))
    expect(middle).toMatchObject({ until: at("10"), replaces: [oldGroup.id] })
    expect(latest.replaces).toEqual([middle.id])
    await memory.forget({ person: "p1", session: "saved-10" })
    expect((await facts()).find((row) => row.group.id === middle.id)?.group.until).toBeUndefined()
    expect((await facts()).find((row) => row.group.id === oldGroup.id)?.group.until).toBe(at("05"))
  })
})
