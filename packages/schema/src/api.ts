import { z } from "zod"
import { Id } from "./id"
import { Recall } from "./recall"
import { Turn } from "./turn"
import { Statement } from "./statement"
import { Window } from "./window"
import { Group } from "./group"

// The HTTP API's bodies (@fluiddb/worker, @fluiddb/client).

export const Ingest = z.strictObject({ turns: z.array(Turn.Info).min(1).max(500) })
export type Ingest = z.infer<typeof Ingest>

// Turns taken into the person's inbox; they are ingested in the background.
export const Accepted = z.strictObject({ accepted: z.int(), pending: z.int() })
export type Accepted = z.infer<typeof Accepted>

export const Ingested = z.strictObject({
  turns: z.int(),
  windows: z.int(),
  statements: z.int(),
  linked: z.int(),
})
export type Ingested = z.infer<typeof Ingested>

export const Ask = z.strictObject({
  conversation: z.array(Turn.Info).max(200),
  render: z.boolean().default(true),
  name: z.string().min(1).max(200).optional(),
})
export type Ask = z.input<typeof Ask>

export const Answer = z.strictObject({ recall: Recall.Info, prompt: z.string().optional() })
export type Answer = z.infer<typeof Answer>

export const Remember = z.strictObject({
  id: Id.Info.describe("Stable caller-generated save ID. Reuse only to retry this exact save."),
  session: Id.Info,
  text: z.string().trim().min(1).max(2_000),
  kind: Statement.Kind,
  at: z.iso.datetime({ offset: true }).transform((at) => new Date(at).toISOString()),
  replaces: Id.Info.optional(),
  pinned: z
    .boolean()
    .default(true)
    .describe("Protect this fact from automatic supersession. Use false for proactive or imported unpinned notes."),
  origin: z
    .enum(["explicit", "agent", "import"])
    .default("explicit")
    .describe("Who supplied this saved fact; imported notes are not conversation turns."),
})
export type Remember = z.input<typeof Remember>

export const Remembered = z.strictObject({
  saved: z.literal(true),
  duplicate: z.boolean(),
  statement: Statement.Info,
})
export type Remembered = z.infer<typeof Remembered>

export const Inspect = z.strictObject({
  kind: z.enum(["statements", "windows"]).default("statements"),
  session: Id.Info.optional(),
  cursor: z.string().min(1).max(2_000).optional(),
  limit: z.int().min(1).max(100).default(20),
})
export type Inspect = z.input<typeof Inspect>
export const Page = z.strictObject({
  kind: z.enum(["statements", "windows"]),
  items: z.array(z.union([Statement.Info, Window.Info])),
  nextCursor: z.string().optional(),
})
export type Page = z.infer<typeof Page>

export const Evidence = z.strictObject({
  statements: z.array(Id.Info).max(20).default([]),
  windows: z.array(Id.Info).max(20).default([]),
})
export type Evidence = z.input<typeof Evidence>
export const Sources = z.strictObject({
  statements: z.array(Statement.Info),
  windows: z.array(Window.Info),
  groups: z.array(Group.Info),
})
export type Sources = z.infer<typeof Sources>

export const Forget = z
  .strictObject({
    session: Id.Info.optional(),
    statements: z.array(Id.Info).max(1_000).optional(),
    windows: z.array(Id.Info).max(1_000).optional(),
    revision: z
      .string()
      .nullable()
      .optional()
      .describe("Revision returned by previewForget; reject a changed deletion scope."),
  })
  .refine((x) => x.session || x.statements?.length || x.windows?.length, "name a session, statements or windows")
export type Forget = z.infer<typeof Forget>

export const ForgetPreview = z.strictObject({
  revision: z.string().nullable(),
  windows: z.array(Window.Info),
  statements: z.array(Statement.Info),
})
export type ForgetPreview = z.infer<typeof ForgetPreview>

export const Source = z.strictObject({
  window: Id.Info,
  offset: z.int().min(0).default(0),
  limit: z.int().min(1).max(4_000).default(1_200),
})
export type Source = z.input<typeof Source>
export const SourcePage = z.strictObject({
  window: Id.Info,
  text: z.string(),
  at: z.string(),
  origin: z.string().optional(),
  offset: z.int(),
  total: z.int(),
  nextOffset: z.int().optional(),
})
export type SourcePage = z.infer<typeof SourcePage>

export const Forgotten = z.strictObject({ windows: z.int(), statements: z.int(), groups: z.int() })
export type Forgotten = z.infer<typeof Forgotten>

export const Erased = z.strictObject({ erased: z.literal(true) })
export type Erased = z.infer<typeof Erased>

export const Status = z.strictObject({
  inbox: z.strictObject({ pending: z.int(), failed: z.int() }),
  // `pending`: windows not yet in the dossier
  windows: z.strictObject({ total: z.int(), pending: z.int() }),
  statements: z.int(),
  groups: z.int(),
  dossier: z.strictObject({ updates: z.int(), at: z.iso.datetime({ offset: true }) }).optional(),
})
export type Status = z.infer<typeof Status>

// Errors name their kind and never echo what was sent.
export const Failure = z.strictObject({
  error: z.strictObject({ name: z.string(), message: z.string() }),
})
export type Failure = z.infer<typeof Failure>

export * as Api from "./api"
