import type { Sql } from "./sql"

// Each migration is a list of statements, applied once, in order, each migration in one transaction. `name` keeps
// the versions of separate schemas apart in one database.
export function run(sql: Sql, name: string, migrations: string[][]) {
  sql.run("CREATE TABLE IF NOT EXISTS fluid_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
  const version = Number(sql.run("SELECT value FROM fluid_meta WHERE key = ?", `${name}.version`)[0]?.value ?? 0)
  for (const [i, steps] of migrations.entries()) {
    if (i < version) continue
    sql.transaction(() => {
      for (const step of steps) sql.run(step)
      sql.run(
        "INSERT INTO fluid_meta (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        `${name}.version`,
        String(i + 1),
      )
    })
  }
}

export * as Migrate from "./migrate"
