import { z } from "zod"
import type { Call, Decider, LanguageModel, Picker, Kind } from "./port"
import { Prompt } from "./prompt"

export interface Item {
  id: string
  text: string
}

export interface Input {
  moment: string
  items: Item[]
  top: number
  // Each candidate is shown up to this many characters.
  chars: number
}

// @ref LLP 0021#part-a-a-better-picker — one closed question per candidate, on the whole text (cuts hid the fact: +4.8)
export async function decider(decider: Decider, input: Input, call?: Call): Promise<string[]> {
  if (!input.items.length) return []
  const excerpts = Object.fromEntries(input.items.map((x, i) => [`e${i}`, x.text.slice(0, input.chars)]))
  const questions = Object.fromEntries(input.items.map((_, i) => [`r${i}`, Prompt.pick.replace("{key}", `e${i}`)]))
  const out = await decider.yes({ moment: input.moment, excerpts }, questions, call)
  return input.items
    .map((x, i) => ({ id: x.id, p: out[`r${i}`] ?? 0, i }))
    .toSorted((a, b) => b.p - a.p || a.i - b.i)
    .slice(0, input.top)
    .map((x) => x.id)
}

export const Ranked = z.strictObject({ top: z.array(z.int()) })

// The model ranks the candidates as a list (P3). Numbers it leaves out follow in search order.
export async function model(model: LanguageModel, input: Input & { role: string }, call?: Call): Promise<string[]> {
  if (!input.items.length) return []
  const texts = input.items.map((x) => x.text.slice(0, input.chars))
  const out = await model.object(
    Prompt.rank({ role: input.role, moment: input.moment, texts, top: input.top }),
    Ranked,
    call,
  )
  const order = [...new Set(out.top.filter((n) => n >= 1 && n <= texts.length).map((n) => n - 1))]
  const rest = input.items.map((_, i) => i).filter((i) => !order.includes(i))
  return [...order, ...rest].slice(0, input.top).map((i) => input.items[i]!.id)
}

// @ref LLP 0024.001#component-boundary — exchange the picker independently of extraction and detection
export function usingDecider(provider: Decider): Picker {
  return {
    id: `pick:${provider.id}`,
    processors: provider.processors,
    pick: (input, call) => decider(provider, input, call),
  }
}

export function usingModel(provider: LanguageModel, role = "therapy assistant"): Picker {
  return {
    id: `pick:${provider.id}`,
    processors: provider.processors,
    pick: (input, call) => model(provider, { ...input, role }, call),
  }
}

/** A no-reranking baseline: candidates arrive in vector-search order. */
export function searchOrder(): Picker {
  return {
    id: "pick:search-order",
    processors: [],
    pick: async ({ items, top }) => items.slice(0, top).map((x) => x.id),
  }
}

/** Route each candidate kind independently; both statement and window searches still run. */
export function route(routes: Record<Kind, Picker>): Picker {
  return {
    id: `route:statement=${routes.statement.id};window=${routes.window.id}`,
    processors: [...new Set(Object.values(routes).flatMap((picker) => [...picker.processors]))],
    pick: (input, call) => routes[input.kind].pick(input, call),
  }
}

export * as Pick from "./pick"
