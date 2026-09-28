import { expect, test } from "bun:test"
import { Skills } from "../src/index"

test("skill manifests and served resources describe the exact canonical files", async () => {
  const entries = Skills.list()
  expect(entries.length).toBe(2)
  for (const entry of entries) {
    expect(entry.uri).toBe(`skill://${entry.frontmatter.name}/SKILL.md`)
    const root = new URL(`../skills/${entry.frontmatter.name}/`, import.meta.url).pathname
    const files = [...new Bun.Glob("**/*").scanSync({ cwd: root, onlyFiles: true })].sort()
    expect(entry.resources.map((r) => r.uri)).toEqual(files.map((f) => `skill://${entry.frontmatter.name}/${f}`))
    for (const resource of entry.resources) {
      const served = Skills.read(resource.uri)!
      const bytes = new TextEncoder().encode(served.text)
      expect(resource.digest).toBe(`sha256:${new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)).toHex()}`)
      expect(resource.size).toBe(bytes.byteLength)
      const filename = resource.uri.slice(`skill://${entry.frontmatter.name}/`.length)
      expect(served.text).toBe(await Bun.file(root + filename).text())
    }
    const frontmatter = Skills.read(entry.uri)!.text.match(/^---\n([\s\S]+?)\n---\n/)![1]!
    expect(Bun.YAML.parse(frontmatter)).toEqual(entry.frontmatter)
  }
  entries[0]!.frontmatter.name = "tampered"
  expect(Skills.list()[0]!.frontmatter.name).not.toBe("tampered")
  expect(Skills.read("file:///etc/passwd")).toBeUndefined()
})
