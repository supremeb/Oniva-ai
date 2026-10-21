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
| Harness | `Harness.open(storage, options)`, `harness.createConversation`, `harness.conversation(id)`, `harness.submission(id)`, `harness.decideApproval`, `harness.approvals`, `harness.answerQuestion`, `harness.questions`, `harness.waitForIdle`, `harness.close`. `options.tasks` registers extra task kinds after the built-ins (host tasks); a duplicate kind throws at `open`. |
| Conversations | `conversation.submit({ text, requestId?, whenBusy?: "followUp" \| "steer" \| "reject", type? })` → `Submission` (`wait()`, `record()`), `conversation.abort(reason?)`, `conversation.cancelSubmission(submissionId)` → `"cancelled" \| "not_queued" \| "unknown"`, `entries()`, `live()`, `usage()` |
| Policy & hooks | `HostPolicy` (`resolveModelRequest`, `authorizeTool` → `allow` / `block` / `requireApproval`), `AgentHooks` |
| Tools | `defineTool`, `ToolRegistry`, `createGatewayTools`, `createSubagentTool`, `askUserTool` |
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
| `POST /v1/conversations/:id/submissions` | submit (`{ text \| content, requestId?, whenBusy?, type?, untrusted? }`; `Idempotency-Key` header works too) → 202 |
| `PUT /v1/conversations/:id/agent` | partial agent config (`systemPrompt`, `tools`, `model`, `thinking`, `depth`, `metadata`, `compaction`, `maxRounds`, `deadlineMs`); unknown or mistyped fields → 400 and nothing committed; applies from the next model request and arrives at watchers as a patch to `/agent` → 200 `{ conversationId, agent }` |
| `GET /v1/conversations/:id/watch` | SSE watch stream (`id:` is the cursor; resume with `Last-Event-ID` or `?cursor=`) |
| `DELETE /v1/conversations/:id/submissions/:submissionId` | cancel a still-queued input; it settles `unanswered(withdrawn)` → 200 `{ conversationId, submissionId, cancelled: true }`; 404 unknown submission, 409 `not_queued` if it already started, was placed into a run or settled |
| `POST /v1/conversations/:id/abort` | abort queued and running work; answers once the run is idle |
| `POST /v1/conversations/:id/tasks/:taskId/signal` | deliver a signal to one of the conversation's tasks: `{ key, payload? }` → 200; 404 for an unknown task or one of another conversation, 409 for a duplicate key or a finished task |
| `GET /v1/conversations/:id/tasks/:taskId` | a task's status: `{ id, kind, status, phase, outcome? }` |
| `GET /v1/conversations/:id/approvals` | `{ conversationId, approvals: ApprovalItem[] }`: the conversation's approvals document plus those of its subagent descendants, oldest first; the decision route also finds a child's item through the root id |
| `POST /v1/conversations/:id/approvals/:toolTaskId/decision` | `{ decision: "approve" \| "deny", note? }`, principal in `X-Oniva-Principal` |
| `GET /v1/conversations/:id/questions` | `{ conversationId, questions: QuestionItem[] }`: the conversation's `agent.questions` document items, oldest first |
| `GET /v1/conversations/:id/trace` | Read-only `ConversationTrace`: `{ conversationId, entries, submissions, children: { <childId>: { parentToolTaskId, role, parentConversationId, entries, submissions, usage, agent } }, usage, taint, compactions, agent, truncated, nextCursor }`. `submissions` (whole list on every page) says how each input ended: `{ id, requestId, status, unanswered: { reason, message } | null }`; the unanswered reason (`compaction_failed`, `model_error`, `aborted`, ...) is not in the entries, where a failed run shows only a `run_error` carrying the provider's error. Children are the subagent conversations below the root, found through tool-task links to `maxSubagentDepth`; a child bound to another tenant or project is never included. `?limit=` (entries, default 2000, max 10000) and `?cursor=` page it; a response that does not hold everything is `truncated` with a `nextCursor`. `agent.metadata` and entry payloads pass through `maskSecrets`. Same auth and tenant/project binding as the other conversation routes (401, 403, 404); 400 for a bad `limit` or `cursor` |
| `POST /v1/conversations/:id/questions/:toolTaskId/answer` | `{ answer: { optionIds?: string[], text?: string } }`, principal in `X-Oniva-Principal` → 200 `{ question }`. Rejections are `{ error: "question_rejected", reason, question? }`: `invalid` 400, `not_owned` 404, `already_answered` and `not_waiting` 409, `expired` 410 |

The watch view has a `questions` member, `{ items: QuestionItem[] }`, beside `approvals`; it is `{ items: [] }` when the conversation has no questions document, and changes arrive as patches under `/questions/items/<n>` when a question opens and when it is answered. The worker always registers the native `ask_user` tool; a conversation can call it only when its agent `tools` list it.

Each watch-view `inbox.queue` item is `{ submissionId, mode, preview?, requestId? }`: `preview` is the first 200 characters of the queued text and `requestId` the caller's id (both absent on items queued by older engines); `inbox.placed` is unchanged.

A rejected decision (409 `approval_rejected`, `reason` `already_decided`, `expired` or `not_waiting`) carries `approval`, a snapshot of the item with its recorded decision and principal, so a caller can finalise its own record from what the engine did.

Taint: the document `agent.taint` on the ROOT conversation (a subagent's reads taint the run that owns it) is `{ tainted, total, sources }`, where `sources` keeps the first 20 events `{ kind: "tool_result" \| "input", name, ref, at }` and `total` counts all of them. It is set in the same commit that stores a tool result or places an input, and is sticky. A result taints when the tool is a gateway tool whose manifest says `untrustedOutput: true`, or a non-gateway tool that is not `internal` and not in `INTERNAL_NATIVE_TOOLS` (`ask_user`, `complete_task`, `present_plan`, `update_todo`; subagent spawn tools are `internal`). A submission sent with `untrusted: true` (boolean, else 400) taints with `kind: "input"`. `HostPolicy.authorizeTool` receives `toolTaskId`, `toolSource` (`"gateway" \| "native"`) and `taint: { tainted, total, sources }` as a copy it cannot mutate.

A `user` entry's payload carries `requestId` (the caller's id, or the submission id when none was given) next to `submissionId`, so a client can match its optimistic message to the entry.

The watch view's `toolOutput` is keyed by tool task id; each value is `{ text, truncated, attempt, toolCallId, toolName }`, so a client can attach live output to the right call when tools run in parallel (`toolCallId` matches the `tool_call` block id and the `tool_result` entry's `toolCallId`; null only on documents written before these fields existed).

The watch view's `compaction` is `{ running: boolean, cause?, startedAt? }`: `running` is true while a context compaction task is in flight, with `cause` (`background`, `threshold` or `overflow`) and `startedAt` (ISO time) alongside; when idle it is `{ running: false }`. It carries no task id or summary text, and arrives in snapshots and as patches to `/compaction` when a compaction starts and when it lands or fails.

The watch view's `subagents` is keyed by the id of the parent's tool task that spawned each child conversation: `{ conversationId, submissionId, status, startedAt, endedAt?, toolCalls, recent, usage }`. `status` is `running` until the child's input settles, then `done`, `aborted` or `failed`; `toolCalls` counts the child's model tool calls; `recent` holds the child's last five tool calls (oldest first), each `name(arguments)` clipped to 160 characters; `usage` is the child's own tally. It is `{}` for a conversation with no subagents, and arrives in snapshots and as patches to `/subagents/<toolTaskId>`. The child's transcript and tool tasks stay in the child conversation (watch it by id).

Agent config `maxRounds` (model rounds per run) and `deadlineMs` (absolute engine-clock epoch ms) are null by default. A model request past either ends the run with a `run_error` of kind `denied` and code `max_rounds` or `run_deadline`; subagent children inherit both.

A subagent tool's model, tools and system prompt can be set per run in the parent's `agent.metadata.subagents[<toolName>]` (`{ model?, tools?, systemPrompt? }`), which wins over the static `createSubagentTool` options. `maxDepth` is never read from metadata. A malformed entry fails the call with an error result and creates no child.

Typed results: `createSubagentTool({ outputSchema })`, or `outputSchema` in the metadata override, makes a subagent promise a result shape. The child gets the schema appended to its system prompt, `complete_task` added to its tools (the host must register `createCompleteTaskTool()` and `INTERNAL_NATIVE_TOOLS` already lists it) and the schema in its own `agent.metadata.outputSchema`. `complete_task({ result })` validates against that schema (the model cannot supply one); violations come back as a tool error so the child can retry, and a valid result is stored in the child's `agent.result` document in the same commit as the tool result and ends the child's run (`terminate`). The parent's tool result `content` is `JSON.stringify(result)` and `details.result` is the object. A child that settles with text only gets one follow-up (`requestId = subagent:<taskId>:complete`, recorded as `nudge` in the tool task checkpoint, `details.nudged: true`); a second miss is an error result whose `details` hold `rawText`. Without an `outputSchema` nothing changes. The schema subset (`agent/json-schema-subset.ts`): `type` (`string`, `number`, `integer`, `boolean`, `null`, `object`, `array`), `properties`, `required`, `items` (one schema), `enum`, `minItems`, `maxItems`, `minLength`, `maxLength`, `minimum`, `maximum`, plus `title` and `description` as annotations. Any other keyword is rejected when the schema is parsed (a spec throws; a metadata override fails the call), never ignored. At most 16 KiB and 8 levels deep; extra object properties are allowed.

The watch view's `run` is `{ active: boolean, runId?, startedAt? }`: `active` is true for as long as the conversation has an unfinished run, including while tools execute, approvals are pending and between model rounds (when `live` is already cleared); `runId` and `startedAt` (ISO time) accompany it while active, and when idle it is `{ active: false }`. It arrives in snapshots and as patches to `/run` when a run starts and when it ends.

When the worker restarts with a task of a run in flight (the run's own generation, or a tool call or subagent task below it, however deep; for a host-task run, any task below its root task), the recovery commit appends one display-only `run_resumed` entry `{ runId, at }` to the run's ROOT conversation, atomically with the recovery, so each restart adds exactly one per run and clean runs add none. The context builder ignores it, so the model never sees it; it reaches clients through the watch view's `entries` like any entry. Clients use it as the "resumed after restart" cue, which a subagent's replayed read would otherwise never produce.

The watch view's `tasks` is keyed by task id and lists host tasks (registered through `HarnessOptions.tasks`) whose definition supplies `view(task)`: `{ kind, parentId, status, phase, label?, attempt?, waitUntil?, startedAt, endedAt?, error? }`. `status` is `pending`, `running`, `waiting`, `done`, `aborted` or `faulted`; `parentId` is the owning task (null for a root task); `label` and `attempt` come from `view`; `waitUntil` (ISO) is the deadline of a waiting task; `endedAt` and `error` (abort reason or fault message) appear once terminal. Kinds without `view` and the built-in generation, tool and compaction tasks never appear, so a plain chat has `tasks: {}`. It arrives in snapshots and as patches under `/tasks/<taskId>`. `loadConversationView` and `loadViewState` take a harness (or any source with `runtime.registry`) to know which kinds opt in; given a bare session they return `tasks: {}`.

`Harness.onSettled(listener)` calls `listener({ conversationId, submissionId, requestId, status, reason? })` once after each submission settles (`done`, or `unanswered` with its `reason`, e.g. `aborted`), including submissions settled when the harness opens (pass `HarnessOptions.onSettled` to hear those), and returns an unsubscribe function. A throwing listener is reported through `onError` (`during: "settle"`) and never affects settlement.

When a generation fails after streaming text (a model error), that text is kept as an `assistant` entry with `partial: true` and `message.stopReason: "error"`, written just before the `run_error` entry; its blocks are as streamed (a tool call may be incomplete and is never run). On the next turn the model sees only the partial entry's text blocks. A failure before any text (including a policy denial) writes only `run_error`.

A worker that does not hold the session lease answers `409 { error: "not_owner", ownerId }`.

## Questions (`ask_user`)

The native `ask_user` tool (`askUserTool`, flagged `internal` and `humanInput`) lets the model ask a person a question and wait for the answer as durably as an approval. Arguments: `{ question, options?: [{ id, label, description? }], multiSelect?, allowFreeText? }`. Free text is always allowed when there are no options.

- **Document.** `agent.questions` (per conversation): `{ items: QuestionItem[] }`, one item per tool task: `id` (`ques_...`), `toolTaskId`, `runId`, `toolCallId`, `question`, `options`, `multiSelect`, `allowFreeText`, `requestedAt`, `expiresAt`, `status` (`pending` / `answered` / `expired` / `cancelled`), `answer` (`{ optionIds, text, principal, answeredAt }`), `closedAt`.
- **Tool phase.** A call still goes through `HostPolicy.authorizeTool` first (an approval, if required, comes before the question). On `allow` the tool task commits the item together with `waiting({ signal: "answer", until: expiresAt })` and holds no running task. The answer commits an `ok` result whose text is the chosen option labels (comma separated) and any free text as given; the deadline commits a `question_expired` result and the run continues. An abort closes the item `cancelled`. An answered question never taints the conversation.
- **Harness API.** `harness.answerQuestion({ conversationId, toolTaskId, answer: { optionIds?, text? }, principal })` and `harness.questions(conversationId)`. A rejected answer throws `QuestionRejected` and writes nothing; `reason` is `not_owned`, `already_answered`, `expired`, `not_waiting` or `invalid` (unknown or repeated option, several options without `multiSelect`, text where not allowed, nothing chosen, an answer over 16 KiB, bad shape).
- **Expiry.** `HarnessOptions.questionTtlMs` (default 7 days; `0` or `null` for none). The worker setting `ENGINE_QUESTION_TTL_MS` and the HTTP, watch and worker wiring come with the follow-up unit.

## Compaction settings

`agent.compaction` holds per-conversation overrides (any subset, unknown keys rejected, `null` for none); `resolveCompactionSettings` layers them over the harness defaults (`DEFAULT_COMPACTION_SETTINGS`). A config that omits a field gets the default below, so existing conversations behave as before.

| Setting | Default | Meaning |
|---|---|---|
| `enabled` | `true` | compaction on; off means an overflow fails the run |
| `contextWindow` / `contextWindows` | `null` / `{}` | window in tokens, or by model id |
| `reserveTokens` | `16384` | tokens kept free for the reply |
| `backgroundMargin` | `16384` | a background compaction starts this far before the reserve |
| `keepRecentTokens` | `20000` | recent context kept verbatim after a compaction |
| `offloadTokens` | `0` (off) | tool results above this size go to the offload store |
| `clearTriggerTokens` | `0` (off) | old tool results are cleared from the view past this estimate |
| `keepToolResults` | `5` | most recent tool results never cleared |
| `clearAtLeastTokens` | `8000` | a clearing pass runs only if it frees at least this much |
| `excludeTools` | `[]` | tools whose results are never cleared |
| `structuredSummary` | `false` | use the sectioned summary prompt |
| `archiveSummarized` | `false` | save summarized history to the offload store |

`structuredSummary` and `archiveSummarized` are live (Tier 2, below); the clearing fields are live too (Tier 1).

### Tier 1: view-time clearing

With `clearTriggerTokens > 0`, stale tool results are replaced by a fixed stub in the context the model sees. Stored entries are never changed, and tool calls keep their results (pairing stays valid).

- **Watermark.** The conversation document `agent.context` holds `{ clearedThroughEntryId, clearedTokens }` (also in the watch view as `context`). `buildContext` stubs every `tool_result` at or before the watermark, except tools in `excludeTools`.
- **Advance.** The generation `request` phase advances the watermark when the context estimate is at or above `clearTriggerTokens` and the results that would newly be cleared (in context, older than the newest `keepToolResults`, not excluded) total at least `clearAtLeastTokens`. It writes the advance before building the context, only moves it forward, and a rerun after a crash plans nothing twice. Between advances the cleared prefix is byte-stable, which keeps the provider's prefix cache.
- **Stub.** `[cleared: <tool> result, ~<n> tokens. Re-run the tool.]`; when the result carries `details.offloaded.path` the stub ends `Re-run the tool, or read <path> if it was saved.]`.
- **Off.** With `clearTriggerTokens` 0 the watermark is ignored and results are verbatim (a one-time cache miss when switching); the stored watermark is kept, and turning clearing back on resumes from it.

### Tier 2: structured delta compaction

With `structuredSummary: true` the compaction task uses a sectioned prompt instead of the plain one (which stays the default and is unchanged). The summary has six sections, in this order: `## Goal and constraints`, `## Decisions`, `## Facts and IDs`, `## Files and artifacts`, `## Open tasks`, `## Errors and dead ends`.

- **Delta.** The request gives the previous summary's sections apart from the new transcript and tells the model to edit the sections in place. The goal section is carried forward byte for byte; if the model rewrites it, the previous text is kept and only the lines it did not have are appended.
- **Repair.** A heading the model leaves out is restored verbatim from the previous summary (or reads `(nothing recorded)` on the first compaction), so a bad summary can only lose detail from the new material. No second model call.
- **Archive.** With `archiveSummarized` and an offload store, the whole summarized range is written (unclipped) through `OffloadStore.put` with `kind: "history"` and the compaction task id as `toolCallId`; the summary gains `Full earlier transcript: <path>` (paths accumulate across compactions in structured mode). A store failure is reported (`offload`) and skipped.
- **Todo.** With `structuredSummary`, the open items of the todo are appended as `Open todo (as of the summary):`, folded from the previous summary's block and every `update_todo` call in the summarized range.
- **Quality.** `afterCompaction` events carry `quality { summaryChars, summarizedChars, ratio, missingHeadings }`; a ratio under 0.01 or a missing heading is also reported through `onError` as `compaction_quality`.
- The `compaction` entry payload is unchanged; the archive path and todo live inside the summary text.

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
| `ENGINE_WORKFLOW_BACKEND_URL` | `ENGINE_GATEWAY_URL` | backend origin of the workflow node gateway (`/internal/workflows/...`), authenticated with the gateway token. The production worker (`npm run worker`) exits 2 at startup when neither is set |
| `ENGINE_APPROVAL_TTL_MS` | `86400000` | local-mode approval expiry only (`0`: none); under `ENGINE_POLICY=backend` the backend's `approvalTtlMs` decides and this is ignored |
| `ENGINE_QUESTION_TTL_MS` | `604800000` | how long an `ask_user` question waits for an answer (`0`: none) |
| `ANTHROPIC_API_KEY` | none | model key (read per request; unused under `ENGINE_POLICY=backend`) |
| `ENGINE_POLICY` | `backend` if `ENGINE_POLICY_URL` is set, else `local` | `backend`: the backend resolves each model request (provider, model, tenant key, token budget, quota) and records usage; a definitive refusal denies the request, an outage is retried (`ENGINE_POLICY_RETRY_MS`) and then fails it as a retryable `policy_unavailable`; there is never a fallback to a local key |
| `ENGINE_POLICY_URL` | `ENGINE_GATEWAY_URL` when `ENGINE_POLICY=backend` | backend origin for `/internal/models/policy` and `/internal/models/usage`; uses `ENGINE_GATEWAY_TOKEN` |
| `ENGINE_POLICY_TIMEOUT_MS` | `5000` | timeout of one policy or usage call |
| `ENGINE_POLICY_RETRY_MS` | `60000` | total time a transient policy or usage failure is retried (`0`: never); see below |

**Tool policy.** Under `ENGINE_POLICY=backend` the worker's `HostPolicy.authorizeTool` holds no policy of its own: every tool call is decided by `POST /internal/tools/authorize` (same bearer token). Two cases never leave the process: a gateway tool whose manifest says `sideEffect: "none"` (a read) and a native tool in `INTERNAL_NATIVE_TOOLS` other than `ask_user` (which is sent so the question is indexed). Everything else sends `{ requestId: runId:toolCallId, tenantId, projectId, conversationId (root), engineConversationId, agentRole?, runId, toolCallId, toolTaskId, toolName, toolSource, arguments, argumentsHash, sideEffect?, replay, taint }`. `arguments` are redacted by `src/worker/redact.ts`; `argumentsHash` is the sha256 of the canonical JSON (sorted keys, `src/worker/args-hash.ts`, shared vectors in `test/support/args-hash-vectors.json`) of the unredacted final arguments, which is what an approval is bound to. The answer maps to `allow`, `block` (the backend's reason) or `requireApproval`, whose `expiresAt` is the injected clock plus the backend's `approvalTtlMs` (`null`: never expires). Transient failures retry like model policy (same `requestId`, `policy_retry`, `ENGINE_POLICY_RETRY_MS`); once the budget is spent, or at once on a 401/403/other 4xx or a malformed body, the call is blocked with reason `policy_unavailable` and the tool does not run. There is no local fallback. Arguments and response bodies are never logged. Without a backend (local development) the old rule applies: a gateway tool whose manifest sets `requiresApproval` requires approval with `ENGINE_APPROVAL_TTL_MS`.

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
  requiresApproval?, untrustedOutput? }] }`. Reads and idempotent writes are replay-safe.
  `untrustedOutput` (boolean, default `false`; any other type is rejected) marks a tool whose result taints the conversation.
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

## Workflow node task

`createWorkflowNodeTask({ gateway, onAbort? })` (`src/workflow/node-task.ts`) is the host task kind `workflow_node`; register it through `HarnessOptions.tasks`. `createNodeGateway(...)` (`src/workflow/node-gateway.ts`) is its HTTP client for the backend node gateway (`/internal/workflows/runs/{runId}/nodes/{nodeId}/prepare|finish`, `/runs/{runId}/finalize`, `GET /invocations/{key}`). The payload shapes are the committed schemas in `contracts/workflow-nodes/`; the contract the backend must reproduce is specified by `test/support/fake-workflow-backend.ts` (`[clause:]` tags).

Input: `{ version: 1, runId, runAttempt, nodeId, nodeType, policy: { maxRetries, backoffMs, timeoutMs, onError }, gateTaskId? }`. Every backend call carries `Idempotency-Key: {runId}:{nodeId}:r{runAttempt}:a{nodeAttempt}:{prepare|finish}`; a key with a stored outcome is answered without running again, so a retry (`nodeAttempt + 1`) or a run retry (`runAttempt + 1`) gets a new key. Phases:

| Phase | What happens |
|---|---|
| `prepare` | Ask the backend. `final` ends the node; `llm` creates one child conversation owned by the node (`requestId = node:{taskId}`) and waits in `awaitChild`; `wait` parks in `awaitWake` (`durationMs` is relative to the engine clock; a signal key and `slaUntilEpochMs` are honored); `fanout` stores the request digest, signals the gate task (`gateTaskId`, key `parked`) and parks in `awaitFanout` for the `fanout-result` signal. |
| `awaitChild`, `awaitWake`, `awaitFanout` | Gather the child's answer, the signal payload or deadline wake, or the counts-only fan-out summary and stage it for `finish`. |
| `finish` | The node calls the backend `finish` itself under its own `...:finish` key and settles on the `final` it returns. The run task never calls `finish`. |

Settling: `failed` retries while `policy.maxRetries` allows (a deadline wait of `backoffMs`); a fan-out node and an interrupted call never retry. Then `onError: "continue"` ends the task `done` with `{ status: "failed", continued: true }`, and `stop` faults it (so the run's `failFast` sees it). `timeoutMs` bounds each backend call of an attempt and ends it as `failed` with `error.code = "timeout"`. After a restart a `finish` is asked again only when the backend declared the call `replaySafe`; otherwise the engine reads the outcome stored under the key and, with none, settles the node `interrupted` (`failed`, `error.code = "interrupted"`). Checkpoints carry `version`; the previous version is upgraded in memory and a newer one faults the task with `unsupported_checkpoint_version`.

## Workflow run task

`createWorkflowRunTask({ gateway, onAbort? })` (`src/workflow/run-task.ts`) is the host task kind `workflow_run`: the DAG scheduler of one run. It spawns a `workflow_node` child per ready node and holds no node semantics. Input: `{ version: 1, runId, runAttempt, graph, variables, errorMode: "stop" | "continue" }`, where `graph` is the compiled `{ nodes: [{ id, type, deps: [{ from, handle? }], policy, handles? }] }` (`src/workflow/graph.ts`). `handles` lists what a branch node may select; an edge naming any other handle is invalid.

`POST /v1/runs` rejects a cycle, a duplicate id, an unknown dependency or handle, and more than 95 nodes (`maxNodes` in `contracts/workflow-nodes/limits.json`) with 400 before anything is spawned (the definition's `validateInput`).

| Phase | What happens |
|---|---|
| `start` | Resolve skips, spawn every ready node and park in `join`. |
| `join` | `waiting({ on: wave, policy })`, `failFast` for `stop` and `allSettled` for `continue`. When the whole wave is terminal: update node states from each child's committed outcome, apply `selectedHandles` and skip propagation, then spawn the next wave or move to `finalize`. Dispatch is in waves: a slow branch delays the later nodes of a fast one. |
| `finalize` | Call the backend `finalize` (key `{runId}:r{runAttempt}:finalize`) with `completed` or `failed`, the node summaries and the first uncaught error, then `done`. |

A node is ready when every in-edge source has settled and at least one edge is live. An edge is dead when its source was skipped or blocked, did not select the edge's handle, or failed without its own `onError: "continue"`; a node with only dead in-edges is `skipped`. Under `stop` the first uncaught failure aborts the siblings in flight (`sibling_failed`, recorded `skipped`) and the run ends `failed` with that error; under `continue` the failure is recorded, its descendants are skipped, and the run ends `failed` once everything has settled. Aborting the conversation aborts the node children first, then `onAbort` calls `finalize` once with `cancelled`. The run's `view` reports `nodes: { nodeId: status }` in `view.tasks`. Checkpoints hold node ids, statuses and task ids only, carry `version`, and follow the same previous-version rule as the node task.

### Fan-out, iteration and subworkflow

Register `createWorkflowFanoutLaneTask()` (`workflow_fanout_lane`) and `createWorkflowGateTask()` (`workflow_gate`) next to the node and run tasks. A graph may carry, besides `nodes`, `subgraphs` (iteration bodies by name) and `workflows` (referenced workflows by id), each a self-contained graph, and marks a node that may answer `prepare` with `fanout` as `mayFanOut: true`. The node limit counts every nested graph; `workflows` nest at most 3 deep (`maxSubworkflowDepth`) and a workflow cannot appear inside itself; all are rejected with 400 at `POST /v1/runs`.

- **Gate.** A wait wakes only when everything it awaits is terminal, so the run cannot learn from the node alone that it parked. For each `mayFanOut` node the run spawns a `workflow_gate` (`{nodeTaskId}:gate`) and waits on the gate. The gate ends when the node parks (it delivers `parked`) or ends for any reason, and faults when the node faulted, so `failFast` still reacts at once. On wake the run reads the node: terminal settles as usual; phase `awaitFanout` means fan-out.
- **Lanes.** For a parked node with `count` items and `parallel` P the run spawns `min(P, count, 50)` lanes (`{runTaskId}:r{runAttempt}:{nodeId}:lane{k}`). Lane `k` runs indexes `k, k+L, …` one at a time, each as a child `workflow_run` with the deterministic id `{runTaskId}:r{runAttempt}:{nodeId}:i{i}` and the backend run id `uuid5(runId, "{nodeId}:{i}")` (the item row the fan-out `prepare` created; `{runId}:{nodeId}:i{i}` for a run id that is not a UUID). Spawning the item and checkpointing `nextIndex` are one commit, so at most P items are live at any commit, also across a restart, and a finished item never runs again. A run retry (`runAttempt + 1`) fans out afresh.
- **Failure.** With `failFast` the first failed item ends its lane and aborts the sibling lanes (and their live items). Otherwise every item runs. An item child's own `finalize` writes its results to the backend.
- **Result.** When the lanes are terminal the run signals the node `fanout-result` with `{ count, completed, failed, cancelled, firstError? }` and waits for the node, which calls `finish` itself under its own key. A count over 1000 items or a target the graph lacks is answered with a failure summary and no lanes.
- **Bounded state (KTD17).** Checkpoints and signals hold counts and ids, never items or results: the run, lane and node checkpoints stay under 8 KiB and do not depend on the item count.

### Run retry, deadline and worker wiring

- **Retry.** `workflow_run` input may carry `retry: { max, delayMs }`. A run that ends `failed` with attempts left does not finalize: it parks on `waiting({ until: now + delayMs })` and restarts from `start` with a fresh checkpoint and `runAttempt + 1` (kept in the checkpoint's `attempt`). Node and lane task ids and node keys carry `r{runAttempt}`, so the retry re-executes nodes that failed in the earlier attempt and a fan-out starts afresh. `finalize` is called once, after the last attempt, with `attempts` (key `{runId}:r{attempt}:finalize`). A retry whose wake would not come before the deadline is not taken.
- **Deadline.** `deadlineMs` (absolute engine-clock epoch ms) bounds every wait of the run. When reached, live nodes, gates and lanes are aborted (reason `run_deadline`, recorded `skipped`) and the run finalizes `failed` with `{ code: "deadline_exceeded" }`; a deadline failure never retries.
- **Worker.** `startWorker({ workflow: { baseUrl, serviceToken } })` registers `workflow_run`, `workflow_node`, `workflow_fanout_lane` and `workflow_gate` (`createWorkflowTasks`, `src/workflow/tasks.ts`) with a node gateway client that carries the lease fence, so `/v1/health` `taskKinds` lists them. `scripts/worker.ts` takes the backend from `ENGINE_WORKFLOW_BACKEND_URL`, falling back to `ENGINE_GATEWAY_URL`.

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
