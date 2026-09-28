import { Pick, type Memory } from "@fluiddb/core"
import { OpenAI, Jev, Shim } from "@fluiddb/providers"
import { synthetic } from "./replay"
import type { Config } from "./schema"
import { EvaluationError } from "./transport"

export const variants = ["vector", "model", "decider", "jev", "routed"] as const
export type VariantName = (typeof variants)[number]

export function providers(config?: Config, fetch?: typeof globalThis.fetch): Omit<Memory.Deps, "store"> {
  if (!config || !fetch) return synthetic()
  const create = (settings: { model: string; maxOutputTokens: number; effort: Config["effort"] }) =>
    OpenAI.model({
      apiKey: process.env.OPENAI_API_KEY ?? "cache-only",
      model: settings.model,
      tokens: settings.maxOutputTokens,
      effort: settings.effort,
      fetch,
      attempts: 1,
      retries: 0,
    })
  const model = create(config)
  const models = Object.fromEntries(
    Object.entries(config.models ?? {})
      .filter(([stage]) => stage !== "detector")
      .map(([stage, settings]) => [stage, create(settings)]),
  )
  return {
    model,
    models,
    embedder: OpenAI.embedder({
      apiKey: process.env.OPENAI_API_KEY ?? "cache-only",
      model: config.embedding,
      fetch,
      attempts: 1,
    }),
    decider: Shim.decider(models.pick ?? model),
    detector: Shim.decider(config.models?.detector ? create(config.models.detector) : model),
    processors: ["openai"],
  }
}

// @ref LLP 0024.001#experiment-declared-before-calls — third-party variants require an explicitly fictional dataset
export function variant(
  name: VariantName,
  deps: Omit<Memory.Deps, "store">,
  options: {
    synthetic: boolean
    config?: Config
    fetch?: typeof globalThis.fetch
  },
): Omit<Memory.Deps, "store"> {
  const model = Pick.usingModel(deps.models?.pick ?? deps.model)
  if (name === "vector") return { ...deps, picker: Pick.searchOrder() }
  if (name === "model") return { ...deps, picker: model }
  if (name === "decider") return { ...deps, picker: Pick.usingDecider(deps.decider) }
  if (!options.synthetic) throw new EvaluationError("Jev variants require a dataset explicitly marked synthetic: true")
  if (!options.config?.jev || !options.fetch)
    throw new EvaluationError("Jev variants require a real provider and priced config.jev")
  const jev = Jev.decider({
    apiKey: process.env.OPENROUTER_API_KEY ?? "cache-only",
    model: options.config.jev.model,
    fetch: options.fetch,
    attempts: 1,
  })
  const picker = Pick.usingDecider(jev)
  return {
    ...deps,
    processors: ["openai", "openrouter", "typesafe"],
    detector: jev,
    picker: name === "jev" ? picker : Pick.route({ statement: model, window: picker }),
  }
}
