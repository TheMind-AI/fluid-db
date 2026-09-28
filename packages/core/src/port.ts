import type { Dossier, Group, Statement, Turn, Window } from "@fluiddb/schema"
import type { z } from "zod"

// Every provider the memory talks to is a port. Adapters live in @fluiddb/providers (models, embeddings, Jev) and
// @fluiddb/sql (storage); in-memory ones in ./local and ./testing.

export interface Call {
  signal?: AbortSignal
}

// `processors` are everyone the data sent to a provider reaches, relays included (Jev through OpenRouter reaches
// OpenRouter and TypeSafe), so a deployment can refuse what its privacy policy doesn't list (LLP 0018#processors).
export interface Provider {
  readonly id: string
  readonly processors: readonly string[]
}

export interface LanguageModel extends Provider {
  text(prompt: string, call?: Call): Promise<string>
  object<T>(prompt: string, schema: z.ZodType<T>, call?: Call): Promise<T>
}

// Unit-length vectors, one per text.
export interface Embedder extends Provider {
  embed(texts: string[], call?: Call): Promise<Float32Array[]>
}

export interface Choice {
  choice: string
  probabilities: Record<string, number>
}

// Jev's two closed question types over a JSON state (LLP 0008): yes/no with a probability, and a choice among options
// with a probability for each. Backticked names in a question refer to fields of the state.
export interface Decider extends Provider {
  yes(state: Record<string, unknown>, questions: Record<string, string>, call?: Call): Promise<Record<string, number>>
  choose(
    state: Record<string, unknown>,
    question: string,
    options: Record<string, string>,
    call?: Call,
  ): Promise<Choice>
}

export type Kind = "statement" | "window"

export interface PickInput {
  kind: Kind
  moment: string
  items: { id: string; text: string }[]
  top: number
  chars: number
}

/** Ranks only the supplied candidates; returned IDs must be unique and belong to that set. */
export interface Picker extends Provider {
  pick(input: PickInput, call?: Call): Promise<string[]>
}

export interface Entry {
  id: string
  kind: Kind
  vector: Float32Array
}

export interface Match {
  id: string
  score: number
}

// Everything one write changes for one person. A store applies it all or nothing: drops first, then puts.
export interface Changes {
  // Original messages, appended before model calls. Existing ids are immutable.
  log?: { session: string; turns: Turn.Info[] }
  // New windows are pending: not yet in the dossier.
  windows?: Window.Info[]
  statements?: Statement.Info[]
  groups?: Group.Info[]
  vectors?: Entry[]
  // Turns ingested, so they are skipped when sent again.
  turns?: { session: string; ids: string[] }
  // The dossier, and the windows it now includes.
  dossier?: { info: Dossier.Info; windows: string[] }
  drop?: {
    // Windows and statements go with their vectors.
    windows?: string[]
    statements?: string[]
    groups?: string[]
    // Original messages. Processed ids remain as tombstones against delayed retries.
    log?: string[]
    // Remove the session's original messages; retain its processed ids.
    session?: string
    // Every window is pending again.
    dossier?: boolean
  }
}

// Reads are scoped to one person. `get` returns rows in the order of the ids asked for, skipping missing ones; lists
// are in time order (`at`, then `id`).
export interface Store {
  readonly log: {
    session(person: string, session: string): Promise<Turn.Info[]>
  }
  readonly turns: {
    seen(person: string, ids: string[]): Promise<string[]>
  }
  readonly windows: {
    list(person: string, query: PageQuery): Promise<Window.Info[]>
    get(person: string, ids: string[]): Promise<Window.Info[]>
    session(person: string, session: string): Promise<Window.Info[]>
    all(person: string): Promise<Window.Info[]>
    pending(person: string): Promise<Window.Info[]>
  }
  readonly statements: {
    list(person: string, query: PageQuery): Promise<Statement.Info[]>
    get(person: string, ids: string[]): Promise<Statement.Info[]>
    windows(person: string, ids: string[]): Promise<Statement.Info[]>
    groups(person: string, ids: string[]): Promise<Statement.Info[]>
  }
  readonly groups: {
    get(person: string, ids: string[]): Promise<Group.Info[]>
    // Groups whose `replaces` names any of `ids`.
    replacing(person: string, ids: string[]): Promise<Group.Info[]>
  }
  readonly dossiers: {
    get(person: string): Promise<Dossier.Info | undefined>
  }
  // Nearest by cosine among the person's vectors of one kind.
  readonly vectors: {
    query(person: string, vector: Float32Array, filter: { kind: Kind; top: number }): Promise<Match[]>
  }
  // Optimistic concurrency token. A stale conditional write must fail without changing any data.
  revision(person: string): Promise<string | null>
  write(person: string, changes: Changes, expected?: { revision: string | null }): Promise<void>
  // Remove source/derived content, vectors and processing tombstones, retaining a fresh content-free revision.
  // Even an absent person must get a new revision so an in-flight first save cannot commit after erasure.
  erase(person: string): Promise<void>
}

// Keyset pagination. Stores return at most `limit` rows after the exclusive (at, id) tuple.
export interface PageQuery {
  limit: number
  session?: string
  after?: { at: string; id: string }
}

export interface Clock {
  now(): Date
}

export interface Ids {
  next(prefix: string): string
}

// Events carry counts and timings, never what people said.
export interface Logger {
  event(name: string, data: Record<string, number | string | boolean>): void
}
