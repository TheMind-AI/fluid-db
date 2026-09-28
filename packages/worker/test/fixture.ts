import { Memory, ProviderError, type Embedder, type LanguageModel, type Store } from "@fluiddb/core"
import { Testing } from "@fluiddb/core/testing"
import type { Turn } from "@fluiddb/schema"
import { Bun } from "@fluiddb/sql/bun"
import { Host } from "../src/host"

export const day = (date: string, time = "09:00") => `${date}T${time}:00.000Z`
export const turn = (id: string, role: Turn.Role, text: string, at: string): Turn.Info => ({ id, role, text, at })

export const session = [
  turn("a1", "assistant", "Hi. What is on your mind today?", day("2026-03-02", "09:00")),
  turn("p1", "person", "My sister Anna moved to Berlin last spring.", day("2026-03-02", "09:01")),
  turn("a2", "assistant", "That is a big change. How are you sleeping?", day("2026-03-02", "09:02")),
  turn("p2", "person", "Badly, since the new job at the bank started.", day("2026-03-02", "09:03")),
]

// A model and an embedder that fail while `down` is set.
export function outage() {
  const state = { down: false }
  const embedder: Embedder = {
    ...Testing.embedder(),
    embed: async (texts) => {
      if (state.down) throw new ProviderError("test:embedder", 503, "down")
      return Testing.embedder().embed(texts)
    },
  }
  const model: LanguageModel = {
    ...Testing.model(),
    text: async (prompt) => {
      if (state.down) throw new ProviderError("test:model", 503, "down")
      return Testing.model().text(prompt)
    },
    object: async (prompt, schema) => {
      if (state.down) throw new ProviderError("test:model", 503, "down")
      return Testing.model().object(prompt, schema)
    },
  }
  return { model, embedder, state }
}

// A host on an in-memory SQLite, the deterministic fakes, a clock the test moves, and an alarm it fires.
export function host(input: { model?: LanguageModel; embedder?: Embedder; settings?: Partial<Host.Settings> } = {}) {
  const clock = { now: Date.parse(day("2026-03-02", "10:00")) }
  const alarm = { at: null as number | null }
  const events: string[] = []
  const memory = (store: Store) =>
    Memory.create({
      model: input.model ?? Testing.model(),
      embedder: input.embedder ?? Testing.embedder(),
      decider: Testing.decider(),
      store,
      clock: Testing.clock(),
      ids: Testing.ids(),
      options: { assistant: "Mind" },
    })
  const sql = Bun.open()
  const made = Host.create({
    sql,
    alarm: { get: async () => alarm.at, set: async (at) => void (alarm.at = at) },
    memory,
    logger: { event: (name) => void events.push(name) },
    now: () => clock.now,
    settings: { delay: 60, wait: 600, ...input.settings },
  })
  // As the runtime does: the alarm is cleared, then the handler runs.
  const fire = async () => {
    clock.now = Math.max(clock.now, alarm.at ?? clock.now)
    alarm.at = null
    await made.alarm()
  }
  return { host: made, clock, alarm, fire, events, sql }
}
