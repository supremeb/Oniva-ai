"""Alembic against an empty Postgres as the owner role (baseline 100_baseline)."""

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

from tests.postgres.conftest import BACKEND_DIR, run_alembic


def test_single_alembic_head():
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert heads == ["103_chat_read_model"], heads


def _scalar(url, sql):
    engine = create_engine(url)
    try:
        with engine.connect() as c:
            return c.execute(text(sql)).scalar()
    finally:
        engine.dispose()


def test_upgrade_head_on_empty_db(pg_migrated_url):
    res = run_alembic(pg_migrated_url, "upgrade", "head")
    assert res.returncode == 0, res.stderr[-2000:]
    assert _scalar(pg_migrated_url, "SELECT to_regclass('public.sandbox_executions')")
    assert _scalar(pg_migrated_url, "SELECT 1 FROM pg_extension WHERE extname='vector'")
    assert _scalar(pg_migrated_url, "SELECT version_num FROM alembic_version") == "103_chat_read_model"
    assert _scalar(
        pg_migrated_url,
        "SELECT character_maximum_length FROM information_schema.columns "
        "WHERE table_name='alembic_version' AND column_name='version_num'",
    ) == 255
    # role exists, is not privileged, and has no hard-coded password
    row = _scalar(pg_migrated_url,
                  "SELECT (NOT rolsuper AND NOT rolbypassrls) FROM pg_roles WHERE rolname='oniva_app'")
    assert row is True


def test_downgrade_base_then_upgrade(pg_migrated_url):
    assert run_alembic(pg_migrated_url, "upgrade", "head").returncode == 0
    down = run_alembic(pg_migrated_url, "downgrade", "base")
    assert down.returncode == 0, down.stderr[-2000:]
    assert _scalar(pg_migrated_url, "SELECT to_regclass('public.users')") is None
    up = run_alembic(pg_migrated_url, "upgrade", "head")
    assert up.returncode == 0, up.stderr[-2000:]
    assert _scalar(pg_migrated_url, "SELECT to_regclass('public.users')")


def test_every_tenant_table_has_forced_rls_and_policy(pg_migrated_url):
    assert run_alembic(pg_migrated_url, "upgrade", "head").returncode == 0
    engine = create_engine(pg_migrated_url)
    try:
        with engine.connect() as c:
            rows = c.execute(text(
                """
                SELECT cl.relname, cl.relrowsecurity, cl.relforcerowsecurity,
                       (SELECT count(*) FROM pg_policies p
                         WHERE p.schemaname='public' AND p.tablename=cl.relname)
                FROM pg_class cl
                JOIN pg_namespace n ON n.oid = cl.relnamespace AND n.nspname='public'
                WHERE cl.relkind = 'r' AND EXISTS (
                    SELECT 1 FROM information_schema.columns col
                    WHERE col.table_schema='public' AND col.table_name=cl.relname
                      AND col.column_name='tenant_id')
                """
            )).all()
            assert len(rows) > 50, "expected the full set of tenant tables"
            bad = [r[0] for r in rows if not (r[1] and r[2] and r[3] >= 1)]
            assert not bad, f"tenant_id tables missing RLS/FORCE/policy: {bad}"
            # tenants (keyed on id) is covered too, and no permissive no-tenant policy on users
            t = c.execute(text(
                "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE relname='tenants'"
            )).scalar()
            assert t is True
            permissive = c.execute(text(
                "SELECT count(*) FROM pg_policies WHERE tablename='users' "
                "AND qual ILIKE '%IS NULL%' AND policyname <> 'tenant_isolation_users'"
            )).scalar()
            assert permissive == 0
    finally:
        engine.dispose()


# --------------------------------------------------------------------------------------
# Legacy-only DDL carried by the baseline (see migrations/versions_archive/README.md)
# --------------------------------------------------------------------------------------

T1 = "11111111-1111-1111-1111-111111111111"
U1 = "22222222-2222-2222-2222-222222222222"
U2 = "44444444-4444-4444-4444-444444444444"
W1 = "33333333-3333-3333-3333-333333333333"


def _exec(url, *statements):
    engine = create_engine(url)
    try:
        with engine.begin() as c:
            for s in statements:
                c.execute(text(s))
    finally:
        engine.dispose()


def _seed_tenant(url):
    _exec(
        url,
        f"INSERT INTO tenants (id, name, slug) VALUES ('{T1}', 't', 't1')",
        f"INSERT INTO users (id, tenant_id, email, name) VALUES ('{U1}', '{T1}', 'a@example.com', 'a')",
        f"INSERT INTO users (id, tenant_id, email, name) VALUES ('{U2}', '{T1}', 'b@example.com', 'b')",
    )


def test_gin_fulltext_indexes_created_by_baseline(pg_migrated_url):
    """Replaces the 072_add_gin_index_for_bm25 migration file (archived): RAG BM25 search needs
    ``to_tsvector('english', content)`` GIN indexes on documents and knowledge_chunks."""
    assert run_alembic(pg_migrated_url, "upgrade", "head").returncode == 0
    for name, table in (("ix_knowledge_chunks_content_gin", "knowledge_chunks"),
                        ("ix_documents_content_gin", "documents")):
        defn = _scalar(pg_migrated_url,
                       f"SELECT indexdef FROM pg_indexes WHERE schemaname='public' AND indexname='{name}'")
        assert defn, f"{name} missing"
        assert f"ON public.{table}" in defn and "USING gin" in defn
        assert "to_tsvector('english'::regconfig, content)" in defn


def test_legacy_only_objects_present(pg_migrated_url):
    assert run_alembic(pg_migrated_url, "upgrade", "head").returncode == 0
    engine = create_engine(pg_migrated_url)
    try:
        with engine.connect() as c:
            q = lambda sql: {r[0] for r in c.execute(text(sql))}  # noqa: E731
            funcs = q("SELECT proname FROM pg_proc WHERE pronamespace='public'::regnamespace")
            assert {"prevent_audit_modification", "audit_trigger_func", "audit_memory_trigger_func",
                    "audit_workflow_execution", "ensure_single_active_version",
                    "check_background_job_version_integrity", "set_updated_at_now"} <= funcs
            trig = q("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal")
            assert {"audit_events_no_update", "audit_events_no_delete", "audit_workflows",
                    "audit_workflow_executions", "ensure_single_active_version_trigger",
                    "trg_background_job_version_integrity", "chat_sessions_updated_at"} <= trig
            idx = q("SELECT indexname FROM pg_indexes WHERE schemaname='public'")
            assert {"idx_background_jobs_job_type_trgm", "idx_episodic_memories_search_vector",
                    "idx_workflows_user_updated", "idx_documents_kb_id", "idx_scheduled_workflows_poll",
                    "idx_usage_costs_hourly_composite"} <= idx
            assert c.execute(text("SELECT to_regclass('public.usage_costs_hourly')")).scalar()
            cons = q("SELECT conname FROM pg_constraint WHERE connamespace='public'::regnamespace")
            assert {"ck_background_jobs_trigger", "chk_auth_method", "chk_status_enabled_consistency",
                    "uq_workflow_versions_workflow_version", "uq_agent_version_number"} <= cons
    finally:
        engine.dispose()


def test_audit_events_immutable(pg_migrated_url):
    """013: audit_events rejects UPDATE and DELETE for every role (trigger), not just via RLS."""
    assert run_alembic(pg_migrated_url, "upgrade", "head").returncode == 0
    _seed_tenant(pg_migrated_url)
    _exec(pg_migrated_url,
          "INSERT INTO audit_events (tenant_id, action, resource_type, resource_id) "
          f"VALUES ('{T1}', 'CREATE', 'thing', gen_random_uuid())")
    for stmt in ("UPDATE audit_events SET action = 'X'", "DELETE FROM audit_events"):
        with pytest.raises(Exception, match="cannot be modified or deleted"):
            _exec(pg_migrated_url, stmt)
    assert _scalar(pg_migrated_url, "SELECT count(*) FROM audit_events") == 1


def test_audit_triggers_record_changes_and_redact_secrets(pg_migrated_url):
    assert run_alembic(pg_migrated_url, "upgrade", "head").returncode == 0
    _seed_tenant(pg_migrated_url)
    _exec(
        pg_migrated_url,
        "INSERT INTO workflows (id, tenant_id, user_id, name, spec) "
        f"VALUES ('{W1}', '{T1}', '{U1}', 'wf', '{{}}')",
        f"UPDATE workflows SET name = 'wf2' WHERE id = '{W1}'",
        "INSERT INTO api_keys (id, tenant_id, user_id, provider, name, encrypted_key, key_hash) "
        f"VALUES (gen_random_uuid(), '{T1}', '{U1}', 'openai', 'k', 'enc', 'SECRETHASH')",
    )
    assert _scalar(pg_migrated_url,
                   "SELECT string_agg(action || ':' || resource_type, ',' ORDER BY created_at, action) "
                   "FROM audit_events").startswith("CREATE:")
    assert _scalar(pg_migrated_url,
                   f"SELECT count(*) FROM audit_events WHERE resource_type='workflows' AND resource_name='wf2'") == 1
    assert _scalar(pg_migrated_url,
                   "SELECT count(*) FROM audit_events WHERE changes::text LIKE '%SECRETHASH%'") == 0
    assert _scalar(pg_migrated_url,
                   "SELECT count(*) FROM audit_events WHERE resource_type='api_keys'") == 1


def test_single_active_workflow_version_trigger(pg_migrated_url):
    assert run_alembic(pg_migrated_url, "upgrade", "head").returncode == 0
    _seed_tenant(pg_migrated_url)
    _exec(pg_migrated_url,
          "INSERT INTO workflows (id, tenant_id, user_id, name, spec) "
          f"VALUES ('{W1}', '{T1}', '{U1}', 'wf', '{{}}')")
    for n in (1, 2):
        _exec(pg_migrated_url,
              "INSERT INTO workflow_versions (id, workflow_id, tenant_id, version_number, spec, is_active) "
              f"VALUES (gen_random_uuid(), '{W1}', '{T1}', {n}, '{{}}', true)")
    assert _scalar(pg_migrated_url,
                   f"SELECT count(*) FROM workflow_versions WHERE workflow_id='{W1}' AND is_active") == 1
    assert _scalar(pg_migrated_url,
                   f"SELECT version_number FROM workflow_versions WHERE workflow_id='{W1}' AND is_active") == 2


def test_background_job_trigger_check_allows_webhook(pg_migrated_url):
    assert run_alembic(pg_migrated_url, "upgrade", "head").returncode == 0
    _seed_tenant(pg_migrated_url)

    def job(trigger):
        return ("INSERT INTO background_jobs (id, tenant_id, user_id, job_type, status, trigger, "
                f"progress, execution_trace) VALUES (gen_random_uuid(), '{T1}', '{U1}', 'x', 'pending', "
                f"'{trigger}', 0, '[]')")
    _exec(pg_migrated_url, job("webhook"), job("manual"))
    with pytest.raises(Exception, match="ck_background_jobs_trigger"):
        _exec(pg_migrated_url, job("bogus"))


def test_memory_rls_is_user_scoped(pg_migrated_url):
    """032/069: private episodic memories are visible only to their owner; shared to the tenant."""
    assert run_alembic(pg_migrated_url, "upgrade", "head").returncode == 0
    _seed_tenant(pg_migrated_url)
    _exec(pg_migrated_url,
          "INSERT INTO episodic_memories (id, tenant_id, user_id, agent_name, action_type, "
          f"context_decision, visibility) VALUES (gen_random_uuid(), '{T1}', '{U1}', 'a', 'other', 'p', 'private')",
          "INSERT INTO episodic_memories (id, tenant_id, user_id, agent_name, action_type, "
          f"context_decision, visibility) VALUES (gen_random_uuid(), '{T1}', '{U1}', 'a', 'other', 's', 'shared')")

    def visible_as(user):
        engine = create_engine(pg_migrated_url)
        try:
            with engine.begin() as c:
                c.execute(text("SET LOCAL ROLE oniva_app"))
                c.execute(text(f"SELECT set_config('app.current_tenant', '{T1}', true)"))
                c.execute(text(f"SELECT set_config('app.current_user_id', '{user}', true)"))
                return c.execute(text("SELECT count(*) FROM episodic_memories")).scalar()
        finally:
            engine.dispose()
    assert visible_as(U1) == 2
    assert visible_as(U2) == 1  # only the shared one
