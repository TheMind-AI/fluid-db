import { z } from "zod"
import { Group } from "./group"
import { Id } from "./id"
import { Statement } from "./statement"

export const Evidence = z.strictObject({
  kind: z.enum(["dossier", "statement", "window"]),
  text: z.string(),
  at: z.iso.datetime({ offset: true }).optional(),
  ids: z.array(Id.Info),
})
export type Evidence = z.infer<typeof Evidence>

// The person is repeating something already in memory (LLP 0022): the statement, its group, and the decider's
// probability.
export const Retold = z.strictObject({
  statement: Statement.Info,
  group: Group.Info,
  probability: z.number().min(0).max(1),
})
export type Retold = z.infer<typeof Retold>

// What the memory offers for one turn of a conversation.
export const Info = z.strictObject({
  person: Id.Info,
  dossier: z.string().optional(),
  statements: z.array(Evidence),
  windows: z.array(Evidence),
  retold: Retold.optional(),
})
export type Info = z.infer<typeof Info>

export * as Recall from "./recall"
