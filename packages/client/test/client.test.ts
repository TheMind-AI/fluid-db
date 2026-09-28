import { describe, expect, test } from "bun:test"
import { Client, FluidError } from "../src/client"

const turn = { id: "t1", role: "person" as const, text: "hi", at: "2026-03-02T09:00:00.000Z" }

function fake(status: number, body: unknown) {
  const requests: { url: string; init: RequestInit }[] = []
  const fetch = (async (url: string, init: RequestInit) => {
    requests.push({ url, init })
    return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } })
  }) as unknown as typeof globalThis.fetch
  return { fetch, requests }
}

describe("Client", () => {
  test("sends the token and the body, to paths with the ids encoded", async () => {
    const { fetch, requests } = fake(202, { accepted: 1, pending: 1 })
    const client = Client.create({ url: "https://fluid.test/", token: "t0ken", fetch })
    expect(await client.accept("person/1", "s 1", [turn])).toEqual({ accepted: 1, pending: 1 })
    expect(requests[0]?.url).toBe("https://fluid.test/v1/people/person%2F1/sessions/s%201/turns")
    expect(requests[0]?.init.headers).toEqual({ authorization: "Bearer t0ken", "content-type": "application/json" })
    expect(JSON.parse(String(requests[0]?.init.body))).toEqual({ turns: [turn] })
  })

  test("errors carry the status and the API's kind", async () => {
    const { fetch } = fake(400, { error: { name: "InvalidRequest", message: "bad" } })
    const error = await Client.create({ url: "https://x", token: "t", fetch })
      .status("p1")
      .catch((e: FluidError) => e)
    expect(error).toBeInstanceOf(FluidError)
    expect(error).toMatchObject({ status: 400, kind: "InvalidRequest", message: "bad" })
  })

  test("no dossier yet is undefined; an answer of the wrong shape is an error", async () => {
    const missing = fake(404, { error: { name: "NotFound", message: "no dossier yet" } })
    expect(await Client.create({ url: "https://x", token: "t", fetch: missing.fetch }).dossier("p1")).toBe(undefined)
    const odd = fake(200, { windows: "many" })
    await expect(
      Client.create({ url: "https://x", token: "t", fetch: odd.fetch }).forget("p1", { session: "s" }),
    ).rejects.toThrow()
  })
})

test("person-bound SDK refuses invalid input and aborts before sending credentials", async () => {
  const { fetch, requests } = fake(200, {})
  const client = Client.create({ url: "https://fluid.test", token: "token", fetch }).person("sam")
  await expect(client.dossier({ signal: AbortSignal.abort() })).rejects.toThrow()
  expect(requests).toHaveLength(0)
  expect(() => Client.create({ url: "https://user:secret@fluid.test", token: "token" })).toThrow()
  expect(() => Client.create({ url: "https://fluid.test", token: "" })).toThrow()
  await expect(client.recall({ conversation: [] })).rejects.toThrow("invalid response")
  expect(requests[0]?.init.redirect).toBe("manual")
})
