import path from "node:path"
import { createHash } from "node:crypto"
import { Api, Dossier, Group, Statement, Window, type Recall } from "@fluiddb/schema"
import { Memory, type Decider, type LanguageModel, type Embedder } from "@fluiddb/core"
import { SqlStore } from "@fluiddb/sql"
import { Bun as Sqlite } from "@fluiddb/sql/bun"
import { z } from "zod"

// @ref LLP 0023.001#research-protocol — exact cached inputs, real SQLite retrieval, labels isolated from providers
const directory = path.resolve(process.argv[2] ?? ".context/research-parity")
if (!directory.split(path.sep).includes(".context")) throw new Error("Private replay must live under .context")
const Input = z.object({
  person: z.string(),
  windows: z.array(Window.Info),
  statements: z.array(Statement.Info),
  groups: z.array(Group.Info),
  dossier: Dossier.Info,
  vectors: z.array(z.object({ id: z.string(), kind: z.enum(["statement", "window"]), vector: z.string() })),
  queries: z.array(z.object({ id: z.string(), question: z.string(), vector: z.string() })),
  calls: z.record(z.string(), z.unknown()),
})
const Label = z.array(
  z.object({
    id: z.string(),
    statements: z.array(z.string()),
    windows: z.array(z.string()),
    statementCandidates: z.array(z.string()),
    windowCandidates: z.array(z.string()),
    evidenceHash: z.string(),
    score: z.number(),
  }),
)
const hash = (text: string) => createHash("sha256").update(text).digest("hex")
const vector = (text: string) => {
  const bytes = Buffer.from(text, "base64")
  return new Float32Array(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength))
}
const same = (a: string[], b: string[]) => JSON.stringify(a) === JSON.stringify(b)
function assembled(recall: Recall.Info) {
  const parts: [string, string[]][] = [
    ["dossier_inc", recall.dossier ? [recall.dossier] : []],
    ["statements_linked_p3", recall.statements.map((x) => x.text)],
    ["log_p3", recall.windows.map((x) => x.text)],
  ].filter((x) => (x[1] as string[]).length) as [string, string[]][]
  const share = Math.floor(48_000 / parts.length)
  return parts
    .map(([store, texts]) => {
      let used = 0
      const kept: string[] = []
      for (const text of texts) {
        const chars = Array.from(text).length
        if (kept.length && used + chars > share) break
        kept.push(text)
        used += chars
      }
      return `## ${store}\n${kept.join("\n")}`
    })
    .join("\n\n")
}
const manifest = await Bun.file(path.join(directory, "manifest.json")).json()
const result = {
  accounts: 0,
  questions: 0,
  completed: 0,
  missingPickerInputs: 0,
  statementCandidateMatches: 0,
  windowCandidateMatches: 0,
  statementOrderMatches: 0,
  windowOrderMatches: 0,
  exactEvidenceMatches: 0,
  matchedEvidenceScore: 0,
  providerRequests: 0,
  evaluated: "read path with imported historical memories; creation and retelling detection excluded",
  historical: manifest.historical,
}
for (const filename of [...new Bun.Glob("*.inputs.json").scanSync(directory)].sort()) {
  const data = Input.parse(await Bun.file(path.join(directory, filename)).json())
  const sql = Sqlite.open()
  const store = SqlStore.store(sql)
  await store.write(data.person, {
    windows: data.windows,
    statements: data.statements,
    groups: data.groups,
    vectors: data.vectors.map((x) => ({ ...x, vector: vector(x.vector) })),
    dossier: { info: data.dossier, windows: data.windows.map((x) => x.id) },
  })
  const cachedModel: LanguageModel = {
    id: "cached-openai",
    processors: ["openai"],
    text: async () => {
      throw new Error("Unexpected text generation")
    },
    object: async (prompt, schema) => {
      const cached = data.calls[hash(prompt)]
      if (cached === undefined) throw new Error("Missing exact picker input")
      return schema.parse(cached)
    },
  }
  const queries = new Map(data.queries.map((q) => [q.question, vector(q.vector)]))
  const embedder: Embedder = {
    id: "cached-openai-embeddings",
    processors: ["openai"],
    embed: async (texts) =>
      texts.map((text) => {
        const found = queries.get(text)
        if (!found) throw new Error("Missing exact embedding input")
        return found
      }),
  }
  const excluded: Decider = {
    id: "excluded-from-historical-config",
    processors: [],
    yes: async () => {
      throw new Error("Unexpected decider call")
    },
    choose: async () => {
      throw new Error("Unexpected detector call")
    },
  }
  const memory = Memory.create({
    store,
    model: cachedModel,
    embedder,
    decider: excluded,
    processors: ["openai"],
    options: { pick: "model", detect: false, assistant: "Mind", role: "therapy assistant" },
  })
  // Complete retrieval before loading the labels. Providers have access only to input vectors and exact prompt caches.
  const actual = []
  for (const query of data.queries) {
    const v = queries.get(query.question)!
    const statementCandidates = (await store.vectors.query(data.person, v, { kind: "statement", top: 40 })).map(
      (x) => x.id,
    )
    const windowCandidates = (await store.vectors.query(data.person, v, { kind: "window", top: 20 })).map((x) => x.id)
    try {
      const recall = await memory.recall({
        person: data.person,
        conversation: [{ id: query.id, role: "person", at: data.dossier.at, text: query.question }],
      })
      actual.push({ id: query.id, statementCandidates, windowCandidates, recall })
    } catch (error) {
      if (!(error instanceof Error) || error.message !== "Missing exact picker input")
        throw new Error("Replay failed; inspect private inputs locally")
      result.missingPickerInputs++
      actual.push({ id: query.id, statementCandidates, windowCandidates, recall: undefined })
    }
  }
  const labels = new Map(
    Label.parse(await Bun.file(path.join(directory, filename.replace(".inputs.", ".labels."))).json()).map((x) => [
      x.id,
      x,
    ]),
  )
  for (const output of actual) {
    const label = labels.get(output.id)!
    result.questions++
    result.statementCandidateMatches += Number(same(output.statementCandidates, label.statementCandidates))
    result.windowCandidateMatches += Number(same(output.windowCandidates, label.windowCandidates))
    if (!output.recall) continue
    result.completed++
    result.statementOrderMatches += Number(
      same(
        output.recall.statements.flatMap((x) => x.ids),
        label.statements,
      ),
    )
    result.windowOrderMatches += Number(
      same(
        output.recall.windows.flatMap((x) => x.ids),
        label.windows,
      ),
    )
    if (hash(assembled(output.recall)) === label.evidenceHash) {
      result.exactEvidenceMatches++
      result.matchedEvidenceScore += label.score
    }
  }
  result.accounts++
  sql.close()
}
await Bun.write(path.join(directory, "typescript-result.json"), JSON.stringify(result, null, 2))
console.log(JSON.stringify(result, null, 2))
