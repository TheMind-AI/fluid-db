// A made-up person's conversations through a running Worker, end to end: ingest, a repeat, recall, the background
// inbox, forgetting and erasing. Usage: FLUID_URL=http://localhost:8787 FLUID_TOKEN=... bun scripts/smoke.ts
import { Client } from "@fluiddb/client"
import type { Turn } from "@fluiddb/schema"
import assert from "node:assert/strict"

const url = process.env.FLUID_URL ?? "http://localhost:8787"
const token = process.env.FLUID_TOKEN ?? ""
const client = Client.create({ url, token })
const person = `smoke-${Date.now()}`
const at = (date: string, time: string) => `${date}T${time}:00.000Z`
const turn = (id: string, role: Turn.Role, text: string, when: string): Turn.Info => ({ id, role, text, at: when })

const first = [
  turn("s1a1", "assistant", "Hi, what brings you here today?", at("2026-03-02", "09:00")),
  turn("s1p1", "person", "My sister Anna moved to Berlin last spring and I miss her a lot.", at("2026-03-02", "09:01")),
  turn("s1a2", "assistant", "That sounds hard. How are you sleeping?", at("2026-03-02", "09:02")),
  turn(
    "s1p2",
    "person",
    "Badly. I started a new job at a bank in January and I lie awake worrying.",
    at("2026-03-02", "09:03"),
  ),
  turn("s1a3", "assistant", "Is there anything that helps you wind down?", at("2026-03-02", "09:04")),
  turn(
    "s1p3",
    "person",
    "Running on Sunday mornings helps, and I want to try journaling before bed.",
    at("2026-03-02", "09:05"),
  ),
]
const second = [
  turn("s2a1", "assistant", "Welcome back. How was your week?", at("2026-03-09", "18:00")),
  turn(
    "s2p1",
    "person",
    "Lonely. Anna, my sister, is in Berlin now, so weekends feel empty.",
    at("2026-03-09", "18:01"),
  ),
  turn("s2a2", "assistant", "Did you get to try the journaling?", at("2026-03-09", "18:02")),
  turn(
    "s2p2",
    "person",
    "Yes, three nights. Writing before bed made it easier to fall asleep.",
    at("2026-03-09", "18:03"),
  ),
]
const third = [
  turn("s3a1", "assistant", "How are things at work?", at("2026-03-16", "08:00")),
  turn(
    "s3p1",
    "person",
    "The bank job is calmer now; my manager moved me to a smaller team.",
    at("2026-03-16", "08:01"),
  ),
]

const step = (name: string, value: unknown) => console.log(`${name}: ${JSON.stringify(value)}`)
const started = Date.now()

try {
  const health = await fetch(`${url}/health`)
  assert.equal(health.status, 200, "the Worker must be configured and healthy")
  step("health", await health.json())
  const ingested = await client.ingest(person, "session-1", first)
  assert.ok(ingested.statements > 0, "the first session must produce statements")
  step("ingest 1", ingested)
  step("ingest 2 (repeats session 1)", await client.ingest(person, "session-2", second))

  const conversation = [
    turn("s4a1", "assistant", "Good to see you. What's on your mind?", at("2026-03-23", "10:00")),
    turn("s4p1", "person", "I keep thinking about my sister Anna in Berlin.", at("2026-03-23", "10:01")),
  ]
  const answer = await client.recall(person, { conversation, name: "Sam" })
  assert.ok(
    answer.recall.statements.some((x) => /Anna|Berlin/i.test(x.text)),
    "recall must find the sister",
  )
  step("recall", {
    statements: answer.recall.statements.length,
    windows: answer.recall.windows.length,
    retold: answer.recall.retold && { text: answer.recall.retold.statement.text, p: answer.recall.retold.probability },
    sections: answer.prompt?.split("\n").filter((x) => x.startsWith("## ")),
  })
  step("first statement", answer.recall.statements[0]?.text)

  step("accept 3 (background)", await client.accept(person, "session-3", third))
  for (const _ of Array.from({ length: 60 })) {
    const status = await client.status(person)
    if (status.inbox.pending === 0 && status.windows.pending === 0) break
    await new Promise((resolve) => setTimeout(resolve, 1000))
  }
  const status = await client.status(person)
  assert.equal(status.inbox.pending, 0, "set INGEST_DELAY=1 before running the smoke test")
  assert.equal(status.inbox.failed, 0, "background ingestion failed")
  assert.equal(status.windows.pending, 0, "the dossier did not catch up")
  step("status after the alarm", status)
  step("dossier", {
    chars: (await client.dossier(person))?.text.length,
    updates: (await client.dossier(person))?.updates,
  })
  const forgotten = await client.forget(person, { session: "session-2" })
  assert.ok(forgotten.windows > 0, "forget must remove the session's source")
  step("forget session 2", forgotten)
  step("status after forgetting", await client.status(person))
} finally {
  await client.erase(person)
  const erased = await client.status(person)
  assert.equal(erased.statements, 0)
  assert.equal(erased.windows.total, 0)
  assert.equal(erased.inbox.pending, 0)
  step("status after erasing", erased)
}
step("seconds", Math.round((Date.now() - started) / 1000))
