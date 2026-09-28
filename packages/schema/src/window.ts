import { z } from "zod"
import { Id } from "./id"

// Up to six of the person's consecutive messages in one session, each with the assistant's question before it
// (LLP 0017#data): "yes, mostly at night" means nothing without the question.
export const Info = z.strictObject({
  id: Id.Info,
  person: Id.Info,
  session: Id.Info,
  at: z.iso.datetime({ offset: true }),
  text: z.string(),
  turns: z.array(Id.Info),
  // Raw messages used by this window, including the assistant's preceding question.
  sources: z.array(Id.Info).optional(),
  // Explicit saves are source records, not fabricated conversation turns.
  origin: z.enum(["conversation", "explicit", "agent", "import"]).optional(),
})
export type Info = z.infer<typeof Info>

export * as Window from "./window"
