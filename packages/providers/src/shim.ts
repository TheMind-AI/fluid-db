import type { Choice, Decider, LanguageModel } from "@fluiddb/core"
import { z } from "zod"

export const Output = z.strictObject({
  yes_no: z.array(
    z.strictObject({
      key: z.string(),
      yes: z.boolean(),
      probability: z.number().describe("your probability (0-1) that the answer is yes"),
    }),
  ),
  choices: z.array(
    z.strictObject({
      key: z.string(),
      choice: z.string(),
      probabilities: z.array(z.strictObject({ option: z.string(), p: z.number() })),
    }),
  ),
})
type Output = z.infer<typeof Output>

// The lab's prompt for an LLM answering Jev's questions (deprecated/python/lab/common/jev.py ask_llm), word for word.
export function prompt(
  state: Record<string, unknown>,
  yes: Record<string, string>,
  choices: Record<string, { question: string; options: Record<string, string> }>,
) {
  const yn = Object.entries(yes).map(([key, q]) => `- ${key}: ${q}`)
  const ch = Object.entries(choices).map(
    ([key, x]) =>
      `- ${key}: ${x.question}\n` +
      Object.entries(x.options)
        .map(([option, text]) => `    - ${option}: ${text}`)
        .join("\n"),
  )
  return (
    "Answer typed questions about the JSON STATE below, as a careful classifier. Names in backticks refer to fields " +
    "of STATE.\n- Yes/no questions: answer yes or no, with your probability (0-1) that the answer is yes.\n- Choice " +
    "questions: choose exactly one listed option, copying its name exactly, and give a probability for every option " +
    "(they sum to 1).\n\nSTATE:\n" +
    JSON.stringify(state, null, 1) +
    "\n\nYES/NO QUESTIONS (key: question):\n" +
    (yn.join("\n") || "(none)") +
    "\n\nCHOICE QUESTIONS (key: question; then its options as name: description):\n" +
    (ch.join("\n") || "(none)")
  )
}

const norm = (x: unknown) =>
  String(x ?? "")
    .trim()
    .replace(/^`+|`+$/g, "")
    .toLowerCase()
const round = (x: number) => Math.round(x * 10_000) / 10_000

// Answers by key: exact, then ignoring case and backticks, then by position when the counts match (LLMs renumber keys).
export function byKey<T extends { key: string }>(keys: string[], items: T[]) {
  const found = new Map(items.map((x) => [norm(x.key), x]))
  const out = new Map(keys.flatMap((k) => (found.has(norm(k)) ? [[k, found.get(norm(k))!] as const] : [])))
  if (out.size < keys.length && items.length === keys.length) {
    keys.forEach((k, i) => {
      if (!out.has(k)) out.set(k, items[i]!)
    })
  }
  return out
}

// The probability made to agree with the stated answer: LLMs often report confidence in their own answer.
export function yesProbability(answer: Output["yes_no"][number] | undefined) {
  if (!answer) return 0.5
  const p = Math.min(1, Math.max(0, answer.probability))
  return (answer.yes && p >= 0.5) || (!answer.yes && p <= 0.5) ? p : 1 - p
}

// A choice's probabilities over the listed options, summing to 1; the choice is the named option, else the likeliest.
export function choice(options: string[], answer: Output["choices"][number] | undefined): Choice {
  const sums = new Map(options.map((x) => [x, 0]))
  for (const x of answer?.probabilities ?? []) {
    const option = options.find((o) => norm(o) === norm(x.option))
    if (option !== undefined) sums.set(option, sums.get(option)! + Math.max(0, x.p))
  }
  const total = [...sums.values()].reduce((a, b) => a + b, 0)
  const probabilities = Object.fromEntries(
    options.map((x) => [x, round(total > 0 ? sums.get(x)! / total : 1 / options.length)]),
  )
  const named = options.find((o) => norm(o) === norm(answer?.choice))
  const likeliest = options.reduce((best, x) => (probabilities[x]! > probabilities[best]! ? x : best), options[0]!)
  return { choice: named ?? likeliest, probabilities }
}

// @ref LLP 0018#processors — Jev's questions answered by a language model, in Jev's answer shape, for data that may not
// go to TypeSafe. Its probabilities are stated, not calibrated like Jev's (LLP 0013.000).
export function decider(model: LanguageModel): Decider {
  return {
    id: `shim:${model.id}`,
    processors: model.processors,
    yes: async (state, questions, call) => {
      const out = await model.object(prompt(state, questions, {}), Output, call)
      const found = byKey(Object.keys(questions), out.yes_no)
      return Object.fromEntries(Object.keys(questions).map((key) => [key, round(yesProbability(found.get(key)))]))
    },
    choose: async (state, question, options, call) => {
      const out = await model.object(prompt(state, {}, { pick: { question, options } }), Output, call)
      return choice(Object.keys(options), byKey(["pick"], out.choices).get("pick"))
    },
  }
}

export * as Shim from "./shim"
