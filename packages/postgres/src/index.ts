import type { Pool, PoolClient } from "pg"
import { Records } from "@fluiddb/core/records"
import { ConflictError, type Store } from "@fluiddb/core"

export interface Options {
  table?: string
}

/** The host owns the pool and its shutdown. Initialization creates the isolated FluidDB table/index. */
export async function create(pool: Pool, options: Options = {}): Promise<Store> {
  const name = options.table ?? "fluiddb_records"
  if (!/^[a-z][a-z0-9_]{0,40}$/.test(name)) throw new Error("Invalid FluidDB table name")
  const table = `"${name}"`
  await pool.query(`CREATE TABLE IF NOT EXISTS ${table} (
    person TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL, data JSONB NOT NULL,
    PRIMARY KEY (person, kind, id))`)
  await pool.query(`CREATE INDEX IF NOT EXISTS "${name}_page" ON ${table} (person, kind, (data->>'at'), id)`)

  const reader = (sql: Pick<PoolClient, "query">): Records.Reader => ({
    read: async (person, query) => {
      if (query.ids?.length === 0 || query.values?.length === 0) return []
      const params: unknown[] = [person, query.kind]
      const bind = (value: unknown) => {
        params.push(value)
        return `$${params.length}`
      }
      let where = "person = $1 AND kind = $2"
      if (query.ids) where += ` AND id = ANY(${bind(query.ids)}::text[])`
      if (query.field) where += ` AND data->>${bind(query.field)} = ANY(${bind(query.values!.map(String))}::text[])`
      if (query.after)
        where += ` AND ((data->>'at') COLLATE "C", id COLLATE "C") > (${bind(query.after.at)}, ${bind(query.after.id)})`
      const order = query.ordered ? ` ORDER BY (data->>'at') COLLATE "C", id COLLATE "C"` : ""
      const limit = query.limit === undefined ? "" : ` LIMIT ${bind(query.limit)}`
      const result = await sql.query(`SELECT kind, id, data FROM ${table} WHERE ${where}${order}${limit}`, params)
      return result.rows as Records.Record[]
    },
  })
  const transaction = async <T>(person: string, fn: (client: PoolClient) => Promise<T>) => {
    const client = await pool.connect()
    try {
      await client.query("BEGIN")
      // A write planner observes a coherent pre-state, including immutable log/tombstone checks.
      await client.query("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", [`${name}:${person}`])
      const result = await fn(client)
      await client.query("COMMIT")
      return result
    } catch (error) {
      await client.query("ROLLBACK").catch(() => {})
      throw error
    } finally {
      client.release()
    }
  }
  const revision = async (sql: Pick<PoolClient, "query">, person: string): Promise<string | null> => {
    const result = await sql.query(
      `SELECT data->>'value' AS value FROM ${table} WHERE person = $1 AND kind = 'revision' AND id = 'current'`,
      [person],
    )
    return result.rows[0]?.value ?? null
  }
  return Records.store({
    revision: (person) => revision(pool, person),
    ...reader(pool),
    transaction: (person, work, expected) =>
      transaction(person, async (client) => {
        if (expected && expected.revision !== (await revision(client, person)))
          throw new ConflictError("Memory changed; retry with the same IDs.")
        const mutations = await work(reader(client))
        for (const x of mutations) {
          if (x.data === undefined)
            await client.query(`DELETE FROM ${table} WHERE person = $1 AND kind = $2 AND id = $3`, [
              person,
              x.kind,
              x.id,
            ])
          else
            await client.query(
              `INSERT INTO ${table} (person, kind, id, data) VALUES ($1, $2, $3, $4)
          ON CONFLICT (person, kind, id) DO UPDATE SET data = EXCLUDED.data`,
              [person, x.kind, x.id, JSON.stringify(x.data)],
            )
        }
        await client.query(
          `INSERT INTO ${table} (person, kind, id, data) VALUES ($1, 'revision', 'current', $2) ON CONFLICT (person, kind, id) DO UPDATE SET data = EXCLUDED.data`,
          [person, JSON.stringify({ value: crypto.randomUUID() })],
        )
      }),
    erase: (person) =>
      transaction(person, async (client) => {
        await client.query(`DELETE FROM ${table} WHERE person = $1`, [person])
        // @ref LLP 0023#forgetting — an erased person retains only a new concurrency generation
        await client.query(`INSERT INTO ${table} (person, kind, id, data) VALUES ($1, 'revision', 'current', $2)`, [
          person,
          JSON.stringify({ value: crypto.randomUUID() }),
        ])
      }),
  })
}
export * as Postgres from "./index"
