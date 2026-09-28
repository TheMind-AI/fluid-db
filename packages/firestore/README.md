# @fluiddb/fluiddb/firestore

```ts
import { Firestore } from "@fluiddb/fluiddb/firestore"
import { Memory, Person } from "@fluiddb/fluiddb"

const store = Firestore.create(existingServerFirestoreClient, { collection: "fluiddb_people" })
const engine = Memory.create({ store, model, embedder, decider })
const memory = Person.bind(engine, authorizedPersonId)
```

Server-side Node adapter for `@google-cloud/firestore`. The application owns credentials and client lifetime.
It uses an isolated collection: encoded person IDs, a revision document and a records subcollection. This does
not read or migrate another product's memory schema. Do not use the browser Firebase client here.

Native transactions atomically commit rows, tombstones and vectors. Revision checks reject stale writes. Model
calls happen outside the transaction; retries cannot repeat model spending inside Firestore's transaction callback.
If a native transaction/document limit is exceeded, the operation fails; writes are never split into partial commits.
Account erasure is also atomic and subject to those limits. Listings and exact vector search scan that person's
selected record kind; pagination bounds the response, not billed reads. No custom composite indexes are required.

Use Node, not Bun, for this adapter: the actual emulator conformance passed under Node; gRPC queries stalled under
Bun in this workspace. `FIRESTORE_EMULATOR_HOST=127.0.0.1:8080 bun run test:adapters` runs the tests using Node.
The test suite requires an explicit emulator address and never falls back to a real project.

## Workers / fetch-only REST

```ts
import { FirestoreRest } from "@fluiddb/fluiddb/firestore/rest"
const store = FirestoreRest.create({ projectId, accessToken: getGoogleAccessToken })
```

Use this entry point in the therapist backend's Cloudflare runtime. Token acquisition and refresh remain with the
host. It shares the Node adapter's document layout, encodes Firestore values, and uses an atomic commit guarded by
the person's revision document update time. Concurrent changes fail with `ConflictError`; retry with stable IDs.
For an emulator supply `origin: "http://127.0.0.1:8080"` and `accessToken: async () => ""`. Each request has a default
30-second deadline and refuses redirects; failures do not echo Firestore response bodies.

Firestore documents the [atomic commit](https://cloud.google.com/firestore/docs/reference/rest/v1/projects.databases.documents/commit)
and [update-time precondition](https://cloud.google.com/firestore/docs/reference/rest/v1/Precondition) used here.
