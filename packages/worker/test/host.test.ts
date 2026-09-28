import { describe, expect, test } from "bun:test"
import { day, host, outage, session, turn } from "./fixture"

describe("the inbox", () => {
  test("takes turns once, and waits for the conversation to be quiet", async () => {
    const { host: h, clock, alarm } = host()
    expect(await h.accept("p1", "s1", session.slice(0, 2))).toEqual({ accepted: 2, pending: 2 })
    expect(alarm.at).toBe(clock.now + 60_000)
    clock.now += 30_000
    expect(await h.accept("p1", "s1", session)).toEqual({ accepted: 2, pending: 4 })
    expect(alarm.at).toBe(clock.now + 60_000)
    // Never later than `wait` after the oldest turn.
    clock.now += 590_000
    await h.accept("p1", "s1", [turn("p3", "person", "More.", day("2026-03-02", "09:10"))])
    expect(alarm.at).toBe(clock.now - 620_000 + 600_000)
  })

  test("the alarm ingests every waiting session, then brings the dossier up to date", async () => {
    const { host: h, fire, alarm } = host()
    await h.accept("p1", "s1", session)
    await fire()
    expect(await h.status("p1")).toEqual({
      inbox: { pending: 0, failed: 0 },
      windows: { total: 1, pending: 0 },
      statements: 2,
      groups: 2,
      dossier: { updates: 1, at: "2026-01-01T00:00:01.000Z" },
    })
    expect(alarm.at).toBe(null)
  })

  test("ingested turns are not taken again", async () => {
    const { host: h } = host()
    expect(await h.ingest("p1", "s1", session)).toEqual({ turns: 2, windows: 1, statements: 2, linked: 0 })
    expect(await h.accept("p1", "s1", session)).toEqual({ accepted: 0, pending: 0 })
    expect(await h.ingest("p1", "s1", session)).toEqual({ turns: 0, windows: 0, statements: 0, linked: 0 })
    expect((await h.status("p1")).inbox).toEqual({ pending: 0, failed: 0 })
  })

  test("ingesting now: a failure is thrown, and the turns wait for the retry", async () => {
    const { model, state } = outage()
    const { host: h } = host({ model })
    state.down = true
    await expect(h.ingest("p1", "s1", session)).rejects.toThrow("failed with 503")
    expect((await h.status("p1")).inbox).toEqual({ pending: 4, failed: 0 })
    state.down = false
    expect((await h.ingest("p1", "s1", session)).statements).toBe(2)
  })

  test("writes run one at a time: the same turns sent twice at once are ingested once", async () => {
    const { host: h } = host()
    const [a, b] = await Promise.all([h.ingest("p1", "s1", session), h.ingest("p1", "s1", session)])
    expect(a.turns + b.turns).toBe(2)
    expect((await h.status("p1")).windows.total).toBe(1)
  })

  test("a failing model: turns wait, retries back off, then they are set aside until retried", async () => {
    const { model, state } = outage()
    const { host: h, fire, alarm, clock, events } = host({ model, settings: { attempts: 3 } })
    state.down = true
    await h.accept("p1", "s1", session)
    await fire()
    expect((await h.status("p1")).inbox).toEqual({ pending: 4, failed: 0 })
    expect(alarm.at).toBe(clock.now + 60_000)
    await fire()
    expect(alarm.at).toBe(clock.now + 120_000)
    await fire()
    expect((await h.status("p1")).inbox).toEqual({ pending: 0, failed: 4 })
    expect(alarm.at).toBe(null)
    expect(events.filter((x) => x === "ingest.failed").length).toBe(3)
    state.down = false
    expect(await h.retry("p1")).toEqual({ accepted: 4, pending: 4 })
    await fire()
    expect(await h.status("p1")).toMatchObject({
      inbox: { pending: 0, failed: 0 },
      statements: 2,
      dossier: { updates: 1 },
    })
  })

  test("a failed dossier update is caught up on the next alarm", async () => {
    const calls = { n: 0 }
    const { model } = outage()
    const flaky = {
      ...model,
      text: async (prompt: string) => (++calls.n === 1 ? Promise.reject(new Error("x")) : model.text(prompt)),
    }
    const { host: h, fire, alarm } = host({ model: flaky })
    await h.accept("p1", "s1", session)
    await fire()
    expect(await h.status("p1")).toMatchObject({ statements: 2, windows: { total: 1, pending: 1 } })
    expect(alarm.at).not.toBe(null)
    await fire()
    expect(await h.status("p1")).toMatchObject({ windows: { total: 1, pending: 0 }, dossier: { updates: 1 } })
  })

  test("an unexpected storage failure schedules another alarm instead of stranding work", async () => {
    const { host: h, sql, fire, alarm, clock } = host()
    await h.accept("p1", "s1", session)
    const run = sql.run
    sql.run = (query, ...params) => {
      if (query.includes("SELECT person, session")) throw new Error("storage unavailable")
      return run(query, ...params)
    }
    await fire()
    expect(alarm.at).toBe(clock.now + 60_000)
    sql.run = run
    await fire()
    expect((await h.status("p1")).statements).toBe(2)
  })

  test("synchronous ingestion reports only its session when other work is waiting", async () => {
    const { host: h } = host()
    await h.accept("p1", "older", session)
    const out = await h.ingest("p1", "newer", [turn("new", "person", "A different topic.", day("2026-03-09"))])
    expect(out.turns).toBe(1)
    expect((await h.status("p1")).statements).toBe(3)
  })
})

describe("the rest", () => {
  test("recall answers with the prompt section, unless asked not to render", async () => {
    const { host: h } = host()
    await h.ingest("p1", "s1", session)
    const conversation = [turn("p9", "person", "My sister Anna moved to Berlin last spring.", day("2026-03-16"))]
    const answer = await h.recall("p1", { conversation, name: "Sam" })
    expect(answer.recall.retold?.statement.text).toContain("Berlin")
    expect(answer.prompt).toContain("## What you know about Sam")
    expect((await h.recall("p1", { conversation, render: false })).prompt).toBe(undefined)
  })

  test("forgetting a session takes its waiting turns too, and the dossier is rewritten", async () => {
    const { host: h, fire, alarm, clock } = host()
    await h.ingest("p1", "s1", session)
    await h.accept("p1", "s2", [turn("p5", "person", "Not yet ingested.", day("2026-03-03"))])
    expect(await h.forget("p1", { session: "s1" })).toEqual({ windows: 1, statements: 2, groups: 2 })
    expect(alarm.at).toBe(clock.now)
    expect(await h.dossier("p1")).toBe(null)
    await h.forget("p1", { session: "s2" })
    expect(await h.accept("p1", "s2", [turn("p5", "person", "Not yet ingested.", day("2026-03-03"))])).toEqual({
      accepted: 0,
      pending: 0,
    })
    expect((await h.status("p1")).inbox.pending).toBe(0)
    await fire()
    expect(await h.status("p1")).toMatchObject({ windows: { total: 0 }, statements: 0 })
  })

  test("erasing a person leaves nothing of theirs, and others untouched", async () => {
    const { host: h } = host()
    await h.ingest("p1", "s1", session)
    await h.ingest("p2", "s1", session)
    await h.accept("p1", "s2", [turn("p5", "person", "Waiting.", day("2026-03-03"))])
    await h.erase("p1")
    expect(await h.status("p1")).toEqual({
      inbox: { pending: 0, failed: 0 },
      windows: { total: 0, pending: 0 },
      statements: 0,
      groups: 0,
    })
    expect((await h.status("p2")).statements).toBe(2)
  })
})
