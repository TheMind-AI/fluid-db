#!/usr/bin/env node
import process from "node:process"
import { serveStdio } from "@modelcontextprotocol/server/stdio"
import { Client } from "@fluiddb/client"
import { FluidMcp } from "./server"

if (process.argv.includes("--help")) {
  process.stdout.write(
    "fluiddb-mcp [--write] [--forget] [--feedback]\nRequired environment: FLUIDDB_URL, FLUIDDB_TOKEN, FLUIDDB_PERSON\nRead-only by default. --write enables ingest/save/retry; --forget separately enables deletion.\n--feedback enables explicit reports to the FluidDB team through HiveNet. No automatic context collection.\nMemory/feedback skills are included via MCP skills, resources and fluiddb_read_skill.\n",
  )
} else {
  try {
    if (process.argv.slice(2).some((arg) => !["--write", "--forget", "--feedback"].includes(arg)))
      throw new Error("Unknown option; use --help")
    const required = (key: string) => {
      const value = process.env[key]
      if (!value?.trim()) throw new Error(`${key} is required`)
      return value
    }
    const memory = Client.create({ url: required("FLUIDDB_URL"), token: required("FLUIDDB_TOKEN") }).person(
      required("FLUIDDB_PERSON"),
    )
    const handle = serveStdio(
      () =>
        FluidMcp.create({
          memory,
          feedback: process.argv.includes("--feedback"),
          permissions: {
            write: process.argv.includes("--write"),
            forget: process.argv.includes("--forget"),
          },
        }),
      { onerror: () => process.stderr.write("FluidDB MCP transport error\n") },
    )
    const close = () => {
      void handle.close().finally(() => process.exit(0))
    }
    process.once("SIGINT", close)
    process.once("SIGTERM", close)
  } catch {
    process.stderr.write("FluidDB MCP could not start. Check FLUIDDB_URL, FLUIDDB_TOKEN, FLUIDDB_PERSON and --help.\n")
    process.exitCode = 1
  }
}
