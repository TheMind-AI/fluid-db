import type { Statement } from "@fluiddb/schema"
import type { Call, Decider } from "./port"
import { Prompt } from "./prompt"

// @ref LLP 0022#design — is the person repeating something already in memory, and which statement? A pick counts only
// at or above `threshold`: the precision knob a product tunes on its own traffic (LLP 0022.000).
export async function detect(
  decider: Decider,
  input: { message: string; statements: Statement.Info[]; threshold: number },
  call?: Call,
): Promise<{ statement: Statement.Info; probability: number } | undefined> {
  if (!input.statements.length) return undefined
  const memory = Object.fromEntries(input.statements.map((x, i) => [`s${i}`, x.text]))
  const options = { ...memory, none: "none of these: the person is saying something new, or nothing about themselves" }
  const out = await decider.choose({ message: input.message, memory }, Prompt.detect, options, call)
  const probability = out.probabilities[out.choice] ?? 0
  const statement = out.choice === "none" ? undefined : input.statements[Number(out.choice.slice(1))]
  if (!statement || probability < input.threshold) return undefined
  return { statement, probability }
}

export * as Detect from "./detect"
