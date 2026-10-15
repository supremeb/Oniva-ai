"""U2 - engine interface tables: tool_invocations, engine_fences, engine_conversations, ledger key.

Runs on the Alembic-migrated database (needs pgvector; skipped locally without it, CI runs it).
"""
from __future__ import annotations

import uuid

import psycopg2
import pytest
from psycopg2 import errors
from sqlalchemy import create_engine, text

from tests.postgres.conftest import run_alembic
from tests.postgres.test_rls import PW, TA, TB, _role_url

USER_A, USER_B = str(uuid.uuid4()), str(uuid.uuid4())
PROJ_A, PROJ_B = str(uuid.uuid4()), str(uuid.uuid4())


@pytest.fixture(scope="module")
def engine_db(pg_server, pg_has_vector):
    if not pg_has_vector:
        pytest.skip("pgvector extension not available for this Postgres; CI runs this tier fully")
    pg_server.recreate_db("oniva_engine_tables")
    owner = pg_server.url("oniva_engine_tables")
    res = run_alembic(owner, "upgrade", "head")
    assert res.returncode == 0, res.stderr[-3000:]
    eng = create_engine(owner)
    with eng.begin() as c:
        c.execute(text(f"ALTER ROLE oniva_app PASSWORD '{PW}'"))
        c.execute(text(f"ALTER ROLE oniva_system PASSWORD '{PW}'"))
        c.execute(text("SET session_replication_role = replica"))  # skip FK triggers for seeding
        for t in (TA, TB):
            c.execute(text("INSERT INTO tenants (id, name, slug) VALUES (:i, :n, :n)"), {"i": t, "n": t[:2]})
        for t, k, p in ((TA, "a", "a"), (TB, "b", "b")):
            c.execute(text(
                "INSERT INTO tool_invocations (id, tenant_id, idempotency_key, session_id, tool, args_hash) "
                "VALUES (gen_random_uuid(), :t, :k, 's1', 'write_file', 'h')"), {"t": t, "k": f"key-{k}"})
            c.execute(text(
                "INSERT INTO engine_conversations (conversation_id, tenant_id, project_id, user_id, session_id) "
                "VALUES (gen_random_uuid(), :t, gen_random_uuid(), gen_random_uuid(), 's1')"), {"t": t})
    eng.dispose()
    return {"owner": owner}


def _conn(db, role="oniva_app"):
    return psycopg2.connect(_role_url(db["owner"], role))


def _tenant_conn(db, tenant):
    conn = _conn(db)
    cur = conn.cursor()
    cur.execute("SELECT set_config('app.current_tenant', %s, false)", (tenant,))
    return conn, cur


@pytest.mark.parametrize("table", ["tool_invocations", "engine_conversations"])
def test_no_tenant_context_sees_zero_rows(engine_db, table):
    conn = _conn(engine_db)
    cur = conn.cursor()
    cur.execute(f"SELECT count(*) FROM {table}")
    assert cur.fetchone()[0] == 0
    conn.close()


@pytest.mark.parametrize("table", ["tool_invocations", "engine_conversations"])
def test_tenant_a_never_sees_tenant_b(engine_db, table):
    for me, other in ((TA, TB), (TB, TA)):
        conn, cur = _tenant_conn(engine_db, me)
        cur.execute(f"SELECT tenant_id::text FROM {table}")
        assert [r[0] for r in cur.fetchall()] == [me]
        conn.close()


def test_with_check_blocks_cross_tenant_write(engine_db):
    conn, cur = _tenant_conn(engine_db, TA)
    with pytest.raises(psycopg2.Error) as ei:
        cur.execute(
            "INSERT INTO tool_invocations (id, tenant_id, idempotency_key, session_id, tool, args_hash) "
            "VALUES (gen_random_uuid(), %s, 'x-cross', 's', 't', 'h')", (TB,))
    assert "row-level security" in str(ei.value)
    conn.close()


def test_duplicate_idempotency_key_rejected(engine_db):
    conn, cur = _tenant_conn(engine_db, TA)
    with pytest.raises(errors.UniqueViolation):
        cur.execute(
            "INSERT INTO tool_invocations (id, tenant_id, idempotency_key, session_id, tool, args_hash) "
            "VALUES (gen_random_uuid(), %s, 'key-a', 's', 't', 'h')", (TA,))
    conn.close()


def test_conflict_claim_returns_no_row_and_status_defaults_in_flight(engine_db):
    conn, cur = _tenant_conn(engine_db, TA)
    ins = ("INSERT INTO tool_invocations (id, tenant_id, idempotency_key, session_id, tool, args_hash) "
           "VALUES (gen_random_uuid(), %s, %s, 's', 't', 'h') ON CONFLICT (idempotency_key) DO NOTHING "
           "RETURNING status")
    cur.execute(ins, (TA, "claim-1"))
    assert cur.fetchone() == ("in_flight",)
    cur.execute(ins, (TA, "claim-1"))
    assert cur.fetchone() is None
    conn.rollback()
    conn.close()


def test_usage_request_id_unique_but_nulls_allowed(engine_db):
    cols = ("id, tenant_id, provider_name, model, input_tokens, output_tokens, cost_usd, latency_ms, request_id")
    ins = f"INSERT INTO llm_usage_records ({cols}) VALUES (gen_random_uuid(), %s, 'p', 'm', 1, 1, 0, 1, %s)"
    conn, cur = _tenant_conn(engine_db, TA)
    cur.execute(ins, (TA, None))
    cur.execute(ins, (TA, None))  # several NULLs allowed
    cur.execute(ins, (TA, "r1"))
    conn.commit()
    with pytest.raises(errors.UniqueViolation):
        cur.execute(ins, (TA, "r1"))
    conn.rollback()
    cur.execute("SELECT set_config('app.current_tenant', %s, false)", (TB,))
    cur.execute(ins, (TB, "r1"))  # same request id, other tenant: fine
    conn.commit()
    cur.execute("SELECT set_config('app.current_tenant', %s, false)", (TA,))
    cur.execute("INSERT INTO llm_usage_records (id, tenant_id, provider_name, model, input_tokens, "
                "output_tokens, cost_usd, latency_ms, request_id, run_id, status) "
                "VALUES (gen_random_uuid(), %s, 'p', 'm', 1, 1, 0, 1, 'r2', 'run', 'unpriced') "
                "ON CONFLICT DO NOTHING", (TA,))
    conn.commit()
    conn.close()


def test_app_role_cannot_touch_engine_fences(engine_db):
    conn = _conn(engine_db)
    cur = conn.cursor()
    with pytest.raises(errors.InsufficientPrivilege):
        cur.execute("SELECT * FROM engine_fences")
    conn.rollback()
    with pytest.raises(errors.InsufficientPrivilege):
        cur.execute("INSERT INTO engine_fences (session_id, high_water) VALUES ('s', 1)")
    conn.close()


def test_system_role_raises_fence_with_greatest(engine_db):
    conn = _conn(engine_db, "oniva_system")
    cur = conn.cursor()
    up = ("INSERT INTO engine_fences (session_id, high_water) VALUES ('sess', %s) "
          "ON CONFLICT (session_id) DO UPDATE SET high_water = GREATEST(engine_fences.high_water, EXCLUDED.high_water), "
          "updated_at = now() RETURNING high_water")
    cur.execute(up, (7,))
    assert cur.fetchone()[0] == 7
    cur.execute(up, (6,))
    assert cur.fetchone()[0] == 7
    conn.rollback()
    conn.close()


def test_fences_and_new_tables_are_forced_rls_with_policy(engine_db):
    conn = psycopg2.connect(engine_db["owner"])
    cur = conn.cursor()
    cur.execute("SELECT relname, relrowsecurity AND relforcerowsecurity FROM pg_class "
                "WHERE relname IN ('tool_invocations','engine_fences','engine_conversations')")
    assert dict(cur.fetchall()) == {"tool_invocations": True, "engine_fences": True,
                                    "engine_conversations": True}
    cur.execute("SELECT tablename FROM pg_policies WHERE tablename IN "
                "('tool_invocations','engine_fences','engine_conversations')")
    assert sorted(r[0] for r in cur.fetchall()) == ["engine_conversations", "engine_fences", "tool_invocations"]
    conn.close()


def test_downgrade_then_upgrade_this_revision(engine_db):
    owner = engine_db["owner"]
    down = run_alembic(owner, "downgrade", "101_system_role")
    assert down.returncode == 0, down.stderr[-2000:]
    eng = create_engine(owner)
    with eng.connect() as c:
        assert c.execute(text("SELECT to_regclass('public.tool_invocations')")).scalar() is None
        assert c.execute(text("SELECT count(*) FROM information_schema.columns WHERE "
                              "table_name='llm_usage_records' AND column_name='request_id'")).scalar() == 0
    up = run_alembic(owner, "upgrade", "head")
    assert up.returncode == 0, up.stderr[-2000:]
    with eng.connect() as c:
        assert c.execute(text("SELECT to_regclass('public.tool_invocations')")).scalar()
        assert c.execute(text("SELECT to_regclass('public.engine_fences')")).scalar()
    eng.dispose()
