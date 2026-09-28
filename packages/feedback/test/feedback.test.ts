import { expect, test } from "bun:test"
import { validateFeedbackEvent, validateIngestResponse } from "hivenet"
import { Feedback, FeedbackError } from "../src/index"

const report: Feedback.Report = {
  feedback: "Synthetic correction reproduction returned the old statement.",
  category: "mcp",
  subject: "fluiddb_recall",
  resume: "synthetic-thread",
  idempotencyKey: "synthetic-retry",
  eval: { task: "Save a synthetic correction and recall it", expected: "Newest fact", actual: "Old fact", attempts: 2 },
  memoryCase: { operation: "recall", challenge: "correction", adapter: "sqlite", turns: 4 },
}
const owner = {
  id: "event-test",
  thread: report.resume!,
  resume: "suggested command",
  guidance: "The team is investigating.",
  ask: { id: "question-1", prompt: "Did the fix work?", command: "untrusted command; never execute" },
  known_issue: {
    title: "Correction issue",
    status: "open" as const,
    reports: 2,
    reopened: true,
    hint: "Already recorded",
  },
}

test("explicit reports conform to HiveNet 0.6.0 and preserve owner replies without collecting context", async () => {
  let sent: any
  const service = Feedback.create({
    fetch: async (url, init) => {
      expect(url).toBe("https://hivenet.app/v1/feedback")
      expect(init!.redirect).toBe("manual")
      sent = JSON.parse(init!.body as string)
      expect(validateFeedbackEvent(sent).ok).toBe(true)
      expect(validateIngestResponse(owner).ok).toBe(true)
      return Response.json(owner, { status: 202 })
    },
  })
  const result = await service.submit(report)
  expect(result).toEqual({ ...owner, idempotencyKey: report.idempotencyKey! })
  expect(sent).toMatchObject({
    to: "fluiddb",
    thread: { id: report.resume },
    eval: report.eval,
    idempotencyKey: report.idempotencyKey,
    consent: { telemetry: false },
    metadata: { memory_operation: "recall", memory_challenge: "correction", memory_adapter: "sqlite", memory_turns: 4 },
  })
  expect(Object.keys(sent).sort()).toEqual([
    "category",
    "client",
    "consent",
    "eval",
    "feedback",
    "idempotencyKey",
    "metadata",
    "subject",
    "thread",
    "to",
    "v",
  ])
})

test("a resumed answer keeps the thread and question IDs but uses a fresh event key", async () => {
  const events: any[] = []
  const service = Feedback.create({
    fetch: async (_, init) => {
      const event = JSON.parse(init!.body as string)
      events.push(event)
      return Response.json({ id: "event", thread: event.thread.id })
    },
  })
  const first = await service.submit({ feedback: "Synthetic test" })
  await service.submit({ feedback: "The synthetic fix worked", resume: first.thread, question: "q-1" })
  expect(events[1].thread.id).toBe(first.thread)
  expect(events[1].question).toBe("q-1")
  expect(events[1].idempotencyKey).not.toBe(events[0].idempotencyKey)
})

test("invalid and unrelated report fields are rejected before the network", async () => {
  let calls = 0
  const service = Feedback.create({
    fetch: async () => {
      calls++
      throw new Error("must not send")
    },
  })
  for (const input of [
    { feedback: "  " },
    { feedback: "x".repeat(5001) },
    { ...report, person: "private-person" },
    { feedback: "Incomplete failed task", eval: { task: "goal" } },
    { feedback: "Answer", question: "q-1" },
    { ...report, resume: "bad" },
    { ...report, memoryCase: { operation: "recall", turns: -1 } },
  ])
    await expect(service.submit(input as Feedback.Report)).rejects.toBeDefined()
  expect(calls).toBe(0)
})

test("ambiguous delivery exposes safe retry identity, never upstream text or an automatic retry", async () => {
  let calls = 0
  const service = Feedback.create({
    fetch: async () => {
      calls++
      throw new Error("private upstream body")
    },
  })
  const failure = await service.submit(report).catch((error) => error as FeedbackError)
  expect(failure).toBeInstanceOf(FeedbackError)
  if (!(failure instanceof FeedbackError)) throw new Error("Expected delivery failure")
  expect(failure.thread).toBe(report.resume!)
  expect(failure.idempotencyKey).toBe(report.idempotencyKey!)
  expect(failure.message).not.toContain("private")
  expect(calls).toBe(1)
})

test("redirects, HTTP failure, malformed/oversized receipts and wrong threads never claim delivery", async () => {
  for (const response of [
    new Response(null, { status: 307, headers: { location: "https://other.example" } }),
    new Response("private upstream content", { status: 429 }),
    new Response("not-json"),
    Response.json({}),
    Response.json({ ...owner, thread: "different-thread" }),
    new Response("x".repeat(65_537)),
  ]) {
    const service = Feedback.create({ fetch: async () => response })
    const failure = await service.submit(report).catch((error) => error as FeedbackError)
    expect(failure).toBeInstanceOf(FeedbackError)
    if (!(failure instanceof FeedbackError)) throw new Error("Expected delivery failure")
    expect(failure.message).not.toContain("private upstream")
  }
})

test("cancellation and deadlines reach fetch and keep an uncertain submission retryable", async () => {
  const controller = new AbortController()
  let started!: () => void
  const ready = new Promise<void>((resolve) => {
    started = resolve
  })
  let signal: AbortSignal | undefined
  const service = Feedback.create({
    fetch: async (_, init) => {
      signal = init!.signal as AbortSignal
      started()
      return new Promise((_, reject) => signal!.addEventListener("abort", () => reject(signal!.reason), { once: true }))
    },
  })
  const pending = service.submit(report, { signal: controller.signal }).catch((error) => error)
  await ready
  controller.abort()
  expect(await pending).toBeInstanceOf(FeedbackError)
  expect(signal!.aborted).toBe(true)
  const already = Feedback.create({
    fetch: async () => {
      throw new Error("not reached")
    },
  })
  await expect(already.submit(report, { signal: controller.signal })).rejects.toHaveProperty("name", "AbortError")
})
