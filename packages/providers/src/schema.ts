import { z } from "zod"

// A zod schema as the JSON schema strict structured output accepts: without "$schema", and without the safe-integer
// bounds zod gives every integer. The memory's output schemas are strict objects with every field required, as strict
// mode wants.
export function strict(schema: z.ZodType): Record<string, unknown> {
  const clean = (node: unknown): unknown => {
    if (Array.isArray(node)) return node.map(clean)
    if (!node || typeof node !== "object") return node
    return Object.fromEntries(
      Object.entries(node)
        .filter(([key, value]) => {
          if (key === "$schema") return false
          if (key === "minimum" && value === Number.MIN_SAFE_INTEGER) return false
          if (key === "maximum" && value === Number.MAX_SAFE_INTEGER) return false
          return true
        })
        .map(([key, value]) => [key, clean(value)]),
    )
  }
  return clean(z.toJSONSchema(schema)) as Record<string, unknown>
}

export * as Schema from "./schema"
