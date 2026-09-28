import type { Turn } from "@fluiddb/schema"

export interface Draft {
  at: string
  turns: string[]
  sources: string[]
  text: string
}

// The last question of an assistant message: what the person's next message answers.
export function question(text: string, limit = 160) {
  const parts = text.trim().split(/(?<=[.?!])\s+/)
  const found = parts.findLast((x) => x.endsWith("?")) ?? parts.at(-1) ?? ""
  return found.slice(-limit)
}

// @ref LLP 0017#data — windows of up to `size` of the person's consecutive messages, each with the question before it
export function windows(input: {
  turns: Turn.Info[]
  skip: Set<string>
  size: number
  assistant: string
  maxChars?: number
}): Draft[] {
  const max = input.maxChars ?? 12_000
  if (!Number.isSafeInteger(max) || max < 1024) throw new RangeError("window budget must be at least 1024 characters")
  const turns = input.turns.toSorted((a, b) => a.at.localeCompare(b.at))
  const out: Draft[] = []
  const state = {
    asked: "",
    questionId: "",
    lines: [] as string[],
    ids: [] as string[],
    sources: [] as string[],
    start: "",
  }
  const close = () => {
    if (!state.lines.length) return
    out.push({
      at: state.start,
      turns: state.ids,
      sources: state.sources,
      text: `From a conversation with ${input.assistant} on ${state.start.slice(0, 10)}:\n${state.lines.join("\n")}`,
    })
    Object.assign(state, { lines: [], ids: [], sources: [], start: "" })
  }
  const header = (at: string) => `From a conversation with ${input.assistant} on ${at.slice(0, 10)}:\n`
  for (const turn of turns) {
    if (turn.role === "assistant") {
      state.asked = question(turn.text)
      state.questionId = turn.id
      continue
    }
    if (input.skip.has(turn.id)) {
      state.asked = ""
      state.questionId = ""
      continue
    }
    const asked = state.asked ? `(${input.assistant} asked: ${state.asked}) ` : ""
    const prefix = `[${turn.at.slice(11, 16)}] ${asked}`
    const room = max - header(turn.at).length - prefix.length
    if (room < 2) throw new RangeError("assistant context exceeds the window budget")
    // @ref LLP 0023.001#complete-input — cover every character; all chunks share source provenance and commit together
    let offset = 0
    do {
      let end = Math.min(turn.text.length, offset + room)
      // Do not split a UTF-16 surrogate pair between chunks.
      if (end < turn.text.length && /[\uD800-\uDBFF]/.test(turn.text[end - 1]!)) end--
      const line = prefix + turn.text.slice(offset, end)
      if (state.lines.length && header(state.start).length + state.lines.join("\n").length + 1 + line.length > max)
        close()
      state.start ||= turn.at
      state.lines.push(line)
      state.ids.push(turn.id)
      state.sources.push(...(state.questionId ? [state.questionId] : []), turn.id)
      offset = end
      if (offset < turn.text.length || state.lines.length >= input.size) close()
    } while (offset < turn.text.length)
    state.asked = ""
    state.questionId = ""
  }
  close()
  return out
}

// @ref LLP 0022#design — what the memory is asked at a turn: the question before the person's last message, then it
export function moment(turns: Turn.Info[], limit = 3_000) {
  const sorted = turns.toSorted((a, b) => a.at.localeCompare(b.at))
  const last = sorted.findLastIndex((x) => x.role === "person")
  if (last === -1) return ""
  const before = sorted[last - 1]
  const asked = before?.role === "assistant" ? question(before.text) : ""
  return [asked, sorted[last]!.text].filter(Boolean).join(" ").slice(0, limit)
}

export * as Split from "./split"
