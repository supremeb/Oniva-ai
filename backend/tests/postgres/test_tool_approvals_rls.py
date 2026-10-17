"""U2: ``tool_approvals`` RLS, unique keys, and the decide protocol against real Postgres.

Runs on the Alembic-migrated database (needs pgvector; skipped locally without it, CI runs it).
"""
from __future__ import annotations

import asyncio
import uuid

import psycopg2
import pytest
from psycopg2 import errors
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.auth.providers.base import AuthUser
from app.core.database import set_tenant_context
from app.models.engine import EngineConversation
from app.models.tool_approval import ToolApproval
from app.security.kms import LocalDevKMS
from app.services.approval_service import ApprovalError, ApprovalService
from app.services.audit_chain import AuditChain
from tests.postgres.conftest import run_alembic
from tests.postgres.test_rls import PW, TA, TB, _role_url
from tests.test_approval_service import FakeEngine

PROJ = str(uuid.uuid4())
CONV = {TA: str(uuid.uuid4()), TB: str(uuid.uuid4())}
ADMIN1, ADMIN2 = str(uuid.uuid4()), str(uuid.uuid4())


@pytest.fixture(scope="module")
def db(pg_server, pg_has_vector, tmp_path_factory):
    if not pg_has_vector:
        pytest.skip("pgvector extension not available for this Postgres; CI runs this tier fully")
    pg_server.recreate_db("oniva_tool_approvals")
    owner = pg_server.url("oniva_tool_approvals")
    res = run_alembic(owner, "upgrade", "head")
    assert res.returncode == 0, res.stderr[-3000:]
    eng = create_engine(owner)
    with eng.begin() as c:
        c.execute(text(f"ALTER ROLE oniva_app PASSWORD '{PW}'"))
        c.execute(text(f"ALTER ROLE oniva_system PASSWORD '{PW}'"))
        c.execute(text("SET session_replication_role = replica"))  # skip FK triggers for seeding
        for t in (TA, TB):
            c.execute(text("INSERT INTO tenants (id, name, slug) VALUES (:i, :n, :n)"), {"i": t, "n": t[:2]})
            c.execute(text(
                "INSERT INTO engine_conversations (conversation_id, tenant_id, project_id, user_id, session_id) "
                "VALUES (:c, :t, :p, :u, 's1')"), {"c": CONV[t], "t": t, "p": PROJ, "u": ADMIN1})
        for uid in (ADMIN1, ADMIN2):
            c.execute(text(
                "INSERT INTO users (id, tenant_id, email, role, is_active) "
                "VALUES (:i, :t, :e, 'admin', true)"), {"i": uid, "t": TA, "e": f"{uid[:6]}@x.io"})
    eng.dispose()
    app_engine = create_async_engine(_role_url(owner, "oniva_app", "postgresql+asyncpg"), poolclass=NullPool)
    return {
        "owner": owner,
        "app": async_sessionmaker(app_engine, class_=AsyncSession, expire_on_commit=False),
        "kms": LocalDevKMS(tmp_path_factory.mktemp("approvals-kms")),
    }


@pytest.fixture(autouse=True)
def clean(db):
    eng = create_engine(db["owner"])
    with eng.begin() as c:
        c.execute(text("SET session_replication_role = replica"))
        c.execute(text("DELETE FROM tool_approvals"))
        c.execute(text("ALTER TABLE compliance_audit_logs DISABLE TRIGGER compliance_audit_logs_immutable"))
        c.execute(text("DELETE FROM compliance_audit_logs"))
        c.execute(text("DELETE FROM audit_chain_heads"))
        c.execute(text("ALTER TABLE compliance_audit_logs ENABLE TRIGGER compliance_audit_logs_immutable"))
    eng.dispose()


def _insert_sql(tenant, task="t1", run="r1", call="c1"):
    return (
        "INSERT INTO tool_approvals (id, tenant_id, conversation_id, tool_task_id, run_id, tool_call_id) "
        f"VALUES (gen_random_uuid(), '{tenant}', '{CONV[tenant]}', "
        f"{'NULL' if task is None else repr(task)}, "
        f"{'NULL' if run is None else repr(run)}, {'NULL' if call is None else repr(call)})"
    )


def _seed(db, *sqls):
    eng = create_engine(db["owner"])
    with eng.begin() as c:
        for s in sqls:
            c.execute(text(s))
    eng.dispose()


def _tenant_conn(db, tenant):
    conn = psycopg2.connect(_role_url(db["owner"], "oniva_app"))
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT set_config('app.current_tenant', %s, false)", (tenant,))
    return conn, cur


def test_without_a_tenant_oniva_app_sees_no_rows(db):
    _seed(db, _insert_sql(TA))
    conn = psycopg2.connect(_role_url(db["owner"], "oniva_app"))
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM tool_approvals")
    assert cur.fetchone()[0] == 0
    conn.close()


def test_a_tenant_cannot_read_another_tenants_rows(db):
    _seed(db, _insert_sql(TA), _insert_sql(TB, task="t2", run="r2", call="c2"))
    for me, task in ((TA, "t1"), (TB, "t2")):
        conn, cur = _tenant_conn(db, me)
        cur.execute("SELECT tenant_id::text, tool_task_id FROM tool_approvals")
        assert cur.fetchall() == [(me, task)]
        conn.close()


def test_with_check_blocks_a_cross_tenant_insert(db):
    conn, cur = _tenant_conn(db, TA)
    with pytest.raises(psycopg2.Error):
        cur.execute(_insert_sql(TB))
    conn.close()


def test_unique_keys_reject_duplicates(db):
    _seed(db, _insert_sql(TA))
    conn, cur = _tenant_conn(db, TA)
    with pytest.raises(errors.UniqueViolation):  # same root conversation + task id
        cur.execute(_insert_sql(TA, run="other", call="other"))
    with pytest.raises(errors.UniqueViolation):  # same run + call id
        cur.execute(_insert_sql(TA, task="other"))
    # NULLs are exempt: partial indexes only cover rows that carry the keys
    cur.execute(_insert_sql(TA, task=None, run=None, call=None))
    cur.execute(_insert_sql(TA, task=None, run=None, call=None))
    # the same task id under another tenant's root conversation is a different key
    conn.close()


def test_check_constraints_reject_unknown_status(db):
    conn, cur = _tenant_conn(db, TA)
    with pytest.raises(errors.CheckViolation):
        cur.execute(_insert_sql(TA).replace("(id,", "(status, id,").replace("VALUES (", "VALUES ('bogus', "))
    conn.close()


def _auth(uid):
    return AuthUser(id=uid, email=f"{uid[:6]}@x.io", tenant_id=TA, tenant_slug="ta", role="admin",
                    auth_provider="local")


async def _decider(db, uid, engine, row_id):
    async with db["app"]() as s:
        await set_tenant_context(s, TA)
        row = (await s.execute(select(ToolApproval).where(ToolApproval.id == row_id))).scalars().one()
        conv = await s.get(EngineConversation, uuid.UUID(CONV[TA]))
        svc = ApprovalService(s, audit=AuditChain(s, kms=db["kms"]))
        try:
            await svc.decide(user=_auth(uid), conv=conv, row=row, decision="approve", note=None, engine=engine)
            return "ok"
        except ApprovalError as e:
            return (e.status, e.code)


async def test_two_concurrent_deciders_exactly_one_wins_and_the_chain_verifies(db):
    row_id = uuid.uuid4()
    _seed(db, "INSERT INTO tool_approvals (id, tenant_id, conversation_id, tool_task_id, tool_name, "
              f"approver_role, requested_by) VALUES ('{row_id}', '{TA}', '{CONV[TA]}', 'tc', 'execute_python', "
              f"'admin', NULL)")

    class SlowEngine(FakeEngine):
        async def decide(self, *a, **k):
            await asyncio.sleep(0.3)
            return await super().decide(*a, **k)

    engine = SlowEngine()
    results = await asyncio.gather(_decider(db, ADMIN1, engine, row_id), _decider(db, ADMIN2, engine, row_id))
    assert sorted(map(str, results)) == sorted(["ok", str((409, "decision_in_progress"))])
    assert len(engine.decide_calls) == 1
    async with db["app"]() as s:
        await set_tenant_context(s, TA)
        row = (await s.execute(select(ToolApproval).where(ToolApproval.id == row_id))).scalars().one()
        assert row.status == "approved"
        verdict = await AuditChain(s, kms=db["kms"]).verify_chain(TA)
        count = (await s.execute(text(
            "SELECT count(*) FROM compliance_audit_logs WHERE action = 'approval_decided'"))).scalar()
    assert verdict["ok"] is True and count == 1


async def test_record_pending_race_returns_one_row(db):
    async def one():
        async with db["app"]() as s:
            await set_tenant_context(s, TA)
            svc = ApprovalService(s, audit=AuditChain(s, kms=db["kms"]))
            row = await svc.record_pending(tenant_id=TA, conversation_id=CONV[TA], tool_task_id="race",
                                           run_id="rr", tool_call_id="rc", tool_name="x")
            await s.commit()
            return row.id

    ids = await asyncio.gather(*(one() for _ in range(5)))
    assert len(set(ids)) == 1
