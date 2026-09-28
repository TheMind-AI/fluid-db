import { describe, expect, test } from "bun:test"
import { Memory } from "../src/memory"
import { Local } from "../src/local"
import { Split } from "../src/split"
import { Testing } from "../src/testing"
import type { Turn } from "@fluiddb/schema"

const at = "2026-09-27T12:00:00.000Z"
const turn = (id: string, text: string): Turn.Info => ({ id, text, role: "person", at })

describe("complete input coverage", () => {
  test("keeps ordinary messages whole and covers every character of an oversized message", () => {
    const text = "ab😀cd ".repeat(4000) + "CANCELLED"
    const drafts = Split.windows({ turns: [turn("long", text)], assistant: "Mind", size: 6, skip: new Set() })
    expect(drafts.length).toBeGreaterThan(1)
    expect(drafts.every((x) => x.text.length <= 12_000 && x.turns[0] === "long")).toBe(true)
    expect(drafts.map((x) => x.text.split("\n").slice(1).join("\n").slice(8)).join("")).toBe(text)
  })

  test("extracts beyond both historical prefix limits and forgets every chunk of the selected source", async () => {
    const store = Local.store()
    const prompts: string[] = []
    const memory = Memory.create({
      store,
      ids: Testing.ids(),
      embedder: Testing.embedder(),
      decider: Testing.decider(),
      model: Testing.model((prompt) => {
        if (!prompt.includes("What would a good therapist")) return undefined
        prompts.push(prompt)
        return {
          items: [
            {
              text: prompt.includes("CANCELLED") ? "The ceramics class was CANCELLED." : "The class was discussed.",
              kind: "event",
            },
          ],
        }
      }),
    })
    const turns = [
      turn("ordinary", "background ".repeat(500) + "TAIL_AFTER_4000"),
      turn("long", "detail ".repeat(2500) + "CANCELLED"),
    ]
    const result = await memory.ingest({ person: "sam", session: "class", turns })
    expect(result.turns).toBe(2)
    expect(prompts.some((x) => x.includes("TAIL_AFTER_4000"))).toBe(true)
    expect(prompts.some((x) => x.includes("CANCELLED"))).toBe(true)
    const recall = await memory.recall({
      person: "sam",
      conversation: [turn("ask", "Was the ceramics class CANCELLED?")],
    })
    expect(recall.statements.some((x) => x.text.includes("CANCELLED"))).toBe(true)
    const windows = await store.windows.session("sam", "class")
    const long = windows.filter((x) => x.turns.includes("long"))
    expect(long.length).toBeGreaterThan(1)
    await memory.forget({ person: "sam", windows: [long.at(-1)!.id] })
    expect((await store.windows.session("sam", "class")).every((x) => !x.turns.includes("long"))).toBe(true)
    expect((await store.log.session("sam", "class")).some((x) => x.id === "long")).toBe(false)
    expect((await memory.ingest({ person: "sam", session: "class", turns })).turns).toBe(0)
  })

  test("a failed late chunk keeps all source turns retryable", async () => {
    const store = Local.store()
    let fail = true
    const memory = Memory.create({
      store,
      ids: Testing.ids(),
      embedder: Testing.embedder(),
      decider: Testing.decider(),
      model: Testing.model((prompt) => {
        if (!prompt.includes("What would a good therapist")) return undefined
        if (prompt.includes("FINAL") && fail) throw new Error("late chunk failed")
        return { items: [] }
      }),
    })
    const input = { person: "sam", session: "long", turns: [turn("source", "detail ".repeat(3000) + "FINAL")] }
    await expect(memory.ingest(input)).rejects.toThrow("late chunk failed")
    expect(await store.turns.seen("sam", ["source"])).toEqual([])
    expect(await store.windows.all("sam")).toEqual([])
    fail = false
    expect((await memory.ingest(input)).turns).toBe(1)
  })
})
