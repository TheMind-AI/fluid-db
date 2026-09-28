import { describe, expect, test } from "bun:test"
import { Link } from "../src/link"
import { Testing } from "../src/testing"
import { answering, day, group } from "./fixture"

const draft = {
  id: "s9",
  person: "p1",
  session: "s2",
  window: "w9",
  text: "They moved to Berlin.",
  kind: "life" as const,
  at: day("2026-04-01"),
}

describe("Link.decide", () => {
  test("is new without asking when nothing is close", async () => {
    const { model, prompts } = answering(() => ({ relation: "same", n: 1 }))
    expect(await Link.decide(model, "anything", [])).toEqual({ relation: "new" })
    expect(prompts).toEqual([])
  })

  test("asks the model the lab's question, with the earlier statements numbered", async () => {
    const { model, prompts } = answering(() => ({ relation: "change", n: 2 }))
    expect(await Link.decide(model, draft.text, ["They work at a bank.", "They live in Prague."])).toEqual({
      relation: "change",
      index: 1,
    })
    expect(prompts[0]).toEndWith(
      "NEW: They moved to Berlin.\n\nEARLIER:\n1. They work at a bank.\n2. They live in Prague.",
    )
  })

  test("a number outside the list is new", async () => {
    for (const n of [0, 3, -1]) {
      const { model } = answering(() => ({ relation: "same", n }))
      expect(await Link.decide(model, draft.text, ["a", "b"])).toEqual({ relation: "new" })
    }
  })
})

describe("Link.apply", () => {
  test("same: the statement joins the group, which counts it and widens its dates", () => {
    const out = Link.apply({ relation: "same", group: group("g1", 2) }, draft, Testing.ids())
    expect(out.statement).toEqual({ ...draft, group: "g1" })
    expect(out.groups).toEqual([{ ...group("g1", 3), last: day("2026-04-01") }])
  })

  test("change: a new group replaces the old one, which ends", () => {
    const out = Link.apply({ relation: "change", group: group("g1", 2) }, draft, Testing.ids())
    expect(out.statement.group).toBe("grp_0001")
    expect(out.groups).toEqual([
      { ...group("grp_0001", 1, day("2026-04-01")), replaces: ["g1"] },
      { ...group("g1", 2), until: day("2026-04-01") },
    ])
  })

  test("new: a group of its own", () => {
    const out = Link.apply({ relation: "new" }, draft, Testing.ids())
    expect(out.groups).toEqual([group("grp_0001", 1, day("2026-04-01"))])
  })
})
