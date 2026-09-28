import { Api, Id, Recall, Turn, type Dossier, type Group, type Statement, type Window } from "@fluiddb/schema"
import { z } from "zod"
import { Detect } from "./detect"
import { Dossiers } from "./dossier"
import { Extract } from "./extract"
import { Limit } from "./limit"
import { Link } from "./link"
import { Pick } from "./pick"
import { Policy } from "./policy"
import type { Call, Clock, Decider, Embedder, Ids, LanguageModel, Logger, Match, Store, Picker, Kind } from "./port"
import { OutputError } from "./error"
import { Split } from "./split"
import { Vector } from "./vector"
import { Inspect } from "./inspect"
import { Save } from "./save"
import { Forget } from "./forget"

// The settings the lab measured (LLP 0019.000 to LLP 0022.000).
export const defaults = {
  // The product's name in transcripts ("Mind asked: ..."), and what it is to the person.
  assistant: "the assistant",
  role: "therapy assistant",
  // The person's messages per window.
  size: 6,
  // A new statement is compared with its closest `candidates` earlier ones at cosine `score` or more.
  link: { score: 0.55, candidates: 5 },
  // Meaning search's candidates, how many the picker keeps, and how much of each it reads.
  statements: { search: 40, keep: 15 },
  windows: { search: 20, keep: 6, chars: 3_000 },
  // Who picks: Jev's closed questions ("decider": 71.8% end to end, 0.33 s a decision) or the model ranking the
  // list ("model": 73.0%, seconds) (LLP 0021.000).
  pick: "decider" as "decider" | "model",
  // The re-telling detector counts a pick at this probability or more (LLP 0022.000).
  threshold: 0.5,
  detect: true,
  // Model calls in flight during ingest, and windows per dossier update.
  concurrency: 4,
  batch: 10,
}
export type Options = typeof defaults

export interface Deps {
  model: LanguageModel
  /** Per-stage overrides; omitted stages use model. */
  models?: Partial<Record<"extract" | "link" | "dossier" | "pick", LanguageModel>>
  embedder: Embedder
  decider: Decider
  /** Overrides options.pick; only ranks candidates already retrieved for this person. */
  picker?: Picker
  /** Retelling detection can use a different decider from the picker. */
  detector?: Decider
  store: Store
  clock?: Clock
  ids?: Ids
  logger?: Logger
  // The processors a person's data may go to; a provider reaching any other is refused (LLP 0018#processors).
  processors?: readonly string[]
  options?: {
    [K in keyof Options]?: Options[K] extends object ? Partial<Options[K]> : Options[K]
  }
}

export interface Service {
  remember(input: { person: string } & Api.Remember, call?: Call): Promise<Api.Remembered>
  inspect(person: string, query?: Api.Inspect, call?: Call): Promise<Api.Page>
  evidence(person: string, query: Api.Evidence, call?: Call): Promise<Api.Sources>
  // Adds a session's turns: windows, and statements linked to what was said before. Turns already ingested are
  // skipped. Originals persist before model calls; derived rows commit together. `fold` updates the dossier.
  ingest(input: { person: string; session: string; turns: Turn.Info[] }, call?: Call): Promise<Api.Ingested>
  // Brings the dossier up to date with the windows it doesn't include yet, saving after each batch.
  fold(person: string, call?: Call): Promise<Dossier.Info | undefined>
  // What to remember at this turn of a conversation.
  recall(input: { person: string; conversation: Turn.Info[] }, call?: Call): Promise<Recall.Info>
  // Removes a session, statements or windows and the links to them, and drops the dossier for `fold` to rewrite from
  // what remains.
  previewForget(input: { person: string } & Api.Forget, call?: Call): Promise<Api.ForgetPreview>
  source(person: string, query: Api.Source, call?: Call): Promise<Api.SourcePage>
  forget(input: { person: string } & Api.Forget, call?: Call): Promise<Api.Forgotten>
  dossier(person: string): Promise<Dossier.Info | undefined>
  erase(person: string): Promise<void>
}

// Ids that sort in the order they were made, so rows made at the same moment keep their order.
export function ids(): Ids {
  const state = { last: 0, seq: 0 }
  return {
    next: (prefix) => {
      const now = Date.now()
      state.seq = now === state.last ? state.seq + 1 : 0
      state.last = now
      const random = [...crypto.getRandomValues(new Uint8Array(6))].map((x) => x.toString(16).padStart(2, "0"))
      return `${prefix}_${now.toString(36).padStart(9, "0")}${state.seq.toString(36).padStart(4, "0")}${random.join("")}`
    },
  }
}

const Turns = z.array(Turn.Info)

type Candidate = { text: string; score: number } & ({ row: Statement.Info } | { batch: number })

export function create(deps: Deps): Service {
  Policy.check(deps.processors, [
    deps.model,
    deps.embedder,
    deps.decider,
    ...Object.values(deps.models ?? {}),
    ...(deps.picker ? [deps.picker] : []),
    ...(deps.detector ? [deps.detector] : []),
  ])
  const opts: Options = {
    ...defaults,
    ...deps.options,
    link: { ...defaults.link, ...deps.options?.link },
    statements: { ...defaults.statements, ...deps.options?.statements },
    windows: { ...defaults.windows, ...deps.options?.windows },
  }
  z.object({
    size: z.int().min(1).max(100),
    batch: z.int().min(1).max(100),
    concurrency: z.int().min(1).max(32),
    threshold: z.number().min(0).max(1),
    detect: z.boolean(),
    pick: z.enum(["decider", "model"]),
    link: z.object({ score: z.number().min(-1).max(1), candidates: z.int().min(1).max(100) }),
    statements: z.object({ search: z.int().min(1).max(200), keep: z.int().min(0).max(200) }),
    windows: z.object({ search: z.int().min(1).max(200), keep: z.int().min(0).max(200), chars: z.int().min(1) }),
  }).parse(opts)
  const clock = deps.clock ?? { now: () => new Date() }
  const next = deps.ids ?? ids()
  const logger = deps.logger ?? { event: () => {} }
  const { model, embedder, decider, store } = deps
  const models = {
    extract: deps.models?.extract ?? model,
    link: deps.models?.link ?? model,
    dossier: deps.models?.dossier ?? model,
    pick: deps.models?.pick ?? model,
  }
  const detector = deps.detector ?? decider
  const picker =
    deps.picker ?? (opts.pick === "model" ? Pick.usingModel(models.pick, opts.role) : Pick.usingDecider(decider))
  const voice = { assistant: opts.assistant, role: opts.role }
  const writes = new Map<string, Promise<unknown>>()
  const serial = <T>(person: string, fn: () => Promise<T>): Promise<T> => {
    const run = (writes.get(person) ?? Promise.resolve()).then(fn, fn)
    writes.set(person, run)
    const clear = () => {
      if (writes.get(person) === run) writes.delete(person)
    }
    void run.then(clear, clear)
    return run
  }

  // @ref LLP 0020#part-b-creation — each statement against its closest earlier ones, stored or earlier in the batch
  // (a vector index may not return what was just written). The decisions are independent, so they run in parallel.
  const candidates = async (person: string, items: { text: string; vector: Float32Array }[]) => {
    const found = await Limit.map(items, opts.concurrency, (x) =>
      store.vectors.query(person, x.vector, { kind: "statement", top: opts.link.candidates }),
    )
    const rows = await store.statements.get(person, [...new Set(found.flat().map((x) => x.id))])
    return items.map((item, i): Candidate[] => {
      const stored = (found[i] ?? []).flatMap((m: Match) => {
        const row = rows.find((x) => x.id === m.id)
        return row ? [{ text: row.text, score: m.score, row }] : []
      })
      const earlier = items
        .slice(0, i)
        .map((x, batch) => ({ text: x.text, score: Vector.dot(item.vector, x.vector), batch }))
      return [...stored, ...earlier]
        .filter((x) => x.score >= opts.link.score)
        .toSorted((a, b) => b.score - a.score)
        .slice(0, opts.link.candidates)
    })
  }

  const ingest: Service["ingest"] = async (input, call) => {
    call?.signal?.throwIfAborted()
    const started = Date.now()
    const person = Id.Info.parse(input.person)
    const session = Id.Info.parse(input.session)
    const received = Turns.parse(input.turns)
    // @ref LLP 0023#raw-log — preserve the complete original before any lossy processing or paid request
    await store.write(person, { log: { session, turns: received } })
    const revision = await store.revision(person)
    const turns = await store.log.session(person, session)
    const said = turns.filter((x) => x.role === "person").map((x) => x.id)
    const skip = new Set(said.length ? await store.turns.seen(person, said) : [])
    const drafts = Split.windows({ turns, skip, size: opts.size, assistant: opts.assistant })
    if (!drafts.length) return { turns: 0, windows: 0, statements: 0, linked: 0 }
    const windows: Window.Info[] = drafts.map((x) => ({
      id: next.next("win"),
      person,
      session,
      at: x.at,
      text: x.text,
      turns: x.turns,
      sources: x.sources,
    }))
    const found = await Limit.map(windows, opts.concurrency, (x) =>
      Extract.statements(models.extract, { ...voice, date: x.at.slice(0, 10), text: x.text }, call),
    )
    const items = windows.flatMap((window, i) => (found[i] ?? []).map((draft) => ({ window, draft })))
    const [wv, sv] = await Promise.all([
      embedder.embed(
        windows.map((x) => x.text),
        call,
      ),
      items.length
        ? embedder.embed(
            items.map((x) => x.draft.text),
            call,
          )
        : Promise.resolve([]),
    ])
    const near = await candidates(
      person,
      items.map((x, i) => ({ text: x.draft.text, vector: sv[i]! })),
    )
    const stored = near.flat().flatMap((x) => ("row" in x ? [x.row.group] : []))
    const groups = new Map((await store.groups.get(person, [...new Set(stored)])).map((g) => [g.id, g]))
    const pinned = new Set(
      (await store.statements.groups(person, [...groups.keys()])).filter((x) => x.pinned).map((x) => x.group),
    )
    const changed = new Set<string>()
    const statements: Statement.Info[] = []
    const candidateGroup = (candidate: Candidate) => {
      const id = "row" in candidate ? candidate.row.group : statements[candidate.batch]?.group
      return id ? groups.get(id) : undefined
    }
    // @ref LLP 0023#temporal-links — don't hide a returning fact in an ended group; offer current candidates instead
    const eligible = (matches: Candidate[], at: string) =>
      matches.filter((candidate) => {
        const group = candidateGroup(candidate)
        return !group?.until || at < group.until
      })
    const matches = near.map((candidates, i) => eligible(candidates, items[i]!.window.at))
    const verdicts = await Limit.map(items, opts.concurrency, (x, i) =>
      Link.decide(
        models.link,
        x.draft.text,
        matches[i]!.map((c) => c.text),
        call,
      ),
    )
    const count = { linked: 0 }
    // Apply in order. An earlier change in this batch can invalidate a later decision's candidates.
    for (const [i, item] of items.entries()) {
      const current = eligible(matches[i]!, item.window.at)
      const verdict =
        current.length === matches[i]!.length
          ? verdicts[i]!
          : await Link.decide(
              models.link,
              item.draft.text,
              current.map((c) => c.text),
              call,
            )
      const target = verdict.relation === "new" ? undefined : current[verdict.index]
      const group = target ? candidateGroup(target) : undefined
      const decision: Link.Decision =
        verdict.relation !== "new" && group && !(verdict.relation === "change" && pinned.has(group.id))
          ? { relation: verdict.relation, group }
          : { relation: "new" }
      const draft = {
        id: next.next("stm"),
        person,
        session,
        window: item.window.id,
        text: item.draft.text,
        kind: item.draft.kind,
        at: item.window.at,
      }
      const out = Link.apply(decision, draft, next)
      if (decision.relation !== "new") count.linked++
      for (const g of out.groups) {
        groups.set(g.id, g)
        changed.add(g.id)
      }
      if (decision.relation === "change" && group?.until && draft.at >= group.last) {
        // Inserting between two historical periods also moves the later period's predecessor edge.
        const replacing = await store.groups.replacing(person, [group.id])
        const successors = new Map([...replacing, ...groups.values()].map((g) => [g.id, g]))
        for (const successor of successors.values()) {
          if (successor.id === out.statement.group || !successor.replaces.includes(group.id)) continue
          groups.set(successor.id, {
            ...successor,
            replaces: successor.replaces.map((id) => (id === group.id ? out.statement.group : id)),
          })
          changed.add(successor.id)
        }
      }
      statements.push(out.statement)
    }

    call?.signal?.throwIfAborted()
    await store.write(
      person,
      {
        windows,
        statements,
        groups: [...changed].map((id) => groups.get(id)!),
        vectors: [
          ...windows.map((x, i) => ({ id: x.id, kind: "window" as const, vector: wv[i]! })),
          ...statements.map((x, i) => ({ id: x.id, kind: "statement" as const, vector: sv[i]! })),
        ],
        turns: { session, ids: turns.map((x) => x.id) },
      },
      { revision },
    )
    const result = {
      turns: new Set(drafts.flatMap((x) => x.turns)).size,
      windows: windows.length,
      statements: statements.length,
      linked: count.linked,
    }
    logger.event("ingest", { ...result, ms: Date.now() - started })
    return result
  }

  const fold: Service["fold"] = async (person, call) => {
    Id.Info.parse(person)
    const started = Date.now()
    let folded = 0
    while (true) {
      call?.signal?.throwIfAborted()
      const revision = await store.revision(person)
      const pending = await store.windows.pending(person)
      const current = await store.dossiers.get(person)
      if (!pending.length) {
        if (folded) logger.event("fold", { windows: folded, ms: Date.now() - started })
        return current
      }
      const batch = pending.slice(0, opts.batch)
      const text = await Dossiers.update(models.dossier, { ...voice, current: current?.text, windows: batch }, call)
      const info = { person, text, at: clock.now().toISOString(), updates: (current?.updates ?? 0) + 1 }
      call?.signal?.throwIfAborted()
      await store.write(person, { dossier: { info, windows: batch.map((x) => x.id) } }, { revision })
      folded += batch.length
    }
  }

  const day = (at: string) => at.slice(0, 10)

  // Each statement with how often and when the same fact was said, and what it replaced (LLP 0021#part-b-keeping-every-wording).
  const evidence = async (person: string, rows: Statement.Info[]): Promise<Recall.Evidence[]> => {
    const groups = await store.groups.get(person, [...new Set(rows.map((x) => x.group))])
    const replaced = [...new Set(groups.flatMap((x) => x.replaces))]
    const [older, ended] = replaced.length
      ? await Promise.all([store.statements.groups(person, replaced), store.groups.get(person, replaced)])
      : [[], []]
    return rows.map((row) => {
      const group = groups.find((x) => x.id === row.group)
      const said = row.save
        ? `${row.save.origin === "import" ? "imported" : row.save.origin === "agent" ? "agent note saved" : "explicitly saved"} ${day(row.at)}`
        : group && group.count > 1
          ? `the same fact recorded ${group.count} times, first ${day(group.first)}, last ${day(group.last)}`
          : `said ${day(row.at)}`
      const was = (group?.replaces ?? []).flatMap((id) => {
        const text = older.filter((x) => x.group === id).at(-1)?.text
        const until = ended.find((x) => x.id === id)?.until
        return text ? [until ? `${text} (until ${day(until)})` : text] : []
      })
      const extra = [
        was.length ? `earlier: ${was.join("; ")}` : "",
        group?.until ? `changed on ${day(group.until)}` : "",
      ].filter(Boolean)
      return {
        kind: "statement",
        text: `${row.text} (${row.kind}; ${[said, ...extra].join("; ")})`,
        at: row.at,
        ids: [row.id],
      }
    })
  }

  // @ref LLP 0024.001#component-boundary — custom pickers cannot introduce IDs outside this person's candidates
  const pick = async (kind: Kind, moment: string, items: Pick.Item[], top: number, call?: Call) => {
    if (!top || !items.length) return []
    const allowed = new Set(items.map((item) => item.id))
    const ids = await picker.pick(
      { kind, moment, items: items.map(({ id, text }) => ({ id, text })), top, chars: opts.windows.chars },
      call,
    )
    if (
      !Array.isArray(ids) ||
      ids.length > top ||
      new Set(ids).size !== ids.length ||
      ids.some((id) => !allowed.has(id))
    )
      throw new OutputError(picker.id, "picker IDs must be unique, within the candidate set and no more than top")
    return ids
  }

  const recall: Service["recall"] = async (input, call) => {
    call?.signal?.throwIfAborted()
    const started = Date.now()
    const person = Id.Info.parse(input.person)
    const moment = Split.moment(Turns.parse(input.conversation))
    const dossier = await store.dossiers.get(person)
    if (!moment) return Recall.Info.parse({ person, dossier: dossier?.text, statements: [], windows: [] })
    const [vector] = await embedder.embed([moment], call)
    const [sm, wm] = await Promise.all([
      store.vectors.query(person, vector!, { kind: "statement", top: opts.statements.search }),
      store.vectors.query(person, vector!, { kind: "window", top: opts.windows.search }),
    ])
    const [statements, windows] = await Promise.all([
      store.statements.get(
        person,
        sm.map((x) => x.id),
      ),
      store.windows.get(
        person,
        wm.map((x) => x.id),
      ),
    ])
    const [kept, shown, found] = await Promise.all([
      pick("statement", moment, statements, opts.statements.keep, call),
      pick("window", moment, windows, opts.windows.keep, call),
      opts.detect
        ? Detect.detect(
            detector,
            { message: moment, statements: statements.filter((row) => !row.save), threshold: opts.threshold },
            call,
          )
        : undefined,
    ])
    const recorded = found ? (await store.groups.get(person, [found.statement.group]))[0] : undefined
    // Imported/agent saves can share a group with later conversational facts. Retelling dates/counts
    // describe actual conversational disclosures, not every record in that group.
    const spoken = recorded
      ? (await store.statements.groups(person, [recorded.id]))
          .filter((row) => !row.save)
          .map((row) => row.at)
          .toSorted()
      : []
    const group =
      recorded && spoken.length
        ? { ...recorded, count: spoken.length, first: spoken[0]!, last: spoken.at(-1)! }
        : undefined
    const out = Recall.Info.parse({
      person,
      dossier: dossier?.text,
      statements: await evidence(
        person,
        kept.flatMap((id) => statements.filter((x) => x.id === id)),
      ),
      // In time order, as the lab showed them.
      windows: windows
        .filter((x) => shown.includes(x.id))
        .toSorted((a, b) => a.at.localeCompare(b.at) || a.id.localeCompare(b.id))
        .map((x) => ({ kind: "window", text: x.text, at: x.at, ids: [x.id] })),
      retold: found && group ? { statement: found.statement, group, probability: found.probability } : undefined,
    })
    logger.event("recall", {
      statements: out.statements.length,
      windows: out.windows.length,
      retold: Boolean(out.retold),
      ms: Date.now() - started,
    })
    return out
  }

  // @ref LLP 0023#forgetting — remove the source window with a statement, or the next fold would resurrect it
  const forget: Service["forget"] = async (input, call) => {
    const { person, ...selectors } = input
    const request = Api.Forget.parse(selectors)
    const plan = await Forget.preview(store, person, request, call)
    const { revision, windows: removedWindows, statements: rows } = plan
    const windows = removedWindows.map((window) => window.id)
    const removed = new Set(rows.map((x) => x.id))
    const touched = [...new Set(rows.map((x) => x.group))]
    const [members, groups] = touched.length
      ? await Promise.all([store.statements.groups(person, touched), store.groups.get(person, touched)])
      : [[], []]
    const left = members.filter((x) => !removed.has(x.id))
    const gone = new Set(groups.filter((g) => !left.some((x) => x.group === g.id)).map((x) => x.id))

    const updates = new Map<string, Group.Info>()
    const current = (g: Group.Info) => updates.get(g.id) ?? g
    for (const g of groups.filter((x) => !gone.has(x.id))) {
      const at = left
        .filter((x) => x.group === g.id)
        .map((x) => x.at)
        .toSorted()
      updates.set(g.id, { ...current(g), count: at.length, first: at[0]!, last: at.at(-1)! })
    }
    // A group that replaced one that is gone no longer does; a group only a gone one replaced no longer ended.
    const [replacing, ended] = gone.size
      ? await Promise.all([
          store.groups.replacing(person, [...gone]),
          store.groups.get(person, [...new Set(groups.filter((g) => gone.has(g.id)).flatMap((g) => g.replaces))]),
        ])
      : [[], []]
    for (const g of replacing.filter((x) => !gone.has(x.id))) {
      updates.set(g.id, { ...current(g), replaces: current(g).replaces.filter((id) => !gone.has(id)) })
    }
    const still = ended.length
      ? await store.groups.replacing(
          person,
          ended.map((x) => x.id),
        )
      : []
    for (const g of ended.filter((x) => !gone.has(x.id))) {
      if (still.some((x) => !gone.has(x.id) && x.replaces.includes(g.id))) continue
      const { until: _, ...rest } = current(g)
      updates.set(g.id, rest)
    }

    // @ref LLP 0023#temporal-links — erasing a later repetition also erases the chronology it supported
    const dated = groups.filter((g) => !gone.has(g.id) && (g.first !== current(g).first || g.last !== current(g).last))
    if (dated.length) {
      const predecessors = await store.groups.get(person, [...new Set(dated.flatMap((g) => current(g).replaces))])
      const affected = new Map([...dated, ...predecessors].filter((g) => !gone.has(g.id)).map((g) => [g.id, g]))
      for (const g of dated) {
        for (const id of current(g).replaces) {
          const prior = affected.get(id)
          if (!prior || current(g).last >= current(prior).last) continue
          updates.set(g.id, { ...current(g), replaces: current(g).replaces.filter((value) => value !== id) })
          updates.set(prior.id, { ...current(prior), replaces: [...new Set([...current(prior).replaces, g.id])] })
        }
      }
      const persisted = await store.groups.replacing(person, [...affected.keys()])
      const successors = [...new Map([...persisted, ...updates.values()].map((g) => [g.id, g])).values()].filter(
        (g) => !gone.has(g.id),
      )
      const observations = (
        await store.statements.groups(
          person,
          successors.map((g) => g.id),
        )
      ).filter((row) => !removed.has(row.id))
      for (const original of affected.values()) {
        const group = current(original)
        const ids = new Set(successors.filter((g) => g.replaces.includes(group.id)).map((g) => g.id))
        const until = observations
          .filter((row) => ids.has(row.group) && row.at >= group.last)
          .map((row) => row.at)
          .toSorted()[0]
        const { until: _, ...rest } = group
        updates.set(group.id, { ...rest, ...(until ? { until } : {}) })
      }
    }

    const dropped = windows.length > 0 || rows.length > 0
    const originals = request.session ? await store.log.session(person, request.session) : []
    call?.signal?.throwIfAborted()
    await store.write(
      person,
      {
        groups: [...updates.values()],
        ...(request.session ? { turns: { session: request.session, ids: originals.map((x) => x.id) } } : {}),
        drop: {
          windows,
          statements: [...removed],
          groups: [...gone],
          session: request.session,
          log: removedWindows.flatMap((x) => (x.sources?.length ? x.sources : x.turns)),
          dossier: dropped,
        },
      },
      { revision },
    )
    const result = { windows: windows.length, statements: rows.length, groups: gone.size }
    logger.event("forget", result)
    return result
  }

  return {
    remember: (input, call) =>
      serial(input.person, async () => {
        const { person, ...request } = input
        const result = await Save.remember(store, embedder, person, request, call)
        return result
      }),
    inspect: (person, query, call) => Inspect.page(store, person, query, call),
    evidence: (person, query, call) => Inspect.evidence(store, person, query, call),
    ingest: (input, call) => serial(input.person, () => ingest(input, call)),
    fold: (person, call) => serial(person, () => fold(person, call)),
    source: (person, query, call) => Inspect.source(store, person, query, call),
    previewForget: ({ person, ...query }, call) => Forget.preview(store, person, query, call),
    forget: (input, call) => serial(input.person, () => forget(input, call)),
    recall: async (input, call) => {
      const revision = await store.revision(input.person)
      const result = await recall(input, call)
      call?.signal?.throwIfAborted()
      // A read waiting on a provider must not return content erased while it was in flight.
      return revision === (await store.revision(input.person))
        ? result
        : { person: input.person, statements: [], windows: [] }
    },
    dossier: (person) => store.dossiers.get(Id.Info.parse(person)),
    erase: (person) => serial(person, () => store.erase(Id.Info.parse(person))),
  }
}

export * as Memory from "./memory"
