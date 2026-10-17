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
- `src/models/` provider-neutral model types, `ModelRegistry`, the Anthropic and OpenAI-compatible adapters and `FakeProvider`
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
| Harness | `Harness.open(storage, options)`, `harness.createConversation`, `harness.conversation(id)`, `harness.submission(id)`, `harness.decideApproval`, `harness.approvals`, `harness.waitForIdle`, `harness.close`. `options.tasks` registers extra task kinds after the built-ins (host tasks); a duplicate kind throws at `open`. |
| Conversations | `conversation.submit({ text, requestId?, whenBusy?: "followUp" \| "steer" \| "reject", type? })` → `Submission` (`wait()`, `record()`), `conversation.abort(reason?)`, `conversation.cancelSubmission(submissionId)` → `"cancelled" \| "not_queued" \| "unknown"`, `entries()`, `live()`, `usage()` |
| Policy & hooks | `HostPolicy` (`resolveModelRequest`, `authorizeTool` → `allow` / `block` / `requireApproval`), `AgentHooks` |
| Tools | `defineTool`, `ToolRegistry`, `createGatewayTools`, `createSubagentTool` |
| Models | `ModelRegistry`, `AnthropicProvider`, `OpenAICompatibleProvider` / `openRouterProvider`, `FakeProvider` (`fakeText`, `fakeToolCall`, ...) |
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
| `GET /v1/health` | `{ status, sessionId, ownerId, owner, apiVersion, taskKinds }`; `apiVersion` is the engine API version and `taskKinds` the sorted registered kinds (built-ins plus host tasks) (no auth) |
| `POST /v1/conversations` | create (`{ id?, agent? }`) → 201 |
| `POST /v1/runs` | start a run: `{ id, kind, input?, agent? }`; creates the conversation with the caller's `id` and spawns a root task of `kind`, conversation-owned → 201 `{ conversationId, taskId }`; a repeat `id` returns the existing run (200) and spawns nothing; an unknown `kind` is 400 |
| `POST /v1/conversations/:id/submissions` | submit (`{ text \| content, requestId?, whenBusy?, type? }`; `Idempotency-Key` header works too) → 202 |
| `PUT /v1/conversations/:id/agent` | partial agent config (`systemPrompt`, `tools`, `model`, `thinking`, `depth`, `metadata`, `compaction`, `maxRounds`, `deadlineMs`); unknown or mistyped fields → 400 and nothing committed; applies from the next model request and arrives at watchers as a patch to `/agent` → 200 `{ conversationId, agent }` |
| `GET /v1/conversations/:id/watch` | SSE watch stream (`id:` is the cursor; resume with `Last-Event-ID` or `?cursor=`) |
| `DELETE /v1/conversations/:id/submissions/:submissionId` | cancel a still-queued input; it settles `unanswered(withdrawn)` → 200 `{ conversationId, submissionId, cancelled: true }`; 404 unknown submission, 409 `not_queued` if it already started, was placed into a run or settled |
| `POST /v1/conversations/:id/abort` | abort queued and running work; answers once the run is idle |
| `POST /v1/conversations/:id/tasks/:taskId/signal` | deliver a signal to one of the conversation's tasks: `{ key, payload? }` → 200; 404 for an unknown task or one of another conversation, 409 for a duplicate key or a finished task |
| `GET /v1/conversations/:id/tasks/:taskId` | a task's status: `{ id, kind, status, phase, outcome? }` |
| `POST /v1/conversations/:id/approvals/:toolTaskId/decision` | `{ decision: "approve" \| "deny", note? }`, principal in `X-Oniva-Principal` |

Each watch-view `inbox.queue` item is `{ submissionId, mode, preview?, requestId? }`: `preview` is the first 200 characters of the queued text and `requestId` the caller's id (both absent on items queued by older engines); `inbox.placed` is unchanged.

A `user` entry's payload carries `requestId` (the caller's id, or the submission id when none was given) next to `submissionId`, so a client can match its optimistic message to the entry.

The watch view's `toolOutput` is keyed by tool task id; each value is `{ text, truncated, attempt, toolCallId, toolName }`, so a client can attach live output to the right call when tools run in parallel (`toolCallId` matches the `tool_call` block id and the `tool_result` entry's `toolCallId`; null only on documents written before these fields existed).

The watch view's `compaction` is `{ running: boolean, cause?, startedAt? }`: `running` is true while a context compaction task is in flight, with `cause` (`background`, `threshold` or `overflow`) and `startedAt` (ISO time) alongside; when idle it is `{ running: false }`. It carries no task id or summary text, and arrives in snapshots and as patches to `/compaction` when a compaction starts and when it lands or fails.

The watch view's `subagents` is keyed by the id of the parent's tool task that spawned each child conversation: `{ conversationId, submissionId, status, startedAt, endedAt?, toolCalls, recent, usage }`. `status` is `running` until the child's input settles, then `done`, `aborted` or `failed`; `toolCalls` counts the child's model tool calls; `recent` holds the child's last five tool calls (oldest first), each `name(arguments)` clipped to 160 characters; `usage` is the child's own tally. It is `{}` for a conversation with no subagents, and arrives in snapshots and as patches to `/subagents/<toolTaskId>`. The child's transcript and tool tasks stay in the child conversation (watch it by id).

Agent config `maxRounds` (model rounds per run) and `deadlineMs` (absolute engine-clock epoch ms) are null by default. A model request past either ends the run with a `run_error` of kind `denied` and code `max_rounds` or `run_deadline`; subagent children inherit both.

A subagent tool's model, tools and system prompt can be set per run in the parent's `agent.metadata.subagents[<toolName>]` (`{ model?, tools?, systemPrompt? }`), which wins over the static `createSubagentTool` options. `maxDepth` is never read from metadata. A malformed entry fails the call with an error result and creates no child.

The watch view's `run` is `{ active: boolean, runId?, startedAt? }`: `active` is true for as long as the conversation has an unfinished run, including while tools execute, approvals are pending and between model rounds (when `live` is already cleared); `runId` and `startedAt` (ISO time) accompany it while active, and when idle it is `{ active: false }`. It arrives in snapshots and as patches to `/run` when a run starts and when it ends.

When the worker restarts with a task of a run in flight (the run's own generation, or a tool call or subagent task below it, however deep; for a host-task run, any task below its root task), the recovery commit appends one display-only `run_resumed` entry `{ runId, at }` to the run's ROOT conversation, atomically with the recovery, so each restart adds exactly one per run and clean runs add none. The context builder ignores it, so the model never sees it; it reaches clients through the watch view's `entries` like any entry. Clients use it as the "resumed after restart" cue, which a subagent's replayed read would otherwise never produce.

The watch view's `tasks` is keyed by task id and lists host tasks (registered through `HarnessOptions.tasks`) whose definition supplies `view(task)`: `{ kind, parentId, status, phase, label?, attempt?, waitUntil?, startedAt, endedAt?, error? }`. `status` is `pending`, `running`, `waiting`, `done`, `aborted` or `faulted`; `parentId` is the owning task (null for a root task); `label` and `attempt` come from `view`; `waitUntil` (ISO) is the deadline of a waiting task; `endedAt` and `error` (abort reason or fault message) appear once terminal. Kinds without `view` and the built-in generation, tool and compaction tasks never appear, so a plain chat has `tasks: {}`. It arrives in snapshots and as patches under `/tasks/<taskId>`. `loadConversationView` and `loadViewState` take a harness (or any source with `runtime.registry`) to know which kinds opt in; given a bare session they return `tasks: {}`.

`Harness.onSettled(listener)` calls `listener({ conversationId, submissionId, requestId, status, reason? })` once after each submission settles (`done`, or `unanswered` with its `reason`, e.g. `aborted`), including submissions settled when the harness opens (pass `HarnessOptions.onSettled` to hear those), and returns an unsubscribe function. A throwing listener is reported through `onError` (`during: "settle"`) and never affects settlement.

When a generation fails after streaming text (a model error), that text is kept as an `assistant` entry with `partial: true` and `message.stopReason: "error"`, written just before the `run_error` entry; its blocks are as streamed (a tool call may be incomplete and is never run). On the next turn the model sees only the partial entry's text blocks. A failure before any text (including a policy denial) writes only `run_error`.

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
| `ANTHROPIC_API_KEY` | none | model key (read per request; unused under `ENGINE_POLICY=backend`) |
| `ENGINE_POLICY` | `backend` if `ENGINE_POLICY_URL` is set, else `local` | `backend`: the backend resolves each model request (provider, model, tenant key, token budget, quota) and records usage; a definitive refusal denies the request, an outage is retried (`ENGINE_POLICY_RETRY_MS`) and then fails it as a retryable `policy_unavailable`; there is never a fallback to a local key |
| `ENGINE_POLICY_URL` | `ENGINE_GATEWAY_URL` when `ENGINE_POLICY=backend` | backend origin for `/internal/models/policy` and `/internal/models/usage`; uses `ENGINE_GATEWAY_TOKEN` |
| `ENGINE_POLICY_TIMEOUT_MS` | `5000` | timeout of one policy or usage call |
| `ENGINE_POLICY_RETRY_MS` | `60000` | total time a transient policy or usage failure is retried (`0`: never); see below |

**Backend outages.** Under `ENGINE_POLICY=backend` a backend restart or blip must not fail a long run. Both `/internal/models/policy` and `/internal/models/usage` retry network errors, timeouts, `408`, `429` and `5xx` with exponential backoff (250 ms doubling, capped at 5 s, jitter 50-100%) for up to `ENGINE_POLICY_RETRY_MS` in total. `Retry-After` on `429`/`503` replaces the computed delay (a value longer than the remaining budget ends the retrying). A policy retry reuses the request id and body, and a run abort stops it at once. Each retry logs `policy_retry` / `usage_retry`. A definitive answer is never retried: a `200 { ok: false }` denial, `401`, `403`, `422` and other `4xx`, or a malformed `200`. When the budget is spent the policy request fails the run with `run_error { kind: "network", code: "policy_unavailable", retryable: true }` (a denial stays `kind: "denied"`, `retryable: false`). A spent usage report logs `usage_failed`. The budget is checked between attempts, so one in-flight attempt may run up to `ENGINE_POLICY_TIMEOUT_MS` past it.

**Durable usage outbox.** Under `ENGINE_POLICY=backend` every usage report is parked in the engine's `engine_usage_outbox` table before its first attempt and deleted only after the backend answers `2xx`, so a report survives a backend outage and a worker restart. On start, and every 30 s, the worker's flusher reclaims rows that are unclaimed or were claimed more than a minute ago (oldest first) and re-sends them with the same `requestId`; the backend ledger dedupes per `(tenant, requestId)`, so a redelivery is counted once. A report older than 7 days is logged `usage_stale` but still delivered. A definitive `4xx` (other than `408`/`429`) is logged `usage_dropped` and removed. Without an outbox (as in tests) reports are memory-only.

**Run-finished push.** The same outbox carries a second row `kind`, `run_finished` (migration `004_outbox_kind.sql`; the runner accepts the gap at `003`). When a submission settles on a conversation whose agent `metadata.notify` is `"backend"` (Fleet and workflow runs; chat and Deep Work carry no tag), the worker parks `{ kind: "run_finished", conversationId, submissionId, status, reason?, usage }` under the id `run_finished:<conversationId>:<submissionId>` and posts it to `POST /internal/runs/finished` with the usage retry policy. A `2xx`, or a permanent `4xx` other than `401`/`403`, deletes the row; `401`/`403` and an exhausted retry budget keep it for the flusher (start, then every 30 s). Each flusher claims only its own kind. The backend's reconciler is the second net for a post that was never parked.
| `ENGINE_MODEL` / `ENGINE_SUMMARY_MODEL` | Anthropic defaults | models for replies / compaction |
| `ENGINE_OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | OpenRouter endpoint (the production worker registers the `openrouter` provider next to `anthropic`) |
| `ENGINE_MODEL_FIRST_BYTE_TIMEOUT_MS` | `120000` | abort an OpenRouter attempt that has produced no data event by then; retried as a `network` error |
| `ENGINE_MODEL_IDLE_TIMEOUT_MS` | `120000` | abort an OpenRouter attempt silent for this long between data events; retryable, partial kept |
| `ENGINE_MAX_TOKENS` | `8192` | reply budget |
| `ENGINE_PROGRESS_INTERVAL_MS` | `100` | progress commit throttle |
| `ENGINE_SUBAGENT_TOOL` | none | register a subagent tool under this name |
| `ENGINE_SUBAGENT_TOOLS` | none | comma-separated subagent tool names to register (with `ENGINE_SUBAGENT_TOOL`, deduplicated) |

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
  tenantId, projectId }` and `Idempotency-Key: runId:toolCallId`. A key with
  a stored outcome returns it without re-running. A started effect runs to
  completion even if the client disconnects, and its outcome is stored.
- `GET /internal/tools/invocations/{key}` → the stored outcome, or `404`
  (used to reconcile after a restart, or once after a non-idempotent write
  timed out; with no stored outcome the timeout result says the write may
  have taken effect, `details.outcome: "unknown"`).
- **Fencing.** While the worker holds a lease, every request (manifest load,
  invoke, reconcile) carries `X-Engine-Fence: <token>` and `X-Engine-Session`.
  The gateway raises the session's high-water mark on any fenced request and
  answers a lower token `409 { error: "fence_rejected" }`; it re-checks an
  invoke's token when the effect commits. So a new owner's fenced manifest
  load (done after lease acquisition, before recovery) or reconcile locks out
  the previous owner's invokes still on the wire, and a `404` from the
  reconcile route is final. `GatewayToolset.raiseFence()` re-sends the token
  for a toolset that outlives a lease.
- Reads retry 5xx, 429 and network failures with full-jitter backoff,
  honoring `Retry-After` on 429/503 within the tool's deadline. Response
  bodies over `maxResponseBytes` (default 1 MiB) are an error.

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

### OpenAI-compatible provider (OpenRouter)

`OpenAICompatibleProvider` streams the Chat Completions API (`POST {baseURL}/chat/completions`, `stream_options.include_usage`) with plain `fetch` and emits the same events as the Anthropic adapter. `openRouterProvider()` is it configured for OpenRouter (name `openrouter`); `defaultModelRegistry(env)` (`src/worker/providers.ts`) registers it beside `anthropic`, so the worker reports both in `supportedProviders` and a tenant whose only stored key is OpenRouter's runs engine chat and Deep Work. Model ids are OpenRouter slugs (`anthropic/claude-sonnet-5.5`, `deepseek/deepseek-v4.1-flash`); the backend policy returns them.

- Tool calls stream per `index` and become one `tool_call` block each; tool results go out as `role: "tool"` messages.
- No thinking blocks: `reasoning` / `reasoning_content` deltas are ignored and `thinking` is not sent.
- Usage: uncached input is `prompt_tokens - cached_tokens - cache_write_tokens`; cache tokens come from `prompt_tokens_details`. Models under `anthropic/` get `cache_control` breakpoints on the system prompt and last stable message.
- Finish reasons: `stop` → `end_turn` (`tool_use` if tool calls were streamed), `tool_calls` → `tool_use`, `length` → `max_tokens`, `content_filter` → `refusal`.
- Retries (`src/models/retry.ts`, option `retry`): a request that fails before any content has been emitted is retried with capped exponential backoff (500 ms doubling to 8 s, jitter 50-100%), at most 3 retries within a 60 s wait budget (`maxRetries`, `budgetMs`, `baseDelayMs`, `maxDelayMs`; `maxRetries: 0` turns it off). Retried: connection errors, timeouts and "fetch failed", HTTP 408, 409, 429 and 5xx, an error object in the stream, an `error` finish, and a stream that breaks or ends before its first text or tool-call delta. `Retry-After` (seconds or date) or, failing that, OpenRouter's `x-ratelimit-reset` replaces the computed delay; a delay that does not fit the remaining budget ends the retrying. Never retried: 400, 401, 402, 403, 404, 413, 422, `context_overflow`, aborts. Aborting the run stops a backoff at once and ends the call `aborted`. The caller sees one `start` and one message; a retried attempt leaves no trace. `retry.onRetry({ provider, model, attempt, delayMs, error })` is the logging hook; `defaultModelRegistry(env, log)` logs it as `model_retry` (the retry helpers `withRetry`, `describeError`, `serverRetryDelay` are reusable by any fetch-based provider).
- Request timeouts (`firstByteTimeoutMs`, `idleTimeoutMs`, both default 120 s; `ENGINE_MODEL_FIRST_BYTE_TIMEOUT_MS` / `ENGINE_MODEL_IDLE_TIMEOUT_MS`): each attempt gets its own `AbortController`. The first-byte timer is cleared by the first data event (an SSE keepalive comment does not clear it); the idle timer is armed then and reset on every data event. A timeout before any block event is a `network` error and retried; after one it ends the attempt `network, retryable: true` with the partial kept and no retry. The run's own abort still wins and ends the call `aborted`.
- Mid-stream failures are not retried. Once a text or tool-call delta has gone out, replaying the request would duplicate partial output, and the generation task has no safe retry: it keeps the streamed text as a `partial` assistant entry and fails the run with the (retryable) `model_error`; a partial tool call is never run. So a connection drop in the middle of a long reply still fails that run, while one before the first token is absorbed.
- Transport error text keeps the real cause: `fetch failed (UND_ERR_SOCKET: other side closed)`, `terminated (ECONNRESET: other side closed)`. Only `code: message` of each error down the `cause` chain is read, never headers, bodies or keys.
- The Anthropic adapter relies on the SDK's own retries (connection errors, 408/409/429/5xx, before the stream starts) and sets `maxRetries` explicitly (default 3, option `maxRetries`); it does not use `retry.ts`.
- Errors: 401/402/403 `auth`, 429 `rate_limited`, 408 `network`, 5xx `overloaded`, 400/413 with a context-length message `context_overflow`, other 4xx `invalid_request`, transport failures `network`, cancellation `aborted`. An `error` object inside the stream fails the generation with the text kept as partial.
