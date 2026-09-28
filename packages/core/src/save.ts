import { Api, Id, type Window, type Group, type Statement } from "@fluiddb/schema"
import { ConflictError } from "./error"
import type { Store, Embedder, Call } from "./port"

// @ref LLP 0023.001#sdk-boundary — explicit facts commit directly, with truthful provenance and stable retry IDs
export async function remember(
  store: Store,
  embedder: Embedder,
  person: string,
  input: Api.Remember,
  call?: Call,
): Promise<Api.Remembered> {
  Id.Info.parse(person)
  const request = Api.Remember.parse(input)
  call?.signal?.throwIfAborted()
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(request.id)))
  const key = [...digest].map((x) => x.toString(16).padStart(2, "0")).join("")
  const id = `saved_${key}`
  const windowId = `save_window_${key}`
  const groupId = `save_group_${key}`
  const revision = await store.revision(person)
  const [existing] = await store.statements.get(person, [id])
  const [previous] = request.replaces ? await store.statements.get(person, [request.replaces]) : []
  if (existing) {
    if (
      existing.text !== request.text ||
      existing.kind !== request.kind ||
      existing.session !== request.session ||
      existing.at !== request.at ||
      existing.save?.id !== request.id ||
      existing.save?.replaces !== request.replaces ||
      existing.pinned !== request.pinned ||
      (existing.save?.origin ?? "explicit") !== request.origin
    )
      throw new ConflictError("This save ID already exists with different content. Use a new ID for a new save.")
    return { saved: true, duplicate: true, statement: existing }
  }
  if ((await store.turns.seen(person, [id])).length)
    throw new ConflictError(
      "This save was forgotten. Reusing its ID cannot restore it; a new explicit save needs a new ID.",
    )
  if (request.replaces && !previous)
    throw new ConflictError("The replacement target does not exist in this person's memory.")
  const [priorGroup] = previous ? await store.groups.get(person, [previous.group]) : []
  if (previous && (!priorGroup || priorGroup.until))
    throw new ConflictError("The replacement target is no longer current. Inspect its history first.")
  if (priorGroup && request.at < priorGroup.last)
    throw new ConflictError("A correction cannot predate the fact it replaces.")
  const window: Window.Info = {
    id: windowId,
    person,
    session: request.session,
    at: request.at,
    text: `${request.origin === "import" ? "Imported memory" : request.origin === "agent" ? "Agent note saved" : "Explicitly saved"} on ${request.at}:\n${request.text}`,
    turns: [],
    sources: [],
    origin: request.origin,
  }
  const statement: Statement.Info = {
    id,
    person,
    session: request.session,
    at: request.at,
    text: request.text,
    kind: request.kind,
    window: windowId,
    group: groupId,
    pinned: request.pinned,
    save: {
      id: request.id,
      ...(request.replaces ? { replaces: request.replaces } : {}),
      ...(request.origin === "explicit" ? {} : { origin: request.origin }),
    },
  }
  const group: Group.Info = {
    id: groupId,
    person,
    count: 1,
    first: request.at,
    last: request.at,
    replaces: priorGroup ? [priorGroup.id] : [],
  }
  const vectors = await embedder.embed([window.text, statement.text], call)
  call?.signal?.throwIfAborted()
  await store.write(
    person,
    {
      windows: [window],
      statements: [statement],
      groups: [group, ...(priorGroup ? [{ ...priorGroup, until: request.at }] : [])],
      vectors: [
        { id: windowId, kind: "window", vector: vectors[0]! },
        { id, kind: "statement", vector: vectors[1]! },
      ],
      turns: { session: request.session, ids: [id] },
      // Never return a stale dossier that still asserts the replaced fact.
      ...(priorGroup ? { drop: { dossier: true } } : {}),
    },
    { revision },
  )
  return { saved: true, duplicate: false, statement }
}

export * as Save from "./save"
