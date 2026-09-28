import { Memory, Pick, type Store } from "@fluiddb/core"
import { Dataset } from "./schema"
import { evaluate, type Report } from "./evaluate"

export interface Variant {
  id: string
  /** Fresh empty store for every variant. Owned connections are released even after failure. */
  create():
    | { deps: Memory.Deps; close?(): void | Promise<void> }
    | Promise<{ deps: Memory.Deps; close?(): void | Promise<void> }>
}
export type Run =
  | { id: string; components?: Record<string, string>; status: "complete"; report: Report }
  | { id: string; components?: Record<string, string>; status: "error"; error: string }

// @ref LLP 0024.001#portable-evaluation — same frozen input, fresh stores, failed variants never count as passes
export async function compare(input: Dataset, variants: Variant[]) {
  const data = Dataset.parse(input)
  if (
    variants.length < 2 ||
    variants.some((x) => !x.id.trim()) ||
    new Set(variants.map((x) => x.id)).size !== variants.length
  )
    throw new Error("Compare requires at least two uniquely named variants")
  const bytes = new TextEncoder().encode(JSON.stringify(data))
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))
  const datasetSha256 = [...digest].map((byte) => byte.toString(16).padStart(2, "0")).join("")
  const used = new Set<Store>()
  const runs: Run[] = []
  for (const variant of variants) {
    let resource: Awaited<ReturnType<Variant["create"]>> | undefined
    let components: Record<string, string> | undefined
    try {
      resource = await variant.create()
      const deps = resource.deps
      if (used.has(deps.store)) throw new Error("Variants must not share a store")
      used.add(deps.store)
      const picker =
        deps.picker ??
        (deps.options?.pick === "model"
          ? Pick.usingModel(deps.models?.pick ?? deps.model, deps.options.role)
          : Pick.usingDecider(deps.decider))
      components = {
        extract: (deps.models?.extract ?? deps.model).id,
        link: (deps.models?.link ?? deps.model).id,
        dossier: (deps.models?.dossier ?? deps.model).id,
        embedder: deps.embedder.id,
        picker: picker.id,
        detector: deps.options?.detect === false ? "disabled" : (deps.detector ?? deps.decider).id,
      }
      const report = await evaluate(data, deps)
      const owned = resource
      resource = undefined
      await owned.close?.()
      runs.push({ id: variant.id, status: "complete", components, report })
    } catch {
      // Plugin/provider exceptions can echo prompts or credentials. Detailed inspection belongs in the host.
      runs.push({
        id: variant.id,
        status: "error",
        components,
        error: "Variant failed; no complete score. Check configuration, fresh store, cache and providers.",
      })
    } finally {
      try {
        await resource?.close?.()
      } catch {
        /* The variant is already marked failed. */
      }
    }
  }
  const baseline = runs[0]!
  const paired = runs.slice(1).map((run) => {
    if (baseline.status !== "complete" || run.status !== "complete") return { id: run.id, comparable: false as const }
    const changes: { probe: string; metric: string; before: boolean; after: boolean }[] = []
    for (const [index, probe] of run.report.probes.entries()) {
      const original = baseline.report.probes[index]!
      for (const [metric, after] of Object.entries(probe.checks)) {
        const before = original.checks[metric]!
        if (before !== after) changes.push({ probe: probe.id, metric, before, after })
      }
    }
    return {
      id: run.id,
      comparable: true as const,
      improvedChecks: changes.filter((x) => x.after).length,
      regressedChecks: changes.filter((x) => !x.after).length,
      unchangedChecks: baseline.report.checks.total - changes.length,
      changes,
    }
  })
  return {
    dataset: data.id,
    datasetSha256,
    baseline: baseline.id,
    complete: runs.every((x) => x.status === "complete"),
    runs,
    paired,
  }
}
export type Comparison = Awaited<ReturnType<typeof compare>>
