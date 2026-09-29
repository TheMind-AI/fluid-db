import type { Dossier, Group, Statement, Turn, Window } from "@fluiddb/schema"
import type { Entry, Match, Store, PageQuery } from "./port"
import { Vector } from "./vector"
import { ConflictError } from "./error"
import { Write } from "./write"

// In-memory adapters: the reference implementation of the Store, for tests and local development.

const byTime = <T extends { at: string; id: string }>(rows: T[]) =>
  rows.toSorted((a, b) => (a.at < b.at ? -1 : a.at > b.at ? 1 : a.id < b.id ? -1 : a.id > b.id ? 1 : 0))

export function store(): Store {
  const key = (person: string, id: string) => JSON.stringify([person, id])
  const revisions = new Map<string, string>()
  const db = {
    log: new Map<string, { person: string; session: string; turn: Turn.Info }>(),
    turns: new Map<string, { person: string; session: string }>(),
    windows: new Map<string, { row: Window.Info; folded: boolean }>(),
    statements: new Map<string, Statement.Info>(),
    groups: new Map<string, Group.Info>(),
    dossiers: new Map<string, Dossier.Info>(),
    vectors: new Map<string, Entry & { person: string }>(),
  }
  const rows = <T>(map: Map<string, T>, person: string, ids: string[]) =>
    ids.flatMap((id) => {
      const row = map.get(key(person, id))
      return row === undefined ? [] : [structuredClone(row)]
    })
  const windows = (person: string, keep: (x: { row: Window.Info; folded: boolean }) => boolean) =>
    byTime(
      [...db.windows.values()].filter((x) => x.row.person === person && keep(x)).map((x) => structuredClone(x.row)),
    )
  const statements = (person: string, keep: (x: Statement.Info) => boolean) =>
    byTime([...db.statements.values()].filter((x) => x.person === person && keep(x)).map((x) => structuredClone(x)))
  const page = <T extends { at: string; id: string; session: string }>(rows: T[], query: PageQuery) =>
    rows
      .filter(
        (x) =>
          (!query.session || x.session === query.session) &&
          (!query.after || x.at > query.after.at || (x.at === query.after.at && x.id > query.after.id)),
      )
      .slice(0, query.limit)

  return {
    revision: async (person) => revisions.get(person) ?? null,
    erase: async (person) => {
      // @ref LLP 0023#forgetting — invalidate in-flight work even when this person has never been written
      revisions.set(person, crypto.randomUUID())
      for (const [key, row] of db.log) if (row.person === person) db.log.delete(key)
      for (const [key, row] of db.turns) if (row.person === person) db.turns.delete(key)
      for (const [key, row] of db.windows) if (row.row.person === person) db.windows.delete(key)
      for (const [key, row] of db.statements) if (row.person === person) db.statements.delete(key)
      for (const [key, row] of db.groups) if (row.person === person) db.groups.delete(key)
      for (const [key, row] of db.vectors) if (row.person === person) db.vectors.delete(key)
      db.dossiers.delete(person)
    },
    log: {
      session: async (person, session) =>
        byTime(
          [...db.log.values()]
            .filter((x) => x.person === person && x.session === session)
            .map((x) => structuredClone(x.turn)),
        ),
    },
    turns: {
      seen: async (person, ids) => ids.filter((id) => db.turns.has(key(person, id))),
    },
    windows: {
      list: async (person, query) =>
        page(
          windows(person, () => true),
          query,
        ),
      get: async (person, ids) => rows(db.windows, person, ids).map((x) => x.row),
      session: async (person, session) => windows(person, (x) => x.row.session === session),
      all: async (person) => windows(person, () => true),
      pending: async (person) => windows(person, (x) => !x.folded),
    },
    statements: {
      list: async (person, query) =>
        page(
          statements(person, () => true),
          query,
        ),
      get: async (person, ids) => rows(db.statements, person, ids),
      windows: async (person, ids) => statements(person, (x) => ids.includes(x.window)),
      groups: async (person, ids) => statements(person, (x) => ids.includes(x.group)),
    },
    groups: {
      get: async (person, ids) => rows(db.groups, person, ids),
      replacing: async (person, ids) =>
        [...db.groups.values()]
          .filter((x) => x.person === person && x.replaces.some((id) => ids.includes(id)))
          .map((x) => structuredClone(x)),
    },
    dossiers: {
      get: async (person) => structuredClone(db.dossiers.get(person)),
    },
    vectors: {
      query: async (person, vector, filter): Promise<Match[]> =>
        [...db.vectors.values()]
          .filter((x) => x.person === person && x.kind === filter.kind)
          .map((x) => ({ id: x.id, score: Vector.dot(vector, x.vector) }))
          .toSorted((a, b) => b.score - a.score || a.id.localeCompare(b.id))
          .slice(0, filter.top),
    },
    write: async (person, changes, expected) => {
      Write.check(person, changes)
      if (expected && expected.revision !== (revisions.get(person) ?? null))
        throw new ConflictError("Memory changed; retry with the same IDs.")
      revisions.set(person, crypto.randomUUID())
      const drop = changes.drop ?? {}
      for (const id of drop.windows ?? []) {
        db.windows.delete(key(person, id))
        db.vectors.delete(key(person, id))
      }
      for (const id of drop.statements ?? []) {
        db.statements.delete(key(person, id))
        db.vectors.delete(key(person, id))
      }
      for (const id of drop.groups ?? []) db.groups.delete(key(person, id))
      for (const id of drop.log ?? []) db.log.delete(key(person, id))
      if (drop.session) {
        for (const [k, x] of db.log) if (x.person === person && x.session === drop.session) db.log.delete(k)
      }
      if (drop.dossier) {
        db.dossiers.delete(person)
        for (const x of db.windows.values()) if (x.row.person === person) x.folded = false
      }
      for (const turn of changes.log?.turns ?? []) {
        const k = key(person, turn.id)
        if (!db.log.has(k) && !db.turns.has(k)) {
          db.log.set(k, { person, session: changes.log!.session, turn: structuredClone(turn) })
        }
      }
      for (const row of changes.windows ?? []) {
        const folded = db.windows.get(key(person, row.id))?.folded ?? false
        db.windows.set(key(person, row.id), { row: structuredClone(row), folded })
      }
      for (const row of changes.statements ?? []) db.statements.set(key(person, row.id), structuredClone(row))
      for (const row of changes.groups ?? []) db.groups.set(key(person, row.id), structuredClone(row))
      for (const x of changes.vectors ?? []) {
        db.vectors.set(key(person, x.id), { id: x.id, kind: x.kind, person, vector: Vector.unit(x.vector) })
      }
      for (const batch of Write.turnBatches(changes))
        for (const id of batch.ids) db.turns.set(key(person, id), { person, session: batch.session })
      if (changes.dossier) {
        db.dossiers.set(person, structuredClone(changes.dossier.info))
        for (const id of changes.dossier.windows) {
          const window = db.windows.get(key(person, id))
          if (window) window.folded = true
        }
      }
    },
  }
}

export * as Local from "./local"
