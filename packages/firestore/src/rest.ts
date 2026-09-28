import { ConflictError, type Store } from "@fluiddb/core"
import { Records } from "@fluiddb/core/records"

export interface Options {
  projectId: string
  databaseId?: string
  collection?: string
  /** OAuth access token supplied by the host. Return an empty string only for a local emulator. */
  accessToken(): Promise<string>
  fetch?: typeof fetch
  /** API origin, e.g. http://127.0.0.1:8080 for the emulator. */
  origin?: string
  timeoutMs?: number
}

type Value = {
  nullValue?: null
  stringValue?: string
  booleanValue?: boolean
  integerValue?: string
  doubleValue?: number
  arrayValue?: { values?: Value[] }
  mapValue?: { fields?: Fields }
}
type Fields = { [key: string]: Value }
interface Document {
  name: string
  fields?: Fields
  updateTime: string
}
const encoded = (text: string) =>
  Array.from({ length: text.length }, (_, i) => text.charCodeAt(i).toString(16).padStart(4, "0")).join("")
function encode(value: unknown): Value {
  if (value === null) return { nullValue: null }
  if (typeof value === "string") return { stringValue: value }
  if (typeof value === "boolean") return { booleanValue: value }
  if (typeof value === "number" && Number.isFinite(value)) return { doubleValue: value }
  if (Array.isArray(value)) return { arrayValue: { values: value.map(encode) } }
  if (value && typeof value === "object")
    return {
      mapValue: {
        fields: Object.fromEntries(
          Object.entries(value)
            .filter(([, x]) => x !== undefined)
            .map(([k, x]) => [k, encode(x)]),
        ),
      },
    }
  throw new Error("Unsupported Firestore value")
}
function decode(value: Value): unknown {
  if ("nullValue" in value) return null
  if ("stringValue" in value) return value.stringValue
  if ("booleanValue" in value) return value.booleanValue
  if ("doubleValue" in value) return value.doubleValue
  if ("integerValue" in value) return Number(value.integerValue)
  if (value.arrayValue) return (value.arrayValue.values ?? []).map(decode)
  if (value.mapValue)
    return Object.fromEntries(Object.entries(value.mapValue.fields ?? {}).map(([k, x]) => [k, decode(x)]))
  throw new Error("Unexpected Firestore value")
}
const data = (document: Document) => decode({ mapValue: { fields: document.fields } }) as Records.Record
const fields = (value: object) => encode(value).mapValue!.fields!
const compare = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0)

/** Fetch/Web Crypto only: usable in Workers without the Node Firestore client. */
export function create(options: Options): Store {
  const collection = options.collection ?? "fluiddb_people"
  if (!/^[a-zA-Z][a-zA-Z0-9_-]{0,80}$/.test(collection) || !options.projectId || /[/?#]/.test(options.projectId))
    throw new Error("Invalid Firestore namespace")
  const origin = new URL(options.origin ?? "https://firestore.googleapis.com")
  if (
    !["https:", "http:"].includes(origin.protocol) ||
    origin.username ||
    origin.password ||
    origin.search ||
    origin.hash ||
    origin.pathname !== "/"
  )
    throw new Error("Invalid Firestore API origin")
  const database = options.databaseId ?? "(default)"
  if (!database || /[/?#]/.test(database)) throw new Error("Invalid Firestore database")
  const timeoutMs = options.timeoutMs ?? 30_000
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1) throw new Error("timeoutMs must be a positive integer")
  const base = `projects/${options.projectId}/databases/${database}/documents`
  const root = (person: string) => `${base}/${collection}/${encoded(person)}`
  const ref = (person: string, kind: string, id: string) => `${root(person)}/records/${kind}_${encoded(id)}`
  const send = async (method: string, name: string, body?: unknown): Promise<unknown> => {
    const token = await options.accessToken()
    if (!token && origin.protocol === "https:") throw new Error("A Firestore access token is required")
    const response = await (options.fetch ?? fetch)(`${origin.origin}/v1/${name}`, {
      method,
      headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}) },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      redirect: "manual",
      signal: AbortSignal.timeout(timeoutMs),
    })
    if (response.status >= 300 && response.status < 400) throw new Error("Firestore redirects are not permitted")
    if (response.status === 404 && method === "GET") return null
    const output = (await response.json().catch(() => {
      throw new Error("Firestore returned invalid JSON")
    })) as { error?: { status?: string } }
    if (!response.ok) {
      if (
        [409, 412].includes(response.status) ||
        ["ABORTED", "FAILED_PRECONDITION", "ALREADY_EXISTS"].includes(output.error?.status ?? "")
      )
        throw new ConflictError("Memory changed; retry with the same IDs.")
      throw new Error(`Firestore request failed (${response.status})`)
    }
    return output
  }
  const revisionDocument = (person: string) => send("GET", root(person)) as Promise<Document | null>
  const revisionOf = (document: Document | null) => document?.fields?.revision?.stringValue ?? null
  const queryRecords = async (person: string, kind?: string) => {
    const result = (await send("POST", `${root(person)}:runQuery`, {
      structuredQuery: {
        from: [{ collectionId: "records" }],
        ...(kind
          ? { where: { fieldFilter: { field: { fieldPath: "kind" }, op: "EQUAL", value: { stringValue: kind } } } }
          : {}),
      },
    })) as { document?: Document }[]
    return result.flatMap((x) => (x.document ? [x.document] : []))
  }
  const reader: Records.Reader = {
    read: async (person, query) => {
      if (query.ids?.length === 0 || query.values?.length === 0) return []
      let rows: Records.Record[] = []
      if (query.ids) {
        const ids = [...new Set(query.ids)]
        for (let i = 0; i < ids.length; i += 100) {
          const results = (await send("POST", `${base}:batchGet`, {
            documents: ids.slice(i, i + 100).map((id) => ref(person, query.kind, id)),
          })) as { found?: Document }[]
          rows.push(...results.flatMap((x) => (x.found ? [data(x.found)] : [])))
        }
      } else rows = (await queryRecords(person, query.kind)).map(data)
      if (query.field) rows = rows.filter((x) => query.values!.includes(x.data[query.field!] as string | boolean))
      if (query.ordered) rows.sort((a, b) => compare(String(a.data.at), String(b.data.at)) || compare(a.id, b.id))
      if (query.after)
        rows = rows.filter(
          (x) => String(x.data.at) > query.after!.at || (x.data.at === query.after!.at && x.id > query.after!.id),
        )
      return query.limit === undefined ? rows : rows.slice(0, query.limit)
    },
  }
  const guarded = (document: Document | null) => (document ? { updateTime: document.updateTime } : { exists: false })
  return Records.store({
    ...reader,
    revision: async (person) => revisionOf(await revisionDocument(person)),
    transaction: async (person, work, expected) => {
      const before = await revisionDocument(person)
      if (expected && expected.revision !== revisionOf(before))
        throw new ConflictError("Memory changed; retry with the same IDs.")
      const changes = await work(reader)
      const writes = changes.map((x) =>
        x.data === undefined
          ? { delete: ref(person, x.kind, x.id) }
          : { update: { name: ref(person, x.kind, x.id), fields: fields(x) } },
      )
      // Firestore commit is atomic. All adapter writes change the guard, so its precondition validates every read.
      await send("POST", `${base}:commit`, {
        writes: [
          ...writes,
          {
            update: { name: root(person), fields: fields({ revision: crypto.randomUUID() }) },
            currentDocument: guarded(before),
          },
        ],
      })
    },
    erase: async (person) => {
      const before = await revisionDocument(person)
      const documents = await queryRecords(person)
      // @ref LLP 0023#forgetting — even erasing an absent person must invalidate its pending first save
      await send("POST", `${base}:commit`, {
        writes: [
          ...documents.map((x) => ({ delete: x.name })),
          {
            update: { name: root(person), fields: fields({ revision: crypto.randomUUID() }) },
            currentDocument: guarded(before),
          },
        ],
      })
    },
  })
}
export * as FirestoreRest from "./rest"
