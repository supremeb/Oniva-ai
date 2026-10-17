---
title: Approvals and Policy, End to End - Plan
type: feat
date: 2026-10-17
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
execution: code
origin: reports/Oniva engine roadmap.md
---

# Approvals and Policy, End to End - Plan

## Goal Capsule

- **Objective:** make "a human decides, and only an authorised human decides" true for every risky thing an Oniva agent does. One approval primitive, decided by the Python control plane under RBAC. It waits for days, survives restarts, and is recorded in a tamper-evident audit log. A global "Needs attention" inbox merges everything waiting on a person. A Rule-of-Two policy stops a run that has read untrusted content from taking outside-world actions unattended. The separate review-ticket path is retired, and approved `execute_python` calls actually run.
- **Authority, highest first:**
  1. This plan.
  2. `engine/README.md` (approvals, signals, `HostPolicy.authorizeTool`, tool gateway contract) and `engine/test/support/fake-gateway.ts` (normative `[clause:]` tags).
  3. `docs/plans/2026-10-15-001-feat-chat-on-engine-plan.md` and `docs/plans/2026-10-15-002-feat-deep-work-on-engine-plan.md`.
  4. `reports/Oniva engine roadmap.md` (Phase 5, Decisions, Risks).
- **Stop conditions:** stop and surface instead of guessing if:
  - the `compliance_audit_logs` immutability trigger (U1) would block a path that legitimately deletes rows (tenant deletion cascade, retention purge) and the GUC escape hatch in KTD9 is not enough;
  - U6 would remove a route that `frontend/lib/api/compliance.ts` still calls with `ENABLE_COMPLIANCE` on and U9 has not yet replaced it;
  - a `/internal/tools/authorize` round trip on every non-read tool call measurably slows a scripted engine run (more than about 50 ms p95 in the e2e harness), which would call for caching rather than skipping the check.
- **Execution profile:**
  - One unit per agent, in its own git worktree off `main`.
  - Units with disjoint files may run in parallel (see "Dependency order and file overlaps").
  - The coordinator verifies, merges and commits, and renumbers Alembic `down_revision` when two migration units land out of order.
  - `engine/` is clean-room: no third-party pi or earendil code or names.

---

## Product Contract

### Summary

Today there are four separate "someone must approve this" mechanisms and none of them is complete:

| Mechanism | Where | What is wrong |
|---|---|---|
| Engine approval (`agent.approvals`) | `engine/src/agent/approvals.ts`, `tool-task.ts` | Durable and correct, but the verdict comes from the worker: `workerHostPolicy.authorizeTool` in `engine/src/worker/worker.ts` approves anything the manifest flags. The decider check is only "owner of the conversation or tenant admin" (`_binding` in `backend/app/api/v1/engine_conversations.py`), so the person who asked can approve their own `execute_python`. Expiry is a flat 24 h (`ENGINE_APPROVAL_TTL_MS`). |
| Review tickets | `backend/app/services/review_queue.py`, `/api/v1/audit/reviews*`, `human_review_tickets` | `execute_python` creates a ticket and returns `pending_review`. Nothing ever polls it or runs the code. `submit_decision` has a `TODO` for the workflow transition. |
| Compliance review queue | `backend/app/api/v1/compliance.py` (`/review-queue`, `ComplianceExecution`) | A real, separate queue with its own UI. Not visible anywhere else. |
| Fleet interventions | `backend/app/services/intervention.py` (Redis list), `backend/app/api/v1/fleet.py` | A message channel, not a queue of things needing a person. Failed runs are only visible on the Fleet page. |

The audit log claims a hash chain and RSA signatures (`backend/app/models/compliance_audit_log.py`, `backend/app/services/audit_logger.py`) but `get_audit_logger(db)` builds a new logger on every call with a db, so `_last_entry_hash` resets to `None` and the chain never links. Rows can be updated or deleted by the app role, and the signing key is a PEM file under `SESSION_BASE_PATH/.audit_keys`.

### Problem Frame

Phase 5 of the roadmap says an approval must wait for days, survive restarts, and be decided by an authorised user, and an unauthorised user must not be able to decide it. It also asks for a Rule-of-Two policy and a tamper-evident log. The pieces exist but are unconnected, so an approval today is both too easy to give (the requester can give it) and impossible to find (no inbox), and code execution approvals lead nowhere.

### Requirements

- **R1.** Every tool call that needs a human goes through one primitive: the engine parks the tool task on `approval`, and the Python control plane decides who may decide. The engine worker holds no policy of its own for non-read tools.
- **R2.** Deciding an approval requires the `approvals:decide` capability for the tool's required approver role. A user outside the tenant, a viewer, or a member without the required role is refused with 403 and nothing changes in the engine. A tenant setting can additionally forbid self-approval.
- **R3.** An approval waits as long as its policy TTL (default 7 days, tenant-configurable, `0` for none). It survives engine worker restarts and backend restarts, and it can be decided after either.
- **R4.** The review-ticket path is retired: no new `human_review_tickets` rows, `/audit/reviews*` routes and `ReviewQueueService` are removed, open legacy tickets are carried into the new ledger, and the UI stops calling the old routes.
- **R5.** An approved `execute_python` call runs in the sandbox and returns its output to the model. An unapproved call never reaches the sandbox, enforced in Python and not only in the engine.
- **R6.** A global "Needs attention" inbox (backend endpoint plus frontend page and nav badge) merges: tool approvals, output-review items, compliance review-queue items, Fleet failures needing an operator, Deep Work runs waiting on the user, and chat questions.
- **R7.** Rule of Two: once a run has taken in untrusted input (web pages, connector data, knowledge and uploaded-file content), any side-effecting tool that acts outside the workspace needs approval. In a conversation where no human can answer (a Deep Work worker), it is blocked instead of left waiting forever. Taint is tracked in the engine and the decision is made in Python.
- **R8.** The audit log is tamper-evident: a per-tenant chain head persisted in the database and rebuilt from committed entries, signatures from a KMS-style abstraction with a local dev implementation, and a database trigger that makes `compliance_audit_logs` append-only. Approval requests, decisions and policy blocks are written to it.
- **R9.** The chat approval card and the question card work in production chat (`EngineChat`), show why the approval is needed, and show a clear "you cannot decide this" state for refused users.
- **R10.** A model can ask a human a question (`ask_user`) in engine chat. The question waits durably like an approval and is answered from the chat card or the inbox.

### Acceptance Examples

- **AE1 (the exit criterion).** A member asks the chat agent to run Python. The call parks on approval. The engine worker is killed and restarted, and the backend is restarted too. The approval is still pending and still in the inbox. A tenant admin approves it, the code runs once, and the model receives the output.
- **AE2.** In AE1, before the admin acts, a viewer in the same tenant, a member of another project, and a user of another tenant each try to decide it. The first two get 403 and the third 404. The engine item stays `pending` and the audit log gains no decision entry.
- **AE3.** The agent reads a web page (`browse_url`) and then calls `linear_create_issue`. The call needs approval and the card says the run read untrusted content. A Deep Work worker in the same situation has the write blocked with a readable reason, and the run does not hang.
- **AE4.** A call that needs approval and is not decided within the TTL ends with `approval_expired`, the inbox item disappears, and the ledger row is `expired`.
- **AE5.** After a tenant's audit entries are altered directly in the database (a field edited, a row deleted, or a row inserted mid-chain), `GET /api/v1/audit/chain/verify` names the first broken sequence number. An `UPDATE` or `DELETE` on `compliance_audit_logs` by the app role fails at the database.
- **AE6.** Two approvals are requested at the same moment for one tenant. Both entries land with consecutive sequence numbers and the chain verifies. After a backend restart, the next entry links to the last committed one.
- **AE7.** An open legacy review ticket from before this change appears in the inbox as a review item and can be decided there. `POST /api/v1/audit/reviews` no longer exists.
- **AE8.** The agent calls `ask_user`. A question card appears in chat and in the inbox. After a worker restart the question is still open, and answering it resumes the run with the answer.

### Scope Boundaries

- **In scope:** units U1-U13.
- **Not in scope:**
  - Moving Fleet background agents and workflows onto engine tasks (roadmap Phase 6). The inbox reads Fleet failures from `background_jobs` as they are today. Workflow `human_in_loop` nodes (`backend/app/node_executors/human_in_loop_executor.py`, a stub that auto-approves) are not touched.
  - A resume hook for output-review items. They are recorded and decided, but resuming the paused workflow stays the existing `TODO`, finished with workflows in Phase 6.
  - Real cloud KMS implementations. The abstraction, the local dev implementation and a factory that fails closed in production are in scope.
  - Notifications (email, Slack) for new inbox items.

#### Deferred to Follow-Up Work

- Per-tenant editable policy rules in a settings UI. This plan reads `tenants.settings["approvals"]`; the UI is later.
- Clearing taint mid-conversation (for example after a human reviews the content).
- An AWS KMS implementation of `KeyManagementService`.

---

## Planning Contract

### Key Technical Decisions

Naming: **the ledger** is the new Python table `tool_approvals` (U2) that indexes every approval-like item. **The engine item** is the entry in the conversation's `agent.approvals` document (`engine/src/agent/approvals.ts`), which stays the source of truth for whether a tool call runs.

- **KTD1 (R1, R2). Python decides policy and who may decide; the engine decides whether the call runs.**
  - Policy: the worker's `authorizeTool` (`engine/src/worker/worker.ts`, `workerHostPolicy`) calls a new `POST /internal/tools/authorize` on the backend (U3, U5). The backend answers `allow`, `block` or `requireApproval`, and a `requireApproval` answer also records a ledger row.
  - Deciding: `POST /api/v1/engine/conversations/{id}/approvals/{toolTaskId}/decision` keeps its URL and body, so the shipped `frontend/lib/engine/client.ts` `decide()` keeps working. Its authorisation moves from `_binding` to `ApprovalService.decide` (U2), which checks RBAC, then calls `EngineClient.decide` (`backend/app/services/engine_client.py`), then records the outcome.
  - The engine's decision route stays service-token only and records the principal it is given. No browser can reach it.
- **KTD2 (R1). The authorize contract** (camelCase JSON, `Authorization: Bearer <ENGINE_GATEWAY_TOKEN>`, same auth as `/internal/models/*` in `backend/app/internal_api/auth.py`). Frozen here so U3 and U5 can be built in parallel against `engine/test/support/fake-gateway.ts` and `backend/tests/internal/test_contract_conformance.py`.

  Request body:

  ```
  { requestId: "<runId>:<toolCallId>",
    tenantId, projectId,
    conversationId,            // the top-level (root) conversation: the only one the backend knows
    engineConversationId,      // the conversation that made the call (may be a child)
    agentRole?,                // present for subagent/worker conversations
    runId, toolCallId, toolTaskId, toolName,
    arguments,                 // final arguments after hooks, already run through engine/src/worker/redact.ts
    sideEffect, replay,        // from the manifest
    taint: { tainted: boolean, sources: [{ kind, name, ref, at }] } }
  ```

  Responses (HTTP 200 in every decided case; 4xx/5xx are transient or fatal as for model policy):
  - `{ decision: "allow" }`
  - `{ decision: "block", code, reason }`
  - `{ decision: "requireApproval", reason, approvalId, approvalTtlMs: number | null, requiredRole: "member" | "admin" }`

  A repeated `requestId` returns the same answer and never creates a second ledger row. The engine converts `approvalTtlMs` to `expiresAt` with its own injected clock. The engine retries transient failures with the same capped backoff as model policy (`engine/src/worker/backend-policy.ts`), then **blocks** the call with `policy_unavailable`. It never falls back to a local allow.
- **KTD3 (R3). Durability is the engine's, indexing is the ledger's.** The wait is the existing `waiting({ signal: "approval", until })` in `tool-task.ts`; nothing about it changes. The ledger is written at authorize time (before the engine commits the item) and keyed `(tenant_id, engine_conversation_id, tool_task_id)`. Because the engine now sends `toolTaskId` in the authorize request, the decision route can find the ledger row from the URL without asking the engine. The engine's `expiresAt` stays authoritative: the ledger stores `expires_at` for display and inbox filtering, and a lazy sweep (U7) marks overdue rows `expired`.
- **KTD4 (R2). Who may decide.** Policy lives in `backend/app/services/approval_service.py` (U2) and uses the existing RBAC in `backend/app/auth/permissions.py`:
  - New permissions: `approvals:decide` for `admin` (owner has `*`), and `approvals:decide_own` for `member`. `viewer` has neither.
  - A row carries `approver_role`, set from the tool spec: `admin` for `execute_python` and connector writes, `member` otherwise.
  - A user may decide a row when: they are in the row's tenant (else 404); they hold `approvals:decide`; or hold `approvals:decide_own` and are the requester and the row's `approver_role` is `member`.
  - The tenant setting `approvals.require_separate_approver` (default `false`) additionally forbids `decided_by == requested_by`.
  - A failed check is 403 `not_authorized` (or `self_approval_forbidden`) and writes nothing.
- **KTD5 (R2, R3). Decide protocol.** A decision is two systems (ledger and engine), so it is made retry-safe:
  1. In one committed transaction, move the row `pending -> deciding` and set `decided_by`, `decision`, `note`. A second concurrent decider fails the guarded `UPDATE ... WHERE status='pending'` and gets 409.
  2. Call `EngineClient.decide`.
  3. On success, set `decided` and append the audit entry. On `approval_rejected` from the engine (`already_decided`, `expired`, `not_waiting`), set the row to match (`expired` or `cancelled`) and return 409 or 410.
  4. A retry by the same user finds `deciding`, repeats step 2 (the engine answers `already_decided` if it had accepted) and finalises. A row stuck in `deciding` is shown pending to nobody else and is finalised by the same retry or by the lazy sweep after 60 s.
- **KTD6 (R7). Rule of Two.** The engine tracks taint, Python decides.
  - **Engine (U4).** A conversation-scoped document `agent.taint` is kept on the ROOT conversation id, so a child worker's reads taint the run that owns it. Shape: `{ tainted: boolean, total: number, sources: [{ kind: "tool_result" | "input", name, ref, at }] }`, with `sources` capped at 20 entries.
  - It is set in the same commit that stores the result of a tool whose manifest says `untrustedOutput: true`, and at submission when the caller marks the input `untrusted`. Setting it in the same commit as the result means a crash can't leave an untrusted result in context without the flag.
  - It is sticky for the life of the root conversation (see Risks).
  - **Python (U3).** `ToolSpec` gains `untrusted_output: bool` and `scope: "workspace" | "external"`. The matrix, evaluated in this order:
    1. unknown or disabled tool: `block`;
    2. `requires_approval`: `requireApproval` (TTL from tenant policy);
    3. tainted, `side_effect == "write"` and `scope == "external"`: if the call comes from a conversation with an `agentRole` (a worker with no human) then `block` with code `untrusted_input_no_approver`, else `requireApproval` with reason "This run read untrusted content" and `rule_of_two` in the ledger;
    4. otherwise `allow`.
  - Classification of today's tools (all in `backend/app/internal_api/tool_specs.py`):
    - `untrusted_output=True`: `web_search`, `browse_url`, `extract_structured_data`, `browser_navigate`, `browser_screenshot`, `dw_browser_navigate`, `dw_browser_screenshot`, `search_knowledge`, `read_file`, `parse_document`, `parse_pdf`, `linear_search_issues`, `servicenow_search_incidents`, `invoke_connector_tool`, `inspect_artifact`.
    - `scope="workspace"`: `write_file`, `delete_file`, `update_todo`, `create_task`, `update_task`, `store_decision`, `browser_screenshot`, and the `dw_*` platform writes.
    - Everything else is `external`, which is also the default for an undeclared tool.
  - A test fails if a spec in the table lacks an explicit scope decision, the way `test_every_skill_has_a_tool_spec` does for specs today.
- **KTD7 (R4, R5). Ticket retirement and `execute_python`.**
  - The engine path already waits for approval before the gateway invokes the tool, so `execute_python` must not create a ticket. The skill runs the code in the sandbox through `SandboxExecutor` (`backend/app/services/sandbox_executor.py`) the way `backend/app/services/deep_work/sandbox_tools.py` does.
  - Defence in depth: `execute_tool` (`backend/app/internal_api/execution.py`) refuses any `requires_approval` tool with HTTP 403 `approval_required` unless the ledger holds a `decided/approve` row for `(tenant, runId, toolCallId)`. A bug in the engine's policy hook therefore still can't reach the sandbox.
  - Outside the gateway (legacy Python agent loops that call the skill directly) the skill refuses with a clear message and does not create a ticket.
  - `HumanReviewTicket` rows stay as read-only history. Open ones are copied into the ledger with `kind='review'` and `legacy_ticket_id` by the migration.
  - `output_gating.py` and `wide_research.py` write ledger rows instead of tickets.
- **KTD8 (R6). The inbox is a read model, not a new store.** `GET /api/v1/inbox` unions typed adapters, each returning `InboxItem { id, kind, title, summary, severity, createdAt, dueAt?, source: { type, id }, actions: [...] , href }` for the current user and tenant. It does not copy rows. Adapters: `approval` (ledger, `kind='tool_call'`), `review` (ledger `kind='review'`), `question` (ledger `kind='question'`), `compliance` (`ComplianceExecution` in `manual_review`), `fleet` (`BackgroundJob` failed in the last 72 h that the user owns), `deep_work` (`DeepWorkRun` with a non-null `awaiting_input`). Visibility is the same RBAC as deciding, so a member sees their own items and an admin sees the tenant's.
- **KTD9 (R8). Audit chain.**
  - **Head.** Table `audit_chain_heads(tenant_id pk, seq bigint, head_hash text, key_id text, updated_at)`. An append locks that row (`INSERT ... ON CONFLICT DO NOTHING`, then `SELECT ... FOR UPDATE`), takes `seq + 1` and `previous_hash = head_hash`, hashes and signs the entry, inserts it with `seq`, and updates the head, all in one transaction. A unique `(tenant_id, seq)` index on `compliance_audit_logs` makes a fork impossible even if the lock is bypassed.
  - **Rebuild.** The head row is a cache. `AuditChain.rebuild_head(tenant)` recomputes it from the last committed entry; `append` calls it when the head is missing or disagrees with the last committed entry, and logs `audit_head_repaired`.
  - **Entries from before the chain** (`seq IS NULL`) are reported as `legacy_unchained` by verification, not treated as breaks.
  - **Signing.** A `KeyManagementService` protocol (`sign`, `verify`, `public_key_pem`, `key_id`) in `backend/app/security/kms.py`. `LocalDevKMS` is the current RSA-PSS key-pair-on-disk behaviour, refused when `settings.ENVIRONMENT` is `production` or `prod` (the check at `backend/app/config/__init__.py:409`). A factory chooses by `AUDIT_KMS_BACKEND` and fails closed in production when none is configured. The entry stores `signing_key_id` so keys can rotate.
  - **Immutability.** Trigger `compliance_audit_logs_immutable` rejects `UPDATE`, and rejects `DELETE` and `TRUNCATE` unless `current_setting('oniva.audit_purge', true) = 'on'` and the row is past `retention_until`. The app role has `UPDATE`/`DELETE` revoked as well, so the trigger is a second wall. Tenant deletion (`ON DELETE CASCADE` from `tenants`) must set the GUC in the system role path; this is the first stop condition.
  - The existing `AuditAction` enum (`backend/app/models/compliance_audit_log.py`) is a `str` enum over a `String(50)` column, so new actions (`approval_requested`, `approval_decided`, `approval_expired`, `tool_policy_block`, `question_answered`) need no schema change.
- **KTD10 (R10). Questions reuse the approval machinery.** `ask_user` becomes an engine-native tool (U10) whose task parks on signal `answer`, with its own document `agent.questions` and its own decision route. The ledger indexes it as `kind='question'` when Python's authorize endpoint sees `toolName == "ask_user"`, which answers `allow`. The existing Python `ask_user` skill stays `_INTERACTIVE` and unserved.

### Assumptions

- `tenants.settings["approvals"]` is the home for policy knobs (`ttl_hours`, `require_separate_approver`, `rule_of_two`), next to `tenants.settings["features"]`. Defaults apply when it is absent.
- `ComplianceExecution` review decisions (`POST /api/v1/compliance/review-queue/{execution_id}/review`) stay where they are; the inbox links to them and does not re-implement them.
- Fleet has no "paused awaiting a human" state today, so the Fleet inbox source is failed runs only (see Risks).
- Deep Work's "question" is the intake clarification returned by `clarify()` in `backend/app/api/v1/deep_work_engine.py` (`IntakeResult.action != "proceed"`). It is held only by the browser today, so U7 persists it on `deep_work_runs`.
- The engine view's `approvals` member (`engine/src/agent/harness.ts` `approvals()`, frontend `ApprovalsState` in `frontend/lib/engine/types.ts`) keeps its shape. A `questions` member is added alongside it (U10) and parsed as empty when absent.
- The migration chain builds from an empty database (`100_baseline` squash; `backend/tests/postgres/test_migrations.py::test_upgrade_head_on_empty_db` runs in CI with pgvector; `DRIFT.md` is historical). New migrations chain after the current head `109_llm_usage_agent_role`, are idempotent, and the single-head assertions in `test_migrations.py` are updated. Units that add migrations land in dependency order so `down_revision` chains stay linear.

---

## Dependency order and file overlaps

```
U1 audit chain ──► U2 approval ledger + decide ──► U3 authorize endpoint ──► U5 engine worker hook ──► U13 e2e
                        │                              ▲                         ▲
                        │                              └──────── U4 engine taint ┘
                        ├──► U6 retire tickets + execute_python (needs U3)
                        ├──► U7 inbox backend ──► U9 inbox page
                        └──► U8 chat approval card
U10 engine questions (after U4) ──► U11 backend questions ──► U12 question card
```

Parallel groups:
- **Group A (start together):** U1, U4. Disjoint.
- **Group B (after U1, U4):** U2; U10 may run alongside U2 (after U4).
- **Group C:** U3 (after U2); U8 (after U2); U7 (after U2).
- **Group D:** U5 (after U3, U4); U6 (after U3); U9 (after U7, U8); U11 (after U2, U7, U10).
- **Group E:** U12 (after U8, U11); U13 (last).

File overlaps to schedule around (serialize these, never run them in parallel):

| File | Units, in merge order |
|---|---|
| `backend/app/api/v1/engine_conversations.py` | U2 (decide route), U3 (submit marks `untrusted`), U11 (answer route) |
| `backend/app/internal_api/tool_specs.py`, `tools.py`, `router.py` | U3, then U6 |
| `backend/app/internal_api/execution.py` | U6 only (U3 does not touch it) |
| `backend/app/auth/permissions.py` | U2 only |
| `backend/app/services/audit_logger.py`, `models/compliance_audit_log.py` | U1 only (others call the new `AuditChain`) |
| `backend/app/main.py` | U7 (mount inbox) |
| `backend/migrations/versions/` | U1 `110_`, U2 `111_`, U7 `112_`; coordinator fixes `down_revision` order |
| `engine/src/agent/tool-task.ts` | U4, then U10 |
| `engine/src/agent/policy.ts` | U4 (request fields), then U5 |
| `engine/src/server/routes.ts`, `engine/src/agent/harness.ts` | U10 only |
| `engine/src/worker/worker.ts`, `backend-policy.ts`, `config.ts` | U5 only |
| `engine/README.md` | U4, U5, U10 (each edits a different section; merge in order) |
| `engine/test/support/fake-gateway.ts` | U3 (contract clause, by test only), U5 |
| `frontend/lib/engine/types.ts` | U10 (adds `questions`), nothing else |
| `frontend/components/chat/EngineChat.tsx` | U8, then U12 |
| `frontend/components/layout/Sidebar.tsx` | U9 only |
| `frontend/lib/api/compliance.ts`, `frontend/components/compliance/ReviewTicketTable.tsx` | U9 only |

---

## Implementation Units

### U1. Tamper-evident audit chain

**Goal:** a per-tenant, signed, append-only chain that survives restarts and that anyone can verify.
**Requirements:** R8, AE5, AE6.
**Dependencies:** none.

**Files:**
- `backend/app/security/kms.py` (new: `KeyManagementService` protocol, `LocalDevKMS`, `get_kms()`)
- `backend/app/services/audit_chain.py` (new: `AuditChain.append`, `rebuild_head`, `verify_chain`)
- `backend/app/services/audit_logger.py` (route `ComplianceAuditLogger.log_agent_decision` through `AuditChain`; delete the in-memory `_last_entry_hash` and the db-swapping singleton behaviour of `get_audit_logger`; keep `log_decision` as a thin caller)
- `backend/app/models/compliance_audit_log.py` (add `seq`, `signing_key_id` columns and the new `AuditAction` members; add `AuditChainHead` model)
- `backend/app/models/__init__.py` (export `AuditChainHead`)
- `backend/app/api/v1/audit.py` (`GET /audit/chain/verify`; switch `/compliance/logs/verify` to use the KMS verifier)
- `backend/migrations/versions/110_audit_chain.py` (new: columns, `audit_chain_heads` with tenant RLS like `103_chat_read_model.py`, unique `(tenant_id, seq)`, immutability trigger, `REVOKE UPDATE, DELETE, TRUNCATE ... FROM oniva_app`)
- `backend/app/config/__init__.py` (`AUDIT_KMS_BACKEND`, default `local`)
- `backend/tests/test_audit_chain.py` (new)
- `backend/tests/test_kms.py` (new)
- `backend/tests/postgres/test_audit_immutability.py` (new)

**Approach:**
- Follow KTD9. The entry hash covers every signable field plus `seq`, `previous_hash` and `signing_key_id`, serialised with sorted keys as `_compute_entry_hash` does today.
- `verify_chain(tenant)` streams entries by `seq`, recomputes each hash, checks `previous_hash` against the prior entry, verifies each signature through the KMS (by `signing_key_id`), and returns `{ ok, checked, legacy_unchained, first_break: { seq, reason } | null }`.
- Existing rows keep `seq NULL` and are never rewritten (the trigger forbids it anyway).
- `LocalDevKMS` reuses the key generation and PSS signing now in `AuditKeyManager`; `AuditKeyManager` is removed once nothing imports it (grep first).
- The migration's trigger function and the `oniva.audit_purge` GUC are documented in the migration docstring and in `DEPLOYMENT.md` only if U-level docs already exist; do not edit `DEPLOYMENT.md` here.

**Patterns to follow:** RLS statements in `backend/migrations/versions/103_chat_read_model.py`; DB-level tests in `backend/tests/postgres/test_engine_tables.py` and `test_rls.py`.

**Test scenarios** (`backend/tests/test_audit_chain.py`, using the `AsyncSession` fixtures the nearby tests use):
- Three sequential appends for one tenant get `seq` 1, 2, 3 and each `previous_hash` equals the prior `entry_hash`.
- Two tenants append interleaved; each has its own `seq` starting at 1 and its own head.
- Concurrent appends (an `asyncio.gather` of 20) for one tenant yield 20 distinct consecutive `seq` values and `verify_chain` is ok. (Postgres-only variant lives in `tests/postgres`; the unit variant uses the same lock path on the test database.)
- A fresh logger instance after the first appends (simulating a restart) links to the last committed entry; the head is not reset to `None`.
- Deleting the head row and appending rebuilds it from the last committed entry and logs `audit_head_repaired`.
- Head row pointing at the wrong hash: append repairs it and the chain still verifies.
- Tampering cases through the ORM with the trigger disabled in the test fixture: edited `decision`, edited `rationale`, removed middle row, inserted row with a duplicate `seq` (unique violation), and a forged signature each make `verify_chain` return `ok: false` with the right `first_break.seq` and reason.
- Legacy rows (`seq NULL`) are counted as `legacy_unchained` and do not fail verification.

`backend/tests/test_kms.py`:
- `LocalDevKMS` signs and verifies; a flipped byte fails; a different key id fails.
- `get_kms()` returns `LocalDevKMS` in development and raises in `ENVIRONMENT=production` when `AUDIT_KMS_BACKEND` is `local` or unset.
- Key rotation: an entry signed with `key_id=a` still verifies after the active key becomes `b`.

`backend/tests/postgres/test_audit_immutability.py`:
- As the app role, `UPDATE` and `DELETE` on `compliance_audit_logs` fail; `INSERT` works.
- `TRUNCATE` fails.
- `DELETE` with `oniva.audit_purge=on` on a row not yet past retention fails; on a row past retention it succeeds.
- A tenant delete cascade fails without the GUC and succeeds with it.

**Verification:** backend ruff and pytest are green; `tests/postgres` file passes against a local Postgres.

### U2. Approval ledger and RBAC decide

**Goal:** a Python-owned index of every approval-like item, and a decision path only authorised users can use.
**Requirements:** R1, R2, R3, AE2, AE4.
**Dependencies:** U1 (audit entries).

**Files:**
- `backend/app/models/tool_approval.py` (new: `ToolApproval`)
- `backend/app/models/__init__.py`
- `backend/migrations/versions/111_tool_approvals.py` (new: table, tenant RLS, indexes, unique `(tenant_id, engine_conversation_id, tool_task_id)` where not null)
- `backend/app/services/approval_service.py` (new: `record_pending`, `can_decide`, `decide`, `expire_overdue`, `mark_closed`)
- `backend/app/auth/permissions.py` (add `approvals:decide`, `approvals:decide_own`)
- `backend/app/api/v1/engine_conversations.py` (`decide_approval` calls `ApprovalService.decide`; `_binding` stays for the other routes)
- `backend/app/services/engine_client.py` (no change expected; used as is)
- `backend/tests/test_approval_service.py` (new)
- `backend/tests/test_engine_approval_decision.py` (new)
- `backend/tests/postgres/test_tool_approvals_rls.py` (new)
- `backend/tests/internal/test_engine_conversations.py` (extend the existing decide tests)

**Approach:**
- Columns: `id`, `tenant_id`, `kind` (`tool_call | review | question`), `status` (`pending | deciding | approved | denied | answered | expired | cancelled`), `project_id`, `conversation_id` (root), `engine_conversation_id`, `run_id`, `tool_call_id`, `tool_task_id`, `tool_name`, `arguments_preview` (JSONB, scrubbed), `reason`, `rule` (`requires_approval | rule_of_two | review | question`), `approver_role`, `requested_by`, `decided_by`, `decision`, `note`, `taint_sources` (JSONB), `expires_at`, `decided_at`, `legacy_ticket_id`, `created_at`, `updated_at`.
- `record_pending` is an upsert keyed on `(tenant, engine_conversation, tool_task_id)`; a repeat returns the existing row (idempotent for KTD2).
- `decide` implements KTD5 exactly and writes `approval_decided` through `AuditChain` with actor id, actor role and tool name.
- Scrub arguments with the helper `execution.py` already uses (`_scan_credentials`), and also drop keys matching the pattern in `frontend/lib/engine/approval.ts` (`SENSITIVE_KEY`).
- Errors map: unknown or other-tenant row 404, failed RBAC 403, `deciding`/decided by someone else 409, expired 410.
- The route keeps today's request and response (`DecisionBody`, engine approval JSON) so `frontend/lib/engine/client.ts` is unchanged.
- Rows with no ledger entry (an approval created before this change, or by an engine without U5) fall back to the old rule (conversation owner or tenant admin) so nothing in flight becomes undecidable. The fallback is removed one release after U5 ships; record this in the route docstring.

**Test scenarios** (`backend/tests/test_approval_service.py`):
- `can_decide` matrix: viewer no; member who is the requester on a `member` row yes; member who is not the requester no; member on an `admin` row no; admin yes; owner yes; user of another tenant 404.
- `require_separate_approver=true`: an admin who is also the requester gets `self_approval_forbidden`.
- `record_pending` twice with the same key returns one row.
- `decide` approve: row goes `pending -> deciding -> approved`, the engine client is called once with `principal=str(user.id)`, one `approval_decided` audit entry is written.
- Engine raises `approval_rejected(already_decided)`: row ends `cancelled` or matches the engine, response 409, no second audit entry.
- Engine unreachable (502): row returns to `pending`, response 502, no audit entry.
- Crash simulation: row left `deciding`; a retry by the same user finalises; a retry by another user gets 409.
- Two concurrent deciders: exactly one succeeds, one gets 409.
- `expire_overdue` marks rows past `expires_at` as `expired` and writes `approval_expired`.

`backend/tests/test_engine_approval_decision.py` (HTTP, via the app test client with a fake `EngineClient` transport):
- AE2: viewer 403, member of another project 403, other tenant 404, and in all three the fake engine records zero decide calls.
- Authorised admin 200; the response is the engine approval.
- A row with no ledger entry falls back to owner-or-admin.

`backend/tests/postgres/test_tool_approvals_rls.py`: a tenant cannot read another tenant's rows; the unique key rejects a duplicate.

**Verification:** backend ruff and pytest green.

### U3. Tool authorize endpoint and Rule of Two

**Goal:** the Python side of KTD2 and KTD6.
**Requirements:** R1, R7, R10 (question indexing), AE3.
**Dependencies:** U2.

**Files:**
- `backend/app/internal_api/tool_specs.py` (add `untrusted_output`, `scope`, `approver_role` to `ToolSpec`; classify every entry per KTD6)
- `backend/app/internal_api/tools.py` (`ManifestTool.untrustedOutput`, `build_manifest`)
- `backend/app/internal_api/authorize.py` (new: `POST /tools/authorize` handler)
- `backend/app/internal_api/router.py` (mount it next to the model and tool routers)
- `backend/app/services/tool_policy.py` (new: pure `decide_tool_call(spec, request, tenant_policy)`)
- `backend/app/api/v1/engine_conversations.py` (submit passes `untrusted: true` to the engine when the request carries attachments; grep the submit body model for the attachment field)
- `backend/tests/internal/test_tool_authorize.py` (new)
- `backend/tests/internal/test_manifest.py` (extend)
- `backend/tests/internal/test_contract_conformance.py` (authorize clause)
- `backend/tests/test_tool_policy.py` (new, pure unit tests)

**Approach:**
- `decide_tool_call` is pure and implements the KTD6 matrix in order. The handler loads the tenant's `approvals` settings, calls it, and on `requireApproval` calls `ApprovalService.record_pending` (and writes `approval_requested`, or `tool_policy_block` on a block, through `AuditChain`).
- TTL: `tenant.settings["approvals"]["ttl_hours"]`, default 168, `0` means `approvalTtlMs: null`.
- `ask_user` returns `allow` and records a `kind='question'` row (KTD10). `ask_user` stays `in_manifest=False`: the engine registers it natively (U10), so it is not in the gateway manifest.
- Idempotent per `requestId`, backed by the ledger's unique key. A `block` is deterministic, so it is recomputed, not stored.
- Auth and tenancy: same dependency as `/internal/models/policy`; the handler never trusts `tenantId` from a conversation it cannot match against `engine_conversations` (use the same lookup `tools.py` uses for invoke binding).
- Add `untrustedOutput` to the manifest as optional. The engine treats absence as `false` so old backends keep working.

**Test scenarios** (`backend/tests/test_tool_policy.py`):
- Read tool, not tainted: `allow`. Read tool, tainted: `allow`.
- `requires_approval` tool: `requireApproval` with the spec's `approver_role` (`execute_python` is `admin`, `delete_file` is `member`).
- Tainted + `write` + `external` + top-level conversation: `requireApproval`, rule `rule_of_two`.
- Same with `agentRole="research_worker"`: `block`, code `untrusted_input_no_approver`.
- Tainted + `write` + `workspace` (`write_file`): `allow`.
- Not tainted + external write that does not require approval (`shell_exec` outside a worker): `allow`.
- Tenant `approvals.rule_of_two=false`: tainted external write is `allow` (explicit opt-out), `requires_approval` still applies.
- Unknown tool name: `block`.
- Exhaustiveness: every entry in `TOOL_SPECS` has an explicit `scope` and `untrusted_output` decision (a test iterates the table and fails on an unclassified entry).

`backend/tests/internal/test_tool_authorize.py`:
- Missing or bad bearer: 401. Tool call for a conversation of another tenant: 404.
- `requireApproval` creates exactly one ledger row, with scrubbed arguments, even when posted twice with the same `requestId`.
- `ask_user` returns `allow` and creates a `question` row.
- Manifest includes `untrustedOutput: true` for `browse_url` and `false` for `write_file`.
- Contract clause: the response shapes match the `fake-gateway.ts` clause tags.

**Verification:** backend ruff and pytest green.

### U4. Engine taint tracking

**Goal:** the engine remembers, durably, that a run has read untrusted content, and tells the host.
**Requirements:** R7.
**Dependencies:** none.

**Files:**
- `engine/src/agent/taint.ts` (new: `taintDoc`, `markTainted(tx, rootId, source)`, `taintOf(session, rootId)`)
- `engine/src/tools/registry.ts` (add optional `untrustedOutput?: boolean` to the tool definition)
- `engine/src/tools/gateway.ts` (parse optional manifest `untrustedOutput`, set it on the gateway tool)
- `engine/src/agent/tool-task.ts` (mark taint in the commit that stores a tool result when the tool is `untrustedOutput`; pass `toolTaskId` and `taint` into the authorize request)
- `engine/src/agent/policy.ts` (`ToolAuthorizationRequest` gains `toolTaskId: string` and `taint: { tainted: boolean; total: number; sources: TaintSource[] }`)
- `engine/src/agent/submission.ts`, `engine/src/agent/inbox.ts`, `engine/src/server/routes.ts` (an `untrusted` boolean on submit marks `kind: "input"` taint in the same commit that places the input)
- `engine/src/index.ts` (export types)
- `engine/README.md` (document the document kind, the manifest field, the submit field and the authorize request fields)
- `engine/test/agent/taint-suite.ts`, `engine/test/agent/taint.memory.test.ts`, `engine/test/agent/taint.postgres.test.ts` (new, same shape as `approvals-suite.ts`)
- `engine/test/tools/gateway.test.ts` (manifest parsing)
- `engine/test/server/http.test.ts` (submit `untrusted`)

**Approach:**
- The document is on the root conversation id (`ToolConversation.rootId ?? id`; `tool-task.ts` already computes `rootId` at line ~172). A child conversation's read therefore taints the run that owns it.
- Cap `sources` at 20 and keep counting in `total`. `sources[].ref` is the tool call id or submission id.
- `authorize` still receives a `structuredClone` of the request (`policy.ts`), so `HostPolicy` cannot mutate taint.
- A tool result that is an error still taints if the tool declares `untrustedOutput` (it may contain page text).
- Clean-room: write from the README contract and this plan only.

**Test scenarios** (`engine/test/agent/taint-suite.ts`, run on memory and Postgres backends):
- A run with a read tool that is `untrustedOutput` marks taint; the next tool's authorize request carries `tainted: true` and the source.
- A tool that is not `untrustedOutput` does not mark.
- The mark and the stored tool result commit together: a crash injected between result and mark is impossible (the document is read after recovery and agrees with the entries).
- A subagent child's untrusted read taints the root; the parent's next authorize sees it.
- A submit with `untrusted: true` taints with `kind: "input"` before the first model request.
- More than 20 sources: `sources.length === 20`, `total` keeps counting.
- Taint survives a worker restart (reopen the harness on the same storage).
- The authorize request received by a recording `HostPolicy` includes `toolTaskId` equal to the task id that appears later in `harness.approvals(...)`.
- Gateway manifest with `untrustedOutput: "yes"` (wrong type) is rejected like other malformed fields; absent means `false`.
- HTTP submit with a non-boolean `untrusted` is 400 and nothing is committed.

**Verification:** engine `npm run lint && npm run typecheck && npx vitest run` green.

### U5. Engine worker delegates tool policy to the backend

**Goal:** the worker holds no tool policy of its own; Python decides; approvals use the policy TTL.
**Requirements:** R1, R3, AE1, AE4.
**Dependencies:** U3 (endpoint), U4 (request fields).

**Files:**
- `engine/src/worker/backend-policy.ts` (`BackendPolicyClient.authorizeTool`, request and response validation, retry and fail-closed)
- `engine/src/worker/worker.ts` (`workerHostPolicy`: use the backend for every tool call that is not a manifest read; keep the existing local rule only when no backend is configured, i.e. local dev with `ENGINE_POLICY` not `backend`)
- `engine/src/worker/config.ts` (`ENGINE_APPROVAL_TTL_MS` becomes the local-mode value only; document it)
- `engine/src/worker/redact.ts` (reuse for arguments sent to the backend; no change expected)
- `engine/test/support/fake-gateway.ts` (add `/internal/tools/authorize` with `[clause:]` tags for allow, block, requireApproval and repeat)
- `engine/test/worker/backend-policy.test.ts` (extend)
- `engine/test/worker/worker.test.ts` (extend)
- `engine/test/worker/config.test.ts`
- `engine/README.md` (HostPolicy, env table, policy section)

**Approach:**
- `authorizeTool(request)` for a gateway tool whose manifest `sideEffect === "none"`: allow locally with no round trip (the manifest is Python's own statement). Everything else sends the KTD2 body, with arguments redacted by `redact.ts`.
- On `requireApproval` build `{ decision: "requireApproval", reason, expiresAt: approvalTtlMs === null ? undefined : clock.now() + approvalTtlMs }`. The reason text is whatever the backend sent.
- Transient failures retry on the shared `RetryOptions` budget with the same `requestId`. After the budget, return `{ decision: "block", reason: "policy_unavailable" }`. A 401/403 or malformed body also blocks, immediately, without retry.
- Never log arguments or response bodies (same rule as model policy).

**Test scenarios:**
- Manifest read: no HTTP call is made; result `allow`.
- Write tool, backend `allow`: the tool runs once.
- Backend `requireApproval` with `approvalTtlMs: 604800000`: the engine item's `expiresAt` equals `clock.now() + 604800000` (use the injected clock), and the tool task is `waiting` on `approval`.
- Backend `requireApproval` with `approvalTtlMs: null`: `expiresAt` is `null` and the item never expires.
- Backend `block`: the tool result is `blocked` and carries the backend reason.
- Backend down for longer than the retry budget: the call is `blocked` with `policy_unavailable`; no tool ran; no local fallback.
- Backend 5xx twice then `allow`: one effective call, `policy_retry` logged, same `requestId` on every attempt.
- Malformed body or a 403: immediate block, no retry.
- Restart case: harness reopened mid-wait does not call authorize again for a task already waiting (the item exists), verified by the fake gateway's call count.
- The request body never contains a key matching the redaction patterns (inject one and assert it is scrubbed).
- Local mode (no backend configured) keeps today's `requiresApproval` behaviour so existing worker tests pass.

**Verification:** engine lint, typecheck and vitest green; clean-room grep empty.

### U6. Retire review tickets; approved `execute_python` runs

**Goal:** one approval path; code execution works after approval and never before.
**Requirements:** R4, R5, AE7.
**Dependencies:** U2, U3.

**Files:**
- `backend/app/skills/execute_python/tool.py` (replace `_execute_e2b`'s ticket creation with a sandbox run)
- `backend/app/internal_api/execution.py` (the `approval_required` guard in `execute_tool`; make sure `build_skill_context` passes `run_id` and `tool_call_id` into `ctx.metadata`)
- `backend/app/services/deep_work/sandbox_tools.py` (reference only; extract a shared helper only if copying more than about 20 lines)
- `backend/app/services/review_queue.py` (delete)
- `backend/app/api/v1/audit.py` (delete `/reviews*` routes and their request models; keep every other route)
- `backend/app/api/v1/sandbox.py` (stop creating `PENDING_REVIEW` executions: respond 410 with a message pointing to engine chat, or delete the route if grep shows no caller)
- `backend/app/services/output_gating.py` and `backend/app/api/v1/wide_research.py` (write `kind='review'` ledger rows through `ApprovalService.record_pending`; no `ReviewQueueService`)
- `backend/app/api/v1/compliance.py` (remove references to `review_queue` if any; keep `/review-queue`, which is `ComplianceExecution`)
- `backend/migrations/versions/111_tool_approvals.py` is U2's; this unit adds a data step in `backend/migrations/versions/113_retire_review_tickets.py` (new): copy open `human_review_tickets` (`pending`, `assigned`, `in_review`, `escalated`) into `tool_approvals` with `kind='review'`, `legacy_ticket_id`, and set the old rows' `status` to `superseded` is NOT done (read-only history; the trigger-style protection is not needed, so just leave them)
- `backend/tests/test_execute_python_skill.py` (rewrite: it asserts the ticket interface today)
- `backend/tests/internal/test_execute_python_gateway.py` (new)
- `backend/tests/test_retire_review_tickets.py` (new)
- `backend/tests/integration/test_deep_work_e2e.py` (remove ticket references)

**Approach:**
- KTD7. Through the gateway, `execute_python` runs `code` (and uploads `input_files`) in `SandboxExecutor`, returns stdout, stderr and files under the existing size and secret guards (`guard_result`), and respects the spec's 90 s timeout.
- `execute_tool` for a `requires_approval` spec looks up the ledger row `(tenant, run_id, tool_call_id)` and requires `status='approved'` with `decision='approve'`; otherwise raise HTTP 403 `approval_required` as a tool outcome error (not a 500). Specs that don't require approval are untouched.
- Outside the gateway the skill returns `{ success: false, error: "Code execution needs an approved request. Use engine chat." }` and creates nothing.
- Before deleting a route or module, grep every caller; this unit must leave `grep -rn "review_queue\|ReviewQueueService\|ReviewTicketCreate" backend/app` empty and the frontend callers are replaced by U9 (see the second stop condition: merge U9 first if `ENABLE_COMPLIANCE` is on in any environment).

**Test scenarios:**
- `execute_python` via `execute_tool` with an approved ledger row: the fake `SandboxExecutor` receives the code once and the outcome carries its output.
- Without a ledger row, or with `pending`, `denied` or `expired`: 403 `approval_required`, the sandbox fake records no call.
- Row approved for a different `toolCallId`: refused (no reuse across calls).
- `ENABLE_SANDBOX=false`: the existing refusal message, still no execution.
- Outside the gateway: refusal, and no ledger row or ticket is created.
- Idempotency: the same `Idempotency-Key` twice returns the stored outcome and runs the sandbox once (existing `tool_invocations` path).
- Migration data step: two open tickets and one closed ticket yield two `review` ledger rows with `legacy_ticket_id`; running it twice does not duplicate.
- `GET /api/v1/audit/reviews` and `POST /api/v1/audit/reviews/{id}/decision` are 404 in the OpenAPI document.
- `output_gating` failure now creates a `review` ledger row.
- No importable module named `review_queue` remains.

**Verification:** backend ruff and pytest green; the Postgres migration test passes.

### U7. Inbox backend

**Goal:** one endpoint answering "what is waiting on me?".
**Requirements:** R6, AE7.
**Dependencies:** U2.

**Files:**
- `backend/app/services/inbox_service.py` (new: adapters and `list_for_user`, `count_for_user`)
- `backend/app/api/v1/inbox.py` (new: `GET /api/v1/inbox`, `GET /api/v1/inbox/count`)
- `backend/app/main.py` (include the router)
- `backend/app/models/deep_work_run.py` (add `awaiting_input` JSONB, nullable)
- `backend/migrations/versions/112_deep_work_awaiting_input.py` (new)
- `backend/app/api/v1/deep_work_engine.py` (`clarify` sets `awaiting_input` when `IntakeResult.action != "proceed"`; the plan route clears it)
- `backend/app/services/deep_work_state.py` (helper `set_awaiting_input` / `clear_awaiting_input`)
- `backend/tests/test_inbox_service.py` (new)
- `backend/tests/test_inbox_api.py` (new)
- `backend/tests/postgres/test_inbox_rls.py` (new)
- `backend/tests/test_deep_work_engine_api.py` (extend: clarify persists, plan clears)

**Approach:**
- KTD8. The route takes `kinds` (comma list), `limit` (default 50, max 200) and a `cursor` (created-at plus id). Sorting: due soonest, then newest.
- Adapters read, per tenant (RLS applies as usual):
  - ledger rows `pending` and not expired, filtered by visibility (admins see all of the tenant; members see rows they requested or may decide);
  - `ComplianceExecution` with `status == manual_review` (admins/owners only, as in `compliance.py`);
  - `BackgroundJob` `failed` in the last 72 h for the user's jobs (admins: tenant);
  - `DeepWorkRun` with `awaiting_input IS NOT NULL` and `status='running'` for the user.
- Each item carries `actions`: for ledger rows `[{ type: "decide", endpoint, requiresRole }]` computed with `can_decide`, so the UI can grey out buttons without a second call; others link via `href`.
- `count` returns `{ total, byKind }` cheaply with one query per source.
- The lazy sweep: `list_for_user` calls `ApprovalService.expire_overdue(tenant)` before reading.
- Do not add Redis or any queue; this is a read model.

**Test scenarios** (`backend/tests/test_inbox_service.py`):
- Each source on its own yields one correctly shaped item.
- Mixed sources are ordered by due date then recency.
- Member sees only own and decidable rows; admin sees the tenant's; user of another tenant sees none.
- `kinds=approval` filters. Cursor pagination returns each item once.
- Expired and decided ledger rows are absent. An overdue pending row is swept to `expired` and disappears.
- A failed fleet job older than 72 h is absent.
- A Deep Work run with `awaiting_input` appears; after `plan` clears it, it is gone.
- `actions` for a viewer carry no decide action.
- `count` agrees with `list`.

`backend/tests/test_inbox_api.py`: 401 without auth; 200 shape with the response model; `limit` above 200 is 422.
`backend/tests/postgres/test_inbox_rls.py`: no cross-tenant leakage through any adapter.

**Verification:** backend ruff and pytest green.

### U8. Chat approval card

**Goal:** the production chat card shows why, who can decide, and handles refusal.
**Requirements:** R9, AE1, AE2, AE3.
**Dependencies:** U2.

**Files:**
- `frontend/components/chat/shared/interactive/ApprovalCard.tsx`
- `frontend/lib/engine/approval.ts` (`toDecisionError`: 403 distinguishes `not_authorized` / `self_approval_forbidden`; add `describeApproval` to turn `rule` and `reason` into copy)
- `frontend/lib/engine/viewToMessages.ts` (pass `reason` and the new `approver` hint through to the card part, if absent today)
- `frontend/components/chat/EngineChat.tsx` (handlers already exist at `approvalHandlers`; show an "awaiting approval" banner that links to the inbox)
- `frontend/components/chat/__tests__/approval.test.tsx` (extend)
- `frontend/lib/engine/__tests__/approval.test.ts` (new or extend if present)
- `frontend/lib/engine/__tests__/viewToMessages.test.ts` (extend)

**Approach:**
- The decide call and URL are unchanged. Add copy: "Needs approval from an admin" for `approver_role=admin` rows (the engine item has no role, so the backend adds `approverRole` and `rule` to the decision error body and to a new field on the engine approval `reason` string; simplest: the reason text from Python already says "Requires admin approval" or "This run read untrusted content", so the card renders `reason` as is).
- On 403 the card locks with "You cannot decide this. Ask an admin." (existing message plus the hint) and keeps showing the arguments, which stay credential-scrubbed by `summarizeArguments`.
- Pending state survives a page reload because the card is derived from `view.approvals.items` (already the case).
- Expired items render the existing "expired" state; no new engine fields are needed.

**Test scenarios:**
- Reason text renders on the card for a Rule-of-Two approval and for a `requires_approval` one.
- 403 locks the card, shows the not-allowed copy and does not retry; 409/410 shows already-decided; 5xx stays retryable.
- Approve and deny call `decide(toolTaskId, ...)` once each and the card shows pending until the view's item changes status.
- A pending item still renders after remounting with the same view (reload).
- Credential-looking argument values are shown as `[hidden]`.
- The "awaiting approval" banner appears only while a pending item exists.

**Verification:** frontend `npx tsc --noEmit && npx jest --ci --watchAll=false` green.

### U9. "Needs attention" inbox page

**Goal:** a global page and badge for everything waiting on a person.
**Requirements:** R6, R4 (UI side), AE7.
**Dependencies:** U7, U8.

**Files:**
- `frontend/app/(dashboard)/inbox/page.tsx` (new)
- `frontend/components/inbox/InboxList.tsx`, `frontend/components/inbox/InboxItemRow.tsx` (new)
- `frontend/lib/api/inbox.ts` (new: `listInbox`, `inboxCount`)
- `frontend/hooks/useInbox.ts` (new: polling every 30 s while visible; refetch on window focus)
- `frontend/components/layout/Sidebar.tsx` (new "Needs attention" item with a count badge, placed under Chat)
- `frontend/lib/api/compliance.ts` and `frontend/components/compliance/ReviewTicketTable.tsx` (stop calling `/audit/reviews*`; point to the inbox `review` items or remove the table, whichever `app/(dashboard)/compliance/page.tsx` uses)
- `frontend/lib/api/index.ts` (export)
- `frontend/app/(dashboard)/inbox/__tests__/page.test.tsx` (new)
- `frontend/components/inbox/__tests__/InboxList.test.tsx` (new)
- `frontend/hooks/__tests__/useInbox.test.tsx` (new)
- `frontend/components/layout/__tests__/Sidebar.inbox.test.tsx` (new)

**Approach:**
- Rows group by kind with a filter chip row. Approval and review rows decide inline through the same `decide` client used by chat (extract nothing new; import `decide` and `toDecisionError`). Compliance, Fleet and Deep Work rows link to their page with `href`. Question rows link to the chat until U12 adds inline answering.
- The badge shows `total` from `/inbox/count` and hides at zero; it caps display at `99+`.
- Empty state: "Nothing needs your attention."
- Honour the existing `featureFlag` mechanism for the compliance and fleet kinds (the backend already omits kinds the user can't see; the page only renders what it gets).
- Accessibility: rows are list items with a labelled action group; the badge has a text alternative ("3 items need attention").

**Test scenarios:**
- Mixed items render in order with correct labels and links.
- Approve in the inbox calls the decision endpoint with the right conversation and task ids and removes the row on success; 403 shows the not-allowed copy on that row only.
- Filtering by kind narrows the list.
- Polling refetches and a new item appears; unmount stops polling.
- The badge shows the count, hides at 0, shows `99+` above 99, and has the accessible text.
- Empty and error states render.
- The compliance page no longer requests `/audit/reviews` (assert on the mocked `apiFetch` calls).

**Verification:** frontend tsc and jest green.

### U10. Engine human-question primitive (`ask_user`)

**Goal:** a model can ask a person a question; the wait is durable like an approval.
**Requirements:** R10, AE8.
**Dependencies:** U4 (shares `tool-task.ts`).

**Files:**
- `engine/src/agent/questions.ts` (new: `questionsDoc` kind `agent.questions`, `requestQuestion`, `applyAnswer`, `closeQuestion`, `QuestionRejected`)
- `engine/src/agent/ask-user-tool.ts` (new: the native `ask_user` tool definition)
- `engine/src/agent/tool-task.ts` (a phase for tools flagged `humanInput: true`: create the question item and park on signal `answer`, resolve on the answer or on expiry)
- `engine/src/tools/registry.ts` (`humanInput?: boolean`)
- `engine/src/agent/harness.ts` (`answerQuestion`, `questions`)
- `engine/src/server/routes.ts` (`POST /v1/conversations/:id/questions/:toolTaskId/answer` with `{ answer: { optionIds?: string[], text?: string } }`, principal in `X-Oniva-Principal`)
- `engine/src/watch/` (include `questions` in the conversation view and its patches; follow how `approvals` is exposed)
- `engine/src/worker/worker.ts` (register the native tool when the conversation's agent tools include `ask_user`)
- `engine/src/index.ts`, `engine/README.md`
- `engine/test/agent/questions-suite.ts`, `questions.memory.test.ts`, `questions.postgres.test.ts` (new)
- `engine/test/server/http.test.ts`, `engine/test/watch/` (view member and patches)

**Approach:**
- Same shape and invariants as `approvals.ts`: one pending item per tool task, one answer, rejected reasons (`not_owned`, `already_answered`, `expired`, `not_waiting`, `invalid`), `answer` payload size-capped, `ENGINE_QUESTION_TTL_MS` (default 7 days, `0` none).
- The model sees the answer as the tool result text. Options are returned as the chosen labels, and free text as given.
- A question call still goes through `authorizeTool` first (Python answers `allow` and indexes it, U3).
- The watch view gains `questions: { items: QuestionItem[] }`, absent on older documents (readers default to empty).
- Clean-room: derive from the README contract and `approvals.ts` structure only.

**Test scenarios** (`questions-suite.ts`, memory and Postgres):
- The model calls `ask_user`; the task parks `waiting` on `answer`; the item is `pending` with the question and options.
- Answer with a valid option: the run resumes, the model's next turn contains the answer, and exactly one result entry exists.
- Answer twice: the second is rejected `already_answered`.
- Answer for another conversation's task id: `not_owned`.
- Expiry before an answer: tool result `question_expired`, run continues.
- Restart while waiting: reopen the harness, the item is still pending, answering works.
- Invalid answer shape or an oversized answer: 400 over HTTP and nothing committed.
- View patch `/questions/items/<n>` arrives on open and on answer.
- An abort of the run while waiting closes the item `cancelled`.

**Verification:** engine lint, typecheck and vitest green; clean-room grep empty.

### U11. Backend questions route and indexing

**Goal:** answer questions through the control plane, and list them in the inbox.
**Requirements:** R10, R6, AE8.
**Dependencies:** U2, U7, U10.

**Files:**
- `backend/app/services/engine_client.py` (`answer(...)`)
- `backend/app/api/v1/engine_conversations.py` (`POST /{conversation_id}/questions/{tool_task_id}/answer`)
- `backend/app/services/approval_service.py` (reuse `decide` mechanics for `kind='question'`; the permission is the conversation owner or an admin, not `approvals:decide`)
- `backend/app/services/inbox_service.py` (the `question` adapter reads the ledger rows already; add the `answer` action shape)
- `backend/app/services/engine_chat_agent.py` (offer `ask_user` to the model only when the conversation can reach a human: top-level chat, not workers)
- `backend/tests/test_engine_question_answer.py` (new)
- `backend/tests/internal/test_engine_chat_agent.py` (extend)
- `backend/tests/test_inbox_service.py` (extend)

**Approach:**
- Same protocol as KTD5 (`pending -> deciding -> answered`), same audit entry (`question_answered`), same 404/403/409/410 mapping. Who may answer: the user the conversation belongs to, or a tenant admin. This is deliberately looser than approvals because answering grants no power.
- Deep Work workers never get `ask_user` (they have no human; the Rule-of-Two block in U3 already assumes this).

**Test scenarios:**
- Owner answers: 200, the engine fake sees one answer with the right principal, row `answered`.
- Another member of the tenant: 403; other tenant: 404; admin: 200.
- Second answer: 409.
- Inbox shows the question and removes it after the answer.
- The chat agent tool list includes `ask_user` for a top-level conversation and excludes it for `agentRole` conversations.

**Verification:** backend ruff and pytest green.

### U12. Chat question card

**Goal:** question cards work in production chat.
**Requirements:** R9, R10, AE8.
**Dependencies:** U8, U11, and the engine `questions` view member (U10).

**Files:**
- `frontend/lib/engine/types.ts` (parse `questions`, default empty when absent)
- `frontend/lib/engine/viewToMessages.ts` (turn pending `questions.items` into the existing question message parts)
- `frontend/lib/engine/client.ts` (`answer(conversationId, toolTaskId, answer)`)
- `frontend/lib/engine/useEngineConversation.ts` (expose `answerQuestion`)
- `frontend/components/chat/EngineChat.tsx` (supply `onQuestionAnswer` in the renderer handlers, next to `approvalHandlers`)
- `frontend/components/chat/shared/MessageRenderer.tsx` (no change expected; `onQuestionAnswer` already exists at line ~67)
- `frontend/components/inbox/InboxItemRow.tsx` (inline answering for `question` rows)
- `frontend/lib/engine/__fixtures__/chat_views.json` (add a view with a pending question, captured from a real U10 run, not hand-made)
- `frontend/lib/engine/__tests__/viewToMessages.test.ts`, `frontend/components/chat/__tests__/EngineChat.test.tsx`, `frontend/components/inbox/__tests__/InboxList.test.tsx` (extend)

**Approach:**
- Reuse `AgentQuestion`, `AgentQuestionGroup` and `QuestionPrompt` (`frontend/components/chat/shared/interactive/`). Map `optionIds` and `otherValue` to the engine answer shape.
- While the answer is in flight the card is disabled; on failure the card shows an inline retry. Locked states mirror the approval card.

**Test scenarios:**
- A view with a pending question renders a question card; an answered one renders the answer read-only.
- Selecting an option and submitting calls the client with the right ids once.
- Free-text "other" answer is sent as `text`.
- 403/409/410/5xx map to the same inline messages as approvals.
- A view without `questions` (older engine) still parses.
- The inbox row answers inline and disappears after success.

**Verification:** frontend tsc and jest green.

### U13. End-to-end proof, restart and RBAC

**Goal:** the exit criterion, executed.
**Requirements:** R1-R5, R8, AE1, AE2, AE4, AE6.
**Dependencies:** U5, U6 (and U7 for the inbox assertion).

**Files:**
- `backend/tests/e2e/test_approval_restart.py` (new)
- `backend/tests/e2e/harness.py`, `backend/tests/e2e/backend_app.py`, `backend/tests/e2e/dw_fakes.py` (extend only as needed: a scripted model that calls `execute_python`, a fake sandbox, a second tenant user and a viewer)
- `engine/test/worker/approval-restart.test.ts` (new: worker-level kill and reopen with a stubbed backend authorize)
- `frontend/e2e/` Playwright spec only if a spec for chat recovery already exists (`chat-engine-recovery`); add `approval-recovery` beside it following the same helpers
- `DEPLOYMENT.md` (short "Approvals and audit" section: TTL, tenant settings, KMS backend setting, the purge GUC)

**Approach:** copy the structure of `backend/tests/e2e/test_chat_engine_recovery.py` (kill the worker with SIGKILL, restart, assert the continuation). Add a backend restart between the kill and the decision.

**Test scenarios:**
- AE1: member's chat triggers `execute_python`; the call parks; `worker.kill(SIGKILL)` and restart the worker and the backend app; the inbox still lists the approval; an admin approves; the fake sandbox ran exactly once; the assistant message contains the output.
- AE2: before the admin acts, viewer and a member of another project get 403 and another tenant's user 404; the engine item stays `pending` (read from the watch view); no `approval_decided` entry exists.
- AE4: with a tiny tenant TTL and the engine clock advanced, the call ends `approval_expired` and the ledger row is `expired`.
- AE6: after the whole scenario, `verify_chain` for the tenant is ok and entries are consecutive; a direct SQL `UPDATE` by the app role fails.
- Rule of Two, scripted: `browse_url` then `linear_create_issue` parks on approval with the untrusted reason; a worker-role conversation doing the same is blocked and the run completes.
- `execute_python` invoked straight at the gateway with no approval row is refused 403 and the sandbox sees nothing (defence in depth).

**Verification:** `ONIVA_E2E=1 uv run pytest -m e2e tests/e2e` and the engine vitest file are green.

---

## Verification Contract

| Area | Gate |
|---|---|
| engine | `npm run lint && npm run typecheck && npx vitest run` green (run from `engine/`) |
| backend | `uv run ruff check app tests && uv run pytest -q -p no:cacheprovider` green (run from `backend/`) |
| backend Postgres | `backend/tests/postgres/test_audit_immutability.py`, `test_tool_approvals_rls.py`, `test_inbox_rls.py` pass against a local Postgres (these are skipped, not failed, without one; run them for U1, U2, U7) |
| frontend | `npx tsc --noEmit && npx jest --ci --watchAll=false` green (run from `frontend/`) |
| e2e | `ONIVA_E2E=1 uv run pytest -m e2e tests/e2e` green (run from `backend/`), plus the Playwright approval-recovery spec if added |
| hygiene | clean-room grep on `engine/src` and `engine/test` for third-party pi or earendil names is empty; gitleaks clean; no `.env` or key material committed; no new `console.log` of arguments |
| grep gates | `grep -rn "review_queue\|ReviewQueueService\|HumanReviewTicket" backend/app` shows only the model and the migration; `grep -rn "audit/reviews" frontend` is empty |

## Definition of Done

- U1-U13 are merged with their test scenarios passing, in dependency order.
- All Verification Contract gates are green in CI.
- AE1-AE8 hold, with AE1, AE2, AE4 and AE6 proven by U13.
- `compliance_audit_logs` is append-only at the database, every tenant's chain verifies, and signing goes through `KeyManagementService` with no PEM-on-disk path in production.
- No code creates a review ticket, and `execute_python` runs only after an approved ledger row.
- `DEPLOYMENT.md` documents approval TTL, tenant approval settings, the KMS backend setting and the audit purge GUC.
- Open Questions below are answered in writing by the coordinator before the unit that depends on them merges.

## Risks and Open Questions

| # | Question or risk | Affects | Default if unanswered |
|---|---|---|---|
| 1 | Sticky taint makes a long chat permanently "tainted" after one web search, so every outside write asks for approval from then on. Is that acceptable, or should taint reset per run, or on a human "I reviewed this" action? | KTD6, U4, U3 | Keep sticky; revisit with real usage. Per-run reset is a one-line change in `markTainted` scope but weakens the guarantee, because history still holds the untrusted text. |
| 2 | Solo-owner tenants cannot get a second approver. Defaults allow self-approval (`require_separate_approver=false`). Is that the right default for regulated tenants? | KTD4, U2 | Default off; turn on per tenant. |
| 3 | The per-call authorize round trip adds latency and a new dependency on the backend for every non-read tool. Fail-closed means a backend outage stops all writes. | KTD2, U5 | Accept; reads still run. Add a short in-process cache of `allow` answers only if the stop-condition latency is hit. |
| 4 | Fleet has no pause state, so "Fleet interventions" in the inbox is only failed runs. Operators may expect "agent needs help". | KTD8, U7 | Ship failures only; real pause states arrive with Fleet on engine tasks (Phase 6). |
| 5 | Parallel units adding migrations can fork the Alembic head. | U1, U2, U6, U7 | Land migration units in order; the single-head test in `test_migrations.py` catches a fork; the coordinator re-chains `down_revision` at merge. |
| 6 | The immutability trigger vs. tenant deletion and retention purge: who runs them and as which role? | KTD9, U1 | System role sets `oniva.audit_purge`; document in `DEPLOYMENT.md`. This is the first stop condition. |
| 7 | `deciding` rows after a crash between engine acceptance and ledger finalisation rely on the same user retrying or a 60 s sweep. If the sweep can't tell whether the engine accepted, the row may stay `deciding`. | KTD5, U2 | Add an engine read of the approvals document to the sweep if this shows up in e2e. |
| 8 | Output-review items and `human_in_loop` workflow nodes are recorded and decided but nothing resumes the paused workflow. | Scope, U6 | Phase 6; call it out in the PR so nobody assumes the review gates a workflow. |
| 9 | Migration number collisions when units merge out of order (`110`, `111`, `112`, `113`). | U1, U2, U6, U7 | Coordinator renumbers `down_revision` at merge. |
