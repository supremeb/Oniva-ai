"""Fleet/workflows on engine U1: concurrent project create and migration 120 on Postgres."""
import asyncio
import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base
from app.services import automation_scope as scope
from tests.postgres.conftest import run_alembic


@pytest.fixture
def scoped(pg_server):
    """A plain-Postgres DB with just the tables the scope needs (no pgvector required)."""
    from app.models.engine import EngineConversation
    from app.models.project import Project
    from app.models.tenant import Tenant
    from app.models.user import User

    pg_server.recreate_db("oniva_scope")
    sync = create_engine(pg_server.url("oniva_scope"))
    tables = [Base.metadata.tables[n] for n in ("tenants", "users", "projects", "engine_conversations")]
    try:
        Base.metadata.create_all(sync, tables=_closure(tables))
    finally:
        sync.dispose()
    return pg_server.url("oniva_scope", driver="postgresql+asyncpg"), (Tenant, User, Project, EngineConversation)


def _closure(tables):
    by_name = Base.metadata.tables
    seen, stack = set(), [t.name for t in tables]
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        stack.extend(fk.column.table.name for fk in by_name[n].foreign_keys)
    return [t for t in Base.metadata.sorted_tables if t.name in seen]


async def test_concurrent_create_yields_one_project(scoped):
    url, (Tenant, User, _, _) = scoped
    engine = create_async_engine(url, pool_size=10)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    @asynccontextmanager
    async def factory():
        async with maker() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    t, u = uuid.uuid4(), uuid.uuid4()
    async with maker() as db:
        db.add(Tenant(id=t, name="t", slug=f"t-{t.hex[:6]}", is_active=True, settings={}))
        await db.commit()
        db.add(User(id=u, tenant_id=t, email="u@x.io", name="u"))
        await db.commit()
    ids = await asyncio.gather(*(scope.ensure_automation_project(factory, t, u) for _ in range(8)))
    assert len(set(ids)) == 1
    async with maker() as db:
        n = (await db.execute(text("SELECT count(*) FROM projects WHERE tenant_id = :t"), {"t": t})).scalar()
    assert n == 1
    await engine.dispose()


def test_migration_120_up_down_up(pg_migrated_url):
    def cols(sql):
        e = create_engine(pg_migrated_url)
        try:
            with e.connect() as c:
                return c.execute(text(sql)).scalars().all()
        finally:
            e.dispose()

    q = ("SELECT column_name FROM information_schema.columns WHERE table_name='{}' "
         "AND column_name IN ({})")
    assert run_alembic(pg_migrated_url, "upgrade", "head").returncode == 0
    assert sorted(cols(q.format("engine_conversations", "'run_kind','run_ref_id'"))) == ["run_kind", "run_ref_id"]
    assert cols(q.format("workflow_executions", "'engine_conversation_id'")) == ["engine_conversation_id"]
    assert cols("SELECT indexname FROM pg_indexes WHERE indexname='ix_engine_conversations_run'") == [
        "ix_engine_conversations_run"]
    down = run_alembic(pg_migrated_url, "downgrade", "109_llm_usage_agent_role")
    assert down.returncode == 0, down.stderr[-2000:]
    assert cols(q.format("engine_conversations", "'run_kind','run_ref_id'")) == []
    up = run_alembic(pg_migrated_url, "upgrade", "head")
    assert up.returncode == 0, up.stderr[-2000:]
    assert run_alembic(pg_migrated_url, "upgrade", "head").returncode == 0  # idempotent re-run
