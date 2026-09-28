import { describe, expect, test } from "bun:test"
import { Prompt } from "@fluiddb/core"
import { Testing } from "@fluiddb/core/testing"
import { Shim } from "../src/shim"
import golden from "./golden.json"

describe("Shim.prompt", () => {
  // golden.json holds the prompts deprecated/python/lab/common/jev.py builds for the same questions (synthetic text).
  test("is the lab's prompt, byte for byte", () => {
    const memory = { s0: "They run every morning.", s1: "They have a sister in Berlin." }
    const options = {
      ...memory,
      none: "none of these: the person is saying something new, or nothing about themselves",
    }
    const state = { message: "I went running again, like I said — 5 km", memory }
    expect(Shim.prompt(state, {}, { pick: { question: Prompt.detect, options } })).toBe(golden.choose)
    const questions = Object.fromEntries([0, 1].map((n) => [`r${n}`, Prompt.pick.replace("{key}", `e${n}`)]))
    expect(
      Shim.prompt({ moment: "How was the run?", excerpts: { e0: "They run.", e1: "Ünïcode ✓" } }, questions, {}),
    ).toBe(golden.yes)
  })
})

describe("Shim answers", () => {
  test("keys match exactly, then loosely, then by position", () => {
    const items = [{ key: "`R1`" }, { key: "r0" }]
    expect([...Shim.byKey(["r0", "r1"], items).entries()]).toEqual([
      ["r0", { key: "r0" }],
      ["r1", { key: "`R1`" }],
    ])
    expect([...Shim.byKey(["a", "b"], [{ key: "1" }, { key: "2" }]).keys()]).toEqual(["a", "b"])
    expect(Shim.byKey(["a", "b"], [{ key: "1" }]).size).toBe(0)
  })

  test("a yes/no probability agrees with the answer", () => {
    expect(Shim.yesProbability({ key: "r", yes: true, probability: 0.9 })).toBe(0.9)
    expect(Shim.yesProbability({ key: "r", yes: false, probability: 0.9 })).toBeCloseTo(0.1)
    expect(Shim.yesProbability({ key: "r", yes: false, probability: 0.2 })).toBe(0.2)
    expect(Shim.yesProbability({ key: "r", yes: true, probability: 7 })).toBe(1)
    expect(Shim.yesProbability(undefined)).toBe(0.5)
  })

  test("a choice's probabilities are over the options and sum to 1", () => {
    const answer = {
      key: "pick",
      choice: "`S1`",
      probabilities: [
        { option: "s1", p: 3 },
        { option: "none", p: 1 },
        { option: "zz", p: 5 },
      ],
    }
    expect(Shim.choice(["s0", "s1", "none"], answer)).toEqual({
      choice: "s1",
      probabilities: { s0: 0, s1: 0.75, none: 0.25 },
    })
    expect(Shim.choice(["a", "b"], { ...answer, choice: "?", probabilities: [{ option: "b", p: 1 }] }).choice).toBe("b")
    expect(Shim.choice(["a", "b"], undefined)).toEqual({ choice: "a", probabilities: { a: 0.5, b: 0.5 } })
  })

  test("a model answers Jev's questions through the shim", async () => {
    const model = Testing.model((prompt) =>
      prompt.includes("YES/NO QUESTIONS (key: question):\n- r0")
        ? { yes_no: [{ key: "r0", yes: false, probability: 0.8 }], choices: [] }
        : {
            yes_no: [],
            choices: [
              {
                key: "pick",
                choice: "b",
                probabilities: [
                  { option: "b", p: 0.6 },
                  { option: "a", p: 0.4 },
                ],
              },
            ],
          },
    )
    const decider = Shim.decider(model)
    expect(await decider.yes({}, { r0: "?" })).toEqual({ r0: 0.2 })
    expect(await decider.choose({}, "?", { a: "a", b: "b" })).toEqual({
      choice: "b",
      probabilities: { a: 0.4, b: 0.6 },
    })
    expect(decider.processors).toEqual(model.processors)
  })
})
