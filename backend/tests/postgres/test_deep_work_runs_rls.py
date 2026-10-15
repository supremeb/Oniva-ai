"""Deep Work U3: ``deep_work_runs`` RLS and row-lock behavior on real Postgres.

Runs on the Alembic-migrated database (needs pgvector; skipped locally without it, CI runs it).
"""
from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager

import psycopg2
import pytest
from psycopg2 import errors
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.database import set_tenant_context
from app.services import deep_work_state as dws
from tests.postgres.conftest import run_alembic
from tests.postgres.test_rls import PW, TA, TB, _role_url

PROJ = {TA: str(uuid.uuid4()), TB: str(uuid.uuid4())}
USER = {TA: str(uuid.uuid4()), TB: str(uuid.uuid4())}
CONV = {TA: str(uuid.uuid4()), TB: str(uuid.uuid4())}


@pytest.fixture(scope="module")
def db(pg_server, pg_has_vector):
    if not pg_has_vector:
        pytest.skip("pgvector extension not available for this Postgres; CI runs this tier fully")
    pg_server.recreate_db("oniva_dw_runs")
    owner = pg_server.url("oniva_dw_runs")
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
                "VALUES (:c, :t, :p, :u, 's1')"), {"c": CONV[t], "t": t, "p": PROJ[t], "u": USER[t]})
            c.execute(text(
                "INSERT INTO deep_work_runs (id, tenant_id, project_id, user_id, conversation_id, query) "
                "VALUES (gen_random_uuid(), :t, :p, :u, :c, 'q')"), {"c": CONV[t], "t": t, "p": PROJ[t], "u": USER[t]})
    eng.dispose()
    return {"owner": owner}


def _tenant_conn(db, tenant):
    conn = psycopg2.connect(_role_url(db["owner"], "oniva_app"))
    cur = conn.cursor()
    cur.execute("SELECT set_config('app.current_tenant', %s, false)", (tenant,))
    return conn, cur


def test_without_a_tenant_oniva_app_sees_no_runs(db):
    conn = psycopg2.connect(_role_url(db["owner"], "oniva_app"))
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM deep_work_runs")
    assert cur.fetchone()[0] == 0
    conn.close()


def test_tenant_b_never_reads_tenant_a_runs(db):
    for me in (TA, TB):
        conn, cur = _tenant_conn(db, me)
        cur.execute("SELECT tenant_id::text, conversation_id::text FROM deep_work_runs")
        assert cur.fetchall() == [(me, CONV[me])]
        conn.close()


def test_with_check_blocks_a_cross_tenant_insert(db):
    conn, cur = _tenant_conn(db, TA)
    with pytest.raises(psycopg2.Error) as ei:
        cur.execute(
            "INSERT INTO deep_work_runs (id, tenant_id, project_id, user_id, conversation_id, query) "
            "VALUES (gen_random_uuid(), %s, %s, %s, gen_random_uuid(), 'x')", (TB, PROJ[TB], USER[TB]))
    assert isinstance(ei.value, errors.InsufficientPrivilege)
    conn.close()


def test_conversation_id_is_unique(db):
    conn, cur = _tenant_conn(db, TA)
    with pytest.raises(psycopg2.Error) as ei:
        cur.execute(
            "INSERT INTO deep_work_runs (id, tenant_id, project_id, user_id, conversation_id, query) "
            "VALUES (gen_random_uuid(), %s, %s, %s, %s, 'dup')", (TA, PROJ[TA], USER[TA], CONV[TA]))
    assert isinstance(ei.value, errors.UniqueViolation)
    conn.close()


def test_rls_is_enabled_and_forced(db):
    conn = psycopg2.connect(db["owner"])
    cur = conn.cursor()
    cur.execute("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = 'deep_work_runs'")
    assert cur.fetchone() == (True, True)
    conn.close()


@pytest.fixture
async def app_factory(db):
    engine = create_async_engine(_role_url(db["owner"], "oniva_app", "postgresql+asyncpg"), poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    @asynccontextmanager
    async def factory():  # one tenant-scoped session per call, as a gateway request opens
        async with maker() as session:
            await set_tenant_context(session, TA)
            yield session
            await session.commit()

    yield factory
    await engine.dispose()


async def test_concurrent_worker_saves_lose_nothing(app_factory):
    """FOR UPDATE serializes read-modify-write: twenty racing workers all land (and stay monotonic)."""
    workers = [f"w{i}" for i in range(20)]
    await asyncio.gather(*(
        dws.save_worker_learnings(app_factory, TA, CONV[TA], round=1 + i % 3, worker_id=w, learnings=[w])
        for i, w in enumerate(workers)
    ))
    run = await dws.load_run(app_factory, TA, CONV[TA])
    saved = {w for r in run.rounds for w in r["workers"]}
    assert saved == set(workers)
    assert [r["round"] for r in run.rounds] == [1, 2, 3] and run.round == 3


async def test_tenant_b_context_cannot_load_or_update_a_run(db):
    engine = create_async_engine(_role_url(db["owner"], "oniva_app", "postgresql+asyncpg"), poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    @asynccontextmanager
    async def factory_b():
        async with maker() as session:
            await set_tenant_context(session, TB)
            yield session
            await session.commit()

    try:
        assert await dws.load_run(factory_b, TA, CONV[TA]) is None  # RLS hides it even when named
        assert await dws.update_run(factory_b, TA, CONV[TA], status="cancelled") is None
    finally:
        await engine.dispose()
