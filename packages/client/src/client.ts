import { Api, Dossier, Id, type Turn } from "@fluiddb/schema"
import type { Call, Person } from "@fluiddb/core"
import type { z } from "zod"

export interface Options {
  url: string
  token: string
  fetch?: typeof fetch
  /** Deadline for each HTTP request, including ingestion. Defaults to 120 seconds. */
  timeoutMs?: number
}

export class FluidError extends Error {
  override readonly name = "FluidError"
  constructor(
    readonly status: number,
    readonly kind: string,
    message: string,
  ) {
    super(message)
  }
}

export function create(options: Options) {
  const url = new URL(options.url)
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash)
    throw new Error("FluidDB requires an HTTP(S) base URL without credentials, query or fragment")
  if (!options.token.trim()) throw new Error("A nonempty FluidDB API token is required")
  const timeoutMs = options.timeoutMs ?? 120_000
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1) throw new Error("timeoutMs must be a positive integer")
  const base = url.href.replace(/\/+$/, "")
  const send = async <T>(
    method: string,
    path: string,
    schema: z.ZodType<T>,
    body?: unknown,
    call?: Call,
  ): Promise<T> => {
    const deadline = AbortSignal.timeout(timeoutMs)
    const signal = call?.signal ? AbortSignal.any([call.signal, deadline]) : deadline
    signal.throwIfAborted()
    const response = await (options.fetch ?? fetch)(`${base}${path}`, {
      method,
      signal,
      redirect: "manual",
      headers: { authorization: `Bearer ${options.token}`, "content-type": "application/json" },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    })
    if (response.status >= 300 && response.status < 400)
      throw new FluidError(response.status, "UnexpectedRedirect", "FluidDB redirects are not permitted")
    const json: unknown = await response.json().catch(() => undefined)
    signal.throwIfAborted()
    if (!response.ok) {
      const failure = Api.Failure.safeParse(json)
      if (failure.success) throw new FluidError(response.status, failure.data.error.name, failure.data.error.message)
      throw new FluidError(response.status, "HttpError", `the API answered ${response.status}`)
    }
    const result = schema.safeParse(json)
    if (!result.success)
      throw new FluidError(response.status, "InvalidResponse", "the API returned an invalid response")
    return result.data
  }
  const path = (id: string) => `/v1/people/${encodeURIComponent(Id.Info.parse(id))}`
  const turns = (id: string, session: string) =>
    `${path(id)}/sessions/${encodeURIComponent(Id.Info.parse(session))}/turns`
  const api = {
    accept: (id: string, session: string, list: Turn.Info[], call?: Call) =>
      send("POST", turns(id, session), Api.Accepted, Api.Ingest.parse({ turns: list }), call),
    ingest: (id: string, session: string, list: Turn.Info[], call?: Call) =>
      send("POST", `${turns(id, session)}?wait=true`, Api.Ingested, Api.Ingest.parse({ turns: list }), call),
    recall: (id: string, ask: Api.Ask, call?: Call) =>
      send("POST", `${path(id)}/recall`, Api.Answer, Api.Ask.parse(ask), call),
    dossier: async (id: string, call?: Call): Promise<Dossier.Info | undefined> => {
      try {
        return await send("GET", `${path(id)}/dossier`, Dossier.Info, undefined, call)
      } catch (error) {
        if (error instanceof FluidError && error.status === 404 && error.kind === "NotFound") return undefined
        throw error
      }
    },
    remember: (id: string, request: Api.Remember, call?: Call) =>
      send("POST", `${path(id)}/remember`, Api.Remembered, Api.Remember.parse(request), call),
    inspect: (id: string, request: Api.Inspect = {}, call?: Call) =>
      send("POST", `${path(id)}/inspect`, Api.Page, Api.Inspect.parse(request), call),
    evidence: (id: string, request: Api.Evidence, call?: Call) =>
      send("POST", `${path(id)}/evidence`, Api.Sources, Api.Evidence.parse(request), call),
    source: (id: string, request: Api.Source, call?: Call) =>
      send("POST", `${path(id)}/source`, Api.SourcePage, Api.Source.parse(request), call),
    previewForget: (id: string, request: Api.Forget, call?: Call) =>
      send("POST", `${path(id)}/forget/preview`, Api.ForgetPreview, Api.Forget.parse(request), call),
    forget: (id: string, request: Api.Forget, call?: Call) =>
      send("POST", `${path(id)}/forget`, Api.Forgotten, Api.Forget.parse(request), call),
    erase: async (id: string, call?: Call) => {
      await send("DELETE", path(id), Api.Erased, undefined, call)
    },
    status: (id: string, call?: Call) => send("GET", `${path(id)}/status`, Api.Status, undefined, call),
    retry: (id: string, call?: Call) => send("POST", `${path(id)}/retry`, Api.Accepted, undefined, call),
  }
  return {
    ...api,
    person(id: string): Person.Service {
      Id.Info.parse(id)
      return {
        accept: (session, turns, call) => api.accept(id, session, turns, call),
        ingest: (session, turns, call) => api.ingest(id, session, turns, call),
        recall: (ask, call) => api.recall(id, ask, call),
        dossier: (call) => api.dossier(id, call),
        remember: (request, call) => api.remember(id, request, call),
        inspect: (request, call) => api.inspect(id, request, call),
        evidence: (request, call) => api.evidence(id, request, call),
        source: (request, call) => api.source(id, request, call),
        previewForget: (request, call) => api.previewForget(id, request, call),
        forget: (request, call) => api.forget(id, request, call),
        status: (call) => api.status(id, call),
        retry: (call) => api.retry(id, call),
      }
    },
  }
}
export type Client = ReturnType<typeof create>
export * as Client from "./client"
