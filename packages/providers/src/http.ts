import { ProviderError } from "@fluiddb/core"

export interface Options {
  fetch?: typeof fetch
  // Attempts in all, for rate limits, server errors, timeouts and dropped connections.
  attempts?: number
  // Milliseconds per attempt.
  timeout?: number
  sleep?: (ms: number) => Promise<void>
}

const RETRY = new Set([408, 409, 425, 429, 500, 502, 503, 504, 529])

// The wait before attempt `attempt + 1`: what the provider asks for, else 1 s doubling to 30 s.
export function wait(attempt: number, after: string | null) {
  const seconds = after === null ? NaN : Number(after)
  if (Number.isFinite(seconds) && seconds >= 0) return Math.min(seconds * 1000, 60_000)
  const date = after === null ? NaN : Date.parse(after)
  if (Number.isFinite(date)) return Math.min(Math.max(0, date - Date.now()), 60_000)
  return Math.min(1000 * 2 ** (attempt - 1), 30_000)
}

// POSTs JSON and returns the parsed answer. Errors name the provider and the status, with at most 300 characters of
// the provider's answer, never the request: requests carry what people said.
export async function post(
  provider: string,
  url: string,
  request: { headers: Record<string, string>; body: unknown; signal?: AbortSignal },
  options: Options = {},
): Promise<unknown> {
  const send = options.fetch ?? fetch
  const attempts = Math.max(1, options.attempts ?? 5)
  const sleep = options.sleep ?? ((ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms)))
  const body = JSON.stringify(request.body)
  for (const attempt of Array.from({ length: attempts }, (_, i) => i + 1)) {
    const signal = AbortSignal.any([
      AbortSignal.timeout(options.timeout ?? 120_000),
      ...(request.signal ? [request.signal] : []),
    ])
    const result = await send(url, {
      method: "POST",
      headers: { "content-type": "application/json", ...request.headers },
      body,
      signal,
      redirect: "manual",
    }).then(
      (response) => ({ response }),
      (error: unknown) => ({ error }),
    )
    if ("error" in result) {
      if (request.signal?.aborted) throw result.error
      if (attempt === attempts) throw new ProviderError(provider, 0, errorName(result.error))
      await sleep(wait(attempt, null))
      continue
    }
    const { response } = result
    // @ref LLP 0023#processors — a redirect cannot add a recipient for private request bodies
    if (response.status >= 300 && response.status < 400) {
      await response.body?.cancel()
      throw new ProviderError(provider, response.status, "redirects are not permitted")
    }
    if (response.ok) return response.json()
    if (!RETRY.has(response.status) || attempt === attempts) {
      throw new ProviderError(provider, response.status, (await response.text()).slice(0, 300))
    }
    await sleep(wait(attempt, response.headers.get("retry-after")))
  }
  throw new ProviderError(provider, 0, "no attempt was made")
}

function errorName(error: unknown) {
  return error instanceof Error ? `${error.name}: ${error.message}`.slice(0, 300) : "the request failed"
}

export * as Http from "./http"
