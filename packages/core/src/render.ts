import type { Recall } from "@fluiddb/schema"
import { Prompt } from "./prompt"

export interface Options {
  name?: string
  directive?: boolean
  budget?: number
}

// Items in order until the share of the budget is used; always at least one (as the lab's answerer assembled evidence).
function fit(items: string[], share: number) {
  const out: string[] = []
  const used = { chars: 0 }
  for (const item of items) {
    if (out.length && used.chars + item.length > share) break
    out.push(item)
    used.chars += item.length
  }
  return out
}

// The memory for one turn, as a section of the assistant's prompt.
export function render(recall: Recall.Info, options: Options = {}) {
  const parts = [
    recall.dossier ? [recall.dossier] : [],
    recall.statements.map((x) => `- ${x.text}`),
    recall.windows.map((x) => x.text),
  ]
  const share = Math.floor((options.budget ?? 48_000) / Math.max(1, parts.filter((x) => x.length).length))
  const [dossier, statements, windows] = parts.map((x) => fit(x, share))
  const sections = [
    dossier?.length ? `## What you know about ${options.name ?? "them"}\n${dossier.join("\n")}` : "",
    statements?.length ? `## Recorded facts\n${statements.join("\n")}` : "",
    windows?.length ? `## Source evidence\n${windows.join("\n\n")}` : "",
    recall.retold ? retold(recall.retold) : "",
    options.directive === false ? "" : `## How to use this\n${Prompt.directive}`,
  ]
  return sections.filter(Boolean).join("\n\n")
}

// @ref LLP 0022#design — the note that doubled acknowledging the exact fact (10.2% → 22.0%, LLP 0022.000)
export function retold(input: NonNullable<Recall.Info["retold"]>) {
  const times = input.group.count > 1 ? `said ${input.group.count} times` : "said once"
  return (
    "## They are repeating something they told you before\n" +
    `They told you this before (first on ${input.group.first.slice(0, 10)}, ${times}): ${input.statement.text}. ` +
    "Acknowledge that you remember it; don't make them explain it again."
  )
}

export * as Render from "./render"
