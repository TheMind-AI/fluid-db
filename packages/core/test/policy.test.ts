import { describe, expect, test } from "bun:test"
import { ProcessorError } from "../src/error"
import { Local } from "../src/local"
import { Memory } from "../src/memory"
import { Policy } from "../src/policy"
import { Testing } from "../src/testing"

describe("Policy.check", () => {
  const providers = [
    { id: "openai:gpt-6-luna", processors: ["openai"] },
    { id: "jev", processors: ["openrouter", "typesafe"] },
  ]

  test("allows everything when no list is given, and listed processors", () => {
    expect(() => Policy.check(undefined, providers)).not.toThrow()
    expect(() => Policy.check(["openai", "openrouter", "typesafe"], providers)).not.toThrow()
  })

  test("refuses a provider that reaches any processor not listed, naming both", () => {
    const error = (() => {
      try {
        Policy.check(["openai", "openrouter"], providers)
      } catch (e) {
        return e
      }
    })()
    expect(error).toBeInstanceOf(ProcessorError)
    expect((error as ProcessorError).provider).toBe("jev")
    expect((error as ProcessorError).processor).toBe("typesafe")
  })

  test("a memory refuses to start with a provider outside the list", () => {
    const make = () =>
      Memory.create({
        model: Testing.model(),
        embedder: Testing.embedder(),
        decider: Testing.decider(),
        store: Local.store(),
        processors: ["openai"],
      })
    expect(make).toThrow(ProcessorError)
  })
})
