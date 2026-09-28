import assert from "node:assert/strict"
import { mkdtemp, writeFile, rm, readdir, readFile } from "node:fs/promises"
import { createRequire } from "node:module"
import { pathToFileURL } from "node:url"
import { tmpdir } from "node:os"
import path from "node:path"
// Install actual tarballs (or published versions with --registry) outside the workspace, then consume their JS
// and declarations. Workspace links cannot hide a broken package. No lifecycle scripts or model calls run here.
const root = path.resolve(import.meta.dir, "..")
const directory = await mkdtemp(path.join(tmpdir(), "fluiddb-packages-"))
const run = async (cmd: string[]) => {
  const proc = Bun.spawn(cmd, { cwd: directory, stdout: "inherit", stderr: "inherit" })
  assert.equal(await proc.exited, 0, cmd.join(" "))
}
try {
  const manifest = await Bun.file(path.join(root, "packages/fluiddb/package.json")).json()
  const name = manifest.name as string
  let packages: string[]
  if (process.argv.includes("--registry")) {
    packages = [`${name}@${manifest.version}`]
  } else {
    const tarballs = [...new Bun.Glob("*.tgz").scanSync(path.join(root, "dist/tarballs"))]
    assert.equal(tarballs.length, 1, "FluidDB ships exactly one tarball")
    packages = tarballs.map((file) => path.join(root, "dist/tarballs", file))
  }
  await writeFile(path.join(directory, "package.json"), JSON.stringify({ private: true, type: "module" }))
  await run([
    "npm",
    "install",
    "--ignore-scripts",
    "--no-audit",
    "--no-fund",
    "--registry=https://registry.npmjs.org/",
    "@modelcontextprotocol/client@2.1.0",
    ...packages,
  ])
  const scopeDirectory = path.join(directory, "node_modules/@fluiddb")
  const scopedPackages = await readdir(scopeDirectory).catch((error: NodeJS.ErrnoException) => {
    if (error.code === "ENOENT") return []
    throw error
  })
  assert.deepEqual(scopedPackages, name.startsWith("@fluiddb/") ? [name.split("/")[1]] : [])
  const packageDirectory = path.join(directory, "node_modules", name)
  const installed = JSON.parse(await readFile(path.join(packageDirectory, "package.json"), "utf8"))
  assert.equal(installed.name, name, "Rebuild the package after a name change")
  assert.equal(installed.version, manifest.version, "Rebuild the package after a version change")
  assert.ok(!Object.keys(installed.dependencies).some((dependency) => dependency.startsWith("@fluiddb/")))
  for await (const file of new Bun.Glob("**/*.{js,d.ts}").scan(packageDirectory)) {
    const source = await readFile(path.join(packageDirectory, file), "utf8")
    assert.doesNotMatch(source, /(?:from\s*|import\s*(?:\(\s*)?)["']@fluiddb\//, `Private import in ${file}`)
  }
  // Optional Node database clients are unnecessary for the root, Firestore REST, CLI and MCP paths.
  for (const dependency of ["pg", "@google-cloud/firestore"]) {
    assert.equal(await Bun.file(path.join(directory, "node_modules", dependency, "package.json")).exists(), false)
  }
  await writeFile(
    path.join(directory, "consumer.mjs"),
    `
import assert from 'node:assert/strict'
import { Memory, Person, Render, Pick, ConflictError } from '${name}'
import { Client as McpClient, InMemoryTransport } from '@modelcontextprotocol/client'
import { Node as NodeSqlite } from '${name}/sqlite/node'
import { FirestoreRest } from '${name}/firestore/rest'
import { Local } from '${name}/local'
import { Testing } from '${name}/testing'
import { Dataset, evaluate, compare } from '${name}/eval'
import { OpenAI, Jev, Shim } from '${name}'
import { Client } from '${name}/client'
import { SqlStore } from '${name}/sqlite'
import { Postgres } from '${name}/postgres'
import { Firestore } from '${name}/firestore'
import { FluidMcp } from '${name}/mcp'
import { createHttpHandler } from '${name}/mcp/http'
import { Turn } from '${name}/schema'
import { Feedback } from '${name}/feedback'
import { Skills } from '${name}/skills'
import { run as runCli } from '${name}/cli'
assert.equal(typeof Pick.route,'function')
const dataset = Dataset.parse({id:'packed',description:'Package smoke',keep:{statements:3,windows:2},steps:[{type:'probe',id:'empty',person:'demo',conversation:[{id:'query',role:'person',text:'Hello',at:'2026-03-01T00:00:00Z'}],expected:{empty:true}}]})
const evaluateDeps = () => ({store:Local.store(),model:Testing.model(),embedder:Testing.embedder(),decider:Testing.decider()})
assert.equal((await evaluate(dataset,evaluateDeps())).passed,true)
assert.equal((await compare(dataset,['one','two'].map(id=>({id,create:()=>({deps:evaluateDeps()})})))).complete,true)
assert.equal(Skills.list().length, 2)
assert.match(Skills.read('fluiddb-memory').text, /fluiddb_recall/)
const report = {feedback:'Synthetic package test', resume:'packed-thread', idempotencyKey:'packed-retry'}
const feedback = Feedback.create({fetch: async (_, init) => {
 const event = JSON.parse(init.body)
 assert.equal(event.consent.telemetry, false)
 return Response.json({id:'packed-event',thread:event.thread.id})
}})
assert.equal((await feedback.submit(report)).thread,'packed-thread')
assert.match(await runCli(['skills','read','fluiddb-feedback'], {readInput:async()=>''}), /HiveNet/)
const turn = Turn.Info.parse({id:'1',role:'person',text:'My sister Anna lives in Berlin.',at:'2026-03-01T10:00:00Z'})
const memory = Memory.create({store:Local.store(), model:Testing.model(),embedder:Testing.embedder(),decider:Testing.decider()})
assert.equal((await memory.ingest({person:'demo',session:'1',turns:[turn]})).statements,1)
await memory.fold('demo')
assert.match(Render.render(await memory.recall({person:'demo',conversation:[turn]})), /Berlin/)
for (const value of [OpenAI.model,Jev.decider,Shim.decider,Client.create,SqlStore.store,Postgres.create,Firestore.create,FluidMcp.create,createHttpHandler]) assert.equal(typeof value,'function')
const sql = NodeSqlite.open()
const stored = SqlStore.store(sql)
await stored.write('p', {turns:{session:'s',ids:['original']}})
assert.deepEqual(await stored.turns.seen('p',['original']),['original'])
await assert.rejects(stored.write('p', {}, {revision:null}), (error) => error instanceof ConflictError)
sql.close()
assert.equal(typeof FirestoreRest.create,'function')
const server = FluidMcp.create({memory:Person.bind(memory,'demo')})
const protocol = new McpClient({name:'packed-consumer',version:'1'})
const [a,b] = InMemoryTransport.createLinkedPair()
await server.connect(b); await protocol.connect(a)
const result = await protocol.callTool({name:'fluiddb_inspect',arguments:{}})
assert.equal(result.structuredContent.items.length,1)
const skill = await protocol.callTool({name:'fluiddb_read_skill',arguments:{name:'fluiddb-memory'}})
assert.equal(skill.structuredContent.text,Skills.read('fluiddb-memory').text)
await protocol.close(); await server.close()
console.log('Packaged ESM works in Node without a TypeScript loader.')
`,
  )
  await run(["node", "consumer.mjs"])
  await run([path.join(directory, "node_modules/.bin/fluiddb-mcp"), "--help"])
  await run([path.join(directory, "node_modules/.bin/fluiddb"), "--help"])
  await run([path.join(directory, "node_modules/.bin/fluiddb"), "skills", "list"])
  await writeFile(
    path.join(directory, "portable.ts"),
    `
import { Memory, Person, OpenAI, Shim, type Store } from '${name}'
import { FirestoreRest } from '${name}/firestore/rest'
import { Local } from '${name}/local'
import { Testing } from '${name}/testing'
const store: Store = Local.store()
const memory = Memory.create({store,model:Testing.model(),embedder:Testing.embedder(),decider:Testing.decider()})
void [Person.bind(memory,'p'), OpenAI.model, Shim.decider, FirestoreRest.create]
`,
  )
  const compiler = path.join(root, "node_modules/.bin/tsc")
  await run([
    compiler,
    "--noEmit",
    "--strict",
    "--target",
    "ES2023",
    "--module",
    "NodeNext",
    "--moduleResolution",
    "NodeNext",
    "portable.ts",
  ])
  await writeFile(
    path.join(directory, "worker.ts"),
    `
import { Memory, Person } from '${name}'
import { Local } from '${name}/local'
import { Testing } from '${name}/testing'
import { FirestoreRest } from '${name}/firestore/rest'
export default { async fetch() {
  if (typeof FirestoreRest.create !== 'function') throw new Error('Missing REST adapter')
  const memory = Person.bind(Memory.create({store:Local.store(),model:Testing.model(),embedder:Testing.embedder(),decider:Testing.decider()}),'worker')
  const turn = {id:'original',role:'person' as const,text:'My sister Anna lives in Berlin.',at:'2026-03-01T10:00:00Z'}
  await memory.ingest('one',[turn])
  const result = await memory.recall({conversation:[turn]})
  if (!result.prompt?.includes('Berlin')) throw new Error('Missing memory')
  return Response.json({ok:true})
}}
`,
  )
  await run([
    compiler,
    "--noEmit",
    "--strict",
    "--target",
    "ES2023",
    "--module",
    "NodeNext",
    "--moduleResolution",
    "NodeNext",
    "worker.ts",
  ])
  const bundled = await Bun.build({
    entrypoints: [path.join(directory, "worker.ts")],
    outdir: path.join(directory, "worker"),
    target: "browser",
    format: "esm",
  })
  assert.ok(bundled.success, bundled.logs.map(String).join("\n"))
  const workerRequire = createRequire(path.join(root, "packages/worker/package.json"))
  await writeFile(
    path.join(directory, "workerd.mjs"),
    `
import assert from 'node:assert/strict'
import { Miniflare, convertV4MiniflareOptions } from ${JSON.stringify(pathToFileURL(workerRequire.resolve("miniflare")).href)}
const runtime = new Miniflare(convertV4MiniflareOptions({name:'packed-fluiddb',modules:true,scriptPath:'worker/worker.js',compatibilityDate:'2026-09-01'}))
try {
 const response = await runtime.dispatchFetch('http://fixture.test/')
 assert.equal(response.status,200,await response.clone().text())
 assert.deepEqual(await response.json(),{ok:true})
 console.log('Single-package SDK and Firestore REST entry point run in workerd without Node compatibility.')
} finally { await runtime.dispose() }
`,
  )
  await run(["node", "workerd.mjs"])
  // The host supplies these clients only when using their Node-specific database adapters.
  await run([
    "npm",
    "install",
    "--ignore-scripts",
    "--no-audit",
    "--no-fund",
    "--registry=https://registry.npmjs.org/",
    "pg@8.23.0",
    "@types/pg@8.23.1",
    "@google-cloud/firestore@9.2.0",
  ])
  await writeFile(
    path.join(directory, "consumer.ts"),
    `
import { Memory, type Store, type Picker } from '${name}'
import { Local } from '${name}/local'
import { Testing } from '${name}/testing'
import { Dataset, evaluate, compare } from '${name}/eval'
import { OpenAI } from '${name}'
import { Client } from '${name}/client'
import { SqlStore } from '${name}/sqlite'
import { Postgres } from '${name}/postgres'
import { Firestore } from '${name}/firestore'
import { FluidMcp } from '${name}/mcp'
import { createHttpHandler } from '${name}/mcp/http'
import type { Turn } from '${name}/schema'
import { Feedback } from '${name}/feedback'
import { Skills } from '${name}/skills'
import { run as runCli } from '${name}/cli'
const report: Feedback.Report = {feedback:'Synthetic typecheck', eval:{task:'Reproduce',expected:'Expected',actual:'Observed'}}
void [Feedback.create, Skills.list, runCli, report, Dataset, evaluate, compare]
const store: Store = Local.store()
const memory: Memory.Service = Memory.create({store, model:Testing.model(),embedder:Testing.embedder(),decider:Testing.decider()})
const turns: Turn.Info[] = []
void memory.ingest({person:'demo',session:'demo',turns})
// @ts-expect-error person ids are strings, not numbers
void memory.ingest({person:123,session:'demo',turns})
// @ts-expect-error a turn role must be person or assistant
const wrong: Turn.Info = {id:'x',role:'bot',text:'hi',at:'2026-03-01T00:00:00Z'}
void [OpenAI.model, Client.create, SqlStore.store]
`,
  )
  await run([
    path.join(root, "node_modules/.bin/tsc"),
    "--noEmit",
    "--strict",
    "--target",
    "ES2023",
    "--module",
    "NodeNext",
    "--moduleResolution",
    "NodeNext",
    "consumer.ts",
  ])
  console.log("Packaged declarations work with NodeNext module resolution.")
} finally {
  await rm(directory, { recursive: true, force: true })
}
