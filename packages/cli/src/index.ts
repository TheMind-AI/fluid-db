import { parseArgs } from "node:util"
import { Feedback } from "@fluiddb/feedback"
import { Skills } from "@fluiddb/skills"

export const help = `fluiddb feedback [options] "specific observation"
  --category <tool|skill|prompt|docs|mcp|cli|api|model|ux|other> (default: cli)
  --subject <exact item>
  --task <reproducible goal> --expected <correct result> --actual <observed result>
  --mistake <wrong step> --attempts <count>
  --resume <thread> --question <ask ID> --idempotency-key <retry key>
  --input <report.json|->   Complete report as JSON; cannot mix with report flags.
  --json                  Receipts always use JSON, including guidance and asks.
fluiddb skills list
fluiddb skills read <fluiddb-memory|fluiddb-feedback>

Feedback is sent to the FluidDB team through HiveNet. No environment or memory
context is collected. Use synthetic reproductions; never send private transcripts
or secrets. Report fields are not automatically redacted. No automatic retries.
Use hivenet's CLI for explicit, reviewed attachments. MCP: fluiddb-mcp --help.
`

export interface IO {
  readInput(path: string): Promise<string>
  feedback?: Feedback.Service
}

export async function run(args: string[], io: IO): Promise<string> {
  const [command, ...rest] = args
  if (!command || command === "--help" || (rest.length === 1 && rest[0] === "--help")) return help
  if (command === "skills") {
    if (rest.length === 1 && rest[0] === "list") return JSON.stringify(Skills.list(), null, 2) + "\n"
    if (rest.length === 2 && rest[0] === "read") {
      const file = Skills.read(rest[1]!)
      if (!file) throw new Error("Unknown skill; use fluiddb skills list")
      return file.text
    }
    throw new Error("Use fluiddb skills list or fluiddb skills read <name>")
  }
  if (command !== "feedback") throw new Error("Unknown command; use fluiddb --help")
  const { values, positionals } = parseArgs({
    args: rest,
    strict: true,
    allowPositionals: true,
    options: {
      category: { type: "string" },
      subject: { type: "string" },
      task: { type: "string" },
      expected: { type: "string" },
      actual: { type: "string" },
      mistake: { type: "string" },
      attempts: { type: "string" },
      resume: { type: "string" },
      question: { type: "string" },
      "idempotency-key": { type: "string" },
      input: { type: "string" },
      json: { type: "boolean" },
    },
  })
  let input: unknown
  if (values.input) {
    if (positionals.length || Object.keys(values).some((k) => k !== "input" && k !== "json"))
      throw new Error("--input takes a complete report; do not combine it with report flags or a message")
    input = JSON.parse(await io.readInput(values.input))
  } else {
    if (positionals.length !== 1) throw new Error("Pass one quoted feedback message, or --input report.json")
    const hasEval = [values.task, values.expected, values.actual, values.mistake, values.attempts].some(
      (v) => v !== undefined,
    )
    input = {
      feedback: positionals[0],
      category: values.category ?? "cli",
      subject: values.subject,
      resume: values.resume,
      question: values.question,
      idempotencyKey: values["idempotency-key"],
      ...(hasEval
        ? {
            eval: {
              task: values.task,
              expected: values.expected,
              actual: values.actual,
              mistake: values.mistake,
              attempts: values.attempts === undefined ? undefined : Number(values.attempts),
            },
          }
        : {}),
    }
  }
  const report = Feedback.Report.parse(input)
  const service = io.feedback ?? Feedback.create({ clientName: "fluiddb-cli" })
  return JSON.stringify(await service.submit({ ...report, category: report.category ?? "cli" }), null, 2) + "\n"
}
