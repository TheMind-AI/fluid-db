import { Dossier, Group, Id, Statement, Turn, Window } from "@fluiddb/schema"
import { z } from "zod"
import type { Changes } from "./port"

const Rows = {
  windows: z.array(Window.Info),
  statements: z.array(Statement.Info),
  groups: z.array(Group.Info),
  ids: z.array(z.string().min(1).max(200)),
}

// Every row of a write is checked before a store applies any of it, so a bad write changes nothing. Messages name
// what is wrong, never whose data it is.
export function check(person: string, changes: Changes) {
  Id.Info.parse(person)
  if (changes.log) {
    Id.Info.parse(changes.log.session)
    z.array(Turn.Info).parse(changes.log.turns)
  }
  const rows = [
    ...Rows.windows.parse(changes.windows ?? []),
    ...Rows.statements.parse(changes.statements ?? []),
    ...Rows.groups.parse(changes.groups ?? []),
    ...(changes.dossier ? [Dossier.Info.parse(changes.dossier.info)] : []),
  ]
  if (rows.some((x) => x.person !== person)) throw new Error("a row of the write belongs to another person")
  for (const x of changes.vectors ?? []) {
    if (!(x.vector instanceof Float32Array) || !x.vector.length) throw new Error("a vector of the write is empty")
    if (!x.vector.every(Number.isFinite)) throw new Error("a vector of the write contains non-finite values")
  }
  Rows.ids.parse(changes.turns?.ids ?? [])
  Rows.ids.parse(changes.dossier?.windows ?? [])
  Rows.ids.parse(changes.drop?.log ?? [])
}

export * as Write from "./write"
