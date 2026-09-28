export type Value = string | number | null | ArrayBuffer
export type Row = Record<string, unknown>

// A synchronous SQLite connection, as Durable Object storage (ctx.storage.sql) and bun:sqlite both are. Everything
// `transaction` runs commits together, or not at all.
export interface Sql {
  run(query: string, ...params: Value[]): Row[]
  transaction<T>(fn: () => T): T
}

// Durable Objects allow 100 bound parameters a query; lists are read in slices well under that.
export const SLICE = 50

export function slices<T>(items: T[], size = SLICE): T[][] {
  return Array.from({ length: Math.ceil(items.length / size) }, (_, i) => items.slice(i * size, (i + 1) * size))
}

export const marks = (n: number) => Array.from({ length: n }, () => "?").join(", ")

export * as Sql from "./sql"
