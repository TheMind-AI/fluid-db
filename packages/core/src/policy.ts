import { ProcessorError } from "./error"
import type { Provider } from "./port"

// A deployment lists the processors a person's data may go to; a provider that reaches any other is refused before
// it is used (LLP 0018#processors).
export function check(allowed: readonly string[] | undefined, providers: Provider[]) {
  if (!allowed) return
  for (const provider of providers) {
    const bad = provider.processors.find((x) => !allowed.includes(x))
    if (bad) throw new ProcessorError(provider.id, bad, [...allowed])
  }
}

export * as Policy from "./policy"
