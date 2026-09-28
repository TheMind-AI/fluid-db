import { expect, test } from "bun:test"
import { Feedback } from "@fluiddb/feedback"
import { run } from "../src/index"

function io(input = "") {
  const reports: Feedback.Report[] = []
  const feedback: Feedback.Service = {
    submit: async (report) => {
      reports.push(report)
      return {
        id: "event",
        thread: "thread-test",
        idempotencyKey: "retry-test",
        guidance: "Synthetic guidance",
        ask: { id: "question", prompt: "Observed outcome?", command: "untrusted answer command" },
      }
    },
  }
  return { reports, feedback, readInput: async () => input }
}

test("CLI structured report, resume and question reach the shared SDK; all reply data is printed", async () => {
  const output = io()
  const reply = await run(
    [
      "feedback",
      "--category",
      "mcp",
      "--subject",
      "fluiddb_recall",
      "--task",
      "Synthetic task",
      "--expected",
      "New fact",
      "--actual",
      "Old fact",
      "--attempts",
      "2",
      "--resume",
      "thread-test",
      "--question",
      "question",
      "--idempotency-key",
      "retry-test",
      "Synthetic report",
    ],
    output,
  )
  expect(output.reports[0]).toMatchObject({
    category: "mcp",
    feedback: "Synthetic report",
    resume: "thread-test",
    question: "question",
    idempotencyKey: "retry-test",
    eval: { task: "Synthetic task", expected: "New fact", actual: "Old fact", attempts: 2 },
  })
  expect(JSON.parse(reply)).toMatchObject({
    thread: "thread-test",
    guidance: "Synthetic guidance",
    ask: { id: "question" },
  })
})

test("CLI JSON input carries memory-case descriptors; incomplete/mixed reports never send", async () => {
  const report: Feedback.Report = {
    feedback: "Synthetic long-input case",
    memoryCase: { operation: "ingest", challenge: "long-context", turns: 20 },
  }
  const output = io(JSON.stringify(report))
  await run(["feedback", "--input", "-"], output)
  expect(output.reports[0]).toEqual({ ...report, category: "cli" })
  for (const args of [
    ["feedback", "--input", "-", "--category", "cli"],
    ["feedback", "--task", "Incomplete", "summary"],
    ["feedback", "--attempts", "1.5", "summary"],
    ["feedback", "--unknown", "summary"],
    ["feedback", "one", "two"],
    ["feedback"],
  ])
    await expect(run(args, output)).rejects.toBeDefined()
  expect(output.reports).toHaveLength(1)
})

test("CLI skills work offline and reject arbitrary files", async () => {
  const output = io()
  expect(JSON.parse(await run(["skills", "list"], output))).toHaveLength(2)
  expect(await run(["skills", "read", "fluiddb-memory"], output)).toContain("fluiddb_recall")
  await expect(run(["skills", "read", "../../.env"], output)).rejects.toBeDefined()
  expect(output.reports).toHaveLength(0)
})

test("executable skill read and invalid stdin report have correct stdout, stderr and exit status", async () => {
  const cli = new URL("../src/cli.ts", import.meta.url).pathname
  const good = Bun.spawn([process.execPath, cli, "skills", "read", "fluiddb-feedback"], {
    stdout: "pipe",
    stderr: "pipe",
  })
  expect(await new Response(good.stdout).text()).toContain("HiveNet")
  expect(await good.exited).toBe(0)
  const bad = Bun.spawn([process.execPath, cli, "feedback", "--input", "-"], {
    stdin: new Blob(['{"private":"secret-should-not-echo"}']),
    stdout: "pipe",
    stderr: "pipe",
  })
  expect(await new Response(bad.stdout).text()).toBe("")
  const error = await new Response(bad.stderr).text()
  expect(error).toContain("Invalid command")
  expect(error).not.toContain("secret-should-not-echo")
  expect(await bad.exited).toBe(1)
})
