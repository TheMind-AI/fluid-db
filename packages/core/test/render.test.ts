import { describe, expect, test } from "bun:test"
import { Prompt } from "../src/prompt"
import { Render } from "../src/render"
import { group, statement } from "./fixture"

const recall = {
  person: "p1",
  dossier: "They are a nurse.",
  statements: [{ kind: "statement" as const, text: "They have a sister in Berlin.", ids: ["s1"] }],
  windows: [{ kind: "window" as const, text: "From a conversation with Mind on 2026-03-01:\n[09:00] hi", ids: ["w1"] }],
}

describe("Render.render", () => {
  test("dossier, statements, windows, then how to use them", () => {
    const text = Render.render(recall, { name: "Sam" })
    expect(text.split("\n").filter((x) => x.startsWith("## "))).toEqual([
      "## What you know about Sam",
      "## Recorded facts",
      "## Source evidence",
      "## How to use this",
    ])
    expect(text.endsWith(Prompt.directive)).toBe(true)
  })

  test("the directive can be left out, and empty parts are", () => {
    expect(Render.render({ person: "p1", statements: [], windows: [] }, { directive: false })).toBe("")
  })

  test("the note on a re-telling goes before the directive", () => {
    const retold = {
      statement: statement("s1", "They have a sister in Berlin", "g1"),
      group: group("g1", 2),
      probability: 0.8,
    }
    const text = Render.render({ ...recall, retold })
    expect(text).toContain(
      "They told you this before (first on 2026-03-01, said 2 times): They have a sister in Berlin. Acknowledge",
    )
    expect(text.indexOf("## They are repeating")).toBeLessThan(text.indexOf("## How to use this"))
  })

  test("each part keeps to its share of the budget, but shows at least one item", () => {
    const long = {
      ...recall,
      windows: ["a", "b", "c"].map((x) => ({ kind: "window" as const, text: x.repeat(500), ids: [x] })),
    }
    const text = Render.render(long, { budget: 1_500, directive: false })
    expect(text).toContain("a".repeat(500))
    expect(text).not.toContain("b".repeat(500))
  })
})
