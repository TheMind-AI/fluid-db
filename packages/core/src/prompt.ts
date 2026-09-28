// The prompts the lab measured (deprecated/python/lab/memory/stores.py, deprecated/python/lab/bench/replay.py), word for word. `assistant` is the
// product's name in transcripts ("Mind asked: ..."); `role` is what it is to the person ("therapy assistant").
export interface Voice {
  assistant: string
  role: string
}

// @ref LLP 0019#the-new-parts — statements: what a good therapist would remember, one lasting thing per line
export function statements(input: Voice & { date: string; text: string }) {
  return `These are one person's messages to their ${input.role} from a conversation on ${input.date}. ("${input.assistant} asked" is the assistant's question they are answering.)

What would a good therapist remember about this person from them? Write each lasting thing as its own short line, in the third person ("They ..."): who is in their life and how things stand with them, their work or studies, where and how they live, their health, what they struggle with again and again, what they tried and how it went, what they plan or intend, and how they like to be talked to. Leave out small talk and how they feel at just this moment.

MESSAGES:
${input.text}`
}

// @ref LLP 0020#part-b-creation — a new statement against its closest earlier ones: the same fact, a change, or new
export function link(input: { text: string; earlier: string[] }) {
  return `A new statement about a person, and earlier statements about them. Is the new statement the same fact as one of the earlier ones (possibly in other words), a change of one of them (the same thing, but different now), or something new?

NEW: ${input.text}

EARLIER:
${input.earlier.map((x, i) => `${i + 1}. ${x}`).join("\n")}`
}

export function dossier(input: Voice & { current?: string; text: string }) {
  return `You keep a ${input.role}'s dossier on a person, read before every conversation so the assistant never has to ask again for something the person already said. Below are the current dossier and what the person said since.

Return the complete updated dossier: add what is new, update what changed (what it was until when, and what it is now), update the status of plans, keep everything still true, and leave out passing moods. Sections: people in their life; work and studies; life and circumstances; health; what keeps coming back; practices tried and how each went; plans and intentions with status and date; how they like to be talked to; key events with dates. Every item specific, with dates and how often it came up. Up to about 2,500 words.

CURRENT DOSSIER:
${input.current || "(empty: nothing is known yet)"}

WHAT THEY SAID SINCE ("${input.assistant} asked" is the assistant's question they answer):
${input.text}`
}

// @ref LLP 0020#part-d-jev-picks-the-search — Jev picks the evidence it can see, one closed question per candidate
export const pick = "Does `excerpts.{key}` hold something the person said that is useful to recall at `moment`?"

// @ref LLP 0021#part-a-a-better-picker — P3: the model ranks the candidates as a list
export function rank(input: { role: string; moment: string; texts: string[]; top: number }) {
  return `Below are ${input.texts.length} excerpts from a person's memory, and a moment in their current conversation with their ${input.role} (or a question about them). Rank the excerpts by how useful each is to recall at this moment, and return the numbers of the ${input.top} most useful, the most useful first.

MOMENT OR QUESTION: ${input.moment}

EXCERPTS:
${input.texts.map((x, i) => `${i + 1}. ${x}`).join("\n\n")}`
}

// @ref LLP 0022#design — the re-telling detector: one closed choice over the closest statements, or none
export const detect =
  "Is the person, in `message`, telling the assistant something it already knows from earlier conversations? If so, " +
  "which statement in `memory` is it?"

// @ref LLP 0021#amendment-telling-the-assistant-to-use-its-memory — without it the memory went unused (9.5% → 45.8%)
export const directive =
  "Before you reply, check what you remember for anything the person told you before that relates to this moment. " +
  'If there is something, say so explicitly and specifically ("you mentioned in August that ..."), so they don\'t ' +
  "have to repeat themselves, and connect it to what they are saying now. Never claim to remember anything that " +
  "isn't here or in this conversation."

export * as Prompt from "./prompt"
