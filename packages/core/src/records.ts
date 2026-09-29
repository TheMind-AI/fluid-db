import { Dossier, Group, Statement, Turn, Window } from "@fluiddb/schema"
import type { Changes, PageQuery, Store } from "./port"
import { Vector } from "./vector"
import { Write } from "./write"

// Implementation helper for document-oriented adapters. Products depend on Store, not this record layout.
export type Kind = "log" | "turn" | "window" | "statement" | "group" | "dossier" | "vector"
export interface Record {
  kind: Kind
  id: string
  data: { [key: string]: unknown }
}
export interface Query {
  kind: Kind
  ids?: string[]
  field?: "session" | "window" | "group" | "folded" | "vectorKind"
  values?: (string | boolean)[]
  after?: { at: string; id: string }
  limit?: number
  ordered?: boolean
}
export interface Mutation {
  kind: Kind
  id: string
  data?: Record["data"]
}
export interface Reader {
  read(person: string, query: Query): Promise<Record[]>
}
export interface Backend extends Reader {
  revision(person: string): Promise<string | null>
  transaction(
    person: string,
    work: (reader: Reader) => Promise<Mutation[]>,
    expected?: { revision: string | null },
  ): Promise<void>
  erase(person: string): Promise<void>
}

const compare = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0)
const record = (kind: Kind, id: string, data: Record["data"]): Mutation => ({ kind, id, data })
const parseWindow = (x: Record) => {
  const { folded: _, ...data } = x.data
  return Window.Info.parse(data)
}

// @ref LLP 0023.001#database-adapters — plan an atomic write from its pre-state, outside model/transport concerns
async function plan(person: string, changes: Changes, reader: Reader): Promise<Mutation[]> {
  const drop = changes.drop ?? {}
  const [windows, logs, seen, session] = await Promise.all([
    reader.read(person, {
      kind: "window",
      ...(drop.dossier
        ? {}
        : { ids: [...new Set([...(changes.windows ?? []).map((x) => x.id), ...(changes.dossier?.windows ?? [])])] }),
    }),
    reader.read(person, { kind: "log", ids: (changes.log?.turns ?? []).map((x) => x.id) }),
    reader.read(person, { kind: "turn", ids: (changes.log?.turns ?? []).map((x) => x.id) }),
    drop.session ? reader.read(person, { kind: "log", field: "session", values: [drop.session] }) : [],
  ])
  const out = new Map<string, Mutation>()
  const add = (x: Mutation) => out.set(`${x.kind}:${x.id}`, x)
  for (const [kind, ids] of [
    ["window", drop.windows],
    ["statement", drop.statements],
    ["group", drop.groups],
    ["log", [...(drop.log ?? []), ...session.map((x) => x.id)]],
  ] as [Kind, string[] | undefined][]) {
    for (const id of ids ?? []) {
      add({ kind, id })
      if (kind === "window" || kind === "statement") add({ kind: "vector", id })
    }
  }
  if (drop.dossier) {
    add({ kind: "dossier", id: "current" })
    for (const x of windows)
      if (!drop.windows?.includes(x.id)) add(record("window", x.id, { ...x.data, folded: false }))
  }
  const existing = new Set([...logs, ...seen].map((x) => x.id))
  for (const turn of changes.log?.turns ?? [])
    if (!existing.has(turn.id)) {
      add(record("log", turn.id, { ...turn, session: changes.log!.session }))
      existing.add(turn.id)
    }
  for (const x of changes.windows ?? [])
    add(
      record("window", x.id, {
        ...x,
        folded:
          !drop.dossier && !drop.windows?.includes(x.id) && Boolean(windows.find((w) => w.id === x.id)?.data.folded),
      }),
    )
  for (const x of changes.statements ?? []) add(record("statement", x.id, { ...x }))
  for (const x of changes.groups ?? []) add(record("group", x.id, { ...x }))
  for (const x of changes.vectors ?? [])
    add(record("vector", x.id, { vectorKind: x.kind, vector: Array.from(Vector.unit(x.vector)) }))
  for (const batch of Write.turnBatches(changes))
    for (const id of batch.ids) add(record("turn", id, { session: batch.session }))
  if (changes.dossier) {
    add(record("dossier", "current", { ...changes.dossier.info }))
    for (const id of changes.dossier.windows) {
      const next = out.get(`window:${id}`)
      const data = next ? next.data : windows.find((x) => x.id === id)?.data
      if (data) add(record("window", id, { ...data, folded: true }))
    }
  }
  return [...out.values()]
}

export function store(backend: Backend): Store {
  const read = (person: string, query: Query) =>
    query.ids?.length === 0 || query.values?.length === 0 ? Promise.resolve([]) : backend.read(person, query)
  const get = async (person: string, kind: Kind, ids: string[]) => {
    const rows = await read(person, { kind, ids })
    const byId = new Map(rows.map((x) => [x.id, x]))
    return ids.flatMap((id) => (byId.has(id) ? [byId.get(id)!] : []))
  }
  const page = (person: string, kind: "window" | "statement", query: PageQuery) =>
    read(person, {
      kind,
      ordered: true,
      limit: query.limit,
      after: query.after,
      ...(query.session ? { field: "session", values: [query.session] } : {}),
    })
  return {
    revision: (person) => backend.revision(person),
    log: {
      session: async (person, session) =>
        (await read(person, { kind: "log", field: "session", values: [session], ordered: true })).map((x) => {
          const { session: _, ...data } = x.data
          return Turn.Info.parse(data)
        }),
    },
    turns: { seen: async (person, ids) => (await get(person, "turn", ids)).map((x) => x.id) },
    windows: {
      get: async (person, ids) => (await get(person, "window", ids)).map(parseWindow),
      all: async (person) => (await read(person, { kind: "window", ordered: true })).map(parseWindow),
      session: async (person, session) =>
        (await read(person, { kind: "window", field: "session", values: [session], ordered: true })).map(parseWindow),
      pending: async (person) =>
        (await read(person, { kind: "window", field: "folded", values: [false], ordered: true })).map(parseWindow),
      list: async (person, query) => (await page(person, "window", query)).map(parseWindow),
    },
    statements: {
      get: async (person, ids) => (await get(person, "statement", ids)).map((x) => Statement.Info.parse(x.data)),
      list: async (person, query) => (await page(person, "statement", query)).map((x) => Statement.Info.parse(x.data)),
      windows: async (person, ids) =>
        (await read(person, { kind: "statement", field: "window", values: ids, ordered: true })).map((x) =>
          Statement.Info.parse(x.data),
        ),
      groups: async (person, ids) =>
        (await read(person, { kind: "statement", field: "group", values: ids, ordered: true })).map((x) =>
          Statement.Info.parse(x.data),
        ),
    },
    groups: {
      get: async (person, ids) => (await get(person, "group", ids)).map((x) => Group.Info.parse(x.data)),
      replacing: async (person, ids) =>
        (await read(person, { kind: "group" }))
          .map((x) => Group.Info.parse(x.data))
          .filter((x) => x.replaces.some((id) => ids.includes(id))),
    },
    dossiers: {
      get: async (person) => {
        const [row] = await get(person, "dossier", ["current"])
        return row ? Dossier.Info.parse(row.data) : undefined
      },
    },
    vectors: {
      query: async (person, vector, filter) =>
        (await read(person, { kind: "vector", field: "vectorKind", values: [filter.kind] }))
          .map((x) => ({ id: x.id, score: Vector.dot(vector, Float32Array.from(x.data.vector as number[])) }))
          .sort((a, b) => b.score - a.score || compare(a.id, b.id))
          .slice(0, filter.top),
    },
    write: async (person, changes, expected) => {
      Write.check(person, changes)
      await backend.transaction(person, (reader) => plan(person, changes, reader), expected)
    },
    erase: (person) => backend.erase(person),
  }
}
export * as Records from "./records"
