import { Limit, OutputError, Vector, type Call, type Embedder, type LanguageModel } from "@fluiddb/core"
import { z } from "zod"
import { Http } from "./http"
import { Schema } from "./schema"

export interface Usage {
  model: string
  input: number
  output: number
}

export interface Options extends Http.Options {
  apiKey: string
  model: string
  // Any OpenAI-compatible API: OpenAI, OpenRouter, a gateway. Name who receives the data in `processors`.
  url?: string
  processors?: string[]
  usage?: (usage: Usage) => void
}

const Completion = z.object({
  choices: z
    .array(
      z.object({
        message: z.object({ content: z.string().nullish(), refusal: z.string().nullish() }),
        finish_reason: z.string().nullish(),
      }),
    )
    .min(1),
  usage: z.object({ prompt_tokens: z.number(), completion_tokens: z.number() }).nullish(),
})

// Chat completions, as the lab called them (deprecated/python/lab/common/llm.py): one user message, reasoning effort "low", and strict
// JSON schema output for objects. Malformed output is asked again up to `retries` times, as the lab did.
export function model(
  options: Options & {
    // null leaves reasoning effort out, for models that don't take it.
    effort?: string | null
    tokens?: number
    retries?: number
  },
): LanguageModel {
  const id = `openai:${options.model}`
  const url = `${options.url ?? "https://api.openai.com/v1"}/chat/completions`
  const complete = async (prompt: string, format: unknown, call?: Call) => {
    const raw = await Http.post(
      id,
      url,
      {
        headers: { authorization: `Bearer ${options.apiKey}` },
        body: {
          model: options.model,
          store: false,
          messages: [{ role: "user", content: prompt }],
          max_completion_tokens: options.tokens ?? 16_000,
          ...(options.effort === null ? {} : { reasoning_effort: options.effort ?? "low" }),
          ...(format ? { response_format: format } : {}),
        },
        signal: call?.signal,
      },
      options,
    )
    const out = Completion.safeParse(raw)
    if (!out.success) throw new OutputError(id, "the response is not a chat completion")
    const usage = out.data.usage
    if (usage) options.usage?.({ model: options.model, input: usage.prompt_tokens, output: usage.completion_tokens })
    const choice = out.data.choices[0]!
    if (choice.message.refusal) throw new OutputError(id, "the model refused")
    if (choice.finish_reason === "length") throw new OutputError(id, "the answer was cut off at the token limit")
    return choice.message.content ?? ""
  }
  return {
    id,
    processors: options.processors ?? ["openai"],
    text: (prompt, call) => complete(prompt, undefined, call),
    object: async <T>(prompt: string, schema: z.ZodType<T>, call?: Call) => {
      const format = {
        type: "json_schema",
        json_schema: { name: "output", schema: Schema.strict(schema), strict: true },
      }
      const tries = 1 + (options.retries ?? 2)
      for (const attempt of Array.from({ length: tries }, (_, i) => i + 1)) {
        const text = await complete(prompt, format, call)
        const out = schema.safeParse(json(text))
        if (out.success) return out.data
        if (attempt === tries) throw new OutputError(id, z.prettifyError(out.error).slice(0, 300))
      }
      throw new OutputError(id, "no attempt was made")
    },
  }
}

function json(text: string): unknown {
  try {
    return JSON.parse(text)
  } catch {
    return undefined
  }
}

const Embeddings = z.object({
  data: z.array(z.object({ index: z.number(), embedding: z.array(z.number()) })),
  usage: z.object({ total_tokens: z.number() }).nullish(),
})

// @ref LLP 0023#embedding-inputs — UTF-8 bytes bound byte-BPE tokens without a tokenizer dictionary in the Worker
function embeddingParts(text: string) {
  const bytes = new TextEncoder().encode(text || " ")
  const decoder = new TextDecoder("utf-8", { fatal: false, ignoreBOM: true })
  const parts: { text: string; bytes: number }[] = []
  for (let start = 0; start < bytes.length;) {
    let end = Math.min(start + 8_000, bytes.length)
    while (end < bytes.length && (bytes[end]! & 0xc0) === 0x80) end--
    parts.push({ text: decoder.decode(bytes.subarray(start, end)), bytes: end - start })
    start = end
  }
  return parts
}

export function embedder(options: Options & { dimensions?: number; batch?: number }): Embedder {
  const id = `openai:${options.model}`
  const url = `${options.url ?? "https://api.openai.com/v1"}/embeddings`
  const batch = Math.min(
    z
      .int()
      .min(1)
      .parse(options.batch ?? 64),
    37,
  )
  return {
    id,
    processors: options.processors ?? ["openai"],
    embed: async (texts, call) => {
      const parts = texts.map(embeddingParts)
      // 37 * 8,000 bytes is also below the provider's 300,000-token request limit.
      const batches = Limit.chunks(parts.flat(), batch)
      const out = await Limit.map(batches, 4, async (items) => {
        const input = items.map((item) => item.text)
        const raw = await Http.post(
          id,
          url,
          {
            headers: { authorization: `Bearer ${options.apiKey}` },
            body: { model: options.model, input, ...(options.dimensions ? { dimensions: options.dimensions } : {}) },
            signal: call?.signal,
          },
          options,
        )
        const parsed = Embeddings.safeParse(raw)
        if (!parsed.success || parsed.data.data.length !== input.length) {
          throw new OutputError(id, "the response does not have one embedding per text")
        }
        const tokens = parsed.data.usage?.total_tokens
        if (tokens !== undefined) options.usage?.({ model: options.model, input: tokens, output: 0 })
        const ordered = parsed.data.data.toSorted((a, b) => a.index - b.index)
        const dimensions = options.dimensions ?? ordered[0]?.embedding.length
        if (!dimensions || ordered.some((x, i) => x.index !== i || x.embedding.length !== dimensions)) {
          throw new OutputError(id, "embedding indices or dimensions do not match the request")
        }
        return ordered.map((x) => {
          const vector = Float32Array.from(x.embedding)
          if (!vector.every(Number.isFinite) || !vector.some((v) => v !== 0)) {
            throw new OutputError(id, "an embedding is not a finite nonzero vector")
          }
          return Vector.unit(vector)
        })
      })
      const vectors = out.flat()
      if (vectors.some((vector) => vector.length !== vectors[0]!.length))
        throw new OutputError(id, "embedding dimensions differ across batches")
      let offset = 0
      return parts.map((items) => {
        const slice = vectors.slice(offset, offset + items.length)
        offset += items.length
        if (slice.length === 1) return slice[0]!
        const sum = new Float32Array(slice[0]!.length)
        const total = items.reduce((n, item) => n + item.bytes, 0)
        for (const [i, vector] of slice.entries()) {
          const weight = items[i]!.bytes / total
          for (let j = 0; j < sum.length; j++) sum[j] = sum[j]! + vector[j]! * weight
        }
        if (!sum.every(Number.isFinite) || !sum.some((value) => value !== 0))
          throw new OutputError(id, "combined embedding is not a finite nonzero vector")
        return Vector.unit(sum)
      })
    },
  }
}

export * as OpenAI from "./openai"
