import { expect, test } from "bun:test"
import { Pick, type Memory } from "@fluiddb/core"
import { Local } from "@fluiddb/core/local"
import { Testing } from "@fluiddb/core/testing"
import { compare, Dataset, evaluate } from "../src/index"
import fixture from "../../../evals/fixtures/conversations.json"

const data = () => Dataset.parse(fixture)
const deps = (): Memory.Deps => ({
  store: Local.store(),
  model: Testing.model(),
  embedder: Testing.embedder(),
  decider: Testing.decider(),
})

test("the public evaluator preserves all 50 existing regression checks", async () => {
  const report = await evaluate(data(), deps())
  expect(report.checks).toEqual({ passed: 50, total: 50 })
  expect(report.probes).toHaveLength(15)
  expect(report.detector).toEqual({ precision: 1, recall: 1 })
})

test("a broken picker produces paired regressions and both stores are independently measured", async () => {
  const result = await compare(data(), [
    { id: "default", create: () => ({ deps: deps() }) },
    {
      id: "broken",
      create: () => ({ deps: { ...deps(), picker: { id: "empty", processors: [], pick: async () => [] } } }),
    },
  ])
  expect(result.complete).toBe(true)
  const pair = result.paired[0]!
  expect(pair.comparable).toBe(true)
  if (!pair.comparable) throw new Error("Missing paired score")
  expect(pair.regressedChecks).toBeGreaterThan(0)
  expect(pair.changes.some((x) => x.metric === "windows@2")).toBe(true)
  expect(pair.changes.some((x) => x.metric === "statements@3")).toBe(true)
})

test("errors are unscored, redacted, incomparable, and all owned stores close", async () => {
  let closed = 0
  const result = await compare(data(), [
    {
      id: "failed",
      create: () => ({
        deps: {
          ...deps(),
          model: Testing.model(() => {
            throw new Error("private conversation and secret")
          }),
        },
        close: () => {
          closed++
        },
      }),
    },
    {
      id: "ok",
      create: () => ({
        deps: deps(),
        close: () => {
          closed++
        },
      }),
    },
  ])
  expect(closed).toBe(2)
  expect(result.complete).toBe(false)
  expect(result.runs[0]!.status).toBe("error")
  expect(result.paired[0]!.comparable).toBe(false)
  expect(JSON.stringify(result)).not.toContain("private conversation")
  expect(result.runs[0]).not.toHaveProperty("report")
})

test("variants cannot share a store or start with existing data", async () => {
  const shared = deps()
  const result = await compare(
    data(),
    ["one", "two"].map((id) => ({ id, create: () => ({ deps: shared }) })),
  )
  expect(result.runs.map((x) => x.status)).toEqual(["complete", "error"])
  await expect(evaluate(data(), shared)).rejects.toThrow("empty store")
})

test("gold labels never reach plugins and input mutation cannot change later variants", async () => {
  const dataset = data()
  const sentinel = "label-only-sentinel"
  const probe = dataset.steps.find((x) => x.type === "probe")!
  if (probe.type !== "probe") throw new Error("Missing probe")
  probe.expected.contains = [sentinel]
  let calls = 0
  const create = () => {
    const base = deps()
    return {
      deps: {
        ...base,
        model: Testing.model((prompt) => {
          expect(prompt).not.toContain(sentinel)
          calls++
        }),
        picker: Pick.searchOrder(),
      },
    }
  }
  const result = await compare(dataset, [
    {
      id: "one",
      create: () => {
        dataset.steps.splice(0)
        return create()
      },
    },
    { id: "two", create },
  ])
  expect(calls).toBeGreaterThan(0)
  expect(result.paired[0]).toMatchObject({
    comparable: true,
    improvedChecks: 0,
    regressedChecks: 0,
    unchangedChecks: 50,
  })
  expect(result.datasetSha256).toMatch(/^[a-f0-9]{64}$/)
  expect(result.runs.every((x) => x.status === "complete" && !x.report.passed)).toBe(true)
})

test("invalid variant names are rejected before provider factories are called", async () => {
  let calls = 0
  const create = () => {
    calls++
    return { deps: deps() }
  }
  for (const names of [["one"], ["same", "same"], ["one", " "]])
    await expect(
      compare(
        data(),
        names.map((id) => ({ id, create })),
      ),
    ).rejects.toThrow("unique")
  expect(calls).toBe(0)
})
