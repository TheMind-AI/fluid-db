import { Memory, Render } from "@fluiddb/core"
import { Testing } from "@fluiddb/core/testing"
import type { Recall } from "@fluiddb/schema"
import { Dataset } from "./schema"

// @ref LLP 0024#replay-protocol — replay the public API; labels are used only after recall, never in model inputs
export async function evaluate(input: Dataset, deps: Memory.Deps) {
  const data = Dataset.parse(input)
  const started = performance.now()
  const latency: number[] = []
  const store = deps.store
  for (const person of new Set(data.steps.map((step) => step.person)))
    if ((await store.revision(person)) !== null) throw new Error("Evaluation requires an empty store")
  const memory = Memory.create({
    ...deps,
    store,
    clock: Testing.clock(),
    ids: Testing.ids(),
    options: {
      ...deps.options,
      statements: {
        search: Math.max(40, data.keep.statements),
        ...deps.options?.statements,
        keep: data.keep.statements,
      },
      windows: { search: Math.max(20, data.keep.windows), ...deps.options?.windows, keep: data.keep.windows },
    },
  })
  const rows: { id: string; checks: Record<string, boolean>; retold?: { expected: boolean; actual: boolean } }[] = []
  const sources = async (person: string, evidence: Recall.Evidence[], kind: "window" | "statement") => {
    const ids = evidence.flatMap((item) => item.ids)
    const windows = kind === "window" ? ids : (await store.statements.get(person, ids)).map((row) => row.window)
    return new Set((await store.windows.get(person, windows)).flatMap((row) => row.sources ?? row.turns))
  }
  for (const step of data.steps) {
    if (step.type === "ingest") {
      await memory.ingest({ person: step.person, session: step.session, turns: step.turns })
      await memory.fold(step.person)
      continue
    }
    if (step.type === "forget") {
      await memory.forget({ person: step.person, session: step.session })
      await memory.fold(step.person)
      continue
    }
    const recallStarted = performance.now()
    const result = await memory.recall({ person: step.person, conversation: step.conversation })
    latency.push(performance.now() - recallStarted)
    const windows = await sources(step.person, result.windows, "window")
    const statements = await sources(step.person, result.statements, "statement")
    const retold = result.retold
      ? new Set(
          (await store.windows.get(step.person, [result.retold.statement.window])).flatMap((w) => w.sources ?? w.turns),
        )
      : new Set<string>()
    const checks: Record<string, boolean> = {}
    const gold = step.expected
    if (gold.sources) {
      checks[`windows@${data.keep.windows}`] = gold.sources.some((id) => windows.has(id))
      checks[`statements@${data.keep.statements}`] = gold.sources.some((id) => statements.has(id))
      if (gold.retold) checks.retoldSource = gold.sources.some((id) => retold.has(id))
    }
    if (gold.absentSources)
      checks.absentSources = gold.absentSources.every(
        (id) => !windows.has(id) && !statements.has(id) && !retold.has(id),
      )
    const rendered = Render.render(result, { directive: false }).toLowerCase()
    if (gold.contains) checks.contains = gold.contains.every((text) => rendered.includes(text.toLowerCase()))
    if (gold.absent) checks.absent = gold.absent.every((text) => !rendered.includes(text.toLowerCase()))
    if (gold.empty)
      checks.empty = !result.dossier && !result.windows.length && !result.statements.length && !result.retold
    if (gold.retold !== undefined) checks.retold = Boolean(result.retold) === gold.retold
    rows.push({
      id: step.id,
      checks,
      ...(gold.retold === undefined ? {} : { retold: { expected: gold.retold, actual: Boolean(result.retold) } }),
    })
  }
  const metrics: Record<string, { passed: number; total: number }> = {}
  const repetition = { truePositive: 0, falsePositive: 0, falseNegative: 0, trueNegative: 0 }
  for (const row of rows) {
    for (const [name, pass] of Object.entries(row.checks)) {
      const count = (metrics[name] ??= { passed: 0, total: 0 })
      count.total++
      if (pass) count.passed++
    }
    if (row.retold) {
      repetition[
        row.retold.expected
          ? row.retold.actual
            ? "truePositive"
            : "falseNegative"
          : row.retold.actual
            ? "falsePositive"
            : "trueNegative"
      ]++
    }
  }
  const checks = Object.values(metrics).reduce(
    (sum, metric) => ({ passed: sum.passed + metric.passed, total: sum.total + metric.total }),
    { passed: 0, total: 0 },
  )
  const sorted = latency.toSorted((a, b) => a - b)
  const percentile = (p: number) => sorted[Math.max(0, Math.ceil(sorted.length * p) - 1)] ?? 0
  return {
    dataset: data.id,
    checks,
    latencyMs: { total: performance.now() - started, recallP50: percentile(0.5), recallP95: percentile(0.95) },
    detector: {
      precision:
        repetition.truePositive + repetition.falsePositive
          ? repetition.truePositive / (repetition.truePositive + repetition.falsePositive)
          : null,
      recall:
        repetition.truePositive + repetition.falseNegative
          ? repetition.truePositive / (repetition.truePositive + repetition.falseNegative)
          : null,
    },
    passed: rows.every((row) => Object.values(row.checks).every(Boolean)),
    metrics,
    repetition,
    probes: rows,
  }
}

export type Report = Awaited<ReturnType<typeof evaluate>>
