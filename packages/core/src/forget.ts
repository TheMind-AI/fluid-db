import { Api, Id } from "@fluiddb/schema"
import { ConflictError } from "./error"
import type { Call, Store } from "./port"

// @ref LLP 0023#forgetting — preview and deletion share the transitive source-window closure
export async function preview(
  store: Store,
  person: string,
  input: Api.Forget,
  call?: Call,
): Promise<Api.ForgetPreview> {
  Id.Info.parse(person)
  const request = Api.Forget.parse(input)
  call?.signal?.throwIfAborted()
  const revision = await store.revision(person)
  if (request.revision !== undefined && request.revision !== revision)
    throw new ConflictError("Memory changed; preview the deletion again.")
  const [session, named, picked] = await Promise.all([
    request.session ? store.windows.session(person, request.session) : [],
    request.windows?.length ? store.windows.get(person, request.windows) : [],
    request.statements?.length ? store.statements.get(person, request.statements) : [],
  ])
  const sources = picked.length
    ? await store.windows.get(
        person,
        picked.map((row) => row.window),
      )
    : []
  const selected = new Map([...session, ...named, ...sources].map((window) => [window.id, window]))
  if (selected.size) {
    const all = await store.windows.all(person)
    let previous = -1
    while (selected.size !== previous) {
      previous = selected.size
      const turns = new Set([...selected.values()].flatMap((window) => window.turns))
      for (const window of all) if (window.turns.some((id) => turns.has(id))) selected.set(window.id, window)
    }
  }
  const windows = [...selected.values()]
  const inside = windows.length
    ? await store.statements.windows(
        person,
        windows.map((window) => window.id),
      )
    : []
  const statements = [...new Map([...inside, ...picked].map((row) => [row.id, row])).values()]
  call?.signal?.throwIfAborted()
  if (revision !== (await store.revision(person)))
    throw new ConflictError("Memory changed; preview the deletion again.")
  return { revision, windows, statements }
}

export * as Forget from "./forget"
