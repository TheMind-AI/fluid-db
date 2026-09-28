import { describe, expect, test } from "bun:test"
import { Detect } from "../src/detect"
import { Pick } from "../src/pick"
import { answering, scripted, statement } from "./fixture"

const items = ["a", "b", "c", "d"].map((id) => ({ id, text: `window ${id} `.repeat(10) }))

describe("Pick.decider", () => {
  test("keeps the top by probability, ties in search order, each excerpt cut to `chars`", async () => {
    const { decider, asked } = scripted({ yes: () => ({ r0: 0.2, r1: 0.9, r2: 0.2, r3: 0.95 }) })
    expect(await Pick.decider(decider, { moment: "now", items, top: 3, chars: 12 })).toEqual(["d", "b", "a"])
    expect(asked[0]?.state).toEqual({
      moment: "now",
      excerpts: { e0: "window a win", e1: "window b win", e2: "window c win", e3: "window d win" },
    })
    expect(asked[0]?.question.split("\n")[3]).toBe(
      "Does `excerpts.e3` hold something the person said that is useful to recall at `moment`?",
    )
  })

  test("asks nothing for no candidates", async () => {
    const { decider, asked } = scripted({})
    expect(await Pick.decider(decider, { moment: "now", items: [], top: 3, chars: 100 })).toEqual([])
    expect(asked).toEqual([])
  })
})

describe("Pick.model", () => {
  test("the model's ranking first, then the rest in search order", async () => {
    const { model, prompts } = answering(() => ({ top: [3, 3, 9, 1] }))
    const out = await Pick.model(model, { moment: "now", items, top: 3, chars: 8, role: "coach" })
    expect(out).toEqual(["c", "a", "b"])
    expect(prompts[0]).toStartWith(
      "Below are 4 excerpts from a person's memory, and a moment in their current conversation with their coach",
    )
    expect(prompts[0]).toContain("return the numbers of the 3 most useful")
    expect(prompts[0]).toEndWith("EXCERPTS:\n1. window a\n\n2. window b\n\n3. window c\n\n4. window d")
  })
})

describe("Detect.detect", () => {
  const statements = [statement("s1", "They have a sister in Berlin.", "g1"), statement("s2", "They run.", "g2")]

  test("returns the statement picked at or above the threshold", async () => {
    const { decider, asked } = scripted({ choose: () => ({ choice: "s1", probabilities: { s1: 0.7, none: 0.3 } }) })
    const out = await Detect.detect(decider, { message: "I went running", statements, threshold: 0.5 })
    expect(out).toEqual({ statement: statements[1]!, probability: 0.7 })
    expect(Object.keys(asked[0]?.options ?? {})).toEqual(["s0", "s1", "none"])
  })

  test("nothing below the threshold, or on none", async () => {
    const low = scripted({ choose: () => ({ choice: "s0", probabilities: { s0: 0.4 } }) })
    expect(await Detect.detect(low.decider, { message: "x", statements, threshold: 0.5 })).toBe(undefined)
    const none = scripted({ choose: () => ({ choice: "none", probabilities: { none: 0.9 } }) })
    expect(await Detect.detect(none.decider, { message: "x", statements, threshold: 0.5 })).toBe(undefined)
  })
})
