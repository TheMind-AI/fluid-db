import { after as afterAll, describe, test } from "node:test"
import assert from "node:assert/strict"
const expect = (value: unknown) => ({
  toEqual: (expected: unknown) => assert.deepEqual(value, expected),
  toBe: (expected: unknown) => assert.equal(value, expected),
  rejects: { toThrow: () => assert.rejects(value as Promise<unknown>) },
})
import { Firestore as Database } from "@google-cloud/firestore"
import { Conformance } from "@fluiddb/core/conformance"
import { Firestore } from "../src/index"

// Never fall back to a real Firestore project in a test.
if (!process.env.FIRESTORE_EMULATOR_HOST) test.skip("Firestore conformance requires FIRESTORE_EMULATOR_HOST", () => {})
else {
  const db = new Database({ projectId: "fluiddb-test" })
  const collections: string[] = []
  afterAll(async () => {
    for (const name of collections) await db.recursiveDelete(db.collection(name))
    await db.terminate()
  })
  Conformance.store({ describe, test, expect }, () => {
    const collection = `fluid_test_${crypto.randomUUID().replaceAll("-", "")}`
    collections.push(collection)
    return Firestore.create(db, { collection })
  })
}
