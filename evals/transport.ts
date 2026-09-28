import { createHash } from "node:crypto"
import { mkdir, readFile, rename, writeFile } from "node:fs/promises"
import path from "node:path"
import { z } from "zod"
import type { Config } from "./schema"

export class EvaluationError extends Error {}

const Usage = z.object({
  prompt_tokens: z.number().nonnegative().optional(),
  completion_tokens: z.number().nonnegative().optional(),
  total_tokens: z.number().nonnegative().optional(),
  input_tokens: z.number().nonnegative().optional(),
})

// @ref LLP 0024#cost-and-caching — cache-only by default; reserve before awaiting so concurrent calls share one cap
export function transport(options: {
  config: Config
  cache: string
  live: boolean
  budget: number
  network?: typeof fetch
}) {
  const { config } = options
  if (options.live && process.env.LAB_CACHE_ONLY) throw new EvaluationError("LAB_CACHE_ONLY forbids live evaluation")
  if (
    options.live &&
    (!config.prices ||
      Object.values(config.models ?? {}).some((model) => !model.prices) ||
      !Number.isFinite(options.budget) ||
      options.budget <= 0)
  ) {
    throw new EvaluationError("Live evaluation requires explicit positive token prices and a positive --budget in USD")
  }
  const stats = {
    cached: 0,
    requests: 0,
    reservedUsd: 0,
    estimatedUsd: 0,
    unknownUsage: 0,
    estimatedUncachedUsd: 0,
    unpricedResponses: 0,
    networkMs: 0,
  }
  let failure: string | undefined
  let stopped = false
  const controller = new AbortController()
  const pending = new Map<string, Promise<unknown>>()
  const send = async (input: Parameters<typeof fetch>[0], init?: RequestInit) => {
    if (controller.signal.aborted) throw new EvaluationError("Evaluation stopped")
    const url = String(input)
    if (
      !(
        /^https:\/\/api\.openai\.com\/v1\/(chat\/completions|embeddings)$/.test(url) ||
        (config.jev && url === "https://openrouter.ai/api/v1/systemone")
      ) ||
      init?.method !== "POST" ||
      typeof init.body !== "string"
    ) {
      throw new EvaluationError("Evaluation transport accepts only configured OpenAI and Jev JSON requests")
    }
    const body = init.body
    const request = JSON.parse(body)
    const embedding = url.endsWith("/embeddings")
    const jev = url === "https://openrouter.ai/api/v1/systemone"
    const priced = [config, ...Object.values(config.models ?? {})].find(
      (model) => request.model === model.model && request.max_completion_tokens === model.maxOutputTokens,
    )
    if (jev ? request.model !== config.jev?.model : embedding ? request.model !== config.embedding : !priced)
      throw new EvaluationError("Request model or token limit does not match the priced evaluation config")
    const inputRate = jev ? config.jev!.inputPrice : embedding ? config.prices?.embedding : priced?.prices?.input
    const outputRate = jev || embedding ? 0 : priced?.prices?.output
    const costOf = (raw: unknown) => {
      const usage = Usage.safeParse(raw && typeof raw === "object" && "usage" in raw ? raw.usage : undefined)
      const input = usage.success
        ? jev
          ? usage.data.input_tokens
          : embedding
            ? usage.data.total_tokens
            : usage.data.prompt_tokens
        : undefined
      const output = embedding || jev ? 0 : usage.success ? usage.data.completion_tokens : undefined
      if (input === undefined || output === undefined || inputRate === undefined || outputRate === undefined)
        return undefined
      return (input * inputRate + output * outputRate) / 1e6
    }
    const key = createHash("sha256").update(`v1\n${url}\n${body}`).digest("hex")
    const file = path.join(options.cache, `${key}.json`)
    const run = async () => {
      try {
        const cached: unknown = JSON.parse(await readFile(file, "utf8"))
        stats.cached++
        const cost = costOf(cached)
        if (cost === undefined) stats.unpricedResponses++
        else stats.estimatedUncachedUsd += cost
        return cached
      } catch (error) {
        if (!(error && typeof error === "object" && "code" in error && error.code === "ENOENT"))
          throw new EvaluationError("Unreadable evaluation cache entry")
      }
      if (!options.live) throw new EvaluationError("Evaluation cache miss; no request sent (live mode is opt-in)")
      const reservation =
        ((2 * Buffer.byteLength(body, "utf8") + 4_096) * inputRate! +
          (embedding || jev ? 0 : priced!.maxOutputTokens * outputRate!)) /
        1e6
      if (stopped || stats.reservedUsd + reservation > options.budget)
        throw new EvaluationError("Evaluation budget exhausted before request")
      stats.reservedUsd += reservation
      stats.requests++
      stats.unknownUsage++
      // Even a failed/ambiguous request retains its reservation; no retries can silently spend it twice.
      const started = performance.now()
      const response = await (options.network ?? fetch)(url, {
        ...init,
        redirect: "manual",
        signal: AbortSignal.any([controller.signal, ...(init.signal ? [init.signal] : [])]),
      })
      if (!response.ok) {
        throw new EvaluationError(`Evaluation provider returned HTTP ${response.status}`)
      }
      const raw: unknown = await response.json()
      stats.networkMs += performance.now() - started
      await mkdir(options.cache, { recursive: true, mode: 0o700 })
      const temp = `${file}.${crypto.randomUUID()}.tmp`
      await writeFile(temp, JSON.stringify(raw), { mode: 0o600 })
      await rename(temp, file)
      const cost = costOf(raw)
      if (cost !== undefined) {
        stats.unknownUsage--
        stats.estimatedUsd += cost
        stats.estimatedUncachedUsd += cost
        if (cost > reservation) {
          stats.reservedUsd += cost - reservation
          stopped = true
          throw new EvaluationError(
            "Provider usage exceeded the conservative reservation; further live requests stopped",
          )
        }
      } else stats.unpricedResponses++
      return raw
    }
    let task = pending.get(key)
    if (!task) {
      task = run()
        .catch((error) => {
          failure ??= error instanceof EvaluationError ? error.message : "Evaluation provider or cache request failed"
          throw error
        })
        .finally(() => pending.delete(key))
      pending.set(key, task)
    }
    return Response.json(await task)
  }
  return {
    fetch: Object.assign(send, { preconnect: fetch.preconnect }) as typeof fetch,
    stats,
    async close() {
      stopped = true
      controller.abort()
      await Promise.allSettled([...pending.values()])
    },
    get failure() {
      return failure
    },
  }
}
