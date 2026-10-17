---
title: Finish and Roll Out Engine Deep Work - Plan
type: feat
date: 2026-10-17
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
execution: code
origin: reports/Oniva engine roadmap.md
---

# Finish and Roll Out Engine Deep Work - Plan

## Goal Capsule

- **Objective:** make engine chat and engine Deep Work the default product path. Close the UX gaps found in the 2026-10-16 live run, harden the OpenRouter path (request timeouts, durable usage), switch tenants to opt-out, delete the legacy v2 Deep Work executor, and document local dev.
- **Authority, highest first:**
  1. This plan.
  2. `engine/README.md` (runtime, view and gateway contract) and `engine/test/support/fake-gateway.ts` (normative `[clause:]` tags).
  3. `docs/plans/2026-10-15-002-feat-deep-work-on-engine-plan.md` (Deep Work on the engine).
  4. `reports/Oniva engine roadmap.md` (Phase 6).
- **Stop conditions:** stop and surface instead of guessing if:
  - deleting legacy v2 would remove a route the frontend still calls with the flag on;
  - an engine storage migration is needed for the usage outbox and conflicts with the lease or commit model.
- **Execution profile:**
  - One unit per agent, in its own git worktree off `main`.
  - Units with disjoint files may run in parallel.
  - The coordinator verifies, merges and commits.
  - `engine/` is clean-room: no third-party pi or earendil code or names.

---

## Product Contract

### Summary

The engine paths work end to end, but the live test left rough edges and the rollout is still opt-in. This plan:

- fixes the UX gaps;
- makes model calls and cost accounting robust;
- flips the default to the engine;
- removes the legacy v2 Deep Work executor.

### Problem Frame

The 2026-10-16 local run with `deepseek/deepseek-v4.1-flash` via OpenRouter found these gaps:

- `/chat` blocks first-time users with "Create a project first".
- Project pages show "No chats yet" for engine conversations.
- The Deep Work card looks idle for about 40 s while the orchestrator writes worker prompts.
- The chat greeting claims web search with no search provider configured.
- A hung model connection has no timeout.
- Usage reports are dropped when the backend is down past the retry budget, so cost is undercounted.

Engine chat and Deep Work are still tenant opt-in. Legacy v2 Deep Work still exists as a second loop.

### Requirements

- **R1.** A user with engine chat on and no project can send a first message from `/chat`. A default project is picked or created, with no error.
- **R2.** A project page's recent chats include that project's engine conversations, merged with legacy sessions and sorted newest first.
- **R3.** The Deep Work card shows an explicit "planning workers" activity while the orchestrator's model turn is producing tool calls, not a blank wait.
- **R4.** The chat system prompt never invites the model to offer capabilities it lacks (web search, knowledge bases, connectors, code execution) in that conversation.
- **R5.** A fetch-based model call has a first-byte timeout and an idle (between-chunk) timeout. A timeout before the first delta is retried under the existing retry policy. A timeout mid-stream fails retryable.
- **R6.** A usage report not yet acknowledged by the backend is persisted by the engine and re-sent until acknowledged, across backend outages and worker restarts. The backend ledger stays idempotent per `(tenant, requestId)`.
- **R7.** When the global engine configuration is present, engine chat and engine Deep Work are on for every tenant unless the tenant opts out.
- **R8.** The legacy v2 Deep Work execution path is removed. Historical sessions stay readable read-only.
- **R9.** `DEPLOYMENT.md` documents the local stack: compose services, env essentials, launch order and tenant bootstrap.

### Acceptance Examples

- **AE1.** A fresh tenant user with no projects opens `/chat`, types "hello" and gets a reply. A project now exists, and the conversation appears in it.
- **AE2.** After one engine conversation in Project 1, the Project 1 page lists it under Recent Chats.
- **AE3.** During the orchestrator's delegation turn, the card shows "Planning workers…" before any worker row appears.
- **AE4.** With no search key, the first chat reply does not mention web search.
- **AE5.** The backend is stopped for 3 minutes during a run. After it restarts, the ledger holds every usage row from the outage, each once.
- **AE6.** A tenant with no feature settings gets engine chat when `ENABLE_ENGINE_API` and the engine config are set. A tenant with `features.ENABLE_ENGINE_CHAT=false` does not.

### Scope Boundaries

- **In scope:** units U1–U9.
- **Not in scope:**
  - Fleet and workflows on the engine, and LiteLLM removal (roadmap Phase 6 remainder, separate plan).
  - Approvals and policy (Phase 5, separate plan).

#### Deferred to Follow-Up Work

- Adding a Tavily or other search key per tenant through a settings UI. Code already supports keys; provisioning is ops.
- A live Deep Work run to a completed report on a paid model, which needs model credit. It is verified by the scripted e2e tests in this plan.

---

## Planning Contract

### Key Technical Decisions

Naming: **legacy v2** means the old `DeepWorkEngine` pipeline in `backend/app/services/deep_work/engine.py`. **The engine** always means `@oniva/engine`.

- **KTD1 (R1).** The `/chat` default project is the most recently updated project **the user owns**. Shared projects are never picked. If the user owns none, create a private project named "Personal" through the existing projects create API, then continue the send. If the user lacks create permission, show a clear message pointing them to pick a project. No new backend endpoint.
- **KTD2 (R2).** Merge engine conversations in `ProjectService.list_project_sessions` (`backend/app/services/project_service.py`), after its existing permission check. Filter on `project_id`, `tenant_id`, `user_id` and `archived_at IS NULL`, and add `source: "engine"|"legacy"` to each dict. The router stays thin.
- **KTD3 (R3).** Derive "planning workers" in the frontend adapter. The predicate must hold with only the fallback:
  - the root run is active;
  - `dw_start_round` for the current round succeeded;
  - no `research_worker` subagent has started for that round.
  
  A live tool-call block named `research_worker` in `live.message`, if the engine streams it at `block_start`, only makes the label more specific. U3 first checks this against the engine stream builder or a recorded fixture.
- **KTD4 (R4).** Add a capability block to the composed chat system prompt. It lists the tools actually granted in this conversation and states that unlisted capabilities are unavailable and must not be offered. The base agent prompt stays untouched.
- **KTD5 (R5).** Timeouts live in the shared fetch retry helper (`engine/src/models/retry.ts`) as per-attempt timers on the attempt's own AbortController.
  - The first-byte timer is cleared by the first non-keepalive SSE data event.
  - The idle timer resets on every data event.
  - A timeout before any block event is emitted is classified `network` and retried under the existing policy.
  - A timeout after a block event ends the attempt as `kind: network, retryable: true` with the partial kept. The helper does not retry it.
  - Defaults are 120 s first-byte (over 2x the ~40 s orchestrator delegation turn observed) and 120 s idle. Env: `ENGINE_MODEL_FIRST_BYTE_TIMEOUT_MS`, `ENGINE_MODEL_IDLE_TIMEOUT_MS`.
- **KTD6 (R6).** The usage outbox is a worker-level interface on `StorageProvider` with `enqueue`, `claimPending(claimTimeoutMs)` and `ack`, backed by `engine/migrations/002_usage_outbox.sql`.
  - Rows are keyed by `requestId` and carry `claimed_at`.
  - Writes are unfenced single-row operations outside the session commit line, so they don't interact with the lease.
  - The reporter enqueues before the first attempt (claimed) and acks on 2xx.
  - The flusher claims only rows unclaimed or past the claim timeout, oldest first, on worker start and every 30 s.
  - `UsageReporter` receives the outbox from `worker.ts`.
  - Before relying on re-sends, verify that the backend ledger dedupes `(tenant, requestId)` and returns `recorded: false`. `test_usage_repeat_false` covers this today.
- **KTD7 (R7).** Rollout is staged.
  - `_engine_tenant_flag` treats an absent tenant setting as on and an explicit `false` as off.
  - `get_tenant_features` and the frontend feature map report the same resolved value.
  - Global ceilings (`ENABLE_ENGINE_API`, `ENABLE_ENGINE_CHAT`, `ENABLE_ENGINE_DEEP_WORK`, `ENGINE_URL`, `ENGINE_SERVICE_TOKEN`) stay the kill switch.
  - Spend is bounded by the existing per-tenant `QuotaService` LLM token check, which runs in the model policy for every engine request. U7 verifies it applies to Deep Work worker requests and documents how to set a tenant quota.
  - U7 ships after U5 and U6, so usage is never undercounted while spend rises.
- **KTD8 (R8).** Legacy v2 removal keeps the URLs the engine path serves: `POST /deep-work/engine/clarify`, `POST /deep-work/engine/plan`, `POST /deep-work/engine` and `POST /deep-work/engine/{id}/cancel`.
  - **Strip from those handlers:** the legacy fall-through body (`DeepWorkEngine`, `NullSink`, `SSEQueueSink`, `_engine_tasks`, `_sse_queues`). It is replaced by a 404 "Deep Work unavailable" when engine Deep Work is off for the tenant.
  - **Delete:** `GET /deep-work/engine/{session_id}/stream`, the frontend v2 stream consumer (`getEngineStreamUrl` and its SSE handling), and `backend/app/services/deep_work/engine.py` and `worker.py`.
  - **`event_sink.py`:** delete only after `sandbox_tools.py` drops its `EventSink` dependency, which the engine path imports through `engine_sandbox.py`. Its tests and `tests/contracts/streams.py` references go with it.
  - **Keep:**
    - `startup_recovery.fail_stale_deep_work_jobs`, because engine jobs share `job_type='deep_work'`. Its existing engine exclusion keeps working, and leftover v2 jobs are failed with a readable message.
    - The historical read branches of `list_deep_work_sessions` and `delete_deep_work_session`.
  - **Not a v1 cleanup:** v1 single-agent execution was already deleted in DW U12, so after this the engine is the only executor for new runs.

### Assumptions

- With engine Deep Work the only executor, opt-out tenants lose Deep Work rather than falling back to the legacy path.
- The default project name "Personal" (private, owned by the user) is acceptable product copy.
- `features.*` stays the tenant settings location.
- **Release gating:** U8 (legacy v2 removal) merges in its own PR after U1–U7. It is deployed only after a soak window in which:
  - engine Deep Work has completed a live run to a final report;
  - no legacy v2 job is running or pending.
  
  These are ops gates recorded in `DEPLOYMENT.md`, not code.

---

## Implementation Units

### U1. `/chat` default project

**Goal:** a first message on `/chat` never fails for lack of a project.
**Requirements:** R1, AE1.
**Dependencies:** none.

**Files:**
- `frontend/components/chat/EngineChat.tsx`
- `frontend/lib/stores/projects.ts`
- `frontend/components/chat/__tests__/EngineChatDefaultProject.test.tsx` (new)

**Approach:** replace the "Create a project first" branch:
- resolve the most recently updated project the user owns from the store, loading the list if needed;
- if none exists, create a private project named "Personal";
- then call the existing `startConversation` flow with that project id, and navigate to the project chat URL as today.

The Deep Work intake path uses the same resolution.

**Test scenarios:**
- No projects: send creates "Personal", then starts the conversation, and no error is shown.
- Two projects: send uses the most recently updated one.
- Project creation fails, or the user lacks create permission: an inline message asks the user to pick or create a project, the draft text is kept, and no conversation is created.
- Shared projects the user doesn't own are never auto-picked.
- The Deep Work toggle with no project resolves a project before calling clarify.

**Verification:** frontend tsc and jest are green.

### U2. Project page lists engine conversations

**Goal:** Recent Chats shows engine conversations.
**Requirements:** R2, AE2.
**Dependencies:** none.

**Files:**
- `backend/app/services/project_service.py` (`list_project_sessions`)
- `backend/tests/test_project_sessions_engine.py` (new)
- `frontend/components/chat/ProjectView.tsx`, if a link target needs the engine URL shape

**Approach:**
- After the existing permission check, add `EngineConversation` rows for the project, filtered by tenant and user (KTD2), to the dict list.
- Exclude archived conversations, including the Deep Work internal conversations stored archived.
- Sort by `updated_at` descending and add `source`.
- Engine items link to `/chat/projects/{id}?c={conversation_id}`.

**Patterns to follow:** the engine-row query in `backend/app/api/v1/chat_sessions.py` (adapted: dict-shaped and project-scoped).

**Test scenarios:**
- A project with one legacy and one engine conversation returns both, newest first.
- Archived engine conversations are excluded.
- Another tenant's or user's conversations are excluded.
- A viewer without project access still gets the existing permission error.
- A project with none returns an empty list.

**Verification:** backend pytest is green, and the page shows the conversation.

### U3. "Planning workers" activity on the Deep Work card

**Goal:** no silent gap during the delegation turn.
**Requirements:** R3, AE3.
**Dependencies:** none.

**Files:**
- `frontend/lib/engine/deepWork.ts`
- `frontend/components/deep-work/DeepWorkEngineCard.tsx`
- `frontend/lib/engine/__tests__/deepWork.test.ts`
- `frontend/components/deep-work/__tests__/DeepWorkEngineCard.test.tsx`

**Approach:**
- First, confirm from the engine stream builder or a recorded golden view whether `live.message` holds a named tool-call block at `block_start`. Build the test fixture from a real captured view, not a hand-made one.
- Add phase value `planning_workers` to the adapter, derived as KTD3 describes (the fallback predicate must work alone).
- The card renders "Planning workers…" with the existing phase line and a subtle progress indicator.
- Keep reference stability: an unchanged view returns the same state object.

**Test scenarios:**
- A view after `dw_start_round` succeeds with the orchestrator turn live and no subagents gives phase `planning_workers`.
- Once the first subagent appears, the phase leaves `planning_workers`.
- A finished run gives no phase.
- The card shows the "Planning workers…" text in that phase.

**Verification:** frontend jest is green, including the golden fixture tests.

### U4. Capability-honest chat prompt

**Goal:** the model only offers what it has.
**Requirements:** R4, AE4.
**Dependencies:** none.

**Files:**
- `backend/app/services/engine_chat_agent.py`
- `backend/tests/internal/test_engine_chat_agent.py` (extend or create)

**Approach:** after composing tools:
- append a "Your capabilities in this conversation" block naming each granted tool family (web search, knowledge bases, connectors, files, code execution);
- add one sentence that anything not listed is unavailable and must not be offered or promised.

**Test scenarios:**
- No web search: the prompt has no web-search instruction, the capability block omits web search, and the "must not offer" sentence is present.
- Web search on: web search is listed and the web instruction is present.
- Knowledge bases selected: knowledge search is listed.
- The prompt never contains a key or secret.

**Verification:** backend pytest is green.

### U5. Model request timeouts

**Goal:** a hung connection can't stall a run.
**Requirements:** R5.
**Dependencies:** none.

**Files:**
- `engine/src/models/retry.ts`
- `engine/src/models/openai-compatible.ts`
- `engine/src/worker/providers.ts`
- `engine/src/worker/config.ts`
- `engine/scripts/worker.ts`
- `engine/README.md`
- `engine/test/models/openai-compatible.retry.test.ts`
- `engine/test/worker/config.test.ts`

**Approach:**
Follow KTD5:
- a first-byte timer cleared by the first non-keepalive SSE data event;
- an idle timer reset per data event;
- each timer aborts only that attempt's controller;
- before any block event, the timeout is retried as `network`; after a block event, the attempt ends `kind: network, retryable: true` with the partial kept and no retry.

The run's AbortSignal still wins.

**Test scenarios:**
- No headers within the timeout, then success on retry: one clean answer.
- A stall after the first delta beyond the idle timeout: a retryable error, the partial kept, no retry.
- Chunks arriving just under the idle timeout keep the stream alive.
- SSE keepalive or comment lines do not clear the first-byte timer.
- A run abort during the wait gives `aborted`, not a timeout.
- Config parses the env and rejects non-positive values.

**Verification:** engine lint, typecheck and vitest are green.

### U6. Durable usage outbox

**Goal:** cost is never undercounted.
**Requirements:** R6, AE5.
**Dependencies:** U5. U5 owns `engine/src/worker/config.ts`, `scripts/worker.ts` and the README env table first; U6 builds on them.

**Files:**
- `engine/src/worker/backend-policy.ts`
- `engine/migrations/002_usage_outbox.sql` (new)
- `engine/src/storage/types.ts`, the Postgres storage implementation, the memory storage, and `engine/src/storage/conformance.ts` (outbox cases)
- `engine/src/worker/worker.ts`
- `engine/README.md`
- `engine/test/worker/backend-policy.test.ts`
- `engine/test/support/fake-gateway.ts` (clause for redelivery)
- `backend/tests/internal/test_contract_conformance.py` (matching clause test)

**Approach:**
- Implement KTD6: enqueue (claimed) before the first attempt, and ack on a 2xx.
- The flusher claims only unclaimed or timed-out rows, oldest first, on worker start and every 30 s, using the existing retry policy.
- First, confirm the backend ledger dedupe (`(tenant, requestId)` returns `recorded: false`).
- A row past a long max age (7 days) is kept, not dropped, and a `usage_stale` event is logged.
- The README "usage gap" note is replaced with the outbox behaviour.

**Test scenarios:**
- Backend down for the whole retry budget: the row stays pending. When the backend comes back, the flusher delivers it exactly once, and the fake gateway counts it once.
- A worker restart with pending rows delivers them after start.
- A 2xx deletes the row.
- A row whose first attempt is in flight (claimed, inside the claim timeout) is not taken by the flusher.
- A re-send of an already-recorded report gets backend `recorded: false`, and the row is deleted.
- Memory and Postgres storage both pass the outbox conformance cases.

**Verification:** engine and backend suites are green.

### U7. Engine paths on by default (tenant opt-out)

**Goal:** the engine is the default.
**Requirements:** R7, AE6.
**Dependencies:** U5, U6.

**Files:**
- `backend/app/services/feature_flags.py`, including `get_tenant_features`, so the frontend feature map agrees
- `backend/tests/test_engine_deep_work_flag.py`
- every test asserting opt-in semantics: grep `tests/` for `engine_chat_enabled`, `engine_deep_work_enabled` and `ENABLE_ENGINE_CHAT`, then list them in the unit's report
- `DEPLOYMENT.md`

**Approach:** KTD7.
- Verify that the `QuotaService` LLM token check runs for Deep Work worker policy requests, and add a test if it is missing.
- Update the docstrings and the DEPLOYMENT feature-flag table: global flags are ceilings and the kill switch, the tenant setting is an opt-out, and a section explains how to set a tenant token quota.

**Test scenarios:**
- No tenant setting with the globals on: enabled.
- Explicit tenant `false`: disabled.
- Any global off: disabled whatever the tenant says.
- Engine URL or token missing: disabled.
- `get_tenant_features` reports the same resolved value for a tenant with no setting.
- A tenant over its token quota is denied `quota_exceeded` on a Deep Work worker policy request.

**Verification:** backend pytest is green.

### U8. Delete legacy v2 Deep Work

**Goal:** one Deep Work executor.
**Requirements:** R8.
**Dependencies:** U7. Ships as its own PR after U1–U7; the deploy is gated per Assumptions.

**Files:**
- `backend/app/api/v1/deep_work.py`
- `backend/app/services/deep_work/engine.py`, `worker.py` (delete)
- `backend/app/services/deep_work/sandbox_tools.py` (drop the `EventSink` dependency), then `event_sink.py` (delete), `backend/tests/test_event_sink.py`, and the references in `backend/tests/contracts/streams.py`
- `backend/tests/e2e/backend_app.py`, if it imports legacy modules
- frontend v2 code: `frontend/lib/stores/deep-work.ts`, `frontend/lib/api/deep-work.ts`, and v2 hydration and stream code in `frontend/hooks/useDeepWorkChat.ts`
- tests that reference them

**Approach:**
- Follow KTD8 exactly: keep the four engine-served URLs, strip only their legacy fall-through bodies, and delete the stream route.
- Before deleting, grep every caller.
- Keep `startup_recovery.fail_stale_deep_work_jobs` with its engine exclusion.
- Historical v1/v2 sessions stay readable through status, full-state, workspace and sessions.
- The frontend hides the Deep Work toggle when engine Deep Work is off.

**Test scenarios:**
- `openapi.json` has no `GET /deep-work/engine/{session_id}/stream` route, and the four engine-served URLs still exist.
- The engine sandbox tools still import and work without `event_sink`.
- No import of the deleted modules remains.
- A historical v2 session opens read-only with its files.
- Tenant opt-out gets 404 on clarify and no toggle in the UI.
- The engine path flow tests still pass.

**Verification:** backend and frontend suites are green, plus e2e and Playwright.

### U9. Local dev documentation

**Goal:** someone can run the stack locally in one pass.
**Requirements:** R9.
**Dependencies:** U7 (shares `DEPLOYMENT.md`).

**Files:** `DEPLOYMENT.md` (new "Local development" section).

**Approach:** document:
- compose `postgres` and `redis` only, with a root `.env` (POSTGRES/APP/SYSTEM/REDIS passwords) and placeholder values for compose interpolation;
- `backend/.env` essentials: comma-separated `CORS_ORIGINS`, the three database URLs, engine tokens, `ENGINE_URL`;
- migrations run as the owner;
- the engine worker env and `oniva_engine` database;
- frontend `NEXT_PUBLIC_API_URL`;
- tenant bootstrap: a tenant with features, an owner user and a provider key entered through the UI.

No secrets in the doc.

**Test expectation:** none, documentation only.

**Verification:** a reviewer can follow it start to finish.

---

## Verification Contract

| Area | Gate |
|---|---|
| engine | `npm run lint`, `npm run typecheck`, `npx vitest run` green |
| backend | `uv run ruff check app tests`, `uv run pytest -q -p no:cacheprovider` green |
| frontend | `npx tsc --noEmit`, `npx jest --ci --watchAll=false` green |
| e2e | backend `ONIVA_E2E=1 pytest -m e2e tests/e2e`; Playwright `chat-engine-recovery`, `deep-work-engine-recovery` |
| hygiene | clean-room grep on `engine/` empty; gitleaks clean; no `.env` committed |

## Definition of Done

- U1–U9 are merged with their test scenarios passing. U8 is in its own PR.
- The first week after U7 deploys, a usage-ledger check confirms per-tenant engine spend sits within quotas.
- All Verification Contract gates are green in CI.
- AE1–AE6 hold.
- Legacy v2 Deep Work code is gone, and historical sessions are readable.
- `DEPLOYMENT.md` has the local-dev section.
