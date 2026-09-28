import { z } from "zod"
import { Id } from "./id"

// One message of a conversation. `person` is the human the memory is about; `assistant` is the product's side.
export const Role = z.enum(["person", "assistant"])
export type Role = z.infer<typeof Role>

export const Info = z.strictObject({
  id: Id.Info,
  role: Role,
  text: z.string().max(200_000),
  at: z.iso.datetime({ offset: true }).transform((at) => new Date(at).toISOString()),
})
export type Info = z.infer<typeof Info>

export * as Turn from "./turn"
