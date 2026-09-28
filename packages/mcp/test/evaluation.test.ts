import { expect, test } from "bun:test"
import { Client, InMemoryTransport } from "@modelcontextprotocol/client"
import { Api, Statement, type Group } from "@fluiddb/schema"
import { FluidMcp } from "../src/server"
import { fixture } from "../evals/fixture"

test("the ten read-only evaluation answers are supported by paginated MCP records and source history", async () => {
  const server = FluidMcp.create({ memory: await fixture() })
  const client = new Client({ name: "evaluation-verifier", version: "1" })
  const [a, b] = InMemoryTransport.createLinkedPair()
  await server.connect(b)
  await client.connect(a)
  try {
    const statements: Statement.Info[] = [],
      groups: Group.Info[] = []
    let cursor: string | undefined
    do {
      const page = Api.Page.parse(
        (await client.callTool({ name: "fluiddb_inspect", arguments: { limit: 2, ...(cursor ? { cursor } : {}) } }))
          .structuredContent,
      )
      const rows = page.items.map((x) => Statement.Info.parse(x))
      const sources = Api.Sources.parse(
        (await client.callTool({ name: "fluiddb_evidence", arguments: { statements: rows.map((x) => x.id) } }))
          .structuredContent,
      )
      expect(sources.windows.every((x) => x.origin === "explicit")).toBe(true)
      statements.push(...sources.statements)
      groups.push(...sources.groups)
      cursor = page.nextCursor
    } while (cursor)
    const find = (id: string) => statements.find((x) => x.save?.id === id)!
    const history = (id: string) => groups.find((x) => x.id === find(id).group)!
    expect(history("relative-old").until).toBe("2026-02-01T00:00:00.000Z")
    expect(history("relative-new").replaces).toContain(find("relative-old").group)
    expect(history("cancel").replaces).toContain(find("visit-plan").group)
    const start = find("work").text.match(/(\d\d):(\d\d)/)!
    const commute = Number(find("commute").text.match(/(\d+) minutes/)![1])
    const time = (minutes: number) =>
      `${Math.floor(minutes / 60)
        .toString()
        .padStart(2, "0")}:${(minutes % 60).toString().padStart(2, "0")}`
    const departure = Number(start[1]) * 60 + Number(start[2]) - commute
    const answers = [
      find("relative-new").text.match(/to (\w+)/)![1],
      "Sunday",
      find("teacher").text.split(" ")[0],
      "Tuesday",
      time(departure),
      find("language").text.match(/in (\w+)/)![1],
      find("style-new").text.includes("short") ? "short" : "detailed",
      history("style-old").until!.slice(0, 10),
      find("visit-plan").text.match(/a (\w+) bowl/)![1],
      time(departure - 5),
    ]
    expect(find("visits").text).toContain("Sundays")
    expect(find("class").text).toContain("Tuesdays")
    expect(find("exercise").text).toContain("five-minute")
    const xml = await Bun.file(new URL("../evals/questions.xml", import.meta.url)).text()
    expect(answers).toEqual([...xml.matchAll(/<answer>(.*?)<\/answer>/g)].map((x) => x[1]))
  } finally {
    await client.close()
    await server.close()
  }
})
