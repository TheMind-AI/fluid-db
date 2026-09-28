import { describe, expect, test } from "bun:test"
import { OutputError } from "@fluiddb/core"
import { Jev } from "../src/jev"
import { fake, instant, json } from "./fake"

const options = { apiKey: "or-test", ...instant }

describe("Jev.decider", () => {
  test("yes/no: noul questions, one request, calibrated probabilities back", async () => {
    const { fetch, requests } = fake((body) =>
      json({
        answers: Object.fromEntries(Object.keys(body.questions).map((k, i) => [k, { type: "noul", noul: i / 10 }])),
        usage: { input_tokens: 50 },
      }),
    )
    const jev = Jev.decider({ ...options, fetch })
    expect(await jev.yes({ moment: "now" }, { r0: "Is it?", r1: "Is that?" })).toEqual({ r0: 0, r1: 0.1 })
    expect(requests[0]?.url).toBe("https://openrouter.ai/api/v1/systemone")
    expect(requests[0]?.body).toEqual({
      model: "typesafe/jev-1.13",
      state: { moment: "now" },
      questions: { r0: { type: "noul", instructions: "Is it?" }, r1: { type: "noul", instructions: "Is that?" } },
    })
    expect(jev.processors).toEqual(["openrouter", "typesafe"])
  })

  test("splits large question sets", async () => {
    const { fetch, requests } = fake((body) =>
      json({ answers: Object.fromEntries(Object.keys(body.questions).map((k) => [k, { type: "noul", noul: 0.5 }])) }),
    )
    const questions = Object.fromEntries(Array.from({ length: 70 }, (_, i) => [`r${i}`, "?"]))
    const out = await Jev.decider({ ...options, fetch }).yes({}, questions)
    expect(Object.keys(out).length).toBe(70)
    expect(requests.map((x) => Object.keys(x.body.questions).length)).toEqual([64, 6])
  })

  test("choose: one choice question with the options as criteria", async () => {
    const { fetch, requests } = fake(() =>
      json({
        answers: {
          pick: { type: "choice", choice: "s1", probabilities: { s0: 0.1, s1: 0.8, none: 0.1 }, confidence: 0.8 },
        },
      }),
    )
    const out = await Jev.decider({ ...options, fetch }).choose({ message: "m" }, "Which?", {
      s0: "a",
      s1: "b",
      none: "neither",
    })
    expect(out).toEqual({ choice: "s1", probabilities: { s0: 0.1, s1: 0.8, none: 0.1 } })
    expect(requests[0]?.body.questions).toEqual({
      pick: { type: "choice", instructions: "Which?", criteria: { s0: "a", s1: "b", none: "neither" } },
    })
  })

  test("a missing answer or an unknown choice is an error", async () => {
    const missing = fake(() => json({ answers: {} }))
    await expect(Jev.decider({ ...options, fetch: missing.fetch }).yes({}, { r0: "?" })).rejects.toThrow(OutputError)
    const unknown = fake(() => json({ answers: { pick: { type: "choice", choice: "zz", probabilities: {} } } }))
    await expect(Jev.decider({ ...options, fetch: unknown.fetch }).choose({}, "?", { a: "a" })).rejects.toThrow(
      OutputError,
    )
  })
})
