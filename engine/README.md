# @oniva/engine

Oniva's durable agent engine: a TypeScript (ESM, strict, Node >= 22) runtime
that keeps agent sessions alive across worker crashes, long approval waits and
lease takeovers, with Postgres as the system of record.

Bake-off results against the plan's acceptance examples: [`docs/bakeoff.md`](docs/bakeoff.md).

## Layout

- `src/core/` ids, injected `Clock`, cancellation `Context`, typed errors, JSON, limits
- `src/storage/` the per-session storage contract, the memory backend, the
  conformance suite, and `postgres/` (storage, migrations, fenced leases)
- `src/session/` the serialized commit line (`Session`) and typed documents
- `src/tasks/` the durable task runtime (checkpoints, waits, signals, abort)
- `src/models/` provider-neutral model types, `ModelRegistry`, the Anthropic adapter and `FakeProvider`
- `src/tools/` the tool registry and the HTTP tool gateway client
- `src/agent/` the harness: conversations, submissions and inbox, generation and
  tool tasks, approvals, subagents, compaction, hooks and `HostPolicy`
- `src/watch/` committed-state watch streams (snapshots and patches with cursors)
- `src/server/` the HTTP service on `node:http`
- `src/worker/` worker-process wiring shared by the entry points
- `scripts/worker.ts` production entry; `scripts/worker-test-harness.ts` scenario-test entry
- `test/` unit and integration tests; `test/scenario/` the bake-off scenarios

## Public API

Everything is exported from the package root (`src/index.ts`).

| Area | Main exports |
| --- | --- |
| Harness | `Harness.open(storage, options)`, `harness.createConversation`, `harness.conversation(id)`, `harness.submission(id)`, `harness.decideApproval`, `harness.approvals`, `harness.waitForIdle`, `harness.close` |
| Conversations | `conversation.submit({ text, requestId?, whenBusy?: "followUp" \| "steer" \| "reject", type? })` → `Submission` (`wait()`, `record()`), `conversation.abort(reason?)`, `entries()`, `live()`, `usage()` |
| Policy & hooks | `HostPolicy` (`resolveModelRequest`, `authorizeTool` → `allow` / `block` / `requireApproval`), `AgentHooks` |
| Tools | `defineTool`, `ToolRegistry`, `createGatewayTools`, `createSubagentTool` |
| Models | `ModelRegistry`, `AnthropicProvider`, `FakeProvider` (`fakeText`, `fakeToolCall`, ...) |
| Storage | `PostgresStorageProvider`, `MemoryStorageProvider`, `migrate`, `PostgresLeases`, `LeaseKeeper`, `defineStorageConformanceSuite` |
| HTTP & watch | `createEngineServer`, `WatchHub`, `loadConversationView` |
| Worker | `startWorker`, `workerHostPolicy`, `workerConfigFromEnv`, `createWorkerPool` |
| Core | `Clock` / `realClock` / `ManualClock`, `Context`, `EngineError` subclasses (`NotOwner`, `LeaseLost`, `SessionFailed`, ...) |

A minimal embedding:

```ts
const pool = createWorkerPool({ databaseUrl, dbSchema: null });
await migrate(pool);
const keeper = await LeaseKeeper.acquire({
  store: new PostgresLeases(pool), sessionId: "project-42", ttlMs: 15_000,
});
const harness = await Harness.open(new PostgresStorageProvider(pool), {
  sessionId: "project-42",
  models: new ModelRegistry().register(new AnthropicProvider()),
  tools: [myTool],
  policy: myHostPolicy,
  fence: keeper.fence,   // every commit carries the lease token
  signal: keeper.signal, // a lost lease stops the runtime
});
const conv = await harness.createConversation({ tenantId: "t", projectId: "p", agent: { tools: ["my_tool"] } });
const answer = await (await conv.submit({ text: "hello", requestId: "req-1" })).wait();
```

### HTTP surface

All routes but health need `Authorization: Bearer <service token>`; conversation
routes also need `X-Oniva-Tenant` / `X-Oniva-Project` matching the conversation.

| Route | Purpose |
| --- | --- |
| `GET /v1/health` | `{ status, sessionId, ownerId, owner }` (no auth) |
| `POST /v1/conversations` | create (`{ id?, agent? }`) → 201 |
| `POST /v1/conversations/:id/submissions` | submit (`{ text \| content, requestId?, whenBusy?, type? }`; `Idempotency-Key` header works too) → 202 |
| `GET /v1/conversations/:id/watch` | SSE watch stream (`id:` is the cursor; resume with `Last-Event-ID` or `?cursor=`) |
| `POST /v1/conversations/:id/abort` | abort queued and running work; answers once the run is idle |
| `POST /v1/conversations/:id/approvals/:toolTaskId/decision` | `{ decision: "approve" \| "deny", note? }`, principal in `X-Oniva-Principal` |

A worker that does not hold the session lease answers `409 { error: "not_owner", ownerId }`.

## Running a worker

One worker process serves one session (KTD4). Start it with `npm run worker`
(runs `scripts/worker.ts` through `tsx`). It migrates, waits for the session
lease (answering `409` meanwhile), then serves. Exit codes: `0` after
SIGTERM/SIGINT (lease released), `2` bad configuration, `3` lease lost
(restart it), `1` other failures.

| Variable | Default | Meaning |
| --- | --- | --- |
| `DATABASE_URL` | required | Postgres connection URL |
| `ENGINE_DB_SCHEMA` | connection default | schema holding the engine tables |
| `ENGINE_SESSION_ID` | required | the session this worker serves |
| `ENGINE_SERVICE_TOKEN` | required | bearer token the backend presents |
| `ENGINE_PORT` / `ENGINE_HOST` | `8080` / `0.0.0.0` | listen address |
| `ENGINE_OWNER_ID` | `host:pid:uuid` | lease owner id |
| `ENGINE_LEASE_TTL_MS` | `15000` | lease TTL; heartbeat every TTL/3 |
| `ENGINE_LEASE_RETRY_MS` | TTL/3 | standby retry interval |
| `ENGINE_GATEWAY_URL` / `ENGINE_GATEWAY_TOKEN` | none / service token | tool gateway |
| `ENGINE_APPROVAL_TTL_MS` | `86400000` | approval expiry (`0`: none) |
| `ANTHROPIC_API_KEY` | none | model key (read per request) |
| `ENGINE_MODEL` / `ENGINE_SUMMARY_MODEL` | Anthropic defaults | models for replies / compaction |
| `ENGINE_MAX_TOKENS` | `8192` | reply budget |
| `ENGINE_PROGRESS_INTERVAL_MS` | `100` | progress commit throttle |
| `ENGINE_SUBAGENT_TOOL` | none | register a subagent tool under this name |

`ENGINE_TEST_HARNESS` together with `NODE_ENV=production` makes the worker
refuse to start. `npm run worker:test` runs the scenario-test entry point
(scripted fake model, database-backed shared test clock in `engine_test_clock`,
test-only `/v1/test/*` routes); it refuses `NODE_ENV=production` and must never
be deployed.

## Storage and lease model

- **Storage** is scoped per session. Every write is one atomic `CommitBatch` on
  the session's serialized commit line and gets the next gap-free `seq`.
  Records (conversations, entries, tasks, submissions, document snapshots) are
  stored whole as `json` beside the columns used for lookups; the schema is
  `migrations/001_init.sql`, applied by `migrate` (advisory-locked, idempotent).
- **Leases** live in `engine_session_leases(session_id, owner_id, token,
  expires_at)`. Acquire, renew and expiry are computed in SQL against the
  database clock; every change of hands bumps `token`, and rows are never
  deleted, so a token is never reused.
- **Fencing.** Each commit locks the lease row `FOR UPDATE` and compares the
  token, so a stale owner's commit fails with `LeaseLost`. `LeaseKeeper`
  heartbeats every TTL/3 and self-fences (aborts its signal, stopping the
  runtime) once `lastRenewal + ttl − safetyMargin` passes on the monotonic
  clock, before another worker can take over. Gateway calls carry the token
  in `X-Engine-Fence`; a gateway rejection calls `LeaseKeeper.fenceNow`.
- **Recovery.** A new owner reopens the runtime from committed state:
  generations re-request, tool calls cut between intent and result are re-run
  (replay-safe tools, same idempotency key) or reconciled from the gateway's
  stored outcome, else reported `interrupted`; approval waits and queued
  inputs carry over.
- **Clocks.** Engine logic (deadlines, approval expiry, throttling) uses the
  injected `Clock`; leases use the database clock only.

## Tool gateway contract

The full contract is specified by the fake gateway's TSDoc in
[`test/support/fake-gateway.ts`](test/support/fake-gateway.ts); the client is
`src/tools/gateway.ts`. In short, with `Authorization: Bearer <token>` on every
request:

- `GET /internal/tools/manifest` → `{ tools: [{ name, description, parameters
  (JSON Schema), sideEffect: "none" | "write", idempotent?, timeoutMs,
  requiresApproval? }] }`. Reads and idempotent writes are replay-safe.
- `POST /internal/tools/{name}/invoke` with `{ args, runId, toolCallId,
  tenantId, projectId }`, `Idempotency-Key: runId:toolCallId`,
  `X-Engine-Fence: <token>` and `X-Engine-Session`. A key with a stored
  outcome returns it without re-running; a token below the session's
  high-water mark gets `409 { error: "fence_rejected" }`.
- `GET /internal/tools/invocations/{key}` → the stored outcome, or `404`
  (used to reconcile after a restart).

## Commands

```sh
npm ci
npm run typecheck       # tsc --noEmit
npm run lint            # biome check
npm test                # unit + integration (excludes test/scenario)
npm run test:scenario   # bake-off scenario suite (spawns worker processes)
npm run build           # emit dist/
npm run worker          # production worker (env above)
```

Postgres tests use `ENGINE_TEST_PG_URL` when set (CI provides Postgres 16);
otherwise each file boots a throwaway local cluster (`initdb` on `PATH` or
`ENGINE_TEST_PG_BIN`).

## Limits

| Limit | Default |
| --- | --- |
| `maxToolCallsPerRound` | 32 |
| `maxSubagentDepth` | 3 |
| `maxRequestBodyBytes` | 1 MiB |
| `maxEntryBytes` | 256 KiB |
| `maxDocumentBytes` | 1 MiB |

Override any of them with `resolveLimits({...})`.
