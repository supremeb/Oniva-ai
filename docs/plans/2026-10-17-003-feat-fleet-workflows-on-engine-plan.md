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
  5. `reports/Oniva engine roadmap.md` (Phase 6, Decisions, Risks).
- **Stop conditions:** stop and surface instead of guessing if:
  - the engine task runtime cannot carry a workflow run without a storage migration that conflicts with the lease or commit model (U3, U12);
  - a deletion would remove a route or table that something outside this repo still consumes (roadmap open question: "does anything outside this repo consume `pending` webhook executions?");
  - the p95 commit latency of a single engine session under the U31 load test makes automation runs starve chat.
- **Execution profile:**
  - One unit per agent, each in its own git worktree off `main`.
  - Units with disjoint files run in parallel. The Overlap matrix lists every shared file and the order in which to merge.
  - The coordinator verifies, merges and commits.
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

- **In scope:** units U1–U32.
- **Not in scope:**
  - Deep Work on the engine (done) and its rollout plan.
  - Approvals and policy (Phase 5).
  - MCP, observability, evals and ops (Phase 7).
  - Rewriting the workflow builder canvas, or any node-schema change.

#### Deferred to Follow-Up Work

- Session sharding so each tenant's automation runs have their own engine worker. v1 uses `EngineDirectory.default_session_id` behind a `session_for` seam (U1). The U31 load test decides whether this is urgent.
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
  - `workflow_run`: the DAG scheduler. It owns a ready-set, spawns one `workflow_node` child per ready node, joins with `waiting({ on, policy })`, and finalises.
  - `workflow_node`: one node. It runs `prepare`, then optionally `generate`, `wait` or `fanout`, then `finish`.
  - Both live in `engine/src/workflow/`. They contain no node semantics. Every node-specific step is an HTTP call to a backend node gateway (KTD4), the way tools are.
  - This needs a harness extension, `HarnessOptions.tasks`. `Harness.open` (`engine/src/agent/harness.ts`) registers only `generation`, `tool` and `compaction` today (U3).
- **KTD4 (R3, R8, R14). Backend node gateway.**
  - New `backend/app/internal_api/workflow_nodes.py`, mounted under `/internal/workflows/...`, with the same auth, fence and idempotency machinery as `/internal/tools` (`internal_api/invocations.py`: `begin`, `store_outcome`, `hold_write_fence`).
  - Calls are keyed `Idempotency-Key: {runId}:{nodeId}:a{attempt}:{phase}`. A key with a stored outcome returns it without re-running. This is what makes AE9 hold.
  - Routes:
    - `POST /internal/workflows/runs/{runId}/nodes/{nodeId}/prepare`
    - `POST /internal/workflows/runs/{runId}/nodes/{nodeId}/finish`
    - `POST /internal/workflows/runs/{runId}/finalize`
  - `prepare` returns one of four shapes:
    - `final`: a `NodeExecutionResult`-shaped body (status, output, error, tokens, classification, `selectedHandles`).
    - `llm`: a generation request (system, prompt, tier or model, tools, `responseFormat`).
    - `wait`: `{ durationMs }` or `{ untilEpochMs }` and an optional signal key.
    - `fanout`: items, a sub-graph or workflow id, a `parallel` limit, and `failFast`.
  - `finish` receives the generation answer, the signal payload or the fan-out results, and returns `final`.
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
  - A human-in-loop node is `wait` with a signal key plus an optional SLA `until`. A decision is delivered by the signal route (U3). The decision API belongs to Phase 5, so U21 ships a minimal route and records the overlap.
- **KTD8 (R4). Webhooks.**
  - Verification runs on `await request.body()` (raw bytes), with constant-time compare and both `sha256=<hex>` and bare hex accepted, as today.
  - The secret is Fernet-encrypted with the existing `get_encryption_service()` (`app/core/encryption`), stored as `auth_config.secret_enc`. A data migration encrypts existing plaintext values and removes the plaintext key.
  - Idempotency key order:
    1. a configured header (`auth_config.idempotency_header`, default `Idempotency-Key`; GitHub's `X-GitHub-Delivery` is selectable);
    2. the `idempotency_key_path` JMESPath into the body;
    3. a SHA-256 of the raw body.
  - The run's id is `uuid5(trigger.id, key)` and its `requestId` is `wh:{trigger.id}:{key}`. A replay therefore returns the same run, with no 24 h window.
  - The trigger runs as `created_by`. A trigger without one is refused at create time, because the engine needs a user binding (`EngineConversation.user_id`).
- **KTD9 (R5). Scheduler.**
  - The poll loop (`WorkflowSchedulerDaemon._poll_loop`, a `FOR UPDATE SKIP LOCKED` claim) stays. It is a claimer, not run work.
  - Dispatch starts an engine run whose id derives from the `scheduled_workflow_executions.id`.
  - Retries are the `workflow_run` root's own retry: on failure with attempts left it enters `waiting({ until: now + retryDelayMs })` and re-runs from `start` with `attempt + 1`.
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
  - Order: ship dark, tenant opt-in, soak, flip to default (U29), then the deletion units (U30–U32).
  - In-flight legacy runs drain before their code is deleted. The ops gates are recorded in `DEPLOYMENT.md` by U29.
- **KTD13 (R12). LiteLLM replacement is native SDKs.**
  - `openai` and `anthropic` are already direct dependencies (`backend/pyproject.toml`). A new `backend/app/services/llm_direct.py` implements complete, stream and embed for provider types `openai`, `anthropic`, `openrouter`, `azure` and `custom` (`models/llm_provider.py`).
  - Cost comes from `app/config/llm_pricing.py` (`find_model_pricing`, `calculate_cost_with_cache`), never from a library estimate.
  - `LLMGateway.complete` and `.stream` keep their signatures, so the roughly 14 other callers are untouched. `create_litellm_model` becomes `create_model` (pydantic-ai native models, already half-native).
- **KTD14 (R13). The `create_task` guard.** A test walks `backend/app` with `ast`. Every `asyncio.create_task` or `ensure_future` must be in an allow-list file with a one-line reason. Allowed: the scheduler poll loop, observability fire-and-forget, doc-processing helpers, and the reconciler. Each is non-run work.

### Assumptions

- The rollout plan (`2026-10-17-001`) U5 (model timeouts), U6 (usage outbox), U7 (engine on by default) and U8 (legacy Deep Work removal) land before U5, U29 and U32 respectively. Where they have not merged, those units say what to do instead.
- Engine handler concurrency (`settings.concurrency`, default 16 in `harness.ts`) bounds simultaneous running handlers. A `waiting` task holds no slot, so long waits and fan-out joins are free.
- A single engine session (one worker) serves automation runs in v1. Fan-out is bounded per run by `parallel` (default 10, max 50) and per tenant by `max_concurrent`.
- `bash_tool.py` is already absent from the repo. Only `USE_BASH_TOOL` in `backend/app/config/__init__.py` and the `agents/tools/` package remain, and the prompt string at `services/session_memory.py:278` (verified by the research notes, re-grep in U32).
- `workflow_approvals`, `webhook_endpoints` and `subworkflow_executions` have no models. They exist only in databases upgraded from the legacy migration chain, because `100_baseline` builds from `Base.metadata` (`tests/postgres/DRIFT.md`). Their drops are `IF EXISTS`.
- Latest migration is `109_llm_usage_agent_role`. This plan uses 110 (U1), 111 (U21), 112 (U30) and 113 (U32). Renumber at merge if the rollout adds one.
- `subworkflow_executor.py` is a stub today (it logs a warning and returns a fake result). Real subworkflows arrive as a side effect of `fanout` (U12, U15).

### Deletions list

| Item | Replaced by | Deleted in |
|---|---|---|
| `backend/app/services/background_agent_executor.py`, `backend/app/services/intervention.py` | U7, U8 | U31 |
| `fail_stale_deep_work_jobs` and its two callers in `main.py` | engine recovery + U5 reconciler | U30 |
| `WorkflowSchedulerDaemon` run tasks (`_semaphore`, `_active_tasks`, pending reset, `recover_orphaned_executions`) | U19 | U30 |
| `WorkflowOrchestrator` run loop, `_cancellation_flags`, `_running_executions`, `cancel_execution` | U12, U16 | U30 |
| `DurableWorkflowEngine`, `workflow_checkpoint.py`, `checkpoint_cleanup.py`, `/execute-durable`, `/recover`, `/replay`, `/checkpoints` | U12, U16, U17 | U30 |
| Tables `workflow_checkpoints`, `_backup_orphaned_checkpoints_062`, `workflow_approvals`, `webhook_endpoints`, `subworkflow_executions`; column `workflow_executions.dbos_workflow_uuid` | n/a (no consumer) | U32 (migration 113) |
| Dead config: `USE_DURABLE_WORKFLOWS`, `DBOS_DATABASE_URL`, `USE_BASH_TOOL`, `STALE_JOB_THRESHOLD_SECONDS` (if no user remains) | n/a | U32 |
| `agents/tools/` bash remnant, `session_memory.py:278` prompt string | n/a | U32 |
| `litellm` imports, `create_litellm_model`, `_run_with_litellm_thinking`, dependency | U27, U28 | U28 |

---

## Phase map

| Phase | Units | Outcome |
|---|---|---|
| A. Foundations | U1–U5 | Run binding, flags, engine host tasks, run view, completion push |
| B. Fleet | U6–U10 | Fleet agents on the engine, steer, cancel, read model, recovery proof |
| C. Workflows | U11–U22 | Node and run tasks, node gateway, LLM nodes, start/cancel/stream, batch, scheduler, webhooks, waits, proof |
| D. UI run model | U23–U26 | One status enum, one run tree, Fleet and workflow views on it |
| E. Flip, LiteLLM removal and deletions | U27–U32 | Defaults flipped, litellm gone, legacy code and tables deleted |

### Dependency order

```
A: U1 ──┬─ U2
        └─ U3 ── U4 ── U5
B: (U1,U2) ── U6 ── U7 ──┬─ U8
                         └─ U9 (needs U5) ── U10
C: (U3,U4) ── U11 ── U12 ──┐
   (U1,U2) ── U13 ── U14 ──┤
   U12+U13 ──────────────── U15 ── U16 ── U17 (stream) ── U22
                            U15 ── U18 (batch)
                            U15 ── U19 (scheduler) ── U20 (webhooks)
                            U15 ── U21 (waits, human-in-loop)
D: U2 ── U23 ── U24 ── (U25 needs U9, U26 needs U17)
E: U10+U22+U26 ── U29 (flip) ── U30 ── U31 ── U32
   U27 ── U28 (independent of A–D except U14 and rollout U8)
```

Parallel waves, subject to the Overlap matrix:

1. U1, U2, U3 and U27.
2. U4, U6 and U13.
3. U5, U11, U14 and U23.
4. U7 and U12.
5. U8, U9, U15 and U24.
6. U10, U16, U18, U19, U21 and U25.
7. U17, U20 and U28.
8. U22 and U26.
9. U29, then U30, U31 and U32 in sequence.

### Overlap matrix (files touched by more than one unit)

| File | Units, in merge order |
|---|---|
| `backend/app/services/feature_flags.py` | rollout U7, U2, U29 |
| `backend/app/config/__init__.py` | U2, U32 |
| `backend/app/models/engine.py`, `models/workflow.py` | U1, U32 |
| `backend/app/services/engine_client.py` | U2, U8, U15 |
| `backend/app/api/v1/background_jobs.py` | U7, U8, U9, U31 |
| `backend/app/api/v1/workflows.py` | U15, U16, U17, U18, U30 |
| `backend/app/services/workflow_scheduler.py` | U7 (agent dispatch), U19, U30 |
| `backend/app/main.py` | U5, U30 |
| `backend/app/services/startup_recovery.py` | rollout U8 (keep), U30 |
| `backend/app/node_executors/__init__.py` | U13, U14, U15 |
| `backend/app/node_executors/{llm,classifier,guardrail,parameter_extractor,agent}_executor.py` | U14 only (litellm import removed there); U28 verifies |
| `backend/app/services/llm_gateway.py` | U27, U28 |
| `backend/app/agents/base.py` | U27, U28 |
| `engine/src/agent/harness.ts` | U3, U4 |
| `engine/src/server/routes.ts` | U3 |
| `engine/src/watch/view.ts` | U4 |
| `engine/src/worker/worker.ts`, `config.ts`, `scripts/worker.ts` | rollout U5/U6, U5, U12 |
| `engine/src/storage/*` and migrations | rollout U6, U5 (adds a `kind` column to the outbox) |
| `engine/README.md` | rollout U5/U6, U3, U4, U5, U11, U12 |
| `engine/test/support/fake-gateway.ts` | rollout U6 |
| `frontend/lib/engine/deepWork.ts` | U24 (reads only: consumed by U24's adapter) |
| `frontend/components/deep-work/DeepWorkEngineCard.tsx` | U24 |
| `DEPLOYMENT.md` | rollout U7/U9, U29 |

---

## Implementation Units

## Phase A. Foundations

### U1. Automation scope, run binding and migration 110

**Goal:** give every automation run a project, an archived engine conversation row and a domain-row link.
**Requirements:** R1, R3, R14.
**Dependencies:** none.

**Files:**
- `backend/app/services/automation_scope.py` (new)
- `backend/app/models/engine.py` (`EngineConversation.run_kind`, `run_ref_id`)
- `backend/app/models/workflow.py` (`WorkflowExecution.engine_conversation_id`)
- `backend/migrations/versions/110_engine_runs.py` (new)
- `backend/app/services/project_service.py` (`list_projects` hides automation projects)
- `backend/app/services/engine_directory.py` (`session_for`)
- `backend/tests/internal/test_automation_scope.py` (new)

**Approach:**
- `ensure_automation_project(factory, tenant_id, owner_id)` finds the tenant's project whose `settings` contains `{"system": "automations"}`, or creates it once. The create is race-safe: an advisory lock keyed on the tenant, or a unique partial index.
- `bind_run_conversation(factory, kind, ref_id, tenant_id, user_id)` returns `(conversation_id, project_id, session_id)`.
  - The conversation id is `uuid5(RUN_NAMESPACE, f"{kind}:{ref_id}")`.
  - It inserts an archived `EngineConversation` with `run_kind` and `run_ref_id`, and is a no-op if the row exists.
- Migration 110 is idempotent (`ADD COLUMN IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS` on `(run_kind, run_ref_id)`), following `109_llm_usage_agent_role.py`.
- `list_projects` and the chat default-project resolution never return an automation project.

**Test scenarios:**
- First call creates the project. A second call and a concurrent call return the same one.
- `bind_run_conversation` called twice returns the same conversation id and writes one row.
- Two tenants get separate projects.
- The automation project is absent from `list_projects` for its owner, and `EngineConversation` rows with `run_kind` are absent from the chat conversation union.
- `session_for` returns `default_session_id`.
- Migration upgrade then downgrade then upgrade is clean on Postgres.

**Verification:** backend ruff and pytest green; migration test on Postgres.

### U2. Flags, engine client run methods and the status mapper

**Goal:** the switches and client calls every later backend unit uses.
**Requirements:** R1, R3, R9.
**Dependencies:** none (parallel with U1).

**Files:**
- `backend/app/config/__init__.py` (`ENABLE_ENGINE_FLEET`, `ENABLE_ENGINE_WORKFLOWS`)
- `backend/app/services/feature_flags.py` (`engine_fleet_enabled`, `engine_workflows_enabled`, into `get_tenant_features`)
- `backend/app/services/engine_client.py` (`start_run`, `signal_task`, `get_task`)
- `backend/app/services/run_status.py` (new)
- `backend/tests/test_engine_run_flags.py` (new)
- `backend/tests/internal/test_run_status.py` (new)
- `backend/tests/fixtures/run_status_map.json` (new; shared with U23)

**Approach:**
- The flags follow `engine_deep_work_enabled`. During this plan they are opt-in per tenant. U29 flips them to opt-out, the same way rollout U7 did.
- `EngineClient.start_run(session, tenant, project, body)` posts `POST /v1/runs` (the route lands in U3) with `Idempotency-Key`. `signal_task(...)` posts the signal route.
- `run_status.py` exposes `normalize(legacy_value, source)`. It maps legacy job and execution values (`pending`, `running`, `completed`, `failed`, `cancelled`, `error`, `success`) to the KTD10 enum. `from_engine_task(status, wake, resumed)` maps task states.
- The fixture file is the single mapping table that both Python and TypeScript tests assert.

**Test scenarios:**
- Flag matrix: global off beats tenant on; tenant absent follows the opt-in default; `ENGINE_URL` or token missing disables.
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
- `engine/src/server/routes.ts` (`POST /v1/runs`, `POST /v1/conversations/:id/tasks/:taskId/signal`, `GET /v1/conversations/:id/tasks/:taskId`)
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

**Test scenarios:**
- A test-only host task (two phases, a deadline wait) registered via `tasks` runs to `done` through `POST /v1/runs`.
- A repeat `POST /v1/runs` with the same `id` spawns once.
- A signal delivered to a task waiting on that key wakes it. A duplicate signal is rejected 409. A signal to a finished task is 409. A signal to another conversation's task is 404.
- A unknown task kind is 400, and a registry duplicate kind throws at open.
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

### U5. Run-finished push, durable outbox and the reconciler

**Goal:** the backend learns Fleet run completion without polling, and a crash in between is still caught.
**Requirements:** R1, R10, R13.
**Dependencies:** U1, U4, rollout U6 (outbox).

**Files:**
- `engine/src/worker/run-notifier.ts` (new)
- `engine/src/worker/worker.ts` (register the notifier on `onSettled`)
- `engine/src/storage/types.ts`, the Postgres and memory storage, `engine/src/storage/conformance.ts`, `engine/migrations/003_outbox_kind.sql` (generalise the usage outbox with `kind`, `payload`)
- `backend/app/internal_api/runs.py` (new: `POST /internal/runs/finished`)
- `backend/app/internal_api/router.py` (mount)
- `backend/app/services/run_reconciler.py` (new)
- `backend/app/main.py` (start and stop the reconciler loop)
- `engine/test/worker/run-notifier.test.ts` (new)
- `backend/tests/internal/test_run_finished.py` (new)
- `backend/tests/test_run_reconciler.py` (new)

**Approach:**
- The notifier fires only for conversations whose agent `metadata.notify === "backend"` (set by U7), so chat and Deep Work are untouched. It enqueues `{ kind: "run_finished", conversationId, submissionId, status, usage }` into the outbox, then posts with the existing retry policy and acks on 2xx.
- `POST /internal/runs/finished` resolves `run_kind`/`run_ref_id` from `engine_conversations`, dispatches to a registered handler (`fleet` here, `workflow` for U16), is idempotent per `(run_ref_id, submissionId)`, and returns 200 even for an already-finished row.
- Reconciler:
  - Every 60 s, under `pg_try_advisory_lock`, it selects automation rows still `running` or `pending` whose `updated_at` is older than `RUN_RECONCILE_AFTER_S` (default 120).
  - For each it reads the engine task and conversation snapshot and calls the same handler.
  - A row whose engine conversation does not exist is failed with `engine_run_missing`.
  - It is non-run work and is on the U32 guard allow-list.
- If rollout U6 has not merged, add the outbox table here and note the duplicate in the PR.

**Test scenarios:**
- Settled submission with the backend down: row stays pending, delivered once after recovery.
- A worker restart with a pending row delivers it after start.
- A conversation without the `notify` tag produces no outbox row.
- The finished handler called twice updates once.
- The reconciler settles a row whose finished post was lost, and fails a row with no engine conversation.
- Two reconcilers: the second takes no lock and does nothing.
- A row `updated_at` newer than the threshold is left alone.

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
**Dependencies:** U1, U2, U6.

**Files:**
- `backend/app/services/fleet_engine.py` (new: `start_fleet_run`)
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
- A failure to reach the engine leaves the job `pending` with `error` set, and the reconciler retries the start (bounded).
- Gate each dispatch site on `engine_fleet_enabled(tenant)`. Off means the legacy `run_background_agent` path is untouched until U31.
- `_poll_agents_once` no longer calls `asyncio.create_task` for engine tenants.

**Test scenarios:**
- A manual create with the flag on calls `create_conversation` once and `submit` once with the expected `requestId`, and spawns no asyncio task.
- A repeated start (reconciler retry) does not create a second conversation or submission.
- The flag off keeps the legacy path.
- The scheduler dispatch for an engine tenant creates the job via `check_and_create_background_job` (concurrency limit still enforced) and starts the engine.
- Engine unreachable: the job stays `pending` with an error, and no exception escapes `_poll_agents_once`.

**Verification:** backend ruff and pytest green.

### U8. Fleet steer and real cancel

**Goal:** intervene is a steer; cancel stops work.
**Requirements:** R2.
**Dependencies:** U7.

**Files:**
- `backend/app/api/v1/fleet.py` (`intervene_in_agent`)
- `backend/app/api/v1/background_jobs.py` (`cancel_job`)
- `backend/app/services/engine_client.py` (no new method: `submit` with `whenBusy`, `abort`)
- `backend/tests/test_fleet.py`, `backend/tests/test_intervention.py` (extend)
- `backend/tests/test_fleet_cancel_engine.py` (new)

**Approach:**
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
- An engine outage on cancel still marks the job `cancelled` and records `abort_pending` so the reconciler retries the abort.

**Verification:** backend ruff and pytest green.

### U9. Fleet read model: finished handler, trace and stream

**Goal:** `background_jobs` rows, the Fleet list and delivery reflect engine runs.
**Requirements:** R1, R2, R9, R10.
**Dependencies:** U5, U7.

**Files:**
- `backend/app/services/fleet_run_sync.py` (new: the `fleet` handler for `/internal/runs/finished` and the reconciler)
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
- Contract fixtures match the live output (`ONIVA_RECORD_CONTRACTS=1` to record, reviewed in the diff).

**Verification:** backend ruff and pytest green.

### U10. Fleet end-to-end recovery proof

**Goal:** prove AE1 and AE2 against a real engine process.
**Requirements:** R1, R2, R10.
**Dependencies:** U8, U9.

**Files:**
- `backend/tests/e2e/test_fleet_engine_recovery.py` (new)
- `backend/tests/e2e/backend_app.py`, `dw_script.py`/`dw_fakes.py` (extend with a Fleet script and fake search tool)
- `frontend/e2e/fleet-engine-recovery.spec.ts` (new, runs after U25)

**Approach:** follow `test_deep_work_engine_recovery.py`: scripted fake model, real worker process, `kill -9` mid-run via the harness in `tests/e2e/harness.py`.

**Test scenarios:**
- Kill the worker mid-run, restart, the run completes once, one delivery, one ledger row per model request.
- Steer during a run reaches the next model request.
- Cancel during a long tool call: no later tool call starts, and the job stays `cancelled`.
- The backend restarts mid-run: nothing is lost.

**Verification:** `ONIVA_E2E=1 uv run pytest -m e2e tests/e2e` green.

## Phase C. Workflows

### U11. Engine: workflow backend contract, fake backend and the node task

**Goal:** the `workflow_node` task and the normative contract for the node gateway.
**Requirements:** R3, R5, R6, R8.
**Dependencies:** U3, U4.

**Files:**
- `engine/src/workflow/node-gateway.ts` (new: HTTP client, fence headers, retry and `Retry-After` on the pattern of `src/tools/gateway.ts`)
- `engine/src/workflow/node-task.ts` (new: `workflow_node` `TaskDefinition`)
- `engine/src/workflow/types.ts` (new: `PrepareResult`, `FinishInput`, `NodePolicy`)
- `engine/src/index.ts` (exports)
- `engine/test/support/fake-workflow-backend.ts` (new; normative TSDoc with `[clause:]` tags, like `fake-gateway.ts`)
- `engine/test/workflow/node-task.suite.ts`, `node-task.memory.test.ts`, `node-task.postgres.test.ts` (new)
- `backend/tests/internal/test_contract_conformance.py` (matching clause tests, added in U13)
- `engine/README.md`

**Approach:** phases and transitions:
- `prepare`:
  - Calls the backend with `Idempotency-Key {runId}:{nodeId}:a{attempt}:prepare`.
  - `final` → `done(result)`.
  - `llm` → spawn a tool-less child conversation (KTD6), then `waiting({ on: [childAnsweringTask] })` in phase `finish`.
  - `wait` → `waiting({ until })` (duration relative to `clock.now()`), plus `signal` if given, in phase `finish`.
  - `fanout` → handled by the run task (U12); the node task stores the request and returns it as its `done` value of kind `fanout`.
- `finish`: reads the child's committed answer, the signal payload, or the deadline wake, calls the backend `finish`, and ends `done`.
- Policy:
  - A node call that is a replay-safe type (declared in the prepare response) is re-run on recovery.
  - Otherwise it is reconciled by the idempotency key, like write tools. If the outcome is unknown, the result is `interrupted`.
  - `retry` (`maxRetries`, `backoffMs` via a deadline wait) and `timeoutMs` apply per attempt.
  - `onError: continue` converts a failed final to a recorded failure the run task treats as settled.
- All phases tolerate re-run (the `define.ts` contract).

**Test scenarios** (memory and Postgres, same suite):
- A `final` prepare completes the node once. A worker restart between prepare and commit re-asks the backend with the same key and gets the stored outcome.
- An `llm` prepare spawns exactly one child conversation (no duplicate after a restart), and the answer reaches `finish`.
- A `wait` of 2 h completes when the ManualClock passes the deadline, not before.
- A `wait` with a signal key wakes on the signal.
- Retry: failure, backoff wait, success on attempt 2 with a new key.
- `timeoutMs` ends an in-flight call as `failed(timeout)`.
- `abort` during `wait` ends the node `aborted` and runs `onAbort`.
- A non-replay-safe node cut off mid-call yields `interrupted` when the backend has no stored outcome.
- The abort path aborts the child conversation first (bottom-up).

**Verification:** engine lint, typecheck and vitest green; clean-room grep empty.

### U12. Engine: the workflow run task and DAG scheduler

**Goal:** `workflow_run` schedules a compiled graph with joins, branches, fan-out, retries and finalise.
**Requirements:** R3, R5, R7.
**Dependencies:** U11.

**Files:**
- `engine/src/workflow/run-task.ts` (new)
- `engine/src/workflow/graph.ts` (new: ready-set, skip propagation, branch handles)
- `engine/src/worker/config.ts`, `engine/src/worker/worker.ts`, `engine/scripts/worker.ts` (`ENGINE_WORKFLOW_BACKEND_URL`, default `ENGINE_GATEWAY_URL`; register `workflow_run` and `workflow_node` via `hostTasks`)
- `engine/README.md` (env row, task kinds, view `tasks` fields)
- `engine/test/workflow/graph.test.ts`, `engine/test/workflow/run-task.suite.ts`, `run-task.memory.test.ts`, `run-task.postgres.test.ts` (new)
- `engine/test/worker/config.test.ts`

**Approach:**
- Input: `{ runId, graph, variables, errorMode, retry?: { max, delayMs }, parallel? }`.
- Phases:
  - `start`: spawn every ready node as a `workflow_node` child (the owning task is the run).
  - `join`: `waiting({ on: readyChildren, policy })`, where `policy` is `failFast` for `errorMode: stop` and `allSettled` for `continue`. On wake, read each child's committed outcome and update node states in the checkpoint, apply branch selection (`selectedHandles`) and skip propagation, then spawn the newly ready nodes or finalise.
  - `fanout`: a node outcome of kind `fanout` makes the run spawn child `workflow_run` tasks.
    - It starts at most `parallel` at a time, tracked in the checkpoint, so a restart does not exceed the limit.
    - It gathers results in item order and passes them to the node's `finish`.
    - The child's input is the sub-graph (iteration) or the compiled graph of the referenced workflow (subworkflow).
  - `finalize`: calls the backend `finalize` (idempotent) with the status, node summaries and error, then `done`.
  - `retry`: when the run failed and attempts remain, `waiting({ until: now + delayMs })` then back to `start` with `attempt + 1`. Node keys include the attempt, so retries re-run.
- A run-level `deadlineMs` aborts the run with `run_deadline`.
- Node states are visible through U4's `view.tasks` (the run's own `view` fn adds a compact `nodes` summary: `{ nodeId: status }`).
- Cap: graph size is limited by the backend validator (`MAX_WORKFLOW_NODES = 95`). The engine rejects a graph over 200 nodes.

**Test scenarios** (shared memory and Postgres suite, scripted fake workflow backend):
- Linear 3-node graph runs in order and finalises `completed`.
- Diamond (fork then join): both branches run concurrently, the join node runs once, after both.
- Condition node selecting handle `true`: the `false` branch is `skipped`, its descendants skipped, and a merge node with one live input still runs.
- `failFast`: one branch fails, the sibling in flight is aborted (`siblingFailed`), and the run is `failed` with the failing error.
- `continue` mode: the failure is recorded, the other branch completes, the run is `failed` at the end with all settled.
- Fan-out of 25 items with `parallel: 5`: at most 5 child runs live at any commit (assert via a commit-event observer), results in order, and a restart in the middle keeps the bound.
- Root retry: first attempt fails, wake exactly at `delayMs` (ManualClock), the second attempt completes, and `attempt` is in the keys.
- A restart in the middle of a join yields no duplicate node `prepare` effects (the backend stub counts keys).
- Abort of the container conversation ends all children `aborted` and `finalize` is called once with `cancelled`.
- A cycle or unknown dependency in the graph input is rejected 400 at `POST /v1/runs`, nothing spawned.

**Verification:** engine lint, typecheck and vitest green; clean-room grep empty.

### U13. Backend node gateway and node specs

**Goal:** the backend side of the contract: executors callable by idempotent HTTP.
**Requirements:** R3, R8, R14.
**Dependencies:** U1, U2 (contract from U11).

**Files:**
- `backend/app/internal_api/workflow_nodes.py` (new)
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
- The compiler also resolves a pinned `workflow_version_id` spec, and rejects unsupported nodes with the validator's error. The four builder node types with no executor (`data`, `analysis`, `research`, `schedule_trigger`) are compiled as pass-through `skipped` with a warning event, preserving today's silent skip (roadmap Foundations).

**Test scenarios:**
- A `template` node via `prepare` returns `final` with the rendered output. A repeated key returns the stored outcome and does not re-run (executor call count 1).
- A cross-tenant `tenantId` in the body is 404.
- A fence-rejected request is 409 `fence_rejected`.
- Classification propagates: a `confidential` input blocks an external-calling node with `blocked` and the audit log entry.
- Compiler: condition and classifier nodes expose handles; an edge to an untaken handle is marked for skipping; cycles rejected; the iteration target's reachable set is excluded from the main graph and attached to the iteration node's `fanout`.
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
**Dependencies:** U12, U13.

**Files:**
- `backend/app/services/workflow_engine_runs.py` (new: `start_workflow_run`, `cancel_workflow_run`)
- `backend/app/api/v1/workflows.py` (`/execute`, `/{workflow_id}/run`, `/{execution_id}/cancel`)
- `backend/app/api/v1/workflow_execution.py` (agent-chat `workflow-execute`)
- `backend/app/services/engine_client.py` (`start_run` payload helpers)
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
- `backend/app/services/workflow_run_sync.py` (new: `finalize`, the `workflow` handler for `/internal/runs/finished`, the reconciler hook)
- `backend/app/internal_api/workflow_nodes.py` (`finalize` route calls it)
- `backend/app/models/workflow.py` (no new columns beyond U1)
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
**Dependencies:** U15.

**Files:**
- `backend/app/api/v1/workflows.py` (`execute_batch`, `get_batch`, `get_batch_items`, `cancel_batch`)
- `backend/app/services/workflow_batch_engine.py` (new)
- `backend/app/models/workflow_batch.py` (no schema change; status transitions only)
- `backend/tests/test_workflow_batch_engine.py` (new)

**Approach:**
- One batch is one `workflow_run` whose graph is a single `fanout` node over the items (`parallel = parallel_limit`, `failFast = false`). Each item runs as a child `workflow_run`.
- The finalize handler (`kind = batch`) updates `workflow_batch_items` and the batch counters. Webhook notify on completion uses `webhook_service.send_webhook` with HMAC over the raw payload and the stored `webhook_secret`, SSRF-checked at create (already done).
- The `TODO` comment and the queued-forever state are removed. `max 1000` and the quota check stay.

**Test scenarios:**
- 25 inputs at `parallel_limit=5`: all item executions complete, with counts `completed=25`.
- The limit is respected (observer on the engine test harness).
- An item failure marks that item `failed` and the batch `completed_with_errors`, with the others unaffected.
- Cancel stops pending items (`cancelled`) and aborts running ones.
- The completion webhook fires once with a valid signature.
- A batch over 1000 is 400 and a foreign workflow is 404.

**Verification:** backend ruff and pytest green.

### U19. Scheduler dispatch and retries on the engine

**Goal:** scheduled runs start through the engine, and retries really run.
**Requirements:** R5, R10, R13.
**Dependencies:** U15, U16.

**Files:**
- `backend/app/services/workflow_scheduler.py` (`_poll_once`, `_execute_one`, `dispatch_immediate`, `trigger_now`)
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
- A scheduler restart between claim and start: the reconciler (U5) starts the missing run, or the execution is retried, with no duplicate (the deterministic id).
- `trigger_now` starts a run and returns the execution.

**Verification:** backend ruff and pytest green.

### U20. Executing webhooks: raw-body HMAC, encrypted secrets, idempotency

**Goal:** inbound webhooks verify correctly and start runs.
**Requirements:** R4, R14.
**Dependencies:** U15 (workflow runs) and U7 (agent triggers).

**Files:**
- `backend/app/api/v1/webhooks.py` (`receive_webhook` reads `await request.body()`; create route returns the secret once)
- `backend/app/services/webhook_trigger_service.py`
- `backend/app/models/webhook_trigger.py` (no new columns; `auth_config.secret_enc`)
- `backend/migrations/versions/111_encrypt_webhook_secrets.py` (data migration)
- `backend/app/services/webhook_secrets.py` (new: encrypt, decrypt, mask)
- `backend/tests/test_webhook_trigger_engine.py` (new)
- `backend/tests/test_webhook_filter_security.py` (extend)

**Approach:**
- `process_webhook` takes the raw bytes plus parsed JSON. HMAC is `hmac.compare_digest` over `hmac.new(secret, raw, sha256)`.
  - If `auth_config.timestamp_header` is set, a timestamp tolerance (default 300 s) is enforced over `ts.raw`.
- A new trigger generates its own secret when `auth_method = hmac` and none is given. The secret is returned once in the create response and stored only as `secret_enc`. List and get responses never include it.
- Migration 111 encrypts every plaintext `auth_config.secret` and `key` (api_key method) and removes the plaintext key. It is idempotent and has a downgrade that refuses (data is not reversibly plaintext).
- The idempotency key order is in KTD8. The run id is `uuid5(trigger.id, key)`. The `WebhookDelivery` row keeps the audit and rate limit.
- Dispatch: `workflow_id` triggers call `start_workflow_run(trigger="webhook", request_key=...)`. `agent_id` triggers call `start_fleet_run` through `check_and_create_background_job` (the concurrency limit applies). A response of 202 returns the execution or job id.
- The `rate_limit_per_hour` count query uses `COUNT(*)` rather than loading rows.

**Test scenarios:**
- A body with extra whitespace and reordered keys, signed over the raw bytes, is accepted. The same signature over re-serialised JSON is rejected (the old behaviour).
- A one-byte tamper gives 401, and a missing signature gives 401.
- The stored `auth_config` JSON contains no plaintext secret; the migration test seeds a plaintext secret and checks it is encrypted and verifies afterwards.
- The same delivery posted twice returns the same execution id and creates one run.
- A different `Idempotency-Key` with an identical body creates a second run.
- A filter mismatch returns 200 and starts nothing.
- A trigger with `created_by = NULL` is refused at create.
- An agent trigger starts a Fleet job, and at the concurrency limit returns 429-style `ConcurrentLimitExceeded` handling (202 with `queued=false`, documented).
- IP allow-list, API key and rate-limit paths keep their tests.

**Verification:** backend ruff and pytest green.

### U21. Wait nodes and human-in-loop on deadline and signal waits

**Goal:** real durations and a real approval wait.
**Requirements:** R6.
**Dependencies:** U12, U13.

**Files:**
- `backend/app/node_executors/wait_executor.py` (`prepare` returns `wait`; the 300 s constant is deleted)
- `backend/app/node_executors/human_in_loop_executor.py` (`prepare` returns `wait` with a signal; `finish` maps the decision)
- `backend/app/api/v1/workflows.py` (new `POST /workflows/executions/{execution_id}/nodes/{node_id}/decision`)
- `backend/app/services/engine_client.py` (`signal_task`)
- `backend/tests/workflows/test_wait_node_engine.py` (new)
- `backend/tests/workflows/test_human_in_loop_engine.py` (new)

**Approach:**
- `duration` returns `{ durationMs }`. `until_time` and `business_hours` return `{ untilEpochMs }` computed with `ZoneInfo` as today. The `maxWaitSeconds` cap stays as a validation error.
- Human-in-loop returns `wait` with `signal = "decision:{node_id}"` and `untilEpochMs` from `slaHours`. Wake reason `deadline` runs `finish` with `decision = "sla_expired"`, which follows the node's escalation config.
- The decision route checks the caller against the node's `approverType` and `approverConfig` (existing fields), then signals the node task. The stored node task id comes from the trace step written at prepare.
- Phase 5 will own the global approvals inbox. This route is deliberately minimal and the overlap is recorded in the PR.

**Test scenarios:**
- `duration: 2 hours` returns `durationMs = 7_200_000`. A zero wait returns `final` immediately (no task wait).
- `until_time` in the past returns `final` with no wait. `business_hours` outside hours waits until the next window start in the node's timezone.
- A wait above `maxWaitSeconds` fails the node with the existing message.
- The decision route with an unauthorised user is 403, and an authorised approve resumes the run. The first decision wins and a second is 409.
- SLA expiry follows the escalation path, using the engine clock.
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
- `frontend/e2e/workflow-engine-recovery.spec.ts` (new, after U26)

**Test scenarios:**
- Kill the worker mid-join. After restart the run completes, the HTTP node's external POST happens once, and the ledger has one row per model request.
- Restart the backend pod mid-run. No effect on the run.
- A branch workflow runs only the taken branch.
- A two-hour wait on the shared test clock survives a restart, then completes when the clock advances.
- A webhook with a valid raw-body signature starts a run. A replay returns the same run.
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

**Verification:** frontend tsc and jest green; Playwright `fleet-engine-recovery` (U10).

### U26. Workflow run views on the run model

**Goal:** logs and test-run panels show node tasks as a tree, with waiting and recovery states.
**Requirements:** R3, R6, R9.
**Dependencies:** U17, U24.

**Files:**
- `frontend/hooks/useWorkflowLogs.ts`, `frontend/lib/api/workflow-logs.ts`
- `frontend/components/workflow/LogsPanel.tsx`, `LogDetailView.tsx`, `TestRunPanel.tsx`
- `frontend/lib/api/workflows.ts` (remove `recover`/`replay`/`checkpoints` client calls if any; `executeWorkflowStream` stays on the adapter or moves to the engine watch)
- `frontend/lib/stores/workflow.ts` (node status uses `RunStatus`)
- `frontend/components/workflow/__tests__/` (extend or add `LogDetailView.test.tsx`)
- `frontend/e2e/workflow-engine-recovery.spec.ts`

**Approach:**
- Active engine runs read the engine watch (`view.tasks` through `engineView` adapter); finished runs read the stored trace through the `legacy` adapter, so both render through `RunTree`.
- Node status on the canvas uses the same enum, including `waiting` (a timer or a human decision) with the wake time and an Approve/Reject control that calls the U21 decision route.
- The "Recover"/"Replay" buttons, if present, are removed in favour of automatic resume.

**Test scenarios:**
- A live run updates node badges as tasks change. A skipped branch shows `skipped`.
- A waiting node shows its wake time and an approve control for authorised users.
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

### U29. Flip defaults, soak gates and rollout docs

**Goal:** engine Fleet and workflows are on by default for tenants, with ops gates written down.
**Requirements:** R1, R3, R10, R11.
**Dependencies:** U10, U22, U26.

**Files:**
- `backend/app/services/feature_flags.py` (the two new flags become opt-out)
- `backend/tests/test_engine_run_flags.py`
- `DEPLOYMENT.md` (feature table; soak gates; worker sizing; how to drain legacy runs)
- `frontend/lib/` feature map users (`useFeatures`-style hooks) only if a type needs the new keys

**Approach:** same staging as rollout U7. Document the soak gates that U30–U32 depend on:
- no `background_jobs` row with `status in (pending, running)` and no `input_data.engine`;
- no `workflow_executions` row `running` without `engine_conversation_id`;
- no `scheduled_workflow_executions` row `pending` with `retry_count > 0` (the dead retry state);
- a week with the ledger check clean.

**Test scenarios:**
- No tenant setting with globals on: enabled. An explicit `false` disables. A global off disables.
- `get_tenant_features` agrees.
- A tenant opted out keeps the legacy path for new runs (until U30).

**Verification:** backend ruff and pytest green.

### U30. Delete the legacy workflow machinery and the boot sweep

**Goal:** remove `DurableWorkflowEngine`, the orchestrator loop, checkpoint code, the in-process cancel maps, scheduler run tasks and the mark-failed sweep.
**Requirements:** R11, R13.
**Dependencies:** U29 and its soak gates (ops).

**Files:**
- `backend/app/services/durable_workflow_engine.py` (delete)
- `backend/app/services/checkpoint_cleanup.py` (delete)
- `backend/app/models/workflow_checkpoint.py` (delete; the table drop is U32)
- `backend/app/services/workflow_orchestrator.py` (delete the run loop, `_cancellation_flags`, `_running_executions`, `cancel_execution`, `execute_workflow_stream`; keep `BranchState` only if U13 imports it, else delete and move)
- `backend/app/api/v1/workflows.py` (`/execute-durable`, `/{uuid}/recover`, `/{uuid}/replay`, `/{uuid}/checkpoints`, legacy branches in `/execute`, `/run`, `/execute-stream`)
- `backend/app/services/workflow_scheduler.py` (`_semaphore`, `_active_tasks`, `_execute_with_release`, the pending-reset block, `recover_orphaned_executions`)
- `backend/app/services/startup_recovery.py` (`fail_stale_deep_work_jobs`), `backend/app/main.py` (both callers and the call to `daemon.recover_orphaned_executions`)
- `backend/app/node_executors/*` (the legacy `execute` wrappers that exist only for the orchestrator are removed)
- `backend/tests/test_workflows_durability.py`, `test_checkpoint_persistence.py`, `test_workflow_orchestrator.py`, `tests/integration/test_architecture_2.py` (delete or port to the engine path)
- `backend/tests/test_no_run_create_task.py` (new; KTD14 guard, with the allow-list)

**Approach:**
- Before deleting, list every caller (grep) and confirm each is on the engine path. Keep `startup_recovery.interrupt_stale_tool_invocations` and `fail_stale_knowledge_documents`. They are not run machinery.
- `audit.py` and `workflow_replay.py` keep working from `execution_trace`.
- No migration here: the table drops are U32, after the code is gone, so a rollback of this PR does not hit a missing table.

**Test scenarios:**
- No import of a deleted module remains (import smoke test of `app.main`).
- The openapi schema has none of the removed routes and keeps `/execute`, `/run`, `/execute-stream`, `/execute-batch`, cancel and logs.
- The `create_task` guard test passes, and fails when a new `asyncio.create_task` with run work is added in a fixture file.
- The scheduler still claims and starts runs after the deletion.
- The lifespan starts and stops cleanly with no sweep.

**Verification:** backend ruff and pytest green; e2e green; Playwright suites green.

### U31. Delete legacy Fleet code and the Redis intervene queue

**Goal:** remove `background_agent_executor.py`, `intervention.py` and the legacy branches.
**Requirements:** R11, R13.
**Dependencies:** U30 (same soak), U25.

**Files:**
- `backend/app/services/background_agent_executor.py` (delete; the pure helpers live in `fleet_engine_agent.py` since U6)
- `backend/app/services/intervention.py` (delete)
- `backend/app/api/v1/background_jobs.py` (legacy spawn, legacy stream, `job.cancel()`-only path), `backend/app/api/v1/fleet.py`
- `backend/app/services/workflow_scheduler.py` (legacy agent dispatch branch)
- `frontend/hooks/useFleetJobStream.ts`, `frontend/lib/fleet/traceToMessageParts.ts` (delete the trace path once no legacy rows are reachable; keep read-only history if old rows exist)
- `backend/tests/test_background_agent_executor.py`, `test_intervention.py` (delete), `test_fleet.py`, `test_agent_scheduler.py` (port)
- `backend/tests/contracts/fixtures/fleet_*.json` (old fixtures replaced by the `fleet_engine_*` ones; delete the legacy driver in `streams.py`)

**Approach:** keep a read-only adapter for historical `background_jobs` rows (`execution_trace` remains a column). Redis stays a dependency for other uses, and only the `fleet:intervention:*` keys disappear.

**Test scenarios:**
- Importing `app.main` works with the modules gone.
- An old job row still opens read-only in the Fleet page.
- Intervene and cancel work only through the engine path.
- `grep -rn "fleet:intervention" backend` is empty.

**Verification:** backend and frontend suites green; e2e green.

### U32. Table drops, dead config and the exit-criteria guards

**Goal:** migration 113 and the last cleanups, then prove the exit criteria.
**Requirements:** R11, R12, R13.
**Dependencies:** U28, U30, U31.

**Files:**
- `backend/migrations/versions/113_drop_legacy_workflow_tables.py` (new)
- `backend/app/models/workflow.py` (remove `dbos_workflow_uuid`)
- `backend/app/config/__init__.py` (`USE_DURABLE_WORKFLOWS`, `DBOS_DATABASE_URL`, `USE_BASH_TOOL`; `STALE_JOB_THRESHOLD_SECONDS` if unused after U30)
- `backend/app/agents/tools/__init__.py` (delete if nothing imports it), `backend/app/services/session_memory.py` (the "Use bash_tool" prompt string at ~278)
- `backend/tests/postgres/DRIFT.md` (remove the drift entries)
- `backend/tests/test_exit_criteria.py` (new)
- `backend/tests/e2e/test_rolling_deploy.py` (new)
- `DEPLOYMENT.md` (env removals)

**Approach:**
- Migration 113 drops, all `IF EXISTS ... CASCADE`: `workflow_checkpoints`, `_backup_orphaned_checkpoints_062`, `workflow_approvals`, `webhook_endpoints`, `subworkflow_executions`; and `ALTER TABLE workflow_executions DROP COLUMN IF EXISTS dbos_workflow_uuid`. The downgrade recreates the empty `workflow_checkpoints` and the column only, and states that dropped data is not restored.
- First check each table for rows on the production-like seed and list the counts in the PR. A non-empty `workflow_approvals` or `webhook_endpoints` stops the unit (Stop conditions).
- RLS: confirm no leftover policy objects reference the dropped tables (the baseline created them for every `tenant_id` table).
- `test_exit_criteria.py`: `grep litellm` empty; the AST guard; no import of any deleted module; the config has none of the dead keys.
- `test_rolling_deploy.py`: restart the backend and then the engine worker during a workflow run and a Fleet run, and assert both complete once (R10, AE9).

**Test scenarios:**
- Upgrade on a database built from the legacy chain (with the extra tables) and on a fresh baseline: both succeed. Downgrade then upgrade is clean.
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
| migrations | upgrade, downgrade and upgrade on Postgres for 110, 111, 113; legacy-chain database for 113 |
| hygiene | gitleaks clean; no `.env` committed; no secret in engine or backend logs (redaction test) |
| exit criteria | `grep -r litellm backend/app` empty; `tests/test_no_run_create_task.py` passes; rolling-deploy e2e passes |

### Load gate (ops, before U29 flips the defaults)

Run 200 concurrent workflow runs of 10 nodes plus 20 Fleet runs against one worker. Record commit latency and chat first-token latency. If chat p95 regresses by more than 20%, stop and open the sharding follow-up before U29's flip.

## Risks and Open Questions

| Risk or question | Mitigation or owner |
|---|---|
| One engine session serialises every automation commit. | KTD1 seam (`session_for`) plus the load gate. A per-tenant worker is the follow-up. |
| Backend and worker clock skew moves `until_time` waits. | Use relative durations where possible (KTD7). `until_time` carries the backend wall clock, and the engine compares it to its own `Clock`. Check NTP in `DEPLOYMENT.md` (U29). |
| Child-conversation LLM nodes may cost more latency than a direct call. | KTD6 fallback to `llm_direct` for a node flagged `lowLatency`. Decided in U14 from the measured p50. |
| Human-in-loop overlaps Phase 5 approvals. | U21 ships a minimal route behind the node's own `approverType` check. Phase 5 replaces the route, not the engine wait. |
| Webhook triggers with no `created_by` exist. | U20 backfills `created_by` from the workflow owner in migration 111 and reports unresolvable rows. |
| Does anything outside the repo consume `pending` webhook executions? | Roadmap open question. Check the production DB for non-test rows before U20 ships. |
| `workflow_approvals` and similar tables may hold data on legacy-chain databases. | U32 stop condition: report row counts first. |
| Removing `drop_params=True` can surface provider 400s. | U27 per-provider request-shape tests, and a staged rollout of the gateway swap. |
| Node-level streaming of LLM tokens is lost on the engine path. | Documented in U14. The UI shows the node text on completion. |
| Rollout U8 (legacy Deep Work removal) may not be merged when U28 starts. | U28 handles the single import; the soak gates are shared. |
| Legacy chat and agent studio still use pydantic-ai through `create_model`. | KTD13. Retiring them is outside this plan. |

## Definition of Done

- U1–U32 are merged with their test scenarios passing. U30, U31 and U32 are separate PRs, each after its soak gates.
- All Verification Contract gates are green in CI.
- AE1–AE10 hold.
- One engine runs Fleet, workflows and Deep Work. No `asyncio.create_task` carries run work. A rolling deploy of backend or engine loses no run.
- `grep -r litellm backend/app` is empty and the dependency is removed.
- The deletions list is done. The five tables and `dbos_workflow_uuid` are dropped by migration 113, with the row counts recorded.
- The UI shows Deep Work, Fleet and workflows with one `RunTree` and the nine-value status enum.
- `DEPLOYMENT.md` lists the new flags, the soak gates and the removed env keys.
