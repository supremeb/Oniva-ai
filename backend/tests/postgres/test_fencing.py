"""U5 - fencing and idempotency locking semantics on real Postgres (KTD4, KTD5, KTD6).

The in-process behavior is covered on SQLite in tests/internal/test_invoke.py; SQLite has no row
locks, so what only Postgres can prove lives here:

* a write effect holds ``FOR SHARE`` on the session's fence row for its whole duration;
* a successor's raise (any fenced request) waits for it, then sees the stored outcome;
* the raise waits at most the bound, then answers 503 + Retry-After;
* the same owner's concurrent calls (same token) never wait on each other;
* concurrent duplicates of one key run the effect once.

The subset tests build only the engine tables from the models, so they run on plain Postgres (no
pgvector). ``test_system_role_*`` needs the migrated baseline and is skipped locally without it.
"""
from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta

import httpx
import psycopg2
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agents.skills.registry import Skill, _registry
from app.config import Settings
from app.internal_api import fence, invocations, tools
from app.internal_api.invocations import get_session_factory, utcnow
from app.internal_api.router import mount_internal_router
from app.internal_api.tool_specs import TOOL_SPECS, ToolSpec
from app.models.engine import EngineConversation, ToolInvocation
from app.models.project import Project
from app.models.tenant import Tenant
from app.models.user import User
from app.services.startup_recovery import interrupt_stale_tool_invocations
from tests.postgres.conftest import run_alembic
from tests.postgres.test_rls import PW, _role_url

GATEWAY = "g" * 12 + "Gateway-Token-0123456789-abcdefXYZ"
ENGINE_SVC = "e" * 12 + "Engine-Svc-Token-0123456789-abcdXY"
SECRET = "Zk3vN8qLw1RtYb6HdPx9CfUe2MaS5JoG7iVn4TlKq0A"
AUTH = {"Authorization": f"Bearer {GATEWAY}"}
SESSION = "sess-pg"


@pytest.fixture(scope="session")
def fencing_url(pg_server) -> str:
    """Async URL of a DB holding just the engine tables (and their FK closure)."""
    from app.models import Base

    by_name = Base.metadata.tables
    closure: set[str] = set()
    stack = ["tool_invocations", "engine_fences", "engine_conversations"]
    while stack:
        name = stack.pop()
        if name in closure:
            continue
        closure.add(name)
        stack.extend(fk.column.table.name for fk in by_name[name].foreign_keys)
    tables = [t for t in Base.metadata.sorted_tables if t.name in closure]

    pg_server.recreate_db("oniva_fencing_subset")
    eng = create_engine(pg_server.url("oniva_fencing_subset"))
    try:
        Base.metadata.create_all(eng, tables=tables)
    finally:
        eng.dispose()
    return pg_server.url("oniva_fencing_subset", driver="postgresql+asyncpg")


def make_factory(async_engine):
    maker = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)

    @asynccontextmanager
    async def factory():
        async with maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    return maker, factory


class World:
    pass


@pytest.fixture
async def pg(fencing_url, monkeypatch):
    sync = create_engine(fencing_url.replace("+asyncpg", ""))
    with sync.begin() as conn:
        conn.execute(text("TRUNCATE tool_invocations, engine_fences, engine_conversations, projects, users, tenants CASCADE"))
    sync.dispose()

    counts = {"pg_write": 0, "pg_read": 0}
    gates: dict[str, asyncio.Event] = {}
    started: dict[str, asyncio.Event] = {}

    def make(name):
        async def handler(ctx, path: str = "a"):
            key = f"{name}:{path}"
            if key in gates:
                started[key].set()
                await gates[key].wait()
            counts[name] += 1
            return {"path": path, "n": counts[name]}

        _registry[name] = Skill(name=name, description=name, category="test", handler=handler)

    make("pg_write")
    make("pg_read")
    monkeypatch.setitem(TOOL_SPECS, "pg_write", ToolSpec(side_effect="write", idempotent=False, timeout_ms=20_000))
    monkeypatch.setitem(TOOL_SPECS, "pg_read", ToolSpec(side_effect="none", idempotent=True, timeout_ms=20_000))

    async_engine = create_async_engine(fencing_url, pool_size=10, max_overflow=10)
    maker, factory = make_factory(async_engine)
    await tools.ensure_skills_loaded()

    w = World()
    w.counts, w.maker, w.factory = counts, maker, factory
    w.tenant, w.user, w.project, w.conv = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    w.url = fencing_url

    def hold(name, path):
        gates[f"{name}:{path}"] = asyncio.Event()
        started[f"{name}:{path}"] = asyncio.Event()
        return started[f"{name}:{path}"], gates[f"{name}:{path}"]

    w.hold = hold
    async with maker() as db:
        db.add(Tenant(id=w.tenant, name="t", slug=f"t-{w.tenant.hex[:6]}", is_active=True, settings={}))
        await db.commit()
        db.add(User(id=w.user, tenant_id=w.tenant, email="u@x.io", name="u"))
        await db.commit()
        db.add(Project(id=w.project, tenant_id=w.tenant, name="p", owner_id=w.user))
        await db.commit()
        db.add(EngineConversation(conversation_id=w.conv, tenant_id=w.tenant, project_id=w.project, user_id=w.user, session_id=SESSION))
        await db.commit()

    app = FastAPI()
    mount_internal_router(
        app, Settings(_env_file=None, SECRET_KEY=SECRET, ENGINE_GATEWAY_TOKEN=GATEWAY, ENGINE_SERVICE_TOKEN=ENGINE_SVC)
    )
    app.dependency_overrides[get_session_factory] = lambda: factory
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=30) as client:
        w.client = client
        yield w
    for g in gates.values():
        g.set()
    pending = list(tools._effects)
    if pending:
        await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), 15)
    _registry.pop("pg_write", None)
    _registry.pop("pg_read", None)
    await async_engine.dispose()


def invoke(w, name="pg_write", *, token, call="c1", path="a", run="r1"):
    return w.client.post(
        f"/internal/tools/{name}/invoke",
        json={"args": {"path": path}, "runId": run, "toolCallId": call, "tenantId": str(w.tenant),
              "projectId": str(w.project), "conversationId": str(w.conv)},
        headers={**AUTH, "Idempotency-Key": f"{run}:{call}", "X-Engine-Fence": str(token), "X-Engine-Session": SESSION},
    )


def fenced(token):
    return {**AUTH, "X-Engine-Fence": str(token), "X-Engine-Session": SESSION}


def manifest(w, token):
    return w.client.get("/internal/tools/manifest", headers=fenced(token))


def reconcile(w, token, key="r1:c1"):
    return w.client.get(f"/internal/tools/invocations/{key}", headers=fenced(token))


def row_lock_free(url: str, lock_clause: str) -> bool:
    """Whether another connection can take ``lock_clause`` on the session's fence row right now."""
    conn = psycopg2.connect(url.replace("postgresql+asyncpg", "postgresql"))
    try:
        cur = conn.cursor()
        try:
            cur.execute(f"SELECT 1 FROM engine_fences WHERE session_id = %s FOR {lock_clause} NOWAIT", (SESSION,))
            return True
        except psycopg2.errors.LockNotAvailable:
            return False
    finally:
        conn.rollback()
        conn.close()


async def settle(task, seconds=0.4):
    """True when ``task`` is still pending after ``seconds`` (i.e. it is blocked)."""
    done, _ = await asyncio.wait({task}, timeout=seconds)
    return not done


# ---- the lock itself -------------------------------------------------------------------------


async def test_write_effect_holds_for_share_on_the_fence_row_until_it_finishes(pg):
    started, release = pg.hold("pg_write", "a")
    call = asyncio.create_task(invoke(pg, token=6))
    await asyncio.wait_for(started.wait(), 10)

    assert not row_lock_free(pg.url, "UPDATE")  # a raise (row UPDATE) would wait
    assert row_lock_free(pg.url, "SHARE")  # other writers of the same owner do not

    release.set()
    assert (await call).status_code == 200
    assert row_lock_free(pg.url, "UPDATE")  # released with the outcome stored


async def test_read_effect_holds_no_lock(pg):
    started, release = pg.hold("pg_read", "a")
    call = asyncio.create_task(invoke(pg, "pg_read", token=6))
    await asyncio.wait_for(started.wait(), 10)
    assert row_lock_free(pg.url, "UPDATE")
    release.set()
    assert (await call).status_code == 200


async def test_same_owner_writes_run_concurrently_without_waiting_on_each_other(pg):
    s1, r1 = pg.hold("pg_write", "one")
    s2, r2 = pg.hold("pg_write", "two")
    a = asyncio.create_task(invoke(pg, token=6, call="c1", path="one"))
    b = asyncio.create_task(invoke(pg, token=6, call="c2", path="two"))
    await asyncio.wait_for(asyncio.gather(s1.wait(), s2.wait()), 10)  # both in flight at once
    r1.set()
    r2.set()
    assert (await a).status_code == (await b).status_code == 200
    assert pg.counts["pg_write"] == 2


# ---- AE4 -------------------------------------------------------------------------------------


async def test_successor_manifest_waits_for_the_effect_then_reconcile_finds_the_outcome_AE4(pg):
    started, release = pg.hold("pg_write", "a")
    old = asyncio.create_task(invoke(pg, token=6))
    await asyncio.wait_for(started.wait(), 10)

    successor = asyncio.create_task(manifest(pg, 7))
    assert await settle(successor)  # blocked by the old owner's in-flight write
    assert (await reconcile_status(pg, 6)) == 404  # in flight is not an outcome (same-token: no wait)

    release.set()
    first = await old
    assert first.status_code == 200
    assert (await successor).status_code == 200  # completes only after the effect

    rec = await reconcile(pg, 7)
    assert rec.status_code == 200 and rec.json() == first.json()

    late = await invoke(pg, token=6, call="c2", path="late")
    assert late.status_code == 409 and late.json() == {"error": "fence_rejected", "highWater": 7}
    assert pg.counts["pg_write"] == 1
    async with pg.maker() as db:
        keys = (await db.execute(select(ToolInvocation.idempotency_key))).scalars().all()
    assert keys == ["r1:c1"]  # the late invoke stored nothing


async def reconcile_status(w, token):
    return (await reconcile(w, token)).status_code


async def test_successor_reconcile_waits_for_the_effect_so_a_404_could_only_mean_it_never_ran(pg):
    """Reconcile at the higher token raises the mark: after it returns, the old effect cannot run."""
    started, release = pg.hold("pg_write", "a")
    old = asyncio.create_task(invoke(pg, token=6))
    await asyncio.wait_for(started.wait(), 10)

    rec = asyncio.create_task(reconcile(pg, 7))
    assert await settle(rec)
    release.set()
    await old
    assert (await rec).status_code == 200  # the effect finished first, so the outcome exists


async def test_raise_gives_up_after_the_bound_with_503_retry_after(pg, monkeypatch):
    monkeypatch.setattr(fence, "RAISE_WAIT_MS", 300)
    started, release = pg.hold("pg_write", "a")
    old = asyncio.create_task(invoke(pg, token=6))
    await asyncio.wait_for(started.wait(), 10)

    r = await manifest(pg, 7)
    assert r.status_code == 503 and r.headers["retry-after"] == "1"
    async with pg.maker() as db:
        hw = (await db.execute(text("SELECT high_water FROM engine_fences WHERE session_id = :s"), {"s": SESSION})).scalar_one()
    assert hw == 6  # the failed raise changed nothing

    release.set()
    assert (await old).status_code == 200
    assert (await manifest(pg, 7)).status_code == 200  # the engine's retry succeeds


async def test_invoke_arriving_after_the_raise_waits_for_nothing_and_is_rejected(pg):
    assert (await manifest(pg, 7)).status_code == 200
    r = await invoke(pg, token=6)
    assert r.status_code == 409 and pg.counts["pg_write"] == 0


async def test_raise_committing_before_the_share_lock_is_seen_under_it(pg, monkeypatch):
    """Accepted at 6, then the mark rises to 7 before the effect's lock: the in-lock check refuses."""
    real_begin = invocations.begin

    async def begin_then_raise(*a, **kw):
        out = await real_begin(*a, **kw)
        assert (await manifest(pg, 7)).status_code == 200  # no write in flight yet, so no wait
        return out

    monkeypatch.setattr(invocations, "begin", begin_then_raise)
    r = await invoke(pg, token=6)
    assert r.status_code == 409 and r.json()["highWater"] == 7
    assert pg.counts["pg_write"] == 0
    async with pg.maker() as db:
        assert (await db.execute(select(ToolInvocation))).scalars().all() == []


# ---- idempotency under real concurrency -------------------------------------------------------


async def test_concurrent_duplicates_of_one_key_run_the_effect_once(pg):
    started, release = pg.hold("pg_write", "a")
    first = asyncio.create_task(invoke(pg, token=6))
    await asyncio.wait_for(started.wait(), 10)
    dups = await asyncio.gather(*[invoke(pg, token=6) for _ in range(6)])
    assert {d.status_code for d in dups} == {503}
    assert all(d.headers["retry-after"] == "1" for d in dups)

    release.set()
    done = await first
    assert done.status_code == 200 and pg.counts["pg_write"] == 1
    after = await invoke(pg, token=6)
    assert after.json() == done.json() and pg.counts["pg_write"] == 1


async def test_simultaneous_claims_have_one_winner(pg):
    results = await asyncio.gather(*[invoke(pg, token=6, call="race") for _ in range(8)])
    codes = sorted(r.status_code for r in results)
    assert codes.count(200) >= 1 and set(codes) <= {200, 503}
    assert pg.counts["pg_write"] == 1
    bodies = {str(r.json()) for r in results if r.status_code == 200}
    assert len(bodies) == 1


# ---- sweep ------------------------------------------------------------------------------------


async def test_startup_sweep_interrupts_stale_non_idempotent_claims_on_postgres(pg):
    async with pg.maker() as db:
        for call, tool in (("old-w", "pg_write"), ("old-r", "pg_read"), ("fresh-w", "pg_write")):
            db.add(ToolInvocation(
                id=uuid.uuid4(), tenant_id=pg.tenant, idempotency_key=f"r1:{call}", session_id=SESSION, tool=tool,
                args_hash="h", status="in_flight", started_at=utcnow() - timedelta(hours=1 if call.startswith("old") else 0),
            ))
        await db.commit()
    # pg_write/pg_read are test tools: declare the idempotent set explicitly via the real spec table.
    assert await interrupt_stale_tool_invocations(pg.factory) == 1
    async with pg.maker() as db:
        status = dict((await db.execute(select(ToolInvocation.idempotency_key, ToolInvocation.status))).all())
    assert status == {"r1:old-w": "completed", "r1:old-r": "in_flight", "r1:fresh-w": "in_flight"}
    rec = await reconcile(pg, 1, "r1:old-w")
    assert rec.status_code == 200 and rec.json()["details"] == {"error": "interrupted", "outcome": "unknown"}


# ---- grants (needs the migrated baseline) ----------------------------------------------------


@pytest.fixture(scope="module")
def migrated(pg_server, pg_has_vector):
    if not pg_has_vector:
        pytest.skip("pgvector extension not available for this Postgres; CI runs this tier fully")
    pg_server.recreate_db("oniva_fencing_migrated")
    owner = pg_server.url("oniva_fencing_migrated")
    res = run_alembic(owner, "upgrade", "head")
    assert res.returncode == 0, res.stderr[-3000:]
    eng = create_engine(owner)
    with eng.begin() as c:
        c.execute(text(f"ALTER ROLE oniva_app PASSWORD '{PW}'"))
        c.execute(text(f"ALTER ROLE oniva_system PASSWORD '{PW}'"))
    eng.dispose()
    return owner


async def test_system_role_admits_locks_and_raises_with_the_migrated_grants(migrated):
    engine = create_async_engine(_role_url(migrated, "oniva_system", "postgresql+asyncpg"))
    _, factory = make_factory(engine)
    try:
        assert await fence.admit_fence(factory, "s-grants", 6) == 6
        async with fence.hold_write_fence(factory, "s-grants", 6):
            with pytest.raises(fence.FenceBusy):
                await fence.admit_fence(factory, "s-grants", 7, wait_ms=200)
            assert await fence.admit_fence(factory, "s-grants", 6) == 6  # same token: no wait
        assert await fence.admit_fence(factory, "s-grants", 7) == 7
        with pytest.raises(fence.FenceRejected) as err:
            await fence.admit_fence(factory, "s-grants", 6)
        assert err.value.high_water == 7
        with pytest.raises(fence.FenceRejected):
            async with fence.hold_write_fence(factory, "s-grants", 6):
                pass
    finally:
        await engine.dispose()
