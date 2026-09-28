#!/usr/bin/env node
import process from "node:process"
import { readFile, stat } from "node:fs/promises"
import { FeedbackError } from "@fluiddb/feedback"
import { run } from "./index"

async function readInput(path: string): Promise<string> {
  if (path !== "-") {
    if ((await stat(path)).size > 65_536) throw new Error("Input too large")
    return readFile(path, "utf8")
  }
  let input = ""
  process.stdin.setEncoding("utf8")
  for await (const part of process.stdin) {
    input += part
    if (Buffer.byteLength(input) > 65_536) throw new Error("Input too large")
  }
  return input
}

try {
  process.stdout.write(await run(process.argv.slice(2), { readInput }))
} catch (error) {
  process.stderr.write(
    JSON.stringify({
      error:
        error instanceof FeedbackError
          ? {
              message: error.message,
              thread: error.thread,
              idempotencyKey: error.idempotencyKey,
              status: error.status,
            }
          : {
              message:
                "Invalid command or feedback report. Use fluiddb --help; check required fields and input limits.",
            },
    }) + "\n",
  )
  process.exitCode = 1
}
