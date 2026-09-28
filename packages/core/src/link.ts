import type { Group, Statement } from "@fluiddb/schema"
import { z } from "zod"
import type { Call, Ids, LanguageModel } from "./port"
import { Prompt } from "./prompt"

export const Output = z.strictObject({
  relation: z.enum(["same", "change", "new"]),
  n: z.int().describe("the number of the earlier statement it matches or changes; 0 if new"),
})

// `index` points into the earlier statements the model was shown.
export type Verdict = { relation: "same" | "change"; index: number } | { relation: "new" }

// @ref LLP 0020#part-b-creation — the model decides; cosine alone merged under 1% of repeats (LLP 0019.000)
export async function decide(model: LanguageModel, text: string, earlier: string[], call?: Call): Promise<Verdict> {
  if (!earlier.length) return { relation: "new" }
  const out = await model.object(Prompt.link({ text, earlier }), Output, call)
  if (out.relation === "new" || out.n < 1 || out.n > earlier.length) return { relation: "new" }
  return { relation: out.relation, index: out.n - 1 }
}

export type Decision = { relation: "same" | "change"; group: Group.Info } | { relation: "new" }

// The statement and the groups that change: a repeat joins its group; a change opens a new group that replaces the
// old one, which gets `until`; anything else opens a group of its own. Statements are never merged away.
export function apply(
  decision: Decision,
  draft: Omit<Statement.Info, "group">,
  ids: Ids,
): { statement: Statement.Info; groups: Group.Info[] } {
  // @ref LLP 0023#temporal-links — a return to an old value starts a new period, never extends an ended one
  if (decision.relation !== "new" && decision.group.until && draft.at >= decision.group.until)
    decision = { relation: "new" }
  if (decision.relation === "same") {
    const group = decision.group
    const updated = {
      ...group,
      count: group.count + 1,
      first: draft.at < group.first ? draft.at : group.first,
      last: draft.at > group.last ? draft.at : group.last,
    }
    return { statement: { ...draft, group: group.id }, groups: [updated] }
  }
  const fresh: Group.Info = {
    id: ids.next("grp"),
    person: draft.person,
    count: 1,
    first: draft.at,
    last: draft.at,
    replaces: decision.relation === "change" ? [decision.group.id] : [],
  }
  if (decision.relation === "new") return { statement: { ...draft, group: fresh.id }, groups: [fresh] }
  // Delayed sessions can arrive after a newer saved fact. Keep the earlier evidence historical.
  if (draft.at < decision.group.last) {
    return {
      statement: { ...draft, group: fresh.id },
      groups: [
        {
          ...fresh,
          replaces: [],
          until: draft.at < decision.group.first ? decision.group.first : decision.group.last,
        },
        { ...decision.group, replaces: [...decision.group.replaces, fresh.id] },
      ],
    }
  }
  return {
    statement: { ...draft, group: fresh.id },
    groups: [
      { ...fresh, ...(decision.group.until ? { until: decision.group.until } : {}) },
      { ...decision.group, until: draft.at },
    ],
  }
}

export * as Link from "./link"
