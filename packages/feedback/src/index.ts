import { z } from "zod"

const text = (max: number) => z.string().trim().min(1).max(max)
const Thread = z.string().regex(/^[A-Za-z0-9_-]{6,64}$/)
const Key = z.string().regex(/^[A-Za-z0-9_-]{8,64}$/)
export const Category = z.enum(["tool", "skill", "prompt", "docs", "mcp", "cli", "api", "model", "ux", "other"])
export const Evaluation = z.strictObject({
  task: text(2000),
  expected: text(2000),
  actual: text(2000),
  mistake: text(2000).optional(),
  attempts: z.number().int().min(1).max(1000).optional(),
})
export const MemoryCase = z.strictObject({
  operation: z.enum(["ingest", "recall", "remember", "inspect", "evidence", "dossier", "forget"]),
  challenge: z
    .enum(["temporal", "correction", "multi-session", "long-context", "provenance", "repetition", "isolation", "other"])
    .optional(),
  adapter: z.enum(["sqlite", "postgres", "firestore", "custom"]).optional(),
  turns: z.number().int().min(0).max(1_000_000).optional(),
  statements: z.number().int().min(0).max(1_000_000).optional(),
})
export const Report = z.strictObject({
  feedback: text(5000),
  category: Category.optional(),
  subject: text(500).optional(),
  eval: Evaluation.optional(),
  memoryCase: MemoryCase.optional(),
  resume: Thread.optional(),
  question: text(64).optional(),
  idempotencyKey: Key.optional(),
})
export type Report = z.infer<typeof Report>

export const Receipt = z.object({
  id: text(64),
  thread: Thread,
  resume: z.string().max(512).optional(),
  guidance: z.string().max(1000).optional(),
  ask: z.object({ id: text(64), prompt: z.string().max(500), command: z.string().max(512) }).optional(),
  known_issue: z
    .object({
      title: z.string().max(120),
      status: z.enum(["open", "wont_fix"]),
      reopened: z.boolean().optional(),
      reports: z.number().int().positive(),
      note: z.string().max(1000).optional(),
      hint: z.string().max(300),
    })
    .optional(),
  duplicate: z.boolean().optional(),
  idempotencyKey: Key,
})
export type Receipt = z.infer<typeof Receipt>
export interface Service {
  submit(report: Report, call?: { signal?: AbortSignal }): Promise<Receipt>
}
export interface Options {
  clientName?: string
  clientVersion?: string
  fetch?: (url: string, init: RequestInit) => Promise<Response>
  timeoutMs?: number
}
export const descriptor = Object.freeze({
  v: 1 as const,
  name: "FluidDB",
  slug: "fluiddb",
  endpoint: "https://hivenet.app/v1/feedback",
})

export class FeedbackError extends Error {
  override readonly name = "FeedbackError"
  constructor(
    message: string,
    readonly thread: string,
    readonly idempotencyKey: string,
    readonly status?: number,
  ) {
    super(message)
  }
}

async function responseBody(response: Response) {
  const reader = response.body?.getReader()
  if (!reader) throw new Error("Missing response")
  const decoder = new TextDecoder()
  let bytes = 0,
    body = ""
  try {
    while (true) {
      const part = await reader.read()
      if (part.done) break
      bytes += part.value.byteLength
      if (bytes > 65_536) throw new Error("Response too large")
      body += decoder.decode(part.value, { stream: true })
    }
    return JSON.parse(body + decoder.decode()) as unknown
  } finally {
    await reader.cancel()
  }
}

// @ref LLP 0023.002#feedback-boundary — explicit reports only, independent of memory and environment
export function create(options: Options = {}): Service {
  const client = z.strictObject({ name: text(64), version: text(32) }).parse({
    name: options.clientName ?? "fluiddb-sdk",
    version: options.clientVersion ?? "1.0.0-next.4",
  })
  const timeoutMs = options.timeoutMs ?? 10_000
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1) throw new Error("Feedback timeout must be a positive integer")
  return {
    async submit(input, call) {
      const report = Report.parse(input)
      if (report.question && !report.resume)
        throw new Error("Answering a feedback question requires its thread in resume")
      const thread = report.resume ?? crypto.randomUUID().replaceAll("-", "")
      const idempotencyKey = report.idempotencyKey ?? crypto.randomUUID().replaceAll("-", "")
      const signal = AbortSignal.any([AbortSignal.timeout(timeoutMs), ...(call?.signal ? [call.signal] : [])])
      const failure = (message: string, status?: number) => new FeedbackError(message, thread, idempotencyKey, status)
      signal.throwIfAborted()
      const event = {
        v: 1,
        to: descriptor.slug,
        feedback: report.feedback,
        category: report.category ?? "api",
        subject: report.subject,
        eval: report.eval,
        question: report.question,
        thread: { id: thread },
        idempotencyKey,
        client,
        consent: { telemetry: false },
        ...(report.memoryCase
          ? {
              metadata: Object.fromEntries(
                Object.entries(report.memoryCase).map(([key, value]) => [`memory_${key}`, value]),
              ),
            }
          : {}),
      }
      let response: Response
      try {
        response = await (options.fetch ?? fetch)(descriptor.endpoint, {
          method: "POST",
          headers: { "content-type": "application/json" },
          redirect: "manual",
          body: JSON.stringify(event),
          signal,
        })
      } catch {
        throw failure("Feedback delivery is unconfirmed. Retry the same report with this thread and idempotency key.")
      }
      if (!response.ok) {
        await response.body?.cancel()
        throw failure(
          "HiveNet did not confirm delivery. Retry the same report with this thread and idempotency key.",
          response.status,
        )
      }
      try {
        const body = await responseBody(response)
        const receipt = Receipt.parse({ ...(body as object), idempotencyKey })
        if (receipt.thread !== thread) throw new Error("Wrong thread")
        return receipt
      } catch {
        throw failure(
          "HiveNet returned an invalid receipt. Delivery is unconfirmed; retain the thread and idempotency key.",
          response.status,
        )
      }
    },
  }
}

export * as Feedback from "./index"
