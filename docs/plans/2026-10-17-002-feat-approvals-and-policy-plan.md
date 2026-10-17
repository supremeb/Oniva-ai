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
  - the `compliance_audit_logs` immutability trigger (U1) would block a path that legitimately deletes rows (tenant deletion, retention purge) and the two GUC modes in KTD9 are not enough;
  - U15 would remove a route that `frontend/lib/api/compliance.ts` still calls with `ENABLE_COMPLIANCE` on and U9 has not yet replaced it;
  - a `/internal/tools/authorize` round trip on every non-read tool call measurably slows a scripted engine run (more than about 50 ms p95 in the e2e harness), which would call for caching rather than skipping the check.
- **Execution profile:**
  - One unit per agent, in its own git worktree off `main`.
  - Units with disjoint files may run in parallel (see "Dependency order and file overlaps").
  - The coordinator verifies, merges and commits, and fixes Alembic `down_revision` if two migration units land out of order. The migration order is fixed: U1 `110`, U2 `111`, U7 `112`, U6 `113`, and **U7 is merged before U6**.
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

- **R1.** Every tool call that needs a human goes through one primitive: the engine parks the tool task on `approval`, and the Python control plane decides who may decide. The engine worker holds no policy of its own for non-read tools, except an explicit allowlist of internal native tools (KTD6).
- **R2.** Deciding an approval requires the `approvals:decide` capability for the tool's required approver role. A user outside the tenant, a viewer, or a member without the required role is refused with 403 and nothing changes in the engine. Rows needing role admin require a separate approver whenever the tenant has two or more admin-or-owner users. A tenant setting can additionally forbid self-approval everywhere.
- **R3.** An approval waits as long as its policy TTL (default 7 days, tenant-configurable, `0` for none). It survives engine worker restarts and backend restarts, and it can be decided after either. A ledger row never outlives its engine item: orphans are reconciled and closed (KTD11).
- **R4.** The review-ticket path is retired: no new `human_review_tickets` rows, `/audit/reviews*` routes and `ReviewQueueService` are removed (U15), open legacy tickets are carried into the new ledger (U6), and the UI stops calling the old routes (U15).
- **R5.** An approved `execute_python` call runs in the sandbox, once, with exactly the approved arguments, and returns its output to the model. An unapproved, changed or already-used call never reaches the sandbox, enforced in Python and not only in the engine.
- **R6.** A global "Needs attention" inbox (backend endpoint plus frontend page and nav badge) merges: tool approvals, output-review items, compliance review-queue items, Fleet failures needing an operator, Deep Work runs waiting on the user, and chat questions.
- **R7.** Rule of Two: once a run has taken in untrusted input (web pages, connector data, knowledge and uploaded-file content, and output of any MCP or native tool not on the internal allowlist), any side-effecting tool that acts outside the workspace and outside the sandbox needs approval. Subagent approvals route to the root conversation's user. Where no human can answer (an unattended Fleet run), it is blocked instead of left waiting forever. Taint is tracked in the engine and the decision is made in Python.
- **R8.** The audit log is tamper-evident: a per-tenant chain head persisted in the database and rebuilt from committed entries, signatures from a KMS-style abstraction with a local dev implementation, and a database trigger that makes `compliance_audit_logs` append-only (with one narrow, auditable exception for user deletion and two guarded purge modes). Approval requests, decisions and policy blocks are written to it, with an actor snapshot that survives user deletion.
- **R9.** The chat approval card and the question card work in production chat (`EngineChat`), show why the approval is needed, and show a clear "you cannot decide this" state for refused users.
- **R10.** A model can ask a human a question (`ask_user`) in engine chat. The question waits durably like an approval and is answered from the chat card or the inbox.

### Acceptance Examples

- **AE1 (the exit criterion).** A member asks the chat agent to run Python. The call parks on approval. The engine worker is killed and restarted, and the backend is restarted too. The approval is still pending and still in the inbox. A tenant admin approves it, the code runs once, and the model receives the output.
- **AE2.** In AE1, before the admin acts, a viewer in the same tenant, a member of another project, and a user of another tenant each try to decide it. The first two get 403 and the third 404. The engine item stays `pending` and the audit log gains no decision entry.
- **AE3.** The agent reads a web page (`browse_url`) and then calls `linear_create_issue`. The call needs approval and the card says the run read untrusted content. The same situation in an unattended run with no human has the write blocked with a readable reason, and the run does not hang. A subagent in the same chat parks its approval for the chat's user.
- **AE4.** A call that needs approval and is not decided within the TTL ends with `approval_expired`, the inbox item disappears, and the ledger row is `expired`. A run that is aborted while an approval is pending closes the row `cancelled`.
- **AE5.** After a tenant's audit entries are altered directly in the database (a field edited, a row deleted, or a row inserted mid-chain), `GET /api/v1/audit/chain/verify` names the first broken sequence number. An `UPDATE` or `DELETE` on `compliance_audit_logs` by the app role fails at the database. Deleting a user succeeds and that user's audit rows keep their actor snapshot.
- **AE6.** Two approvals are requested at the same moment for one tenant. Both entries land with consecutive sequence numbers and the chain verifies. After a backend restart, the next entry links to the last committed one.
- **AE7.** An open legacy review ticket from before this change appears in the inbox as a review item and can be decided there. After U15, `POST /api/v1/audit/reviews` no longer exists.
- **AE8.** The agent calls `ask_user`. A question card appears in chat and in the inbox. After a worker restart the question is still open, and answering it resumes the run with the answer.
- **AE9.** An admin approves `execute_python` for code A. A modified call (code B) for the same tool task is refused with `approval_arguments_mismatch`, and the approved call can run only once.

### Scope Boundaries

- **In scope:** units U1-U15.
- **Not in scope:**
  - Moving Fleet background agents and workflows onto engine tasks (roadmap Phase 6). The inbox reads Fleet failures from `background_jobs` as they are today. Workflow `human_in_loop` nodes (`backend/app/node_executors/human_in_loop_executor.py`, a stub that auto-approves) are not touched.
  - A resume hook for output-review items. They are recorded and decided, but resuming the paused workflow stays the existing `TODO`, finished with workflows in Phase 6.
  - Real cloud KMS implementations. The abstraction, the local dev implementation and a factory that fails closed in production are in scope.
  - Notifications (email, Slack) for new inbox items.

#### Deferred to Follow-Up Work

- Per-tenant editable policy rules in a settings UI. This plan reads `tenants.settings["approvals"]` and adds one owner-only write route (U3); the UI is later.
- Clearing taint mid-conversation (for example after a human reviews the content).
- An AWS KMS implementation of `KeyManagementService`.

---

## Planning Contract

### Key Technical Decisions

Naming: **the ledger** is the new Python table `tool_approvals` (U2) that indexes every approval-like item. **The engine item** is the entry in a conversation's `agent.approvals` document (`engine/src/agent/approvals.ts`), which stays the source of truth for whether a tool call runs. **Root conversation** is the top-level conversation the backend knows; `engine_conversation_id` is the conversation that made the call (may be a child).

- **KTD1 (R1, R2). Python decides policy and who may decide; the engine decides whether the call runs.**
  - Policy: the worker's `authorizeTool` (`engine/src/worker/worker.ts`, `workerHostPolicy`) calls a new `POST /internal/tools/authorize` on the backend (U3, U5). The backend answers `allow`, `block` or `requireApproval`, and a `requireApproval` answer also records a ledger row.
  - Deciding: `POST /api/v1/engine/conversations/{id}/approvals/{toolTaskId}/decision` keeps its URL and body, so the shipped `frontend/lib/engine/client.ts` `decide()` keeps working. Its authorisation moves from `_binding` to `ApprovalService.decide` (U2), which checks RBAC, then calls `EngineClient.decide` (`backend/app/services/engine_client.py`), then records the outcome.
  - The engine's decision route stays service-token only and records the principal it is given. No browser can reach it.
- **KTD2 (R1, R5). The authorize contract** (camelCase JSON, `Authorization: Bearer <ENGINE_GATEWAY_TOKEN>`, same auth as `/internal/models/*` in `backend/app/internal_api/auth.py`). Frozen here so U3 and U5 can be built in parallel against `engine/test/support/fake-gateway.ts` and `backend/tests/internal/test_contract_conformance.py`.

  Request body:

  ```
  { requestId: "<runId>:<toolCallId>",
    tenantId, projectId,
    conversationId,            // the root conversation: the only one the backend knows
    engineConversationId,      // the conversation that made the call (may be a child)
    agentRole?,                // present for subagent/worker conversations
    runId, toolCallId, toolTaskId, toolName,
    toolSource,                // "gateway" (in the manifest) | "native" (registered in the engine)
    arguments,                 // final arguments after hooks, already run through engine/src/worker/redact.ts
    argumentsHash,             // sha256 hex of the canonical form of the UNREDACTED final arguments
    sideEffect, replay,        // from the manifest (absent for native tools)
    taint: { tainted: boolean, sources: [{ kind, name, ref, at }] } }
  ```

  Canonical form: UTF-8 JSON with object keys sorted recursively, no insignificant whitespace, numbers as `JSON.stringify` emits them (the RFC 8785 subset that matters here). The engine (`engine/src/worker/args-hash.ts`) and the backend (`backend/app/services/tool_policy.py::canonical_args_hash`) share test vectors in `engine/test/support/args-hash-vectors.json`, read by both test suites.

  Responses (HTTP 200 in every decided case; 4xx/5xx are transient or fatal as for model policy):
  - `{ decision: "allow" }`
  - `{ decision: "block", code, reason }`
  - `{ decision: "requireApproval", reason, approvalId, approvalTtlMs: number | null, requiredRole: "member" | "admin" }`

  A repeated `requestId` returns the same answer and never creates a second ledger row. The engine converts `approvalTtlMs` to `expiresAt` with its own injected clock. The engine retries transient failures with the same capped backoff as model policy (`engine/src/worker/backend-policy.ts`), then **blocks** the call with `policy_unavailable`. It never falls back to a local allow.
- **KTD3 (R3). Durability is the engine's, indexing is the ledger's.**
  - The wait is the existing `waiting({ signal: "approval", until })` in `tool-task.ts`; nothing about it changes.
  - The ledger is written at authorize time (before the engine commits the item). Its lookup key is `(tenant_id, conversation_id, tool_task_id)` with `conversation_id` the ROOT conversation; `tool_task_id` is unique per root. `engine_conversation_id` is a plain non-key column. Because the engine sends `toolTaskId` in the authorize request, the decision route finds the ledger row from the URL (root id plus task id) without asking the engine. A second unique index on `(tenant_id, run_id, tool_call_id)` serves the `execute_tool` guard (KTD7) and `requestId` idempotency.
  - The engine's `expiresAt` stays authoritative: the ledger stores `expires_at` for display and inbox filtering, the lazy sweep (U7) marks overdue rows `expired`, and the reconciler (KTD11) closes whatever the sweep cannot (TTL `0`, cancelled, missing).
  - **Legacy fallback (approvals created before this change).** When no ledger row exists, the decision route falls back to the old rule (conversation owner, tenant admin, or tenant owner) only if the engine item's `requestedAt` is earlier than `settings.APPROVALS_LEGACY_CUTOFF` (an ISO timestamp the coordinator sets to the U5 deploy time; unset means the fallback is disabled). It never applies to "row missing" in general: a missing row for an item newer than the cutoff is refused 409 `approval_not_indexed` and logged. The fallback and the setting are deleted one release after U5 ships.
- **KTD4 (R2). Who may decide.** Policy lives in `backend/app/services/approval_service.py` (U2) and uses the existing RBAC in `backend/app/auth/permissions.py`:
  - New permissions: `approvals:decide` for `admin` (owner has `*`), and `approvals:decide_own` for `member`. `viewer` has neither.
  - A row carries `approver_role`, set from the tool spec: `admin` for `execute_python` and connector writes, `member` otherwise.
  - A user may decide a row when: they are in the row's tenant (else 404); they hold `approvals:decide`; or hold `approvals:decide_own` and are the row's `requested_by` and its `approver_role` is `member`.
  - **`requested_by` is the human accountable for the run.** For a subagent or worker conversation it is the ROOT conversation's owning user (the subagent's approvals route to that user, they are not blocked). For a Deep Work lead run it is the run owner, and the item appears in the owner's inbox. Where the root conversation has no owning user (an unattended Fleet run) there is no approver: see KTD6.
  - **Self-approval.** A row requiring role `admin` needs a separate approver (`decided_by != requested_by`) whenever the tenant has two or more users with role `admin` or `owner`; otherwise self-approval is allowed and the audit entry carries `self_approved: true`. The tenant setting `approvals.require_separate_approver` (default `false`) forces a separate approver for every row regardless of admin count (a tenant with a single user then cannot decide its own approvals; this is the owner's explicit choice).
  - A failed check is 403 `not_authorized` (or `self_approval_forbidden`) and writes nothing.
- **KTD5 (R2, R3). Decide protocol.** A decision is two systems (ledger and engine), so it is made retry-safe and the ledger never records anything the engine did not do:
  1. In one committed transaction, move the row `pending -> deciding` and set `decided_by`, `decision`, `note`. A second concurrent decider fails the guarded `UPDATE ... WHERE status='pending'` and gets 409.
  2. Call `EngineClient.decide`.
  3. **Definitive success:** set `approved` or `denied`, append `approval_decided`.
  4. **Any non-definitive engine outcome (HTTP 502, 504, timeout, connection error):** the row stays `deciding`. It is never reverted to `pending`, because the engine may have accepted. The caller gets 502/504. **Only the original decider (`decided_by`) may retry**; anyone else gets 409 `decision_in_progress`. The retry repeats step 2 with the same inputs, and the lazy sweep (U7) reconciles a `deciding` row older than 60 s against the engine (KTD11) instead of guessing.
  5. **Definitive engine rejection:** `expired` sets the row `expired` (410); `not_waiting` sets it `cancelled` (409); `already_decided` reads the engine-recorded principal and decision from the rejection body and writes **those** into the ledger (`decided_by`, `decision`, `status`) and into the `approval_decided` audit entry (actor looked up from the engine principal), never the retrying caller's. If the engine's principal is the caller and the decision matches, the response is 200 (the earlier attempt had succeeded); otherwise 409.
- **KTD6 (R7). Rule of Two.** The engine tracks taint, Python decides.
  - **Engine (U4).** A conversation-scoped document `agent.taint` is kept on the ROOT conversation id, so a child worker's reads taint the run that owns it. Shape: `{ tainted: boolean, total: number, sources: [{ kind: "tool_result" | "input", name, ref, at }] }`, with `sources` capped at 20 entries.
  - **Taint sources.** (1) A gateway tool whose manifest says `untrustedOutput: true`. (2) Any MCP or native engine tool **not on the internal allowlist**: its output is untrusted by default. (3) At submission, a caller-marked `untrusted` input (U4 backend: set when `SubmissionBody.content` is a list with any part whose `type` is not `"text"`; the model has no attachment field today and this is the coordinator-owned rule until one exists).
  - **Internal native allowlist** (side-effect-free, not a taint source, allowed without a round trip): `ask_user`, `update_todo`, `present_plan`, `complete_task`, and every subagent-spawn tool (flagged `internal: true` by `subagents.ts`). The names live in `INTERNAL_NATIVE_TOOLS` (`engine/src/tools/registry.ts`) and are mirrored in `backend/app/services/tool_policy.py`; a shared fixture `engine/test/support/internal-native-tools.json` is read by both test suites so the lists cannot drift. **Any other non-manifest tool is default untrusted and `require_approval`.**
  - It is set in the same commit that stores a tool result (or places the input), so a crash can't leave an untrusted result in context without the flag. It is sticky for the life of the root conversation (see Open Questions).
  - **Python (U3).** `ToolSpec` gains `untrusted_output: bool`, `scope: "sandbox" | "workspace" | "external"` and `approver_role`. The matrix, evaluated in this order:
    1. `toolSource == "gateway"` and unknown or disabled tool: `block` (`unknown_tool`).
    2. `toolSource == "native"`: on the internal allowlist, `allow`; otherwise `requireApproval` (rule `requires_approval`, reason "Tool is not on the internal allowlist"), subject to the unattended check in row 4.
    3. `requires_approval` spec: `requireApproval` (TTL from tenant policy, `approver_role` from the spec).
    4. **Unattended check** (applies to rows 2, 3 and 7): if the root conversation has no owning user (`engine_conversations.user_id IS NULL`, the coordinator-owned signal until Fleet sets an explicit origin in Phase 6), no human can answer, so `block` with code `no_approver`, or `untrusted_input_no_approver` when the cause is taint.
    5. `scope == "sandbox"` (`shell_exec`, `dw_browser_navigate`, `dw_browser_screenshot`): `allow`, even when tainted. A sandbox has no external side effects and its workspace is the run's own.
    6. Not tainted: `allow`.
    7. Tainted, `side_effect == "write"` and `scope == "external"`: `requireApproval` with reason "This run read untrusted content" and `rule_of_two` in the ledger; the approver is the root conversation's user (also for subagent and worker conversations, and the run owner for a Deep Work lead). If the unattended check in row 4 applies, `block` instead.
    8. Otherwise `allow` (reads, workspace writes).
  - Classification of today's tools (all in `backend/app/internal_api/tool_specs.py`):
    - `untrusted_output=True`: `web_search`, `browse_url`, `extract_structured_data`, `browser_navigate`, `browser_screenshot`, `dw_browser_navigate`, `dw_browser_screenshot`, `search_knowledge`, `read_file`, `parse_document`, `parse_pdf`, `linear_search_issues`, `servicenow_search_incidents`, `invoke_connector_tool`, `inspect_artifact`.
    - `scope="sandbox"`: `shell_exec`, `dw_browser_navigate`, `dw_browser_screenshot`.
    - `scope="workspace"`: `write_file`, `delete_file`, `update_todo`, `create_task`, `update_task`, `store_decision`, `browser_screenshot`, `publish_artifact`, and the other `dw_*` platform writes.
    - Everything else is `external`, which is also the default for an undeclared tool.
  - A test fails if a spec in the table lacks an explicit scope decision, the way `test_every_skill_has_a_tool_spec` does for specs today.
  - **Opt-out.** Tenant setting `approvals.rule_of_two=false` disables rows 7's approval requirement (row 3 `requires_approval` always applies). Writing it requires role `owner` and writes a `rule_of_two_changed` audit entry (U3).
- **KTD7 (R4, R5). Ticket retirement, `execute_python`, argument binding.**
  - The engine path already waits for approval before the gateway invokes the tool, so `execute_python` must not create a ticket. The skill runs the code in the sandbox through `SandboxExecutor` (`backend/app/services/sandbox_executor.py`) the way `backend/app/services/deep_work/sandbox_tools.py` does.
  - **Defence in depth and binding.** `execute_tool` (`backend/app/internal_api/execution.py`) refuses any `requires_approval` tool with HTTP 403 `approval_required` unless the ledger row for `(tenant, runId, toolCallId)` is `approved` with `decision='approve'`, then recomputes the canonical hash of the arguments in the invoke body and requires it to equal the row's `arguments_hash` (403 `approval_arguments_mismatch` otherwise). The row is **single use**: it is locked `FOR UPDATE` during execution and `consumed_at` is set on the first successful execution; a later call with a different `Idempotency-Key` is refused 403 `approval_consumed`. Same `Idempotency-Key` returns the stored outcome without running again (existing `tool_invocations` path).
  - Outside the gateway (legacy Python agent loops that call the skill directly) the skill refuses with a clear message and does not create a ticket.
  - `HumanReviewTicket` rows stay as read-only history. Open ones are copied into the ledger with `kind='review'` and `legacy_ticket_id` by the U6 migration, idempotent on `legacy_ticket_id`; legacy rows are left untouched.
  - `output_gating.py` and `wide_research.py` write ledger rows instead of tickets (U6). The old routes and `ReviewQueueService` are deleted by U15.
- **KTD8 (R6). The inbox is a read model, not a new store.** `GET /api/v1/inbox` unions typed adapters, each returning `InboxItem { id, kind, title, summary, severity, createdAt, dueAt?, source: { type, id }, actions: [...] , href }` for the current user and tenant. It does not copy rows. Adapters: `approval` (ledger, `kind='tool_call'`), `review` (ledger `kind='review'`), `question` (ledger `kind='question'`), `compliance` (`ComplianceExecution` in `manual_review`), `fleet` (`BackgroundJob` failed in the last 72 h that the user owns), `deep_work` (`DeepWorkRun` with a non-null `awaiting_input`). Visibility is the same RBAC as deciding, so a member sees their own items and an admin sees the tenant's.
- **KTD9 (R8). Audit chain.**
  - **Head.** Table `audit_chain_heads(tenant_id pk, seq bigint, head_hash text, key_id text, updated_at)`. An append locks that row (`INSERT ... ON CONFLICT DO NOTHING`, then `SELECT ... FOR UPDATE`), takes `seq + 1` and `previous_hash = head_hash`, hashes and signs the entry, inserts it with `seq`, and updates the head, all in one transaction. A unique `(tenant_id, seq)` index on `compliance_audit_logs` makes a fork impossible even if the lock is bypassed.
  - **Rebuild.** The head row is a cache. `AuditChain.rebuild_head(tenant)` recomputes it from the last committed entry; `append` calls it when the head is missing or disagrees with the last committed entry, and logs `audit_head_repaired`.
  - **Entries from before the chain** (`seq IS NULL`) are reported as `legacy_unchained` by verification, not treated as breaks.
  - **Actor snapshot.** New columns `actor_email` and `actor_name` are captured at write time from the acting user. The entry hash covers the snapshot and **does not cover `user_id`**, which is a mutable pointer. Attribution therefore survives user deletion.
  - **Signing.** A `KeyManagementService` protocol (`sign`, `verify`, `public_key_pem`, `key_id`) in `backend/app/security/kms.py`. `LocalDevKMS` is the current RSA-PSS key-pair-on-disk behaviour, refused when `settings.ENVIRONMENT` is `production` or `prod` (the check at `backend/app/config/__init__.py:409`). A factory chooses by `AUDIT_KMS_BACKEND` and fails closed in production when none is configured. The entry stores `signing_key_id` so keys can rotate.
  - **Immutability.** The table owner is the migration/owner role (`MIGRATION_DATABASE_URL`, per `backend/migrations/env.py`), **not `oniva_app`**, so `REVOKE UPDATE, DELETE, TRUNCATE ... FROM oniva_app` is effective, and the owner-only trigger functions cannot be dropped by the app role. Trigger `compliance_audit_logs_immutable`:
    - rejects `TRUNCATE` always;
    - rejects `UPDATE` except exactly one shape: `user_id` changes from non-null to `NULL` with every other column `IS NOT DISTINCT FROM` its old value. This is what the existing FK `compliance_audit_logs.user_id ... ON DELETE SET NULL` performs when a user is deleted, so user deletion succeeds;
    - rejects `DELETE` unless one of two GUC modes is set and `session_user = 'oniva_system'` (the BYPASSRLS system role; `session_user` is used because FK cascades run as the table owner):
      - `oniva.audit_purge = 'retention'`: only rows with `retention_until < now()` may be deleted;
      - `oniva.audit_purge = 'tenant_delete'`: any row whose `tenant_id` equals `current_setting('oniva.audit_purge_tenant')` may be deleted (the `ON DELETE CASCADE` from `tenants` during tenant deletion). Both settings are transaction-local (`SET LOCAL`).
    - `oniva_system` keeps `DELETE` granted for these two paths; `oniva_app` has no grant.
    This is the first stop condition: if a path needs more than these two modes, stop.
  - The existing `AuditAction` enum (`backend/app/models/compliance_audit_log.py`) is a `str` enum over a `String(50)` column, so new actions (`approval_requested`, `approval_decided`, `approval_expired`, `approval_cancelled`, `tool_policy_block`, `question_answered`, `rule_of_two_changed`) need no schema change.
- **KTD10 (R10). Questions reuse the approval machinery.** `ask_user` becomes an engine-native tool (U10) whose task parks on signal `answer`, with its own document `agent.questions` and its own decision route (U14). The ledger indexes it as `kind='question'` when Python's authorize endpoint sees `toolName == "ask_user"`, which answers `allow` (it is on the internal allowlist, but is still sent so the row is indexed). The existing Python `ask_user` skill stays `_INTERACTIVE` and unserved.
- **KTD11 (R3). Orphan reconciliation.** A ledger row can outlive its engine item (cancelled run, expiry with TTL `0`, crash between authorize and engine commit, engine item decided but ledger not finalised). `ApprovalService.reconcile(tenant)` compares `pending` and `deciding` rows against the engine approvals document (read through `EngineClient.approvals`, served by the engine read route added in U4) and:
  - closes rows whose engine item is `expired` (row `expired`) or `cancelled` (row `cancelled`), or is **missing** and the row is older than a 120 s grace window (row `cancelled`, to avoid racing the authorize-then-commit gap), or whose conversation no longer exists;
  - finalises rows whose engine item is `approved` or `denied` from the engine-recorded principal and decision (same rule as `already_decided`, KTD5);
  - never changes a row when the engine is unreachable.

  Callers: the lazy sweep in `ApprovalService.expire_overdue` (U7, which now also reconciles `deciding` rows older than 60 s), and every inbox read (U7, rate-limited to once per tenant per 30 s and capped at 20 engine conversations per pass; the rest are caught by the next pass). TTL `0` rows have no `expires_at`, so reconciliation is the only thing that closes them.

  `mark_closed(tenant, scope, reason)` closes pending/deciding rows without calling the engine. Its callers: the engine `abort` route and the `cancel_submission` route in `engine_conversations.py` (U2, scoped to the conversation's run), Deep Work run terminal transitions (complete, cancel, fail) in `deep_work_state.py` (U7, scoped to the run), and the reconciler itself.

### Assumptions

- `tenants.settings["approvals"]` is the home for policy knobs (`ttl_hours`, `require_separate_approver`, `rule_of_two`), next to `tenants.settings["features"]`. Defaults apply when it is absent. They are written through `backend/app/api/v1/tenant_settings.py` (U3).
- `ComplianceExecution` review decisions (`POST /api/v1/compliance/review-queue/{execution_id}/review`) stay where they are; the inbox links to them and does not re-implement them.
- Fleet has no "paused awaiting a human" state today, so the Fleet inbox source is failed runs only (see Open Questions).
- Deep Work's "question" is the intake clarification returned by `clarify()` in `backend/app/api/v1/deep_work_engine.py` (`IntakeResult.action != "proceed"`). It is held only by the browser today, so U7 persists it on `deep_work_runs`.
- The engine view's `approvals` member (`engine/src/agent/harness.ts` `approvals()`, frontend `ApprovalsState` in `frontend/lib/engine/types.ts`) keeps its shape. A `questions` member is added alongside it (U14) and parsed as empty when absent.
- The decision route keeps addressing the engine by the conversation id it receives (the root); `engine_conversation_id` is used for reads of the approvals document. U4 adds a test that child-conversation items are reachable through the root (see Open Questions 5).
- The migration chain builds from an empty database (`100_baseline` squash; `backend/tests/postgres/test_migrations.py::test_upgrade_head_on_empty_db` runs in CI with pgvector; `DRIFT.md` is historical). New migrations chain after the current head `109_llm_usage_agent_role`, are idempotent, and the single-head assertions in `test_migrations.py` are updated. **Order is fixed: U1 `110`, U2 `111`, U7 `112`, U6 `113`; U7 merges before U6.**

---

## Dependency order and file overlaps

```
U1 audit chain ─────┐
                    ├──► U2 ledger + decide ──┬──► U3 authorize endpoint ──┬──► U5 engine worker hook ──┬──► U13 e2e
U4 engine taint ────┘          │              │                            │          ▲                 │
   (+ approvals read route,    │              │                            │          │                 │
    submit untrusted)          │              ├──► U7 inbox backend ───────┼──► U6 ────┴────────────────►│
                               │              │        │                   │   retire tickets +         │
                               │              │        └──► U9 inbox page ◄┼── U8 chat card (U2)        │
                               │              │                            │   execute_python (U2,U3,U7)│
                               │              └──► U8 chat approval card   │                            │
                               │                                           │                            │
U4 ──► U10 engine questions core ──► U14 engine routes/watch/worker (also after U5) ──► U11 backend ──► U12 card
U6 + U9 ──► U15 cleanup (delete /audit/reviews*, frontend callers)
```

Edges (dependency lists, authoritative):

| Unit | Depends on |
|---|---|
| U1 | none |
| U4 | none |
| U2 | U1, U4 |
| U3 | U2 |
| U7 | U2 |
| U8 | U2 |
| U10 | U4 |
| U5 | U3, U4 |
| U6 | U2, U3, U7 (U7 merged first: migration order) |
| U9 | U7, U8 |
| U14 | U5, U10 |
| U11 | U2, U7, U14 |
| U12 | U8, U11, U14 |
| U13 | U5, U6, U7 |
| U15 | U6, U9 |

Parallel groups:
- **Group A (start together):** U1, U4. Disjoint.
- **Group B (after U1, U4):** U2; U10 may run alongside U2 (after U4 only).
- **Group C (after U2):** U3, U7, U8. U3 and U7 share no files; both extend `approval_service.py` callers only, not the file.
- **Group D:** U5 (after U3, U4); U6 (after U2, U3 and U7 merged); U9 (after U7, U8).
- **Group E:** U14 (after U5, U10); U13 (after U5, U6, U7); U15 (after U6, U9).
- **Group F:** U11 (after U2, U7, U14).
- **Group G:** U12 (after U8, U11, U14). U13 and U15 may finish anywhere after their dependencies; U15 is the last backend-visible change and nothing depends on it.

File overlaps to schedule around, regenerated from every unit's Files list (serialize units that share a file, in the merge order shown; never run them in parallel):

| File | Units, in merge order |
|---|---|
| `backend/app/api/v1/engine_conversations.py` | U4 (submit marks `untrusted`), U2 (decide, abort and cancel hooks), U11 (answer route) |
| `backend/app/services/engine_client.py` | U2 (`approvals` read), U11 (`answer`, `questions` read) |
| `backend/app/services/approval_service.py` | U2, U7 (reconcile, sweep), U6 (consume and execute guard lookup), U11 (question mode) |
| `backend/app/services/inbox_service.py` | U7, U11 |
| `backend/app/models/__init__.py` | U1 (`AuditChainHead`), U2 (`ToolApproval`) |
| `backend/app/models/compliance_audit_log.py`, `backend/app/services/audit_logger.py` | U1 only |
| `backend/app/api/v1/audit.py` | U1 (`chain/verify`), then U15 (delete `/reviews*`) |
| `backend/app/auth/permissions.py` | U2 only |
| `backend/app/internal_api/tool_specs.py`, `tools.py`, `router.py` | U3 only |
| `backend/app/services/tool_policy.py` | U3 only (U6 imports `canonical_args_hash`) |
| `backend/app/api/v1/tenant_settings.py` | U3 only |
| `backend/app/internal_api/execution.py` | U6 only |
| `backend/app/main.py` | U7 (mount inbox) |
| `backend/app/models/deep_work_run.py`, `app/services/deep_work_state.py`, `app/api/v1/deep_work_engine.py` | U7 only |
| `backend/app/services/engine_chat_agent.py` | U11 only |
| `backend/app/api/v1/sandbox.py`, `app/services/output_gating.py`, `app/api/v1/wide_research.py`, `app/skills/execute_python/tool.py` | U6 only |
| `backend/app/services/review_queue.py`, `app/api/v1/compliance.py` (review_queue references) | U15 only |
| `backend/app/config/__init__.py` | U1 (`AUDIT_KMS_BACKEND`), U2 (`APPROVALS_LEGACY_CUTOFF`) |
| `backend/migrations/versions/` | U1 `110_`, U2 `111_`, U7 `112_`, U6 `113_` (U7 merged before U6) |
| `backend/tests/postgres/test_migrations.py` | U1, U2, U7, U6 (single-head assertion, in that order) |
| `backend/tests/internal/test_engine_conversations.py`, `test_contract_conformance.py` | U2, then U3 (conformance) |
| `backend/tests/test_inbox_service.py` | U7, U11 |
| `backend/tests/e2e/harness.py`, `backend_app.py`, `dw_fakes.py` | U13 only |
| `engine/src/agent/tool-task.ts` | U4, then U10 |
| `engine/src/agent/policy.ts` | U4 (request fields), then U5 |
| `engine/src/agent/approvals.ts` | U4 (rejection body carries the item) |
| `engine/src/tools/registry.ts` | U4 (`untrustedOutput`, `internal`, `INTERNAL_NATIVE_TOOLS`), then U10 (`humanInput`) |
| `engine/src/agent/subagents.ts` | U4 (flag spawn tools `internal`) |
| `engine/src/server/routes.ts` | U4 (submit `untrusted`, approvals read route), then U14 (answer route, questions read route) |
| `engine/test/server/http.test.ts` | U4, then U14 |
| `engine/src/agent/harness.ts` | U10 only |
| `engine/src/watch/view.ts`, `patch.ts`, `engine/test/watch/` | U14 only |
| `engine/src/worker/worker.ts` | U5 (delegate policy), then U14 (register `ask_user`) |
| `engine/src/worker/backend-policy.ts`, `config.ts` | U5 only |
| `engine/src/index.ts` | U4, then U10 |
| `engine/README.md` | U4, U5, U10, U14 (each edits its own section; merge in order) |
| `engine/test/support/fake-gateway.ts` | U5 (authorize clauses; U3 conformance reads it only) |
| `frontend/lib/engine/types.ts` | U12 only |
| `frontend/lib/engine/viewToMessages.ts` | U12 only (U8 needs no change: `reason` already flows to `description`) |
| `frontend/lib/engine/approval.ts` | U8 only |
| `frontend/lib/engine/client.ts`, `useEngineConversation.ts` | U12 only |
| `frontend/components/chat/EngineChat.tsx` | U8, then U12 |
| `frontend/components/chat/shared/interactive/ApprovalCard.tsx` | U8 only |
| `frontend/components/inbox/InboxItemRow.tsx`, `InboxList.tsx` | U9, then U12 |
| `frontend/components/layout/Sidebar.tsx`, `frontend/lib/api/index.ts` | U9 only |
| `frontend/lib/api/compliance.ts`, `frontend/components/compliance/ReviewTicketTable.tsx`, `frontend/app/(dashboard)/compliance/page.tsx`, `frontend/lib/api/sandbox.ts` | U15 only |
| `frontend/e2e/` | U13 only |
| `DEPLOYMENT.md` | U13 only |

---

## Implementation Units

### U1. Tamper-evident audit chain

**Goal:** a per-tenant, signed, append-only chain that survives restarts, survives user deletion, and that anyone can verify.
**Requirements:** R8, AE5, AE6.
**Dependencies:** none.

**Files:**
- `backend/app/security/kms.py` (new: `KeyManagementService` protocol, `LocalDevKMS`, `get_kms()`)
- `backend/app/services/audit_chain.py` (new: `AuditChain.append`, `rebuild_head`, `verify_chain`)
- `backend/app/services/audit_logger.py` (route `ComplianceAuditLogger.log_agent_decision` through `AuditChain`; delete the in-memory `_last_entry_hash` and the db-swapping singleton behaviour of `get_audit_logger`; keep `log_decision` as a thin caller; remove `AuditKeyManager`, imported nowhere else)
- `backend/app/models/compliance_audit_log.py` (add `seq`, `signing_key_id`, `actor_email`, `actor_name` columns and the new `AuditAction` members; add `AuditChainHead` model)
- `backend/app/models/__init__.py` (export `AuditChainHead`)
- `backend/app/api/v1/audit.py` (`GET /audit/chain/verify`; switch `/compliance/logs/verify` to use the KMS verifier)
- `backend/migrations/versions/110_audit_chain.py` (new: columns, `audit_chain_heads` with tenant RLS like `103_chat_read_model.py`, unique `(tenant_id, seq)`, immutability trigger functions with the KTD9 UPDATE shape and two GUC modes, `REVOKE UPDATE, DELETE, TRUNCATE ... FROM oniva_app`, explicit `GRANT DELETE ... TO oniva_system`)
- `backend/app/config/__init__.py` (`AUDIT_KMS_BACKEND`, default `local`)
- `backend/tests/postgres/test_migrations.py` (single-head assertion)
- `backend/tests/test_audit_chain.py` (new)
- `backend/tests/test_kms.py` (new)
- `backend/tests/postgres/test_audit_immutability.py` (new)

**Approach:**
- Follow KTD9. The entry hash covers every signable field plus `seq`, `previous_hash`, `signing_key_id`, `actor_email` and `actor_name`, and excludes `user_id`, serialised with sorted keys as `_compute_entry_hash` does today. Callers pass the acting user so the snapshot is filled at write.
- `verify_chain(tenant)` streams entries by `seq`, recomputes each hash, checks `previous_hash` against the prior entry, verifies each signature through the KMS (by `signing_key_id`), and returns `{ ok, checked, legacy_unchained, first_break: { seq, reason } | null }`.
- Existing rows keep `seq NULL` and are never rewritten (the trigger forbids it anyway).
- `LocalDevKMS` reuses the key generation and PSS signing now in `AuditKeyManager`; the only importer is `audit_logger.py`, so `AuditKeyManager` is removed in this unit.
- The trigger functions, both `oniva.audit_purge` modes and the owner-role requirement are documented in the migration docstring. `DEPLOYMENT.md` is written by U13 only.
- The migration asserts at upgrade time that the table owner is not `oniva_app`.

**Patterns to follow:** RLS statements in `backend/migrations/versions/103_chat_read_model.py`; DB-level tests in `backend/tests/postgres/test_engine_tables.py` and `test_rls.py`.

**Test scenarios** (`backend/tests/test_audit_chain.py`, using the `AsyncSession` fixtures the nearby tests use):
- Three sequential appends for one tenant get `seq` 1, 2, 3 and each `previous_hash` equals the prior `entry_hash`.
- Two tenants append interleaved; each has its own `seq` starting at 1 and its own head.
- Concurrent appends (an `asyncio.gather` of 20) for one tenant yield 20 distinct consecutive `seq` values and `verify_chain` is ok. (Postgres-only variant lives in `tests/postgres`; the unit variant uses the same lock path on the test database.)
- A fresh logger instance after the first appends (simulating a restart) links to the last committed entry; the head is not reset to `None`.
- Deleting the head row and appending rebuilds it from the last committed entry and logs `audit_head_repaired`.
- Head row pointing at the wrong hash: append repairs it and the chain still verifies.
- The actor snapshot (`actor_email`, `actor_name`) is stored at write and is covered by the hash: editing it makes `verify_chain` fail; setting `user_id` to `NULL` does not.
- Tampering cases through the ORM with the trigger disabled in the test fixture: edited `decision`, edited `rationale`, removed middle row, inserted row with a duplicate `seq` (unique violation), and a forged signature each make `verify_chain` return `ok: false` with the right `first_break.seq` and reason.
- Legacy rows (`seq NULL`) are counted as `legacy_unchained` and do not fail verification.

`backend/tests/test_kms.py`:
- `LocalDevKMS` signs and verifies; a flipped byte fails; a different key id fails.
- `get_kms()` returns `LocalDevKMS` in development and raises in `ENVIRONMENT=production` when `AUDIT_KMS_BACKEND` is `local` or unset.
- Key rotation: an entry signed with `key_id=a` still verifies after the active key becomes `b`.

`backend/tests/postgres/test_audit_immutability.py` (connecting as the real roles):
- As `oniva_app`, `UPDATE` and `DELETE` on `compliance_audit_logs` fail (privilege and trigger); `INSERT` works. The table owner is the migration role, not `oniva_app`.
- `TRUNCATE` fails for every role, including with a GUC set.
- **Delete a user:** deleting a user who has audit rows succeeds (the FK `SET NULL` update is the one allowed UPDATE shape), `user_id` becomes `NULL`, and `actor_email`/`actor_name` and the chain verification are unchanged.
- Any other UPDATE, including one that sets `user_id` to `NULL` and also changes another column, or sets `user_id` from `NULL` to a value, fails.
- **`retention` mode:** as `oniva_system` with `oniva.audit_purge='retention'`, `DELETE` of a row not yet past `retention_until` fails; past retention it succeeds. The same GUC as `oniva_app` or without `session_user = oniva_system` fails.
- **`tenant_delete` mode:** deleting a tenant (cascade) fails without the GUC; with `oniva.audit_purge='tenant_delete'` and `oniva.audit_purge_tenant=<that tenant>` as `oniva_system` it succeeds and removes only that tenant's rows; the same GUC naming a different tenant fails; as `oniva_app` it fails.

**Verification:** backend ruff and pytest are green; `tests/postgres` file passes against a local Postgres.

### U2. Approval ledger and RBAC decide

**Goal:** a Python-owned index of every approval-like item, and a decision path only authorised users can use.
**Requirements:** R1, R2, R3, AE2, AE4, AE9 (binding columns).
**Dependencies:** U1 (audit entries), U4 (engine approvals read route and rejection body).

**Files:**
- `backend/app/models/tool_approval.py` (new: `ToolApproval`)
- `backend/app/models/__init__.py` (export `ToolApproval`)
- `backend/migrations/versions/111_tool_approvals.py` (new: table, tenant RLS, indexes; unique `(tenant_id, conversation_id, tool_task_id)` where `tool_task_id` is not null; unique `(tenant_id, run_id, tool_call_id)` where both are not null)
- `backend/app/services/approval_service.py` (new: `record_pending`, `can_decide`, `decide`, `expire_overdue`, `mark_closed`, `finalize_from_engine`)
- `backend/app/auth/permissions.py` (add `approvals:decide`, `approvals:decide_own`)
- `backend/app/api/v1/engine_conversations.py` (`decide_approval` calls `ApprovalService.decide`; `abort` and `cancel_submission` call `mark_closed`; `_binding` stays for the other routes)
- `backend/app/services/engine_client.py` (add `approvals(...)`: reads the conversation's approvals document through the U4 route)
- `backend/app/config/__init__.py` (`APPROVALS_LEGACY_CUTOFF`, optional ISO timestamp, default unset)
- `backend/tests/postgres/test_migrations.py` (single-head assertion)
- `backend/tests/test_approval_service.py` (new)
- `backend/tests/test_engine_approval_decision.py` (new)
- `backend/tests/postgres/test_tool_approvals_rls.py` (new)
- `backend/tests/internal/test_engine_conversations.py` (extend the existing decide tests)

**Approach:**
- Columns: `id`, `tenant_id`, `kind` (`tool_call | review | question`), `status` (`pending | deciding | approved | denied | answered | expired | cancelled`), `project_id`, `conversation_id` (root), `engine_conversation_id` (non-key), `run_id`, `tool_call_id`, `tool_task_id`, `tool_name`, `arguments_preview` (JSONB, scrubbed), `arguments_hash`, `reason`, `rule` (`requires_approval | rule_of_two | review | question`), `approver_role`, `requested_by`, `decided_by`, `decision`, `note`, `self_approved`, `taint_sources` (JSONB), `expires_at`, `decided_at`, `consumed_at`, `legacy_ticket_id`, `created_at`, `updated_at`.
- `record_pending` is an upsert keyed on `(tenant, conversation_id, tool_task_id)` (and the run/call index); a repeat returns the existing row (idempotent for KTD2).
- `decide` implements KTD5 exactly, including: a row stays `deciding` on 502/504/timeout; only the original decider retries; `already_decided` finalises with the engine-recorded principal and decision, never the caller's. It writes `approval_decided` through `AuditChain` with actor id, actor role, tool name and `self_approved`.
- `can_decide` implements KTD4 including the admin-or-owner count (active users with role `admin` or `owner` in the tenant, counted at decision time).
- `mark_closed(tenant, conversation_id | run_id, reason)` closes pending and deciding rows without an engine call and writes `approval_cancelled`; `abort` and `cancel_submission` call it after the engine accepts the abort (callers per KTD11).
- Scrub arguments with the helper `execution.py` already uses (`_scan_credentials`), and also drop keys matching the pattern in `frontend/lib/engine/approval.ts` (`SENSITIVE_KEY`).
- Errors map: unknown or other-tenant row 404, failed RBAC 403, `deciding` or decided by someone else 409, expired 410.
- The route keeps today's request and response (`DecisionBody`, engine approval JSON) so `frontend/lib/engine/client.ts` is unchanged.
- **Legacy fallback (KTD3):** no ledger row and the engine item's `requestedAt` is before `APPROVALS_LEGACY_CUTOFF`: old rule (conversation owner, tenant admin, or tenant owner). Otherwise 409 `approval_not_indexed`. Record the removal plan in the route docstring.

**Test scenarios** (`backend/tests/test_approval_service.py`):
- `can_decide` matrix: viewer no; member who is the requester on a `member` row yes; member who is not the requester no; member on an `admin` row no; admin yes; **owner yes**; user of another tenant 404.
- Admin row, tenant with two admin-or-owner users, admin is the requester: `self_approval_forbidden`. Same tenant with exactly one admin-or-owner user: allowed, audit entry has `self_approved: true`. An owner counts toward the two.
- `require_separate_approver=true`: self-decision on any row is `self_approval_forbidden`.
- `record_pending` twice with the same key returns one row; a different `engine_conversation_id` with the same root and task id is the same row.
- `decide` approve: row goes `pending -> deciding -> approved`, the engine client is called once with `principal=str(user.id)`, one `approval_decided` audit entry is written.
- Engine raises `approval_rejected(already_decided)` with principal P and decision D: the ledger row and the audit entry carry P and D, not the retrying caller's; 409 (200 when P is the caller and D matches); no second audit entry.
- Engine unreachable (502, 504, timeout): row stays `deciding`, response 502/504, no audit entry; a retry by the same user repeats the engine call and finalises; a retry by another user (even an admin) gets 409 `decision_in_progress`.
- Two concurrent deciders: exactly one succeeds, one gets 409.
- `expire_overdue` marks rows past `expires_at` as `expired` and writes `approval_expired`.
- `mark_closed` by conversation and by run closes pending and deciding rows, writes `approval_cancelled`, leaves decided rows alone.

`backend/tests/test_engine_approval_decision.py` (HTTP, via the app test client with a fake `EngineClient` transport):
- AE2: viewer 403, member of another project 403, other tenant 404, and in all three the fake engine records zero decide calls.
- Authorised admin 200; the response is the engine approval.
- No ledger row, engine item `requestedAt` before the cutoff: owner-or-admin-or-owner fallback works and a plain member who is not the owner is refused. No ledger row and item after the cutoff, or cutoff unset: 409 `approval_not_indexed`.
- The `abort` route closes that conversation's pending rows `cancelled`.

`backend/tests/postgres/test_tool_approvals_rls.py`: a tenant cannot read another tenant's rows; the unique keys reject a duplicate.

**Verification:** backend ruff and pytest green.

### U3. Tool authorize endpoint and Rule of Two

**Goal:** the Python side of KTD2, KTD6 and the argument-hash binding.
**Requirements:** R1, R5 (hash stored), R7, R10 (question indexing), AE3.
**Dependencies:** U2.

**Files:**
- `backend/app/internal_api/tool_specs.py` (add `untrusted_output`, `scope`, `approver_role` to `ToolSpec`; classify every entry per KTD6)
- `backend/app/internal_api/tools.py` (`ManifestTool.untrustedOutput`, `build_manifest`)
- `backend/app/internal_api/authorize.py` (new: `POST /tools/authorize` handler)
- `backend/app/internal_api/router.py` (mount it next to the model and tool routers)
- `backend/app/services/tool_policy.py` (new: pure `decide_tool_call(spec, request, tenant_policy, attended)`, `NATIVE_INTERNAL_TOOLS`, `canonical_args_hash`)
- `backend/app/api/v1/tenant_settings.py` (new `PUT` route for `approvals` settings; role `owner` is required to change `rule_of_two`, and the change writes a `rule_of_two_changed` audit entry through `AuditChain`)
- `backend/tests/internal/test_tool_authorize.py` (new)
- `backend/tests/internal/test_manifest.py` (extend)
- `backend/tests/internal/test_contract_conformance.py` (authorize clause)
- `backend/tests/test_tool_policy.py` (new, pure unit tests)
- `backend/tests/test_tenant_approval_settings.py` (new)

(The submit-side `untrusted` marking is in U4.)

**Approach:**
- `decide_tool_call` is pure and implements the KTD6 matrix in order. The handler loads the tenant's `approvals` settings, derives `attended` from the root conversation (`engine_conversations.user_id IS NOT NULL`), calls it, and on `requireApproval` calls `ApprovalService.record_pending` with `requested_by` set to the root conversation's owning user (KTD4) and `arguments_hash` taken from the request (and writes `approval_requested`, or `tool_policy_block` on a block, through `AuditChain`).
- TTL: `tenant.settings["approvals"]["ttl_hours"]`, default 168, `0` means `approvalTtlMs: null`.
- `ask_user` returns `allow` and records a `kind='question'` row (KTD10). `ask_user` stays `in_manifest=False`: the engine registers it natively (U10), so it is not in the gateway manifest.
- Idempotent per `requestId`, backed by the ledger's unique run/call key. A `block` is deterministic, so it is recomputed, not stored.
- Auth and tenancy: same dependency as `/internal/models/policy`; the handler never trusts `tenantId` from a conversation it cannot match against `engine_conversations` (use the same lookup `tools.py` uses for invoke binding).
- Add `untrustedOutput` to the manifest as optional. The engine treats absence as `false` for gateway tools so old backends keep working.
- `canonical_args_hash` passes the shared vectors in `engine/test/support/args-hash-vectors.json`.

**Test scenarios** (`backend/tests/test_tool_policy.py`):
- Read tool, not tainted: `allow`. Read tool, tainted: `allow`.
- `requires_approval` tool: `requireApproval` with the spec's `approver_role` (`execute_python` is `admin`, `delete_file` is `member`).
- Tainted + `write` + `external` + top-level attended conversation: `requireApproval`, rule `rule_of_two`.
- Same with `agentRole="research_worker"` in an attended conversation: `requireApproval` (not block), ledger `requested_by` is the root conversation's user.
- Same with a root conversation that has no owning user: `block`, code `untrusted_input_no_approver`. A `requires_approval` tool in that conversation: `block`, code `no_approver`.
- Tainted + `scope="sandbox"` (`shell_exec`, `dw_browser_navigate`) in a worker conversation: `allow`.
- Tainted + `write` + `workspace` (`write_file`): `allow`.
- Not tainted + external write that does not require approval: `allow`.
- Native tool on the internal allowlist (`update_todo`, a spawn tool): `allow`. Native tool not on the list: `requireApproval`.
- Gateway tool name unknown: `block` (`unknown_tool`).
- Tenant `approvals.rule_of_two=false`: tainted external write is `allow`, `requires_approval` still applies.
- Exhaustiveness: every entry in `TOOL_SPECS` has an explicit `scope` and `untrusted_output` decision.
- The Python `NATIVE_INTERNAL_TOOLS` equals `engine/test/support/internal-native-tools.json`.
- `canonical_args_hash` matches every shared vector (key order, nested objects, unicode, numbers).

`backend/tests/internal/test_tool_authorize.py`:
- Missing or bad bearer: 401. Tool call for a conversation of another tenant: 404.
- `requireApproval` creates exactly one ledger row, with scrubbed arguments and the request's `argumentsHash`, even when posted twice with the same `requestId`.
- `ask_user` returns `allow` and creates a `question` row.
- Manifest includes `untrustedOutput: true` for `browse_url` and `false` for `write_file`.
- Contract clause: the response shapes match the `fake-gateway.ts` clause tags.

`backend/tests/test_tenant_approval_settings.py`: an admin may change `ttl_hours` and `require_separate_approver`; changing `rule_of_two` as admin is 403, as owner succeeds and writes one `rule_of_two_changed` audit entry with the actor snapshot.

**Verification:** backend ruff and pytest green.

### U4. Engine taint tracking, approvals read route, submit `untrusted`

**Goal:** the engine remembers, durably, that a run has read untrusted content and tells the host; the backend can read an approvals document; submit can mark an input untrusted.
**Requirements:** R7, R3 (read route and rejection body for U2).
**Dependencies:** none.

**Files:**
- `engine/src/agent/taint.ts` (new: `taintDoc`, `markTainted(tx, rootId, source)`, `taintOf(session, rootId)`)
- `engine/src/tools/registry.ts` (add `untrustedOutput?: boolean`, `internal?: boolean` to the tool definition; export `INTERNAL_NATIVE_TOOLS`)
- `engine/src/agent/subagents.ts` (flag spawn tools `internal: true`)
- `engine/src/tools/gateway.ts` (parse optional manifest `untrustedOutput`, set it on the gateway tool)
- `engine/src/agent/tool-task.ts` (mark taint in the commit that stores a tool result when the tool is `untrustedOutput`, or is a non-gateway tool that is not `internal`; pass `toolTaskId`, `toolSource` and `taint` into the authorize request)
- `engine/src/agent/policy.ts` (`ToolAuthorizationRequest` gains `toolTaskId: string`, `toolSource: "gateway" | "native"` and `taint: { tainted: boolean; total: number; sources: TaintSource[] }`)
- `engine/src/agent/approvals.ts` (the `already_decided`, `expired` and `not_waiting` rejection carries the item snapshot so the backend can read the recorded principal and decision)
- `engine/src/agent/submission.ts`, `engine/src/agent/inbox.ts` (an `untrusted` boolean marks `kind: "input"` taint in the same commit that places the input)
- `engine/src/server/routes.ts` (submit accepts `untrusted`; new `GET /v1/conversations/:id/approvals` returns the conversation's approvals document, tenant-scoped, service-token only)
- `engine/src/index.ts` (export types)
- `engine/README.md` (document the document kind, the manifest field, the submit field, the read route, the rejection body and the authorize request fields)
- `engine/test/support/internal-native-tools.json` (new: the allowlist fixture)
- `engine/test/agent/taint-suite.ts`, `engine/test/agent/taint.memory.test.ts`, `engine/test/agent/taint.postgres.test.ts` (new, same shape as `approvals-suite.ts`)
- `engine/test/tools/gateway.test.ts` (manifest parsing)
- `engine/test/server/http.test.ts` (submit `untrusted`, approvals read route, rejection body)
- `backend/app/api/v1/engine_conversations.py` (submit sets `untrusted: true` on the payload sent to the engine when `SubmissionBody.content` is a list containing any part whose `type` is not `"text"`; this is the whole rule, there is no attachment field)
- `backend/tests/internal/test_engine_conversations.py` (submit marks `untrusted`; extend)

**Approach:**
- The document is on the root conversation id (`ToolConversation.rootId ?? id`; `tool-task.ts` already computes `rootId` at line ~172). A child conversation's read therefore taints the run that owns it.
- Cap `sources` at 20 and keep counting in `total`. `sources[].ref` is the tool call id or submission id.
- `authorize` still receives a `structuredClone` of the request (`policy.ts`), so `HostPolicy` cannot mutate taint.
- A tool result that is an error still taints if the tool declares `untrustedOutput` (it may contain page text). A non-gateway tool not flagged `internal` taints by default.
- The approvals read route and decision route must reach an item held by a child conversation through the root (test below); if the engine rejects child ids, the route resolves `:toolTaskId` across the root's tree.
- Backend submit change is small and merges first on `engine_conversations.py` (before U2).
- Clean-room: write from the README contract and this plan only.

**Test scenarios** (`engine/test/agent/taint-suite.ts`, run on memory and Postgres backends):
- A run with a read tool that is `untrustedOutput` marks taint; the next tool's authorize request carries `tainted: true` and the source.
- A tool that is not `untrustedOutput` does not mark. A native tool not flagged `internal` marks; an `internal` one (`ask_user`, a spawn tool) does not.
- The mark and the stored tool result commit together: a crash injected between result and mark is impossible (the document is read after recovery and agrees with the entries).
- A subagent child's untrusted read taints the root; the parent's next authorize sees it.
- A submit with `untrusted: true` taints with `kind: "input"` before the first model request.
- More than 20 sources: `sources.length === 20`, `total` keeps counting.
- Taint survives a worker restart (reopen the harness on the same storage).
- The authorize request received by a recording `HostPolicy` includes `toolTaskId` equal to the task id that appears later in `harness.approvals(...)`, and `toolSource`.
- Gateway manifest with `untrustedOutput: "yes"` (wrong type) is rejected like other malformed fields; absent means `false`.
- HTTP submit with a non-boolean `untrusted` is 400 and nothing is committed.
- `GET /v1/conversations/:id/approvals` returns items, 404 for an unknown conversation and for a conversation of another tenant, and finds a child conversation's item through the root.
- `already_decided` rejection body includes the item with its recorded decision and principal.
- `INTERNAL_NATIVE_TOOLS` equals the JSON fixture.

Backend (`test_engine_conversations.py`): a submit whose `content` list has an `image` or `file` part carries `untrusted: true` to the fake engine; text-only content and plain `text` do not.

**Verification:** engine `npm run lint && npm run typecheck && npx vitest run` green; backend ruff and pytest green.

### U5. Engine worker delegates tool policy to the backend

**Goal:** the worker holds no tool policy of its own beyond the internal allowlist; Python decides; approvals use the policy TTL.
**Requirements:** R1, R3, R5 (argument hash), AE1, AE4.
**Dependencies:** U3 (endpoint), U4 (request fields).

**Files:**
- `engine/src/worker/backend-policy.ts` (`BackendPolicyClient.authorizeTool`, request and response validation, retry and fail-closed)
- `engine/src/worker/args-hash.ts` (new: canonical form and sha256 of the unredacted final arguments)
- `engine/src/worker/worker.ts` (`workerHostPolicy`: use the backend for every tool call that is not a manifest read and not an `INTERNAL_NATIVE_TOOLS` tool; keep the existing local rule only when no backend is configured, i.e. local dev with `ENGINE_POLICY` not `backend`)
- `engine/src/worker/config.ts` (`ENGINE_APPROVAL_TTL_MS` becomes the local-mode value only; document it)
- `engine/src/worker/redact.ts` (reuse for arguments sent to the backend; no change expected)
- `engine/test/support/fake-gateway.ts` (add `/internal/tools/authorize` with `[clause:]` tags for allow, block, requireApproval and repeat)
- `engine/test/support/args-hash-vectors.json` (new: shared canonical-hash vectors, also read by backend tests)
- `engine/test/worker/backend-policy.test.ts` (extend)
- `engine/test/worker/args-hash.test.ts` (new)
- `engine/test/worker/worker.test.ts` (extend)
- `engine/test/worker/config.test.ts`
- `engine/README.md` (HostPolicy, env table, policy section)

**Approach:**
- `authorizeTool(request)` for a gateway tool whose manifest `sideEffect === "none"`: allow locally with no round trip (the manifest is Python's own statement). A native tool on `INTERNAL_NATIVE_TOOLS` also allows locally, except `ask_user`, which is sent so the question is indexed. Everything else sends the KTD2 body, with arguments redacted by `redact.ts` and `argumentsHash` computed over the unredacted final arguments.
- On `requireApproval` build `{ decision: "requireApproval", reason, expiresAt: approvalTtlMs === null ? undefined : clock.now() + approvalTtlMs }`. The reason text is whatever the backend sent.
- Transient failures retry on the shared `RetryOptions` budget with the same `requestId`. After the budget, return `{ decision: "block", reason: "policy_unavailable" }`. A 401/403 or malformed body also blocks, immediately, without retry.
- Never log arguments or response bodies (same rule as model policy).

**Test scenarios:**
- Manifest read: no HTTP call is made; result `allow`. Internal native tool (`update_todo`): no HTTP call. `ask_user`: one call.
- Non-allowlisted native tool: the request has `toolSource: "native"` and the backend answer is honoured.
- Write tool, backend `allow`: the tool runs once.
- Backend `requireApproval` with `approvalTtlMs: 604800000`: the engine item's `expiresAt` equals `clock.now() + 604800000` (use the injected clock), and the tool task is `waiting` on `approval`.
- Backend `requireApproval` with `approvalTtlMs: null`: `expiresAt` is `null` and the item never expires.
- Backend `block`: the tool result is `blocked` and carries the backend reason.
- Backend down for longer than the retry budget: the call is `blocked` with `policy_unavailable`; no tool ran; no local fallback.
- Backend 5xx twice then `allow`: one effective call, `policy_retry` logged, same `requestId` on every attempt.
- Malformed body or a 403: immediate block, no retry.
- Restart case: harness reopened mid-wait does not call authorize again for a task already waiting (the item exists), verified by the fake gateway's call count.
- The request body never contains a key matching the redaction patterns (inject one and assert it is scrubbed) while `argumentsHash` still equals the hash of the unredacted arguments.
- `args-hash` matches every shared vector.
- Local mode (no backend configured) keeps today's `requiresApproval` behaviour so existing worker tests pass.

**Verification:** engine lint, typecheck and vitest green; clean-room grep empty.

### U6. `execute_python` runs after approval; execute_tool guard; ledger writes; ticket data migration

**Goal:** code execution works after approval, once, with the approved arguments, and never before; nothing creates a ticket any more; open tickets move into the ledger.
**Requirements:** R4 (no new tickets, data carried over), R5, AE7, AE9.
**Dependencies:** U2, U3, U7 (U7 and migration `112` merged first).

**Files:**
- `backend/app/skills/execute_python/tool.py` (replace `_execute_e2b`'s ticket creation with a sandbox run)
- `backend/app/internal_api/execution.py` (the `approval_required`, `approval_arguments_mismatch` and `approval_consumed` guard in `execute_tool`; `build_skill_context` passes `run_id` and `tool_call_id` into `ctx.metadata`)
- `backend/app/services/approval_service.py` (`find_for_execution`, `consume`)
- `backend/app/services/deep_work/sandbox_tools.py` (reference only; extract a shared helper only if copying more than about 20 lines)
- `backend/app/services/output_gating.py` and `backend/app/api/v1/wide_research.py` (write `kind='review'` ledger rows through `ApprovalService.record_pending` instead of creating tickets)
- `backend/app/api/v1/sandbox.py` (delete the `POST /sandbox/execute` route and the by-id read route, the only places that create or read `PENDING_REVIEW` executions; no frontend caller of `executeSandbox` or `getSandboxExecution` exists outside `frontend/lib/api/sandbox.ts`; keep the workspace file and todo routes)
- `backend/migrations/versions/113_retire_review_tickets.py` (new, data only; `down_revision` is `112_deep_work_awaiting_input`): copies each open `human_review_tickets` row (status `pending`, `assigned`, `in_review` or `escalated`) into `tool_approvals` with `kind='review'` and `legacy_ticket_id`, skipping any `legacy_ticket_id` already present, so it is idempotent; legacy rows are left untouched
- `backend/app/services/review_queue.py` (legacy decision path only: when a legacy ticket with a ledger copy is decided, close the matching ledger row by `legacy_ticket_id`, so the old and new UI cannot double-decide before U15 removes the path; `create_ticket` is made to raise `review_tickets_retired`)
- `backend/tests/postgres/test_migrations.py` (single-head assertion)
- `backend/tests/test_execute_python_skill.py` (rewrite: it asserts the ticket interface today)
- `backend/tests/internal/test_execute_python_gateway.py` (new)
- `backend/tests/test_retire_review_tickets.py` (new)
- `backend/tests/integration/test_deep_work_e2e.py` (remove ticket references)

**Approach:**
- KTD7. Through the gateway, `execute_python` runs `code` (and uploads `input_files`) in `SandboxExecutor`, returns stdout, stderr and files under the existing size and secret guards (`guard_result`), and respects the spec's 90 s timeout.
- `execute_tool` for a `requires_approval` spec locks the ledger row for `(tenant, run_id, tool_call_id)` `FOR UPDATE`, requires `status='approved'`, `decision='approve'` and `consumed_at IS NULL`, recomputes `canonical_args_hash` over the invoke body's arguments and requires it to equal `arguments_hash`, runs the tool, and sets `consumed_at` on the first successful execution. Failures are tool outcome errors (403 with the codes in KTD7), not 500s. Specs that don't require approval are untouched.
- Outside the gateway the skill returns `{ success: false, error: "Code execution needs an approved request. Use engine chat." }` and creates nothing.
- The old `/audit/reviews*` routes keep serving (decision only) until U15 so the existing UI does not break; they can no longer create tickets.

**Test scenarios:**
- `execute_python` via `execute_tool` with an approved ledger row: the fake `SandboxExecutor` receives the code once and the outcome carries its output; `consumed_at` is set.
- Without a ledger row, or with `pending`, `deciding`, `denied` or `expired`: 403 `approval_required`, the sandbox fake records no call.
- Row approved for a different `toolCallId`: refused (no reuse across calls).
- Invoke body arguments differ from the approved ones (changed code): 403 `approval_arguments_mismatch`, sandbox untouched, row not consumed.
- A second invoke with a new `Idempotency-Key` after success: 403 `approval_consumed`, sandbox runs once in total. The same `Idempotency-Key` twice returns the stored outcome and runs the sandbox once (existing `tool_invocations` path).
- Two concurrent invokes with different keys on one approved row: exactly one runs.
- `ENABLE_SANDBOX=false`: the existing refusal message, still no execution.
- Outside the gateway: refusal, and no ledger row or ticket is created.
- Migration data step: two open tickets and one closed ticket yield two `review` ledger rows with `legacy_ticket_id`; running it twice does not duplicate; the legacy rows are byte-identical afterwards.
- Deciding a legacy ticket through the old route closes its ledger copy.
- `output_gating` failure and a `wide_research` review now create `review` ledger rows and no ticket.
- `POST /api/v1/sandbox/execute` is absent from the OpenAPI document.

**Verification:** backend ruff and pytest green; the Postgres migration test passes.

### U7. Inbox backend and orphan reconciliation

**Goal:** one endpoint answering "what is waiting on me?", backed by ledger rows that cannot be stale.
**Requirements:** R6, R3 (KTD11), AE4, AE7.
**Dependencies:** U2.

**Files:**
- `backend/app/services/inbox_service.py` (new: adapters and `list_for_user`, `count_for_user`)
- `backend/app/api/v1/inbox.py` (new: `GET /api/v1/inbox`, `GET /api/v1/inbox/count`)
- `backend/app/main.py` (include the router)
- `backend/app/services/approval_service.py` (`reconcile`, extend `expire_overdue` to also reconcile `deciding` rows older than 60 s)
- `backend/app/models/deep_work_run.py` (add `awaiting_input` JSONB, nullable)
- `backend/migrations/versions/112_deep_work_awaiting_input.py` (new; `down_revision` is `111_tool_approvals`)
- `backend/app/api/v1/deep_work_engine.py` (`clarify` sets `awaiting_input` when `IntakeResult.action != "proceed"`; the plan route clears it)
- `backend/app/services/deep_work_state.py` (helpers `set_awaiting_input` / `clear_awaiting_input`; terminal transitions (complete, cancel, fail) call `ApprovalService.mark_closed` for the run)
- `backend/tests/postgres/test_migrations.py` (single-head assertion)
- `backend/tests/test_inbox_service.py` (new)
- `backend/tests/test_approval_reconcile.py` (new)
- `backend/tests/test_inbox_api.py` (new)
- `backend/tests/postgres/test_inbox_rls.py` (new)
- `backend/tests/test_deep_work_engine_api.py` (extend: clarify persists, plan clears, terminal closes rows)

**Approach:**
- KTD8. The route takes `kinds` (comma list), `limit` (default 50, max 200) and a `cursor` (created-at plus id). Sorting: due soonest, then newest.
- Adapters read, per tenant (RLS applies as usual):
  - ledger rows `pending` and not expired, filtered by visibility (admins and owners see all of the tenant; members see rows they requested or may decide);
  - `ComplianceExecution` with `status == manual_review` (admins/owners only, as in `compliance.py`);
  - `BackgroundJob` `failed` in the last 72 h for the user's jobs (admins: tenant);
  - `DeepWorkRun` with `awaiting_input IS NOT NULL` and `status='running'` for the user.
- Each item carries `actions`: for ledger rows `[{ type: "decide", endpoint, requiresRole }]` computed with `can_decide`, so the UI can grey out buttons without a second call; others link via `href`.
- `count` returns `{ total, byKind }` cheaply with one query per source.
- **Sweep and reconcile (KTD11).** Every `list_for_user` call runs `expire_overdue(tenant)` and then `reconcile(tenant)` before reading. `reconcile` is rate-limited (once per tenant per 30 s, in-process), reads at most 20 engine conversations per pass through `EngineClient.approvals`, applies the grace window (120 s) before treating a missing item as orphaned, and is a no-op when the engine is unreachable. It also finalises `deciding` rows older than 60 s from the engine item.
- Do not add Redis or any queue; this is a read model.

**Test scenarios** (`backend/tests/test_inbox_service.py`):
- Each source on its own yields one correctly shaped item.
- Mixed sources are ordered by due date then recency.
- Member sees only own and decidable rows; admin and owner see the tenant's; user of another tenant sees none.
- `kinds=approval` filters. Cursor pagination returns each item once.
- Expired and decided ledger rows are absent. An overdue pending row is swept to `expired` and disappears.
- A failed fleet job older than 72 h is absent.
- A Deep Work run with `awaiting_input` appears; after `plan` clears it, it is gone.
- `actions` for a viewer carry no decide action.
- `count` agrees with `list`.

`backend/tests/test_approval_reconcile.py` (fake `EngineClient.approvals`):
- Engine item `expired` or `cancelled`: row closed to match, `approval_expired` / `approval_cancelled` audit entry, item leaves the inbox.
- TTL `0` row (no `expires_at`) whose engine item is `cancelled`: closed by reconcile, not by the time sweep.
- Row whose item is missing and older than 120 s: closed `cancelled`; younger than 120 s: untouched.
- Conversation gone (404): rows closed.
- Engine item `approved` while the ledger says `pending` or `deciding`: finalised with the engine-recorded principal and decision.
- Engine unreachable: nothing changes.
- A Deep Work run reaching a terminal state closes its pending rows; the inbox no longer lists them.
- Rate limit: two inbox reads within 30 s produce one engine pass.

`backend/tests/test_inbox_api.py`: 401 without auth; 200 shape with the response model; `limit` above 200 is 422.
`backend/tests/postgres/test_inbox_rls.py`: no cross-tenant leakage through any adapter.

**Verification:** backend ruff and pytest green.

### U8. Chat approval card

**Goal:** the production chat card shows why, who can decide, and handles refusal.
**Requirements:** R9, AE1, AE2, AE3.
**Dependencies:** U2.

**Files:**
- `frontend/components/chat/shared/interactive/ApprovalCard.tsx`
- `frontend/lib/engine/approval.ts` (`toDecisionError`: for 403, read `error.data?.detail?.code` (`APIError.data` holds the parsed body) to distinguish `not_authorized` from `self_approval_forbidden`; add `describeApproval` to turn `rule` and `reason` into copy)
- `frontend/components/chat/EngineChat.tsx` (handlers already exist at `approvalHandlers`; show an "awaiting approval" banner that links to the inbox)
- `frontend/components/chat/__tests__/approval.test.tsx` (extend)
- `frontend/lib/engine/__tests__/approval.test.ts` (new; no such file exists today)

`viewToMessages.ts` needs no change: `a.reason` already flows to the card part's `description` (line ~262).

**Approach:**
- The decide call and URL are unchanged. The card renders the engine item's `reason` as is: Python already writes "Requires admin approval" or "This run read untrusted content" into it (U3), so no new engine field and no `approverRole` plumbing is added.
- On 403 the card locks with "You cannot decide this. Ask an admin." and, for `self_approval_forbidden`, "Another admin must approve this." It keeps showing the arguments, which stay credential-scrubbed by `summarizeArguments`.
- Pending state survives a page reload because the card is derived from `view.approvals.items` (already the case).
- Expired items render the existing "expired" state.

**Test scenarios:**
- Reason text renders on the card for a Rule-of-Two approval and for a `requires_approval` one.
- 403 `not_authorized` locks the card with the not-allowed copy and does not retry; 403 `self_approval_forbidden` shows the separate-approver copy; 409/410 shows already-decided; 5xx stays retryable.
- Approve and deny call `decide(toolTaskId, ...)` once each and the card shows pending until the view's item changes status.
- A pending item still renders after remounting with the same view (reload).
- Credential-looking argument values are shown as `[hidden]`.
- The "awaiting approval" banner appears only while a pending item exists.

**Verification:** frontend `npx tsc --noEmit && npx jest --ci --watchAll=false` green.

### U9. "Needs attention" inbox page

**Goal:** a global page and badge for everything waiting on a person.
**Requirements:** R6, AE7.
**Dependencies:** U7, U8.

**Files:**
- `frontend/app/(dashboard)/inbox/page.tsx` (new)
- `frontend/components/inbox/InboxList.tsx`, `frontend/components/inbox/InboxItemRow.tsx` (new)
- `frontend/lib/api/inbox.ts` (new: `listInbox`, `inboxCount`)
- `frontend/hooks/useInbox.ts` (new: polling every 30 s while visible; refetch on window focus)
- `frontend/components/layout/Sidebar.tsx` (new "Needs attention" item with a count badge, placed under Chat)
- `frontend/lib/api/index.ts` (export)
- `frontend/app/(dashboard)/inbox/__tests__/page.test.tsx` (new)
- `frontend/components/inbox/__tests__/InboxList.test.tsx` (new)
- `frontend/hooks/__tests__/useInbox.test.tsx` (new)
- `frontend/components/layout/__tests__/Sidebar.inbox.test.tsx` (new)

(The compliance page and `/audit/reviews*` frontend callers are replaced in U15, which depends on this unit.)

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

**Verification:** frontend tsc and jest green.

### U10. Engine human-question primitive, core (`ask_user`)

**Goal:** a model can ask a person a question; the wait is durable like an approval. This unit is the engine core only; HTTP, watch and worker wiring are U14.
**Requirements:** R10, AE8.
**Dependencies:** U4 (shares `tool-task.ts`).

**Files:**
- `engine/src/agent/questions.ts` (new: `questionsDoc` kind `agent.questions`, `requestQuestion`, `applyAnswer`, `closeQuestion`, `QuestionRejected`)
- `engine/src/agent/ask-user-tool.ts` (new: the native `ask_user` tool definition, flagged `internal`)
- `engine/src/agent/tool-task.ts` (a phase for tools flagged `humanInput: true`: create the question item and park on signal `answer`, resolve on the answer or on expiry)
- `engine/src/tools/registry.ts` (`humanInput?: boolean`)
- `engine/src/agent/harness.ts` (`answerQuestion`, `questions`)
- `engine/src/index.ts`
- `engine/README.md` (questions document, tool phase and harness API section)
- `engine/test/agent/questions-suite.ts`, `questions.memory.test.ts`, `questions.postgres.test.ts` (new)

**Approach:**
- Same shape and invariants as `approvals.ts`: one pending item per tool task, one answer, rejected reasons (`not_owned`, `already_answered`, `expired`, `not_waiting`, `invalid`), `answer` payload size-capped, `ENGINE_QUESTION_TTL_MS` (default 7 days, `0` none).
- The model sees the answer as the tool result text. Options are returned as the chosen labels, and free text as given.
- A question call still goes through `authorizeTool` first (Python answers `allow` and indexes it, U3).
- Clean-room: derive from the README contract and `approvals.ts` structure only.

**Test scenarios** (`questions-suite.ts`, memory and Postgres, harness level):
- The model calls `ask_user`; the task parks `waiting` on `answer`; the item is `pending` with the question and options.
- Answer with a valid option: the run resumes, the model's next turn contains the answer, and exactly one result entry exists.
- Answer twice: the second is rejected `already_answered`.
- Answer for another conversation's task id: `not_owned`.
- Invalid answer shape or an oversized answer: rejected `invalid` and nothing committed.
- Expiry before an answer: tool result `question_expired`, run continues.
- Restart while waiting: reopen the harness, the item is still pending, answering works.
- An abort of the run while waiting closes the item `cancelled`.

**Verification:** engine lint, typecheck and vitest green; clean-room grep empty.

### U11. Backend questions route and indexing

**Goal:** answer questions through the control plane, and list them in the inbox.
**Requirements:** R10, R6, AE8.
**Dependencies:** U2, U7, U14.

**Files:**
- `backend/app/services/engine_client.py` (`answer(...)`, `questions(...)` read)
- `backend/app/api/v1/engine_conversations.py` (`POST /{conversation_id}/questions/{tool_task_id}/answer`)
- `backend/app/services/approval_service.py` (reuse `decide` mechanics for `kind='question'`; the permission is the conversation owner or an admin or owner, not `approvals:decide`; extend `reconcile` to compare question rows with the questions document)
- `backend/app/services/inbox_service.py` (the `question` adapter reads the ledger rows already; add the `answer` action shape)
- `backend/app/services/engine_chat_agent.py` (offer `ask_user` to the model only when the conversation can reach a human: top-level chat, not workers)
- `backend/tests/test_engine_question_answer.py` (new)
- `backend/tests/internal/test_engine_chat_agent.py` (extend)
- `backend/tests/test_inbox_service.py` (extend)

**Approach:**
- Same protocol as KTD5 (`pending -> deciding -> answered`, including the non-definitive-outcome and original-decider rules), same audit entry (`question_answered`), same 404/403/409/410 mapping. Who may answer: the user the conversation belongs to, or a tenant admin or owner. This is deliberately looser than approvals because answering grants no power.
- Deep Work workers never get `ask_user` (they have no human; the unattended block in U3 already assumes this).

**Test scenarios:**
- Owner answers: 200, the engine fake sees one answer with the right principal, row `answered`.
- Another member of the tenant: 403; other tenant: 404; admin: 200.
- Second answer: 409. Engine 502 leaves the row `deciding`; only the answerer retries.
- Inbox shows the question and removes it after the answer; a cancelled question is reconciled away.
- The chat agent tool list includes `ask_user` for a top-level conversation and excludes it for `agentRole` conversations.

**Verification:** backend ruff and pytest green.

### U12. Chat question card

**Goal:** question cards work in production chat.
**Requirements:** R9, R10, AE8.
**Dependencies:** U8, U11, U14.

**Files:**
- `frontend/lib/engine/types.ts` (parse `questions`, default empty when absent)
- `frontend/lib/engine/viewToMessages.ts` (turn pending `questions.items` into the existing question message parts)
- `frontend/lib/engine/client.ts` (`answer(conversationId, toolTaskId, answer)`)
- `frontend/lib/engine/useEngineConversation.ts` (expose `answerQuestion`)
- `frontend/components/chat/EngineChat.tsx` (supply `onQuestionAnswer` in the renderer handlers, next to `approvalHandlers`)
- `frontend/components/chat/shared/MessageRenderer.tsx` (no change expected; `onQuestionAnswer` already exists at line ~67)
- `frontend/components/inbox/InboxItemRow.tsx` (inline answering for `question` rows)
- `frontend/lib/engine/__fixtures__/chat_views.json` (add a view with a pending question, captured from a real U14 run, not hand-made)
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
**Requirements:** R1-R5, R8, AE1, AE2, AE4, AE6, AE9.
**Dependencies:** U5, U6, U7.

**Files:**
- `backend/tests/e2e/test_approval_restart.py` (new)
- `backend/tests/e2e/harness.py`, `backend/tests/e2e/backend_app.py`, `backend/tests/e2e/dw_fakes.py` (extend only as needed: a scripted model that calls `execute_python`, a fake sandbox, a second tenant user and a viewer, a second admin)
- `engine/test/worker/approval-restart.test.ts` (new: worker-level kill and reopen with a stubbed backend authorize)
- `frontend/e2e/approval-recovery.spec.ts` (new Playwright spec, beside the existing `chat-engine-recovery.spec.ts`, reusing its helpers and `global-setup.ts`: an approval card survives a page reload and shows the inbox badge)
- `DEPLOYMENT.md` (short "Approvals and audit" section: TTL, tenant settings, `APPROVALS_LEGACY_CUTOFF` runbook step, KMS backend setting, the two purge GUC modes and the `oniva_system` requirement)

**Approach:** copy the structure of `backend/tests/e2e/test_chat_engine_recovery.py` (kill the worker with SIGKILL, restart, assert the continuation). Add a backend restart between the kill and the decision.

**Test scenarios:**
- AE1: member's chat triggers `execute_python`; the call parks; `worker.kill(SIGKILL)` and restart the worker and the backend app; the inbox still lists the approval; an admin approves; the fake sandbox ran exactly once; the assistant message contains the output.
- AE2: before the admin acts, viewer and a member of another project get 403 and another tenant's user 404; the engine item stays `pending` (read from the watch view); no `approval_decided` entry exists.
- AE4: with a tiny tenant TTL and the engine clock advanced, the call ends `approval_expired` and the ledger row is `expired`. Aborting a run with a pending approval closes the ledger row `cancelled` and the inbox item disappears.
- AE6: after the whole scenario, `verify_chain` for the tenant is ok and entries are consecutive; a direct SQL `UPDATE` by the app role fails.
- AE9: after approval, an invoke with changed code is refused `approval_arguments_mismatch`; the approved invoke runs once; a replay with a new idempotency key is `approval_consumed`.
- Rule of Two, scripted: `browse_url` then `linear_create_issue` parks on approval with the untrusted reason; a subagent doing the same parks for the chat's user; an unattended conversation doing the same is blocked and the run completes.
- `execute_python` invoked straight at the gateway with no approval row is refused 403 and the sandbox sees nothing (defence in depth).
- Playwright: the pending card survives a reload; the badge shows 1.

**Verification:** `ONIVA_E2E=1 uv run pytest -m e2e tests/e2e`, the engine vitest file, and the Playwright spec are green.

### U14. Engine questions: HTTP routes, watch view, worker registration

**Goal:** the U10 primitive is reachable: answerable over HTTP, visible in the conversation view, and registered in the worker.
**Requirements:** R10, AE8.
**Dependencies:** U5 (worker file), U10 (core), U4 (routes file).

**Files:**
- `engine/src/server/routes.ts` (`POST /v1/conversations/:id/questions/:toolTaskId/answer` with `{ answer: { optionIds?: string[], text?: string } }`, principal in `X-Oniva-Principal`; `GET /v1/conversations/:id/questions` returning the questions document, for the U7/U11 reconciler)
- `engine/src/watch/view.ts`, `engine/src/watch/patch.ts` (include `questions` in the conversation view and its patches; follow how `approvals` is exposed)
- `engine/src/worker/worker.ts` (register the native `ask_user` tool when the conversation's agent tools include `ask_user`)
- `engine/README.md` (routes and view member)
- `engine/test/server/http.test.ts` (answer route, read route, error mapping)
- `engine/test/watch/view.test.ts`, `engine/test/watch/patch.test.ts` (view member and patches)
- `engine/test/worker/worker.test.ts` (registration)

**Approach:**
- The watch view gains `questions: { items: QuestionItem[] }`, absent on older documents (readers default to empty). The answer route maps `QuestionRejected` reasons to HTTP like the approval decision route (`not_owned` 404, `already_answered` and `not_waiting` 409, `expired` 410, `invalid` 400).
- Clean-room: derive from the README contract and the approval route structure only.

**Test scenarios:**
- Valid answer over HTTP: 200, the run resumes; missing `X-Oniva-Principal`: 400; wrong tenant: 404.
- Answer twice: second is 409; invalid shape or oversized answer: 400 and nothing committed.
- View patch `/questions/items/<n>` arrives on open and on answer; a conversation document without `questions` yields an empty member.
- `GET .../questions` returns items and is tenant-scoped.
- The worker registers `ask_user` only when the conversation's tools include it; a worker conversation without it cannot call it.

**Verification:** engine lint, typecheck and vitest green; clean-room grep empty.

### U15. Cleanup: delete the review-ticket routes and frontend callers

**Goal:** the review-ticket path is gone, not just dormant.
**Requirements:** R4, AE7.
**Dependencies:** U6, U9.

**Files:**
- `backend/app/services/review_queue.py` (delete)
- `backend/app/api/v1/audit.py` (delete the `/reviews*` routes and their request models; keep every other route)
- `backend/app/api/v1/compliance.py` (remove references to `review_queue`, if any; keep `/review-queue`, which is `ComplianceExecution`)
- `frontend/lib/api/compliance.ts` (delete the `/audit/reviews*` calls, `ReviewTicket` types and `useReviewTickets`)
- `frontend/components/compliance/ReviewTicketTable.tsx` (delete)
- `frontend/app/(dashboard)/compliance/page.tsx` (it imports `ReviewTicketTable` and `useReviewTickets` and renders the table near lines 281-284; replace that section with a link to `/inbox?kinds=review`)
- `frontend/lib/api/sandbox.ts` (delete `executeSandbox`, `getSandboxExecution` and their types, which U6 left without a server route)
- `backend/tests/test_audit_reviews_removed.py` (new)
- `frontend/app/(dashboard)/compliance/__tests__/page.test.tsx` (extend or add)

**Approach:**
- Callers are known (the three frontend files above and `review_queue.py` users already moved by U6). The grep gates must be empty afterwards.

**Test scenarios:**
- `GET /api/v1/audit/reviews` and `POST /api/v1/audit/reviews/{id}/decision` are absent from the OpenAPI document and return 404.
- No importable module named `review_queue` remains.
- The compliance page renders the inbox link and its mocked `apiFetch` calls never include `/audit/reviews`.
- `grep -rn "audit/reviews" frontend` is empty; `grep -rn "review_queue\|ReviewQueueService" backend/app` is empty.

**Verification:** backend ruff and pytest green; frontend tsc and jest green.

---

## Verification Contract

| Area | Gate |
|---|---|
| engine | `npm run lint && npm run typecheck && npx vitest run` green (run from `engine/`) |
| backend | `uv run ruff check app tests && uv run pytest -q -p no:cacheprovider` green (run from `backend/`) |
| backend Postgres | `backend/tests/postgres/test_audit_immutability.py`, `test_tool_approvals_rls.py`, `test_inbox_rls.py` and `test_migrations.py` pass against a local Postgres (these are skipped, not failed, without one; run them for U1, U2, U6, U7) |
| frontend | `npx tsc --noEmit && npx jest --ci --watchAll=false` green (run from `frontend/`) |
| e2e | `ONIVA_E2E=1 uv run pytest -m e2e tests/e2e` green (run from `backend/`), plus the Playwright `frontend/e2e/approval-recovery.spec.ts` spec |
| hygiene | clean-room grep on `engine/src` and `engine/test` for third-party pi or earendil names is empty; gitleaks clean; no `.env` or key material committed; no new `console.log` of arguments |
| grep gates | after U15: `grep -rn "review_queue\|ReviewQueueService" backend/app` is empty and `HumanReviewTicket` appears only in the model and migrations; `grep -rn "audit/reviews" frontend` is empty |

## Definition of Done

- U1-U15 are merged with their test scenarios passing, in dependency order, with the migration order U1 `110`, U2 `111`, U7 `112`, U6 `113`.
- All Verification Contract gates are green in CI.
- AE1-AE9 hold, with AE1, AE2, AE4, AE6 and AE9 proven by U13.
- `compliance_audit_logs` is append-only at the database (one allowed UPDATE shape, two guarded purge modes), every tenant's chain verifies, and signing goes through `KeyManagementService` with no PEM-on-disk path in production.
- No code creates a review ticket, and `execute_python` runs only after an approved, unconsumed ledger row whose argument hash matches the call.
- `DEPLOYMENT.md` documents approval TTL, tenant approval settings, `APPROVALS_LEGACY_CUTOFF`, the KMS backend setting and the audit purge modes.
- Open Questions below are answered in writing by the coordinator before the unit that depends on them merges.

## Risks

| # | Risk | Affects | Mitigation |
|---|---|---|---|
| 1 | The per-call authorize round trip adds latency and a new dependency on the backend for every non-read tool. Fail-closed means a backend outage stops all writes. | KTD2, U5 | Accepted; reads and internal native tools still run. Add a short in-process cache of `allow` answers only if the stop-condition latency is hit. |
| 2 | Sticky taint makes a long chat permanently "tainted" after one web search, so every outside write asks for approval from then on. | KTD6, U4, U3 | Keep sticky; revisit with real usage. Per-run reset is a one-line change in `markTainted` scope but weakens the guarantee, because history still holds the untrusted text. |
| 3 | Output-review items and `human_in_loop` workflow nodes are recorded and decided but nothing resumes the paused workflow. | Scope, U6 | Phase 6; call it out in the PR so nobody assumes the review gates a workflow. |
| 4 | Reconciliation adds engine reads on inbox load. | KTD11, U7 | Rate-limited to once per tenant per 30 s and capped at 20 engine conversations per pass; engine-unreachable is a no-op. |
| 5 | The `already_decided` path depends on the engine rejection body carrying the item (U4); an older engine would not. | KTD5, U2 | U2 requires U4; if the body lacks the item the backend falls back to `EngineClient.approvals` before finalising. |
| 6 | A tenant with a single admin-or-owner can self-approve `execute_python`. | KTD4 | Accepted by decision: allowed and audited as `self_approved`; the tenant can set `require_separate_approver`. |

## Open Questions

Genuinely open, to be answered by the coordinator (named default applies if unanswered):

| # | Question | Affects | Default if unanswered |
|---|---|---|---|
| 1 | What is the deploy timestamp for `APPROVALS_LEGACY_CUTOFF`? It is known only when U5 ships. | KTD3, U2, U13 | Unset, which disables the legacy fallback; the U13 runbook step sets it to the U5 deploy time before the engine upgrade. |
| 2 | How is an "unattended" run identified? Fleet is not on the engine until Phase 6. | KTD6 row 4, U3 | `engine_conversations.user_id IS NULL`; Phase 6 adds an explicit origin column. |
| 3 | Should the file/image-part rule for `untrusted` submissions widen (for example to pasted URLs or connector attachments) once a real attachment field exists? | KTD6, U4 | Keep "any non-text content part"; revisit with the attachment API. |
| 4 | Is taint clearing needed (human "I reviewed this")? | KTD6 | No; sticky for the root conversation's life. |
| 5 | Does the engine's decide/read route accept a child conversation id under the root's session binding, or must it resolve the task id across the root's tree? | Assumptions, U2, U4 | The route resolves `:toolTaskId` across the root's tree (U4 tests it); the backend always sends the root id. |
| 6 | Fleet has no pause state, so "Fleet interventions" in the inbox is only failed runs. Operators may expect "agent needs help". | KTD8, U7 | Ship failures only; real pause states arrive with Fleet on engine tasks (Phase 6). |
