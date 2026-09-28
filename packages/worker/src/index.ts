import { App } from "./app"
import { Config } from "./config"
import { Host } from "./host"
import { log } from "./log"
import type { Person } from "./person"

export { Person } from "./person"

export interface Env extends Config.Vars {
  PERSON: DurableObjectNamespace<Person>
}

export default {
  async fetch(request, env, ctx) {
    const config = (() => {
      try {
        return Config.load(env)
      } catch (error) {
        log.event("config.invalid", { error: error instanceof Error ? error.name : "Error" })
        return undefined
      }
    })()
    if (!config) {
      const failure = { name: "ConfigError", message: "the Worker is not configured; see its README" }
      return Response.json({ error: failure }, { status: 500 })
    }
    const app = App.create({
      token: config.FLUID_TOKEN,
      people: (person): Host.Service => env.PERSON.get(env.PERSON.idFromName(person)),
      logger: log,
    })
    return app.fetch(request, env, ctx)
  },
} satisfies ExportedHandler<Env>
