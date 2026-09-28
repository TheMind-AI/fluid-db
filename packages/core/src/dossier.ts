import type { Window } from "@fluiddb/schema"
import type { Call, LanguageModel } from "./port"
import { Prompt } from "./prompt"

// @ref LLP 0020#part-b-creation — kept current in batches of windows: as good as one full read (-1.0, LLP 0020.000)
export async function update(
  model: LanguageModel,
  input: Prompt.Voice & { current?: string; windows: Window.Info[] },
  call?: Call,
): Promise<string> {
  const text = input.windows.map((x) => `[${x.at.slice(0, 16).replace("T", " ")}] ${x.text}`).join("\n\n")
  return (await model.text(Prompt.dossier({ ...input, text }), call)).trim()
}

export * as Dossiers from "./dossier"
