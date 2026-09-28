import { Memory } from "@fluiddb/core"
import type { Api, Turn } from "@fluiddb/schema"
import { DurableObject } from "cloudflare:workers"
import { Config } from "./config"
import { Host } from "./host"
import { log } from "./log"

// One person's memory: their rows and vectors in this object's SQLite, their turns in its inbox, their writes one at
// a time. Named by the person's id, so every request about them reaches this object.
export class Person extends DurableObject<Config.Vars> implements Host.Service {
  private readonly host: Host.Host
  private readonly service: Host.Service

  constructor(ctx: DurableObjectState, env: Config.Vars) {
    super(ctx, env)
    const config = Config.load(env)
    this.host = Host.create({
      sql: {
        run: (query, ...params) => ctx.storage.sql.exec(query, ...params).toArray(),
        transaction: (fn) => ctx.storage.transactionSync(fn),
      },
      alarm: { get: () => ctx.storage.getAlarm(), set: (at) => ctx.storage.setAlarm(at) },
      memory: (store) => Memory.create({ ...Config.memory(config, log), store }),
      logger: log,
      settings: { delay: config.INGEST_DELAY },
    })
    this.service = Host.serve(this.host)
  }

  accept(person: string, session: string, turns: Turn.Info[]) {
    return this.service.accept(person, session, turns)
  }
  ingest(person: string, session: string, turns: Turn.Info[]) {
    return this.service.ingest(person, session, turns)
  }
  recall(person: string, ask: Api.Ask) {
    return this.service.recall(person, ask)
  }
  dossier(person: string) {
    return this.service.dossier(person)
  }
  remember(person: string, request: Api.Remember) {
    return this.service.remember(person, request)
  }
  inspect(person: string, request: Api.Inspect) {
    return this.service.inspect(person, request)
  }
  evidence(person: string, request: Api.Evidence) {
    return this.service.evidence(person, request)
  }
  source(person: string, request: Api.Source) {
    return this.service.source(person, request)
  }
  previewForget(person: string, request: Api.Forget) {
    return this.service.previewForget(person, request)
  }
  forget(person: string, request: Api.Forget) {
    return this.service.forget(person, request)
  }
  erase(person: string) {
    return this.service.erase(person)
  }
  status(person: string) {
    return this.service.status(person)
  }
  retry(person: string) {
    return this.service.retry(person)
  }
  override async alarm() {
    await this.host.alarm()
  }
}
