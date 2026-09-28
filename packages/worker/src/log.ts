import type { Logger } from "@fluiddb/core"

// Structured events to the Worker's logs: counts, timings and error kinds, never people's ids or what they said.
export const log: Logger = {
  event: (name, data) => console.log(JSON.stringify({ event: name, ...data })),
}
