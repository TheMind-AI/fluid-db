import { catalog } from "./generated"

export interface Entry {
  uri: string
  frontmatter: { name: string; description: string; [key: string]: unknown }
  resources: { uri: string; digest: string; size: number }[]
}
export interface File {
  uri: string
  mimeType: string
  text: string
}

// @ref LLP 0023.002#skills — all surfaces use the same bytes and complete, static manifests
export function list(): Entry[] {
  return structuredClone(catalog.map(({ entry }) => entry))
}
export function get(nameOrUri: string): Entry | undefined {
  const item = catalog.find(({ entry }) => entry.frontmatter.name === nameOrUri || entry.uri === nameOrUri)
  return item ? structuredClone(item.entry) : undefined
}
export function read(nameOrUri: string): File | undefined {
  const uri = get(nameOrUri)?.uri ?? nameOrUri
  const file = catalog.flatMap(({ files }) => files).find((file) => file.uri === uri)
  return file ? structuredClone(file) : undefined
}

export * as Skills from "./index"
