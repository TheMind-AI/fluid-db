import { describe, expect, test } from "bun:test"
import { Client } from "@fluiddb/client"
import { App } from "../src/app"
import { Host } from "../src/host"
import { day, host, session, turn } from "./fixture"

// The typed client against the real app, in process: what a product sees.
describe("the client against the app", () => {
  test("a session, a recall, the dossier, forgetting and erasing", async () => {
    const { host: h, fire } = host()
    const token = "test-token-0123456789"
    const app = App.create({ token, people: () => Host.serve(h) })
    const fetch = ((url: string, init?: RequestInit) => app.request(url, init)) as unknown as typeof globalThis.fetch
    const client = Client.create({ url: "http://fluid.test", token, fetch })

    expect(await client.accept("p1", "s1", session)).toEqual({ accepted: 4, pending: 4 })
    await fire()
    const conversation = [turn("p9", "person", "My sister Anna moved to Berlin last spring.", day("2026-03-16"))]
    const answer = await client.recall("p1", { conversation })
    expect(answer.recall.retold?.group.count).toBe(1)
    expect(answer.prompt).toContain("## How to use this")
    expect((await client.dossier("p1"))?.updates).toBe(1)
    expect(await client.status("p1")).toMatchObject({ statements: 2, windows: { total: 1, pending: 0 } })
    const preview = await client.person("p1").previewForget({ session: "s1" })
    const source = await client.person("p1").source({ window: preview.windows[0]!.id, limit: 5 })
    expect(source.nextOffset).toBe(5)
    expect(await client.forget("p1", { session: "s1", revision: preview.revision })).toEqual({
      windows: 1,
      statements: 2,
      groups: 2,
    })
    expect(await client.dossier("p1")).toBe(undefined)
    await client.erase("p1")
    await expect(
      Client.create({ url: "http://fluid.test", token: "wrong-token-000000", fetch }).status("p1"),
    ).rejects.toThrow("a valid bearer token is required")
  })
})
