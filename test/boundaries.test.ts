import { describe, expect, test } from "bun:test"
import { Glob } from "bun"
import path from "node:path"

// What each package's source may import, beyond its own files. The core knows ports, not providers or places.
const allowed: Record<string, string[]> = {
  schema: ["zod"],
  fluiddb: ["@fluiddb/core", "@fluiddb/providers", "@fluiddb/cli"],
  core: ["@fluiddb/schema", "zod"],
  eval: ["@fluiddb/core", "@fluiddb/schema", "zod"],
  providers: ["@fluiddb/core", "zod"],
  sql: ["@fluiddb/core", "@fluiddb/schema"],
  client: ["@fluiddb/core", "@fluiddb/schema", "zod"],
  postgres: ["@fluiddb/core", "@fluiddb/schema", "pg"],
  firestore: ["@fluiddb/core", "@fluiddb/schema", "@google-cloud/firestore"],
  feedback: ["zod"],
  skills: [],
  cli: ["@fluiddb/feedback", "@fluiddb/skills"],
  mcp: [
    "@fluiddb/feedback",
    "@fluiddb/skills",
    "@fluiddb/core",
    "@fluiddb/schema",
    "@fluiddb/client",
    "@modelcontextprotocol/server",
    "zod",
  ],
  worker: [
    "@fluiddb/feedback",
    "@fluiddb/skills",
    "@fluiddb/core",
    "@fluiddb/providers",
    "@fluiddb/schema",
    "@fluiddb/sql",
    "hono",
    "zod",
  ],
}
// Runtime-specific imports, each confined to the files that adapt to that runtime.
const confined: Record<string, string[]> = {
  "node:process": ["packages/mcp/src/cli.ts", "packages/cli/src/cli.ts"],
  "node:util": ["packages/cli/src/index.ts"],
  "node:fs/promises": ["packages/cli/src/cli.ts"],
  "node:sqlite": ["packages/sql/src/node.ts"],
  "bun:sqlite": ["packages/sql/src/bun.ts"],
  "cloudflare:workers": ["packages/worker/src/person.ts"],
}

const root = path.join(import.meta.dir, "..")
const specifiers = (source: string) =>
  [...source.matchAll(/(?:^|\n)\s*(?:import|export)[^;]*?from\s+"([^"]+)"/g)].map((x) => x[1]!)
const packageOf = (specifier: string) =>
  specifier.startsWith("@") ? specifier.split("/").slice(0, 2).join("/") : specifier.split("/")[0]!

describe("package boundaries", () => {
  for (const [name, deps] of Object.entries(allowed)) {
    test(`${name} imports only what it may, and declares it`, async () => {
      const manifest = await Bun.file(path.join(root, "packages", name, "package.json")).json()
      const declared = Object.keys(manifest.dependencies ?? {})
      const files = [...new Glob(`packages/${name}/src/**/*.ts`).scanSync(root)]
      expect(files.length).toBeGreaterThan(0)
      for (const file of files) {
        const imports = specifiers(await Bun.file(path.join(root, file)).text()).filter((x) => !x.startsWith("."))
        for (const specifier of imports) {
          const where = confined[specifier]
          if (where) {
            expect({ file, specifier, allowed: where.includes(file) }).toEqual({ file, specifier, allowed: true })
            continue
          }
          const dep = packageOf(specifier)
          expect({ file, dep, allowed: deps.includes(dep) }).toEqual({ file, dep, allowed: true })
          expect({ file, dep, declared: declared.includes(dep) }).toEqual({ file, dep, declared: true })
        }
      }
    })
  }

  test("every package is covered", async () => {
    const found = [...new Glob("packages/*/package.json").scanSync(root)].map((x) => x.split("/")[1]).toSorted()
    expect(found).toEqual(Object.keys(allowed).toSorted())
  })
})
