import { Memory, type Logger } from "@fluiddb/core"
import { Jev, OpenAI, Shim } from "@fluiddb/providers"
import { z } from "zod"

const list = z
  .string()
  .transform((x) =>
    x
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean),
  )
  .pipe(z.array(z.string()).min(1))

// The Worker's variables and secrets. Messages about them name the variable, never its value.
export const Vars = z
  .object({
    FLUID_TOKEN: z.string().min(16, "FLUID_TOKEN must be at least 16 characters"),
    OPENAI_API_KEY: z.string().min(1),
    OPENROUTER_API_KEY: z.string().min(1).optional(),
    // Where people's data may go (LLP 0018#processors). Required: there is no default for a privacy decision.
    PROCESSORS: list,
    // "jev" decides through Jev (OpenRouter, TypeSafe); "model" has the model answer Jev's questions.
    DECIDER: z.enum(["jev", "model"]).default("model"),
    PICK: z.enum(["decider", "model"]).default("decider"),
    MODEL: z.string().min(1).default("gpt-6-luna"),
    EMBEDDING_MODEL: z.string().min(1).default("text-embedding-3-small"),
    ASSISTANT: z.string().min(1).default(Memory.defaults.assistant),
    ROLE: z.string().min(1).default(Memory.defaults.role),
    THRESHOLD: z.coerce.number().min(0).max(1).default(Memory.defaults.threshold),
    INGEST_DELAY: z.coerce.number().int().min(0).default(300),
  })
  .refine((x) => x.DECIDER !== "jev" || x.OPENROUTER_API_KEY, {
    message: "DECIDER=jev needs OPENROUTER_API_KEY",
    path: ["OPENROUTER_API_KEY"],
  })
export type Vars = z.input<typeof Vars>
export type Info = z.output<typeof Vars>

const parsed = new WeakMap<object, Info>()

export function load(env: object): Info {
  const found = parsed.get(env)
  if (found) return found
  const info = Vars.parse(env)
  parsed.set(env, info)
  return info
}

// The providers and settings a memory is made of. Token usage is logged per call, with the model's name only.
export function memory(config: Info, logger: Logger, fetch?: typeof globalThis.fetch) {
  const usage = (x: { model: string; input: number; output: number }) => logger.event("usage", x)
  const model = OpenAI.model({ apiKey: config.OPENAI_API_KEY, model: config.MODEL, usage, fetch })
  const embedder = OpenAI.embedder({ apiKey: config.OPENAI_API_KEY, model: config.EMBEDDING_MODEL, usage, fetch })
  const decider =
    config.DECIDER === "jev"
      ? Jev.decider({ apiKey: config.OPENROUTER_API_KEY ?? "", usage, fetch })
      : Shim.decider(model)
  return {
    model,
    embedder,
    decider,
    processors: config.PROCESSORS,
    logger,
    options: { assistant: config.ASSISTANT, role: config.ROLE, pick: config.PICK, threshold: config.THRESHOLD },
  }
}

export * as Config from "./config"
