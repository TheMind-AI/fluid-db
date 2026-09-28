import {
  ConflictError,
  Vector,
  Write,
  type Changes,
  type Kind,
  type Match,
  type Store,
  type PageQuery,
} from "@fluiddb/core"
import type { Dossier, Group, Statement, Turn, Window } from "@fluiddb/schema"
import { Migrate } from "./migrate"
import { Sql } from "./sql"

export const migrations = [
  [
    `CREATE TABLE fluid_turns (
      person TEXT NOT NULL, id TEXT NOT NULL, session TEXT NOT NULL,
      PRIMARY KEY (person, id))`,
    "CREATE INDEX fluid_turns_session ON fluid_turns (person, session)",
    `CREATE TABLE fluid_windows (
      person TEXT NOT NULL, id TEXT NOT NULL, session TEXT NOT NULL, at TEXT NOT NULL, text TEXT NOT NULL,
      turns TEXT NOT NULL, folded INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY (person, id))`,
    "CREATE INDEX fluid_windows_session ON fluid_windows (person, session)",
    "CREATE INDEX fluid_windows_at ON fluid_windows (person, at, id)",
    `CREATE TABLE fluid_statements (
      person TEXT NOT NULL, id TEXT NOT NULL, session TEXT NOT NULL, window_id TEXT NOT NULL, text TEXT NOT NULL,
      kind TEXT NOT NULL, at TEXT NOT NULL, group_id TEXT NOT NULL,
      PRIMARY KEY (person, id))`,
    "CREATE INDEX fluid_statements_window ON fluid_statements (person, window_id)",
    "CREATE INDEX fluid_statements_group ON fluid_statements (person, group_id)",
    `CREATE TABLE fluid_groups (
      person TEXT NOT NULL, id TEXT NOT NULL, count INTEGER NOT NULL, first_at TEXT NOT NULL, last_at TEXT NOT NULL,
      until_at TEXT, replaces TEXT NOT NULL,
      PRIMARY KEY (person, id))`,
    `CREATE TABLE fluid_dossiers (
      person TEXT PRIMARY KEY, text TEXT NOT NULL, at TEXT NOT NULL, updates INTEGER NOT NULL)`,
    `CREATE TABLE fluid_vectors (
      person TEXT NOT NULL, id TEXT NOT NULL, kind TEXT NOT NULL, vector BLOB NOT NULL,
      PRIMARY KEY (person, id))`,
  ],
  [
    `CREATE TABLE fluid_log (
      person TEXT NOT NULL, id TEXT NOT NULL, session TEXT NOT NULL, at TEXT NOT NULL, turn TEXT NOT NULL,
      PRIMARY KEY (person, id))`,
    "CREATE INDEX fluid_log_session ON fluid_log (person, session, at, id)",
    "ALTER TABLE fluid_windows ADD COLUMN sources TEXT NOT NULL DEFAULT '[]'",
  ],
  [
    "ALTER TABLE fluid_windows ADD COLUMN origin TEXT",
    "ALTER TABLE fluid_statements ADD COLUMN pinned INTEGER",
    "ALTER TABLE fluid_statements ADD COLUMN save TEXT",
    "CREATE INDEX fluid_statements_at ON fluid_statements (person, at, id)",
  ],
  ["CREATE TABLE fluid_revisions (person TEXT PRIMARY KEY, revision TEXT NOT NULL)"],
]

const window = (row: Record<string, unknown>): Window.Info => ({
  id: String(row.id),
  person: String(row.person),
  session: String(row.session),
  at: String(row.at),
  text: String(row.text),
  turns: JSON.parse(String(row.turns)),
  ...(row.sources && row.sources !== "[]" ? { sources: JSON.parse(String(row.sources)) } : {}),
  ...(row.origin ? { origin: String(row.origin) as Window.Info["origin"] } : {}),
})

const statement = (row: Record<string, unknown>): Statement.Info => ({
  id: String(row.id),
  person: String(row.person),
  session: String(row.session),
  window: String(row.window_id),
  text: String(row.text),
  kind: String(row.kind) as Statement.Kind,
  at: String(row.at),
  group: String(row.group_id),
  ...(row.pinned == null ? {} : { pinned: Boolean(row.pinned) }),
  ...(row.save == null ? {} : { save: JSON.parse(String(row.save)) }),
})

const group = (row: Record<string, unknown>): Group.Info => ({
  id: String(row.id),
  person: String(row.person),
  count: Number(row.count),
  first: String(row.first_at),
  last: String(row.last_at),
  ...(row.until_at === null || row.until_at === undefined ? {} : { until: String(row.until_at) }),
  replaces: JSON.parse(String(row.replaces)),
})

const blob = (vector: Float32Array) =>
  vector.buffer.slice(vector.byteOffset, vector.byteOffset + vector.byteLength) as ArrayBuffer

const floats = (value: unknown) => {
  const bytes = value instanceof ArrayBuffer ? new Uint8Array(value) : (value as Uint8Array)
  return new Float32Array(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength))
}

export interface Counts {
  windows: number
  pending: number
  statements: number
  groups: number
}

// The Store on SQLite. Vectors are kept in memory per person once read, and searched exactly: one person's memory is
// thousands of vectors, not millions.
export function store(sql: Sql) {
  Migrate.run(sql, "store", migrations)
  const cache = new Map<string, Map<string, { kind: Kind; vector: Float32Array }>>()

  const cachedRevisions = new Map<string, string | null>()
  const vectors = (person: string) => {
    const current = revision(person)
    if (cachedRevisions.get(person) !== current) cache.delete(person)
    cachedRevisions.set(person, current)
    const found = cache.get(person)
    if (found) return found
    const loaded = new Map(
      sql
        .run("SELECT id, kind, vector FROM fluid_vectors WHERE person = ?", person)
        .map((x) => [String(x.id), { kind: String(x.kind) as Kind, vector: floats(x.vector) }]),
    )
    cache.set(person, loaded)
    return loaded
  }

  // Rows by id, in the order asked for.
  const byId = <T extends { id: string }>(table: string, parse: (row: Record<string, unknown>) => T) => {
    return async (person: string, ids: string[]) => {
      const rows = Sql.slices([...new Set(ids)]).flatMap((part) =>
        sql.run(`SELECT * FROM ${table} WHERE person = ? AND id IN (${Sql.marks(part.length)})`, person, ...part),
      )
      const found = new Map(rows.map((x) => [String(x.id), parse(x)]))
      return ids.flatMap((id) => (found.has(id) ? [found.get(id)!] : []))
    }
  }

  const statementsBy = (column: "window_id" | "group_id") => async (person: string, ids: string[]) =>
    Sql.slices([...new Set(ids)])
      .flatMap((part) =>
        sql.run(
          `SELECT * FROM fluid_statements WHERE person = ? AND ${column} IN (${Sql.marks(part.length)})`,
          person,
          ...part,
        ),
      )
      .map(statement)
      .toSorted((a, b) => a.at.localeCompare(b.at) || a.id.localeCompare(b.id))

  const windows = (where: string, ...params: string[]) =>
    sql.run(`SELECT * FROM fluid_windows WHERE ${where} ORDER BY at, id`, ...params).map(window)

  const page =
    <T>(table: "fluid_windows" | "fluid_statements", parse: (row: Record<string, unknown>) => T) =>
    async (person: string, query: PageQuery): Promise<T[]> => {
      const clauses = ["person = ?"]
      const params: (string | number)[] = [person]
      if (query.session) {
        clauses.push("session = ?")
        params.push(query.session)
      }
      if (query.after) {
        clauses.push("(at > ? OR (at = ? AND id > ?))")
        params.push(query.after.at, query.after.at, query.after.id)
      }
      return sql
        .run(`SELECT * FROM ${table} WHERE ${clauses.join(" AND ")} ORDER BY at, id LIMIT ?`, ...params, query.limit)
        .map(parse)
    }

  const each = (query: string, person: string, ids: string[]) => {
    for (const part of Sql.slices(ids)) sql.run(query.replace("(?)", `(${Sql.marks(part.length)})`), person, ...part)
  }

  const apply = (person: string, changes: Changes) => {
    const drop = changes.drop ?? {}
    each("DELETE FROM fluid_windows WHERE person = ? AND id IN (?)", person, drop.windows ?? [])
    each("DELETE FROM fluid_statements WHERE person = ? AND id IN (?)", person, drop.statements ?? [])
    each("DELETE FROM fluid_vectors WHERE person = ? AND id IN (?)", person, [
      ...(drop.windows ?? []),
      ...(drop.statements ?? []),
    ])
    each("DELETE FROM fluid_groups WHERE person = ? AND id IN (?)", person, drop.groups ?? [])
    each("DELETE FROM fluid_log WHERE person = ? AND id IN (?)", person, drop.log ?? [])
    if (drop.session) sql.run("DELETE FROM fluid_log WHERE person = ? AND session = ?", person, drop.session)
    if (drop.dossier) {
      sql.run("DELETE FROM fluid_dossiers WHERE person = ?", person)
      sql.run("UPDATE fluid_windows SET folded = 0 WHERE person = ?", person)
    }
    for (const turn of changes.log?.turns ?? []) {
      sql.run(
        `INSERT OR IGNORE INTO fluid_log (person, id, session, at, turn)
         SELECT ?, ?, ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM fluid_turns WHERE person = ? AND id = ?)`,
        person,
        turn.id,
        changes.log!.session,
        turn.at,
        JSON.stringify(turn),
        person,
        turn.id,
      )
    }
    for (const x of changes.windows ?? []) {
      sql.run(
        `INSERT INTO fluid_windows (person, id, session, at, text, turns, sources, origin) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
         ON CONFLICT (person, id) DO UPDATE SET
           session = excluded.session, at = excluded.at, text = excluded.text, turns = excluded.turns,
           sources = excluded.sources, origin = excluded.origin`,
        person,
        x.id,
        x.session,
        x.at,
        x.text,
        JSON.stringify(x.turns),
        JSON.stringify(x.sources ?? []),
        x.origin ?? null,
      )
    }
    for (const x of changes.statements ?? []) {
      sql.run(
        `INSERT OR REPLACE INTO fluid_statements (person, id, session, window_id, text, kind, at, group_id, pinned, save)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
        person,
        x.id,
        x.session,
        x.window,
        x.text,
        x.kind,
        x.at,
        x.group,
        x.pinned === undefined ? null : Number(x.pinned),
        x.save ? JSON.stringify(x.save) : null,
      )
    }
    for (const x of changes.groups ?? []) {
      sql.run(
        `INSERT OR REPLACE INTO fluid_groups (person, id, count, first_at, last_at, until_at, replaces)
         VALUES (?, ?, ?, ?, ?, ?, ?)`,
        person,
        x.id,
        x.count,
        x.first,
        x.last,
        x.until ?? null,
        JSON.stringify(x.replaces),
      )
    }
    for (const x of changes.vectors ?? []) {
      sql.run(
        "INSERT OR REPLACE INTO fluid_vectors (person, id, kind, vector) VALUES (?, ?, ?, ?)",
        person,
        x.id,
        x.kind,
        blob(Vector.unit(x.vector)),
      )
    }
    if (changes.turns) {
      for (const id of changes.turns.ids) {
        sql.run(
          "INSERT OR REPLACE INTO fluid_turns (person, id, session) VALUES (?, ?, ?)",
          person,
          id,
          changes.turns.session,
        )
      }
    }
    if (changes.dossier) {
      const x = changes.dossier.info
      sql.run(
        "INSERT OR REPLACE INTO fluid_dossiers (person, text, at, updates) VALUES (?, ?, ?, ?)",
        person,
        x.text,
        x.at,
        x.updates,
      )
      each("UPDATE fluid_windows SET folded = 1 WHERE person = ? AND id IN (?)", person, changes.dossier.windows)
    }
  }

  const erase = async (person: string) => {
    sql.transaction(() => {
      for (const table of [
        "fluid_log",
        "fluid_turns",
        "fluid_windows",
        "fluid_statements",
        "fluid_groups",
        "fluid_dossiers",
        "fluid_vectors",
      ]) {
        sql.run(`DELETE FROM ${table} WHERE person = ?`, person)
      }
      // @ref LLP 0023#forgetting — erasure advances a content-free generation, including for an absent person
      sql.run(
        "INSERT INTO fluid_revisions(person, revision) VALUES (?, ?) ON CONFLICT(person) DO UPDATE SET revision = excluded.revision",
        person,
        crypto.randomUUID(),
      )
    })
    cache.delete(person)
  }

  const revision = (person: string): string | null => {
    const row = sql.run("SELECT revision FROM fluid_revisions WHERE person = ?", person)[0]
    return row ? String(row.revision) : null
  }
  const memory: Store = {
    revision: async (person) => revision(person),
    erase,
    log: {
      session: async (person, session): Promise<Turn.Info[]> =>
        sql
          .run("SELECT turn FROM fluid_log WHERE person = ? AND session = ? ORDER BY at, id", person, session)
          .map((x) => JSON.parse(String(x.turn))),
    },
    turns: {
      seen: async (person, ids) => {
        const found = new Set(
          Sql.slices([...new Set(ids)]).flatMap((part) =>
            sql
              .run(`SELECT id FROM fluid_turns WHERE person = ? AND id IN (${Sql.marks(part.length)})`, person, ...part)
              .map((x) => String(x.id)),
          ),
        )
        return ids.filter((id) => found.has(id))
      },
    },
    windows: {
      list: page("fluid_windows", window),
      get: byId("fluid_windows", window),
      session: async (person, session) => windows("person = ? AND session = ?", person, session),
      all: async (person) => windows("person = ?", person),
      pending: async (person) => windows("person = ? AND folded = 0", person),
    },
    statements: {
      list: page("fluid_statements", statement),
      get: byId("fluid_statements", statement),
      windows: statementsBy("window_id"),
      groups: statementsBy("group_id"),
    },
    groups: {
      get: byId("fluid_groups", group),
      replacing: async (person, ids) =>
        Sql.slices([...new Set(ids)]).flatMap((part) =>
          sql
            .run(
              `SELECT DISTINCT g.* FROM fluid_groups g, json_each(g.replaces) r
               WHERE g.person = ? AND r.value IN (${Sql.marks(part.length)}) ORDER BY g.id`,
              person,
              ...part,
            )
            .map(group),
        ),
    },
    dossiers: {
      get: async (person): Promise<Dossier.Info | undefined> => {
        const [row] = sql.run("SELECT * FROM fluid_dossiers WHERE person = ?", person)
        return row && { person, text: String(row.text), at: String(row.at), updates: Number(row.updates) }
      },
    },
    vectors: {
      query: async (person, vector, filter): Promise<Match[]> =>
        [...vectors(person)]
          .filter(([, x]) => x.kind === filter.kind)
          .map(([id, x]) => ({ id, score: Vector.dot(vector, x.vector) }))
          .toSorted((a, b) => b.score - a.score || a.id.localeCompare(b.id))
          .slice(0, filter.top),
    },
    write: async (person, changes, expected) => {
      Write.check(person, changes)
      sql.transaction(() => {
        if (expected && expected.revision !== revision(person))
          throw new ConflictError("Memory changed; retry with the same IDs.")
        apply(person, changes)
        sql.run(
          "INSERT INTO fluid_revisions(person, revision) VALUES (?, ?) ON CONFLICT(person) DO UPDATE SET revision = excluded.revision",
          person,
          crypto.randomUUID(),
        )
      })
      // Reload on the next query, including writes from another instance.
      cache.delete(person)
    },
  }

  return {
    ...memory,
    count: (person: string): Counts => {
      const one = (query: string) => Number(sql.run(query, person)[0]?.n ?? 0)
      return {
        windows: one("SELECT count(*) AS n FROM fluid_windows WHERE person = ?"),
        pending: one("SELECT count(*) AS n FROM fluid_windows WHERE person = ? AND folded = 0"),
        statements: one("SELECT count(*) AS n FROM fluid_statements WHERE person = ?"),
        groups: one("SELECT count(*) AS n FROM fluid_groups WHERE person = ?"),
      }
    },
    // People with windows their dossier doesn't include yet.
    behind: () =>
      sql
        .run("SELECT DISTINCT person FROM fluid_windows WHERE folded = 0 ORDER BY person")
        .map((x) => String(x.person)),
    // Everything the person said and everything made from it.
  }
}

export type SqlStore = ReturnType<typeof store>

export * as SqlStore from "./store"
