import { parseArgs } from "node:util"
import { mkdir, writeFile } from "node:fs/promises"
import { createHash } from "node:crypto"
import path from "node:path"
import { Dataset, Config } from "./schema"
import { replay } from "./replay"
import { providers } from "./providers"
import { EvaluationError, transport } from "./transport"

const { values } = parseArgs({
  args: process.argv.slice(2),
  options: {
    dataset: { type: "string", default: path.join(import.meta.dir, "fixtures/conversations.json") },
    provider: { type: "string", default: "synthetic" },
    config: { type: "string" },
    live: { type: "boolean", default: false },
    budget: { type: "string" },
    out: { type: "string", default: ".cache/evals/results/latest.json" },
    help: { type: "boolean" },
  },
  strict: true,
})
if (values.help) {
  console.log(
    "bun run eval [--dataset file.json] [--provider synthetic|openai] [--config file.json] [--live --budget USD] [--out file.json]\nOpenAI defaults to cache-only. See evals/README.md for config, dataset and grading details.",
  )
  process.exit(0)
}

let http: ReturnType<typeof transport> | undefined
const save = async (report: unknown) => {
  await mkdir(path.dirname(values.out), { recursive: true })
  await writeFile(values.out, JSON.stringify(report, null, 2) + "\n", { mode: 0o600 })
}
try {
  if (!["synthetic", "openai"].includes(values.provider))
    throw new EvaluationError("Provider must be synthetic or openai")
  if (values.provider === "synthetic" && (values.live || values.budget || values.config))
    throw new EvaluationError("Live/config/budget options require --provider openai")
  if (!values.live && values.budget) throw new EvaluationError("--budget only applies with --live")
  const input = await Bun.file(values.dataset).text()
  const parsed = Dataset.safeParse(JSON.parse(input))
  if (!parsed.success)
    throw new EvaluationError("Invalid evaluation dataset; check evals/schema.ts (no input text logged)")
  const config = values.config ? Config.parse(await Bun.file(values.config).json()) : undefined
  if (values.provider === "openai" && !config) throw new EvaluationError("OpenAI evaluation requires --config")
  if (values.live && !process.env.OPENAI_API_KEY) throw new EvaluationError("Live evaluation requires OPENAI_API_KEY")
  http = config
    ? transport({ config, cache: ".cache/evals/openai", live: values.live, budget: Number(values.budget ?? 0) })
    : undefined
  const deps = providers(config, http?.fetch)
  const result = await replay(parsed.data, deps)
  const report = {
    ...result,
    provider: values.provider,
    mode: values.provider === "synthetic" ? "synthetic-regression" : values.live ? "live" : "cache-only",
    datasetSha256: createHash("sha256").update(input).digest("hex"),
    config,
    usage: http?.stats ?? { cached: 0, requests: 0, reservedUsd: 0, estimatedUsd: 0, unknownUsage: 0 },
  }
  await save({ status: "complete", ...report })
  console.log(`${report.mode}: ${result.probes.length} probes, ${result.passed ? "PASS" : "FAIL"}`)
  for (const [name, count] of Object.entries(result.metrics)) console.log(`  ${name}: ${count.passed}/${count.total}`)
  console.log(`Repetition: ${JSON.stringify(result.repetition)}`)
  console.log(`Usage: ${JSON.stringify(report.usage)}`)
  console.log(`Report: ${values.out}`)
  if (!result.passed) process.exitCode = 1
} catch (error) {
  await http?.close()
  // Provider errors may echo user text. Only known local errors may be printed in a command-line report.
  const message =
    http?.failure ??
    (error instanceof EvaluationError
      ? error.message
      : "Evaluation failed; check dataset, config, cache and provider availability. No conversation text logged.")
  console.error(message)
  if (http) console.error(`Usage: ${JSON.stringify(http.stats)}`)
  await save({ status: "error", passed: false, error: message, usage: http?.stats }).catch(() =>
    console.error("Could not write evaluation report"),
  )
  process.exitCode = 1
} finally {
  await http?.close()
}
