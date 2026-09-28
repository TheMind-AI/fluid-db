import { z } from "zod"
export { Dataset } from "@fluiddb/eval"

const Model = z.strictObject({
  model: z.string().min(1),
  maxOutputTokens: z.int().min(1).max(32_768).default(4_096),
  effort: z.enum(["none", "minimal", "low", "medium", "high"]).nullable().default("low"),
  prices: z.strictObject({ input: z.number().positive(), output: z.number().positive() }).optional(),
})
export const Config = z.strictObject({
  model: z.string().min(1),
  embedding: z.string().min(1),
  maxOutputTokens: z.int().min(1).max(32_768).default(4_096),
  effort: z.enum(["none", "minimal", "low", "medium", "high"]).nullable().default("low"),
  models: z
    .strictObject({
      extract: Model.optional(),
      link: Model.optional(),
      dossier: Model.optional(),
      pick: Model.optional(),
      detector: Model.optional(),
    })
    .optional(),
  jev: z.strictObject({ model: z.string().min(1), inputPrice: z.number().positive() }).optional(),
  // USD per million tokens, supplied explicitly for live runs rather than silently using stale prices.
  prices: z
    .strictObject({
      input: z.number().positive(),
      output: z.number().positive(),
      embedding: z.number().positive(),
    })
    .optional(),
})
export type Config = z.infer<typeof Config>
