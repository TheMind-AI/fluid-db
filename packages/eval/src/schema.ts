import { Id, Turn } from "@fluiddb/schema"
import { z } from "zod"

const ingest = z.strictObject({
  type: z.literal("ingest"),
  person: Id.Info,
  session: Id.Info,
  turns: z.array(Turn.Info).min(1),
})
const forget = z.strictObject({ type: z.literal("forget"), person: Id.Info, session: Id.Info })
const expected = z
  .strictObject({
    sources: z.array(Id.Info).min(1).optional(),
    absentSources: z.array(Id.Info).min(1).optional(),
    contains: z.array(z.string().min(1)).min(1).optional(),
    absent: z.array(z.string().min(1)).min(1).optional(),
    retold: z.boolean().optional(),
    empty: z.literal(true).optional(),
  })
  .refine((value) => Object.keys(value).length > 0, "A probe needs at least one expectation")
const probe = z.strictObject({
  type: z.literal("probe"),
  id: Id.Info,
  person: Id.Info,
  conversation: z.array(Turn.Info).min(1),
  expected,
})

export const Dataset = z
  .strictObject({
    id: Id.Info,
    description: z.string(),
    synthetic: z.boolean().default(false),
    keep: z.strictObject({ statements: z.int().min(1).max(200), windows: z.int().min(1).max(200) }),
    steps: z.array(z.discriminatedUnion("type", [ingest, forget, probe])).min(1),
  })
  .superRefine((data, ctx) => {
    const seen = new Map<string, Map<string, string>>()
    const probes = new Set<string>()
    for (const [index, step] of data.steps.entries()) {
      const turns = seen.get(step.person) ?? new Map<string, string>()
      seen.set(step.person, turns)
      const issue = (message: string) => ctx.addIssue({ code: "custom", path: ["steps", index], message })
      if (step.type === "ingest") {
        for (const turn of step.turns) {
          const value = JSON.stringify([step.session, turn])
          if (turns.has(turn.id) && turns.get(turn.id) !== value) issue("A redelivered turn changed")
          turns.set(turn.id, value)
        }
      }
      if (step.type !== "probe") continue
      if (probes.has(step.id)) issue("Probe IDs must be unique")
      probes.add(step.id)
      for (const source of [...(step.expected.sources ?? []), ...(step.expected.absentSources ?? [])]) {
        if (!turns.has(source)) issue("A labelled source must have been ingested for this person before the probe")
      }
    }
    if (!probes.size) ctx.addIssue({ code: "custom", message: "A dataset needs at least one probe" })
  })
export type Dataset = z.infer<typeof Dataset>
