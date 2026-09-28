import { Memory, Person } from "@fluiddb/core"
import { Local } from "@fluiddb/core/local"
import { Testing } from "@fluiddb/core/testing"
import type { Statement } from "@fluiddb/schema"

/** Frozen, invented data. The test providers are for protocol verification, not a model-quality score. */
export async function fixture() {
  const memory = Person.bind(
    Memory.create({
      store: Local.store(),
      model: Testing.model(),
      embedder: Testing.embedder(),
      decider: Testing.decider(),
    }),
    "fixture-person",
  )
  const notes: [string, string, Statement.Kind, string, string?][] = [
    ["relative-old", "Their sister Anna lived in Berlin.", "people", "2026-01-01"],
    ["visits", "They visit their sister on Sundays.", "life", "2026-01-02"],
    ["work", "They start work at 08:30.", "work", "2026-01-03"],
    ["commute", "They commute by train for 35 minutes.", "life", "2026-01-04"],
    ["class", "Their ceramics class meets on Tuesdays.", "practice", "2026-01-05"],
    ["teacher", "Mira teaches their ceramics class.", "people", "2026-01-06"],
    ["visit-plan", "They will bring a blue bowl to the exhibition on 2026-04-12.", "plan", "2026-01-07"],
    ["language", "They prefer replies in Czech.", "preference", "2026-01-08"],
    ["style-old", "They prefer detailed replies.", "preference", "2026-01-09"],
    ["exercise", "They use a five-minute breathing exercise before the train.", "practice", "2026-01-10"],
    ["relative-new", "Their sister Anna moved to Porto.", "people", "2026-02-01", "relative-old"],
    ["style-new", "They now prefer short replies.", "preference", "2026-02-09", "style-old"],
    ["cancel", "They cancelled the exhibition visit.", "plan", "2026-02-12", "visit-plan"],
  ]
  const ids = new Map<string, string>()
  for (const [id, text, kind, day, replaces] of notes) {
    const saved = await memory.remember({
      id,
      text,
      kind,
      session: "frozen",
      at: `${day}T00:00:00.000Z`,
      ...(replaces ? { replaces: ids.get(replaces)! } : {}),
    })
    ids.set(id, saved.statement.id)
  }
  return memory
}
