"""U1 - compliance_audit_logs is append-only, with the one narrow user-deletion UPDATE and the
two guarded purge modes (KTD9). Runs on the Alembic-migrated database as the real roles.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import psycopg2
import pytest
from psycopg2 import errors
from sqlalchemy import create_engine, text

from tests.postgres.conftest import pg_has_vector, pg_server, run_alembic  # noqa: F401
from tests.postgres.test_rls import PW, TA, TB, _role_url, seed_row


@pytest.fixture(scope="module")
def imm_db(pg_server, pg_has_vector):
    if not pg_has_vector:
        pytest.skip("pgvector extension not available for this Postgres; CI runs this tier fully")
    pg_server.recreate_db("oniva_audit_imm")
    owner = pg_server.url("oniva_audit_imm")
    res = run_alembic(owner, "upgrade", "head")
    assert res.returncode == 0, res.stderr[-3000:]
    eng = create_engine(owner)
    with eng.begin() as c:
        c.execute(text(f"ALTER ROLE oniva_app PASSWORD '{PW}'"))
        c.execute(text(f"ALTER ROLE oniva_system PASSWORD '{PW}'"))
        for t, slug in ((TA, "ta"), (TB, "tb")):
            c.execute(text("INSERT INTO tenants (id, name, slug) VALUES (:i, :n, :s)"),
                      {"i": t, "n": slug, "s": slug})
    eng.dispose()
    return owner


@pytest.fixture(autouse=True)
def clean_audit(imm_db):
    eng = create_engine(imm_db)
    with eng.begin() as c:
        c.execute(text("ALTER TABLE compliance_audit_logs DISABLE TRIGGER compliance_audit_logs_immutable"))
        c.execute(text("DELETE FROM compliance_audit_logs"))
        c.execute(text("ALTER TABLE compliance_audit_logs ENABLE TRIGGER compliance_audit_logs_immutable"))
    eng.dispose()


def _insert_audit(url, tenant, user_id=None, retention_until=None, seq=None) -> str:
    entry_id = str(uuid.uuid4())
    eng = create_engine(url)
    with eng.begin() as c:
        c.execute(text(
            "INSERT INTO compliance_audit_logs (id, tenant_id, agent_id, user_id, action, decision, "
            "rationale, data_sources, tools_called, signature, previous_hash, entry_hash, extra_metadata, "
            "retention_until, seq, signing_key_id, actor_email, actor_name) VALUES "
            "(:id, :t, 'agent', :u, 'agent_decision', 'd', 'r', '{}', '{}', 'sig', NULL, 'hash', "
            "'{}'::jsonb, :ru, :seq, 'k1', 'a@x.io', 'A')"),
            {"id": entry_id, "t": tenant, "u": user_id, "seq": seq,
             "ru": retention_until or datetime.now(timezone.utc) + timedelta(days=365)})
    eng.dispose()
    return entry_id


def _conn(url, role="oniva_app"):
    return psycopg2.connect(_role_url(url, role))


def _tenant_conn(url, tenant=TA, role="oniva_app"):
    conn = _conn(url, role)
    cur = conn.cursor()
    cur.execute("SELECT set_config('app.current_tenant', %s, false)", (tenant,))
    return conn, cur


def test_table_owner_is_not_app_role_and_grants(imm_db):
    conn = psycopg2.connect(imm_db)
    cur = conn.cursor()
    cur.execute("SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid='compliance_audit_logs'::regclass")
    assert cur.fetchone()[0] != "oniva_app"
    cur.execute(
        "SELECT has_table_privilege('oniva_app','compliance_audit_logs','INSERT'), "
        "has_table_privilege('oniva_app','compliance_audit_logs','UPDATE'), "
        "has_table_privilege('oniva_app','compliance_audit_logs','DELETE'), "
        "has_table_privilege('oniva_app','compliance_audit_logs','TRUNCATE'), "
        "has_table_privilege('oniva_system','compliance_audit_logs','DELETE')")
    assert cur.fetchone() == (True, False, False, False, True)
    conn.close()


def test_app_role_can_insert_but_not_update_or_delete(imm_db):
    _insert_audit(imm_db, TA)
    conn, cur = _tenant_conn(imm_db)
    with pytest.raises(errors.InsufficientPrivilege):
        cur.execute("UPDATE compliance_audit_logs SET decision = 'x'")
    conn.rollback()
    with pytest.raises(errors.InsufficientPrivilege):
        cur.execute("DELETE FROM compliance_audit_logs")
    conn.rollback()
    conn.close()


def test_owner_update_of_any_other_shape_is_rejected(imm_db):
    entry_id = _insert_audit(imm_db, TA)
    conn = psycopg2.connect(imm_db)
    cur = conn.cursor()
    with pytest.raises(psycopg2.Error, match="append-only"):
        cur.execute("UPDATE compliance_audit_logs SET decision = 'tampered' WHERE id = %s", (entry_id,))
    conn.rollback()
    conn.close()


def test_owner_delete_without_a_purge_mode_is_rejected(imm_db):
    _insert_audit(imm_db, TA)
    conn = psycopg2.connect(imm_db)
    cur = conn.cursor()
    with pytest.raises(psycopg2.Error, match="append-only"):
        cur.execute("DELETE FROM compliance_audit_logs")
    conn.rollback()
    conn.close()


@pytest.mark.parametrize("role", ["oniva_app", "oniva_system", None])
def test_truncate_always_fails(imm_db, role):
    _insert_audit(imm_db, TA)
    conn = psycopg2.connect(imm_db) if role is None else _conn(imm_db, role)
    cur = conn.cursor()
    if role == "oniva_system":
        cur.execute("SET LOCAL oniva.audit_purge = 'retention'")
    with pytest.raises(psycopg2.Error):
        cur.execute("TRUNCATE compliance_audit_logs")
    conn.rollback()
    conn.close()


def test_deleting_a_user_sets_null_and_keeps_the_snapshot(imm_db):
    uid = str(uuid.uuid4())
    eng = create_engine(imm_db)
    with eng.begin() as c:
        assert seed_row(c, "users", TA, {"id": f"'{uid}'"})
    eng.dispose()
    entry_id = _insert_audit(imm_db, TA, user_id=uid)
    before = _fetch(imm_db, entry_id)

    conn, cur = _tenant_conn(imm_db)
    cur.execute("DELETE FROM users WHERE id = %s", (uid,))
    conn.commit()
    conn.close()

    after = _fetch(imm_db, entry_id)
    assert after[0] is None                      # user_id -> NULL
    assert after[1] == before[1]                 # actor_email unchanged
    assert after[2] == before[2]                 # actor_name unchanged
    assert after[3] == before[3]                 # entry_hash unchanged
    assert after[4] == before[4]                 # signature unchanged


def _fetch(url, entry_id):
    eng = create_engine(url)
    with eng.connect() as c:
        return c.execute(text(
            "SELECT user_id, actor_email, actor_name, entry_hash, signature "
            "FROM compliance_audit_logs WHERE id = :i"), {"i": entry_id}).one()


def test_user_id_to_null_with_another_change_is_rejected(imm_db):
    uid = str(uuid.uuid4())
    eng = create_engine(imm_db)
    with eng.begin() as c:
        assert seed_row(c, "users", TA, {"id": f"'{uid}'"})
    eng.dispose()
    entry_id = _insert_audit(imm_db, TA, user_id=uid)
    conn = psycopg2.connect(imm_db)
    cur = conn.cursor()
    with pytest.raises(psycopg2.Error, match="append-only"):
        cur.execute("UPDATE compliance_audit_logs SET user_id = NULL, decision = 'x' WHERE id = %s",
                    (entry_id,))
    conn.rollback()
    conn.close()


def test_user_id_from_null_to_a_value_is_rejected(imm_db):
    uid = str(uuid.uuid4())
    eng = create_engine(imm_db)
    with eng.begin() as c:
        assert seed_row(c, "users", TA, {"id": f"'{uid}'"})
    eng.dispose()
    entry_id = _insert_audit(imm_db, TA, user_id=None)
    conn = psycopg2.connect(imm_db)
    cur = conn.cursor()
    with pytest.raises(psycopg2.Error, match="append-only"):
        cur.execute("UPDATE compliance_audit_logs SET user_id = %s WHERE id = %s", (uid, entry_id))
    conn.rollback()
    conn.close()


def test_retention_purge_mode(imm_db):
    past = _insert_audit(imm_db, TA, retention_until=datetime.now(timezone.utc) - timedelta(days=1))
    future = _insert_audit(imm_db, TA, retention_until=datetime.now(timezone.utc) + timedelta(days=365))
    conn = _conn(imm_db, "oniva_system")
    cur = conn.cursor()
    cur.execute("SET LOCAL oniva.audit_purge = 'retention'")
    with pytest.raises(psycopg2.Error, match="retention"):
        cur.execute("DELETE FROM compliance_audit_logs WHERE id = %s", (future,))
    conn.rollback()
    cur.execute("SET LOCAL oniva.audit_purge = 'retention'")
    cur.execute("DELETE FROM compliance_audit_logs WHERE id = %s", (past,))
    conn.commit()
    conn.close()
    assert _exists(imm_db, past) is False and _exists(imm_db, future) is True


def test_retention_purge_requires_the_system_session(imm_db):
    past = _insert_audit(imm_db, TA, retention_until=datetime.now(timezone.utc) - timedelta(days=1))
    conn = psycopg2.connect(imm_db)  # owner/superuser, session_user != oniva_system
    cur = conn.cursor()
    cur.execute("SET LOCAL oniva.audit_purge = 'retention'")
    with pytest.raises(psycopg2.Error, match="append-only"):
        cur.execute("DELETE FROM compliance_audit_logs WHERE id = %s", (past,))
    conn.rollback()
    conn.close()


def test_retention_purge_from_app_role_is_refused(imm_db):
    past = _insert_audit(imm_db, TA, retention_until=datetime.now(timezone.utc) - timedelta(days=1))
    conn, cur = _tenant_conn(imm_db, role="oniva_app")
    cur.execute("SET LOCAL oniva.audit_purge = 'retention'")
    with pytest.raises(errors.InsufficientPrivilege):
        cur.execute("DELETE FROM compliance_audit_logs WHERE id = %s", (past,))
    conn.rollback()
    conn.close()


def _seed_tenant(url, tenant_id, slug) -> None:
    eng = create_engine(url)
    with eng.begin() as c:
        c.execute(text("INSERT INTO tenants (id, name, slug) VALUES (:i, :n, :s)"),
                  {"i": tenant_id, "n": slug, "s": slug})
    eng.dispose()


def test_tenant_delete_mode(imm_db):
    t1, t2 = str(uuid.uuid4()), str(uuid.uuid4())
    _seed_tenant(imm_db, t1, "purge-t1")
    _seed_tenant(imm_db, t2, "purge-t2")
    a = _insert_audit(imm_db, t1)
    b = _insert_audit(imm_db, t2)
    conn = _conn(imm_db, "oniva_system")
    cur = conn.cursor()
    # without the GUC the cascade is rejected
    with pytest.raises(psycopg2.Error, match="append-only"):
        cur.execute("DELETE FROM tenants WHERE id = %s", (t1,))
    conn.rollback()
    # naming the tenant lets the cascade through, for that tenant only
    cur.execute("SET LOCAL oniva.audit_purge = 'tenant_delete'")
    cur.execute("SELECT set_config('oniva.audit_purge_tenant', %s, true)", (t1,))
    cur.execute("DELETE FROM tenants WHERE id = %s", (t1,))
    conn.commit()
    assert _exists(imm_db, a) is False and _exists(imm_db, b) is True
    # the wrong tenant is still refused
    cur.execute("SET LOCAL oniva.audit_purge = 'tenant_delete'")
    cur.execute("SELECT set_config('oniva.audit_purge_tenant', %s, true)", (t1,))
    with pytest.raises(psycopg2.Error, match="append-only"):
        cur.execute("DELETE FROM tenants WHERE id = %s", (t2,))
    conn.rollback()
    conn.close()


def test_tenant_delete_from_app_role_cannot_delete_the_tenant(imm_db):
    t1 = str(uuid.uuid4())
    _seed_tenant(imm_db, t1, "purge-app")
    _insert_audit(imm_db, t1)
    conn, cur = _tenant_conn(imm_db, tenant=t1, role="oniva_app")
    try:
        cur.execute("DELETE FROM tenants WHERE id = %s", (t1,))
        conn.commit()
    except psycopg2.Error:
        conn.rollback()
    conn.close()
    assert _exists(imm_db, t1, column="id", table="tenants") is True


def _exists(url, entry_id, column="id", table="compliance_audit_logs"):
    eng = create_engine(url)
    try:
        with eng.connect() as c:
            return c.execute(text(f"SELECT count(*) FROM {table} WHERE {column} = :i"),
                             {"i": entry_id}).scalar() == 1
    finally:
        eng.dispose()
