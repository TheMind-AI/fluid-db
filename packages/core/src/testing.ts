import type { z } from "zod"
import type { Choice, Clock, Decider, Embedder, Ids, LanguageModel } from "./port"
import { Vector } from "./vector"

// Deterministic fakes for tests: words stand in for meaning, overlap for judgment.

export function words(text: string) {
  return new Set(
    text
      .toLowerCase()
      .split(/[^\p{L}\p{N}]+/u)
      .filter((x) => x.length >= 3)
      .map((x) => x.replace(/s$/, "")),
  )
}

// The share of the shorter text's words that the other has.
export function overlap(a: string, b: string) {
  const x = words(a)
  const y = words(b)
  const shared = [...x].filter((w) => y.has(w)).length
  return shared / Math.max(1, Math.min(x.size, y.size))
}

// A hashing bag-of-words embedder: texts that share words point the same way.
export function embedder(dimensions = 256): Embedder {
  const hash = (word: string) => [...word].reduce((h, c) => Math.imul(h ^ c.charCodeAt(0), 16777619) >>> 0, 2166136261)
  return {
    id: "test:embedder",
    processors: ["test"],
    embed: async (texts) =>
      texts.map((text) => {
        const vector = new Float32Array(dimensions)
        for (const word of words(text)) vector[hash(word) % dimensions]! += 1
        return Vector.unit(vector)
      }),
  }
}

export type Handler = (prompt: string) => unknown

const after = (prompt: string, marker: string) => prompt.split(marker)[1] ?? ""

// A model that answers the memory's prompts by word overlap: one statement per message of a window ("They said:
// ..."), the same fact when half the words match, a ranking by overlap, and a dossier listing every message shown.
// A handler answers first; returning undefined falls through to these.
export function model(handler?: Handler): LanguageModel {
  const fallback = (prompt: string): unknown => {
    if (prompt.includes("What would a good therapist remember")) {
      const lines = after(prompt, "MESSAGES:\n").split("\n").slice(1)
      return {
        items: lines
          .map((line) => line.replace(/^\[\d\d:\d\d\] (\([^)]*\) )?/, "").trim())
          .filter(Boolean)
          .map((text) => ({ text: `They said: ${text}`, kind: "life" })),
      }
    }
    if (prompt.startsWith("A new statement about a person")) {
      const text = /NEW: (.*)/.exec(prompt)?.[1] ?? ""
      const earlier = after(prompt, "EARLIER:\n")
        .split("\n")
        .map((x) => x.replace(/^\d+\. /, ""))
      const best = earlier.map((x, i) => ({ n: i + 1, p: overlap(text, x) })).toSorted((a, b) => b.p - a.p)[0]
      return best && best.p >= 0.5 ? { relation: "same", n: best.n } : { relation: "new", n: 0 }
    }
    if (prompt.includes("Rank the excerpts")) {
      const moment = /MOMENT OR QUESTION: (.*)/.exec(prompt)?.[1] ?? ""
      const top = Number(/return the numbers of the (\d+) most useful/.exec(prompt)?.[1] ?? 1)
      const texts = after(prompt, "EXCERPTS:\n").split(/\n\n(?=\d+\. )/)
      const ranked = texts.map((x, i) => ({ n: i + 1, p: overlap(moment, x) })).toSorted((a, b) => b.p - a.p)
      return { top: ranked.slice(0, top).map((x) => x.n) }
    }
    const current = /CURRENT DOSSIER:\n([\s\S]*?)\n\nWHAT THEY SAID SINCE/.exec(prompt)?.[1] ?? ""
    const since = after(prompt, "the assistant's question they answer):\n")
    const lines = since.split("\n").filter((x) => /^\[\d\d:\d\d\]/.test(x))
    const kept = current.startsWith("(empty") ? [] : current.split("\n").filter(Boolean)
    return [...kept, ...lines.map((x) => `- ${x}`)].join("\n")
  }
  const run = (prompt: string) => handler?.(prompt) ?? fallback(prompt)
  return {
    id: "test:model",
    processors: ["test"],
    text: async (prompt) => String(run(prompt)),
    object: async <T>(prompt: string, schema: z.ZodType<T>) => schema.parse(run(prompt)),
  }
}

// A decider that judges by word overlap. yes(): each excerpt's overlap with the moment. choose(): the option whose
// text in the state overlaps most with the message, if at least `threshold`, else "none".
export function decider(threshold = 0.5): Decider {
  const text = (state: Record<string, unknown>, key: string) => {
    const group = Object.values(state).find((x) => x && typeof x === "object" && key in x) as
      Record<string, string> | undefined
    return group?.[key] ?? ""
  }
  return {
    id: "test:decider",
    processors: ["test"],
    yes: async (state, questions) =>
      Object.fromEntries(
        Object.entries(questions).map(([key, question]) => {
          const ref = /`excerpts\.(\w+)`/.exec(question)?.[1] ?? ""
          return [key, overlap(String(state.moment ?? ""), text(state, ref))]
        }),
      ),
    choose: async (state, _question, options): Promise<Choice> => {
      const subject = String(state.message ?? "")
      const scored = Object.keys(options).map((key) => ({ key, p: overlap(subject, text(state, key)) }))
      const best = scored.toSorted((a, b) => b.p - a.p)[0]
      const choice = best && best.p >= threshold ? best.key : "none"
      const probabilities = Object.fromEntries(
        scored.map((x) => [x.key, x.key === choice ? Math.max(x.p, 0.9) : x.p * 0.1]),
      )
      return { choice, probabilities }
    },
  }
}

export function clock(start = "2026-01-01T00:00:00.000Z"): Clock {
  const state = { at: Date.parse(start) }
  return {
    now: () => {
      state.at += 1000
      return new Date(state.at)
    },
  }
}

// Ids that sort in the order they were made: `prefix_0001`, `prefix_0002`, ...
export function ids(): Ids {
  const count = { n: 0 }
  return { next: (prefix) => `${prefix}_${String(++count.n).padStart(4, "0")}` }
}

export * as Testing from "./testing"
