---
title: Fleet and Workflows on the Engine, One Run Model, Deletions and LiteLLM Removal - Plan
type: feat
date: 2026-10-17
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
execution: code
origin: reports/Oniva engine roadmap.md
---

# Fleet and Workflows on the Engine, One Run Model, Deletions and LiteLLM Removal - Plan

## Goal Capsule

- **Objective:** finish roadmap Phase 6 apart from Deep Work, which is already on the engine. Fleet background agents and workflows run as engine work. The UI gets one recursive run-tree and one status enum. The replaced Python run machinery is deleted. LiteLLM is removed from `backend/app`.
- **Authority, highest first:**
  1. This plan.
  2. `engine/README.md` (conversations, submissions, inbox, abort, view contract) and the task runtime contract in `engine/src/tasks/define.ts` (spawn, `waiting({ on, policy, signal, until })`, `onAbort`, `abandonOnRestart`). `engine/README.md` does not document child tasks, `allSettled`/`failFast` waits, deadline waits or `background`. They exist only in `define.ts`, `signals.ts` and `scheduler.ts`.
  3. `engine/test/support/fake-gateway.ts` (normative `[clause:]` tags). New contracts in this plan get the same treatment (U11).
  4. `docs/plans/2026-10-17-001-feat-engine-deep-work-rollout-plan.md` (the rollout). This plan assumes its U5, U6, U7 and U8 are merged or merge first where a unit says so.
  5. `docs/plans/2026-10-17-002-feat-approvals-and-policy-plan.md` (Phase 5). It owns the approval and question decision API, the approval ledger and backend migrations 110–119. This plan consumes them and never re-implements a decision route.
  6. `reports/Oniva engine roadmap.md` (Phase 6, Decisions, Risks).
- **Stop conditions:** stop and surface instead of guessing if:
  - the engine task runtime cannot carry a workflow run without a storage migration that conflicts with the lease or commit model (U3, U12);
  - a deletion would remove a route or table that something outside this repo still consumes (roadmap open question: "does anything outside this repo consume `pending` webhook executions?");
  - the p95 commit latency of a single engine session under the U36 load gate makes automation runs starve chat.
  - the engine task runtime cannot wake a `workflow_run` when a fan-out node parks (the park gate in U34 fails its spike), or fan-out lanes cannot hold the `parallel` bound across a restart.
- **Execution profile:**
  - One unit per agent, each in its own git worktree off `main`.
  - Units with disjoint files run in parallel. The Overlap matrix lists every shared file and the order in which to merge.
  - The coordinator verifies, merges and commits.
  - **Cross-plan merge order:** the rollout plan (001) first, then Phase 5 (002), then this plan (003). Files shared across plans are listed in the Overlap matrix.
  - **Migrations are allocated globally:** Phase 5 owns backend migrations 110–119, this plan owns 120–129 (KTD15). The coordinator rebases only `down_revision` when plans land out of order.
  - `engine/` is clean-room: no third-party pi or earendil code, names or identifiers. Run the repo's clean-room grep on `engine/` after every engine unit.
  - Deletions come after replacements. A unit that deletes code names the unit that replaced it in `Dependencies`.

---

## Product Contract

### Summary

Chat and Deep Work already run on the engine. Fleet and workflows still run inside the API process:

- `asyncio.create_task(run_background_agent(...))` in `backend/app/api/v1/background_jobs.py` and `backend/app/services/workflow_scheduler.py`.
- `WorkflowOrchestrator` and `DurableWorkflowEngine` loops in `backend/app/services/workflow_orchestrator.py` and `durable_workflow_engine.py`.
- In-process cancel flags (`_cancellation_flags` in the orchestrator) and a Redis intervene queue (`backend/app/services/intervention.py`).
- A boot sweep that fails "stale" jobs (`backend/app/services/startup_recovery.py`, called from `backend/app/main.py`).
- Seven modules that import `litellm`.

This plan moves that work onto the engine, replaces the UI's four run views with one, deletes the old machinery, and drops the dependency.

### Problem Frame

Current defects, all verified in code on 2026-10-17:

- **Workflow webhooks never run.** `WebhookTriggerService._trigger_workflow` (`backend/app/services/webhook_trigger_service.py`) inserts a `pending` `WorkflowExecution` that nothing consumes.
- **Webhook HMAC is weak.**
  - `_verify_auth` signs `json.dumps(body, separators=(',', ':'))` of the parsed body, not the raw bytes.
  - The secret is plaintext in `webhook_triggers.auth_config` (JSONB).
  - The idempotency window is a 24 h `webhook_deliveries` lookup.
- **Scheduler retries never run.** After a failure with retries left, `WorkflowSchedulerDaemon._execute_one` resets the row to `pending` with `retry_count + 1`. `_poll_once` selects only due `scheduled_workflows`, so nothing picks the retry up.
- **Waits are capped.**
  - `WaitExecutor` (`backend/app/node_executors/wait_executor.py`) rejects any wait over `MAX_IN_PROCESS_WAIT_SECONDS = 300`.
  - A restart loses an in-flight `asyncio.sleep`.
- **`/execute-batch` queues nothing.** `execute_batch` (`backend/app/api/v1/workflows.py`) inserts `WorkflowBatch` and `WorkflowBatchItem` rows and carries `# TODO: Queue background task for batch processing`.
- **Fleet cancel is cosmetic.**
  - `cancel_job` calls `job.cancel()` only.
  - `run_background_agent` keeps going and later overwrites the row's status.
  - The intervene queue is Redis with a 1 h TTL, consumed only between iterations.
- **A restart kills runs.**
  - `lifespan` in `main.py` runs `fail_stale_deep_work_jobs` at boot and at shutdown.
  - `recover_orphaned_executions` fails scheduled and manual executions older than their timeout.
  - There is no resume.
- **Workflows have no durable execution.** `DurableWorkflowEngine` writes `workflow_checkpoints` rows (`backend/app/models/workflow_checkpoint.py`, `services/checkpoint_cleanup.py`). Recovery is a manual `/recover` call that re-runs from a checkpoint inside the API process.
- **The LLM layer is split.** `litellm` is imported by:
  - `app/agents/base.py`, `app/agents/step_builder.py` (through `create_litellm_model`);
  - `app/services/llm_gateway.py`, `contextual_retrieval.py`, `rag_service.py` (`litellm.aembedding`);
  - `app/node_executors/{llm,classifier,guardrail,parameter_extractor,agent}_executor.py`;
  - `app/services/deep_work/engine.py` (removed by rollout U8).
  - `observability_api.py` mentions litellm only in a docstring. The real `completion_cost` call is `llm_gateway.py:734`.

### Requirements

- **R1.** A Fleet background-agent run executes as engine work. The API process holds no loop for it.
- **R2.** Operator intervention is an engine inbox steer. Cancel stops model rounds and tool calls, and the job's terminal status is never overwritten by a late completion.
- **R3.** A workflow run executes as an engine task tree: one task per node, with `allSettled`/`failFast` joins. An untaken IF/ELSE or classifier branch does not run. A failed node fails the run (unless the node's `try` adornment catches it).
- **R4.** A webhook trigger that is valid starts a real run. The HMAC covers the raw request bytes, secrets are encrypted at rest, and the idempotency key becomes the run's `requestId`.
- **R5.** Scheduled retries run after the configured delay, as an engine deadline wait, not a poll reset.
- **R6.** A wait node honours its configured duration up to its `maxWaitSeconds`, with no process holding the wait. A restart does not lose it.
- **R7.** `/execute-batch` runs the batch with the requested parallel limit, tracks counts, supports cancel, and notifies the optional webhook.
- **R8.** One-shot LLM nodes (LLM, classifier, guardrail, parameter extractor) and the agent node call models through the engine, so they use the backend model policy and write the usage ledger.
- **R9.** The UI renders Deep Work, Fleet and workflows with one recursive run tree and one status enum that includes crash-recovery states.
- **R10.** A rolling deploy of backend pods or engine workers loses no run. A killed worker resumes its runs from committed state.
- **R11.** The deletions list is done: `DurableWorkflowEngine`, `workflow_checkpoints`, `checkpoint_cleanup`, the boot mark-failed sweep, the in-process cancel maps, `dbos_workflow_uuid`, `workflow_approvals`, `webhook_endpoints`, `subworkflow_executions`, dead config keys and the bash tool. Each follows its replacement and ships with a migration where a table or column goes.
- **R12.** `grep -r litellm backend/app` is empty and `litellm` is out of `pyproject.toml`, `requirements.txt` and `uv.lock`.
- **R13.** No `asyncio.create_task` carries run work. A guard test fails on a new one.
- **R14.** Tenancy holds. Every engine-to-backend node call is bound to tenant, project and run. No provider key reaches an engine log. The engine stays free of business logic (roadmap risk "two runtimes").

### Acceptance Examples

- **AE1.** A scheduled background agent starts a run. The engine worker is `kill -9`ed mid-run and restarted. The run finishes exactly once. The `background_jobs` row is `completed`, and output delivery fires once.
- **AE2.** An operator intervenes during a run. The message appears as a steer in the next model request. The operator then cancels during a tool call. No further tool call starts, and the job ends `cancelled` and stays `cancelled`.
- **AE3.** A workflow with a condition node and two branches runs only the taken branch (the other is `skipped`). A parallel pair with `failFast` aborts the sibling when one fails. The run ends `failed` with the failing node's error.
- **AE4.** A webhook signed over the raw body, including whitespace and key order a re-serialization would change, is accepted. A one-byte change is rejected 401. The same delivery sent twice starts one run. The DB holds no plaintext secret.
- **AE5.** A scheduled workflow with `max_retries=2` and `retry_delay_seconds=60` fails once, waits 60 s (engine clock), reruns and completes. The execution shows `retry_count=1`.
- **AE6.** A workflow with a 2 h wait node is `waiting` with a wake time. An engine worker restart during the wait changes nothing. Advancing the test clock past the deadline completes the run.
- **AE7.** A batch of 25 inputs with `parallel_limit=5` never has more than 5 inputs executing at once. Counts match, and cancel stops the rest.
- **AE8.** The Fleet page, the workflow log view and the Deep Work card render the same `RunTree` with the same status badges, including `resuming` after a restart and `interrupted` for a tool call whose outcome is unknown.
- **AE9.** The backend is restarted during a workflow run that is calling an HTTP node. The node's external POST happens once, and the run completes.
- **AE10.** `grep -r litellm backend/app` returns nothing and the full suites are green with the dependency removed.

### Scope Boundaries

- **In scope:** units U1–U36. U33–U36 were added after U32 (KTD ids and unit ids are stable; nothing is renumbered).
- **Not in scope:**
  - Deep Work on the engine (done) and its rollout plan.
  - Approvals and policy (Phase 5). That includes the approval and question decision API, the ledger and the inbox. U21 only wires workflow wait nodes to them.
  - MCP, observability, evals and ops (Phase 7).
  - Rewriting the workflow builder canvas, or any node-schema change.

#### Deferred to Follow-Up Work

- Session sharding so each tenant's automation runs have their own engine worker. v1 uses `EngineDirectory.default_session_id` behind a `session_for` seam (U1). The load gate (U36, a unit that must pass before U29) decides whether sharding must ship first; building sharding is a separate plan.
- Moving `compliance.py` workflow runs off the orchestrator is in scope (U16). Moving compliance logic itself is not.
- Replacing pydantic-ai in the legacy chat and agent-studio loops. Those loops keep working, on native SDK models, until chat opt-out is retired.

---

## Planning Contract

### Key Technical Decisions

Naming: **the engine** is `@oniva/engine`. **Legacy** means the Python run machinery listed in Problem Frame. **Automation run** means a Fleet job or a workflow execution that runs on the engine.

- **KTD1 (R1, R3, R14). Scope and binding.**
  - Every automation run lives in a hidden per-tenant "Automations" project, because engine conversations require a project.
  - The run's conversation is stored as an archived `EngineConversation` row, as Deep Work does (`backend/app/api/v1/deep_work_engine.py` `_open_session`). This satisfies the model policy and usage ledger lookups (`internal_api/models.py::record_usage` 404s on an unknown conversation) and keeps runs out of chat lists.
  - Two nullable columns on `engine_conversations` map back to the domain row: `run_kind` (`fleet` or `workflow`) and `run_ref_id` (the `background_jobs.id` or `workflow_executions.id`).
  - The conversation id is deterministic: `uuid5(NAMESPACE, f"{run_kind}:{run_ref_id}")`. A repeated start is idempotent by construction (R4 idempotency, R10).
  - `EngineDirectory` gets a `session_for(tenant_id, run_kind)` seam that returns the default session today.
- **KTD2 (R1, R2). Fleet needs no new task kind.**
  - A Fleet run is an engine conversation plus one submission with `requestId = fleet:{job_id}`.
  - The engine has no `background` flag, and none is added. "Background" means a conversation-owned root run with no attached watcher. The existing conversation run, inbox steer (`whenBusy: "steer"`) and `abort` cover the rest.
  - The `CustomAgent` budgets map to agent config: `resource_constraints.max_tool_calls` becomes `maxRounds` and `timeout_seconds` becomes `deadlineMs`. Both are enforced by the engine (`run_error` codes `max_rounds`, `run_deadline`).
  - Steer replaces the Redis queue: `fleet.py::intervene_in_agent` submits with `whenBusy: "steer"`.
  - Cancel is `POST /v1/conversations/:id/abort`. Abort aborts tool tasks bottom-up, so no later completion exists to overwrite the row.
- **KTD3 (R3, R5, R6, R7). Workflows are two host-registered task kinds in the engine.**
  - `workflow_run`: the DAG scheduler. It owns a ready-set, spawns one `workflow_node` child per ready node, joins with `waiting({ on, policy })`, and finalises. `waiting` wakes on `on` only when every awaited task is terminal (`engine/src/tasks/signals.ts`, `dueWake`), so dispatch is in waves: the next nodes start when the whole spawned wave has settled. A slow branch therefore delays later nodes of a fast branch. This is accepted for v1 and listed under Risks.
  - `workflow_node`: one node. It runs `prepare`, then optionally `generate`, `wait` or `fanout`, then `finish`.
  - A `fanout` node stays parked in a waiting phase (`awaitFanout`). The run task executes the children (fan-out lanes, U34) and signals the node task, and the node task calls `finish` itself with its own idempotency key. The run learns that a node parked through a per-node gate task (U34).
  - Both live in `engine/src/workflow/`. Their payload shapes are the typed contract in U33. They contain no node semantics. Every node-specific step is an HTTP call to a backend node gateway (KTD4), the way tools are.
  - This needs a harness extension, `HarnessOptions.tasks`. `Harness.open` (`engine/src/agent/harness.ts`) registers only `generation`, `tool` and `compaction` today (U3).
- **KTD4 (R3, R8, R14). Backend node gateway.**
  - New `backend/app/internal_api/workflow_nodes.py`, mounted under `/internal/workflows/...`, with the same auth, fence and idempotency machinery as `/internal/tools` (`internal_api/invocations.py`: `begin`, `store_outcome`, `hold_write_fence`).
  - Calls are keyed `Idempotency-Key: {runId}:{nodeId}:r{runAttempt}:a{nodeAttempt}:{phase}`, where `phase` is `prepare` or `finish`.
    - `runAttempt` is the run-level retry counter (KTD9, U35). `nodeAttempt` is the node's own retry counter (`policy.maxRetries`).
    - A key with a stored outcome returns it without re-running. This is what makes AE9 hold.
    - Because `runAttempt` is in the key, a run retry re-executes a node that failed in run attempt 0 instead of replaying its stored failure.
    - The `finalize` call is keyed `{runId}:r{runAttempt}:finalize`.
  - Routes:
    - `POST /internal/workflows/runs/{runId}/nodes/{nodeId}/prepare`
    - `POST /internal/workflows/runs/{runId}/nodes/{nodeId}/finish`
    - `POST /internal/workflows/runs/{runId}/finalize`
  - `prepare` returns one of four shapes. U33 defines their JSON schemas and fixtures, and both the engine and the backend validate against them:
    - `final`: a `NodeExecutionResult`-shaped body (status, output, error, tokens, classification, `selectedHandles`).
    - `llm`: a generation request (system, prompt, tier or model, tools, `responseFormat`).
    - `wait`: `{ durationMs }` or `{ untilEpochMs }` and an optional signal key.
    - `fanout`: item count, a sub-graph or workflow id, a `parallel` limit, and `failFast`. It carries item counts and indexes only, never item payloads or results (KTD17).
  - `finish` receives the generation answer, the signal payload or a fan-out summary (counts and first error), and returns `final`.
  - The backend persists the trace step on every `final`, so the logs UI stays current without a separate event feed.
- **KTD5 (R3). Graph semantics move into a compiler plus a tiny scheduler.**
  - The backend compiles the React Flow spec once, at start, with `WorkflowValidator.validate_workflow`, into `{ nodes: [{ id, type, deps: [{ from, handle }], policy }], errorMode }`.
  - The engine schedules only from that compiled form:
    - A node is ready when every live in-edge's source is terminal.
    - A node whose in-edges are all dead becomes `skipped`.
    - An edge is dead if its source was skipped or did not select that handle.
    - Merge and aggregator nodes wait only on live inputs.
  - `errorMode` is `stop` (default, the current behaviour: first failure ends the run, via `failFast`) or `continue` (`allSettled`; used when a node's `try` adornment catches).
  - Adornments split:
    - `retry` and `timeout` map to node `policy` (`maxRetries`, `backoffMs`, `timeoutMs`), enforced by the engine node task.
    - `try` maps to `onError: "continue"`.
    - `cache` stays in the backend (`adornments.py`), inside `prepare`.
- **KTD6 (R8). One-shot LLM nodes are `prepare`/`generate`/`finish`.**
  - `prepare` builds the prompt from the node config and inputs and returns `kind: "llm"` with a tier (never a key).
  - `generate` runs it as a tool-less child conversation of the node task (the subagent pattern in `engine/src/agent/subagents.ts`: child conversation, submission `requestId = node:{taskId}`, wait on the child's answering task). Model choice, key and quota come from the existing model policy, and usage is recorded by the existing ledger path with `agentRole = "wf_node"` (the ledger regex `^[a-z0-9_]+$` allows it).
  - `finish` parses the answer (classifier label, guardrail verdict, parameter JSON) and returns `final`.
  - The `agent` node uses the same path with `tools` set, which replaces its `create_litellm_model` pydantic-ai loop.
  - Fallback if U11's child-conversation helper proves infeasible: a direct provider call through the policy and ledger (`llm_direct`, U27). The unit that finds this records it.
- **KTD7 (R6). Wait nodes are engine deadline waits.**
  - `duration` waits are relative: the engine computes `until = clock.now() + durationMs` when the node task enters `wait`.
  - `until_time` and `business_hours` are computed in the backend `prepare` (timezone logic stays in Python) and returned as an absolute epoch ms. Backend and worker clock skew is a stated risk (see Risks).
  - The existing `maxWaitSeconds` cap (default 7 days) stays. The 300 s in-process cap is deleted.
  - A human-in-loop node is `wait` with a signal key plus an optional SLA `until`.
  - The approval and question decision API belongs to Phase 5 (002): its ledger, RBAC (`ApprovalService.decide`) and decision route. U21 ships no decision route. It depends on Phase 5's decision route and adds only the workflow wait-node wiring: record a ledger row when the node parks, and register a post-decision hook that signals the node task (`EngineClient.signal_task`, created in U2).
- **KTD8 (R4). Webhooks.**
  - Verification runs on `await request.body()` (raw bytes), with constant-time compare and both `sha256=<hex>` and bare hex accepted, as today.
  - The secret is Fernet-encrypted with the existing `get_encryption_service()` (`app/core/encryption`), stored as `auth_config.secret_enc`. A data migration encrypts existing plaintext values and removes the plaintext key.
  - Idempotency key order:
    1. a configured header (`auth_config.idempotency_header`, default `Idempotency-Key`; GitHub's `X-GitHub-Delivery` is selectable);
    2. the `idempotency_key_path` JMESPath into the body;
    3. a SHA-256 of the raw body (the fallback).
  - Header and JMESPath keys are permanent: the run id is `uuid5(trigger.id, key)` and the `requestId` is `wh:{trigger.id}:{key}`. A replay at any time returns the same run.
  - The body-hash fallback is bucketed, because an identical body can legitimately recur (a periodic ping): the run id is `uuid5(trigger.id, key + ":" + window_bucket)`, where `window_bucket = floor(received_at_epoch_s / 86400)` (a 24 h bucket). A replay that straddles a bucket boundary starts a second run. That is documented behaviour, and a test pins it.
  - A deduplicated delivery returns 202 with `deduplicated: true` and the existing run's id. A first delivery returns 202 with `deduplicated: false`.
  - The trigger runs as `created_by`. A trigger without one is refused at create time, because the engine needs a user binding (`EngineConversation.user_id`). Existing triggers without one are backfilled by migration 121 (U20).
- **KTD9 (R5). Scheduler.**
  - The poll loop (`WorkflowSchedulerDaemon._poll_loop`, a `FOR UPDATE SKIP LOCKED` claim) stays. It is a claimer, not run work.
  - Dispatch starts an engine run whose id derives from the `scheduled_workflow_executions.id`.
  - Retries are the `workflow_run` root's own retry (U35): on failure with attempts left it enters `waiting({ until: now + retryDelayMs })` and re-runs from `start` with `runAttempt + 1`. Node idempotency keys include `runAttempt` (KTD4), so the retry re-executes nodes that failed.
  - `_semaphore`, `_active_tasks`, the `pending` reset and `recover_orphaned_executions` are deleted (U30). Concurrency stays the DB count `max_concurrent`.
- **KTD10 (R9). One status enum.**
  - The nine values are `queued`, `running`, `waiting`, `resuming`, `completed`, `failed`, `cancelled`, `skipped` and `interrupted`.
  - `waiting` covers an approval, a signal, a timer or a retry delay. `resuming` is a run the engine restarted after a worker restart, and it is cleared when the next progress entry lands. `interrupted` is a tool or node call whose outcome is unknown after a crash.
  - The mapping from legacy values lives in exactly two places, kept in lockstep by a shared fixture: `backend/app/services/run_status.py` and `frontend/lib/run/status.ts`.
- **KTD11 (R2, R10). Completion is pushed and reconciled.**
  - The engine has no run-end hook today (`AgentHooks` has only request and tool hooks).
  - Workflow runs finalise by the `finalize` call from the `workflow_run` task, which is durable because it is a task phase.
  - Fleet runs use a new `Harness.onSettled(listener)`. The worker's notifier posts `POST /internal/runs/finished` through a durable outbox (U5).
  - A leader-safe backend reconciler (`services/run_reconciler.py`) closes the crash window between settle and post by reading engine snapshots for automation rows stuck active. It replaces the boot "mark failed" sweep.
- **KTD12 (R11, R12). Rollout is staged and deletions are gated.**
  - New global flags `ENABLE_ENGINE_FLEET` and `ENABLE_ENGINE_WORKFLOWS`, with tenant settings under `features.*`, follow the Deep Work pattern in `backend/app/services/feature_flags.py` (`_engine_tenant_flag`).
  - Order: ship dark, tenant opt-in, soak, load gate (U36), flip to default (U29), then the deletion units (U30–U32).
  - In-flight legacy runs drain before their code is deleted. The ops gates are recorded in `DEPLOYMENT.md` by U29.
  - After deletion there is no legacy path. U30 and U31 remove the legacy branches, and a tenant whose flag is false (or a deployment without the capability, KTD16) gets 503 `engine_unavailable` on every run-start site. The soak gate in U29 requires that no tenant has either flag explicitly `false`, so the 503 reaches nobody on the day of deletion.
- **KTD13 (R12). LiteLLM replacement is native SDKs.**
  - `openai` and `anthropic` are already direct dependencies (`backend/pyproject.toml`). A new `backend/app/services/llm_direct.py` implements complete, stream and embed for provider types `openai`, `anthropic`, `openrouter`, `azure` and `custom` (`models/llm_provider.py`).
  - Cost comes from `app/config/llm_pricing.py` (`find_model_pricing`, `calculate_cost_with_cache`), never from a library estimate.
  - `LLMGateway.complete` and `.stream` keep their signatures, so the roughly 14 other callers are untouched. `create_litellm_model` becomes `create_model` (pydantic-ai native models, already half-native).
- **KTD14 (R13). The `create_task` guard.** A test walks `backend/app` with `ast`. Every `asyncio.create_task` or `ensure_future` must be in an allow-list file with a one-line reason. Allowed: the scheduler poll loop, observability fire-and-forget, doc-processing helpers, and the reconciler. Each is non-run work.

- **KTD15 (R10, R11). Global migration allocation.**
  - Backend: Phase 5 (002) owns 110–119 (currently 110 audit chain, 111 tool approvals, 112 deep work awaiting input, 113 retire review tickets). This plan owns 120–129 and uses exactly three:
    - `120_engine_runs.py` (U1), `down_revision` = the last Phase 5 migration at merge time (`113_retire_review_tickets` once Phase 5 has landed; `109_llm_usage_agent_role` if this plan merges first);
    - `121_encrypt_webhook_secrets.py` (U20), `down_revision = "120_engine_runs"`;
    - `122_drop_legacy_workflow_tables.py` (U32), `down_revision = "121_encrypt_webhook_secrets"`.
  - The coordinator rebases only `down_revision` (never the revision id, filename or body) when plans land out of order. `test_migrations.py` single-head assertions catch a fork.
  - Engine (`engine/migrations/NNN_name.sql`, applied in numeric order by `engine/src/storage/postgres/migrate.ts`): rollout U6 owns `002_usage_outbox.sql`; Phase 5 may add `003_*`; this plan's outbox generalisation (U5) is `004_outbox_kind.sql`. U5 verifies that `migrate.ts` accepts a gap at 003 and, if it does not, the coordinator renames to the next free number.
- **KTD16 (R10, R14). Capability gate and versioned checkpoints.**
  - Engine `GET /v1/health` (no auth) additionally returns `apiVersion` (an integer, starts at 1 for this plan) and `taskKinds` (the registered task kind names, e.g. `workflow_run`, `workflow_node`, `workflow_fanout_lane`, `workflow_gate`).
  - `engine_fleet_enabled` and `engine_workflows_enabled` (U2) additionally require the capability: a cached probe (TTL 60 s, failure cached for 10 s) of `/v1/health` must show `apiVersion >= REQUIRED_API_VERSION` and every kind the flag needs. A missing capability reads as flag off and logs once.
  - `workflow_run` and `workflow_node` checkpoints carry `version`. Each task decodes the previous version (`decodeCheckpoint(raw)` accepts `version - 1` and upgrades it in memory) so a worker rolled out mid-run still reads checkpoints written by the old one. Tests keep a golden previous-version checkpoint fixture.
  - Deploy order, documented in U29: engine first, then backend, then the tenant flag.
- **KTD17 (R7, R14). Bounded checkpoints.** Checkpoints and signal payloads hold identifiers, counts and statuses only. Per-item inputs, outputs and results are written to the backend (batch items in `workflow_batch_items`, other fan-out items as child `workflow_executions` rows with deterministic ids) and read back there. The fanout node's `prepare` creates the item execution rows (idempotently, by deterministic id). A test at 1000 items bounds checkpoint size (U34).

### Assumptions

- The rollout plan (`2026-10-17-001`) U5 (model timeouts), U6 (usage outbox), U7 (engine on by default) and U8 (legacy Deep Work removal) land before U5, U29 and U32 respectively. Where they have not merged, those units say what to do instead.
- Engine handler concurrency (`settings.concurrency`, default 16 in `harness.ts`) bounds simultaneous running handlers. A `waiting` task holds no slot, so long waits and fan-out joins are free.
- A single engine session (one worker) serves automation runs in v1. Fan-out is bounded per run by `parallel` (default 10, max 50) and per tenant by `max_concurrent`.
- `bash_tool.py` is already absent from the repo. Only `USE_BASH_TOOL` in `backend/app/config/__init__.py` and the `agents/tools/` package remain, and the prompt string at `services/session_memory.py:278` (verified by the research notes, re-grep in U32).
- Model-backed targets (verified in the repo): `workflow_checkpoints` is the model `backend/app/models/workflow_checkpoint.py` (exported from `models/__init__.py`, used by `durable_workflow_engine.py` and `checkpoint_cleanup.py`), and `workflow_executions.dbos_workflow_uuid` is a column on `models/workflow.py` (used by `durable_workflow_engine.py` and the `/execute-durable` response in `api/v1/workflows.py`). Both models and usages are deleted in U30 (the checkpoint model, its export, the column attribute and the response fields) and U31 where Fleet code is involved, so U32's migration drops database objects that no code references.
- `workflow_approvals`, `webhook_endpoints` and `subworkflow_executions` have no models. They exist only in databases upgraded from the legacy migration chain, because `100_baseline` builds from `Base.metadata` (`tests/postgres/DRIFT.md`). `_backup_orphaned_checkpoints_062` is also legacy-chain only. Their drops are `IF EXISTS`.
- Latest migration is `109_llm_usage_agent_role`. Phase 5 uses 110–113. This plan uses 120 (U1), 121 (U20) and 122 (U32) (KTD15).
- `MAX_WORKFLOW_NODES` is 95 (`backend/app/config/__init__.py:168`, enforced in `workflow_validator.py`). The engine's graph node limit equals it (U12, via the shared limits fixture from U33).
- `subworkflow_executor.py` is a stub today (it logs a warning and returns a fake result). Real subworkflows arrive as a side effect of `fanout` (U34).
- `waiting` wakes on `on` only when all awaited tasks are terminal, a signal key can be delivered once per task, and a signal that arrives before the wait is kept (`engine/src/tasks/signals.ts`). The workflow tasks are designed around those three facts.

### Deletions list

| Item | Replaced by | Deleted in |
|---|---|---|
| `backend/app/services/background_agent_executor.py`, `backend/app/services/intervention.py` | U7, U8 | U31 |
| `fail_stale_deep_work_jobs` and its two callers in `main.py` | engine recovery + U5 reconciler | U30 |
| `WorkflowSchedulerDaemon` run tasks (`_semaphore`, `_active_tasks`, pending reset, `recover_orphaned_executions`) | U19 | U30 |
| `WorkflowOrchestrator` run loop, `_cancellation_flags`, `_running_executions`, `cancel_execution` | U12, U15, U16 | U30 |
| `DurableWorkflowEngine`, `workflow_checkpoint.py`, `checkpoint_cleanup.py`, `/execute-durable`, `/recover`, `/replay`, `/checkpoints` | U12, U16, U17 | U30 |
| Models and usages: `models/workflow_checkpoint.py` and its export, `workflow_executions.dbos_workflow_uuid` attribute and `/execute-durable` response fields | U12, U16, U17 | U30 |
| Tables `workflow_checkpoints`, `_backup_orphaned_checkpoints_062`, `workflow_approvals`, `webhook_endpoints`, `subworkflow_executions`; column `workflow_executions.dbos_workflow_uuid` | n/a (no consumer once U30 lands) | U32 (migration 122) |
| Dead config: `USE_DURABLE_WORKFLOWS`, `DBOS_DATABASE_URL`, `USE_BASH_TOOL`, `STALE_JOB_THRESHOLD_SECONDS` (if no user remains) | n/a | U32 |
| `agents/tools/` bash remnant, `session_memory.py:278` prompt string | n/a | U32 |
| `litellm` imports, `create_litellm_model`, `_run_with_litellm_thinking`, dependency | U27, U28 | U28 |

---

## Phase map

| Phase | Units | Outcome |
|---|---|---|
| A. Foundations | U1–U5 | Run binding, flags and capability gate, engine host tasks, run view, completion push and reconciler registry |
| B. Fleet | U6–U10 | Fleet agents on the engine, steer, cancel, read model, recovery proof |
| C. Workflows | U11–U22, U33–U35 | Node contract, node and run tasks, fan-out, run retry and worker wiring, node gateway, LLM nodes, start/cancel/stream, batch, scheduler, webhooks, waits, proof |
| D. UI run model | U23–U26 | One status enum, one run tree, Fleet and workflow views on it |
| E. Flip, LiteLLM removal and deletions | U27–U32, U36 | Load gate, defaults flipped, litellm gone, legacy code and tables deleted |

U33–U36 were added after the original U1–U32. They keep their ids; no unit was renumbered. U12 was split: it keeps graph scheduling (linear, branch, join, finalize); fan-out, iteration and subworkflow moved to U34; run-level retry, deadline and worker wiring moved to U35.

### Dependency order

Unit dependencies (a unit starts only after every unit it lists is merged). "P5 Un" is a Phase 5 (002) unit and "R Un" a rollout (001) unit.

| Unit | Depends on |
|---|---|
| U1, U2, U3, U27, U33 | none |
| U4 | U3 |
| U5 | U1, U4, R U6 |
| U6 | U1, U2 |
| U7 | U1, U2, U5, U6 |
| U8 | U7 |
| U9 | U5, U7 |
| U10 | U8, U9 |
| U11 | U3, U4, U33 |
| U12 | U11 |
| U13 | U1, U2, U33 |
| U14 | U13 |
| U15 | U13, U35 |
| U16 | U5, U15 |
| U17 | U16 |
| U18 | U5, U15, U34 |
| U19 | U5, U15, U16 |
| U20 | U1, U7, U15 |
| U21 | U12, U13, P5 U2 (ledger and decision route) |
| U22 | U17, U18, U19, U20, U21 |
| U23 | U2, U4 |
| U24 | U23 |
| U25 | U8, U9, U24 |
| U26 | U17, U21, U24 |
| U28 | U14, U27, R U8 |
| U29 | U10, U22, U26, U36 |
| U30 | U29 (and its soak gates) |
| U31 | U25, U30 |
| U32 | U28, U30, U31 |
| U33 | none |
| U34 | U12 |
| U35 | U34 |
| U36 | U10, U22, U26 |

```
A: U1 ──┬─ U2
        └─ U3 ── U4 ── U5
B: (U1,U2) ── U6;  (U5,U6) ── U7 ──┬─ U8 ──┐
                                   └─ U9 ──┴─ U10
C: U33 ──┬─ U11 (U3,U4) ── U12 ── U34 ── U35 ──┐
         └─ U13 (U1,U2) ──┬─ U14               │
                          └──────────────────── U15 ── U16 ── U17 ──┐
                                  U15+U34+U5 ── U18 ───────────────┤
                                  U15+U16+U5 ── U19 ───────────────┤
                                  U15+U7+U1 ─── U20 ───────────────┼─ U22
                                  U12+U13+P5U2 ─ U21 ──────────────┘
D: U2+U4 ── U23 ── U24 ── (U25 needs U8,U9; U26 needs U17,U21)
E: U27 ── U28 (also needs U14, R U8)
   U10+U22+U26 ── U36 (load gate) ── U29 (flip) ── U30 ── U31 ── U32 (U32 also needs U28)
```

Parallel waves (each wave's units can run in separate worktrees at once, subject to the Overlap matrix merge order):

1. U1, U2, U3, U27, U33.
2. U4, U6, U13.
3. U5, U11, U14, U23.
4. U7, U12, U28.
5. U8, U9, U21, U24, U34.
6. U10, U25, U35.
7. U15.
8. U16, U18, U20.
9. U17, U19.
10. U22, U26.
11. U36.
12. U29.
13. U30, then U31, then U32 (strictly in sequence, each its own PR after its soak gates).

### Overlap matrix (files touched by more than one unit, built from every unit's Files list)

Cross-plan merge order: **rollout plan (001) first, then Phase 5 (002), then this plan (003)**. Within this plan, the order in each row follows the waves above. "P5" is a Phase 5 unit and "R" a rollout unit.

Backend:

| File | Units, in merge order |
|---|---|
| `backend/migrations/versions/` | P5 U1 `110_`, P5 U2 `111_`, P5 U7 `112_`, P5 U6 `113_`, then U1 `120_`, U20 `121_`, U32 `122_`. Coordinator rebases only `down_revision` |
| `backend/app/services/feature_flags.py` | R U7, U2, U29 |
| `backend/app/config/__init__.py` | U2, U32 |
| `backend/app/models/workflow.py` | U1 (adds `engine_conversation_id`), U30 (removes `dbos_workflow_uuid`) |
| `backend/app/services/engine_client.py` | U2 (creates `start_run`, `signal_task`, `get_task`, capability probe), U15 (payload helpers). P5 U2 only calls `decide`, unchanged |
| `backend/app/internal_api/router.py` | P5 U3 and U6, U5, U13 |
| `backend/app/internal_api/workflow_nodes.py` | U13, U16 (`finalize` route) |
| `backend/app/services/approval_service.py` (P5 U2) | P5 U2, U21 (additive decision-hook registry only, if P5 has not exposed one) |
| `backend/app/services/run_handlers.py` | U5 (creates registry and `bootstrap()`), U7, U9, U16, U18, U19 (each adds one module to `bootstrap()`) |
| `backend/app/services/workflow_engine_runs.py` | U15, U30 (`engine_unavailable` 503 helper) |
| `backend/app/services/fleet_engine.py` | U7, U31 (`require_engine_fleet`) |
| `backend/app/api/v1/background_jobs.py` | U7, U8, U9, U31 |
| `backend/app/api/v1/fleet.py` | U8, U9, U31 |
| `backend/app/api/v1/workflows.py` | U21 (waiting-nodes read route), U15, U18, U17, U30 |
| `backend/app/api/v1/workflow_execution.py` | U15, U17, U30 |
| `backend/app/api/v1/compliance.py` | U15, U30 |
| `backend/app/services/workflow_scheduler.py` | U7 (agent dispatch), U19, U30, U31 (legacy agent dispatch branch) |
| `backend/app/main.py` | P5 U7 (mounts inbox), U5 (reconciler loop), U30 |
| `backend/app/services/startup_recovery.py` | R U8 (keeps), U30 |
| `backend/app/node_executors/__init__.py` | U13, U14, U30 |
| `backend/app/node_executors/{llm,classifier,guardrail,parameter_extractor,agent}_executor.py` | U14, U28 (verifies), U30 (removes legacy wrappers) |
| `backend/app/node_executors/{wait,human_in_loop}_executor.py` | U21, U30 |
| `backend/app/services/llm_gateway.py` | U27, U28 |
| `backend/tests/internal/test_contract_conformance.py` | P5 U3 and U5, U13 |
| `backend/tests/contracts/streams.py`, `test_contracts.py` | U9, U16, U17, U31 |
| `backend/tests/test_engine_run_flags.py` | U2, U29 |
| `backend/tests/test_agent_scheduler.py` | U7, U19, U31 |
| `backend/tests/workflows/test_correctness.py` | U14, U15 |
| `backend/tests/e2e/backend_app.py`, `harness.py` | U10, U22 |
| `DEPLOYMENT.md` | R U7 and U9, U29, U32 |

Engine (cross-plan files marked):

| File | Units, in merge order |
|---|---|
| `engine/src/agent/harness.ts` | P5 U10 (`answerQuestion`, `questions`), U3 (`tasks` option), U4 (`onSettled`) |
| `engine/src/server/routes.ts` | P5 U10 (question answer route), U3 (`POST /v1/runs`, signal, task read, `/v1/health` capability fields) |
| `engine/src/worker/worker.ts` | R U5 and U6, P5 U5 (`workerHostPolicy`), U3 (host task pass-through), U5 (notifier), U35 (registers workflow task kinds) |
| `engine/src/worker/config.ts`, `engine/scripts/worker.ts`, `engine/test/worker/config.test.ts` | R U5 and U6, P5 U5 (`config.ts`), U35 |
| `engine/src/watch/view.ts` | P5 U10 (`questions`), U4 (`tasks`) |
| `engine/src/index.ts` | P5 U10, U3, U33, U11 |
| `engine/src/storage/*` | R U6 (outbox), U5 (`kind`, `payload`) |
| `engine/migrations/` | R U6 `002_usage_outbox.sql`, P5 `003_*` (only if it needs one), U5 `004_outbox_kind.sql` |
| `engine/src/workflow/run-task.ts` | U12, U34, U35 |
| `engine/src/workflow/graph.ts` | U12, U34 (sub-graph validation) |
| `engine/test/support/fake-workflow-backend.ts` | U11 (creates), U34 (fan-out item scripting), U35 (run-retry scripting) |
| `engine/README.md` | R U5 and U6, P5 U4, U5 and U10, U3, U4, U5, U11, U12, U34, U35 (each edits its own section) |

Not shared (single owner, listed so nobody reaches into them): `engine/test/support/fake-gateway.ts` (R U6 and P5 only), `engine/src/workflow/types.ts` and `engine/contracts/workflow-nodes/` (U33 only), `backend/tests/fixtures/run_status_map.json` (U2 creates, U23 reads), `engine/src/tasks/define.ts` (U4 only).

Frontend:

| File | Units, in merge order |
|---|---|
| `frontend/lib/engine/types.ts` | P5 U10 (`questions`), U23 (`tasks`) |
| `frontend/hooks/useFleetJobStream.ts` | U25, U31 |
| `frontend/lib/engine/__fixtures__/` | U4 (creates the golden view), U23 (reads) |

Playwright specs are single-owner: `frontend/e2e/fleet-engine-recovery.spec.ts` is U25's and `frontend/e2e/workflow-engine-recovery.spec.ts` is U26's. U10 and U22 are backend e2e only.


## Implementation Units

## Phase A. Foundations

### U1. Automation scope, run binding and migration 120

**Goal:** give every automation run a project, an archived engine conversation row and a domain-row link.
**Requirements:** R1, R3, R14.
**Dependencies:** none. Migration ordering: `120_engine_runs.py` chains after the last Phase 5 migration (KTD15).

**Files:**
- `backend/app/services/automation_scope.py` (new)
- `backend/app/models/engine.py` (`EngineConversation.run_kind`, `run_ref_id`)
- `backend/app/models/workflow.py` (`WorkflowExecution.engine_conversation_id`)
- `backend/migrations/versions/120_engine_runs.py` (new; `revision = "120_engine_runs"`, `down_revision` = the last Phase 5 migration at merge, `"113_retire_review_tickets"` once Phase 5 has landed, otherwise `"109_llm_usage_agent_role"`; the coordinator rebases only `down_revision`)
- `backend/app/services/project_service.py` (`list_projects` hides automation projects)
- `backend/app/services/engine_directory.py` (`session_for`)
- `backend/tests/internal/test_automation_scope.py` (new)

**Approach:**
- `ensure_automation_project(factory, tenant_id, owner_id)` finds the tenant's project whose `settings` contains `{"system": "automations"}`, or creates it once. The create is race-safe: an advisory lock keyed on the tenant, or a unique partial index.
- `bind_run_conversation(factory, kind, ref_id, tenant_id, user_id)` returns `(conversation_id, project_id, session_id)`.
  - The conversation id is `uuid5(RUN_NAMESPACE, f"{kind}:{ref_id}")`.
  - It inserts an archived `EngineConversation` with `run_kind` and `run_ref_id`, and is a no-op if the row exists.
- Migration 120 is idempotent (`ADD COLUMN IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS` on `(run_kind, run_ref_id)`), following `109_llm_usage_agent_role.py`.
- `list_projects` and the chat default-project resolution never return an automation project.

**Test scenarios:**
- First call creates the project. A second call and a concurrent call return the same one.
- `bind_run_conversation` called twice returns the same conversation id and writes one row.
- Two tenants get separate projects.
- The automation project is absent from `list_projects` for its owner, and `EngineConversation` rows with `run_kind` are absent from the chat conversation union.
- `session_for` returns `default_session_id`.
- Migration upgrade then downgrade then upgrade is clean on Postgres.

**Verification:** backend ruff and pytest green; migration test on Postgres.

### U2. Flags, capability gate, engine client run methods and the status mapper

**Goal:** the switches (gated on engine capability) and client calls every later backend unit uses. U2 is the only unit that creates `EngineClient.signal_task`.
**Requirements:** R1, R3, R9.
**Dependencies:** none (parallel with U1).

**Files:**
- `backend/app/config/__init__.py` (`ENABLE_ENGINE_FLEET`, `ENABLE_ENGINE_WORKFLOWS`)
- `backend/app/services/feature_flags.py` (`engine_fleet_enabled`, `engine_workflows_enabled`, into `get_tenant_features`)
- `backend/app/services/engine_client.py` (`start_run`, `signal_task`, `get_task`, `capabilities()` with a cached probe of `GET /v1/health`)
- `backend/app/services/run_status.py` (new)
- `backend/tests/test_engine_run_flags.py` (new)
- `backend/tests/internal/test_run_status.py` (new)
- `backend/tests/fixtures/run_status_map.json` (new; shared with U23)

**Approach:**
- The flags follow `engine_deep_work_enabled`. During this plan they are opt-in per tenant. U29 flips them to opt-out, the same way rollout U7 did.
- Capability gate (KTD16): `engine_fleet_enabled` and `engine_workflows_enabled` also require `EngineClient.capabilities()` to show `apiVersion >= REQUIRED_API_VERSION` and the task kinds the flag needs (`workflow_run`, `workflow_node`, and the U34 kinds for workflows; none for Fleet beyond the version). The probe is cached (60 s on success, 10 s on failure), never blocks a request for more than one short timeout, and treats an unreachable engine or a missing field as "capability absent" (flag off). The engine side lands in U3.
- `EngineClient.start_run(session, tenant, project, body)` posts `POST /v1/runs` (the route lands in U3) with `Idempotency-Key`. `signal_task(...)` posts the signal route.
- `run_status.py` exposes `normalize(legacy_value, source)`. It maps legacy job and execution values (`pending`, `running`, `completed`, `failed`, `cancelled`, `error`, `success`) to the KTD10 enum. `from_engine_task(status, wake, resumed)` maps task states.
- The fixture file is the single mapping table that both Python and TypeScript tests assert.

**Test scenarios:**
- Flag matrix: global off beats tenant on; tenant absent follows the opt-in default; `ENGINE_URL` or token missing disables.
- Capability matrix: health lacking `apiVersion`, an older `apiVersion`, or lacking a required task kind turns the flags off even when global and tenant are on. A newer engine passes. The probe result is cached (one health call for many flag reads) and a failed probe is retried after 10 s.
- `get_tenant_features` includes both new flags.
- Every legacy status string maps to exactly one enum value.
- An unknown string maps to `failed` and logs, never raises.
- The mapping JSON round-trips.
- `start_run` retries nothing itself, and maps engine 409 `not_owner` to `EngineError`.

**Verification:** backend ruff and pytest green.

### U3. Engine: host task kinds, run container route and task signals

**Goal:** let the worker register extra task kinds and start and signal them over HTTP.
**Requirements:** R3, R5, R6, R10.
**Dependencies:** none.

**Files:**
- `engine/src/agent/harness.ts` (`HarnessOptions.tasks?: readonly TaskDefinition[]`; registered after the built-ins; duplicate kind throws)
- `engine/src/server/routes.ts` (`POST /v1/runs`, `POST /v1/conversations/:id/tasks/:taskId/signal`, `GET /v1/conversations/:id/tasks/:taskId`; `GET /v1/health` also returns `apiVersion` and `taskKinds`, KTD16)
- `engine/src/server/http.ts` (wiring, only if the route table needs it)
- `engine/src/worker/worker.ts` (pass `hostTasks` through to `Harness.open`)
- `engine/src/index.ts` (export `defineTask`, `waiting`, `next`, `done` helpers if not already exported)
- `engine/README.md` (routes, `tasks` option)
- `engine/test/server/runs.test.ts` (new)
- `engine/test/tasks/host-tasks.memory.test.ts` and `engine/test/tasks/host-tasks.postgres.test.ts` (new, reuse `test/tasks/suite.ts`)

**Approach:**
- `POST /v1/runs` body: `{ id, kind, input, agent? }`.
  - It creates the container conversation with the tenant and project bound from headers (existing binding check). It uses the caller's `id` for a deterministic, idempotent create.
  - It spawns the root task of registered `kind` with `input`, conversation-owned.
  - A repeat of the same `id` returns the existing run (200) and spawns nothing. Unknown `kind` is 400. Response `201 { conversationId, taskId }`.
- The signal route calls `ops.signal(taskId, key, payload)`. `SignalRejected` maps to 404 or 409. A task from another conversation is 404.
- `GET .../tasks/:taskId` returns `{ id, kind, status, phase, outcome? }` for polling and reconcile.
- The host task kinds are the only thing the harness learns. No workflow logic enters the harness.
- `GET /v1/health` keeps `status`, `sessionId`, `ownerId`, `owner` and adds `apiVersion` (integer constant `ENGINE_API_VERSION`, 1 here) and `taskKinds` (sorted registered kinds, built-ins plus host kinds). It stays unauthenticated and carries nothing tenant-specific. Phase 5 (U10) merges first and adds its own routes in the same table; this unit appends.

**Test scenarios:**
- A test-only host task (two phases, a deadline wait) registered via `tasks` runs to `done` through `POST /v1/runs`.
- A repeat `POST /v1/runs` with the same `id` spawns once.
- A signal delivered to a task waiting on that key wakes it. A duplicate signal is rejected 409. A signal to a finished task is 409. A signal to another conversation's task is 404.
- An unknown task kind is 400, and a registry duplicate kind throws at open.
- `GET /v1/health` lists `apiVersion` and `taskKinds`; a host kind registered via `tasks` appears, and an engine without host tasks lists only the built-ins.
- Abort of the container conversation (`POST /v1/conversations/:id/abort`) aborts the host task and its children, and the task's `onAbort` runs.
- A worker restart with the task `waiting({ until })` resumes and wakes at the deadline (ManualClock in memory, shared test clock in the Postgres suite).

**Verification:** engine lint, typecheck and vitest green; clean-room grep empty.

### U4. Engine: `tasks` in the watch view and `Harness.onSettled`

**Goal:** clients see the task tree live, and the worker learns when a submission settles.
**Requirements:** R9, R10.
**Dependencies:** U3.

**Files:**
- `engine/src/tasks/define.ts` (optional `view?(task): TaskViewInfo` on `TaskDefinition`)
- `engine/src/watch/view.ts` (`ConversationView.tasks`)
- `engine/src/watch/patch.ts` (only if the diff needs a new path rule)
- `engine/src/agent/harness.ts` (`onSettled(listener)`)
- `engine/README.md` (the `tasks` member, `run_resumed` for host tasks)
- `engine/test/watch/view.tasks.test.ts` (new)
- `engine/test/agent/on-settled.memory.test.ts` (new)
- `frontend/lib/engine/__fixtures__/` (golden view with `tasks`, consumed by U23)

**Approach:**
- `view.tasks` is keyed by task id: `{ kind, parentId, status, phase, label?, attempt?, waitUntil?, startedAt, endedAt?, error? }`.
  - Only kinds whose definition supplies `view` appear. The view stays small for chat.
  - `parentId` links `workflow_node` to `workflow_run`.
  - `applyCommitToView` emits JSON Patch under `/tasks/<id>`, and view-at-N plus patches equals view-later (the existing invariant, with a test).
- The recovery hook `markResumedRuns` already appends `run_resumed` for the root conversation. Extend its interrupted-task walk so a host task in flight also produces one entry.
- `Harness.onSettled(listener)` fires after a submission settles (`done` or `unanswered`) with `{ conversationId, submissionId, requestId, status, reason? }`. It returns an unsubscribe function. A throwing listener is reported through `onError` and never affects settlement.

**Test scenarios:**
- A view loaded at seq N plus the events after N equals a view loaded later, with host tasks spawning, waiting and finishing.
- A chat conversation with no host tasks has `tasks: {}` and an unchanged snapshot.
- A worker restart mid-run adds exactly one `run_resumed` entry for a host-task run.
- `onSettled` fires once per settle, including `unanswered(aborted)`. It fires for a submission settled by startup orphan settlement. A throwing listener does not block the next submission.

**Verification:** engine lint, typecheck and vitest green.

### U5. Run-finished push, durable outbox, handler registry and the reconciler

**Goal:** the backend learns Fleet run completion without polling, a crash in between is still caught, and every kind of run registers its own recovery hooks against one registry.
**Requirements:** R1, R10, R13.
**Dependencies:** U1, U4, rollout U6 (outbox, `002_usage_outbox.sql`).

**Files:**
- `engine/src/worker/run-notifier.ts` (new)
- `engine/src/worker/worker.ts` (register the notifier on `onSettled`)
- `engine/src/storage/types.ts`, the Postgres and memory storage, `engine/src/storage/conformance.ts`, `engine/migrations/004_outbox_kind.sql` (generalise the usage outbox with `kind`, `payload`; rollout U6 owns `002_usage_outbox.sql`, Phase 5 may add `003`)
- `backend/app/internal_api/runs.py` (new: `POST /internal/runs/finished`)
- `backend/app/internal_api/router.py` (mount)
- `backend/app/services/run_handlers.py` (new: the registry interface and `bootstrap()`)
- `backend/app/services/run_reconciler.py` (new)
- `backend/app/main.py` (start and stop the reconciler loop)
- `engine/test/worker/run-notifier.test.ts` (new)
- `backend/tests/internal/test_run_finished.py` (new)
- `backend/tests/test_run_reconciler.py` (new)
- `backend/tests/test_run_handlers.py` (new)

**Approach:**
- The notifier fires only for conversations whose agent `metadata.notify === "backend"` (set by U7), so chat and Deep Work are untouched. It enqueues `{ kind: "run_finished", conversationId, submissionId, status, usage }` into the outbox, then posts with the existing retry policy and acks on 2xx.
- Handler registry (`run_handlers.py`). Each run kind registers hooks; hooks can be registered separately by different units for the same kind:
  - `register(kind, on_finished=…, start_missing=…, find_stuck=…, fail=…)`.
  - `on_finished(ref, snapshot)`: apply a settled engine run to the domain row. Idempotent. Never moves a terminal row.
  - `start_missing(ref) -> StartResult`: the domain row exists but the engine run was never started (a crash between row insert and `start_run`/`submit`). It re-issues the start with the same deterministic id and idempotency key, and returns `started`, `already_started` or `failed(error)`.
  - `find_stuck(session, older_than) -> list[ref]`: rows the reconciler should look at.
  - `fail(ref, code)`: terminal failure used when the engine conversation is missing or start retries are exhausted.
  - `bootstrap()` imports the modules that register hooks (`fleet_engine`, `fleet_run_sync`, `workflow_run_sync`, `workflow_batch_engine`, `scheduled_run_hooks`). Each later unit adds its module to that tuple.
  - Registrations by unit: U7 `fleet` `start_missing` and `find_stuck`; U9 `fleet` `on_finished`; U16 `workflow` `on_finished`; U18 `batch` `on_finished` and `start_missing`; U19 `scheduled` `start_missing`.
- `POST /internal/runs/finished` resolves `run_kind`/`run_ref_id` from `engine_conversations`, dispatches to the registered `on_finished`, is idempotent per `(run_ref_id, submissionId)`, and returns 200 even for an already-finished row.
- Reconciler:
  - Every 60 s, under `pg_try_advisory_lock`, it asks each registered kind's `find_stuck(older_than = RUN_RECONCILE_AFTER_S, default 120)`.
  - For a row with an engine conversation it reads the engine task and conversation snapshot and calls `on_finished`.
  - For a row with no engine run it calls `start_missing`. Retries are bounded: at most `RUN_START_RETRY_MAX` (default 5) attempts with exponential backoff from `RUN_START_RETRY_BASE_S` (default 30), the counter kept by the handler in a JSON column its row already has (verified in U7, U18 and U19). When the bound is reached the reconciler calls `fail(ref, "engine_start_failed")`.
  - A row whose engine conversation does not exist and that has no start hook is failed with `engine_run_missing`.
  - It is non-run work and is on the U30 guard allow-list.
- If rollout U6 has not merged, add the outbox table here and note the duplicate in the PR.

**Test scenarios:**
- Settled submission with the backend down: row stays pending, delivered once after recovery.
- A worker restart with a pending row delivers it after start.
- A conversation without the `notify` tag produces no outbox row.
- The finished handler called twice updates once.
- The reconciler settles a row whose finished post was lost, and fails a row with no engine conversation.
- Two reconcilers: the second takes no lock and does nothing.
- A row `updated_at` newer than the threshold is left alone.
- Registry: hooks registered by two separate calls for one kind are both used; registering the same hook twice raises; an unregistered kind is skipped with a log, not an error.
- `start_missing` (with a fake handler): called for a row with no engine run; a `started` result stops further calls; `failed` retries with backoff and stops at `RUN_START_RETRY_MAX`, after which `fail(ref, "engine_start_failed")` is called once.
- `bootstrap()` imports every listed module and registers each kind once (a kind that is missing its `on_finished` fails the test).
- Migration `004_outbox_kind.sql`: applies after `002` (and with a `003` present); existing usage rows get `kind = 'usage'`; the usage reporter keeps draining them.

**Verification:** engine and backend suites green.

## Phase B. Fleet

### U6. Fleet agent config builder

**Goal:** a pure function turns a `CustomAgent` into an engine agent config, and the prompt logic leaves the executor.
**Requirements:** R1, R2.
**Dependencies:** U1, U2.

**Files:**
- `backend/app/services/fleet_engine_agent.py` (new, patterned on `services/engine_deep_work_agent.py`)
- `backend/app/services/background_agent_executor.py` (re-export `_build_system_prompt`, `_load_tools`, `_preload_connector_summary` from the new module; no behaviour change)
- `backend/tests/internal/test_fleet_engine_agent.py` (new)

**Approach:**
- Move the pure parts: `_build_system_prompt`, the tool allowlist (`_load_tools`, `_SESSION_DEPENDENT_SKILLS`, `_BATCH_SUPERSEDES`), and the connector summary.
- Output: `{ systemPrompt, tools: [names], model?, tier?, maxRounds, deadlineMs, metadata: { notify: "backend", runKind: "fleet", jobId } }`.
  - `maxRounds` comes from `resource_constraints.max_tool_calls`. Defaults are the executor constants (`DEFAULT_MAX_TOOL_CALLS` 50, `DEFAULT_MAX_DURATION_SEC` 1800).
  - `deadlineMs` is an absolute epoch ms computed at start (the caller passes `now`).
  - Tools never include a name absent from the gateway manifest (`build_manifest()` in `internal_api/tools.py`). Unknown skills are dropped and listed in a returned `warnings`.
- No key, no tenant secret, no DB handle in the config.

**Test scenarios:**
- A session-dependent skill (`create_task`) is dropped; a batch skill suppresses its individual one.
- Budgets map to `maxRounds` and `deadlineMs`; absent constraints use defaults.
- A skill missing from the manifest produces a warning, not a failure.
- The config contains no `api_key`/secret-looking strings.
- The existing `test_background_agent_executor.py` still passes through the re-exports.

**Verification:** backend ruff and pytest green.

### U7. Start Fleet runs on the engine

**Goal:** create, scheduler and webhook dispatch start the engine, not an asyncio task.
**Requirements:** R1, R10, R13.
**Dependencies:** U1, U2, U5 (handler registry), U6.

**Files:**
- `backend/app/services/fleet_engine.py` (new: `start_fleet_run`; registers the `fleet` `start_missing` and `find_stuck` hooks)
- `backend/app/services/run_handlers.py` (add `fleet_engine` to `bootstrap()`)
- `backend/app/api/v1/background_jobs.py` (`create_job`)
- `backend/app/services/workflow_scheduler.py` (`_poll_agents_once`)
- `backend/app/services/background_dispatch.py` (no change except the docstring; the advisory-lock count stays)
- `backend/tests/test_fleet_engine_start.py` (new)
- `backend/tests/test_agent_scheduler.py` (extend)

**Approach:**
- `start_fleet_run(db, job, agent, user_id)`:
  - bind the conversation (U1);
  - create it with the U6 config;
  - submit the task text with `requestId = fleet:{job.id}` and `Idempotency-Key` the same;
  - set `job.conversation_id`, `job.session_id` and `input_data["engine"] = "oniva-engine"`;
  - set `status = running`, `started_at`.
- A failure to reach the engine leaves the job `pending` with `error` set. The `fleet` `start_missing` hook re-runs `start_fleet_run` for it; the attempt counter is `background_jobs.input_data["start_attempts"]`, and the bound and backoff are the registry's (U5).
- Gate each dispatch site on `engine_fleet_enabled(tenant)`. Off means the legacy `run_background_agent` path is untouched until U31.
- `_poll_agents_once` no longer calls `asyncio.create_task` for engine tenants.

**Test scenarios:**
- A manual create with the flag on calls `create_conversation` once and `submit` once with the expected `requestId`, and spawns no asyncio task.
- A repeated start (reconciler retry) does not create a second conversation or submission.
- The flag off keeps the legacy path.
- The scheduler dispatch for an engine tenant creates the job via `check_and_create_background_job` (concurrency limit still enforced) and starts the engine.
- Engine unreachable: the job stays `pending` with an error, and no exception escapes `_poll_agents_once`.
- Fleet start retry (registry hook): a `pending` job with no `conversation_id` is picked up by `find_stuck`, `start_missing` starts it exactly once, a second pass is `already_started`, and after the bound the job is `failed` with `engine_start_failed`.

**Verification:** backend ruff and pytest green.

### U8. Fleet steer and real cancel

**Goal:** intervene is a steer; cancel stops work.
**Requirements:** R2.
**Dependencies:** U7.

**Files:**
- `backend/app/api/v1/fleet.py` (`intervene_in_agent`)
- `backend/app/api/v1/background_jobs.py` (`cancel_job`)
- `backend/tests/test_fleet.py`, `backend/tests/test_intervention.py` (extend)
- `backend/tests/test_fleet_cancel_engine.py` (new)

**Approach:**
- This unit uses the existing `EngineClient.submit` (`whenBusy`) and `abort`; it adds no client method.
- For engine jobs, `intervene_in_agent` submits `{ text: message, whenBusy: "steer", requestId: "steer:{job}:{sha}" }`. The `InterventionResponse` shape is unchanged.
  - It still returns 404 when the job is not `running`.
  - Redis errors disappear for this path.
- For engine jobs, `cancel_job` calls `abort`, then sets the job `cancelled` with the reason. The finished handler (U9) never moves a terminal row, so a late settle cannot overwrite it.
- Legacy jobs keep the Redis path until U31.

**Test scenarios:**
- Intervene on a running engine job submits a steer once, with a stable `requestId` for a repeat.
- Intervene on a pending, finished or foreign-tenant job is 404.
- Cancel calls abort once. A second cancel is 400 `already completed`.
- A finished event arriving after cancel leaves the job `cancelled`.
- An engine outage on cancel still marks the job `cancelled` and records `abort_pending` in `input_data`. The U7 `find_stuck` hook returns `abort_pending` jobs, and the reconciler retries the abort through the same registered `fleet` hooks (U9 `on_finished` re-issues `abort` for a `cancelled` row whose engine run is still active).

**Verification:** backend ruff and pytest green.

### U9. Fleet read model: finished handler, trace and stream

**Goal:** `background_jobs` rows, the Fleet list and delivery reflect engine runs.
**Requirements:** R1, R2, R9, R10.
**Dependencies:** U5, U7.

**Files:**
- `backend/app/services/fleet_run_sync.py` (new: registers the `fleet` `on_finished` hook with the U5 registry; used by `/internal/runs/finished` and the reconciler)
- `backend/app/services/run_handlers.py` (add `fleet_run_sync` to `bootstrap()`)
- `backend/app/api/v1/background_jobs.py` (`get_job`, `stream_job_progress`)
- `backend/app/api/v1/fleet.py` (`list_fleet_runs`)
- `backend/app/services/output_gating.py` (no change to logic; called from the handler)
- `backend/tests/test_fleet_run_sync.py` (new)
- `backend/tests/contracts/fixtures/fleet_engine_completed.json`, `fleet_engine_cancelled.json`, `fleet_engine_failed.json` (new)
- `backend/tests/contracts/streams.py`, `test_contracts.py` (register the new streams)

**Approach:**
- The handler reads the conversation snapshot and sets:
  - `status` through `run_status.normalize`;
  - `result` (final answer);
  - `total_tokens` (the engine view `usage`);
  - `progress = 100`;
  - `execution_trace`, built from the assistant and tool entries into the existing `ExecutionTraceStep` shape, so `traceToMessageParts` keeps working until U25;
  - `completed_at`, `duration_ms`.
- It then calls `evaluate_and_deliver` once.
- A terminal row is never changed.
- `GET /background-jobs/{id}/stream` for an engine job proxies the engine watch stream (`EngineClient.watch_stream`) and translates frames to the existing job SSE events. The new UI (U25) reads the engine watch directly through the existing `/engine/conversations/{id}/watch` proxy and ignores this route.

**Test scenarios:**
- A done submission sets `completed`, result, tokens, trace steps in order, and delivers once.
- A run with `run_error max_rounds` sets `failed` with a readable message.
- An aborted submission leaves a `cancelled` job `cancelled`.
- The handler called twice delivers once.
- Registry: `fleet` has an `on_finished` registered after `bootstrap()`; the reconciler calling it for a stuck `running` row whose engine run is settled updates the row once.
- Contract fixtures match the live output (`ONIVA_RECORD_CONTRACTS=1` to record, reviewed in the diff).

**Verification:** backend ruff and pytest green.

### U10. Fleet end-to-end recovery proof

**Goal:** prove AE1 and AE2 against a real engine process.
**Requirements:** R1, R2, R10.
**Dependencies:** U8, U9.

**Files:**
- `backend/tests/e2e/test_fleet_engine_recovery.py` (new)
- `backend/tests/e2e/backend_app.py`, `dw_script.py`/`dw_fakes.py` (extend with a Fleet script and fake search tool)

The Playwright spec `frontend/e2e/fleet-engine-recovery.spec.ts` is owned by U25 so that it is written next to the UI it drives. U10 is backend e2e only.

**Approach:** follow `test_deep_work_engine_recovery.py`: scripted fake model, real worker process, `kill -9` mid-run via the harness in `tests/e2e/harness.py`.

**Test scenarios:**
- Kill the worker mid-run, restart, the run completes once, one delivery, one ledger row per model request.
- Steer during a run reaches the next model request.
- Cancel during a long tool call: no later tool call starts, and the job stays `cancelled`.
- The backend restarts mid-run: nothing is lost.

**Verification:** `ONIVA_E2E=1 uv run pytest -m e2e tests/e2e` green.

## Phase C. Workflows

### U33. Workflow node contract: schemas and fixtures

**Goal:** one typed, versioned definition of the node gateway payloads (the four `prepare` shapes, the `finish` input, `finalize` and the shared limits), checked from both the engine and the backend, so U11 and U13 can be built in parallel against it. Added after U32; the id is stable.
**Requirements:** R3, R8, R14.
**Dependencies:** none. U11 and U13 depend on this unit.

**Files:**
- `engine/contracts/workflow-nodes/prepare.final.schema.json`, `prepare.llm.schema.json`, `prepare.wait.schema.json`, `prepare.fanout.schema.json`, `prepare.response.schema.json` (discriminated union on `kind`: `final`, `llm`, `wait`, `fanout`)
- `engine/contracts/workflow-nodes/finish.input.schema.json`, `finalize.input.schema.json`, `limits.json` (`{ "maxNodes": 95, "maxParallel": 50, "maxFanoutItems": 1000, "maxSubworkflowDepth": 3 }`; `maxNodes` mirrors `MAX_WORKFLOW_NODES` in `backend/app/config/__init__.py`)
- `engine/contracts/workflow-nodes/fixtures/` (valid and invalid examples for every shape, plus a golden previous-version checkpoint used by U11/U12)
- `engine/src/workflow/types.ts` (new: `PrepareResult`, `FinishInput`, `NodePolicy`, `FinalizeInput`, `CONTRACT_VERSION`)
- `engine/src/workflow/contract.ts` (new: zod validators for the same shapes; exported for U11/U12)
- `engine/src/index.ts` (type exports)
- `engine/test/workflow/contract.test.ts` (new)
- `backend/app/internal_api/workflow_contract.py` (new: pydantic models for the same shapes)
- `backend/tests/internal/test_workflow_contract.py` (new)

**Approach:**
- The committed JSON schemas and fixtures are normative. Each side proves it agrees with them; neither generates the other's code.
- Shapes (camelCase JSON, `contractVersion: 1` on every body):
  - `final`: `{ kind: "final", status: "success"|"failed"|"blocked"|"skipped", output, error?, tokensUsed, classification?, selectedHandles?: string[] }`.
  - `llm`: `{ kind: "llm", system, prompt, tier?, model?, tools?: string[], maxRounds?, responseFormat?: "text"|"json", replaySafe: boolean }`. A tier or model is a hint only; no key.
  - `wait`: exactly one of `durationMs` or `untilEpochMs`, plus optional `signal` and optional `slaUntilEpochMs`.
  - `fanout`: `{ kind: "fanout", count, parallel (1..maxParallel), failFast, target: { subgraph: string } | { workflowId: string }, itemsRef }`. It carries a count and a backend reference, never item payloads (KTD17).
  - `finish` input: `{ kind: "llm"|"wait"|"fanout", answer? , signalPayload?, wakeReason?: "signal"|"deadline", fanout?: { count, completed, failed, cancelled, firstError? } }`. The output of `finish` is a `final`.
  - `finalize` input: `{ status, nodeSummaries: { nodeId: status }, error?, attempts }`.
- `limits.json` is read by the U12/U34 engine tests and the backend validator test, so the node limit cannot drift apart.
- Each body is size-capped by the validators (default 64 KiB) so a payload can never turn into an unbounded checkpoint.

**Test scenarios:**
- Every valid fixture validates in the engine (zod) and the backend (pydantic); every invalid fixture is rejected by both, with a stable error code.
- Unknown `kind`, a `wait` with both or neither duration fields, a `fanout` with `parallel` over `maxParallel`, and a body over the size cap are each rejected.
- Property parity: every property and enum value named in a committed schema exists in the matching zod validator and pydantic model (a drift test fails when one side adds or drops a field).
- `limits.json.maxNodes` equals `settings.MAX_WORKFLOW_NODES` (backend test).
- The previous-version checkpoint fixture decodes (consumed by U11/U12 tests).

**Verification:** engine lint, typecheck and vitest green; backend ruff and pytest green; clean-room grep empty.

### U11. Engine: workflow backend fake and the node task

**Goal:** the `workflow_node` task, its HTTP client, and the normative fake of the node gateway.
**Requirements:** R3, R5, R6, R8.
**Dependencies:** U3, U4, U33 (payload contract).

**Files:**
- `engine/src/workflow/node-gateway.ts` (new: HTTP client, fence headers, retry and `Retry-After` on the pattern of `src/tools/gateway.ts`; validates every response with `contract.ts`)
- `engine/src/workflow/node-task.ts` (new: `workflow_node` `TaskDefinition`)
- `engine/src/index.ts` (export the task definition)
- `engine/test/support/fake-workflow-backend.ts` (new; normative TSDoc with `[clause:]` tags, like `fake-gateway.ts`; serves the U33 fixtures)
- `engine/test/workflow/node-task.suite.ts`, `node-task.memory.test.ts`, `node-task.postgres.test.ts` (new)
- `backend/tests/internal/test_contract_conformance.py` (matching clause tests, added in U13)
- `engine/README.md`

**Approach:** input `{ version: 1, runId, runAttempt, nodeId, nodeType, policy, gateTaskId? }`. The checkpoint carries `version` and `decodeCheckpoint` accepts the previous version (KTD16). Phases and transitions:
- Idempotency keys: `{runId}:{nodeId}:r{runAttempt}:a{nodeAttempt}:{phase}` with `phase` of `prepare` or `finish`. The key is both the `Idempotency-Key` header and the backend's `begin` key.
- `prepare`:
  - `final` → `done(result)`.
  - `llm` → spawn a tool-less child conversation (KTD6), then `waiting({ on: [childAnsweringTask] })` in phase `finish`.
  - `wait` → `waiting({ until })` (duration relative to `clock.now()`), plus `signal` if given, in phase `finish`.
  - `fanout` → store the fan-out request digest (count, parallel, failFast, target) in the checkpoint, set phase `awaitFanout`, signal the run's gate task (`input.gateTaskId`, key `parked`, payload = the digest; skipped when no gate id is given, as in unit tests), and `waiting({ signal: "fanout-result" })`. The node stays parked. The run task (U34) executes the children and delivers `fanout-result` with a counts-only summary.
- `awaitFanout` wake → phase `finish`. The node task itself calls the backend `finish` with its own key (`...:finish`) and the summary, and ends `done`. The run task never calls `finish` for a node.
- `finish`: reads the child's committed answer, the signal payload, the fan-out summary or the deadline wake, calls the backend `finish`, and ends `done`.
- Policy:
  - A node call that is replay-safe (declared in the prepare response) is re-run on recovery.
  - Otherwise it is reconciled by the idempotency key, like write tools. If the outcome is unknown, the result is `interrupted`.
  - `retry` (`maxRetries`, `backoffMs` via a deadline wait) and `timeoutMs` apply per attempt, and each retry increments `nodeAttempt` (a new key). A `fanout` node never retries (the compiler sets `maxRetries = 0`; the task ignores a nonzero value for that kind).
  - `onError: continue` converts a failed final to a recorded failure the run task treats as settled.
- All phases tolerate re-run (the `define.ts` contract).

**Test scenarios** (memory and Postgres, same suite):
- A `final` prepare completes the node once. A worker restart between prepare and commit re-asks the backend with the same key and gets the stored outcome.
- Keys: the fake backend rejects (`[clause:idempotency-key-format]`) any key not matching `{runId}:{nodeId}:r{n}:a{n}:{phase}`; two runs differing only in `runAttempt` produce different keys.
- An `llm` prepare spawns exactly one child conversation (no duplicate after a restart), and the answer reaches `finish`.
- A `wait` of 2 h completes when the ManualClock passes the deadline, not before.
- A `wait` with a signal key wakes on the signal.
- Retry: failure, backoff wait, success on attempt 2 with a new key (`a1`).
- `timeoutMs` ends an in-flight call as `failed(timeout)`.
- `abort` during `wait` ends the node `aborted` and runs `onAbort`.
- A non-replay-safe node cut off mid-call yields `interrupted` when the backend has no stored outcome.
- The abort path aborts the child conversation first (bottom-up).
- Fan-out park: a `fanout` prepare leaves the node `waiting` in phase `awaitFanout` (not `done`) and signals the gate once; a restart while parked does not call `prepare` again; delivering `fanout-result` wakes it, and `finish` is called exactly once with the node's own `...:finish` key; a duplicate `fanout-result` is rejected; abort while parked ends it `aborted`.
- Checkpoints: a golden previous-version checkpoint fixture decodes and the task completes; a checkpoint with an unknown newer version fails the task with `unsupported_checkpoint_version`.

**Verification:** engine lint, typecheck and vitest green; clean-room grep empty.

### U12. Engine: the workflow run task and DAG scheduler (graph, linear, branch, join, finalize)

**Goal:** `workflow_run` schedules a compiled graph with joins, branch skipping and finalise. Fan-out is U34 and run-level retry, deadline and worker wiring are U35.
**Requirements:** R3.
**Dependencies:** U11.

**Files:**
- `engine/src/workflow/run-task.ts` (new)
- `engine/src/workflow/graph.ts` (new: ready-set, skip propagation, branch handles, validation)
- `engine/README.md` (task kinds, view `tasks` fields)
- `engine/test/workflow/graph.test.ts`, `engine/test/workflow/run-task.suite.ts`, `run-task.memory.test.ts`, `run-task.postgres.test.ts` (new)

**Approach:**
- Input: `{ version: 1, runId, runAttempt: 0, graph, variables, errorMode }`. The checkpoint carries `version` with a decode path for the previous version (KTD16).
- Phases:
  - `start`: spawn every ready node as a `workflow_node` child (the owning task is the run).
  - `join`: `waiting({ on: spawnedWave, policy })`, where `policy` is `failFast` for `errorMode: stop` and `allSettled` for `continue`. The wait wakes when the whole wave is terminal, so dispatch is in waves (see KTD3). On wake, read each child's committed outcome, update node states in the checkpoint, apply branch selection (`selectedHandles`) and skip propagation, then spawn the newly ready nodes or finalise.
  - `finalize`: calls the backend `finalize` (idempotent, key `{runId}:r{runAttempt}:finalize`) with the status, node summaries and error, then `done`.
- A node outcome of kind `fanout` is not handled here. Until U34 lands the run fails that node with `fanout_unsupported`, and U34 replaces that branch.
- Node states are visible through U4's `view.tasks` (the run's own `view` fn adds a compact `nodes` summary: `{ nodeId: status }`).
- Graph validation (`graph.ts`): cycle, unknown dependency, unknown handle, and a node count above the limit are rejected at `POST /v1/runs` with 400 and nothing spawned. The limit is `maxNodes` from `limits.json` (U33), which equals `MAX_WORKFLOW_NODES = 95` (`backend/app/config/__init__.py:168`).

**Test scenarios** (shared memory and Postgres suite, scripted fake workflow backend):
- Linear 3-node graph runs in order and finalises `completed`.
- Diamond (fork then join): both branches run concurrently, the join node runs once, after both.
- Condition node selecting handle `true`: the `false` branch is `skipped`, its descendants skipped, and a merge node with one live input still runs.
- `failFast`: one branch fails, the sibling in flight is aborted (`siblingFailed`), and the run is `failed` with the failing error.
- `continue` mode: the failure is recorded, the other branch completes, the run is `failed` at the end with all settled.
- Wave dispatch: with one slow and one fast branch, the fast branch's successor starts after the slow node settles (pins the documented behaviour).
- A restart in the middle of a join yields no duplicate node `prepare` effects (the backend stub counts keys).
- Abort of the container conversation ends all children `aborted` and `finalize` is called once with `cancelled`.
- A cycle, an unknown dependency, or 96 nodes is rejected 400 at `POST /v1/runs`, nothing spawned; 95 nodes is accepted.
- A node returning `fanout` fails with `fanout_unsupported` (until U34).
- Checkpoints: the golden previous-version run checkpoint decodes and the run completes.

**Verification:** engine lint, typecheck and vitest green; clean-room grep empty.

### U34. Engine: fan-out, iteration and subworkflow

**Goal:** a parked `fanout` node is served by the run task through bounded fan-out lanes, with checkpoints that do not grow with the item count. Added after U32; the id is stable.
**Requirements:** R3, R7.
**Dependencies:** U12.

**Files:**
- `engine/src/workflow/run-task.ts` (park handling, gate and lane spawn, node signalling)
- `engine/src/workflow/fanout-lane-task.ts` (new: `workflow_fanout_lane` task)
- `engine/src/workflow/gate-task.ts` (new: `workflow_gate` task)
- `engine/src/workflow/graph.ts` (sub-graph validation and node counting across nested graphs)
- `engine/test/support/fake-workflow-backend.ts` (fan-out item scripting; extends U11's fake)
- `engine/test/workflow/fanout.suite.ts`, `fanout.memory.test.ts`, `fanout.postgres.test.ts`, `checkpoint-size.test.ts` (new)
- `engine/README.md`

**Approach:**
- Park gate. `wait` on `on` wakes only when every awaited task is terminal and a signal key can be delivered once (`signals.ts`), so the run cannot learn from `on` alone that a node parked. For each node the compiled graph marks `mayFanOut`, the run spawns a `workflow_gate` task next to the node (id `{nodeTaskId}:gate`) and puts the gate, not the node, in the wave's `on` list. The gate does `waiting({ on: [nodeTaskId], signal: "parked" })`: it wakes when the node parks (the node delivered `parked` with the digest, U11) or when the node ends for any reason, then ends `done`. On wake the run reads the node: terminal → normal outcome; parked → start fan-out.
- A spike at the start of this unit confirms that a task handler can deliver a signal to another task (`ops.signal`). If it cannot, this unit stops and surfaces (Stop conditions).
- Lanes. For a parked node with `count` items and `parallel` P, the run spawns `min(P, count, maxParallel)` `workflow_fanout_lane` children. Lane `k` handles indexes `k, k+P, k+2P, …` strictly one at a time. For index `i` it starts a child `workflow_run` with the deterministic id `{runTaskId}:r{runAttempt}:{nodeId}:i{i}` (iteration: the sub-graph; subworkflow: the referenced workflow's compiled graph, from `target.workflowId`), waits on it, records the item's status, and moves on. Because at most one child per lane is live, at most `parallel` children are live at any commit, and a restart resumes each lane from its checkpointed `nextIndex` without exceeding the bound or re-running a finished item.
- The run waits `{ on: lanes, policy }` with `failFast` or `allSettled` taken from the fan-out request. Lanes return counts only (`{ completed, failed, cancelled, firstError? }`). When all lanes are terminal the run sums them and delivers `fanout-result` to the node task with that summary. The node task (U11) then calls `finish` itself with its own key and ends `done`. The run never calls `finish` for a node.
- Results never pass through checkpoints. An item child's `finalize` writes the item's results to the backend (batch items in `workflow_batch_items`, other items as child `workflow_executions` rows with deterministic ids, KTD17), and the backend's `finish` aggregates them from there. The fanout `prepare` (U13, U18) has already created the item execution rows.
- Limits: `parallel` is clamped to `maxParallel` (50); `count` above `maxFanoutItems` (1000) is rejected by the validator; the total node count across nested graphs is capped by `maxNodes` (95); subworkflow nesting is capped by `maxSubworkflowDepth` (3). Child ids include `runAttempt`, so a run retry (U35) fans out afresh.

**Test scenarios** (shared memory and Postgres suite, scripted fake workflow backend):
- Fan-out of 25 items with `parallel: 5`: at most 5 child runs live at any commit (assert via a commit-event observer), all 25 items run once, and the node's `finish` receives `{ count: 25, completed: 25 }`.
- A restart in the middle of fan-out keeps the bound and does not re-run a finished item (the stub counts item keys).
- `failFast: true`: one item fails, the other lanes' in-flight children are aborted, the node's `finish` receives the failure, and the run fails. `failFast: false`: all items settle and the summary counts failures.
- Gate: a node that parks wakes the run (no hang); a `mayFanOut` node that returns `final` without parking also wakes the run through the gate's `on` branch; a node that fails before parking wakes the run.
- The node task is still `waiting` while lanes run, and `finish` is called exactly once with the node's own `...:finish` key, never by the run.
- Subworkflow: `target.workflowId` runs the referenced graph once; nesting beyond depth 3 is rejected; a recursive reference is rejected.
- Iteration of 1000 items at `parallel: 10`: completes, and the checkpoint size of the run, every lane and the node task is bounded (see next).
- Checkpoint size: serialise the run, lane and node checkpoints and the `fanout-result` signal payload for N=10 and N=1000; each stays under 8 KiB, and the N=1000 size exceeds the N=10 size by under 1 KiB (size does not depend on the item count). No item payload or result appears in any checkpoint or signal.
- 1001 items is rejected 400; `parallel` of 500 is clamped to 50.
- Abort of the container conversation aborts lanes, children and the parked node bottom-up, and `finalize` is called once with `cancelled`.

**Verification:** engine lint, typecheck and vitest green; clean-room grep empty.

### U35. Engine: run-level retry, deadline and worker wiring

**Goal:** the run retries as a whole on a deadline wait, honours a run deadline, and the worker registers the workflow task kinds. Added after U32; the id is stable.
**Requirements:** R5, R10.
**Dependencies:** U34.

**Files:**
- `engine/src/workflow/run-task.ts` (retry and deadline)
- `engine/src/worker/config.ts`, `engine/src/worker/worker.ts`, `engine/scripts/worker.ts` (`ENGINE_WORKFLOW_BACKEND_URL`, default `ENGINE_GATEWAY_URL`; register `workflow_run`, `workflow_node`, `workflow_fanout_lane` and `workflow_gate` via `hostTasks`)
- `engine/README.md` (env row, task kinds)
- `engine/test/workflow/run-retry.suite.ts`, `run-retry.memory.test.ts`, `run-retry.postgres.test.ts` (new)
- `engine/test/worker/config.test.ts`, `engine/test/worker/worker.test.ts`
- `engine/test/support/fake-workflow-backend.ts` (scripting for per-attempt failures)

**Approach:**
- Input gains `retry?: { max, delayMs }`, `deadlineMs?` and `runAttempt`.
- `retry`: when the run failed and attempts remain, it enters `waiting({ until: clock.now() + delayMs })`, then goes back to `start` with `runAttempt + 1`. Node keys include `runAttempt` (KTD4), so the retry re-executes nodes that failed in the earlier attempt instead of replaying their stored failure; `finalize` is called once, after the last attempt, with `attempts`.
- `deadlineMs` (absolute epoch ms) aborts the run with `run_deadline`; `finalize` reports `failed` with `deadline_exceeded`. A retry never extends past the deadline.
- The worker registers the four kinds, so `/v1/health` `taskKinds` lists them (U3, KTD16) and the backend capability probe (U2) passes.

**Test scenarios** (shared memory and Postgres suite):
- Root retry: first attempt fails, wake exactly at `delayMs` (ManualClock), the second attempt completes, and `runAttempt` is in the keys (`r0` then `r1`).
- A run retry re-executes a node that failed in run attempt 0: the fake backend stored a `failed` outcome for the `r0` key, and the `r1` key is a different key that runs `prepare` again and succeeds.
- Retries exhausted: the run ends `failed` with `attempts = max + 1`.
- A restart during the retry delay wakes at the deadline and runs attempt 1 once.
- `deadlineMs` in the past or reached mid-run ends the run `failed` with `deadline_exceeded`, aborts live children, and calls `finalize` once.
- A retry after a fan-out run starts a fresh fan-out (child ids carry the new `runAttempt`).
- Worker: with `ENGINE_WORKFLOW_BACKEND_URL` unset it falls back to `ENGINE_GATEWAY_URL`; `/v1/health` lists `workflow_run`, `workflow_node`, `workflow_fanout_lane` and `workflow_gate`; a config without a backend URL fails fast with a clear message.

**Verification:** engine lint, typecheck and vitest green; clean-room grep empty.

### U13. Backend node gateway and node specs

**Goal:** the backend side of the contract: executors callable by idempotent HTTP.
**Requirements:** R3, R8, R14.
**Dependencies:** U1, U2, U33 (payload contract, validated through `workflow_contract.py`).

**Files:**
- `backend/app/internal_api/workflow_nodes.py` (new; `prepare` and `finish` routes, bodies validated by U33's models)
- `backend/app/internal_api/node_specs.py` (new: per-type replay-safety, timeout, `makes_external_calls`)
- `backend/app/internal_api/router.py` (mount)
- `backend/app/services/workflow_compiler.py` (new: spec to compiled graph, KTD5)
- `backend/app/node_executors/__init__.py` (`NodeExecutionContext.orchestrator` becomes optional; add `prepare`/`finish` hooks with a default that wraps `execute`)
- `backend/tests/internal/test_workflow_nodes.py` (new)
- `backend/tests/internal/test_workflow_compiler.py` (new)
- `backend/tests/internal/test_contract_conformance.py` (clauses from `fake-workflow-backend.ts`)

**Approach:**
- `prepare` for a type with only `execute` runs `get_executor(type).execute(...)` with adornments `cache` applied, wrapping the result as `final`, with `retry`/`timeout`/`try` stripped (the engine owns them).
- The binding comes from `resolve_binding` (tenant, project, conversation). Executors receive a tenant-scoped session from `get_db_for_tenant`, never the request session.
- `begin`/`store_outcome` give exactly-once semantics per key. Stale claims are released by `interrupt_stale` (already wired).
- Export control (`execute_with_export_control`) and the classification high-water mark are kept. The engine passes the max predecessor classification in the prepare body and stores each `final.classification`.
- Keys: the route accepts `Idempotency-Key` only in the form `{runId}:{nodeId}:r{runAttempt}:a{nodeAttempt}:{phase}` (KTD4) and rejects any other with 400. Because `runAttempt` is part of the key, a run retry re-executes a node whose `r0` key stored a failure.
- Fan-out `prepare` (iteration, subworkflow, batch): creates the item execution rows idempotently (deterministic ids from `uuid5(run_ref, f"{nodeId}:{index}")`) and returns a `fanout` body with `count`, `parallel`, `failFast`, `target` and an `itemsRef`, never the items (KTD17). `finish` for a fanout node reads the item rows to build the node's `final` output. The compiler sets `policy.maxRetries = 0` on fan-out nodes.
- The compiler also resolves a pinned `workflow_version_id` spec, and rejects unsupported nodes with the validator's error. The four builder node types with no executor (`data`, `analysis`, `research`, `schedule_trigger`) are compiled as pass-through `skipped` with a warning event, preserving today's silent skip (roadmap Foundations).

**Test scenarios:**
- A `template` node via `prepare` returns `final` with the rendered output. A repeated key returns the stored outcome and does not re-run (executor call count 1).
- A cross-tenant `tenantId` in the body is 404.
- A fence-rejected request is 409 `fence_rejected`.
- Classification propagates: a `confidential` input blocks an external-calling node with `blocked` and the audit log entry.
- Compiler: condition and classifier nodes expose handles; an edge to an untaken handle is marked for skipping; cycles rejected; the iteration target's reachable set is excluded from the main graph and attached to the iteration node's `fanout`.
- Fan-out `prepare` for an iteration of 1000 items creates 1000 item rows once (a repeated key creates none), the response body is under the U33 size cap, and a 1001-item iteration is refused.
- A key without `r{n}` or with an unknown phase is 400; the same node under `r0` and `r1` runs twice and stores two outcomes.
- Adornment mapping: `retry` becomes `policy.maxRetries/backoffMs`, `try` becomes `onError: continue`, `timeout` becomes `timeoutMs`, and `cache` still hits.
- Conformance tests assert every `[clause:]` of the fake backend against this implementation.

**Verification:** backend ruff and pytest green.

### U14. One-shot LLM nodes and the agent node on the engine

**Goal:** LLM, classifier, guardrail, parameter extractor and agent nodes use `prepare`/`finish` and import no `litellm`.
**Requirements:** R8, R12.
**Dependencies:** U13.

**Files:**
- `backend/app/node_executors/llm_executor.py`
- `backend/app/node_executors/classifier_executor.py`
- `backend/app/node_executors/guardrail_executor.py`
- `backend/app/node_executors/parameter_extractor_executor.py`
- `backend/app/node_executors/agent_executor.py`
- `backend/app/node_executors/__init__.py` (a `PrepareResult` helper)
- `backend/tests/workflows/test_llm_nodes_engine.py` (new)
- `backend/tests/workflows/test_correctness.py` (drop the litellm mocks)
- `backend/tests/test_state_machine_integration.py` (adjust if it patches the executor)

**Approach:**
- Split each executor into `build_prompt(context)` (pure) and `parse_answer(context, text)` (pure).
  - `prepare` returns `llm` with the system prompt, user prompt, tier or model, and `responseFormat` where the node wants JSON.
  - `finish` runs `parse_answer`.
  - The existing `execute` stays as a thin legacy wrapper over the same pure functions until U30. It must not import `litellm`: it calls `LLMGateway`/`llm_direct` after U27, or raises on engine-disabled tenants.
- The LLM node's streaming option and `USE_STATE_MACHINE` wrapping (`StatefulAgent`) are out of scope. The unit documents that node-level token streaming is not supported on the engine path and the UI shows the final text.
- The agent node returns `llm` with `tools` set to its configured skills (manifest names only) and `maxRounds` from `maxIterations`. This replaces its pydantic-ai loop and the `litellm.get_model_info` context-window lookup. `tokens_used` comes from the child's usage.
- The tier or model is a hint only. The policy chooses the key.

**Test scenarios:**
- Classifier: `prepare` returns the classification prompt and the label set. `finish` maps the answer to `selectedHandles = [label]`, and an unknown label routes to the default handle.
- Guardrail: a block verdict returns `blocked` with the reason; a pass returns `success`.
- Parameter extractor: valid JSON returns the variables; invalid JSON returns `failed` with a parse error and keeps the raw text (the engine retry policy may re-ask).
- LLM node: variable substitution matches the legacy output byte for byte on the existing fixtures.
- Agent node: `maxRounds` equals `maxIterations`; tools outside the manifest are dropped with a warning.
- No test imports `litellm`, and `grep -n litellm` on these five files is empty.

**Verification:** backend ruff and pytest green.

### U15. Start and cancel workflow runs on the engine

**Goal:** the run endpoints start engine runs and cancel them for real.
**Requirements:** R3, R10, R13.
**Dependencies:** U13, U35 (the full run task: graph, fan-out, retry, deadline and worker registration).

**Files:**
- `backend/app/services/workflow_engine_runs.py` (new: `start_workflow_run`, `cancel_workflow_run`)
- `backend/app/api/v1/workflows.py` (`/execute`, `/{workflow_id}/run`, `/{execution_id}/cancel`)
- `backend/app/api/v1/workflow_execution.py` (agent-chat `workflow-execute`)
- `backend/app/api/v1/compliance.py` (the `WorkflowOrchestrator` call sites)
- `backend/app/services/engine_client.py` (`start_run` payload helpers; `start_run`, `signal_task` and `get_task` themselves are U2's)
- `backend/tests/test_workflow_engine_start.py` (new)
- `backend/tests/workflows/test_correctness.py` (extend)

**Approach:**
- `start_workflow_run(db, workflow, spec, input, user, trigger, request_key)`:
  - create the `WorkflowExecution` row (`status = queued`, `total_nodes`);
  - bind the conversation (U1) and set `engine_conversation_id`;
  - compile (U13);
  - `engine.start_run(kind="workflow_run", id=conversation_id, input=...)`;
  - mark `running` when the engine accepts.
- A sync `/execute` returns after the run finishes by awaiting the finished state: it polls `get_task` with a bounded wait (default 120 s) and returns `202 { execution_id, status }` with the current status if the run is still going. The existing response model stays for finished runs.
- Cancel calls `abort` on the container conversation and sets `cancelled` (KTD9). The finalize call from a cancelled run is ignored if the row is terminal.
- Quota (`QuotaType.EXECUTIONS`) and permission checks stay exactly where they are in the handlers.
- `compliance.py` runs (lines using `WorkflowOrchestrator`, `compliance_executor`) are converted here to `start_workflow_run` with `trigger="compliance"`. This unit lists every other `WorkflowOrchestrator(` call site it converts.

**Test scenarios:**
- A run with the flag on creates one execution and one engine run, and the execution id is stable across a client retry (same `Idempotency-Key`).
- The flag off uses the legacy path.
- Cancel marks `cancelled`, calls abort once, and a later finalize does not change it.
- A run for an unauthorised user is refused before any engine call.
- Quota exceeded is refused before any engine call.
- The compliance run produces the same output shape as before on a stubbed engine.
- An engine outage on start returns 502, and the execution is `failed` with `engine_unreachable` (no dangling `queued`).

**Verification:** backend ruff and pytest green.

### U16. Finalize handler and workflow read model

**Goal:** `workflow_executions` reflect engine runs, with the trace written as nodes finish.
**Requirements:** R3, R9, R10.
**Dependencies:** U5, U15.

**Files:**
- `backend/app/services/workflow_run_sync.py` (new: `finalize`; registers the `workflow` `on_finished` hook with the U5 registry for `/internal/runs/finished` and the reconciler)
- `backend/app/services/run_handlers.py` (add `workflow_run_sync` to `bootstrap()`)
- `backend/app/internal_api/workflow_nodes.py` (`finalize` route calls it)
- `backend/tests/test_workflow_run_sync.py` (new)
- `backend/tests/contracts/fixtures/workflow_engine_success.json`, `workflow_engine_failure.json`, `workflow_engine_branch.json` (new)
- `backend/tests/contracts/streams.py`, `test_contracts.py`

**Approach:**
- `finalize` sets `status` (via `run_status`), `results`, `error`, `nodes_executed`, `total_tokens` (from the ledger rows for the run's conversation, which fixes the `tokens_used` bug), `duration_ms`, `completed_at`. It also finalises the `scheduled_workflow_executions` row when `trigger = scheduled` (U19 fills that in).
- The trace step is written on each node `final` in the node gateway (U13). This unit adds the ordering guarantee: steps are keyed by `(node_id, attempt)` and upserted, so a replayed `finish` does not duplicate a step.
- Replay and audit: `workflow_replay.py` and `audit.py` read `execution_trace`, which is preserved. The checkpoint-based `/replay` endpoint is replaced by a read of the stored trace (the data exists without checkpoints).

**Test scenarios:**
- A branch run finalises `completed` with `skipped` nodes recorded and counted out of `nodes_executed`.
- A failed node finalises `failed` with its error (the "reports success after failures" regression).
- `total_tokens` equals the ledger sum for the run (fixes the always-0 total).
- A repeated finalize changes nothing.
- Registry: `workflow` has an `on_finished` registered after `bootstrap()`; the reconciler applying a settled engine run to a stuck `running` execution finalises it once.
- A cancelled row is not overwritten.
- Trace steps are not duplicated after a replayed `finish`.
- Contract fixtures match.

**Verification:** backend ruff and pytest green.

### U17. Live stream for workflow runs

**Goal:** the existing execute-stream and logs-stream endpoints work for engine runs.
**Requirements:** R3, R9.
**Dependencies:** U16.

**Files:**
- `backend/app/api/v1/workflows.py` (`/execute-stream`, `/{workflow_id}/logs/stream`)
- `backend/app/api/v1/workflow_logs.py` (`/logs/stream`)
- `backend/app/api/v1/workflow_execution.py` (SSE `workflow_progress`/`workflow_complete` for agent chat)
- `backend/app/services/workflow_stream_adapter.py` (new: engine watch frames to legacy events)
- `backend/tests/test_workflow_stream_adapter.py` (new)
- `backend/tests/contracts/streams.py`, `fixtures/workflow_success.json` (re-record deliberately; the diff is reviewed)

**Approach:**
- The adapter consumes `EngineClient.watch_stream` for the run's container conversation, diffs `view.tasks`, and emits the legacy events (`node_start`, `node_complete`, `workflow_complete`, `error`) so the current UI keeps working until U26.
- It reconnects with the cursor on disconnect and is stateless, so any pod can serve it.
- U26 switches the UI to the engine watch directly. The adapter then stays only for the agent-studio chat path.

**Test scenarios:**
- A scripted view sequence produces the same legacy event order as `workflow_success.json`.
- A skipped node emits `node_skipped` and a failed node emits `error` with the node id.
- A reconnect with `Last-Event-ID` resumes with no duplicates and no gap.
- A client disconnect closes the engine stream.

**Verification:** backend ruff and pytest green.

### U18. `/execute-batch` on the engine

**Goal:** implement the batch endpoint with a real parallel limit.
**Requirements:** R7.
**Dependencies:** U5 (handler registry), U15, U34 (fan-out lanes).

**Files:**
- `backend/app/api/v1/workflows.py` (`execute_batch`, `get_batch`, `get_batch_items`, `cancel_batch`)
- `backend/app/services/workflow_batch_engine.py` (new; registers the `batch` `on_finished` and `start_missing` hooks)
- `backend/app/services/run_handlers.py` (add `workflow_batch_engine` to `bootstrap()`)
- `backend/tests/test_workflow_batch_engine.py` (new)

**Approach:**
- One batch is one `workflow_run` whose graph is a single `fanout` node over the items (`parallel = parallel_limit`, `failFast = false`). Each item runs as a child `workflow_run` in a fan-out lane (U34).
- Checkpoints store item counts only (KTD17). `execute_batch` already inserts the `WorkflowBatchItem` rows (`sequence`, `inputs`, `status`, `results`). The fanout node's `prepare` (U13) creates or finds each item's `WorkflowExecution` row by deterministic id and links it to the item; the engine's item child run reads its `inputs` by `sequence` from the backend and its `finalize` writes the item's `results` and `status` there. No item input or result passes through the engine checkpoint or a signal payload.
- Limits: the batch cap of 1000 equals `maxFanoutItems` (U33), and `parallel_limit` is clamped to `maxParallel`.
- The `batch` `on_finished` hook updates `workflow_batch_items` and the batch counters from the item rows. The `batch` `start_missing` hook re-issues the engine start for a batch row that has no engine run (same deterministic id; attempts bounded by the registry). Webhook notify on completion uses `webhook_service.send_webhook` with HMAC over the raw payload and the stored `webhook_secret`, SSRF-checked at create (already done).
- The `TODO` comment and the queued-forever state are removed. `max 1000` and the quota check stay.

**Test scenarios:**
- 25 inputs at `parallel_limit=5`: all item executions complete, with counts `completed=25`.
- The limit is respected (observer on the engine test harness).
- An item failure marks that item `failed` and the batch `completed_with_errors`, with the others unaffected.
- Cancel stops pending items (`cancelled`) and aborts running ones.
- The completion webhook fires once with a valid signature.
- A batch over 1000 is 400 and a foreign workflow is 404.
- A batch of 1000 items at `parallel_limit=10` completes with `completed_count=1000`, and the serialised engine checkpoints (run, lanes, node) stay under the U34 size bound; no item payload appears in them.
- Batch handler registry: the `batch` kind has `on_finished` and `start_missing` registered after `bootstrap()`. A batch row inserted with no engine run is started once by `start_missing`, a second pass is `already_started`, and a settled engine run is applied once by `on_finished`.

**Verification:** backend ruff and pytest green.

### U19. Scheduler dispatch and retries on the engine

**Goal:** scheduled runs start through the engine, and retries really run.
**Requirements:** R5, R10, R13.
**Dependencies:** U5 (handler registry), U15, U16.

**Files:**
- `backend/app/services/workflow_scheduler.py` (`_poll_once`, `_execute_one`, `dispatch_immediate`, `trigger_now`)
- `backend/app/services/scheduled_run_hooks.py` (new: registers the `scheduled` `start_missing` and `find_stuck` hooks)
- `backend/app/services/run_handlers.py` (add `scheduled_run_hooks` to `bootstrap()`)
- `backend/app/api/v1/schedules.py` (response shape unchanged; add `engine_conversation_id` to the execution detail if not present)
- `backend/app/models/scheduled_workflow.py` (no schema change)
- `backend/tests/test_workflow_scheduler_engine.py` (new)
- `backend/tests/test_agent_scheduler.py`

**Approach:**
- `_execute_one` becomes: mark running, create the `WorkflowExecution`, call `start_workflow_run` with `retry = { max: max_retries if retry_on_failure else 0, delayMs: retry_delay_seconds*1000 }`, `timeout` mapped to the run's `deadlineMs`, and the deterministic run id from the execution id. Then return.
- Everything after the start (success, failure, retry count, `last_run_*` on the schedule) is written by the finalize handler (U16).
  - The retry count comes from the final attempt number.
  - The executed-attempt history is stored in the execution's error text and trace.
- `trigger_now` no longer uses `asyncio.create_task`. It calls the same start. The semaphore, `_active_tasks`, the `pending` reset block and `recover_orphaned_executions` stay in place (disabled by the flag) until U30.

**Test scenarios:**
- A due schedule claims once (`SKIP LOCKED`), starts one engine run, and increments `execution_count` once.
- A failing workflow with `max_retries=2` and `retry_delay_seconds=60` retries twice at 60 s each on the engine clock and ends `failed` after the third attempt, or `completed` on success, with `retry_count` correct.
- `retry_on_failure=false` runs once.
- `max_concurrent` blocks a second start.
- A scheduler restart between claim and start: a `scheduled_workflow_executions` row `running` with no engine run is found by the `scheduled` `find_stuck` hook and started once by its `start_missing` hook (same deterministic id, so no duplicate). A second reconciler pass is `already_started`, and after the registry's retry bound the execution is `failed` with `engine_start_failed`. The attempt counter is stored in `scheduled_workflow_executions.result_summary["start_attempts"]` (an existing JSONB column).
- `trigger_now` starts a run and returns the execution.

**Verification:** backend ruff and pytest green.

### U20. Executing webhooks: raw-body HMAC, encrypted secrets, idempotency

**Goal:** inbound webhooks verify correctly and start runs.
**Requirements:** R4, R14.
**Dependencies:** U1 (migration 120 precedes 121), U7 (agent triggers) and U15 (workflow runs).

**Files:**
- `backend/app/api/v1/webhooks.py` (`receive_webhook` reads `await request.body()`; create route returns the secret once)
- `backend/app/services/webhook_trigger_service.py`
- `backend/app/models/webhook_trigger.py` (no new columns; `auth_config.secret_enc`)
- `backend/migrations/versions/121_encrypt_webhook_secrets.py` (new data migration; `revision = "121_encrypt_webhook_secrets"`, `down_revision = "120_engine_runs"`; it chains after U1's migration, so it is independent of where Phase 5 ends)
- `backend/tests/postgres/test_webhook_secret_migration.py` (new)
- `backend/app/services/webhook_secrets.py` (new: encrypt, decrypt, mask)
- `backend/tests/test_webhook_trigger_engine.py` (new)
- `backend/tests/test_webhook_filter_security.py` (extend)

**Approach:**
- `process_webhook` takes the raw bytes plus parsed JSON. HMAC is `hmac.compare_digest` over `hmac.new(secret, raw, sha256)`.
  - If `auth_config.timestamp_header` is set, a timestamp tolerance (default 300 s) is enforced over `ts.raw`.
- A new trigger generates its own secret when `auth_method = hmac` and none is given. The secret is returned once in the create response and stored only as `secret_enc`. List and get responses never include it.
- Migration 121 does two things, in this order, in one transaction:
  1. Encrypts every plaintext `auth_config.secret` and `key` (api_key method) into `secret_enc` / `key_enc` with the Fernet service and removes the plaintext key. It skips values already encrypted, so it is idempotent. The downgrade refuses (data is not reversibly plaintext).
  2. Backfills `webhook_triggers.created_by` where it is NULL: from `workflows.user_id` for a workflow trigger, or the agent's `user_id` for an agent trigger. A trigger whose target no longer exists (or has no owner) is unresolvable.
  - The migration reports the unresolvable triggers: a WARNING per trigger id (tenant id, target kind) and a summary count in the migration log, and it records them in the returned `BackfillReport` that the test inspects. It does not delete or disable them. At runtime an unresolvable trigger is refused with 422 `trigger_unowned` and logged on `WebhookDelivery`, and the report is copied into `DEPLOYMENT.md` (U29) for ops to fix or delete.
- The idempotency key order and the bucketed fallback are in KTD8. Header and JMESPath keys give `uuid5(trigger.id, key)`; the body-hash fallback gives `uuid5(trigger.id, key + ":" + window_bucket)` with a 24 h bucket. The `WebhookDelivery` row keeps the audit and rate limit. A deduplicated delivery returns 202 with `deduplicated: true`.
- Dispatch: `workflow_id` triggers call `start_workflow_run(trigger="webhook", request_key=...)`. `agent_id` triggers call `start_fleet_run` through `check_and_create_background_job` (the concurrency limit applies). A response of 202 returns the execution or job id.
- The `rate_limit_per_hour` count query uses `COUNT(*)` rather than loading rows.

**Test scenarios:**
- A body with extra whitespace and reordered keys, signed over the raw bytes, is accepted. The same signature over re-serialised JSON is rejected (the old behaviour).
- A one-byte tamper gives 401, and a missing signature gives 401.
- The stored `auth_config` JSON contains no plaintext secret; the migration test seeds a plaintext secret and checks it is encrypted and verifies afterwards.
- The same delivery posted twice returns the same execution id and creates one run. The second response is 202 with `deduplicated: true`; the first has `deduplicated: false`.
- A header or JMESPath idempotency key dedupes permanently: the same key posted 3 days later (clock moved) still returns the original run.
- Body-hash fallback: an identical body 25 h later (next bucket) starts a new run; within the same bucket it dedupes; a delivery straddling the bucket boundary starts a second run (pinned behaviour).
- Migration 121 (Postgres test): seeds plaintext secrets, triggers with `created_by` NULL for a workflow, an agent and a deleted target. After upgrade the secrets are encrypted and verify, the first two have `created_by` set to the workflow owner and agent owner, and the report lists exactly the unresolvable one; re-running the upgrade changes nothing; the runtime returns 422 `trigger_unowned` for the unresolvable trigger.
- A different `Idempotency-Key` with an identical body creates a second run.
- A filter mismatch returns 200 and starts nothing.
- A trigger with `created_by = NULL` is refused at create.
- An agent trigger starts a Fleet job, and at the concurrency limit returns 429-style `ConcurrentLimitExceeded` handling (202 with `queued=false`, documented).
- IP allow-list, API key and rate-limit paths keep their tests.

**Verification:** backend ruff and pytest green.

### U21. Wait nodes and human-in-loop on deadline and signal waits

**Goal:** real durations and a real approval wait. The decision API is Phase 5's; this unit is the workflow wait-node wiring only.
**Requirements:** R6.
**Dependencies:** U12, U13, Phase 5 U2 (approval ledger and `ApprovalService.decide`, the decision route). If Phase 5 has not merged, this unit does not start; it must not ship its own decision route.

**Files:**
- `backend/app/node_executors/wait_executor.py` (`prepare` returns `wait`; the 300 s constant is deleted)
- `backend/app/node_executors/human_in_loop_executor.py` (`prepare` returns `wait` with a signal and records a ledger row; `finish` maps the decision)
- `backend/app/services/workflow_wait_decisions.py` (new: post-decision hook that signals the node task)
- `backend/app/services/approval_service.py` (Phase 5 U2's file: add the small `register_decision_hook(kind, run_kind, fn)` registry only if Phase 5 did not already expose one; additive)
- `backend/app/api/v1/workflows.py` (new read route `GET /workflows/executions/{execution_id}/waiting-nodes`: node id, wake time, ledger id, for the UI)
- `backend/tests/workflows/test_wait_node_engine.py` (new)
- `backend/tests/workflows/test_human_in_loop_engine.py` (new)

`EngineClient.signal_task` is created in U2 and is not re-created here.

**Approach:**
- `duration` returns `{ durationMs }`. `until_time` and `business_hours` return `{ untilEpochMs }` computed with `ZoneInfo` as today. The `maxWaitSeconds` cap stays as a validation error.
- Human-in-loop `prepare` returns `wait` with `signal = "decision:{node_id}"` and `untilEpochMs` from `slaHours`, and records a Phase 5 ledger row through `ApprovalService.record_pending` (`kind = 'review'`, `tool_task_id` = the node task id, `engine_conversation_id` = the run's container conversation, `approver_role` from the node's `approverType`/`approverConfig`). The row appears in Phase 5's "Needs attention" inbox with no further work here.
- Deciding goes through Phase 5's decision route and RBAC (`ApprovalService.decide`), not a route in this plan. For a ledger row whose run is a workflow node, `decide` calls the registered hook in `workflow_wait_decisions.py`, which signals the node task (`signal_task`, key `decision:{node_id}`, payload = the decision) instead of `EngineClient.decide`. The first decision wins and a second is 409, as in Phase 5.
- Wake reason `deadline` runs `finish` with `decision = "sla_expired"`, which follows the node's escalation config; the ledger row is marked `expired` by Phase 5's sweep.
- Open coordination item with Phase 5 (also in Open Questions): whether the ledger `kind` for a workflow wait is `review` (assumed here) or a new `workflow_wait` value. Either is a Phase 5 enum change, not a schema change here.

**Test scenarios:**
- `duration: 2 hours` returns `durationMs = 7_200_000`. A zero wait returns `final` immediately (no task wait).
- `until_time` in the past returns `final` with no wait. `business_hours` outside hours waits until the next window start in the node's timezone.
- A wait above `maxWaitSeconds` fails the node with the existing message.
- Human-in-loop `prepare` creates exactly one ledger row (a repeated key creates none) with the node task id and the SLA expiry.
- Through Phase 5's decision route (fake engine client): an unauthorised user gets 403 and nothing is signalled; an authorised approve signals the node once and resumes the run; the first decision wins and a second is 409.
- SLA expiry follows the escalation path, using the engine clock.
- The waiting-nodes route lists a parked node with its wake time and ledger id, and is tenant-scoped.
- Existing `__approval_decision` replay input is no longer needed and is removed with a test confirming resume without it.

**Verification:** backend ruff and pytest green.

### U22. Workflow end-to-end proof

**Goal:** prove AE3, AE5, AE6, AE9 and the webhook path against real processes.
**Requirements:** R3–R7, R10.
**Dependencies:** U17, U18, U19, U20, U21.

**Files:**
- `backend/tests/e2e/test_workflow_engine_recovery.py` (new)
- `backend/tests/e2e/test_workflow_webhook_e2e.py` (new)
- `backend/tests/e2e/backend_app.py`, `harness.py` (workflow fixtures and a counting HTTP target)

The Playwright spec `frontend/e2e/workflow-engine-recovery.spec.ts` is owned by U26. U22 is backend e2e only.

**Test scenarios:**
- Kill the worker mid-join. After restart the run completes, the HTTP node's external POST happens once, and the ledger has one row per model request.
- Restart the backend pod mid-run. No effect on the run.
- A branch workflow runs only the taken branch.
- A two-hour wait on the shared test clock survives a restart, then completes when the clock advances.
- A webhook with a valid raw-body signature starts a run. A replay returns the same run with `deduplicated: true`.
- A batch of 25 items at `parallel_limit=5` against real processes: the worker is killed mid-batch, and after restart all 25 items ran exactly once (the counting target sees 25 posts).
- A scheduled workflow whose first attempt fails retries as run attempt 1, re-executing the node that failed in attempt 0.
- A scheduled workflow with a failing first attempt retries at the delay and completes.
- A cancel mid-run stops node starts, and the execution stays `cancelled`.

**Verification:** `ONIVA_E2E=1 uv run pytest -m e2e tests/e2e` green.

## Phase D. UI run model

### U23. Shared status enum and run-tree contracts

**Goal:** one TypeScript status enum and one `RunNode` shape, with adapters from each source.
**Requirements:** R9.
**Dependencies:** U2 (mapping fixture), U4 (view fixture).

**Files:**
- `frontend/lib/run/status.ts` (new: `RunStatus`, `normalizeStatus`, `isTerminal`, `isActive`, labels)
- `frontend/lib/run/tree.ts` (new: `RunNode`, `buildRunTree`)
- `frontend/lib/run/adapters/engineView.ts` (new: from `ConversationView.subagents` and `tasks`)
- `frontend/lib/run/adapters/legacy.ts` (new: from `BackgroundJob` and `WorkflowLog` rows)
- `frontend/lib/engine/types.ts` (add `tasks` to `ConversationView` and its guard)
- `frontend/lib/run/__tests__/status.test.ts`, `tree.test.ts`, `adapters.test.ts` (new)
- `frontend/lib/engine/__tests__/types.test.ts` (extend)

**Approach:**
- `RunNode` is `{ id, kind: 'run'|'subagent'|'worker'|'node'|'tool', label, status, startedAt, endedAt, usage?, waitUntil?, error?, children }`.
- `status.ts` loads `backend/tests/fixtures/run_status_map.json` in its test to assert parity with Python (U2).
- `resuming` comes from a `run_resumed` entry newer than the last progress entry. `interrupted` comes from a tool result or node outcome of status `interrupted`.
- Adapters are pure, reference-stable for unchanged input (the property the Deep Work adapter already keeps), and tolerate older engines without `tasks`.

**Test scenarios:**
- The full mapping table round-trips and matches the shared JSON.
- An engine view with a root run, two subagents and a failed one builds the expected tree.
- A view with `run_resumed` yields `resuming` then `running`.
- A workflow view with a skipped branch yields `skipped` leaves.
- An unchanged view returns the identical tree object.
- A legacy job row with `status: 'error'` maps to `failed`.
- A view from an older engine (no `tasks`) does not throw.

**Verification:** `npx tsc --noEmit && npx jest --ci --watchAll=false` green.

### U24. `RunTree` component and Deep Work adoption

**Goal:** one tree UI, proven on the surface that already exists.
**Requirements:** R9.
**Dependencies:** U23.

**Files:**
- `frontend/components/run/RunTree.tsx`, `RunStatusBadge.tsx`, `RunNodeRow.tsx` (new)
- `frontend/components/deep-work/DeepWorkEngineCard.tsx`, `WorkerActivityCard.tsx` (render workers and the reflector as `RunNode`s)
- `frontend/lib/engine/deepWork.ts` (export a `toRunTree(state)` helper; the adapter output is unchanged)
- `frontend/components/run/__tests__/RunTree.test.tsx` (new)
- `frontend/components/deep-work/__tests__/DeepWorkEngineCard.test.tsx` (extend)

**Approach:**
- Collapsed by default past depth 2. Rows show the status badge, label, elapsed time and token usage. A `waiting` row shows the wake time.
- The component takes `RunNode` only. It never reads engine, job or execution shapes.
- A keyboard-accessible tree (`role="tree"`, arrow keys), reduced-motion safe.
- Deep Work's behaviour and existing tests are unchanged.

**Test scenarios:**
- Renders each of the nine statuses with a distinct label and an accessible name.
- Expanding a node shows its children; a node with 50 children renders the first 20 with a "show more" control.
- `resuming` and `interrupted` show their explanatory text.
- The Deep Work card shows the same worker rows as before (existing assertions pass).
- The tree is navigable with the keyboard.

**Verification:** frontend tsc and jest green.

### U25. Fleet page on the run model

**Goal:** Fleet runs use engine watch, `RunTree`, steer and real cancel.
**Requirements:** R2, R9.
**Dependencies:** U8, U9, U24.

**Files:**
- `frontend/hooks/useFleetJobStream.ts` (engine jobs use `useEngineConversation`; legacy jobs keep the trace path)
- `frontend/components/fleet/FleetChat.tsx`, `FleetRunList.tsx`
- `frontend/lib/api/fleet.ts`, `lib/api/background-jobs.ts` (`cancelJob`)
- `frontend/lib/models.ts` (`FleetRunItem` gains `conversation_id`, `engine`)
- `frontend/lib/types/chat-panels.ts` (`JobStatus` becomes `RunStatus` alias)
- `frontend/hooks/__tests__/useFleetJobStream.test.ts` (extend)
- `frontend/components/fleet/__tests__/FleetRunList.test.tsx` (new)
- `frontend/e2e/fleet-engine-recovery.spec.ts` (new Playwright spec; drives the U10 backend e2e fixtures)

**Approach:**
- The intervene box submits through the same endpoint (the backend steers). The UI shows queued steer chips from `view.inbox.queue`, like chat.
- The Cancel button is enabled for `running`, `waiting` and `queued`, and uses the status enum.
- `FleetRunList` badges use `RunStatusBadge`.

**Test scenarios:**
- An engine job opens a watch and renders the run tree and transcript. A legacy job still renders from the trace.
- A steer chip appears after intervene and clears when placed.
- A refresh mid-run rejoins with no lost text.
- A cancelled job shows `cancelled` and the input is disabled.
- A `resuming` run shows the resume cue.

**Verification:** frontend tsc and jest green; Playwright `fleet-engine-recovery` green against the U10 fixtures.

### U26. Workflow run views on the run model

**Goal:** logs and test-run panels show node tasks as a tree, with waiting and recovery states.
**Requirements:** R3, R6, R9.
**Dependencies:** U17, U21, U24.

**Files:**
- `frontend/hooks/useWorkflowLogs.ts`, `frontend/lib/api/workflow-logs.ts`
- `frontend/components/workflow/LogsPanel.tsx`, `LogDetailView.tsx`, `TestRunPanel.tsx`
- `frontend/lib/api/workflows.ts` (remove `recover`/`replay`/`checkpoints` client calls if any; `executeWorkflowStream` stays on the adapter or moves to the engine watch)
- `frontend/lib/stores/workflow.ts` (node status uses `RunStatus`)
- `frontend/components/workflow/__tests__/` (extend or add `LogDetailView.test.tsx`)
- `frontend/e2e/workflow-engine-recovery.spec.ts`

**Approach:**
- Active engine runs read the engine watch (`view.tasks` through `engineView` adapter); finished runs read the stored trace through the `legacy` adapter, so both render through `RunTree`.
- Node status on the canvas uses the same enum, including `waiting` (a timer or a human decision) with the wake time and an Approve/Reject control that calls Phase 5's decision route (the ledger id comes from U21's waiting-nodes route).
- The "Recover"/"Replay" buttons, if present, are removed in favour of automatic resume.

**Test scenarios:**
- A live run updates node badges as tasks change. A skipped branch shows `skipped`.
- A waiting node shows its wake time and an approve control for authorised users; the control calls Phase 5's decision route with the ledger id, and a 403 shows the Phase 5 "you cannot decide this" state.
- A refresh mid-run rejoins.
- A finished run renders from the stored trace identically to the live tree.
- No call to the removed endpoints remains (`grep` check in the test).

**Verification:** frontend tsc and jest green; Playwright `workflow-engine-recovery` green.

## Phase E. Flip, LiteLLM removal and deletions

### U27. Direct provider layer for `LLMGateway`

**Goal:** `LLMGateway`, embeddings and the contextual retrieval helpers work without `litellm`.
**Requirements:** R12.
**Dependencies:** none (parallel with phases A–D).

**Files:**
- `backend/app/services/llm_direct.py` (new)
- `backend/app/services/llm_gateway.py` (`complete`, `stream`, `_build_litellm_params` removed, `verify_provider_connection`, the module-level patches and `litellm.drop_params`)
- `backend/app/services/rag_service.py` (`litellm.aembedding` to `llm_direct.embed`)
- `backend/app/services/contextual_retrieval.py` (two `litellm.acompletion` calls to `LLMGateway`/`llm_direct`)
- `backend/app/config/llm_pricing.py` (context-window helper `context_window_for(model)` to replace `litellm.get_model_info`, if absent)
- `backend/tests/test_llm_gateway.py` (rewrite the mocks)
- `backend/tests/test_rag_upgrade.py`, `test_research_vector_search.py`, `test_settings_isolation.py`, `tests/conftest.py` (drop `litellm` stubs)
- `backend/tests/test_llm_direct.py` (new)

**Approach:**
- `llm_direct` offers `complete(request, provider)`, `stream(...)` and `embed(texts, provider)`.
  - `anthropic` uses `AsyncAnthropic`. The existing `_prepare_anthropic_request` cache markers feed `system` and `tools` natively.
  - `openai`, `openrouter`, `azure` and `custom` use `AsyncOpenAI` with `base_url`. OpenRouter prefixing follows `to_openrouter_model_id`.
- The circuit breaker, fallback order, quota recording and `_log_usage` in `LLMGateway` keep their logic. The breaker is fixed to ignore 4xx (roadmap Phase 3 item, if not already done: check `get_provider_circuit_breaker` callers first).
- Cost: `calculate_cost_with_cache(find_model_pricing(model), usage)`. This replaces `litellm.completion_cost` at `llm_gateway.py:734`. An unknown model records cost 0 and a warning (the ledger convention in `internal_api/models.py`).
- Dropping `drop_params=True` changes behaviour for providers that reject unknown params. The unit adds the roadmap's test: a request with `system` and `temperature` for each provider type, asserting what is sent.
- Google/Gemini: `LLMProvider.provider_type` has no `google` value (`openai`, `anthropic`, `openrouter`, `azure`, `custom`), but model ids with a `google/` prefix are routed through OpenRouter or a `custom` OpenAI-compatible endpoint. No Gemini-native path is added.

**Test scenarios:**
- For each provider type: the request body (messages, system, tools, tool_choice, max_tokens) matches the provider's API.
- Streaming yields the same `LLMStreamChunk` sequence for a scripted SSE.
- Fallback to the next provider on a 5xx; no fallback on 401/400; the breaker opens on repeated 5xx and ignores 4xx.
- Usage and cost are recorded from `llm_pricing`, and the quota recorder is called for streams.
- `embed` returns vectors in input order and batches over the provider limit.
- `verify_provider_connection` returns success and failure shapes unchanged.
- Existing gateway tests pass with the new mocks.

**Verification:** backend ruff and pytest green (the dependency is still installed at this point).

### U28. Native pydantic-ai models, `base.py` and `step_builder.py`, and the dependency removal

**Goal:** the last `litellm` importers are gone and the dependency is dropped.
**Requirements:** R12.
**Dependencies:** U14, U27, rollout U8 (so `deep_work/engine.py` is gone). If rollout U8 has not merged, this unit also deletes the single `import litellm.exceptions` at `deep_work/engine.py:1126` (the retry classifier) in a way rollout U8's deletion supersedes without conflict.

**Files:**
- `backend/app/services/llm_gateway.py` (`create_litellm_model` renamed `create_model`; callers updated)
- `backend/app/agents/base.py` (line 5 and 579–582 `get_model_info`, `_run_with_litellm_thinking` at 1084–1164, all `create_litellm_model` call sites)
- `backend/app/agents/step_builder.py`
- `backend/app/api/v1/workflow_ai.py` (comment/model prefix at line 124)
- `backend/app/api/v1/observability_api.py` (docstring only)
- `backend/app/services/{wide_research_orchestrator,entity_extraction,chat_service,agent_generator,agent_evaluator}.py`, `api/v1/{sandbox_proxy,chat,agent_chat,agent_generation,agent_test_cases,llm_providers}.py`, `agents/skills/registry.py`, `services/sandbox_tokens.py` (rename import only; each is checked)
- `backend/pyproject.toml`, `backend/requirements.txt`, `backend/uv.lock`
- `backend/tests/test_no_litellm.py` (new)

**Approach:**
- `create_model` is today's `create_litellm_model` body (it is already native: `AnthropicModel`, `OpenAIChatModel`, `GoogleModel`). The unit makes it the only factory, with the name and docstring corrected.
- The Anthropic thinking path in `_run_with_litellm_thinking` uses the native `AsyncAnthropic` client. Other providers' `thinking` parameter is dropped with a debug log (it was a LiteLLM extension).
- Context windows come from `context_window_for` (U27).
- Dependency: remove `litellm>=1.83.2` from `pyproject.toml` and regenerate `uv.lock` and `requirements.txt` with the repo's existing command. Re-run the full suite to find anything that relied on its transitive packages (`tiktoken`, `tokenizers`, `jinja2`, `aiohttp`), and pin them directly where needed.
- `tests/test_no_litellm.py` greps `backend/app` and the dependency files, and fails on a hit.

**Test scenarios:**
- `grep -rn litellm backend/app` is empty and the dependency files contain no `litellm`.
- The app imports cleanly with the package uninstalled (run the suite in an environment from the regenerated lockfile).
- A pydantic-ai agent run on a scripted `FunctionModel` still works for the legacy chat and agent-studio paths.
- Anthropic thinking returns reasoning parts on a scripted client.
- `observability_api` model-name cleaning behaves as before.

**Verification:** backend ruff and pytest green on the regenerated lockfile; `uv sync --frozen` succeeds.

### U36. Load gate and the sharding decision

**Goal:** measure whether one engine session carries automation runs and chat together, and decide before the flip whether sharding must ship first. Added after U32; the id is stable. This is the explicit pre-U29 unit; U29 does not start without its recorded result.
**Requirements:** R10, R14.
**Dependencies:** U10, U22, U26 (everything the flip exposes must exist).

**Files:**
- `backend/tests/load/automation_load.py` (new: driver, marked `load`, excluded from the default pytest run and CI)
- `backend/tests/load/gate.py` (new: pure gate evaluation and percentile code)
- `backend/tests/load/fakes.py` (new: scripted fake model and counting tool target, reusing the e2e harness fakes)
- `backend/tests/load/test_gate.py` (new: runs in CI against synthetic numbers)

**Approach:**
- Run 200 concurrent workflow runs of 10 nodes each (a mix of `template`, `http`, `llm` via the scripted fake model, one branch, one 30 s wait) plus 20 Fleet runs against one worker on one session, while a steady chat load measures first-token latency. Record engine commit latency p50/p95/p99, chat first-token latency p50/p95 with and without the automation load, worker CPU and memory, outbox depth, reconciler lag, and the number of runs not terminal after the drain.
- Gate: chat first-token p95 under load may not regress more than 20% against the no-load baseline, no run may be left non-terminal, and the ledger rows must equal the model requests. If the gate fails, stop: open the sharding follow-up (per-tenant or per-kind sessions through `session_for`) and ship it before U29's flip.
- The result (numbers, hardware, date, pass or fail) is pasted into the PR and copied by U29 into `DEPLOYMENT.md`. This unit changes no product code.

**Test scenarios** (CI, synthetic):
- The percentile function matches known values on a fixed sample.
- The gate passes at +19% chat p95 and fails at +21%; it fails when any run is non-terminal or when ledger rows differ from model requests.
- A smoke profile (5 workflow runs, 1 Fleet run, in-memory worker) passes end to end, so the driver itself does not rot.

**Verification:** backend ruff and pytest green (default run); the full load profile run by ops once, result recorded.

### U29. Flip defaults, soak gates and rollout docs

**Goal:** engine Fleet and workflows are on by default for tenants, with ops gates written down.
**Requirements:** R1, R3, R10, R11.
**Dependencies:** U10, U22, U26, U36 (recorded load gate result).

**Files:**
- `backend/app/services/feature_flags.py` (the two new flags become opt-out)
- `backend/tests/test_engine_run_flags.py`
- `DEPLOYMENT.md` (feature table; deploy order; soak gates; worker sizing; load gate result; how to drain legacy runs; the unresolvable-webhook-trigger report from migration 121)
- `frontend/lib/` feature map users (`useFeatures`-style hooks) only if a type needs the new keys

**Approach:** same staging as rollout U7. `DEPLOYMENT.md` documents:
- **Deploy order: engine first, then backend, then the tenant flag (KTD16).** The engine release must show `apiVersion >= REQUIRED_API_VERSION` and the four workflow task kinds in `GET /v1/health` before the backend release that requires them. The backend capability gate (U2) keeps the flags off until it does. Rolling back goes in the reverse order.
- **Soak gates** that U30–U32 depend on, each as a query in `DEPLOYMENT.md` with an expected empty result:
  - no `background_jobs` row with `status in (pending, running)` and no `input_data.engine`;
  - no `workflow_executions` row `running` without `engine_conversation_id`;
  - no `scheduled_workflow_executions` row `pending` with `retry_count > 0` (the dead retry state);
  - **no tenant has either flag explicitly `false`** (a query over the tenant settings keys the two flags read). A tenant that opted out must be moved back or contacted first, because after U30/U31 `false` means 503, not a legacy path;
  - a week with the ledger check clean.
- The U36 load gate result, and the NTP requirement for wait-node clock skew (see Risks).

**Test scenarios:**
- No tenant setting with globals on: enabled. An explicit `false` disables. A global off disables.
- `get_tenant_features` agrees.
- A tenant opted out keeps the legacy path for new runs (until U30).
- Capability absent (U2 probe) turns both flags off even with the global on and no tenant setting.
- The soak-gate queries in `DEPLOYMENT.md` run (a test executes each against a seeded Postgres and asserts it returns rows when a violating row exists and none otherwise).

**Verification:** backend ruff and pytest green.

### U30. Delete the legacy workflow machinery and the boot sweep

**Goal:** remove `DurableWorkflowEngine`, the orchestrator loop, checkpoint code and model, the in-process cancel maps, scheduler run tasks and the mark-failed sweep. After this unit a workflow run has no legacy path.
**Requirements:** R11, R13.
**Dependencies:** U29 and its soak gates (ops).

**Files:**
- `backend/app/services/durable_workflow_engine.py` (delete)
- `backend/app/services/checkpoint_cleanup.py` (delete)
- `backend/app/models/workflow_checkpoint.py` (delete) and its export in `backend/app/models/__init__.py` (the table drop is U32)
- `backend/app/models/workflow.py` (remove the `dbos_workflow_uuid` attribute; the column drop is U32)
- `backend/app/services/workflow_orchestrator.py` (delete the run loop, `_cancellation_flags`, `_running_executions`, `cancel_execution`, `execute_workflow_stream`; keep `BranchState` only if U13 imports it, else delete and move)
- `backend/app/api/v1/workflows.py` (`/execute-durable` and its `dbos_workflow_uuid` response fields, `/{uuid}/recover`, `/{uuid}/replay`, `/{uuid}/checkpoints`, legacy branches in `/execute`, `/run`, `/execute-stream`)
- `backend/app/api/v1/workflow_execution.py`, `backend/app/api/v1/compliance.py` (legacy branches)
- `backend/app/services/workflow_engine_runs.py` (`require_engine_workflows()`: raises 503 `engine_unavailable` when the flag is false or the capability is absent; every start site calls it)
- `backend/app/services/workflow_scheduler.py` (`_semaphore`, `_active_tasks`, `_execute_with_release`, the pending-reset block, `recover_orphaned_executions`)
- `backend/app/services/startup_recovery.py` (`fail_stale_deep_work_jobs`), `backend/app/main.py` (both callers and the call to `daemon.recover_orphaned_executions`)
- `backend/app/node_executors/*` (the legacy `execute` wrappers that exist only for the orchestrator are removed; this includes `wait_executor.py` and `human_in_loop_executor.py`)
- `backend/tests/test_workflows_durability.py`, `test_checkpoint_persistence.py`, `test_workflow_orchestrator.py`, `tests/integration/test_architecture_2.py` (delete or port to the engine path)
- `backend/tests/test_no_run_create_task.py` (new; KTD14 guard, with the allow-list: scheduler poll loop, reconciler, observability fire-and-forget, doc-processing helpers)
- `backend/tests/test_workflow_flag_false.py` (new)

**Approach:**
- Before deleting, list every caller (grep) and confirm each is on the engine path. Keep `startup_recovery.interrupt_stale_tool_invocations` and `fail_stale_knowledge_documents`. They are not run machinery.
- `audit.py` and `workflow_replay.py` keep working from `execution_trace`.
- Flag false is not a legacy path any more: every workflow start site (`/execute`, `/run`, `/execute-stream`, `/execute-batch`, agent-chat `workflow-execute`, compliance, `trigger_now`) calls `require_engine_workflows()` first, so a false flag, a missing engine or a missing capability returns 503 `engine_unavailable` before any row is created. The scheduler and webhook dispatchers record the execution `failed` with `engine_unavailable` and do not retry into a legacy path.
- No migration here: the table drops are U32, after the code and models are gone, so a rollback of this PR does not hit a missing table or column.

**Test scenarios:**
- No import of a deleted module remains (import smoke test of `app.main`). `WorkflowCheckpoint` is not in `app.models`; `WorkflowExecution` has no `dbos_workflow_uuid` attribute; the `ExecuteDurableResponse` fields are gone.
- The openapi schema has none of the removed routes and keeps `/execute`, `/run`, `/execute-stream`, `/execute-batch`, cancel and logs.
- Flag false selects no legacy code: with the tenant flag false, every start site returns 503 `engine_unavailable`, creates no `workflow_executions` row, makes no engine call, and the deleted symbols (`WorkflowOrchestrator`, `DurableWorkflowEngine`) are absent from the module namespace, so no fallback is possible. The same 503 results when the capability probe fails.
- The `create_task` guard test passes, and fails when a new `asyncio.create_task` with run work is added in a fixture file.
- The scheduler still claims and starts runs after the deletion.
- The lifespan starts and stops cleanly with no sweep.

**Verification:** backend ruff and pytest green; e2e green; Playwright suites green.

### U31. Delete legacy Fleet code and the Redis intervene queue

**Goal:** remove `background_agent_executor.py`, `intervention.py` and the legacy branches. After this unit a Fleet run has no legacy path.
**Requirements:** R11, R13.
**Dependencies:** U30 (same soak), U25.

**Files:**
- `backend/app/services/background_agent_executor.py` (delete; the pure helpers live in `fleet_engine_agent.py` since U6)
- `backend/app/services/intervention.py` (delete)
- `backend/app/api/v1/background_jobs.py` (legacy spawn, legacy stream, `job.cancel()`-only path; `create_job` calls `require_engine_fleet()`), `backend/app/api/v1/fleet.py`
- `backend/app/services/fleet_engine.py` (`require_engine_fleet()`: 503 `engine_unavailable` when the flag is false or the capability is absent)
- `backend/app/services/workflow_scheduler.py` (legacy agent dispatch branch; the dispatcher records the job `failed` with `engine_unavailable` instead of falling back)
- `frontend/hooks/useFleetJobStream.ts`, `frontend/lib/fleet/traceToMessageParts.ts` (delete the trace path once no legacy rows are reachable; keep read-only history if old rows exist)
- `backend/tests/test_background_agent_executor.py`, `test_intervention.py` (delete), `test_fleet.py`, `test_agent_scheduler.py` (port)
- `backend/tests/contracts/fixtures/fleet_*.json` (old fixtures replaced by the `fleet_engine_*` ones; delete the legacy driver in `streams.py`), `backend/tests/contracts/test_contracts.py`
- `backend/tests/test_fleet_flag_false.py` (new)

**Approach:** keep a read-only adapter for historical `background_jobs` rows (`execution_trace` remains a column). Redis stays a dependency for other uses, and only the `fleet:intervention:*` keys disappear.

**Test scenarios:**
- Importing `app.main` works with the modules gone.
- An old job row still opens read-only in the Fleet page.
- Intervene and cancel work only through the engine path.
- Flag false selects no legacy code: with the tenant flag false (or the capability absent), `create_job`, intervene, the webhook agent trigger and the scheduler dispatch each return 503 `engine_unavailable` or record `failed(engine_unavailable)`, create no engine conversation, and `run_background_agent` no longer exists to be called.
- `grep -rn "fleet:intervention" backend` is empty.

**Verification:** backend and frontend suites green; e2e green.

### U32. Table drops, dead config and the exit-criteria guards

**Goal:** migration 122 and the last cleanups, then prove the exit criteria. The migration is safe by construction: it records counts, refuses to drop live data, lists what it drops, and drops no dependent object implicitly.
**Requirements:** R11, R12, R13.
**Dependencies:** U28, U30, U31.

**Files:**
- `backend/migrations/versions/122_drop_legacy_workflow_tables.py` (new; `revision = "122_drop_legacy_workflow_tables"`, `down_revision = "121_encrypt_webhook_secrets"`)
- `backend/app/config/__init__.py` (`USE_DURABLE_WORKFLOWS`, `DBOS_DATABASE_URL`, `USE_BASH_TOOL`; `STALE_JOB_THRESHOLD_SECONDS` if unused after U30)
- `backend/app/agents/tools/__init__.py` (delete if nothing imports it), `backend/app/services/session_memory.py` (the "Use bash_tool" prompt string at ~278)
- `backend/tests/postgres/DRIFT.md` (remove the drift entries)
- `backend/tests/postgres/test_drop_legacy_tables.py` (new)
- `backend/tests/test_exit_criteria.py` (new)
- `backend/tests/e2e/test_rolling_deploy.py` (new)
- `DEPLOYMENT.md` (env removals; the archive and `pg_dump` procedure; the recorded row counts)

`models/workflow.py` and the checkpoint model are already gone (U30). This unit touches no model.

**Approach:**
- Tables: `workflow_checkpoints`, `workflow_approvals`, `webhook_endpoints`, `subworkflow_executions` (the four non-backup tables) and `_backup_orphaned_checkpoints_062` (the backup). Column: `workflow_executions.dbos_workflow_uuid`. Every statement is `IF EXISTS`.
- Steps, in one transaction, in this order:
  1. **Record row counts** for all five tables (skipping any that do not exist) and log them at WARNING in the migration output. The same counts are pasted into the PR. Counts for production are collected by ops before the PR merges (Open Questions).
  2. **Refuse to drop data:** `RAISE EXCEPTION` if any of the four non-backup tables has a row. The migration never deletes rows. Ops must first archive with the `DEPLOYMENT.md` procedure (`pg_dump --table=<name>` to cold storage, verified restore, then an explicit `TRUNCATE` signed off by the owner) and rerun.
  3. **Archive the backup:** if `_backup_orphaned_checkpoints_062` has rows, rename it to `_archive_backup_orphaned_checkpoints_062` (data kept, nothing dropped, ops dump and drop it by hand later); if empty, drop it.
  4. **Enumerate dependents, then drop explicitly. No `CASCADE`.** For each table the migration lists `pg_depend`/catalog dependents: inbound foreign keys from other tables, views, triggers, policies and sequences. It `RAISE`s if any inbound foreign key, view or function body references a dropped table. It then drops, by explicit name, the RLS policies (`DROP POLICY IF EXISTS`), triggers and indexes the baseline created for each `tenant_id` table, and finally `DROP TABLE IF EXISTS <name>` with no `CASCADE`. For the column: `DROP INDEX`/`DROP CONSTRAINT IF EXISTS` for the unique constraint on `dbos_workflow_uuid` by its actual name (looked up by the test), then `ALTER TABLE workflow_executions DROP COLUMN IF EXISTS dbos_workflow_uuid`.
- Downgrade recreates the empty `workflow_checkpoints` table and the nullable column only, and states that dropped data is not restored.
- RLS: confirm no leftover policy objects reference the dropped tables (the baseline created them for every `tenant_id` table).
- `test_exit_criteria.py`: `grep litellm` empty; the AST guard; no import of any deleted module; the config has none of the dead keys.
- `test_rolling_deploy.py`: restart the backend and then the engine worker during a workflow run and a Fleet run, and assert both complete once (R10, AE9).

**Test scenarios:**
- Upgrade on a database built from the legacy chain (with the extra tables, all empty) and on a fresh baseline (only the tables that exist): both succeed. Downgrade then upgrade is clean.
- Non-empty guard: with one row in each of `workflow_checkpoints`, `workflow_approvals`, `webhook_endpoints` and `subworkflow_executions` in turn, the upgrade fails with the message naming the table and its count, and the database is unchanged (transactional).
- Backup table: non-empty is renamed to `_archive_backup_orphaned_checkpoints_062` and its rows are intact; empty is dropped.
- No `CASCADE`: a test view that references `workflow_checkpoints` makes the upgrade fail naming the view; the migration source contains no `CASCADE` token (a grep test).
- Dependent enumeration: the test lists `pg_depend` for each table before the upgrade and asserts the migration's explicit drop list covers every dependent policy, trigger and index, so the plain `DROP TABLE` succeeds.
- Counts: the recorded counts appear in the migration log for all five tables.
- After the migration, the app boots and the workflow log routes work.
- The exit-criteria test fails if a `litellm` string is reintroduced.
- Rolling deploy e2e: no run lost, no duplicate external effect.

**Verification:** every Verification Contract gate green; `grep -r litellm backend/app` empty.

---

## Verification Contract

| Area | Gate |
|---|---|
| engine | `npm run lint && npm run typecheck && npx vitest run`; clean-room grep on `engine/` empty |
| backend | `uv run ruff check app tests && uv run pytest -q -p no:cacheprovider` |
| frontend | `npx tsc --noEmit && npx jest --ci --watchAll=false` |
| e2e | `ONIVA_E2E=1 uv run pytest -m e2e tests/e2e`; Playwright `fleet-engine-recovery`, `workflow-engine-recovery`, plus the existing `chat-engine-recovery`, `deep-work-engine-recovery` |
| contracts | `uv run pytest tests/contracts`; any re-recorded fixture reviewed in the diff |
| migrations | upgrade, downgrade and upgrade on Postgres for 120, 121, 122; legacy-chain database for 122; the 122 non-empty guard and no-`CASCADE` tests; the chain stays single-head with Phase 5's 110–113 ahead of 120 |
| hygiene | gitleaks clean; no `.env` committed; no secret in engine or backend logs (redaction test) |
| contract | `engine/contracts/workflow-nodes` fixtures validate in the engine and the backend (U33); `[clause:]` conformance for the fake workflow backend |
| exit criteria | `grep -r litellm backend/app` empty; `tests/test_no_run_create_task.py` passes; flag-false tests pass (U30, U31); rolling-deploy e2e passes |

### Load gate (U36, before U29 flips the defaults)

U36 runs 200 concurrent workflow runs of 10 nodes plus 20 Fleet runs against one worker and records commit latency and chat first-token latency. If chat p95 regresses by more than 20%, stop and open the sharding follow-up before U29's flip. U29 depends on U36's recorded result.

## Risks and Open Questions

Answered by the coordinator decisions (kept here so the reasoning is findable):

| Question or risk | Decision |
|---|---|
| Human-in-loop overlaps Phase 5 approvals. | Phase 5 (002) owns the approval and question decision API. U21 depends on its decision route and ledger and adds only the workflow wait-node wiring (KTD7). |
| Webhook triggers with no `created_by` exist. | Migration 121 (U20) backfills `created_by` from the workflow owner (or the agent's owner) and reports unresolvable triggers; those are refused at runtime with 422 `trigger_unowned`. Tested. |
| Is a 24 h idempotency window still needed? | Only for the body-hash fallback, as a 24 h bucket in the run id. Header and JMESPath keys are permanent (KTD8). |
| Do tables with possible legacy data block the drops? | U32 records counts for all five, `RAISE`s on any non-empty non-backup table, archives the backup by rename, and uses no `CASCADE`. |
| Migration numbers collide with Phase 5. | Phase 5 owns 110–119, this plan 120–129 (KTD15). The coordinator rebases only `down_revision`. |
| Engine and backend deploy skew. | Capability gate and versioned checkpoints (KTD16); deploy order engine, backend, flag (U29). |

Risks with mitigations:

| Risk | Mitigation or owner |
|---|---|
| One engine session serialises every automation commit. | KTD1 seam (`session_for`) plus the U36 load gate. A per-tenant worker is the follow-up. |
| Backend and worker clock skew moves `until_time` waits. | Use relative durations where possible (KTD7). `until_time` carries the backend wall clock, and the engine compares it to its own `Clock`. Check NTP in `DEPLOYMENT.md` (U29). |
| Wave dispatch lets a slow branch delay a fast branch's later nodes. | `waiting` on `on` wakes only when every awaited task is terminal (KTD3). Accepted for v1; pinned by a U12 test. A per-node dependency wait is the follow-up if p95 run time matters. |
| A fan-out park must wake the run, and the key-once signal rule makes this subtle. | The gate task in U34 (`waiting({ on: [node], signal: "parked" })`), proven by a spike and tests, and a Stop condition if handlers cannot signal other tasks. |
| Child-conversation LLM nodes may cost more latency than a direct call. | KTD6 fallback to `llm_direct` for a node flagged `lowLatency`. Decided in U14 from the measured p50. |
| Removing `drop_params=True` can surface provider 400s. | U27 per-provider request-shape tests, and a staged rollout of the gateway swap. |
| Node-level streaming of LLM tokens is lost on the engine path. | Documented in U14. The UI shows the node text on completion. |
| Rollout U8 (legacy Deep Work removal) may not be merged when U28 starts. | U28 handles the single import; the soak gates are shared. |
| Legacy chat and agent studio still use pydantic-ai through `create_model`. | KTD13. Retiring them is outside this plan. |
| Cross-plan merge conflicts on engine files. | Merge order rollout, Phase 5, this plan; the Overlap matrix lists every shared file. |

Open questions (genuinely unresolved):

| Question | Owner and when |
|---|---|
| Sharding: is one session enough? | Decided by the U36 measurement, before U29. If the gate fails, a sharding plan ships first. |
| Does anything outside this repo consume `pending` webhook executions (roadmap open question)? U20 changes what a webhook delivery does. | Ops and product: confirm from the production DB and integrations before U20 ships. |
| Production row counts for `workflow_checkpoints`, `workflow_approvals`, `webhook_endpoints`, `subworkflow_executions` and `_backup_orphaned_checkpoints_062`. | Ops: collect and sign off the archive plan before the U32 PR merges. |
| Ledger `kind` for a workflow wait (`review` assumed) and whether Phase 5 exposes a decision-hook registry or U21 adds it. | Phase 5 owner and U21 author, before U21 starts. |
| Can a task handler deliver a signal to a different task (the park gate relies on it)? | U34 spike, first task of the unit. |

## Definition of Done

- U1–U36 are merged with their test scenarios passing. U30, U31 and U32 are separate PRs, each after its soak gates; U36's load gate result is recorded before U29.
- All Verification Contract gates are green in CI.
- AE1–AE10 hold.
- One engine runs Fleet, workflows and Deep Work. No `asyncio.create_task` carries run work. A rolling deploy of backend or engine loses no run.
- `grep -r litellm backend/app` is empty and the dependency is removed.
- The deletions list is done. The five tables and `dbos_workflow_uuid` are dropped by migration 122, with the row counts recorded and no `CASCADE`.
- The UI shows Deep Work, Fleet and workflows with one `RunTree` and the nine-value status enum.
- `DEPLOYMENT.md` lists the new flags, the deploy order (engine, backend, flag), the soak gates and the removed env keys.
- With either flag false (or the capability absent) a run start returns 503 `engine_unavailable`; no legacy path exists.
