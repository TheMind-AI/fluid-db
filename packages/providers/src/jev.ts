import { Limit, OutputError, type Call, type Decider } from "@fluiddb/core"
import { z } from "zod"
import { Http } from "./http"

export interface Options extends Http.Options {
  apiKey: string
  model?: string
  url?: string
  // Questions per request; larger sets are split and asked in parallel (the lab split at 64).
  chunk?: number
  usage?: (usage: { model: string; input: number; output: number }) => void
}

type Question =
  { type: "noul"; instructions: string } | { type: "choice"; instructions: string; criteria: Record<string, string> }

const Answer = z.discriminatedUnion("type", [
  z.object({ type: z.literal("noul"), noul: z.number().min(0).max(1) }),
  z.object({ type: z.literal("choice"), choice: z.string(), probabilities: z.record(z.string(), z.number()) }),
])
const Response = z.object({
  answers: z.record(z.string(), Answer),
  usage: z.object({ input_tokens: z.number() }).nullish(),
})

// @ref LLP 0008 — Jev (TypeSafe's System One model) through OpenRouter: a JSON state and typed questions in, calibrated
// probabilities out. The data reaches OpenRouter and TypeSafe (deprecated/python/lab/common/jev.py).
export function decider(options: Options): Decider {
  const model = options.model ?? "typesafe/jev-1.13"
  const id = `jev:${model}`
  const url = options.url ?? "https://openrouter.ai/api/v1/systemone"
  const ask = async (state: Record<string, unknown>, questions: Record<string, Question>, call?: Call) => {
    const raw = await Http.post(
      id,
      url,
      {
        headers: { authorization: `Bearer ${options.apiKey}` },
        body: { model, state, questions },
        signal: call?.signal,
      },
      options,
    )
    const out = Response.safeParse(raw)
    if (!out.success) throw new OutputError(id, "the response is not a set of answers")
    if (out.data.usage) options.usage?.({ model, input: out.data.usage.input_tokens, output: 0 })
    return out.data.answers
  }
  return {
    id,
    processors: ["openrouter", "typesafe"],
    yes: async (state, questions, call) => {
      const entries = Object.entries(questions)
      const parts = await Promise.all(
        Limit.chunks(entries, options.chunk ?? 64).map((part) =>
          ask(state, Object.fromEntries(part.map(([key, q]) => [key, { type: "noul", instructions: q }])), call),
        ),
      )
      const answers = Object.assign({}, ...parts) as Record<string, z.infer<typeof Answer>>
      return Object.fromEntries(
        entries.map(([key]) => {
          const answer = answers[key]
          if (answer?.type !== "noul") throw new OutputError(id, `no yes/no answer for ${key}`)
          return [key, answer.noul]
        }),
      )
    },
    choose: async (state, question, options, call) => {
      const answers = await ask(state, { pick: { type: "choice", instructions: question, criteria: options } }, call)
      const answer = answers.pick
      if (answer?.type !== "choice" || !(answer.choice in options))
        throw new OutputError(id, "no choice among the options")
      return { choice: answer.choice, probabilities: answer.probabilities }
    },
  }
}

export * as Jev from "./jev"
