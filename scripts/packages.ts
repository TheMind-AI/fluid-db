// @ref LLP 0023#packaging — private module boundaries do not require separate npm releases
// Dependency order for declaration generation. Only the combined FluidDB artifact is published.
export const modules = [
  "schema",
  "core",
  "eval",
  "providers",
  "sql",
  "client",
  "postgres",
  "firestore",
  "feedback",
  "skills",
  "mcp",
  "cli",
  "fluiddb",
] as const

export const entrypoints = {
  ".": ["fluiddb", "."],
  "./schema": ["schema", "."],
  "./local": ["core", "./local"],
  "./testing": ["core", "./testing"],
  "./conformance": ["core", "./conformance"],
  "./records": ["core", "./records"],
  "./sqlite": ["sql", "."],
  "./sqlite/node": ["sql", "./node"],
  "./sqlite/bun": ["sql", "./bun"],
  "./postgres": ["postgres", "."],
  "./firestore": ["firestore", "."],
  "./firestore/rest": ["firestore", "./rest"],
  "./client": ["client", "."],
  "./eval": ["eval", "."],
  "./feedback": ["feedback", "."],
  "./skills": ["skills", "."],
  "./cli": ["cli", "."],
  "./cli/run": ["fluiddb", "./cli"],
  "./mcp": ["mcp", "."],
  "./mcp/http": ["mcp", "./http"],
  "./mcp/cli": ["mcp", "./cli"],
} as const

// These adapters accept an existing host client; the portable engine needs none of these drivers.
export const optionalPeers = ["pg", "@types/pg", "@google-cloud/firestore"] as const
