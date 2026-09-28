import type { Group, Statement, Turn } from "@fluiddb/schema"
import type { Choice, Decider, LanguageModel } from "../src/port"

export const day = (date: string, time = "09:00") => `${date}T${time}:00.000Z`

export const turn = (id: string, role: Turn.Role, text: string, at: string): Turn.Info => ({ id, role, text, at })

export const statement = (id: string, text: string, group: string, at = day("2026-03-01")): Statement.Info => ({
  id,
  person: "p1",
  session: "s1",
  window: "w1",
  text,
  kind: "life",
  at,
  group,
})

export const group = (id: string, count = 1, at = day("2026-03-01")): Group.Info => ({
  id,
  person: "p1",
  count,
  first: at,
  last: at,
  replaces: [],
})

// A decider that answers from a script and records what it was asked.
export function scripted(script: {
  yes?: (state: Record<string, unknown>, questions: Record<string, string>) => Record<string, number>
  choose?: (state: Record<string, unknown>, options: Record<string, string>) => Choice
}) {
  const asked: { state: Record<string, unknown>; question: string; options?: Record<string, string> }[] = []
  const decider: Decider = {
    id: "test:scripted",
    processors: ["test"],
    yes: async (state, questions) => {
      asked.push({ state, question: Object.values(questions).join("\n") })
      return script.yes?.(state, questions) ?? {}
    },
    choose: async (state, question, options) => {
      asked.push({ state, question, options })
      return script.choose?.(state, options) ?? { choice: "none", probabilities: {} }
    },
  }
  return { decider, asked }
}

// A model that answers structured prompts from a script and records the prompts.
export function answering(answer: (prompt: string) => unknown) {
  const prompts: string[] = []
  const model: LanguageModel = {
    id: "test:answering",
    processors: ["test"],
    text: async (prompt) => {
      prompts.push(prompt)
      return String(answer(prompt))
    },
    object: async (prompt, schema) => {
      prompts.push(prompt)
      return schema.parse(answer(prompt))
    },
  }
  return { model, prompts }
}
