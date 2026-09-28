import assert from "node:assert/strict"
import { Miniflare, convertV4MiniflareOptions } from "miniflare"
if (!process.env.FIRESTORE_EMULATOR_HOST)
  throw new Error("FIRESTORE_EMULATOR_HOST is required; this test never uses a real project")
const origin = `http://${process.env.FIRESTORE_EMULATOR_HOST}`
const runtime = new Miniflare(
  convertV4MiniflareOptions({
    name: "fluiddb-firestore-runtime",
    modules: true,
    scriptPath: ".wrangler/firestore-test.js",
    compatibilityDate: "2026-09-01",
    bindings: { FIRESTORE_ORIGIN: origin, COLLECTION: `runtime_${crypto.randomUUID().replaceAll("-", "")}` },
    outboundService: async (request) => {
      assert.equal(new URL(request.url).origin, origin)
      return fetch(request.url, {
        method: request.method,
        headers: Object.fromEntries(request.headers),
        redirect: "manual",
        ...(["GET", "HEAD"].includes(request.method) ? {} : { body: await request.arrayBuffer() }),
      })
    },
  }),
)
try {
  const response = await runtime.dispatchFetch("http://fixture.test/")
  const body = await response.text()
  assert.equal(response.status, 200, body)
  assert.deepEqual(JSON.parse(body), { saved: true, source: "explicit", isolated: 0, recalled: 1, remaining: 0 })
  console.log("Firestore REST passed in workerd: SDK save, evidence, isolation, recall, forget and erase.")
} finally {
  await runtime.dispose()
}
