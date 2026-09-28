import { z } from "zod"
import { Id } from "./id"

// What to know before every conversation, kept current after each session (LLP 0020.000).
export const Info = z.strictObject({
  person: Id.Info,
  text: z.string(),
  at: z.iso.datetime({ offset: true }),
  updates: z.int().min(0),
})
export type Info = z.infer<typeof Info>

export * as Dossier from "./dossier"
