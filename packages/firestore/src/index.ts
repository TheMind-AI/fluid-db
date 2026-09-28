import type { Firestore as Database, Transaction, Query as FireQuery } from "@google-cloud/firestore"
import { Records } from "@fluiddb/core/records"
import { ConflictError, type Store } from "@fluiddb/core"

export interface Options {
  collection?: string
}

const encoded = (value: string) =>
  Array.from({ length: value.length }, (_, i) => value.charCodeAt(i).toString(16).padStart(4, "0")).join("")
const order = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0)

/** Server SDK only. Pass the product's existing Firestore client; no credentials are loaded by FluidDB. */
export function create(db: Database, options: Options = {}): Store {
  const collection = options.collection ?? "fluiddb_people"
  if (!/^[a-zA-Z][a-zA-Z0-9_-]{0,80}$/.test(collection)) throw new Error("Invalid FluidDB collection name")
  const root = (person: string) => db.collection(collection).doc(encoded(person))
  const records = (person: string) => root(person).collection("records")
  const ref = (person: string, kind: string, id: string) => records(person).doc(`${kind}_${encoded(id)}`)
  const reader = (tx?: Transaction): Records.Reader => ({
    read: async (person, query) => {
      if (query.ids?.length === 0 || query.values?.length === 0) return []
      // ID reads avoid Firestore's in-query cardinality limit and preserve an identical contract for long batches.
      if (query.ids) {
        const unique = [...new Set(query.ids)]
        const out: Records.Record[] = []
        for (let i = 0; i < unique.length; i += 100) {
          const refs = unique.slice(i, i + 100).map((id) => ref(person, query.kind, id))
          const docs = tx ? await tx.getAll(...refs) : await db.getAll(...refs)
          out.push(...docs.filter((d) => d.exists).map((d) => d.data() as Records.Record))
        }
        return out
      }
      // One indexed kind query, then person-local filtering: works without product-specific composite indexes.
      // Exact vectors and chronological paging are linear in this person's selected record kind.
      const source: FireQuery = records(person).where("kind", "==", query.kind)
      const snapshot = tx ? await tx.get(source) : await source.get()
      let rows = snapshot.docs.map((d) => d.data() as Records.Record)
      if (query.field) rows = rows.filter((x) => query.values!.includes(x.data[query.field!] as string | boolean))
      if (query.ordered) rows.sort((a, b) => order(String(a.data.at), String(b.data.at)) || order(a.id, b.id))
      if (query.after)
        rows = rows.filter(
          (x) => String(x.data.at) > query.after!.at || (x.data.at === query.after!.at && x.id > query.after!.id),
        )
      return query.limit === undefined ? rows : rows.slice(0, query.limit)
    },
  })
  return Records.store({
    ...reader(),
    revision: async (person) => (await root(person).get()).data()?.revision ?? null,
    transaction: async (person, work, expected) => {
      await db.runTransaction(async (tx) => {
        // Serializes atomic changes for this person, including decisions based on absent records.
        const current = await tx.get(root(person))
        if (expected && expected.revision !== (current.data()?.revision ?? null))
          throw new ConflictError("Memory changed; retry with the same IDs.")
        const mutations = await work(reader(tx))
        // Reads precede all writes. The transaction either commits every mutation or fails; never chunk commits.
        for (const x of mutations) {
          const target = ref(person, x.kind, x.id)
          if (x.data === undefined) tx.delete(target)
          else tx.set(target, JSON.parse(JSON.stringify(x)))
        }
        tx.set(root(person), { revision: crypto.randomUUID() })
      })
    },
    erase: async (person) => {
      await db.runTransaction(async (tx) => {
        await tx.get(root(person))
        const snapshot = await tx.get(records(person))
        for (const doc of snapshot.docs) tx.delete(doc.ref)
        // @ref LLP 0023#forgetting — a fresh marker invalidates work started before this erasure
        tx.set(root(person), { revision: crypto.randomUUID() })
      })
    },
  })
}
export * as Firestore from "./index"
