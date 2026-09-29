import type { Group, Statement, Turn, Window } from "@fluiddb/schema"
import type { Store } from "./port"
import { Vector } from "./vector"

// The contract every Store keeps, runnable under any test framework: pass its describe/test/expect.
export interface Kit {
  describe(name: string, fn: () => void): void
  test(name: string, fn: () => Promise<void> | void): void
  expect(value: unknown): {
    toEqual(expected: unknown): void
    toBe(expected: unknown): void
    rejects: { toThrow(): unknown }
  }
}

const at = (minute: number) => `2026-01-01T10:${String(minute).padStart(2, "0")}:00.000Z`

const window = (id: string, person: string, session: string, minute: number): Window.Info => ({
  id,
  person,
  session,
  at: at(minute),
  text: `text of ${id}`,
  turns: [`${id}-t1`, `${id}-t2`],
})

const statement = (id: string, person: string, window: string, group: string, minute: number): Statement.Info => ({
  id,
  person,
  session: "s1",
  window,
  text: `statement ${id}`,
  kind: "life",
  at: at(minute),
  group,
})

const group = (id: string, person: string, extra: Partial<Group.Info> = {}): Group.Info => ({
  id,
  person,
  count: 1,
  first: at(1),
  last: at(1),
  replaces: [],
  ...extra,
})

const v = (...x: number[]) => Vector.unit(Float32Array.from(x))

export function store(kit: Kit, make: () => Promise<Store> | Store) {
  const { describe, test, expect } = kit
  const ids = (rows: { id: string }[]) => rows.map((x) => x.id)

  describe("Store", () => {
    test("erasure invalidates absent and previously erased revisions", async () => {
      const store = await make()
      const absent = await store.revision("p1")
      expect(absent).toBe(null)
      await store.erase("p1")
      const erased = await store.revision("p1")
      expect(typeof erased).toBe("string")
      await expect(
        store.write("p1", { windows: [window("stale", "p1", "s1", 1)] }, { revision: absent }),
      ).rejects.toThrow()
      await store.erase("p1")
      const again = await store.revision("p1")
      expect(typeof again).toBe("string")
      expect(again === erased).toBe(false)
      await expect(
        store.write("p1", { windows: [window("stale", "p1", "s1", 1)] }, { revision: erased }),
      ).rejects.toThrow()
      expect(await store.windows.all("p1")).toEqual([])
      await store.write("p1", { windows: [window("fresh", "p1", "s1", 1)] }, { revision: again })
      expect(ids(await store.windows.all("p1"))).toEqual(["fresh"])
    })

    test("conditional changes and erasure: stale writes fail atomically, including after recreation", async () => {
      const store = await make()
      const before = await store.revision("p1")
      await store.write("p1", { windows: [window("w1", "p1", "s1", 1)] }, { revision: before })
      await expect(
        store.write("p1", { windows: [window("stale", "p1", "s1", 1)] }, { revision: before }),
      ).rejects.toThrow()
      expect(ids(await store.windows.all("p1"))).toEqual(["w1"])
      const saved = await store.revision("p1")
      await store.write("p2", { windows: [window("w2", "p2", "s1", 1)] })
      await store.erase("p1")
      await store.write("p1", { windows: [window("new", "p1", "s1", 1)] })
      await expect(store.write("p1", { drop: { windows: ["new"] } }, { revision: saved })).rejects.toThrow()
      expect(ids(await store.windows.all("p2"))).toEqual(["w2"])
    })
    test("inspection: stable keyset paging, scoping and explicit save metadata", async () => {
      const store = await make()
      const saved = { ...statement("b", "p1", "w1", "g1", 1), pinned: true, save: { id: "request", replaces: "old" } }
      await store.write("p1", {
        statements: [statement("c", "p1", "w1", "g1", 2), saved, statement("a", "p1", "w1", "g1", 1)],
        windows: [{ ...window("w1", "p1", "s1", 1), origin: "explicit" }],
      })
      await store.write("p2", { statements: [{ ...saved, person: "p2" }] })
      expect(ids(await store.statements.list("p1", { limit: 2 }))).toEqual(["a", "b"])
      expect(ids(await store.statements.list("p1", { limit: 2, after: { at: at(1), id: "b" } }))).toEqual(["c"])
      expect(await store.statements.list("p1", { limit: 2, session: "absent" })).toEqual([])
      expect((await store.statements.get("p1", ["b"]))[0]).toEqual(saved)
      expect((await store.windows.list("p1", { limit: 1 }))[0]?.origin).toBe("explicit")
    })
    test("turns: scoped to a person, retained after forgetting to stop delayed redelivery", async () => {
      const store = await make()
      await store.write("p1", { turns: { session: "s1", ids: ["a", "b"] } })
      await store.write("p1", { turns: { session: "s2", ids: ["c"] } })
      expect(await store.turns.seen("p1", ["c", "a", "x"])).toEqual(["c", "a"])
      expect(await store.turns.seen("p2", ["a"])).toEqual([])
      await store.write("p1", { drop: { session: "s1" } })
      expect(await store.turns.seen("p1", ["a", "b", "c"])).toEqual(["a", "b", "c"])
    })

    test("batched processed IDs retain cross-session tombstones and reject ambiguous ownership atomically", async () => {
      const store = await make()
      await store.write("p1", {
        turns: [
          { session: "s1", ids: ["a"] },
          { session: "s2", ids: ["b"] },
        ],
      })
      await store.write("p1", { drop: { session: "s1" } })
      expect(await store.turns.seen("p1", ["a", "b"])).toEqual(["a", "b"])
      expect(await store.turns.seen("p2", ["a", "b"])).toEqual([])
      await expect(
        store.write("p1", {
          windows: [window("invalid", "p1", "s1", 1)],
          turns: [
            { session: "s1", ids: ["c"] },
            { session: "s2", ids: ["c"] },
          ],
        }),
      ).rejects.toThrow()
      expect(await store.windows.get("p1", ["invalid"])).toEqual([])
      expect(await store.turns.seen("p1", ["c"])).toEqual([])
    })

    test("raw log: immutable originals, scoped reads, erasure and replay tombstones", async () => {
      const store = await make()
      const turn: Turn.Info = { id: "t1", role: "person", text: "original words", at: at(1) }
      await store.write("p1", { log: { session: "s1", turns: [turn] } })
      await store.write("p2", { log: { session: "s1", turns: [{ ...turn, text: "another person" }] } })
      await store.write("p1", { log: { session: "s1", turns: [{ ...turn, text: "overwritten" }] } })
      expect(await store.log.session("p1", "s1")).toEqual([turn])
      expect(await store.log.session("p1", "s2")).toEqual([])
      await store.write("p1", { turns: { session: "s1", ids: [turn.id] }, drop: { log: [turn.id] } })
      await store.write("p1", { log: { session: "s1", turns: [turn] } })
      expect(await store.log.session("p1", "s1")).toEqual([])
      expect((await store.log.session("p2", "s1"))[0]?.text).toBe("another person")
      await store.write("p2", { drop: { session: "s1" } })
      expect(await store.log.session("p2", "s1")).toEqual([])
    })

    test("windows: get keeps the order asked for; lists are per person, in time order", async () => {
      const store = await make()
      await store.write("p1", { windows: [window("w2", "p1", "s2", 2), window("w1", "p1", "s1", 1)] })
      await store.write("p2", { windows: [window("w3", "p2", "s1", 3)] })
      expect(ids(await store.windows.get("p1", ["w2", "w1", "w3", "nope"]))).toEqual(["w2", "w1"])
      expect(await store.windows.get("p1", ["w1"])).toEqual([window("w1", "p1", "s1", 1)])
      expect(ids(await store.windows.session("p1", "s1"))).toEqual(["w1"])
      expect(ids(await store.windows.all("p1"))).toEqual(["w1", "w2"])
      expect(ids(await store.windows.all("p2"))).toEqual(["w3"])
    })

    test("windows: pending until a dossier includes them; dropping the dossier makes all pending again", async () => {
      const store = await make()
      await store.write("p1", { windows: [window("w1", "p1", "s1", 1), window("w2", "p1", "s1", 2)] })
      expect(ids(await store.windows.pending("p1"))).toEqual(["w1", "w2"])
      const info = { person: "p1", text: "d1", at: at(5), updates: 1 }
      await store.write("p1", { dossier: { info, windows: ["w1"] } })
      expect(ids(await store.windows.pending("p1"))).toEqual(["w2"])
      expect(await store.dossiers.get("p1")).toEqual(info)
      await store.write("p1", { windows: [{ ...window("w1", "p1", "s1", 1), text: "rewritten" }] })
      expect(ids(await store.windows.pending("p1"))).toEqual(["w2"])
      await store.write("p1", { drop: { dossier: true } })
      expect(await store.dossiers.get("p1")).toBe(undefined)
      expect(ids(await store.windows.pending("p1"))).toEqual(["w1", "w2"])
    })

    test("statements: by window and by group, in time order, then id", async () => {
      const store = await make()
      await store.write("p1", {
        statements: [
          statement("s3", "p1", "w2", "g2", 3),
          statement("s2", "p1", "w1", "g1", 1),
          statement("s1", "p1", "w1", "g1", 1),
        ],
      })
      await store.write("p2", { statements: [statement("s9", "p2", "w1", "g1", 1)] })
      expect(ids(await store.statements.windows("p1", ["w1"]))).toEqual(["s1", "s2"])
      expect(ids(await store.statements.groups("p1", ["g2", "g1"]))).toEqual(["s1", "s2", "s3"])
      expect(ids(await store.statements.get("p1", ["s3", "s9", "s1"]))).toEqual(["s3", "s1"])
      await store.write("p1", { statements: [{ ...statement("s1", "p1", "w1", "g2", 1), text: "changed" }] })
      expect(await store.statements.get("p1", ["s1"])).toEqual([
        { ...statement("s1", "p1", "w1", "g2", 1), text: "changed" },
      ])
    })

    test("groups: upsert keeps optional fields, and clears them when left out", async () => {
      const store = await make()
      await store.write("p1", { groups: [group("g1", "p1", { until: at(9), replaces: ["g0"] })] })
      expect(await store.groups.get("p1", ["g1"])).toEqual([group("g1", "p1", { until: at(9), replaces: ["g0"] })])
      await store.write("p1", { groups: [group("g1", "p1", { count: 2 })] })
      expect(await store.groups.get("p1", ["g1"])).toEqual([group("g1", "p1", { count: 2 })])
    })

    test("groups: which replace given ones", async () => {
      const store = await make()
      await store.write("p1", {
        groups: [
          group("g1", "p1"),
          group("g2", "p1", { replaces: ["g1"] }),
          group("g3", "p1", { replaces: ["g0", "g2"] }),
        ],
      })
      await store.write("p2", { groups: [group("g2", "p2", { replaces: ["g1"] })] })
      expect(ids(await store.groups.replacing("p1", ["g1"]))).toEqual(["g2"])
      expect(ids(await store.groups.replacing("p1", ["g1", "g2"])).toSorted()).toEqual(["g2", "g3"])
      expect(await store.groups.replacing("p1", ["g3"])).toEqual([])
    })

    test("vectors: ranked by cosine within one person and kind", async () => {
      const store = await make()
      await store.write("p1", {
        vectors: [
          { id: "a", kind: "statement", vector: v(1, 0, 0) },
          { id: "b", kind: "statement", vector: v(1, 1, 0) },
          { id: "c", kind: "window", vector: v(1, 0, 0) },
        ],
      })
      await store.write("p2", { vectors: [{ id: "d", kind: "statement", vector: v(1, 0, 0) }] })
      const found = await store.vectors.query("p1", v(1, 0, 0), { kind: "statement", top: 5 })
      expect(ids(found)).toEqual(["a", "b"])
      expect(Math.round((found[0]?.score ?? 0) * 1000)).toBe(1000)
      expect(Math.round((found[1]?.score ?? 0) * 1000)).toBe(707)
      expect(ids(await store.vectors.query("p1", v(1, 0, 0), { kind: "statement", top: 1 }))).toEqual(["a"])
      await store.write("p1", { vectors: [{ id: "a", kind: "statement", vector: v(0, 0, 1) }] })
      expect(ids(await store.vectors.query("p1", v(1, 0, 0), { kind: "statement", top: 1 }))).toEqual(["b"])
    })

    test("dropping windows and statements drops their vectors; groups and other people stay", async () => {
      const store = await make()
      await store.write("p1", {
        windows: [window("w1", "p1", "s1", 1)],
        statements: [statement("s1", "p1", "w1", "g1", 1)],
        groups: [group("g1", "p1")],
        vectors: [
          { id: "w1", kind: "window", vector: v(1, 0) },
          { id: "s1", kind: "statement", vector: v(1, 0) },
        ],
      })
      await store.write("p2", {
        windows: [window("w1", "p2", "s1", 1)],
        vectors: [{ id: "w1", kind: "window", vector: v(1, 0) }],
      })
      await store.write("p1", { drop: { windows: ["w1"], statements: ["s1"] } })
      expect(await store.windows.all("p1")).toEqual([])
      expect(await store.statements.get("p1", ["s1"])).toEqual([])
      expect(await store.vectors.query("p1", v(1, 0), { kind: "window", top: 5 })).toEqual([])
      expect(await store.vectors.query("p1", v(1, 0), { kind: "statement", top: 5 })).toEqual([])
      expect(ids(await store.groups.get("p1", ["g1"]))).toEqual(["g1"])
      expect(ids(await store.vectors.query("p2", v(1, 0), { kind: "window", top: 5 }))).toEqual(["w1"])
      await store.write("p1", { drop: { groups: ["g1"] } })
      expect(await store.groups.get("p1", ["g1"])).toEqual([])
    })

    test("a write drops before it puts", async () => {
      const store = await make()
      await store.write("p1", { windows: [window("w1", "p1", "s1", 1)] })
      await store.write("p1", {
        windows: [{ ...window("w1", "p1", "s1", 1), text: "again" }],
        vectors: [{ id: "w1", kind: "window", vector: v(1, 0) }],
        drop: { windows: ["w1"] },
      })
      expect((await store.windows.get("p1", ["w1"]))[0]?.text).toBe("again")
      expect(ids(await store.vectors.query("p1", v(1, 0), { kind: "window", top: 5 }))).toEqual(["w1"])
    })

    test("a bad write changes nothing", async () => {
      const store = await make()
      const bad = { ...statement("s1", "p1", "w1", "g1", 1), kind: "nonsense" } as unknown as Statement.Info
      await expect(store.write("p1", { windows: [window("w1", "p1", "s1", 1)], statements: [bad] })).rejects.toThrow()
      await expect(store.write("p1", { windows: [window("w2", "p2", "s1", 1)] })).rejects.toThrow()
      expect(await store.windows.all("p1")).toEqual([])
      expect(await store.windows.all("p2")).toEqual([])
    })

    test("large reads and writes", async () => {
      const store = await make()
      const many = Array.from({ length: 250 }, (_, i) =>
        statement(`s${String(i).padStart(3, "0")}`, "p1", "w1", `g${i % 3}`, 1),
      )
      await store.write("p1", { statements: many, turns: { session: "s1", ids: many.map((x) => x.id) } })
      expect((await store.statements.get("p1", ids(many))).length).toBe(250)
      expect((await store.statements.groups("p1", ["g0", "g1", "g2"])).length).toBe(250)
      expect((await store.turns.seen("p1", ids(many))).length).toBe(250)
    })
  })
}

export * as Conformance from "./conformance"
