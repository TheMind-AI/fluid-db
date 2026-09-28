import { after, describe, test } from "node:test"
import assert from "node:assert/strict"
import { Conformance } from "@fluiddb/core/conformance"
import { SqlStore } from "../src/store"
import { Node } from "../src/node"
const expect = (value: unknown) => ({
  toEqual: (expected: unknown) => assert.deepEqual(value, expected),
  toBe: (expected: unknown) => assert.equal(value, expected),
  rejects: { toThrow: () => assert.rejects(value as Promise<unknown>) },
})
const databases: ReturnType<typeof Node.open>[] = []
after(() => databases.forEach((db) => db.close()))
Conformance.store({ describe, test, expect }, () => {
  const db = Node.open()
  databases.push(db)
  return SqlStore.store(db)
})
