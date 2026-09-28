import { Database } from "bun:sqlite"
import type { Sql } from "./sql"

// Sql over bun:sqlite, for tests, scripts and local development. ":memory:" by default.
export function open(path = ":memory:"): Sql & { close(): void } {
  const db = new Database(path, { create: true })
  if (path !== ":memory:") db.run("PRAGMA journal_mode = WAL")
  const bind = (x: unknown) => (x instanceof ArrayBuffer ? new Uint8Array(x) : x)
  return {
    run: (query, ...params) => db.query(query).all(...(params.map(bind) as [])) as Record<string, unknown>[],
    transaction: (fn) => db.transaction(fn)(),
    close: () => db.close(),
  }
}

export * as Bun from "./bun"
