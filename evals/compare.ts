import { parseArgs } from "node:util"
import { mkdir, writeFile } from "node:fs/promises"
import path from "node:path"
import { compare } from "@fluiddb/eval"
import { SqlStore } from "@fluiddb/sql"
import { Bun as Sqlite } from "@fluiddb/sql/bun"
import { Dataset, Config } from "./schema"
import { providers, variant, variants, type VariantName } from "./providers"
import { EvaluationError, transport } from "./transport"

const { values } = parseArgs({
  options: {
    dataset: { type: "string", default: path.join(import.meta.dir, "fixtures/conversations.json") },
    provider: { type: "string", default: "synthetic" },
    config: { type: "string" },
    variants: { type: "string", default: "vector,model,decider" },
    live: { type: "boolean", default: false },
    budget: { type: "string" },
    out: { type: "string", default: ".cache/evals/results/comparison.json" },
    help: { type: "boolean" },
  },
  strict: true,
})
if (values.help) {
  console.log(
    "bun run eval:compare [--provider synthetic|real] [--variants vector,model,decider,jev,routed] [--dataset file.json] [--config file.json] [--live --budget USD] [--out file.json]\nReal providers default to cache-only. Exit 1 on errors or regressions against the first variant. See evals/README.md.",
  )
  process.exit(0)
}
let http: ReturnType<typeof transport> | undefined
const save = async (report: unknown) => {
  await mkdir(path.dirname(values.out), { recursive: true })
  await writeFile(values.out, JSON.stringify(report, null, 2) + "\n", { mode: 0o600 })
}
try {
  if (!["synthetic", "real"].includes(values.provider)) throw new EvaluationError("Provider must be synthetic or real")
  if (values.provider === "synthetic" && (values.live || values.budget || values.config))
    throw new EvaluationError("Live/config/budget options require --provider real")
  if (!values.live && values.budget) throw new EvaluationError("--budget only applies with --live")
  const data = Dataset.parse(await Bun.file(values.dataset).json())
  const names = values.variants.split(",").map((name) => name.trim())
  if (
    names.length < 2 ||
    new Set(names).size !== names.length ||
    names.some((name) => !variants.includes(name as VariantName))
  )
    throw new EvaluationError(`Choose at least two unique variants from ${variants.join(", ")}`)
  const config = values.config ? Config.parse(await Bun.file(values.config).json()) : undefined
  if (values.provider === "real" && !config) throw new EvaluationError("Real evaluation requires --config")
  if (values.live && !process.env.OPENAI_API_KEY) throw new EvaluationError("Live evaluation requires OPENAI_API_KEY")
  if (values.live && names.some((name) => ["jev", "routed"].includes(name)) && !process.env.OPENROUTER_API_KEY)
    throw new EvaluationError("Live Jev variants require OPENROUTER_API_KEY")
  http = config
    ? transport({ config, cache: ".cache/evals/openai", live: values.live, budget: Number(values.budget ?? 0) })
    : undefined
  // Validate all variants before the first paid call; usage and budget are shared across the entire matrix.
  const configured = names.map((name) => ({
    id: name,
    deps: variant(name as VariantName, providers(config, http?.fetch), {
      synthetic: data.synthetic,
      config,
      fetch: http?.fetch,
    }),
  }))
  const usage: Record<string, Record<string, number>> = {}
  const result = await compare(
    data,
    configured.map(({ id, deps }) => ({
      id,
      create: () => {
        console.log(`Evaluating ${id}…`)
        const before = { ...http?.stats }
        const sql = Sqlite.open(":memory:")
        return {
          deps: { ...deps, store: SqlStore.store(sql) },
          close: () => {
            sql.close()
            usage[id] = Object.fromEntries(
              Object.entries(http?.stats ?? {}).map(([key, value]) => [
                key,
                value - (before[key as keyof typeof before] ?? 0),
              ]),
            )
          },
        }
      },
    })),
  )
  const report = {
    ...result,
    mode: values.provider === "synthetic" ? "synthetic-regression" : values.live ? "live" : "cache-only",
    config,
    usage: http?.stats,
    variantUsage: usage,
    ...(http?.failure ? { providerError: http.failure } : {}),
  }
  await save(report)
  const lines = [
    `Dataset: ${result.dataset} (${result.datasetSha256})`,
    "",
    "| Variant | Checks | Windows | Statements | Repetition TP / FP / FN | Recall p50 ms | Network requests / cache hits |",
    "| --- | --- | --- | --- | --- | --- | --- |",
    ...result.runs.map((run) => {
      if (run.status === "error") return `| ${run.id} | ERROR — no score | — | — | — | — | — |`
      const r = run.report
      const count = (name: string) => (r.metrics[name] ? `${r.metrics[name]!.passed}/${r.metrics[name]!.total}` : "—")
      return `| ${run.id} | ${r.checks.passed}/${r.checks.total} | ${count(`windows@${data.keep.windows}`)} | ${count(`statements@${data.keep.statements}`)} | ${r.repetition.truePositive} / ${r.repetition.falsePositive} / ${r.repetition.falseNegative} | ${r.latencyMs.recallP50.toFixed(1)} | ${usage[run.id]?.requests ?? 0} / ${usage[run.id]?.cached ?? 0} |`
    }),
    "",
    "Counts are fixture checks, not answer-quality scores. Latency includes cache hits; it is not a provider speed comparison.",
    ...result.paired.map((pair) =>
      pair.comparable
        ? `${pair.id} vs ${result.baseline}: ${pair.improvedChecks} improved, ${pair.regressedChecks} regressed checks.`
        : `${pair.id}: comparison incomplete.`,
    ),
  ]
  console.log(lines.join("\n"))
  console.log(`Usage: ${JSON.stringify(http?.stats ?? { requests: 0 })}\nReport: ${values.out}`)
  await writeFile(values.out.replace(/\.json$/, "") + ".md", lines.join("\n") + "\n", { mode: 0o600 })
  if (!result.complete || result.paired.some((pair) => pair.comparable && pair.regressedChecks > 0))
    process.exitCode = 1
} catch (error) {
  const message =
    http?.failure ??
    (error instanceof EvaluationError
      ? error.message
      : "Evaluation failed; check dataset, config and providers. No conversation text logged.")
  console.error(message)
  await save({ complete: false, error: message, usage: http?.stats })
  process.exitCode = 1
} finally {
  await http?.close()
}
