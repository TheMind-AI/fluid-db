import type { Memory, Decider, Embedder, LanguageModel } from "@fluiddb/core"
import { Testing } from "@fluiddb/core/testing"
import { evaluate, type Dataset } from "@fluiddb/eval"
import { SqlStore } from "@fluiddb/sql"
import { Bun } from "@fluiddb/sql/bun"

export interface Providers {
  model: LanguageModel
  embedder: Embedder
  decider: Decider
  processors: string[]
}
export function synthetic(): Providers {
  return { model: Testing.model(), embedder: Testing.embedder(), decider: Testing.decider(), processors: ["test"] }
}
export async function replay(data: Dataset, providers: Omit<Memory.Deps, "store">) {
  const sql = Bun.open(":memory:")
  try {
    return await evaluate(data, { ...providers, store: SqlStore.store(sql) })
  } finally {
    sql.close()
  }
}
