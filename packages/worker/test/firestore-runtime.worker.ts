import { Memory, Person } from "@fluiddb/core"
import { Testing } from "@fluiddb/core/testing"
import { FirestoreRest } from "@fluiddb/firestore/rest"

// Synthetic fixture only; this Worker is never deployed by the application configuration.
export default {
  async fetch(_: Request, env: { FIRESTORE_ORIGIN: string; COLLECTION: string }) {
    const store = FirestoreRest.create({
      projectId: "fluiddb-test",
      collection: env.COLLECTION,
      origin: env.FIRESTORE_ORIGIN,
      accessToken: async () => "",
    })
    const engine = Memory.create({
      store,
      model: Testing.model(),
      embedder: Testing.embedder(),
      decider: Testing.decider(),
    })
    const memory = Person.bind(engine, "runtime-person")
    const saved = await memory.remember({
      id: "fixture",
      session: "test",
      text: "They prefer short replies.",
      kind: "preference",
      at: "2026-09-01T00:00:00.000Z",
    })
    const receipt = await memory.evidence({ statements: [saved.statement.id] })
    const isolated = await Person.bind(engine, "other").inspect()
    const recall = await memory.recall({
      conversation: [{ id: "ask", role: "person", text: "short replies", at: "2026-09-02T00:00:00.000Z" }],
    })
    await memory.forget({ statements: [saved.statement.id] })
    const empty = await memory.inspect()
    await engine.erase("runtime-person")
    return Response.json({
      saved: saved.saved,
      source: receipt.windows[0]?.origin,
      isolated: isolated.items.length,
      recalled: recall.recall.statements.length,
      remaining: empty.items.length,
    })
  },
}
