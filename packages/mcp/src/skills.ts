import { ProtocolError, INVALID_PARAMS, type McpServer } from "@modelcontextprotocol/server"
import { Skills } from "@fluiddb/skills"
import { z } from "zod"

// @ref LLP 0023.002#skills — SEP-2640 manifest, exact resource bytes, and legacy resource access
export function registerSkills(server: McpServer) {
  server.server.registerCapabilities({ extensions: { "io.modelcontextprotocol/skills": {} } })
  const cache = { resultType: "complete" as const, ttlMs: 300_000, cacheScope: "public" as const }
  server.server.setRequestHandler("skills/list", { params: z.object({ cursor: z.string().optional() }) }, (params) => {
    if (params.cursor !== undefined)
      throw new ProtocolError(INVALID_PARAMS, "Invalid skill cursor; this catalog fits on one page")
    return { ...cache, skills: Skills.list() }
  })
  server.server.setRequestHandler("skills/get", { params: z.object({ uri: z.string() }) }, ({ uri }) => {
    const skill = Skills.list().find((skill) => skill.uri === uri)
    if (!skill) throw new ProtocolError(INVALID_PARAMS, "Unknown skill URI")
    return { ...cache, skill }
  })
  for (const skill of Skills.list()) {
    for (const resource of skill.resources) {
      const file = Skills.read(resource.uri)!
      server.registerResource(
        skill.frontmatter.name,
        file.uri,
        { description: skill.frontmatter.description, mimeType: file.mimeType, size: resource.size },
        async () => ({ ...cache, contents: [{ uri: file.uri, mimeType: file.mimeType, text: file.text }] }),
      )
    }
  }
}
