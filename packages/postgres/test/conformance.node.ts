import { after as afterAll, describe, test } from "node:test"
import assert from "node:assert/strict"
const expect = (value: unknown) => ({
  toEqual: (expected: unknown) => assert.deepEqual(value, expected),
  toBe: (expected: unknown) => assert.equal(value, expected),
  rejects: { toThrow: () => assert.rejects(value as Promise<unknown>) },
})
import { Pool } from "pg"
import { Conformance } from "@fluiddb/core/conformance"
import { Postgres } from "../src/index"

const url = process.env.FLUID_TEST_POSTGRES_URL
if (!url) test.skip("PostgreSQL conformance requires FLUID_TEST_POSTGRES_URL", () => {})
else {
  const pool = new Pool({ connectionString: url })
  const tables: string[] = []
  afterAll(async () => {
    for (const table of tables) await pool.query(`DROP TABLE "${table}"`)
    await pool.end()
  })
  Conformance.store({ describe, test, expect }, async () => {
    const table = `fluid_test_${crypto.randomUUID().replaceAll("-", "").slice(0, 20)}`
    tables.push(table)
    return Postgres.create(pool, { table })
  })
}
