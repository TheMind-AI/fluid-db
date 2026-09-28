import { z } from "zod"
import { Id } from "./id"

// The statements that say the same fact. `count` is how often it was said; `until` is set when a later statement
// changed it, and the changing group lists it in `replaces`.
export const Info = z.strictObject({
  id: Id.Info,
  person: Id.Info,
  count: z.int().min(0),
  first: z.iso.datetime({ offset: true }),
  last: z.iso.datetime({ offset: true }),
  until: z.iso.datetime({ offset: true }).optional(),
  replaces: z.array(Id.Info),
})
export type Info = z.infer<typeof Info>

export * as Group from "./group"
