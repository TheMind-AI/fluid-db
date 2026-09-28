import { describe, expect, test } from "bun:test"
import { Api, Turn } from "../src"

describe("Turn", () => {
  test("accepts a message with an offset timestamp", () => {
    const turn = Turn.Info.parse({ id: "t1", role: "person", text: "hi", at: "2026-09-27T10:00:00Z" })
    expect(turn.role).toBe("person")
    expect(Turn.Info.parse({ ...turn, at: "2026-09-27T12:00:00+02:00" }).at).toBe("2026-09-27T10:00:00.000Z")
  })

  test("rejects unknown fields and bad roles", () => {
    expect(Turn.Info.safeParse({ id: "t1", role: "bot", text: "hi", at: "2026-09-27T10:00:00Z" }).success).toBe(false)
    expect(
      Turn.Info.safeParse({ id: "t1", role: "person", text: "hi", at: "2026-09-27T10:00:00Z", x: 1 }).success,
    ).toBe(false)
  })
})

describe("Api.Forget", () => {
  test("needs a session, statements or windows", () => {
    expect(Api.Forget.safeParse({}).success).toBe(false)
    expect(Api.Forget.safeParse({ statements: [] }).success).toBe(false)
    expect(Api.Forget.safeParse({ session: "s1" }).success).toBe(true)
  })
})

describe("Api.Ask", () => {
  test("renders by default, and allows an empty conversation", () => {
    const ask = Api.Ask.parse({ conversation: [{ id: "t1", role: "person", text: "hi", at: "2026-09-27T10:00:00Z" }] })
    expect(ask.render).toBe(true)
    expect(Api.Ask.parse({ conversation: [] }).conversation).toEqual([])
  })
})
