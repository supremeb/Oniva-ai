"""Baseline schema: squash of the legacy 001..084 chain.

Revision ID: 100_baseline
Revises: (none)

Fresh installs run this single revision. Databases deployed from the legacy chain are NOT
upgraded by it: verify they are at ``084_create_sandbox_executions`` and run
``alembic stamp 100_baseline`` (see migrations/versions_archive/README.md).

What it does:
  1. extensions (vector for pgvector columns; gen_random_uuid() is core in PG13+),
  2. the full schema from ``Base.metadata`` (create_all - acceptable for a baseline; the
     schema is frozen to what the models define at the time of this revision),
  3. the ``oniva_app`` role (created only if missing, NO password - set it out-of-band with
     ``ALTER ROLE oniva_app PASSWORD ...``) and DML grants,
  4. row-level security for EVERY table with a ``tenant_id`` column (ENABLE + FORCE + policy),
     plus ``tenants`` keyed on ``id``. The GUC is ``app.current_tenant`` (set by
     ``app/core/database.py`` via ``set_config(..., true)``),
  5. DDL that existed ONLY in the legacy chain and cannot be expressed from the models
     (indexes with INCLUDE / DESC / partial / expression / GIN / HNSW / trigram, CHECK and
     UNIQUE constraints, SQL functions + triggers incl. audit_events immutability, the
     ``usage_costs_hourly`` materialized view, user-level RLS on memory tables). Everything is
     idempotent. See ``migrations/versions_archive/README.md`` for the object-by-object audit
     (what was ported, what was intentionally dropped and why).

Deliberately NOT recreated: the legacy permissive ``users``/``tenants``/``pending_users``
signup policies that pass when no tenant is set. Pre-tenant lookups (login, signup,
check-email) must go through a system (BYPASSRLS) role; this is U9's job.

Downgrade drops everything and is DEV-ONLY.
"""
from alembic import op
import sqlalchemy as sa

revision = "100_baseline"
down_revision = None
branch_labels = None
depends_on = None

GUC = "app.current_tenant"
# NULLIF: set_config(..., true) leaves '' (not NULL) after a transaction ends; a bare
# ''::uuid cast would raise instead of matching zero rows.
TENANT_EXPR = f"NULLIF(current_setting('{GUC}', true), '')::uuid"

# Tables whose tenant_id may be NULL for shared/system rows that every tenant may read.
SHARED_READ_TABLES = {"skills"}


# --------------------------------------------------------------------------------------
# Legacy-only DDL (see migrations/versions_archive/README.md for provenance per object).
# Kept as SQL, not in model __table_args__: these are partial/expression/INCLUDE/GIN indexes
# that the sqlite-backed unit tests and the create_all() subset fixtures should not have to
# compile, and several models are edited concurrently by feature work.
# --------------------------------------------------------------------------------------

# (name, table, "[USING method] (cols) [INCLUDE (..)] [WHERE ..]"); all CREATE INDEX IF NOT EXISTS.
PERF_INDEXES = [
    # 001/004 user_id lookups, 078/079 list ordering
    ("ix_api_keys_user_id", "api_keys", "(user_id)"),
    ("ix_connectors_user_id", "connectors", "(user_id)"),
    ("ix_audit_logs_user_id", "audit_logs", "(user_id)"),
    ("idx_documents_user_id", "documents", "(user_id)"),
    ("idx_workflows_user_id", "workflows", "(user_id)"),
    ("idx_api_keys_user_provider", "api_keys", "(user_id, provider)"),
    ("idx_connectors_user_type", "connectors", "(user_id, connector_type)"),
    ("idx_workflows_user_updated", "workflows", "(user_id, updated_at DESC) INCLUDE (id, name)"),
    ("idx_workflow_executions_workflow_created", "workflow_executions", "(workflow_id, started_at DESC)"),
    ("idx_documents_user_created", "documents", "(user_id, created_at DESC) INCLUDE (id, title)"),
    ("ix_custom_agents_user_id", "custom_agents", "(user_id)"),
    ("ix_custom_agents_agent_type", "custom_agents", "(agent_type)"),
    ("ix_tenants_is_active", "tenants", "(is_active)"),
    ("ix_users_auth_provider_external_id", "users", "(auth_provider, external_id)"),
    # 013 audit_events
    ("ix_audit_events_tenant_created", "audit_events", "(tenant_id, created_at)"),
    ("ix_audit_events_resource", "audit_events", "(resource_type, resource_id)"),
    ("ix_audit_events_user", "audit_events", "(user_id)"),
    # 015/016/078 chat sessions, agent versions, background jobs
    ("ix_chat_sessions_user_agent", "chat_sessions", "(user_id, agent_id)"),
    ("ix_chat_sessions_user_workflow", "chat_sessions", "(user_id, workflow_id)"),
    ("ix_chat_sessions_updated", "chat_sessions", "(updated_at)"),
    ("ix_chat_sessions_context_type", "chat_sessions", "(context_type)"),
    ("ix_chat_sessions_is_pinned", "chat_sessions", "(is_pinned) WHERE is_pinned = true"),
    ("ix_chat_sessions_tenant_user_updated", "chat_sessions", "(tenant_id, user_id, updated_at DESC)"),
    ("ix_agent_versions_agent_deployed", "agent_versions", "(agent_id, is_deployed)"),
    ("ix_agent_versions_created", "agent_versions", "(created_at)"),
    ("idx_agent_versions_deployed", "agent_versions", "(agent_id, is_deployed) WHERE is_deployed = true"),
    ("ix_background_jobs_user_status", "background_jobs", "(user_id, status)"),
    ("ix_background_jobs_created", "background_jobs", "(created_at)"),
    ("ix_background_jobs_tenant_status", "background_jobs", "(tenant_id, status)"),
    ("idx_background_jobs_cursor_pagination", "background_jobs", "(workflow_id, created_at DESC, id DESC)"),
    ("idx_background_jobs_tenant_workflow_created", "background_jobs", "(tenant_id, workflow_id, created_at DESC)"),
    ("ix_background_jobs_tenant_version_created", "background_jobs", "(tenant_id, workflow_version_id, created_at DESC)"),
    ("idx_background_jobs_job_type_trgm", "background_jobs", "USING gin (job_type gin_trgm_ops)"),
    ("idx_background_jobs_status_trgm", "background_jobs", "USING gin (status gin_trgm_ops)"),
    # 017 compliance
    ("ix_compliance_executions_subject", "compliance_executions", "(subject_id, subject_type)"),
    ("ix_compliance_executions_status", "compliance_executions", "(status)"),
    ("ix_compliance_executions_risk", "compliance_executions", "(risk_level, risk_score)"),
    ("ix_compliance_executions_created", "compliance_executions", "(created_at)"),
    ("ix_compliance_executions_template", "compliance_executions", "(template_id)"),
    ("ix_screening_results_execution", "compliance_screening_results", "(execution_id)"),
    ("ix_screening_results_type", "compliance_screening_results", "(screening_type)"),
    ("ix_screening_results_flagged", "compliance_screening_results", "(flagged)"),
    # 019-023 agent studio
    ("idx_agent_chat_sessions_user_tenant", "agent_chat_sessions", "(user_id, tenant_id)"),
    ("idx_agent_chat_sessions_updated", "agent_chat_sessions", "(updated_at DESC)"),
    ("idx_agent_test_cases_agent", "agent_test_cases", "(agent_id)"),
    ("idx_agent_test_cases_active", "agent_test_cases", "(agent_id, is_active) WHERE is_active = true"),
    ("idx_agent_test_cases_baseline", "agent_test_cases", "(agent_id, is_baseline) WHERE is_baseline = true"),
    ("idx_agent_test_cases_category", "agent_test_cases", "(agent_id, category)"),
    ("idx_agent_accuracy_agent_time", "agent_accuracy_history", "(agent_id, evaluated_at DESC)"),
    ("idx_agent_accuracy_version", "agent_accuracy_history", "(agent_version_id)"),
    ("idx_deploy_history_agent_time", "deploy_history", "(agent_id, created_at DESC)"),
    ("idx_deploy_history_version", "deploy_history", "(agent_version_id)"),
    # 032/045/047/068/078 episodic memory
    ("idx_episodic_memories_tenant_user", "episodic_memories", "(tenant_id, user_id)"),
    ("idx_episodic_memories_agent_action", "episodic_memories", "(agent_name, action_type)"),
    ("idx_episodic_memories_created", "episodic_memories", "(created_at DESC)"),
    ("idx_episodic_memories_entities", "episodic_memories", "USING gin (entities)"),
    ("idx_episodic_memories_search_vector", "episodic_memories", "USING gin (search_vector)"),
    ("idx_episodic_memories_importance", "episodic_memories", "(importance)"),
    ("idx_episodic_memories_archived", "episodic_memories", "(archived)"),
    ("idx_episodic_memories_type", "episodic_memories", "(memory_type)"),
    ("idx_episodic_memories_feedback", "episodic_memories", "(feedback)"),
    ("idx_episodic_memories_visibility", "episodic_memories", "(visibility)"),
    ("ix_episodic_memories_team_id", "episodic_memories", "(team_id) WHERE team_id IS NOT NULL"),
    ("ix_episodic_memories_tenant_shared", "episodic_memories",
     "(tenant_id, visibility) WHERE visibility = 'shared' AND archived = false"),
    # 034/074 scheduler (074 replaced the (is_enabled, next_run_at) index with the poll index)
    ("idx_scheduled_workflows_poll", "scheduled_workflows", "(next_run_at ASC) WHERE is_enabled = true AND status = 'ACTIVE'"),
    ("idx_scheduled_workflows_tenant_status", "scheduled_workflows", "(tenant_id, status)"),
    ("idx_scheduled_executions_schedule_status", "scheduled_workflow_executions", "(schedule_id, status)"),
    ("idx_scheduled_executions_scheduled_for", "scheduled_workflow_executions", "(scheduled_for)"),
    # 035 workflow versions
    ("idx_workflow_versions_active", "workflow_versions", "(workflow_id, is_active) WHERE is_active = true"),
    ("idx_workflow_versions_tag", "workflow_versions", "(workflow_id, version_tag) WHERE version_tag IS NOT NULL"),
    ("idx_workflow_versions_spec_hash", "workflow_versions", "(workflow_id, spec_hash)"),
    # 036/037/038 endpoint whitelist, LLM providers/usage
    ("ix_llm_providers_tenant_priority", "llm_providers", "(tenant_id, priority)"),
    ("ix_llm_usage_tenant_date", "llm_usage_records", "(tenant_id, created_at)"),
    ("ix_llm_usage_provider", "llm_usage_records", "(provider_id, created_at)"),
    ("ix_llm_usage_workflow", "llm_usage_records", "(workflow_id)"),
    # 039/041/072 documents + RAG (072: BM25-style full-text; queries use to_tsvector('english', content))
    ("idx_documents_kb_id", "documents", "((doc_metadata->>'kb_id')) WHERE doc_metadata->>'kb_id' IS NOT NULL"),
    ("idx_documents_parent_document_id", "documents", "(parent_document_id) WHERE parent_document_id IS NOT NULL"),
    ("idx_documents_chunk_index", "documents", "(parent_document_id, chunk_index) WHERE parent_document_id IS NOT NULL"),
    ("ix_documents_content_gin", "documents", "USING gin (to_tsvector('english', content))"),
    ("ix_knowledge_chunks_content_gin", "knowledge_chunks", "USING gin (to_tsvector('english', content))"),
    # 040 connectors
    ("idx_connectors_source_type", "connectors", "(source_type)"),
    ("idx_connectors_registry_id", "connectors", "(registry_id) WHERE registry_id IS NOT NULL"),
    # 046 workflow entities
    ("idx_workflow_entities_tenant_name", "workflow_entities", "(tenant_id, name)"),
    ("idx_workflow_entities_type", "workflow_entities", "(entity_type)"),
    ("idx_workflow_entities_properties", "workflow_entities", "USING gin (properties)"),
    # 055 batches / oauth
    ("idx_oauth_clients_is_active", "oauth_clients", "(is_active)"),
    ("idx_workflow_batches_workflow_id", "workflow_batches", "(workflow_id)"),
    ("idx_workflow_batches_created_at", "workflow_batches", "(created_at)"),
    ("idx_workflow_batch_items_batch_seq", "workflow_batch_items", "(batch_id, sequence)"),
    # 057 alerting / quotas
    ("idx_notification_channels_type", "notification_channels", "(channel_type)"),
    ("idx_alert_rules_trigger_type", "alert_rules", "(trigger_type)"),
    ("idx_alert_rules_active", "alert_rules", "(is_active)"),
    ("idx_alert_history_created", "alert_history", "(created_at)"),
    ("idx_usage_quotas_scope", "usage_quotas", "(scope_type, scope_id)"),
    ("idx_usage_quotas_type", "usage_quotas", "(quota_type)"),
    ("idx_usage_quotas_active", "usage_quotas", "(is_active)"),
    ("idx_usage_history_scope", "usage_history", "(scope_type, scope_id)"),
    ("idx_usage_history_created", "usage_history", "(created_at)"),
    # 058 api keys
    ("idx_api_keys_expires", "api_keys", "(expires_at) WHERE expires_at IS NOT NULL"),
    ("idx_api_keys_scopes", "api_keys", "USING gin (scopes)"),
    # 061/063/064/065/078
    ("idx_webhook_deliveries_received", "webhook_deliveries", "(received_at)"),
    ("idx_webhook_deliveries_idempotency", "webhook_deliveries", "(webhook_trigger_id, idempotency_key)"),
    ("idx_approval_requests_status", "approval_requests", "(status)"),
    ("ix_agent_learnings_tenant_agent_status", "agent_learnings", "(tenant_id, agent_id, status)"),
    ("ix_custom_agents_compliance_tags", "custom_agents", "USING gin (compliance_tags)"),
    ("ix_custom_agents_tenant_user_active", "custom_agents", "(tenant_id, user_id) WHERE is_active = true"),
    ("ix_custom_agents_tenant_visibility_category", "custom_agents", "(tenant_id, visibility, category)"),
]

# pgvector HNSW indexes (need the ``vector`` extension >= 0.5). Kept apart so environments
# without pgvector can be shimmed. ``knowledge_chunks`` HNSW is declared in its model.
VECTOR_INDEXES = [
    ("idx_documents_embedding_hnsw", "documents",
     "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"),
    ("idx_episodic_memories_embedding", "episodic_memories",
     "USING hnsw (embedding vector_cosine_ops) WITH (m = 24, ef_construction = 100)"),
    ("ix_episodic_memories_shared_embedding", "episodic_memories",
     "USING hnsw (embedding vector_cosine_ops) WHERE visibility = 'shared' AND archived = false"),
    ("ix_episodic_memories_active_embedding", "episodic_memories",
     "USING hnsw (embedding vector_cosine_ops) WHERE archived = false"),
    ("ix_agent_learnings_embedding_hnsw", "agent_learnings",
     "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 100)"),
]

# (table, name, kind, definition): added only if no constraint of that name exists.
# NOTE background_jobs.trigger also allows 'webhook' (JobTrigger.WEBHOOK); the legacy CHECK
# (manual/api/scheduled) would have rejected it.
CONSTRAINTS = [
    ("human_review_tickets", "ck_human_review_tickets_status", "CHECK",
     "status IN ('pending', 'assigned', 'in_review', 'approved', 'rejected', 'escalated')"),
    ("human_review_tickets", "ck_human_review_tickets_risk_level", "CHECK",
     "risk_level IN ('low', 'medium', 'high', 'critical')"),
    ("human_review_tickets", "ck_human_review_tickets_priority", "CHECK",
     "priority IN ('low', 'normal', 'high', 'urgent')"),
    ("background_jobs", "ck_background_jobs_trigger", "CHECK",
     "trigger IN ('manual', 'api', 'scheduled', 'webhook')"),
    ("workflow_permissions", "chk_grantee_type", "CHECK",
     "grantee_type IN ('user', 'role', 'organization')"),
    ("workflow_permissions", "chk_permission", "CHECK",
     "permission IN ('view', 'execute', 'edit', 'admin')"),
    ("notification_channels", "chk_channel_type", "CHECK",
     "channel_type IN ('email', 'slack', 'webhook', 'teams', 'pagerduty')"),
    ("alert_rules", "chk_trigger_type", "CHECK",
     "trigger_type IN ('execution_failed', 'high_failure_rate', 'sla_breach', 'quota_exceeded', 'custom')"),
    ("usage_quotas", "chk_scope_type", "CHECK", "scope_type IN ('tenant', 'user', 'team')"),
    ("usage_quotas", "chk_quota_type", "CHECK",
     "quota_type IN ('llm_tokens', 'executions', 'storage_bytes', 'api_calls')"),
    ("usage_quotas", "chk_period", "CHECK",
     "period IN ('daily', 'weekly', 'monthly', 'annual', 'lifetime')"),
    ("webhook_triggers", "chk_auth_method", "CHECK",
     "auth_method IN ('hmac', 'api_key', 'ip_whitelist', 'none')"),
    ("scheduled_workflows", "chk_status_enabled_consistency", "CHECK",
     "(status = 'ACTIVE' AND is_enabled = true) OR "
     "(status IN ('PAUSED', 'DISABLED', 'COMPLETED', 'ERROR') AND is_enabled = false)"),
    ("agent_versions", "uq_agent_version", "UNIQUE", "(agent_id, version)"),
    ("agent_versions", "uq_agent_version_number", "UNIQUE", "(agent_id, version_number)"),
    ("custom_agents", "uq_custom_agent_tenant_sequence", "UNIQUE", "(tenant_id, sequence_number)"),
    ("workflow_versions", "uq_workflow_versions_workflow_version", "UNIQUE", "(workflow_id, version_number)"),
    ("llm_providers", "uq_llm_provider_tenant_name", "UNIQUE", "(tenant_id, name)"),
    ("usage_quotas", "uq_usage_quota_scope_type_period", "UNIQUE",
     "(scope_type, scope_id, quota_type, period, period_start)"),
    ("teams", "uq_teams_tenant_slug", "UNIQUE", "(tenant_id, slug)"),
    ("team_memberships", "uq_team_memberships_team_user", "UNIQUE", "(team_id, user_id)"),
]

# JSON keys never copied into the (immutable) audit log.
AUDIT_REDACT_KEYS = (
    "key_hash, password_hash, hashed_password, token_hash, secret, encrypted_credentials, "
    "credentials, auth_config, embedding, search_vector"
)

FUNCTIONS_SQL = [
    # --- audit_events is append-only (013) -----------------------------------------------
    """
    CREATE OR REPLACE FUNCTION prevent_audit_modification() RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION 'Audit events cannot be modified or deleted';
    END;
    $$ LANGUAGE plpgsql
    """,
    # --- redaction helper (new; the legacy audit functions copied whole rows incl. secrets) --
    f"""
    CREATE OR REPLACE FUNCTION audit_redact(j jsonb) RETURNS jsonb AS $$
        SELECT j - ARRAY[{", ".join("'" + k.strip() + "'" for k in AUDIT_REDACT_KEYS.split(","))}]
    $$ LANGUAGE sql IMMUTABLE
    """,
    # --- generic audit trigger for tables with a ``name`` column (014) ---------------------
    """
    CREATE OR REPLACE FUNCTION audit_trigger_func() RETURNS trigger AS $$
    DECLARE
        v_action text; v_changes jsonb; v_name text; v_tenant uuid;
    BEGIN
        IF TG_OP = 'INSERT' THEN
            v_action := 'CREATE'; v_tenant := NEW.tenant_id; v_name := NEW.name;
            v_changes := jsonb_build_object('after', audit_redact(to_jsonb(NEW)));
        ELSIF TG_OP = 'UPDATE' THEN
            v_action := 'UPDATE'; v_tenant := NEW.tenant_id; v_name := NEW.name;
            v_changes := jsonb_build_object('before', audit_redact(to_jsonb(OLD)),
                                            'after', audit_redact(to_jsonb(NEW)));
        ELSE
            v_action := 'DELETE'; v_tenant := OLD.tenant_id; v_name := OLD.name;
            v_changes := jsonb_build_object('before', audit_redact(to_jsonb(OLD)));
        END IF;
        IF v_tenant IS NOT NULL THEN
            INSERT INTO audit_events (tenant_id, user_id, user_email, action, resource_type,
                                      resource_id, resource_name, changes, ip_address, user_agent)
            VALUES (v_tenant,
                    NULLIF(current_setting('app.current_user_id', true), '')::uuid,
                    current_setting('app.current_user_email', true),
                    v_action, TG_TABLE_NAME,
                    CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END,
                    v_name, v_changes,
                    NULLIF(current_setting('app.client_ip', true), '')::inet,
                    current_setting('app.user_agent', true));
        END IF;
        IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp
    """,
    # --- generic audit trigger for tables without ``name`` (032) ---------------------------
    """
    CREATE OR REPLACE FUNCTION audit_memory_trigger_func() RETURNS trigger AS $$
    DECLARE
        v_action text; v_changes jsonb; v_name text; v_tenant uuid;
    BEGIN
        IF TG_OP = 'INSERT' THEN
            v_action := 'CREATE'; v_tenant := NEW.tenant_id; v_name := TG_TABLE_NAME || ':' || NEW.id::text;
            v_changes := jsonb_build_object('after', audit_redact(to_jsonb(NEW)));
        ELSIF TG_OP = 'UPDATE' THEN
            v_action := 'UPDATE'; v_tenant := NEW.tenant_id; v_name := TG_TABLE_NAME || ':' || NEW.id::text;
            v_changes := jsonb_build_object('before', audit_redact(to_jsonb(OLD)),
                                            'after', audit_redact(to_jsonb(NEW)));
        ELSE
            v_action := 'DELETE'; v_tenant := OLD.tenant_id; v_name := TG_TABLE_NAME || ':' || OLD.id::text;
            v_changes := jsonb_build_object('before', audit_redact(to_jsonb(OLD)));
        END IF;
        IF v_tenant IS NOT NULL THEN
            INSERT INTO audit_events (tenant_id, user_id, user_email, action, resource_type,
                                      resource_id, resource_name, changes, ip_address, user_agent)
            VALUES (v_tenant,
                    NULLIF(current_setting('app.current_user_id', true), '')::uuid,
                    current_setting('app.current_user_email', true),
                    v_action, TG_TABLE_NAME,
                    CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END,
                    v_name, v_changes,
                    NULLIF(current_setting('app.client_ip', true), '')::inet,
                    current_setting('app.user_agent', true));
        END IF;
        IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp
    """,
    # --- workflow execution audit (014). Column is event_metadata in the model (legacy: metadata) --
    """
    CREATE OR REPLACE FUNCTION audit_workflow_execution() RETURNS trigger AS $$
    BEGIN
        IF NEW.tenant_id IS NOT NULL THEN
            INSERT INTO audit_events (tenant_id, user_id, action, resource_type, resource_id,
                                      resource_name, changes, event_metadata)
            VALUES (NEW.tenant_id, NEW.user_id, 'EXECUTE', 'workflow_execution', NEW.id,
                    (SELECT name FROM workflows WHERE id = NEW.workflow_id),
                    jsonb_build_object('status', NEW.status, 'workflow_id', NEW.workflow_id),
                    jsonb_build_object('started_at', NEW.started_at, 'completed_at', NEW.completed_at));
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp
    """,
    # --- updated_at maintenance (015, 032, 034) --------------------------------------------
    """
    CREATE OR REPLACE FUNCTION set_updated_at_now() RETURNS trigger AS $$
    BEGIN
        NEW.updated_at = now();
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """,
    # --- only one active workflow version per workflow (035/053) -----------------------------
    """
    CREATE OR REPLACE FUNCTION ensure_single_active_version() RETURNS trigger AS $$
    BEGIN
        IF NEW.is_active = true AND (TG_OP = 'INSERT' OR OLD.is_active = false) THEN
            UPDATE workflow_versions SET is_active = false
            WHERE workflow_id = NEW.workflow_id AND id != NEW.id AND is_active = true;
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """,
    # --- background job's workflow version must belong to same tenant + workflow (052a) -------
    """
    CREATE OR REPLACE FUNCTION check_background_job_version_integrity() RETURNS trigger AS $$
    BEGIN
        IF NEW.workflow_version_id IS NOT NULL THEN
            IF NOT EXISTS (SELECT 1 FROM workflow_versions
                           WHERE id = NEW.workflow_version_id
                             AND tenant_id = NEW.tenant_id
                             AND workflow_id = NEW.workflow_id) THEN
                RAISE EXCEPTION 'workflow_version_id must belong to same tenant AND workflow as background_job';
            END IF;
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """,
]

# (trigger, table, timing/events [+ WHEN], function)
TRIGGERS = [
    ("audit_events_no_update", "audit_events", "BEFORE UPDATE", "prevent_audit_modification"),
    ("audit_events_no_delete", "audit_events", "BEFORE DELETE", "prevent_audit_modification"),
    ("audit_workflows", "workflows", "AFTER INSERT OR UPDATE OR DELETE", "audit_trigger_func"),
    ("audit_custom_agents", "custom_agents", "AFTER INSERT OR UPDATE OR DELETE", "audit_trigger_func"),
    ("audit_connectors", "connectors", "AFTER INSERT OR UPDATE OR DELETE", "audit_trigger_func"),
    ("audit_api_keys", "api_keys", "AFTER INSERT OR UPDATE OR DELETE", "audit_trigger_func"),
    ("audit_workflow_executions", "workflow_executions", "AFTER INSERT OR UPDATE", "audit_workflow_execution"),
    ("audit_user_preferences_trigger", "user_preferences", "AFTER INSERT OR UPDATE OR DELETE", "audit_memory_trigger_func"),
    ("audit_episodic_memories_trigger", "episodic_memories", "AFTER INSERT OR UPDATE OR DELETE", "audit_memory_trigger_func"),
    ("audit_pending_users_trigger", "pending_users", "AFTER INSERT OR UPDATE OR DELETE", "audit_memory_trigger_func"),
    ("audit_scheduled_workflows_trigger", "scheduled_workflows", "AFTER INSERT OR UPDATE OR DELETE", "audit_memory_trigger_func"),
    ("audit_scheduled_executions_trigger", "scheduled_workflow_executions", "AFTER INSERT OR UPDATE OR DELETE", "audit_memory_trigger_func"),
    ("audit_workflow_versions_trigger", "workflow_versions", "AFTER INSERT OR UPDATE OR DELETE", "audit_memory_trigger_func"),
    ("chat_sessions_updated_at", "chat_sessions", "BEFORE UPDATE", "set_updated_at_now"),
    ("update_user_preferences_updated_at", "user_preferences", "BEFORE UPDATE", "set_updated_at_now"),
    ("update_scheduled_workflows_timestamp", "scheduled_workflows", "BEFORE UPDATE", "set_updated_at_now"),
    ("ensure_single_active_version_trigger", "workflow_versions",
     "BEFORE INSERT OR UPDATE OF is_active", "ensure_single_active_version", "WHEN (NEW.is_active = true)"),
    ("trg_background_job_version_integrity", "background_jobs",
     "BEFORE INSERT OR UPDATE OF workflow_version_id", "check_background_job_version_integrity",
     "WHEN (NEW.workflow_version_id IS NOT NULL)"),
]

# 060: app/api/v1/analytics.py queries this view (always with an explicit tenant_id filter).
USAGE_COSTS_VIEW_SQL = """
CREATE MATERIALIZED VIEW IF NOT EXISTS usage_costs_hourly AS
SELECT tenant_id, user_id, workflow_id, provider_name, model,
       date_trunc('hour', created_at) AS hour_bucket,
       SUM(cost_usd) AS total_cost_usd,
       SUM(input_tokens) AS total_input_tokens,
       SUM(output_tokens) AS total_output_tokens,
       SUM(input_tokens + output_tokens) AS total_tokens,
       COUNT(*) AS api_call_count,
       AVG(latency_ms) AS avg_latency_ms,
       MAX(latency_ms) AS max_latency_ms
FROM llm_usage_records
GROUP BY tenant_id, user_id, workflow_id, provider_name, model, hour_bucket
"""
USAGE_COSTS_VIEW_INDEXES = [
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_usage_costs_hourly_composite ON usage_costs_hourly "
    "(tenant_id, user_id, workflow_id, provider_name, model, hour_bucket)",
    "CREATE INDEX IF NOT EXISTS idx_usage_costs_hourly_tenant_hour ON usage_costs_hourly (tenant_id, hour_bucket)",
    "CREATE INDEX IF NOT EXISTS idx_usage_costs_hourly_workflow ON usage_costs_hourly "
    "(workflow_id, hour_bucket) WHERE workflow_id IS NOT NULL",
]

# user-level RLS on top of tenant isolation (032/069): private memories + preferences are
# visible only to their owner (``app.current_user_id``), shared memories to the whole tenant.
_USER = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"


def _legacy_only_ddl() -> None:
    """Indexes, constraints, functions, triggers and the usage view that models cannot express."""
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    for name, table, rest in PERF_INDEXES:
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} {rest}")
    for name, table, rest in VECTOR_INDEXES:
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} {rest}")

    for table, name, kind, definition in CONSTRAINTS:
        clause = f"CHECK ({definition})" if kind == "CHECK" else f"UNIQUE {definition}"
        op.execute(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_constraint
                               WHERE conname = '{name}' AND conrelid = '{table}'::regclass) THEN
                    ALTER TABLE {table} ADD CONSTRAINT {name} {clause};
                END IF;
            END
            $$
            """
        )

    # 042: deleting a user must not be blocked by (or erase) their skill execution history.
    op.execute(
        """
        DO $$
        DECLARE c text;
        BEGIN
            SELECT conname INTO c FROM pg_constraint
             WHERE conrelid = 'skill_executions'::regclass AND contype = 'f'
               AND confrelid = 'users'::regclass AND confdeltype <> 'n';
            IF c IS NOT NULL THEN
                EXECUTE format('ALTER TABLE skill_executions DROP CONSTRAINT %I', c);
                ALTER TABLE skill_executions ADD CONSTRAINT fk_skill_executions_user_id
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL;
            END IF;
        END
        $$
        """
    )

    for sql in FUNCTIONS_SQL:
        op.execute(sql)
    for spec in TRIGGERS:
        name, table, when, func = spec[:4]
        cond = spec[4] if len(spec) > 4 else ""
        op.execute(f"DROP TRIGGER IF EXISTS {name} ON {table}")
        op.execute(
            f"CREATE TRIGGER {name} {when} ON {table} FOR EACH ROW {cond} EXECUTE FUNCTION {func}()"
        )

    op.execute(USAGE_COSTS_VIEW_SQL)
    for sql in USAGE_COSTS_VIEW_INDEXES:
        op.execute(sql)


def _legacy_rls_refinements() -> None:
    """Replace the systematic tenant policy with the stricter legacy ones where they existed."""
    # audit_events is append-only: SELECT + INSERT only (013). The trigger functions are
    # SECURITY DEFINER and run as the table owner, which FORCE RLS also binds, so the owner
    # gets an INSERT-only policy as well (a superuser/BYPASSRLS owner ignores it).
    op.execute('DROP POLICY IF EXISTS tenant_isolation_audit_events ON "audit_events"')
    op.execute(
        f'CREATE POLICY tenant_isolation_audit_events ON "audit_events" FOR SELECT TO oniva_app '
        f"USING (tenant_id = {TENANT_EXPR})"
    )
    op.execute(
        f'CREATE POLICY audit_insert ON "audit_events" FOR INSERT TO oniva_app '
        f"WITH CHECK (tenant_id = {TENANT_EXPR})"
    )
    op.execute(
        'CREATE POLICY audit_definer_insert ON "audit_events" FOR INSERT TO CURRENT_USER '
        "WITH CHECK (true)"
    )

    # user_preferences: owner-only (032).
    op.execute('DROP POLICY IF EXISTS tenant_isolation_user_preferences ON "user_preferences"')
    op.execute(
        'CREATE POLICY user_isolation_prefs ON "user_preferences" FOR ALL TO oniva_app '
        f"USING (tenant_id = {TENANT_EXPR} AND user_id = {_USER}) "
        f"WITH CHECK (tenant_id = {TENANT_EXPR} AND user_id = {_USER})"
    )

    # episodic_memories: private rows need tenant AND user; shared rows need tenant (069).
    op.execute('DROP POLICY IF EXISTS tenant_isolation_episodic_memories ON "episodic_memories"')
    op.execute(
        'CREATE POLICY user_private_episodic ON "episodic_memories" FOR ALL TO oniva_app '
        f"USING (visibility = 'private' AND tenant_id = {TENANT_EXPR} AND user_id = {_USER}) "
        f"WITH CHECK (visibility = 'private' AND tenant_id = {TENANT_EXPR} AND user_id = {_USER})"
    )
    op.execute(
        'CREATE POLICY tenant_shared_episodic ON "episodic_memories" FOR ALL TO oniva_app '
        f"USING (visibility = 'shared' AND tenant_id = {TENANT_EXPR}) "
        f"WITH CHECK (visibility = 'shared' AND tenant_id = {TENANT_EXPR})"
    )


def _import_models():
    from app.models import Base  # noqa: WPS433 - imports every model module

    return Base


def upgrade() -> None:
    bind = op.get_bind()
    Base = _import_models()

    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    Base.metadata.create_all(bind=bind)

    # Legacy-only objects first, so the grants below also cover the usage view.
    _legacy_only_ddl()

    # Role: no password, no DB-name literal. Password is provisioned out-of-band.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'oniva_app') THEN
                CREATE ROLE oniva_app WITH LOGIN NOSUPERUSER NOBYPASSRLS;
            END IF;
        END
        $$;
        """
    )
    op.execute("GRANT USAGE ON SCHEMA public TO oniva_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO oniva_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO oniva_app")
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO oniva_app"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO oniva_app"
    )

    # Systematic RLS: every table that has a tenant_id column.
    tenant_tables = sorted(
        name for name, table in Base.metadata.tables.items() if "tenant_id" in table.c
    )
    for name in tenant_tables:
        op.execute(f'ALTER TABLE "{name}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{name}" FORCE ROW LEVEL SECURITY')
        if name in SHARED_READ_TABLES:
            using = f"tenant_id IS NULL OR tenant_id = {TENANT_EXPR}"
        else:
            using = f"tenant_id = {TENANT_EXPR}"
        op.execute(
            f'CREATE POLICY tenant_isolation_{name} ON "{name}" FOR ALL TO oniva_app '
            f"USING ({using}) WITH CHECK (tenant_id = {TENANT_EXPR})"
        )

    # tenants is keyed on id, not tenant_id. Read-own only; creation is a system-role job.
    op.execute('ALTER TABLE "tenants" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "tenants" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'CREATE POLICY tenant_read_own ON "tenants" FOR SELECT TO oniva_app '
        f"USING (id = {TENANT_EXPR})"
    )

    _legacy_rls_refinements()


def downgrade() -> None:
    """DEV-ONLY: drops the whole schema (all data lost). The oniva_app role is kept."""
    bind = op.get_bind()
    Base = _import_models()
    op.execute("DROP MATERIALIZED VIEW IF EXISTS usage_costs_hourly")
    Base.metadata.drop_all(bind=bind)  # also drops the triggers/indexes/constraints with the tables
    for fn in (
        "prevent_audit_modification()", "audit_redact(jsonb)", "audit_trigger_func()",
        "audit_memory_trigger_func()", "audit_workflow_execution()", "set_updated_at_now()",
        "ensure_single_active_version()", "check_background_job_version_integrity()",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS {fn}")
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM oniva_app"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE USAGE, SELECT ON SEQUENCES FROM oniva_app"
    )
