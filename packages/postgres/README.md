# @fluiddb/fluiddb/postgres

```ts
import { Pool } from "pg"
import { Postgres } from "@fluiddb/fluiddb/postgres"
import { Memory, Person } from "@fluiddb/fluiddb"

const pool = new Pool({ connectionString: process.env.DATABASE_URL })
const store = await Postgres.create(pool) // optional { table: "my_fluiddb_records" }
const engine = Memory.create({ store, model, embedder, decider })
const memory = Person.bind(engine, authorizedPersonId)
// Close the application-owned pool at shutdown: await pool.end().
```

Uses native transactions, an isolated JSONB table and a chronological listing index. Exact cosine search is scoped
to a person; vectors and records commit/delete together. No pgvector extension is needed. Initialization requires
permission to create the table/index. Use a dedicated PostgreSQL database/schema/search_path if desired.

The same `Store` contract and conformance tests apply to SQLite and Firestore. Conditional writes use revision
tokens and fail atomically on conflict. Retry `ConflictError` through your host's durable queue with identical
source IDs. No model calls run in a database transaction. The host owns authorization, jobs and connection pooling.

Run the real database tests with `FLUID_TEST_POSTGRES_URL=... bun run test:adapters` at the repository root. Tests
create uniquely named temporary tables, then remove them. They do not load production credentials automatically.
