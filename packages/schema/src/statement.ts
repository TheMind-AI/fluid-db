import { z } from "zod"
import { Id } from "./id"

export const Kind = z.enum(["people", "work", "life", "health", "struggle", "practice", "plan", "preference", "event"])
export type Kind = z.infer<typeof Kind>

// One lasting fact about the person, in the words the extractor wrote for one window. Statements are never merged
// away: a repeat or a change is recorded by linking it to a group (LLP 0021.000).
export const Info = z.strictObject({
  id: Id.Info,
  person: Id.Info,
  session: Id.Info,
  window: Id.Info,
  text: z.string().min(1).max(2_000),
  kind: Kind,
  at: z.iso.datetime({ offset: true }),
  group: Id.Info,
  // Explicitly saved facts cannot be superseded by automatic extraction.
  pinned: z.boolean().optional(),
  save: z
    .strictObject({
      id: Id.Info,
      replaces: Id.Info.optional(),
      origin: z.enum(["explicit", "agent", "import"]).optional(),
    })
    .optional(),
})
export type Info = z.infer<typeof Info>

export * as Statement from "./statement"
