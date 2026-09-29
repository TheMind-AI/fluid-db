import assert from "node:assert/strict"
import path from "node:path"
import { readFile } from "node:fs/promises"
import { entrypoints, modules, optionalPeers } from "./packages"

// @ref LLP 0023#packaging — publishing uses one reviewed artifact, never the internal workspaces
const root = path.resolve(import.meta.dir, "..")
try {
  const workspace = await Bun.file(path.join(root, "package.json")).json()
  const source = await Bun.file(path.join(root, "packages/fluiddb/package.json")).json()
  const target = path.join(root, "dist/fluiddb")
  const manifest = await Bun.file(path.join(target, "package.json")).json()
  for (const module of modules) {
    const internal = await Bun.file(path.join(root, "packages", module, "package.json")).json()
    assert.equal(internal.private, true, `${module}: source workspaces must stay private`)
  }
  assert.equal(manifest.private, undefined)
  assert.equal(manifest.name, source.name, "Rebuild stale package name")
  assert.equal(manifest.version, source.version, "Rebuild stale version")
  assert.match(manifest.version, /^1\.0\.0-next\.\d+$/, "Use a v1 preview until production quality is established")
  if (process.env.FLUID_RELEASE_TAG !== undefined)
    assert.equal(process.env.FLUID_RELEASE_TAG, `v${manifest.version}`, "Release tag must match the package version")
  assert.equal(manifest.repository?.url, workspace.repository.url)
  assert.equal(manifest.repository?.directory, "packages/fluiddb")
  assert.deepEqual(manifest.publishConfig, { access: "public", tag: "next", registry: "https://registry.npmjs.org/" })
  assert.deepEqual(Object.keys(manifest.exports).sort(), Object.keys(entrypoints).sort())
  for (const [name, version] of Object.entries(manifest.dependencies)) {
    assert.ok(!name.startsWith("@fluiddb/"), "The single package must bundle internal FluidDB modules")
    assert.ok(!/^(workspace|catalog|file|link):/.test(String(version)), "Unresolved local dependency")
  }
  for (const peer of optionalPeers) assert.equal(manifest.peerDependenciesMeta[peer].optional, true)
  for (const entry of Object.values(manifest.exports) as { types: string; import: string }[]) {
    for (const file of [entry.types, entry.import])
      assert.ok(await Bun.file(path.join(target, file)).exists(), `Missing ${file}`)
  }
  assert.equal(manifest.license, workspace.license)
  assert.ok(manifest.license && manifest.license !== "UNLICENSED", "Choose an approved public license")
  for (const file of ["LICENSE", "NOTICE"]) {
    const text = await readFile(path.join(root, file), "utf8")
    assert.ok(text.trim(), `${file} must not be empty`)
    assert.equal(await readFile(path.join(target, file), "utf8"), text, `Rebuild ${file}`)
  }
  console.log(`Validated one package: ${manifest.name}@${manifest.version}. This check performs no publishing.`)
  console.log("For a new, verified version with npm authentication and package-name access:")
  console.log(
    `npm publish ./dist/tarballs/fluiddb-fluiddb-${manifest.version}.tgz --access public --tag next --ignore-scripts --registry=https://registry.npmjs.org/`,
  )
} catch (error) {
  console.error(
    `BLOCKED: ${error instanceof Error ? error.message : "Artifact validation failed"}; run bun run verify:packages`,
  )
  process.exitCode = 1
}
