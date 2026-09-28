import { z } from "zod"

// Ids come from the product (person, session, turn) or are made here (window, statement, group). Plain strings, bounded.
export const Info = z.string().min(1).max(200)
export type Info = z.infer<typeof Info>

export * as Id from "./id"
