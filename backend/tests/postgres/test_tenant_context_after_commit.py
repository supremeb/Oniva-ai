"""The tenant GUC is SET LOCAL, so it vanishes at commit. A tenant-scoped session must re-apply it
for the next transaction, otherwise ``await db.commit(); await db.refresh(obj)`` runs with no
tenant and RLS hides the row just written (the 500s on upload / background-jobs create+cancel)."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.database import set_tenant_context
from app.models.background_job import BackgroundJob
from tests.postgres.conftest import run_alembic
from tests.postgres.test_rls import PW, TA, _role_url

USER = str(uuid.uuid4())


@pytest.fixture(scope="module")
def db(pg_server, pg_has_vector):
    if not pg_has_vector:
        pytest.skip("pgvector extension not available for this Postgres; CI runs this tier fully")
    pg_server.recreate_db("oniva_tenant_ctx")
    owner = pg_server.url("oniva_tenant_ctx")
    res = run_alembic(owner, "upgrade", "head")
    assert res.returncode == 0, res.stderr[-3000:]
    eng = create_engine(owner)
    with eng.begin() as c:
        c.execute(text(f"ALTER ROLE oniva_app PASSWORD '{PW}'"))
        c.execute(text("INSERT INTO tenants (id, name, slug) VALUES (:i, 'a', 'a')"), {"i": TA})
        c.execute(text("INSERT INTO users (id, tenant_id, email, role, is_active) "
                       "VALUES (:u, :t, 'a@x.io', 'admin', true)"), {"u": USER, "t": TA})
    eng.dispose()
    app_engine = create_async_engine(_role_url(owner, "oniva_app", "postgresql+asyncpg"), poolclass=NullPool)
    return async_sessionmaker(app_engine, class_=AsyncSession, expire_on_commit=False)


async def test_refresh_after_commit_keeps_tenant_context(db):
    async with db() as s:
        await set_tenant_context(s, TA)
        job = BackgroundJob(tenant_id=uuid.UUID(TA), user_id=uuid.UUID(USER), job_type="background_agent",
                            status="pending", trigger="manual", execution_trace=[])
        s.add(job)
        await s.commit()
        await s.refresh(job)  # raised InvalidRequestError (row hidden by RLS) before the fix
        assert job.status == "pending"
        later = (await s.execute(select(BackgroundJob).where(BackgroundJob.id == job.id))).scalar_one()
        assert later.id == job.id


async def test_background_jobs_routes_as_oniva_app(db):
    """Real routes (create then cancel both commit-then-refresh) on the non-bypass app role."""
    import httpx
    from fastapi import FastAPI, Request

    from app.api.v1 import background_jobs
    from app.auth.providers.base import AuthUser
    from app.core.database import get_db

    app = FastAPI()
    app.include_router(background_jobs.router, prefix="/api/v1/background-jobs")

    @app.middleware("http")
    async def _auth(request: Request, call_next):
        request.state.user = AuthUser(id=USER, email="a@x.io", tenant_id=TA, tenant_slug="a",
                                      role="admin", auth_provider="local")
        return await call_next(request)

    async def _db():
        async with db() as s:
            await set_tenant_context(s, TA)
            yield s
            await s.commit()

    app.dependency_overrides[get_db] = _db
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/v1/background-jobs/", json={"job_type": "batch_processing"})
        assert r.status_code == 201, r.text
        r = await c.post(f"/api/v1/background-jobs/{r.json()['id']}/cancel", json={})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "cancelled"


async def test_session_without_tenant_context_still_sees_nothing(db):
    async with db() as s:
        rows = (await s.execute(select(BackgroundJob))).scalars().all()
        assert rows == []
