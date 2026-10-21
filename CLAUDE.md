# Oniva: working agreements for Claude Code

## How work is done here

- **The main session leads; Sonnet subagents do the hands-on work.** Implementation, tests, QA passes, fixes and research go to Sonnet subagents (`model: "sonnet"`, `isolation: "worktree"` for code changes). The main session plans, splits work into briefs with disjoint file scopes, reviews and integrates the results, verifies, merges, and reports insights to the user.
- **Briefs include:**
  - the goal;
  - the files the agent may and may not touch (name the files other agents own);
  - the branch to create from `origin/main`;
  - failing test first, then the fix;
  - the exact verification commands;
  - a commit ending with the attribution line, and no pushing;
  - a final report listing per-item status, files, tests, branch and worktree.
- **Before every push:** `gitleaks git --log-opts="origin/main..HEAD" --redact`. Never commit secrets.
- **Never read, print or create `.env` files.** To check whether a variable is set, use `grep -c '^NAME=.\+' file`. Load values through `uv run --env-file .env …` inside scripts that never print them.
- **Merge only when CI is green** on the exact head commit. Remove worktrees after merging; disk is tight.

## Where things live

Paths are repo-relative. `*` = several files; `ls` the folder.

### engine/ (`@oniva/engine`, TypeScript; src/index.ts is the public API)
- `engine/src/agent/`: the agent loop and everything a turn touches
  - `generation.ts` (model call + tool loop), `context-builder.ts` (prompt/context assembly), `prompt.ts`, `compaction.ts` (summarising old turns)
  - `inbox.ts`, `submission.ts` (user messages, steering, follow-ups); `conversation.ts`; `policy.ts`, `taint.ts`, `hooks.ts`
  - `approvals.ts`, `questions.ts`, `ask-user-tool.ts` (human-in-the-loop waits); `subagents.ts`; `tool-task.ts` (one tool call as a task)
  - `harness.ts` (wires the pieces), `usage.ts`, `progress.ts`
- `engine/src/tasks/`: durable task runtime (`runtime.ts`, `scheduler.ts`, `define.ts`, `signals.ts`, `ownership.ts`, `abort.ts`)
- `engine/src/tools/`: `registry.ts` (tool definitions), `gateway.ts` (calls back to backend `/internal/tools/*`)
- `engine/src/workflow/`: host tasks for workflow runs
  - `run-task.ts`, `node-task.ts`, `fanout-lane-task.ts`, `gate-task.ts`; `tasks.ts` (registration)
  - `graph.ts`, `types.ts`, `contract.ts`, `node-gateway.ts` (calls backend `/internal/workflows/*`)
- `engine/src/storage/`: `types.ts` (interface), `memory.ts`, `postgres/storage.ts`, `postgres/migrate.ts`, `postgres/lease.ts`, `conformance.ts` (shared suite)
- `engine/migrations/`: engine SQL (`001_init.sql`, `002_usage_outbox.sql`, `004_outbox_kind.sql`)
- `engine/src/session/`: session state, `publish.ts` (events to watchers), `transaction.ts`, `documents.ts`
- `engine/src/worker/`: the worker process
  - `worker.ts`, `config.ts` (env), `backend-policy.ts` (model/tool policy from backend), `providers.ts`, `run-notifier.ts` (posts run finished), `redact.ts`, `args-hash.ts`
- `engine/src/models/`: `anthropic.ts`, `openai-compatible.ts`, `http.ts` (pool), `retry.ts`, `stream.ts`, `usage.ts`, `fake.ts` (tests)
- `engine/src/server/`: HTTP API (`routes.ts`, `http.ts`, `auth.ts`); `engine/src/watch/`: `view.ts`, `patch.ts`, `stream.ts` (SSE view the frontend consumes)
- `engine/src/core/`: ids, errors, clock, limits, context
- `engine/scripts/`: `worker.ts` (`npm run worker`), `worker-test-harness.ts` (`npm run worker:test`)
- `engine/test/`: mirrors `src/`; `*.suite.ts` run by `*.memory.test.ts` and `*.postgres.test.ts`; `scenario/` (crash-resume, approvals, steering; `npm run test:scenario`); `support/` (scripted-model, fake-gateway, fake-workflow-backend, pg); `fixtures/`
- `engine/contracts/workflow-nodes/`: JSON schemas + `fixtures/` + `limits.json` shared with backend (checked by `backend/tests/internal/test_contract_conformance.py`)

### backend/app/ (FastAPI; app wiring in `backend/app/main.py`, settings in `backend/app/config/__init__.py`)
- `api/v1/`: browser-facing routers, by domain
  - chat and engine conversations: `chat.py`, `chat_sessions.py`, `engine_conversations.py` (mounted via `mount_engine_conversations`), `agent_chat.py`, `mentions.py`
  - Deep Work: `deep_work.py`, `deep_work_engine.py`
  - workflows: `workflows.py`, `workflow_execution.py`, `workflow_logs.py`, `workflow_versions.py`, `workflow_permissions.py`, `workflow_ai.py`, `workflow_dsl.py`, `schedules.py`, `webhooks.py`, `process_logs.py`
  - fleet and agents: `fleet.py`, `background_jobs.py`, `agents.py`, `custom_agents.py`, `agent_*.py` (versions, deploy, evaluation, studio pieces), `marketplace.py`
  - knowledge and projects: `knowledge_bases.py`, `knowledge_management.py`, `projects.py`, `workspace.py`, `volumes.py`, `artifacts.py`, `memory.py`
  - settings and providers: `settings.py`, `tenant_settings.py`, `llm_providers.py`, `routing_policies.py`, `preferences.py`, `connectors.py`, `skills.py`
  - auth, users, RBAC: `auth.py`, `users.py`, `pending_users.py`, `saml.py`, `oauth_clients.py`; permission map in `backend/app/auth/permissions.py`
  - admin, audit, inbox: `admin.py`, `audit.py`, `inbox.py`, `compliance.py`, `alert_rules.py`
  - usage and analytics: `llm_usage.py`, `analytics.py`, `observability_api.py`
- `api/dependencies/` (`quota.py`), `api/exception_handlers.py`
- `internal_api/`: engine-facing, mounted at `/internal` (`router.py`, token auth in `auth.py`)
  - `models.py` (model policy), `tools.py` + `authorize.py` (tool gateway and authorize), `tool_specs.py` (per-tool policy/timeouts), `invocations.py`, `execution.py`
  - `workflow_nodes.py`, `workflow_contract.py`, `node_specs.py` (workflow node gateway); `runs.py` (`run_finished`), `fence.py`, `schemas.py`
- `services/`: business logic, by domain
  - engine plumbing: `engine_client.py` (start/cancel runs), `engine_directory.py`, `engine_chat_agent.py`, `engine_deep_work_agent.py`, `fleet_engine_agent.py`
  - Deep Work: `deep_work/` (`engine_tools.py`, `engine_sandbox.py`, `engine_view.py`, `synthesis.py`, `prompts/`), `deep_work_state.py`, `deep_work_run_sync.py`
  - workflows: `workflow_compiler.py`, `workflow_engine_runs.py`, `workflow_run_sync.py`, `workflow_batch_engine.py`, `workflow_scheduler.py`, `workflow_wait_decisions.py`, `workflow_validator.py`, `workflow_dsl.py`, `durable_workflow_engine.py`
  - fleet: `fleet_engine.py`, `fleet_run_sync.py`, `background_dispatch.py`, `background_agent_executor.py`
  - run settling: `run_reconciler.py`, `run_handlers.py`, `run_status.py`, `run_event_service.py`
  - approvals, audit, inbox: `approval_service.py`, `audit_chain.py`, `audit_logger.py`, `inbox_service.py`, `tool_policy.py`
  - LLM: `llm_gateway.py`, `llm_direct.py`, `quota_service.py`; RAG: `rag_service.py`, `knowledge_base.py`; webhooks: `webhook_service.py`, `webhook_trigger_service.py`, `webhook_secrets.py`
- `models/`: SQLAlchemy tables. Key ones: `engine.py` (engine runs/bindings), `deep_work_run.py`, `background_job.py`, `tool_approval.py`, `approval_request.py`, `audit_event.py`, `workflow.py`, `tenant.py`, `user.py`, `llm_usage.py`, `chat_session.py`
- `node_executors/`: legacy in-process workflow node executors (`*_executor.py`; the live path until the U29 flip)
- `auth/`: `permissions.py` (`ROLE_PERMISSIONS`, `require_permission`), `dependencies.py` (`require_role`, `require_feature`, `require_platform_operator`), `jwt.py`, `providers/`
- `core/database.py` (sessions, RLS tenant context); `core/encryption.py`; `skills/` (backend skill/tool implementations, one folder each); `schemas/`; `middleware/`; `security/`
- `backend/migrations/versions/`: alembic chain `100_baseline.py` .. `121_encrypt_webhook_secrets.py` (recent: `120_engine_runs.py`, `121_*`); `versions_archive/` is old history

### backend/tests/
- `backend/tests/*.py`: unit tier (default `pytest`, `-m "not postgres"` in CI)
- `internal/`: `/internal` API, contract conformance, compiler, engine agents; `workflows/`: workflow engine behaviour
- `postgres/`: needs real Postgres (`-m postgres`): RLS, migrations, fencing, reconciler; see `postgres/DRIFT.md`
- `e2e/`: real engine + backend stack (`ONIVA_E2E=1`, `-m e2e`): `harness.py`, `stack.py`, `backend_app.py`, scripted fakes (`dw_fakes.py`, `*_script.py`)
- `contracts/`: recorded engine stream fixtures (`fixtures/`, `README.md`); `load/gate.py`: automation load gate; `integration/`, `security/`, `connectors/`, `evaluation/`
- `backend/tests/KNOWN_FAILURES.md`: known red tests

### frontend/ (Next.js app router)
- `frontend/app/(dashboard)/`: routes: `chat/` (+ `projects/`), `fleet/`, `workflows/` (+ `[id]/`), `inbox/`, `knowledge/`, `schedules/`, `agents/`, `connectors/`, `observability/`, `analytics/`, `compliance/`, `memory/`, `settings/*`, `admin/`; `frontend/app/platform-admin/`; `frontend/app/api/chat`
- `frontend/components/chat/`: `ChatPage.tsx`, `EngineChat.tsx` (engine path), `LegacyChat.tsx`, `Composer.tsx`, `ChatSidebar.tsx`, `EngineMessageList.tsx`, `QueuedChips.tsx`, `ProjectView.tsx`
- `frontend/components/deep-work/`: `DeepWorkEngineCard.tsx`, `PlanCard.tsx`, `WorkerActivityCard.tsx`, `WorkerTranscript.tsx`, `engineSession.ts`
- `frontend/components/workflow/`: `FlowEditor.tsx`, `ConfigPanel.tsx`, `NodePalette.tsx`, `nodes/` (one component per node type), `bottom-panel/`, `LogsPanel.tsx`
- `frontend/components/fleet/` (`FleetChat.tsx`, `FleetRunList.tsx`), `inbox/`, `settings/`, `ui/` (primitives)
- `frontend/lib/engine/`: `client.ts` (HTTP), `watcher.ts` + `patch.ts` + `sse.ts` (live view), `viewToMessages.ts` (view to chat parts), `useEngineConversation.ts`, `useEngineDeepWork.ts`, `deepWork.ts`, `deepWorkApi.ts`, `approval.ts`, `run.ts`
- `frontend/lib/api/`: one typed client per backend router (`client.ts` base); `lib/stores/` (zustand: `auth.ts`, `workflow.ts`, `deep-work.ts`, `engine-nav.ts`); `lib/run/`, `lib/sse/`, `lib/chat/parts/`
- `frontend/hooks/`: data hooks (`useDeepWorkChat.ts`, `useFleetRuns.ts`, `useInbox.ts`, `usePermission.ts`, `useWorkflow*.ts`, `useConversationUnion.ts`)
- `frontend/e2e/`: Playwright recovery specs (CI `chat-browser` job)

### Docs, deploy, CI
- `docs/plans/` (never delete):
  - engine: `2026-10-14-001-...-durable-engine-plan.md`, `2026-10-14-003-...-engine-python-interfaces-plan.md`; backend hardening: `2026-10-14-002-...`
  - chat on engine: `2026-10-15-001-...`; Deep Work on engine: `2026-10-15-002-...`
  - Deep Work rollout: `2026-10-17-001-...`; approvals and policy: `2026-10-17-002-...`; fleet and workflows on engine (U-numbers, U29 flip): `2026-10-17-003-...`
- `DEPLOYMENT.md` (Local development at ~line 397), `QUICKSTART.md`, `TESTING.md`, `engine/README.md`
- `docker-compose.yml` (dev), `docker-compose.prod.yml`, `k8s/` (kustomize, `migrate-job.yaml`), `nginx/`, `terraform/`, `postgres/`
- `.github/workflows/`:
  - `engine.yml`: typecheck, lint, vitest unit, scenario
  - `backend.yml`: `unit` (ruff + pytest not postgres), `postgres` (pytest -m postgres)
  - `frontend.yml`: tsc + jest
  - `e2e.yml`: `engine-smoke` (e2e recovery tests + `tests/contracts`), `chat-browser` (Playwright)
  - `gitleaks.yml`: secret scan

### Common tasks: where to start
| Task | Start at |
|---|---|
| Add an engine tool | `engine/src/tools/registry.ts`; gateway call `engine/src/tools/gateway.ts`; backend side `backend/app/internal_api/tool_specs.py` + `tools.py`, impl in `backend/app/skills/<name>/`; `engine/test/support/internal-native-tools.json` |
| Add a Deep Work tool | `backend/app/services/deep_work/engine_tools.py` (+ `engine_sandbox.py` if sandboxed); view in `engine_view.py`; UI in `frontend/components/deep-work/` |
| Add a workflow node type | `backend/app/internal_api/node_specs.py`, `workflow_nodes.py`; `services/workflow_compiler.py`; schema in `engine/contracts/workflow-nodes/`; engine `engine/src/workflow/node-task.ts`; UI `frontend/components/workflow/nodes/` + `NodePalette.tsx`; legacy `backend/app/node_executors/` |
| Change model selection or policy | `backend/app/internal_api/models.py` (`/internal/models/policy`); engine side `engine/src/worker/backend-policy.ts`, `providers.ts`; `backend/app/services/llm_gateway.py`, `api/v1/routing_policies.py` |
| Change agent loop, context or compaction | `engine/src/agent/generation.ts`, `context-builder.ts`, `compaction.ts` |
| Add an approval or policy rule | `backend/app/services/tool_policy.py`, `approval_service.py`, `internal_api/authorize.py`; engine `engine/src/agent/approvals.ts`; plan 002 |
| Add an API route with RBAC | new/edit `backend/app/api/v1/<domain>.py`; guard with `require_permission` (`backend/app/auth/permissions.py`, add to `ROLE_PERMISSIONS`); register in `backend/app/main.py`; frontend client in `frontend/lib/api/` |
| Add a migration | backend: next number in `backend/migrations/versions/` (after 121) + model in `backend/app/models/`; add RLS policy and a test in `backend/tests/postgres/`; engine: new SQL in `engine/migrations/` |
| Add a setting or feature flag | `backend/app/config/__init__.py`; `services/feature_flags.py`; frontend `lib/hooks/useFeature.ts` |
| Follow a run end to end | UI (`frontend/components/chat/EngineChat.tsx`, `lib/engine/client.ts`) -> `backend/app/api/v1/engine_conversations.py` -> `services/engine_client.py` (`start_run`) -> engine `server/routes.ts` -> `agent/generation.ts` -> `tools/gateway.ts` -> `/internal/tools/*` and `/internal/models/policy` -> `worker/run-notifier.ts` -> `internal_api/runs.py` (`run_finished`) -> `services/run_handlers.py`; `run_reconciler.py` repairs lost callbacks; live updates `engine/src/watch/` -> `frontend/lib/engine/watcher.ts` |
| Debug a stuck or lost run | `backend/app/services/run_reconciler.py`, `run_handlers.py`, `run_status.py`; `backend/tests/e2e/` recovery tests |

## Verify

- **Engine:** `cd engine && npm run lint && npm run typecheck && npx vitest run`
- **Backend:** `cd backend && uv run ruff check app tests && uv run pytest -q -p no:cacheprovider`
- **Frontend:** `cd frontend && npx tsc --noEmit && npx jest --ci --watchAll=false`
- **E2E:** `cd backend && ONIVA_E2E=1 uv run pytest -q -p no:cacheprovider -m e2e tests/e2e`

## Gotchas learned the hard way

- **Row-level security is on and the tenant context is transaction-local.** The app connects as `oniva_app` (NOBYPASSRLS). A `commit()` followed by a `refresh()` or another query runs without tenant context unless it is re-applied. Tests that connect as a superuser or owner do not see this class of bug, so RLS regressions must be tested under a non-bypass role.
- **Engine feature flags are opt-out per tenant**, but global `ENABLE_ENGINE_*` settings act as kill switches. Fleet and workflows still default off until the U29 flip. Until then the legacy workflow and Fleet code is the live path, so do not delete it (U30–U32 come after the flip and its soak gates).
- **Idempotency keys are deterministic.** A retry under the same key replays the stored outcome, including a failure. Retrying a failed intake or plan must open a new session or key.
- **Deep Work runs settle on read and through the run reconciler** (`deep_work` kind). A run's background job must reach a terminal state together with the run.
- **Local dev stack:** see `DEPLOYMENT.md` ("Local development"). The engine worker needs `ENGINE_WORKFLOW_BACKEND_URL` or `ENGINE_GATEWAY_URL`.
