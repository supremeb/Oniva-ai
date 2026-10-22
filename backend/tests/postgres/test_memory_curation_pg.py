"""U31 on real Postgres: the chat sweep is leader-safe (advisory lock), and the curator's reads and writes run
under the non-bypass ``oniva_app`` role with the transaction-local tenant context (CLAUDE.md RLS gotcha).

The lock test needs no schema. The RLS test runs on the Alembic-migrated database (needs pgvector; skipped
locally without it, CI runs it).
"""
from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import Settings
from app.core.database import set_tenant_context
from app.models.project_memory import MemoryBullet
from app.services import memory_curator as mc
from tests.internal.test_memory_curate_routes import FakeEngine, make_trace
from tests.postgres.conftest import run_alembic
from tests.postgres.test_rls import PW, TA, TB, _role_url

SETTINGS = dict(_env_file=None, SECRET_KEY="Zk3vN8qLw1RtYb6HdPx9CfUe2MaS5JoG7iVn4TlKq0A")
DB = "oniva_memory_curation"
LOCK_DB = "oniva_memory_curation_lock"
USER = {TA: str(uuid.uuid4()), TB: str(uuid.uuid4())}
PROJ = {TA: str(uuid.uuid4()), TB: str(uuid.uuid4())}
CONV = {TA: str(uuid.uuid4()), TB: str(uuid.uuid4())}


# ---- advisory lock ---------------------------------------------------------------------------------------


@pytest.fixture
def lock_factory(pg_server):
    pg_server.recreate_db(LOCK_DB)
    engine = create_async_engine(pg_server.url(LOCK_DB, driver="postgresql+asyncpg"))
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    @asynccontextmanager
    async def factory():
        async with maker() as s:
            yield s

    yield factory


async def test_second_sweep_takes_no_lock_while_the_first_is_mid_pass(lock_factory):
    settings = Settings(**SETTINGS, MEMORY_CURATOR_MODE="auto")
    entered, release = asyncio.Event(), asyncio.Event()
    passes = []

    class Held(mc.CurationSweep):
        async def _pass(self):
            passes.append(self)
            entered.set()
            await release.wait()
            return {"checked": 0, "started": 0, "skipped": 0}

    first, second = Held(lock_factory, settings), Held(lock_factory, settings)
    try:
        task = asyncio.create_task(first.run_once())
        await asyncio.wait_for(entered.wait(), 10)
        assert await second.run_once() is None  # the lock is held elsewhere: it did nothing
        assert passes == [first]
        release.set()
        assert await task is not None
        assert await second.run_once() is not None  # released: the next pass runs
    finally:
        release.set()


async def test_a_sweep_that_is_off_never_connects(lock_factory):
    def boom():
        raise AssertionError("connected")

    assert await mc.CurationSweep(boom, Settings(**SETTINGS), engine_factory=boom).run_once() == {
        "checked": 0, "started": 0}


# ---- RLS ---------------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def db(pg_server, pg_has_vector):
    if not pg_has_vector:
        pytest.skip("pgvector extension not available for this Postgres; CI runs this tier fully")
    pg_server.recreate_db(DB)
    owner = pg_server.url(DB)
    res = run_alembic(owner, "upgrade", "head")
    assert res.returncode == 0, res.stderr[-3000:]
    eng = create_engine(owner)
    with eng.begin() as c:
        c.execute(text(f"ALTER ROLE oniva_app PASSWORD '{PW}'"))
        c.execute(text(f"ALTER ROLE oniva_system PASSWORD '{PW}'"))
        for t in (TA, TB):
            c.execute(text("INSERT INTO tenants (id, name, slug, settings) VALUES (:i, :n, :n, "
                           "'{\"features\": {\"ENABLE_PROJECT_MEMORY\": true}}'::jsonb)"), {"i": t, "n": t[:2]})
            c.execute(text("INSERT INTO users (id, tenant_id, email, role, is_active) "
                           "VALUES (:u, :t, :e, 'member', true)"), {"u": USER[t], "t": t, "e": f"{t[:2]}@x.io"})
            c.execute(text("INSERT INTO projects (id, tenant_id, owner_id, name, settings) VALUES "
                           "(:p, :t, :u, 'p', '{\"memory\": {\"enabled\": true}}'::jsonb)"),
                      {"p": PROJ[t], "t": t, "u": USER[t]})
            c.execute(text("INSERT INTO engine_conversations (conversation_id, tenant_id, project_id, user_id, "
                           "session_id) VALUES (:c, :t, :p, :u, 's')"),
                      {"c": CONV[t], "t": t, "p": PROJ[t], "u": USER[t]})
    eng.dispose()
    app_engine = create_async_engine(_role_url(owner, "oniva_app", "postgresql+asyncpg"), poolclass=NullPool)
    sys_engine = create_async_engine(_role_url(owner, "oniva_system", "postgresql+asyncpg"), poolclass=NullPool)
    return {"owner": owner,
            "app": async_sessionmaker(app_engine, class_=AsyncSession, expire_on_commit=False),
            "system": async_sessionmaker(sys_engine, class_=AsyncSession, expire_on_commit=False)}


@pytest.fixture(autouse=True)
def clean(request):
    yield
    if "db" in request.fixturenames:
        eng = create_engine(request.getfixturevalue("db")["owner"])
        with eng.begin() as c:
            c.execute(text("DELETE FROM memory_bullets"))
        eng.dispose()


def tenants_for(db):
    @asynccontextmanager
    async def tenants(tenant_id):
        async with db["app"]() as s:
            await set_tenant_context(s, tenant_id)
            yield s
            await s.commit()

    return tenants


def binding(tenant):
    return mc.Binding(uuid.UUID(CONV[tenant]), uuid.UUID(tenant), uuid.UUID(PROJ[tenant]), uuid.UUID(USER[tenant]), "s")


async def visible(db, tenant):
    async with db["app"]() as s:
        await set_tenant_context(s, tenant)
        return (await s.execute(select(MemoryBullet.bullet_key, MemoryBullet.status))).all()


async def test_prepare_and_apply_run_under_the_non_bypass_role_and_tenants_stay_apart(db):
    settings = Settings(**SETTINGS, MEMORY_CURATOR_MODE="auto", MEMORY_CURATE_MIN_TURNS=1)
    tenants = tenants_for(db)

    @asynccontextmanager
    async def system():
        async with db["system"]() as s:
            yield s
            await s.commit()

    engine = FakeEngine(make_trace(CONV[TA]))
    b = binding(TA)
    out = await mc.prepare(factory=system, tenants=tenants, engine=engine, settings=settings, binding=b,
                           conversation_id=CONV[TA], run_id="u1")
    assert out["kind"] == "llm", out
    ops = '{"deltas": [{"op": "add", "section": "deploy", "text": "Run the staging deploy script first"}]}'
    done = await mc.apply(tenants=tenants, settings=settings, binding=b, conversation_id=CONV[TA], run_id="u1",
                          ctx=out["ctx"], answer=ops)
    assert done["status"] == "applied" and done["counts"]["applied"] == 1
    # committed rows are visible to tenant A's context and invisible to tenant B's
    assert [s for _, s in await visible(db, TA)] == ["active"]
    assert await visible(db, TB) == []
    # the same delta again is idempotent (derived delta id), under the same role
    again = await mc.apply(tenants=tenants, settings=settings, binding=b, conversation_id=CONV[TA], run_id="u1",
                           ctx=out["ctx"], answer=ops)
    assert again["status"] == "applied" and len(await visible(db, TA)) == 1


async def test_a_binding_that_names_another_tenants_project_writes_nothing(db):
    settings = Settings(**SETTINGS, MEMORY_CURATOR_MODE="auto")
    tenants = tenants_for(db)
    forged = mc.Binding(uuid.UUID(CONV[TB]), uuid.UUID(TB), uuid.UUID(PROJ[TA]), uuid.UUID(USER[TB]), "s")
    ctx = mc.make_ctx(settings, conversation_id=CONV[TB], run_id="u1", tainted=False, mode="auto", injected=[], turns=3)
    out = await mc.apply(tenants=tenants, settings=settings, binding=forged, conversation_id=CONV[TB], run_id="u1",
                         ctx=ctx, answer='{"deltas": [{"op": "add", "section": "s", "text": "Cross-tenant lesson"}]}')
    assert out["status"] == "skipped" and out["reason"] == "memory_disabled"  # tenant B cannot see project A
    assert await visible(db, TA) == [] and await visible(db, TB) == []


async def test_tainted_source_stays_pending_under_rls(db):
    settings = Settings(**SETTINGS, MEMORY_CURATOR_MODE="auto")
    ctx = mc.make_ctx(settings, conversation_id=CONV[TA], run_id="u2", tainted=True, mode="auto", injected=[], turns=3)
    out = await mc.apply(tenants=tenants_for(db), settings=settings, binding=binding(TA), conversation_id=CONV[TA],
                         run_id="u2", ctx=ctx, answer='{"deltas": [{"op": "add", "section": "s", "text": "A lesson"}]}')
    assert out["counts"]["pending"] == 1 and out["counts"]["applied"] == 0
    assert [s for _, s in await visible(db, TA)] == ["pending"]
