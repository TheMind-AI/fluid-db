import { Statement } from "@fluiddb/schema"
import { z } from "zod"
import type { Call, LanguageModel } from "./port"
import { Prompt } from "./prompt"

export const Output = z.strictObject({
  items: z.array(z.strictObject({ text: z.string(), kind: Statement.Kind })),
})

export interface Draft {
  text: string
  kind: Statement.Kind
}

// The lasting things one window says about the person. Output schemas stay within what strict structured output
// accepts: no string lengths.
export async function statements(
  model: LanguageModel,
  input: Prompt.Voice & { date: string; text: string },
  call?: Call,
): Promise<Draft[]> {
  const out = await model.object(Prompt.statements(input), Output, call)
  return out.items.map((x) => ({ text: x.text.trim(), kind: x.kind })).filter((x) => x.text.length > 0)
}

export * as Extract from "./extract"
