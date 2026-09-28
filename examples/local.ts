import { Memory, Render } from "@fluiddb/core"
import { Testing } from "@fluiddb/core/testing"
import { Bun } from "@fluiddb/sql/bun"
import { SqlStore } from "@fluiddb/sql"

// No keys, network or model charges. Replace Testing's three providers for a real integration.
const sql = Bun.open()
try {
  const memory = Memory.create({
    store: SqlStore.store(sql),
    model: Testing.model(),
    embedder: Testing.embedder(),
    decider: Testing.decider(),
    processors: ["test"],
  })
  await memory.ingest({
    person: "sam",
    session: "monday",
    turns: [
      { id: "m1", role: "assistant", text: "How is your family?", at: "2026-03-02T09:00:00Z" },
      { id: "m2", role: "person", text: "My sister Anna moved to Berlin last spring.", at: "2026-03-02T09:01:00Z" },
    ],
  })
  await memory.fold("sam")
  const recall = await memory.recall({
    person: "sam",
    conversation: [{ id: "q1", role: "person", text: "I miss my sister Anna in Berlin.", at: "2026-03-09T09:00:00Z" }],
  })
  console.log(Render.render(recall, { name: "Sam" }))
  await memory.forget({ person: "sam", session: "monday" })
  console.log("After forgetting:", await memory.recall({ person: "sam", conversation: [] }))
} finally {
  sql.close()
}
