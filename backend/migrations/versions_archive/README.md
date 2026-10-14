# Archived migration chain (001 .. 084)

These revisions are **not loaded by Alembic** (`script_location = migrations` reads only
`migrations/versions/`). They are kept for history. The chain was never runnable from an empty
database (see `backend/tests/postgres/DRIFT.md`); it was squashed into `100_baseline`.

## Fresh installs
`alembic upgrade head` runs `100_baseline` (extensions, full schema from the models, `oniva_app`
role without a password, RLS on every `tenant_id` table). Set the role password out-of-band.

## Already-deployed databases
Do not run the baseline against them. Instead:
1. Verify the DB is at the end of the legacy chain: `SELECT version_num FROM alembic_version;`
   must be `084_create_sandbox_executions`.
2. `alembic stamp 100_baseline` (the `alembic_version.version_num` column is widened to
   varchar(255) by `env.py` automatically).
3. Schema/RLS changes that exist only in the baseline (e.g. policies for tables that lacked them,
   removal of the permissive `users` policy) are applied by the follow-up revision (U9), not by
   the stamp.

## Objects intentionally dropped (in old migrations, not in any model)
`workflow_approvals`, `webhook_endpoints`, `subworkflow_executions`, `_backup_orphaned_checkpoints_062`.
`grep` over `backend/app` and `backend/tests` finds no code using them (only comments in
`app/node_executors/human_in_loop_executor.py` mentioning `workflow_approvals`, which is a
placeholder; the executor does not query it). Re-introduce via a model plus a new revision if needed.

## Audit: DDL that existed only in the legacy chain

Method: every archived migration was scanned for `op.execute` / raw SQL / `create_index` with
`postgresql_using|where|ops` or expressions / triggers / functions / CHECK + UNIQUE constraints /
views / extensions / grants, and each named object was compared with what `Base.metadata.create_all`
builds (a scratch PG14 diff of index, constraint, trigger and function names + definitions). Objects
the models already create (all `index=True`, model `Index(...)`, model CHECKs such as
`ck_background_jobs_status`, `ck_episodic_memories_*`, `ck_project_*`, enum types, FKs, the
`knowledge_chunks` HNSW index, `sandbox_executions` indexes) are not listed. Everything below is
now created idempotently by `100_baseline.py` (constants `PERF_INDEXES`, `VECTOR_INDEXES`,
`CONSTRAINTS`, `FUNCTIONS_SQL`, `TRIGGERS`, `USAGE_COSTS_VIEW_*`, `_legacy_rls_refinements`).
Indexes live in the baseline as SQL (not model `__table_args__`) on purpose: they are partial /
INCLUDE / expression / GIN / trigram / HNSW indexes, and the sqlite unit tests and `create_all`
subset fixtures should not have to compile them.

| Object | Source migration(s) | Ported? |
|---|---|---|
| `audit_events` immutability: fn `prevent_audit_modification`, triggers `audit_events_no_update` / `_no_delete` | 013 | **yes** (test: `test_audit_events_immutable`) |
| `audit_events` RLS = SELECT + INSERT only (`tenant_isolation_audit_events`, `audit_insert`) | 013 | **yes**, plus an INSERT policy for the table owner (`audit_definer_insert`) because the audit trigger functions are SECURITY DEFINER and FORCE RLS also binds the owner |
| fn `audit_trigger_func` + triggers `audit_workflows`, `audit_custom_agents`, `audit_connectors`, `audit_api_keys` | 014 | **yes**, changed: `SET search_path`, and rows are passed through `audit_redact()` (drops `key_hash`, `password_hash`, `token_hash`, `credentials`, `auth_config`, `embedding`, `search_vector`, ...); legacy copied whole rows (incl. secrets) into an append-only table |
| fn `audit_workflow_execution` + trigger `audit_workflow_executions` | 014 | **yes**, changed: writes `event_metadata` (model column name; legacy used `metadata`, which would fail on a model-built schema) |
| fn `audit_memory_trigger_func` + triggers on `user_preferences`, `episodic_memories`, `pending_users`, `scheduled_workflows`, `scheduled_workflow_executions`, `workflow_versions` | 032, 033, 034, 035 | **yes** (same redaction as above; also keeps embeddings and password hashes out of the audit log) |
| updated_at triggers `chat_sessions_updated_at`, `update_user_preferences_updated_at`, `update_scheduled_workflows_timestamp` (+ 3 fns) | 015, 032, 034 | **yes**, the three fns merged into `set_updated_at_now()` |
| fn `ensure_single_active_version` + trigger `ensure_single_active_version_trigger` | 035, fixed in 053 | **yes** (053 version) |
| fn `check_background_job_version_integrity` + trigger `trg_background_job_version_integrity` | 052a | **yes** |
| fn `get_next_workflow_version_number(uuid)` | 035 | intentionally dropped: no caller in `app/` or `tests/` |
| fn `update_episodic_memory_search_vector` + trigger `tsvector_update_trigger` | 045 | intentionally dropped: `services/episodic_memory.py` and `api/v1/memory.py` populate `search_vector` explicitly from context + rationale + entities; the trigger would overwrite that with `context_decision` only. The GIN index on `search_vector` is ported |
| GIN FTS indexes `ix_documents_content_gin`, `ix_knowledge_chunks_content_gin` (`to_tsvector('english', content)`) | 072 | **yes** (was `CONCURRENTLY`; plain `CREATE INDEX IF NOT EXISTS` since the tables are empty; tests: `test_gin_fulltext_indexes_created_by_baseline`, `tests/test_rag_upgrade.py::TestMigrationExists`) |
| BM25 indexes `idx_documents_content_bm25`, `idx_documents_title_bm25` (`USING bm25`) | 002 | intentionally dropped: needs the ParadeDB `pg_search` extension that no later migration or deployment installs; superseded by 072 |
| `idx_documents_embedding` (hnsw, default params) | 002 | intentionally dropped: duplicate of 003's `idx_documents_embedding_hnsw` |
| HNSW: `idx_documents_embedding_hnsw` (m16/ef64) | 003 | **yes** (`VECTOR_INDEXES`) |
| HNSW: `idx_episodic_memories_embedding` (m24/ef100) | 032 | **yes** |
| HNSW partial: `ix_episodic_memories_shared_embedding` (shared, not archived) | 068 | **yes** |
| HNSW partial: `ix_episodic_memories_active_embedding` (not archived) | 078 | **yes** |
| HNSW: `ix_agent_learnings_embedding_hnsw` (m16/ef100) | 064 | **yes** |
| HNSW: `ix_knowledge_chunks_embedding` | 070 | already in the model (`KnowledgeChunk.__table_args__`) |
| GIN: `idx_episodic_memories_entities`, `idx_episodic_memories_search_vector`, `idx_workflow_entities_properties`, `idx_api_keys_scopes`, `ix_custom_agents_compliance_tags` | 032, 045, 046, 058, 065 | **yes** |
| trigram GIN: `idx_background_jobs_job_type_trgm`, `idx_background_jobs_status_trgm` (+ `CREATE EXTENSION pg_trgm`) | 051 | **yes** |
| expression index `idx_documents_kb_id` (`doc_metadata->>'kb_id'`, partial) | 039 | **yes** (the column exists in the model; the DRIFT.md "column missing" note was about the legacy schema) |
| partial indexes: `ix_chat_sessions_is_pinned`, `idx_agent_test_cases_active` / `_baseline`, `idx_agent_versions_deployed`, `idx_workflow_versions_active` / `_tag`, `idx_connectors_registry_id`, `idx_documents_parent_document_id` / `_chunk_index`, `idx_api_keys_expires`, `ix_episodic_memories_team_id` / `_tenant_shared`, `ix_custom_agents_tenant_user_active`, `idx_scheduled_workflows_poll` | 016, 020, 023, 035, 040, 041, 058, 068, 074, 078 | **yes** |
| `INCLUDE` / `DESC` indexes: `idx_workflows_user_updated`, `idx_documents_user_created`, `idx_workflow_executions_workflow_created`, `idx_agent_chat_sessions_updated`, `idx_agent_accuracy_agent_time`, `idx_deploy_history_agent_time`, `idx_episodic_memories_created`, `idx_background_jobs_cursor_pagination`, `idx_background_jobs_tenant_workflow_created`, `ix_background_jobs_tenant_version_created`, `ix_chat_sessions_tenant_user_updated` | 004, 019, 021, 022, 032, 050, 052b, 078 | **yes** |
| other plain/composite btree indexes (about 60: `ix_audit_events_*`, `ix_compliance_executions_*`, `ix_screening_results_*`, `ix_background_jobs_*`, `idx_usage_quotas_*`, `idx_alert_*`, `ix_llm_usage_*`, `idx_episodic_memories_*`, ...) | 001, 004, 005, 008, 009/026, 013, 015, 017, 019-023, 029, 032, 034, 035, 037, 038, 046, 047, 055, 057, 061, 063, 064, 065, 078 | **yes**, full list in `PERF_INDEXES` (all are on columns that exist in the models) |
| plain indexes that are redundant with a model index / unique constraint (e.g. `idx_user_preferences_tenant_user`, `idx_pending_users_tenant_email`, `idx_scheduled_workflows_workflow`, `ix_llm_providers_tenant`, `idx_teams_tenant`, `idx_oauth_clients_tenant_id`, `ix_background_jobs_workflow_version`, `idx_agent_chat_sessions_agent`) | 019, 032-034, 037, 046, 052b, 055, 063, 077 | intentionally dropped: same table + columns already indexed by the models |
| `idx_scheduled_workflows_next_run`, `ix_background_jobs_workflow_created`, `ix_background_jobs_tenant_workflow_created` | 034, 029 | intentionally dropped: superseded by `idx_scheduled_workflows_poll` (074) and the DESC cursor indexes (050) |
| `ix_workflow_executions_max_classification` + `ck_workflow_executions_classification` (column `max_classification`) | 036 | intentionally dropped: column no longer exists in `WorkflowExecution` |
| CHECK `ck_human_review_tickets_status` / `_risk_level` / `_priority` | 024 | **yes** |
| CHECK `ck_background_jobs_trigger` | 029 | **yes**, widened to include `webhook` (`JobTrigger.WEBHOOK`; the legacy CHECK would reject webhook-triggered jobs) |
| CHECK `chk_grantee_type`, `chk_permission` (workflow_permissions); `chk_channel_type` (notification_channels); `chk_trigger_type` (alert_rules); `chk_scope_type`, `chk_quota_type`, `chk_period` (usage_quotas); `chk_auth_method` (webhook_triggers); `chk_status_enabled_consistency` (scheduled_workflows) | 056, 057, 061, 074 | **yes** (value sets verified against the model enums) |
| CHECK `ck_subworkflow_executions_max_depth` | 018 | intentionally dropped with its table (see above) |
| UNIQUE `uq_agent_version`, `uq_agent_version_number`, `uq_custom_agent_tenant_sequence`, `uq_workflow_versions_workflow_version`, `uq_llm_provider_tenant_name`, `uq_usage_quota_scope_type_period`, `uq_teams_tenant_slug`, `uq_team_memberships_team_user` | 015, 035, 037, 057, 063, 083 | **yes** |
| UNIQUE `scheduled_workflows_apscheduler_job_id_key` | 074 | intentionally dropped: `apscheduler_job_id` no longer exists in the model |
| FK `skill_executions.user_id` `ON DELETE SET NULL` | 042 | **yes** (model FK has no ondelete) |
| FK `workflow_checkpoints.workflow_uuid -> workflows.id ON DELETE CASCADE` (+ orphan cleanup, `_backup_orphaned_checkpoints_062`) | 062 | intentionally dropped: `workflow_uuid` is the DBOS workflow UUID (see model docstring), not `workflows.id`; the FK would reject durable-engine checkpoints. Flag for the workflow owner |
| materialized view `usage_costs_hourly` + 3 indexes (one UNIQUE, required for `REFRESH ... CONCURRENTLY`) | 060 | **yes** (queried by `api/v1/analytics.py`; always with an explicit `tenant_id` filter, RLS does not apply to materialized views; refreshing it is still a cron job outside the migrations) |
| RLS: `user_isolation_prefs` on `user_preferences`; `user_private_episodic` + `tenant_shared_episodic` on `episodic_memories` (user-level, via `app.current_user_id`) | 032, 069 | **yes** (replace the systematic tenant-only policy for these tables) |
| RLS: `skills` shared-read | 043 | already in the baseline (`SHARED_READ_TABLES`) |
| RLS: permissive `users` / `tenants` / `pending_users` signup policies (`signup_email_check_users`, `signup_email_lookup_pending`, `*_full_access`, `WITH CHECK (true)`) | 044, 048 | intentionally dropped (see top of this file) |
| RLS: `workflow_entities_bypass` (`app.bypass_rls`) and `workflow_entities_tenant_isolation` using GUC `app.tenant_id` | 046 | intentionally dropped: wrong/unused GUC names; the systematic `app.current_tenant` policy covers the table |
| `oniva_app` role + grants + default privileges | 011, 043, 054 | already in the baseline (no password, no DB-name literal; the `GRANT CONNECT ON DATABASE` is deployment-specific) |
| `internal_endpoint_whitelist` grant limited to SELECT/INSERT/UPDATE | 036 | intentionally dropped: baseline grants DML on all tables uniformly |
| extensions `vector`, `pg_trgm` | 001, 003, 051 | **yes** (`pg_search` for BM25 not ported, see above) |
| tsvector column `episodic_memories.search_vector`, `importance`, `archived`, ... and other column adds | 045 etc. | already created from the models |
| enum types (`schedulestatus`, `batch_status`, `teamrole`, ...) | 034, 055, 063, 065 | already created from the models (labels are the Python member names, e.g. `ACTIVE`; hence the `'ACTIVE'` literals above) |
| data backfills (083 sequence numbers / aliases, 045 tsvector populate, 053 duplicate-active cleanup, 062 delete orphans) | 045, 053, 062, 083 | not applicable: fresh installs have no rows. Already-deployed DBs keep their data through `alembic stamp` |
| tables with no model: `workflow_approvals`, `webhook_endpoints`, `subworkflow_executions`, `_backup_orphaned_checkpoints_062` | 018, 062 | intentionally dropped (section above) |

Not verified locally: the five `VECTOR_INDEXES` statements (no pgvector for PG14/15 on the dev
machine). They are copied from the legacy migrations; CI (pgvector/pg16) runs them.
