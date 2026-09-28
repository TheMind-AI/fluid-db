import { describe, expect, test } from "bun:test"
import { Conformance } from "@fluiddb/core/conformance"
import { Vector } from "@fluiddb/core"
import { Bun } from "../src/bun"
import { Migrate } from "../src/migrate"
import { SqlStore } from "../src/store"

Conformance.store({ describe, test, expect }, () => SqlStore.store(Bun.open()))

describe("SqlStore", () => {
  const window = (id: string) => ({
    id,
    person: "p1",
    session: "s1",
    at: "2026-01-01T10:00:00.000Z",
    text: "t",
    turns: ["a"],
  })

  test("upgrading the original schema keeps existing windows while adding raw-log storage", async () => {
    const sql = Bun.open()
    Migrate.run(sql, "store", SqlStore.migrations.slice(0, 1))
    const old = window("old")
    sql.run(
      "INSERT INTO fluid_windows (person, id, session, at, text, turns) VALUES (?, ?, ?, ?, ?, ?)",
      old.person,
      old.id,
      old.session,
      old.at,
      old.text,
      JSON.stringify(old.turns),
    )
    const store = SqlStore.store(sql)
    expect(await store.windows.all("p1")).toEqual([old])
    expect(await store.log.session("p1", "s1")).toEqual([])
  })

  test("migrations run once, and a second store on the same database reads what the first wrote", async () => {
    const sql = Bun.open()
    const first = SqlStore.store(sql)
    const vector = Vector.unit(Float32Array.from({ length: 1536 }, (_, i) => Math.sin(i)))
    await first.write("p1", { windows: [window("w1")], vectors: [{ id: "w1", kind: "window", vector }] })
    const second = SqlStore.store(sql)
    expect(await second.windows.all("p1")).toEqual([window("w1")])
    const [match] = await second.vectors.query("p1", vector, { kind: "window", top: 1 })
    expect(match?.id).toBe("w1")
    expect(match?.score).toBeCloseTo(1, 6)
    expect(sql.run("SELECT value FROM fluid_meta WHERE key = 'store.version'")).toEqual([
      { value: String(SqlStore.migrations.length) },
    ])
  })

  test("the vector cache follows writes that commit, not ones that fail", async () => {
    const store = SqlStore.store(Bun.open())
    const v = (...x: number[]) => Vector.unit(Float32Array.from(x))
    await store.write("p1", { vectors: [{ id: "a", kind: "statement", vector: v(1, 0) }] })
    expect((await store.vectors.query("p1", v(1, 0), { kind: "statement", top: 5 })).length).toBe(1)
    const bad = { ...window("w9"), person: "p2" }
    await expect(
      store.write("p1", { windows: [bad], vectors: [{ id: "b", kind: "statement", vector: v(1, 0) }] }),
    ).rejects.toThrow()
    expect((await store.vectors.query("p1", v(1, 0), { kind: "statement", top: 5 })).map((x) => x.id)).toEqual(["a"])
    await store.write("p1", { drop: { statements: ["a"] }, vectors: [{ id: "b", kind: "statement", vector: v(0, 1) }] })
    expect((await store.vectors.query("p1", v(1, 0), { kind: "statement", top: 5 })).map((x) => x.id)).toEqual(["b"])
  })

  test("a failure inside the transaction rolls everything back", async () => {
    const sql = Bun.open()
    const store = SqlStore.store(sql)
    sql.run(
      "CREATE TRIGGER no_bad BEFORE INSERT ON fluid_statements WHEN new.text = 'boom' BEGIN SELECT RAISE(ABORT, 'no'); END",
    )
    const statement = {
      id: "s1",
      person: "p1",
      session: "s1",
      window: "w1",
      text: "boom",
      kind: "life" as const,
      at: window("w1").at,
      group: "g1",
    }
    await expect(store.write("p1", { windows: [window("w1")], statements: [statement] })).rejects.toThrow()
    expect(await store.windows.all("p1")).toEqual([])
  })

  test("counts, and erasing a person leaves the others", async () => {
    const store = SqlStore.store(Bun.open())
    const original = { id: "raw", role: "person" as const, text: "original", at: window("w1").at }
    await store.write("p1", { log: { session: "s1", turns: [original] } })
    await store.write("p2", { log: { session: "s1", turns: [original] } })
    await store.write("p1", { windows: [window("w1"), window("w2")], turns: { session: "s1", ids: ["a"] } })
    await store.write("p2", { windows: [{ ...window("w1"), person: "p2" }] })
    expect(store.count("p1")).toEqual({ windows: 2, pending: 2, statements: 0, groups: 0 })
    expect(store.behind()).toEqual(["p1", "p2"])
    store.erase("p1")
    expect(store.count("p1")).toEqual({ windows: 0, pending: 0, statements: 0, groups: 0 })
    expect(await store.turns.seen("p1", ["a"])).toEqual([])
    expect(await store.log.session("p1", "s1")).toEqual([])
    expect(await store.log.session("p2", "s1")).toEqual([original])
    expect(store.count("p2").windows).toBe(1)
  })

  test("a later migration applies on top of earlier ones", () => {
    const sql = Bun.open()
    Migrate.run(sql, "x", [["CREATE TABLE x1 (a TEXT)"]])
    Migrate.run(sql, "x", [["CREATE TABLE x1 (a TEXT)"], ["CREATE TABLE x2 (b TEXT)"]])
    expect(sql.run("SELECT name FROM sqlite_master WHERE name LIKE 'x%' ORDER BY name")).toEqual([
      { name: "x1" },
      { name: "x2" },
    ])
  })
})

test("a second store's commit invalidates the first store's cached vectors", async () => {
  const sql = Bun.open()
  try {
    const first = SqlStore.store(sql),
      second = SqlStore.store(sql)
    const vector = Float32Array.of(1, 0)
    await first.write("sam", { vectors: [{ id: "fact", kind: "statement", vector }] })
    expect(await first.vectors.query("sam", vector, { kind: "statement", top: 1 })).toHaveLength(1)
    await second.write("sam", { drop: { statements: ["fact"] } })
    expect(await first.vectors.query("sam", vector, { kind: "statement", top: 1 })).toEqual([])
  } finally {
    sql.close()
  }
})
