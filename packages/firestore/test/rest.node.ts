import { after, describe, test } from "node:test"
import assert from "node:assert/strict"
import { Firestore as Database } from "@google-cloud/firestore"
import { Conformance } from "@fluiddb/core/conformance"
import { Firestore } from "../src/index"
import { FirestoreRest } from "../src/rest"
const expect = (value: unknown) => ({
  toEqual: (expected: unknown) => assert.deepEqual(value, expected),
  toBe: (expected: unknown) => assert.equal(value, expected),
  rejects: { toThrow: () => assert.rejects(value as Promise<unknown>) },
})
if (!process.env.FIRESTORE_EMULATOR_HOST)
  test.skip("Firestore REST conformance requires FIRESTORE_EMULATOR_HOST", () => {})
else {
  const db = new Database({ projectId: "fluiddb-test" })
  const collections: string[] = []
  after(async () => {
    for (const name of collections) await db.recursiveDelete(db.collection(name))
    await db.terminate()
  })
  Conformance.store({ describe, test, expect }, () => {
    const collection = `fluid_rest_${crypto.randomUUID().replaceAll("-", "")}`
    collections.push(collection)
    return FirestoreRest.create({
      projectId: "fluiddb-test",
      collection,
      accessToken: async () => "",
      origin: `http://${process.env.FIRESTORE_EMULATOR_HOST}`,
    })
  })
}

if (process.env.FIRESTORE_EMULATOR_HOST)
  test("Node and REST adapters share data and reject a competing stale write", async () => {
    const db = new Database({ projectId: "fluiddb-test" })
    const collection = `interop_${crypto.randomUUID().replaceAll("-", "")}`
    const native = Firestore.create(db, { collection })
    const rest = FirestoreRest.create({
      projectId: "fluiddb-test",
      collection,
      accessToken: async () => "",
      origin: `http://${process.env.FIRESTORE_EMULATOR_HOST}`,
    })
    try {
      await native.write("sam", { turns: { session: "one", ids: ["first"] } })
      assert.deepEqual(await rest.turns.seen("sam", ["first"]), ["first"])
      const revision = await rest.revision("sam")
      const writes = await Promise.allSettled([
        native.write("sam", { turns: { session: "one", ids: ["native"] } }, { revision }),
        rest.write("sam", { turns: { session: "one", ids: ["rest"] } }, { revision }),
      ])
      assert.equal(writes.filter((x) => x.status === "fulfilled").length, 1)
      assert.equal((await rest.turns.seen("sam", ["native", "rest"])).length, 1)
      await rest.erase("sam")
      assert.deepEqual(await native.turns.seen("sam", ["first"]), [])
    } finally {
      await db.recursiveDelete(db.collection(collection))
      await db.terminate()
    }
  })
