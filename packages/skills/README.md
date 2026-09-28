# @fluiddb/fluiddb/skills

Portable skills for using FluidDB memory and reporting feedback. Canonical `skills/*/SKILL.md` files ship in the
package; their generated catalog works without filesystem, network or runtime-specific dependencies.

```ts
import { Skills } from "@fluiddb/fluiddb/skills"

Skills.list() // MCP-compatible entries: full frontmatter, resource URIs, SHA-256 digests and byte sizes
Skills.get("fluiddb-memory") // also accepts the full skill URI
Skills.read("skill://fluiddb-memory/SKILL.md") // { uri, mimeType, text }, or undefined
```

`@fluiddb/fluiddb/mcp` serves these through the official Skills extension, ordinary resources and `fluiddb_read_skill`.
`@fluiddb/fluiddb/cli` provides `fluiddb skills list` and `fluiddb skills read <name>`. The Worker serves their canonical
markdown at `/skills/<name>/SKILL.md` and advertises them at `/llms.txt`.

To install locally, copy `skills/fluiddb-memory` and `skills/fluiddb-feedback` into your agent's skill directory,
or use CLI output as each directory's `SKILL.md`. Installing a skill grants no tool or data-sharing permission.

When editing the canonical files in this repository, run `bun run skills:generate`; `bun run skills:check` checks
for drift. The catalog includes every markdown file in each skill and errors on unsupported file types. The MCP
manifest describes the exact served bytes, not a summary or a dynamically generated skill.
