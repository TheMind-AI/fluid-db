import { describe, expect, test } from "bun:test"
import type { Turn } from "@fluiddb/schema"
import { Split } from "../src/split"

const turn = (id: string, role: Turn.Role, text: string, minute: number): Turn.Info => ({
  id,
  role,
  text,
  at: `2026-03-04T09:${String(minute).padStart(2, "0")}:00.000Z`,
})

describe("Split.question", () => {
  test("takes the last question of a message", () => {
    expect(Split.question("That sounds hard. How did you sleep? Tell me more.")).toBe("How did you sleep?")
    expect(Split.question("No question here. Just this.")).toBe("Just this.")
  })
})

describe("Split.windows", () => {
  test("puts the assistant's question before each of the person's messages", () => {
    const drafts = Split.windows({
      turns: [turn("a1", "assistant", "Hi. How are you today?", 0), turn("p1", "person", "tired again", 1)],
      skip: new Set(),
      size: 6,
      assistant: "Mind",
    })
    expect(drafts).toEqual([
      {
        at: "2026-03-04T09:01:00.000Z",
        turns: ["p1"],
        sources: ["a1", "p1"],
        text: "From a conversation with Mind on 2026-03-04:\n[09:01] (Mind asked: How are you today?) tired again",
      },
    ])
  })

  test("closes a window every `size` messages, sorts by time, and skips turns already seen", () => {
    const turns = [3, 1, 2, 4, 5].map((n) => turn(`p${n}`, "person", `message ${n}`, n))
    const drafts = Split.windows({ turns, skip: new Set(["p1"]), size: 2, assistant: "Mind" })
    expect(drafts.map((x) => x.turns)).toEqual([
      ["p2", "p3"],
      ["p4", "p5"],
    ])
  })
})
