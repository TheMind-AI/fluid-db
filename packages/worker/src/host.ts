import {
  ConflictError,
  InputError,
  Memory,
  OutputError,
  ProcessorError,
  ProviderError,
  Render,
  type Logger,
  type Store,
} from "@fluiddb/core"
import { Api, Id, Turn, type Dossier } from "@fluiddb/schema"
import { Migrate, Sql, SqlStore } from "@fluiddb/sql"
import { z } from "zod"
import { Lock } from "./lock"

// One person's memory where it lives: a Durable Object in production, anything with a synchronous SQLite in tests.
// Turns wait in a durable inbox and are ingested in the background; writes run one at a time.

export interface Alarm {
  get(): Promise<number | null>
  set(at: number): Promise<void>
}

export const defaults = {
  // Seconds of quiet after turns arrive before they are ingested, so a session's messages make whole windows...
  delay: 300,
  // ...but at most this long after the oldest waiting turn.
  wait: 3_600,
  // Attempts per session before its turns are set aside for `retry`.
  attempts: 5,
}
export type Settings = typeof defaults

export interface Deps {
  sql: Sql
  alarm: Alarm
  memory: (store: Store) => Memory.Service
  logger?: Logger
  now?: () => number
  settings?: Partial<Settings>
}

const migrations = [
  [
    `CREATE TABLE fluid_inbox (
      person TEXT NOT NULL, id TEXT NOT NULL, session TEXT NOT NULL, turn TEXT NOT NULL, received INTEGER NOT NULL,
      attempts INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0, error TEXT,
      PRIMARY KEY (person, id))`,
    "CREATE INDEX fluid_inbox_session ON fluid_inbox (person, session)",
  ],
]

const Turns = z.array(Turn.Info).min(1).max(500)

// A request the caller got wrong, in a way zod didn't see (a body that isn't JSON).
export class RequestError extends Error {
  override readonly name = "InvalidRequest"
}

export interface Failure {
  name: string
  message: string
  status: number
}

// What a caller may learn of an error: its kind and a message, never what was sent.
export function failure(error: unknown): Failure {
  if (error instanceof z.ZodError) return { name: "InvalidRequest", message: z.prettifyError(error), status: 400 }
  if (error instanceof ConflictError) return { name: error.name, message: error.message, status: 409 }
  if (error instanceof InputError) return { name: error.name, message: error.message, status: 400 }
  if (error instanceof RequestError) return { name: error.name, message: error.message, status: 400 }
  if (error instanceof ProcessorError) return { name: error.name, message: error.message, status: 500 }
  if (error instanceof ProviderError)
    return { name: error.name, message: "a model provider request failed", status: 502 }
  if (error instanceof OutputError)
    return { name: error.name, message: "a model provider returned an invalid answer", status: 502 }
  return { name: "InternalError", message: "internal error", status: 500 }
}

const named = (error: unknown) => (error instanceof Error ? error.name : "Error")

export function create(deps: Deps) {
  const settings = { ...defaults, ...deps.settings }
  const now = deps.now ?? Date.now
  const logger = deps.logger ?? { event: () => {} }
  const { sql, alarm } = deps
  Migrate.run(sql, "inbox", migrations)
  const store = SqlStore.store(sql)
  const memory = deps.memory(store)
  const serial = Lock.serial()
  const revisions = new Map<string, number>()

  const inbox = (person: string) => {
    const rows = sql.run("SELECT failed, count(*) AS n FROM fluid_inbox WHERE person = ? GROUP BY failed", person)
    const count = (failed: number) => Number(rows.find((x) => Number(x.failed) === failed)?.n ?? 0)
    return { pending: count(0), failed: count(1) }
  }

  const failures = {
    get: () => Number(sql.run("SELECT value FROM fluid_meta WHERE key = 'host.failures'")[0]?.value ?? 0),
    set: (n: number) =>
      sql.run(
        "INSERT INTO fluid_meta (key, value) VALUES ('host.failures', ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        String(n),
      ),
  }

  // Turns into the inbox; the alarm is pushed back until the conversation has been quiet for `delay`.
  const accept = (person: string, session: string, input: Turn.Info[]): Promise<Api.Accepted> =>
    serial(async () => {
      Id.Info.parse(person)
      Id.Info.parse(session)
      const turns = Turns.parse(input)
      const seen = new Set(
        await store.turns.seen(
          person,
          turns.map((x) => x.id),
        ),
      )
      const received = now()
      const before = inbox(person).pending
      sql.transaction(() => {
        for (const turn of turns.filter((x) => !seen.has(x.id))) {
          sql.run(
            "INSERT OR IGNORE INTO fluid_inbox (person, id, session, turn, received) VALUES (?, ?, ?, ?, ?)",
            person,
            turn.id,
            session,
            JSON.stringify(turn),
            received,
          )
        }
      })
      const pending = inbox(person).pending
      const oldest = Number(sql.run("SELECT min(received) AS t FROM fluid_inbox WHERE failed = 0")[0]?.t ?? received)
      if (pending) await alarm.set(Math.min(received + settings.delay * 1000, oldest + settings.wait * 1000))
      return { accepted: pending - before, pending }
    })

  // Every waiting session, oldest first, then the dossiers that are behind. Failures are counted per session; after
  // `attempts` its turns are set aside. What is left is retried later, backing off.
  const process = () =>
    serial(async () => {
      const total = { turns: 0, windows: 0, statements: 0, linked: 0 }
      const failed = { count: 0 }
      const errors = new Map<string, unknown>()
      const results = new Map<string, Api.Ingested>()
      const sessions = sql.run(
        `SELECT person, session, min(received) AS first FROM fluid_inbox WHERE failed = 0
         GROUP BY person, session ORDER BY first, person, session`,
      )
      for (const x of sessions) {
        const person = String(x.person)
        const session = String(x.session)
        const rows = sql.run(
          "SELECT id, turn FROM fluid_inbox WHERE person = ? AND session = ? AND failed = 0",
          person,
          session,
        )
        try {
          const out = await memory.ingest({ person, session, turns: rows.map((r) => JSON.parse(String(r.turn))) })
          results.set(`${person}\u0000${session}`, out)
          for (const key of Object.keys(total) as (keyof typeof total)[]) total[key] += out[key]
          for (const part of Sql.slices(rows.map((r) => String(r.id)))) {
            sql.run(`DELETE FROM fluid_inbox WHERE person = ? AND id IN (${Sql.marks(part.length)})`, person, ...part)
          }
        } catch (error) {
          failed.count++
          errors.set(`${person}\u0000${session}`, error)
          logger.event("ingest.failed", { error: named(error) })
          for (const part of Sql.slices(rows.map((r) => String(r.id)))) {
            sql.run(
              `UPDATE fluid_inbox SET attempts = attempts + 1, error = ?, failed = (attempts + 1 >= ?)
               WHERE person = ? AND id IN (${Sql.marks(part.length)})`,
              named(error),
              settings.attempts,
              person,
              ...part,
            )
          }
        }
      }
      for (const person of store.behind()) {
        await memory.fold(person).catch((error: unknown) => {
          failed.count++
          logger.event("fold.failed", { error: named(error) })
        })
      }
      const streak = failed.count ? failures.get() + 1 : 0
      failures.set(streak)
      const waiting = Number(sql.run("SELECT count(*) AS n FROM fluid_inbox WHERE failed = 0")[0]?.n ?? 0)
      if (waiting || store.behind().length) {
        await alarm.set(now() + (streak ? Math.min(60_000 * 2 ** (streak - 1), 3_600_000) : settings.delay * 1000))
      }
      return { total, errors, results }
    })

  return {
    accept,
    // Takes the turns and ingests now. If this session fails, its turns stay in the inbox and the error is thrown.
    ingest: async (person: string, session: string, turns: Turn.Info[]): Promise<Api.Ingested> => {
      await accept(person, session, turns)
      const out = await process()
      const key = `${person}\u0000${session}`
      if (out.errors.has(key)) throw out.errors.get(key)
      return out.results.get(key) ?? { turns: 0, windows: 0, statements: 0, linked: 0 }
    },
    recall: async (person: string, input: Api.Ask): Promise<Api.Answer> => {
      const ask = Api.Ask.parse(input)
      const revision = revisions.get(person) ?? 0
      const result = await memory.recall({ person, conversation: ask.conversation })
      const recall = revision === (revisions.get(person) ?? 0) ? result : { person, statements: [], windows: [] }
      return { recall, ...(ask.render ? { prompt: Render.render(recall, { name: ask.name }) } : {}) }
    },
    dossier: async (person: string): Promise<Dossier.Info | null> =>
      (await memory.dossier(Id.Info.parse(person))) ?? null,
    remember: (person: string, input: Api.Remember): Promise<Api.Remembered> =>
      serial(async () => {
        const result = await memory.remember({ ...Api.Remember.parse(input), person })
        if (!result.duplicate) {
          revisions.set(person, (revisions.get(person) ?? 0) + 1)
          await alarm.set(now() + settings.delay * 1000)
        }
        return result
      }),
    inspect: (person: string, input: Api.Inspect) => memory.inspect(person, input),
    evidence: (person: string, input: Api.Evidence) => memory.evidence(person, input),
    // Turns of the session still waiting go too; the dossier is rewritten soon after.
    source: (person: string, input: Api.Source) => memory.source(person, input),
    previewForget: (person: string, input: Api.Forget) => memory.previewForget({ person, ...input }),
    forget: (person: string, input: Api.Forget): Promise<Api.Forgotten> =>
      serial(async () => {
        const request = Api.Forget.parse(input)
        const out = await memory.forget({ person, ...request })
        if (request.session) {
          // A delayed delivery of a forgotten, not-yet-ingested turn must not restore its text.
          sql.run(
            `INSERT OR IGNORE INTO fluid_turns (person, id, session)
            SELECT person, id, session FROM fluid_inbox WHERE person = ? AND session = ?`,
            person,
            request.session,
          )
          sql.run("DELETE FROM fluid_inbox WHERE person = ? AND session = ?", person, request.session)
        }
        revisions.set(person, (revisions.get(person) ?? 0) + 1)
        if (out.windows || out.statements) await alarm.set(now())
        return out
      }),
    erase: (person: string): Promise<null> =>
      serial(async () => {
        Id.Info.parse(person)
        await store.erase(person)
        sql.run("DELETE FROM fluid_inbox WHERE person = ?", person)
        revisions.set(person, (revisions.get(person) ?? 0) + 1)
        return null
      }),
    status: async (person: string): Promise<Api.Status> => {
      const counts = store.count(Id.Info.parse(person))
      const dossier = await store.dossiers.get(person)
      return {
        inbox: inbox(person),
        windows: { total: counts.windows, pending: counts.pending },
        statements: counts.statements,
        groups: counts.groups,
        ...(dossier ? { dossier: { updates: dossier.updates, at: dossier.at } } : {}),
      }
    },
    // Turns set aside after failing are tried again.
    retry: async (person: string): Promise<Api.Accepted> => {
      const before = inbox(Id.Info.parse(person)).pending
      sql.run("UPDATE fluid_inbox SET failed = 0, attempts = 0 WHERE person = ? AND failed = 1", person)
      const pending = inbox(person).pending
      if (pending) await alarm.set(now())
      return { accepted: pending - before, pending }
    },
    process,
    // Expected provider failures are handled by `process`. Unexpected failures still need another alarm;
    // if scheduling fails too, throw so Cloudflare's own alarm retry can recover.
    alarm: async () => {
      await process().catch(async (error: unknown) => {
        logger.event("alarm.failed", { error: named(error) })
        await alarm.set(now() + 60_000)
      })
    },
  }
}

export type Host = ReturnType<typeof create>

export type Result<T> = { ok: true; value: T } | { ok: false; error: Failure }

async function settle<T>(fn: () => Promise<T>): Promise<Result<T>> {
  try {
    return { ok: true, value: await fn() }
  } catch (error) {
    return { ok: false, error: failure(error) }
  }
}

// What the HTTP API calls. Every answer is a Result, so errors cross Durable Object RPC as data.
export interface Service {
  accept(person: string, session: string, turns: Turn.Info[]): Promise<Result<Api.Accepted>>
  ingest(person: string, session: string, turns: Turn.Info[]): Promise<Result<Api.Ingested>>
  recall(person: string, ask: Api.Ask): Promise<Result<Api.Answer>>
  dossier(person: string): Promise<Result<Dossier.Info | null>>
  remember(person: string, request: Api.Remember): Promise<Result<Api.Remembered>>
  inspect(person: string, request: Api.Inspect): Promise<Result<Api.Page>>
  evidence(person: string, request: Api.Evidence): Promise<Result<Api.Sources>>
  source(person: string, request: Api.Source): Promise<Result<Api.SourcePage>>
  previewForget(person: string, request: Api.Forget): Promise<Result<Api.ForgetPreview>>
  forget(person: string, request: Api.Forget): Promise<Result<Api.Forgotten>>
  erase(person: string): Promise<Result<null>>
  status(person: string): Promise<Result<Api.Status>>
  retry(person: string): Promise<Result<Api.Accepted>>
}

export function serve(host: Host): Service {
  return {
    accept: (person, session, turns) => settle(() => host.accept(person, session, turns)),
    ingest: (person, session, turns) => settle(() => host.ingest(person, session, turns)),
    recall: (person, ask) => settle(() => host.recall(person, ask)),
    dossier: (person) => settle(() => host.dossier(person)),
    remember: (person, request) => settle(() => host.remember(person, request)),
    inspect: (person, request) => settle(() => host.inspect(person, request)),
    evidence: (person, request) => settle(() => host.evidence(person, request)),
    source: (person, request) => settle(() => host.source(person, request)),
    previewForget: (person, request) => settle(() => host.previewForget(person, request)),
    forget: (person, request) => settle(() => host.forget(person, request)),
    erase: (person) => settle(() => host.erase(person)),
    status: (person) => settle(() => host.status(person)),
    retry: (person) => settle(() => host.retry(person)),
  }
}

export * as Host from "./host"
