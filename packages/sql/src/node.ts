import { DatabaseSync, type SQLInputValue } from "node:sqlite"
import type { Sql } from "./sql"

/** SQLite for Node 22.13+; the caller owns the database lifetime. */
export function open(path = ":memory:"): Sql & { close(): void } {
  const db = new DatabaseSync(path)
  if (path !== ":memory:") db.exec("PRAGMA journal_mode = WAL")
  db.exec("PRAGMA busy_timeout = 5000")
  let sequence = 0
  return {
    run: (query, ...params) =>
      db
        .prepare(query)
        .all(...(params.map((x) => (x instanceof ArrayBuffer ? new Uint8Array(x) : x)) as SQLInputValue[]))
        .map((row) => ({ ...row })),
    transaction: (fn) => {
      const name = `fluid_tx_${++sequence}`
      db.exec(`SAVEPOINT ${name}`)
      try {
        const value = fn()
        db.exec(`RELEASE SAVEPOINT ${name}`)
        return value
      } catch (error) {
        db.exec(`ROLLBACK TO SAVEPOINT ${name}`)
        db.exec(`RELEASE SAVEPOINT ${name}`)
        throw error
      }
    },
    close: () => db.close(),
  }
}
export * as Node from "./node"
