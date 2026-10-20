"""U7 - the inbox on real Postgres as ``oniva_app``: no cross-tenant leakage through any adapter,
and ``awaiting_input = None`` is SQL NULL (so a cleared clarification leaves the inbox)."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.auth.providers.base import AuthUser
from app.core.database import set_tenant_context
from app.models.deep_work_run import DeepWorkRun
from app.services.approval_service import ApprovalService
from app.services.inbox_service import InboxService
from tests.postgres.conftest import run_alembic
from tests.postgres.test_rls import PW, TA, TB, _role_url

PROJ = {TA: str(uuid.uuid4()), TB: str(uuid.uuid4())}
CONV = {TA: str(uuid.uuid4()), TB: str(uuid.uuid4())}
ADMIN = {TA: str(uuid.uuid4()), TB: str(uuid.uuid4())}
RUN = {TA: str(uuid.uuid4()), TB: str(uuid.uuid4())}


@pytest.fixture(scope="module")
def db(pg_server, pg_has_vector):
    if not pg_has_vector:
        pytest.skip("pgvector extension not available for this Postgres; CI runs this tier fully")
    pg_server.recreate_db("oniva_inbox_rls")
    owner = pg_server.url("oniva_inbox_rls")
    res = run_alembic(owner, "upgrade", "head")
    assert res.returncode == 0, res.stderr[-3000:]
    eng = create_engine(owner)
    with eng.begin() as c:
        c.execute(text(f"ALTER ROLE oniva_app PASSWORD '{PW}'"))
        c.execute(text("SET session_replication_role = replica"))
        for t in (TA, TB):
            c.execute(text("INSERT INTO tenants (id, name, slug) VALUES (:i, :n, :n)"), {"i": t, "n": t[:2]})
            c.execute(text("INSERT INTO users (id, tenant_id, email, role, is_active) "
                           "VALUES (:u, :t, :e, 'admin', true)"), {"u": ADMIN[t], "t": t, "e": f"{t[:6]}@x.io"})
            c.execute(text("INSERT INTO engine_conversations (conversation_id, tenant_id, project_id, user_id, session_id) "
                           "VALUES (:c, :t, :p, :u, 's1')"), {"c": CONV[t], "t": t, "p": PROJ[t], "u": ADMIN[t]})
            c.execute(text("INSERT INTO tool_approvals (id, tenant_id, conversation_id, tool_task_id, tool_name, requested_by) "
                           "VALUES (gen_random_uuid(), :t, :c, 'task', 'execute_python', :u)"),
                      {"t": t, "c": CONV[t], "u": ADMIN[t]})
            c.execute(text("INSERT INTO compliance_executions (id, tenant_id, user_id, template_id, template_version, "
                           "subject_id, subject_type, status, results, execution_trace) VALUES "
                           "(gen_random_uuid(), :t, :u, 'kyc', '1', 's', 'customer', 'manual_review', '{}', '[]')"),
                      {"t": t, "u": ADMIN[t]})
            c.execute(text("INSERT INTO background_jobs (id, tenant_id, user_id, job_type, status, trigger, progress, execution_trace, "
                           "completed_at) VALUES (gen_random_uuid(), :t, :u, 'background_agent', 'failed', 'manual', 0, '[]', now())"),
                      {"t": t, "u": ADMIN[t]})
            c.execute(text("INSERT INTO deep_work_runs (id, tenant_id, project_id, user_id, conversation_id, query, status, "
                           "phase, round, rounds, reflections, artifacts, is_engine, awaiting_input) VALUES "
                           "(:r, :t, :p, :u, :c, 'q', 'running', 'clarify', 0, '[]', '[]', '[]', true, "
                           "'{\"action\":\"ask\"}'::jsonb)"),
                      {"r": RUN[t], "t": t, "p": PROJ[t], "u": ADMIN[t], "c": CONV[t]})
    eng.dispose()
    app_engine = create_async_engine(_role_url(owner, "oniva_app", "postgresql+asyncpg"), poolclass=NullPool)
    return async_sessionmaker(app_engine, class_=AsyncSession, expire_on_commit=False)


def admin(tenant, uid=None) -> AuthUser:
    return AuthUser(id=uid or ADMIN[tenant], email="a@x.io", tenant_id=tenant, tenant_slug="t", role="admin",
                    auth_provider="local")


async def test_each_adapter_returns_only_the_callers_tenant(db):
    for t, other in ((TA, TB), (TB, TA)):
        async with db() as s:
            await set_tenant_context(s, t)
            svc = InboxService(s, approvals=ApprovalService(s))
            items, _ = await svc.list_for_user(admin(t))
            assert sorted(i["kind"] for i in items) == ["approval", "compliance", "deep_work", "fleet"]
            assert all(other[:8] not in str(i) for i in items)
            assert (await svc.count_for_user(admin(t)))["total"] == 4


async def test_a_session_for_another_tenant_sees_nothing(db):
    async with db() as s:
        await set_tenant_context(s, TA)
        svc = InboxService(s, approvals=ApprovalService(s))
        items, _ = await svc.list_for_user(admin(TB, uid=ADMIN[TB]))  # claims B while RLS context is A
        assert items == []
        assert (await svc.count_for_user(admin(TB)))["total"] == 0


async def test_clearing_awaiting_input_is_sql_null_and_leaves_the_inbox(db):
    async with db() as s:
        await set_tenant_context(s, TA)
        run = (await s.execute(select(DeepWorkRun).where(DeepWorkRun.tenant_id == uuid.UUID(TA)))).scalar_one()
        run.awaiting_input = None
        await s.commit()
        await set_tenant_context(s, TA)
        svc = InboxService(s, approvals=ApprovalService(s))
        items, _ = await svc.list_for_user(admin(TA), kinds=["deep_work"])
        assert items == []
        n = (await s.execute(text("SELECT count(*) FROM deep_work_runs WHERE awaiting_input IS NULL"))).scalar()
        assert n == 1
