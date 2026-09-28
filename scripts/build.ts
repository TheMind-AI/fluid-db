import { cp, mkdir, readFile, rm, writeFile } from "node:fs/promises"
import path from "node:path"
import { createScanner, SyntaxKind } from "typescript/unstable/ast"
import { entrypoints, modules, optionalPeers } from "./packages"

// @ref LLP 0023#packaging — one public artifact bundles private modules and shares their runtime identities
const root = path.resolve(import.meta.dir, "..")
const workspace = await Bun.file(path.join(root, "package.json")).json()
const output = path.join(root, "dist")
const destination = path.join(output, "fluiddb")
type ModuleManifest = {
  name: string
  version: string
  description: string
  exports: Record<string, string>
  dependencies: Record<string, string>
}
const manifests = new Map<string, ModuleManifest>()
const aliases = new Map<string, string>()
const dependencies: Record<string, string> = {}
const peerDependencies: Record<string, string> = {}
const run = async (cmd: string[]) => {
  const proc = Bun.spawn(cmd, { cwd: root, stdout: "inherit", stderr: "inherit" })
  if (await proc.exited) throw new Error(`${cmd.join(" ")} failed`)
}
await run(["bun", "scripts/skills.ts", "--check"])
await rm(output, { recursive: true, force: true })
await mkdir(destination, { recursive: true })

for (const module of modules) {
  const manifest: ModuleManifest = await Bun.file(path.join(root, "packages", module, "package.json")).json()
  manifests.set(module, manifest)
  for (const [specifier, file] of Object.entries(manifest.exports as Record<string, string>)) {
    aliases.set(
      manifest.name + (specifier === "." ? "" : specifier.slice(1)),
      path.join(destination, "types", module, file.replace("./src/", "").replace(/\.ts$/, ".d.ts")),
    )
  }
  for (const [name, version] of Object.entries(manifest.dependencies as Record<string, string>)) {
    if (version.startsWith("workspace:")) continue
    const resolved = version === "catalog:" ? workspace.workspaces.catalog[name] : version
    const target = optionalPeers.includes(name as (typeof optionalPeers)[number]) ? peerDependencies : dependencies
    if (target[name] && target[name] !== resolved) throw new Error(`Conflicting dependency versions for ${name}`)
    target[name] = resolved
  }
}

const exports: Record<string, { types: string; import: string; default: string }> = {}
const sources: string[] = []
for (const [specifier, [module, entry]] of Object.entries(entrypoints)) {
  const file = manifests.get(module)!.exports[entry]!
  sources.push(path.join(root, "packages", module, file))
  const runtime = `./${module}/${file.slice(2).replace(/\.ts$/, ".js")}`
  exports[specifier] = {
    types: `./types/${module}/${file.replace("./src/", "").replace(/\.ts$/, ".d.ts")}`,
    import: runtime,
    default: runtime,
  }
}
const result = await Bun.build({
  entrypoints: sources,
  root: path.join(root, "packages"),
  outdir: destination,
  target: "node",
  format: "esm",
  packages: "bundle",
  external: [...Object.keys(dependencies), ...optionalPeers, "bun:sqlite", "node:*", "cloudflare:workers"],
  splitting: true,
  sourcemap: "none",
})
if (!result.success) throw new AggregateError(result.logs, "Building FluidDB failed")

const paths: Record<string, string[]> = {}
for (const module of modules) {
  const source = path.join(root, "packages", module)
  const target = path.join(destination, "types", module)
  await mkdir(target, { recursive: true })
  const config = path.join(target, "declarations.json")
  await writeFile(
    config,
    JSON.stringify({
      extends: path.join(source, "tsconfig.json"),
      compilerOptions: {
        noEmit: false,
        declaration: true,
        emitDeclarationOnly: true,
        rootDir: path.join(source, "src"),
        outDir: target,
        paths,
      },
      include: [path.join(source, "src/**/*.ts")],
      exclude: [],
    }),
  )
  await run(["bun", "x", "--no-install", "tsc", "-p", config])
  await rm(config)
  const manifest = manifests.get(module)!
  for (const specifier of Object.keys(manifest.exports)) {
    const alias = manifest.name + (specifier === "." ? "" : specifier.slice(1))
    paths[alias] = [aliases.get(alias)!]
  }
}

// Rewrite after all modules emit: consumers resolve one type graph without retired npm workspaces.
for await (const file of new Bun.Glob("types/**/*.d.ts").scan(destination)) {
  const target = path.join(destination, file)
  const source = await readFile(target, "utf8")
  // Scan tokens so literal values such as "import" cannot consume a later module specifier.
  const scanner = createScanner(true, undefined, source)
  const edits: { start: number; end: number; text: string }[] = []
  let previous: SyntaxKind = SyntaxKind.Unknown
  let beforePrevious: SyntaxKind = SyntaxKind.Unknown
  for (let token = scanner.scan(); token !== SyntaxKind.EndOfFile; token = scanner.scan()) {
    if (
      token === SyntaxKind.StringLiteral &&
      (previous === SyntaxKind.FromKeyword ||
        previous === SyntaxKind.ImportKeyword ||
        (previous === SyntaxKind.OpenParenToken && beforePrevious === SyntaxKind.ImportKeyword))
    ) {
      const specifier = scanner.getTokenValue()
      let resolved = specifier
      if (resolved.startsWith("@fluiddb/")) {
        const declaration = aliases.get(resolved)
        if (!declaration) throw new Error(`Unknown private module in declarations: ${resolved}`)
        resolved = path.relative(path.dirname(target), declaration).replace(/\.d\.ts$/, ".js")
        if (!resolved.startsWith(".")) resolved = "./" + resolved
      } else if (resolved.startsWith(".") && !resolved.endsWith(".js")) {
        resolved += ".js"
      }
      if (resolved !== specifier)
        edits.push({ start: scanner.getTokenStart() + 1, end: scanner.getTokenEnd() - 1, text: resolved })
    }
    beforePrevious = previous
    previous = token
  }
  let rewritten = source
  for (const edit of edits.toSorted((a, b) => b.start - a.start))
    rewritten = rewritten.slice(0, edit.start) + edit.text + rewritten.slice(edit.end)
  await writeFile(target, rewritten)
}

const manifest = manifests.get("fluiddb")!
await writeFile(
  path.join(destination, "package.json"),
  JSON.stringify(
    {
      name: manifest.name,
      version: manifest.version,
      description: manifest.description,
      license: workspace.license,
      repository: { ...workspace.repository, directory: "packages/fluiddb" },
      homepage: workspace.homepage,
      bugs: workspace.bugs,
      publishConfig: { access: "public", tag: "next", registry: "https://registry.npmjs.org/" },
      type: "module",
      exports,
      main: exports["."]!.import,
      types: exports["."]!.types,
      files: ["**/*.js", "**/*.d.ts", "README.md", "LICENSE", "NOTICE", "skills"],
      sideEffects: [exports["./cli/run"]!.import, exports["./mcp/cli"]!.import],
      engines: { node: ">=22" },
      bin: { fluiddb: exports["./cli/run"]!.import, "fluiddb-mcp": exports["./mcp/cli"]!.import },
      dependencies,
      peerDependencies,
      peerDependenciesMeta: Object.fromEntries(optionalPeers.map((name) => [name, { optional: true }])),
    },
    null,
    2,
  ) + "\n",
)
for (const file of ["LICENSE", "NOTICE"]) await cp(path.join(root, file), path.join(destination, file))
await cp(path.join(root, "packages/fluiddb/README.md"), path.join(destination, "README.md"))
await cp(path.join(root, "packages/skills/skills"), path.join(destination, "skills"), { recursive: true })
console.log(`Built one package: ${manifest.name}@${manifest.version}`)
