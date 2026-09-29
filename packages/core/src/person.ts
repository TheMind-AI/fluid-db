import { Api, Id, type Dossier, type Turn } from "@fluiddb/schema"
import type { Memory } from "./memory"
import type { Call } from "./port"
import { Render } from "./render"

// The product authorizes the person once. Neither an agent tool nor an individual SDK call chooses another person.
export interface Service {
  ingest(session: string, turns: Turn.Info[], call?: Call): Promise<Api.Ingested>
  recall(ask: Api.Ask, call?: Call): Promise<Api.Answer>
  dossier(call?: Call): Promise<Dossier.Info | undefined>
  remember(input: Api.Remember, call?: Call): Promise<Api.Remembered>
  rememberMany(input: Api.RememberMany, call?: Call): Promise<Api.Remembered[]>
  inspect(query?: Api.Inspect, call?: Call): Promise<Api.Page>
  evidence(query: Api.Evidence, call?: Call): Promise<Api.Sources>
  source(input: Api.Source, call?: Call): Promise<Api.SourcePage>
  previewForget(input: Api.Forget, call?: Call): Promise<Api.ForgetPreview>
  forget(input: Api.Forget, call?: Call): Promise<Api.Forgotten>
  accept?(session: string, turns: Turn.Info[], call?: Call): Promise<Api.Accepted>
  status?(call?: Call): Promise<Api.Status>
  retry?(call?: Call): Promise<Api.Accepted>
}

export function bind(memory: Memory.Service, id: string): Service {
  const person = Id.Info.parse(id)
  return {
    ingest: (session, turns, call) =>
      memory.ingest({ person, session, turns: Api.Ingest.parse({ turns }).turns }, call),
    recall: async (input, call) => {
      const ask = Api.Ask.parse(input)
      const recall = await memory.recall({ person, conversation: ask.conversation }, call)
      return { recall, ...(ask.render ? { prompt: Render.render(recall, { name: ask.name }) } : {}) }
    },
    dossier: (call) => {
      call?.signal?.throwIfAborted()
      return memory.dossier(person)
    },
    remember: (input, call) => memory.remember({ ...input, person }, call),
    rememberMany: (memories, call) => memory.rememberMany({ person, memories }, call),
    inspect: (query, call) => memory.inspect(person, query, call),
    evidence: (query, call) => memory.evidence(person, query, call),
    source: (input, call) => memory.source(person, input, call),
    previewForget: (input, call) => memory.previewForget({ ...input, person }, call),
    forget: (input, call) => memory.forget({ ...input, person }, call),
  }
}

export * as Person from "./person"
