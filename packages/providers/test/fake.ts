// A fetch that answers from a script and records each request's URL, headers and body.
export function fake(answer: (body: any, n: number) => Response | Promise<Response>) {
  const requests: { url: string; headers: Record<string, string>; body: any }[] = []
  const fetch = (async (url: string | URL | Request, init?: RequestInit) => {
    const body = JSON.parse(String(init?.body))
    requests.push({ url: String(url), headers: init?.headers as Record<string, string>, body })
    return answer(body, requests.length)
  }) as typeof globalThis.fetch
  return { fetch, requests }
}

export const json = (value: unknown, status = 200, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(value), { status, headers: { "content-type": "application/json", ...headers } })

export const completion = (content: string, extra: Record<string, unknown> = {}) =>
  json({
    choices: [{ message: { content, refusal: null }, finish_reason: "stop", ...extra }],
    usage: { prompt_tokens: 100, completion_tokens: 20 },
  })

export const instant = { sleep: async () => {} }
