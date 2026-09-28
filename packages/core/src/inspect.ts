import { Api, Id } from "@fluiddb/schema"
import { z } from "zod"
import { ConflictError, InputError } from "./error"
import type { Store, Call } from "./port"

const Cursor = z.strictObject({
  person: Id.Info,
  kind: z.enum(["statements", "windows"]),
  session: Id.Info.optional(),
  at: z.iso.datetime({ offset: true }),
  id: Id.Info,
})

export async function page(store: Store, person: string, input: Api.Inspect = {}, call?: Call): Promise<Api.Page> {
  Id.Info.parse(person)
  const query = Api.Inspect.parse(input)
  let after: z.infer<typeof Cursor> | undefined
  if (query.cursor) {
    try {
      after = Cursor.parse(JSON.parse(decodeURIComponent(atob(query.cursor))))
      if (after.person !== person || after.kind !== query.kind || after.session !== query.session) throw new Error()
    } catch {
      throw new InputError("Invalid cursor. Restart this listing without a cursor.")
    }
  }
  call?.signal?.throwIfAborted()
  const revision = await store.revision(person)
  const rows = await store[query.kind].list(person, { limit: query.limit + 1, session: query.session, after })
  if (revision !== (await store.revision(person))) throw new ConflictError("Memory changed; restart the listing.")
  call?.signal?.throwIfAborted()
  const items = rows.slice(0, query.limit)
  const last = items.at(-1)
  const nextCursor =
    rows.length > query.limit && last
      ? btoa(
          encodeURIComponent(
            JSON.stringify({ person, kind: query.kind, session: query.session, at: last.at, id: last.id }),
          ),
        )
      : undefined
  return { kind: query.kind, items, ...(nextCursor ? { nextCursor } : {}) }
}

export async function evidence(store: Store, person: string, input: Api.Evidence, call?: Call): Promise<Api.Sources> {
  Id.Info.parse(person)
  const query = Api.Evidence.parse(input)
  call?.signal?.throwIfAborted()
  const revision = await store.revision(person)
  const statements = await store.statements.get(person, query.statements)
  const [windows, groups] = await Promise.all([
    store.windows.get(person, [...new Set([...query.windows, ...statements.map((x) => x.window)])]),
    store.groups.get(person, [...new Set(statements.map((x) => x.group))]),
  ])
  if (revision !== (await store.revision(person)))
    throw new ConflictError("Memory changed; retry the evidence request.")
  call?.signal?.throwIfAborted()
  return { statements, windows, groups }
}

export * as Inspect from "./inspect"

/** Read the complete source in bounded Unicode-code-point pages, without prefix clipping. */
export async function source(store: Store, person: string, input: Api.Source, call?: Call): Promise<Api.SourcePage> {
  Id.Info.parse(person)
  const query = Api.Source.parse(input)
  call?.signal?.throwIfAborted()
  const revision = await store.revision(person)
  const [window] = await store.windows.get(person, [query.window])
  if (!window) throw new InputError("Source window not found.")
  const chars = Array.from(window.text)
  if (query.offset > chars.length) throw new InputError("Source offset exceeds its length.")
  const end = Math.min(chars.length, query.offset + query.limit)
  call?.signal?.throwIfAborted()
  if (revision !== (await store.revision(person))) throw new ConflictError("Memory changed; retry the source request.")
  return {
    window: window.id,
    text: chars.slice(query.offset, end).join(""),
    at: window.at,
    ...(window.origin ? { origin: window.origin } : {}),
    offset: query.offset,
    total: chars.length,
    ...(end < chars.length ? { nextOffset: end } : {}),
  }
}
