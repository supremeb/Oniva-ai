"""U5 / R3, R6-R9 / AE2-AE4: invoke, idempotency, fencing and reconcile.

The contract is engine/test/support/fake-gateway.ts. These tests run on a file-backed SQLite so
each request gets its own connection (concurrent requests are real); the row-lock semantics
(FOR SHARE, successor waits) are covered on Postgres in tests/postgres/test_fencing.py.
"""
import asyncio
import uuid
from contextlib import asynccontextmanager, suppress
from datetime import timedelta

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agents.skills.registry import Skill, _registry
from app.config import Settings
from app.internal_api import invocations, tools
from app.internal_api.execution import ToolRateLimited
from app.internal_api.invocations import INTERRUPTED_OUTCOME, get_session_factory, utcnow
from app.internal_api.router import mount_internal_router
from app.internal_api.tool_specs import TOOL_SPECS, ToolSpec
from app.models import Base
from app.models.engine import EngineConversation, EngineFence, ToolInvocation
from app.models.project import Project
from app.models.tenant import Tenant
from app.models.user import User
from app.services.startup_recovery import interrupt_stale_tool_invocations

GATEWAY = "g" * 12 + "Gateway-Token-0123456789-abcdefXYZ"
ENGINE_SVC = "e" * 12 + "Engine-Svc-Token-0123456789-abcdXY"
SECRET = "Zk3vN8qLw1RtYb6HdPx9CfUe2MaS5JoG7iVn4TlKq0A"
AUTH = {"Authorization": f"Bearer {GATEWAY}"}
SESSION = "sess-1"


class World:
    """Seeded tenants, a tool set with effect counters, and the HTTP client."""


@pytest.fixture
async def maker(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'gw.db'}", connect_args={"timeout": 30}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def factory(maker):
    @asynccontextmanager
    async def _factory():
        async with maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    return _factory


@pytest.fixture
def effects(monkeypatch):
    """Effect counters and gates for three spy tools: a write, an idempotent write, a read."""
    counts = {"spy_write": 0, "spy_idem": 0, "spy_read": 0}
    gate = {"start": asyncio.Event(), "release": asyncio.Event(), "hold": False}
    fail = {"spy_write": False}

    def make(name):
        async def handler(ctx, path: str = "a"):
            if gate["hold"] and name == "spy_write":
                gate["start"].set()
                await gate["release"].wait()
            if fail.get(name):
                counts[name] += 1
                raise RuntimeError("boom")
            counts[name] += 1
            return {"tool": name, "path": path, "n": counts[name], "user": str(ctx.user_id)}

        _registry[name] = Skill(name=name, description=name, category="test", handler=handler)

    for n, spec in (
        ("spy_write", ToolSpec(side_effect="write", idempotent=False, timeout_ms=10_000)),
        ("spy_idem", ToolSpec(side_effect="write", idempotent=True, timeout_ms=10_000)),
        ("spy_read", ToolSpec(side_effect="none", idempotent=True, timeout_ms=10_000)),
    ):
        make(n)
        monkeypatch.setitem(TOOL_SPECS, n, spec)
    yield type("E", (), {"counts": counts, "gate": gate, "fail": fail})
    for n in counts:
        _registry.pop(n, None)


@pytest.fixture
async def gw(maker, factory, effects):
    await tools.ensure_skills_loaded()
    app = FastAPI()
    mount_internal_router(
        app, Settings(_env_file=None, SECRET_KEY=SECRET, ENGINE_GATEWAY_TOKEN=GATEWAY, ENGINE_SERVICE_TOKEN=ENGINE_SVC)
    )
    app.dependency_overrides[get_session_factory] = lambda: factory

    w = World()
    w.maker, w.factory, w.effects = maker, factory, effects
    w.tenant_a, w.tenant_b = uuid.uuid4(), uuid.uuid4()
    w.user_a, w.user_b = uuid.uuid4(), uuid.uuid4()
    w.project_a, w.project_b = uuid.uuid4(), uuid.uuid4()
    w.conv_a, w.conv_b = uuid.uuid4(), uuid.uuid4()
    async with maker() as db:
        for t, u, p, c, tag in (
            (w.tenant_a, w.user_a, w.project_a, w.conv_a, "a"),
            (w.tenant_b, w.user_b, w.project_b, w.conv_b, "b"),
        ):
            db.add(Tenant(id=t, name=tag, slug=f"t-{tag}-{t.hex[:6]}", is_active=True, settings={}))
        await db.commit()
        for t, u, p, c, tag in (
            (w.tenant_a, w.user_a, w.project_a, w.conv_a, "a"),
            (w.tenant_b, w.user_b, w.project_b, w.conv_b, "b"),
        ):
            db.add(User(id=u, tenant_id=t, email=f"{tag}@x.io", name=tag))
        await db.commit()
        for t, u, p, c, tag in (
            (w.tenant_a, w.user_a, w.project_a, w.conv_a, "a"),
            (w.tenant_b, w.user_b, w.project_b, w.conv_b, "b"),
        ):
            db.add(Project(id=p, tenant_id=t, name=tag, owner_id=u))
        await db.commit()
        for t, u, p, c, tag in (
            (w.tenant_a, w.user_a, w.project_a, w.conv_a, "a"),
            (w.tenant_b, w.user_b, w.project_b, w.conv_b, "b"),
        ):
            db.add(EngineConversation(conversation_id=c, tenant_id=t, project_id=p, user_id=u, session_id=SESSION))
        await db.commit()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        w.client = client
        yield w
    pending = list(tools._effects)
    if pending:
        await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), 10)


def body(w, *, run="r1", call="c1", args=None, tenant="a", conversation=True, tool=None):
    t, p, c = (w.tenant_a, w.project_a, w.conv_a) if tenant == "a" else (w.tenant_b, w.project_b, w.conv_b)
    b = {"args": args if args is not None else {"path": "a"}, "runId": run, "toolCallId": call,
         "tenantId": str(t), "projectId": str(p)}
    if conversation:
        b["conversationId"] = str(c)
    return b


def headers(run="r1", call="c1", token=None, session=SESSION):
    h = {**AUTH, "Idempotency-Key": f"{run}:{call}"}
    if token is not None:
        h["X-Engine-Fence"] = str(token)
    if session is not None:
        h["X-Engine-Session"] = session
    return h


def fence_only(token, session=SESSION):
    return {**AUTH, "X-Engine-Fence": str(token), "X-Engine-Session": session}


async def invoke(w, name="spy_write", token=None, session=SESSION, **kw):
    b = body(w, **{k: v for k, v in kw.items() if k in ("run", "call", "args", "tenant", "conversation")})
    return await w.client.post(
        f"/internal/tools/{name}/invoke", json=b,
        headers=headers(kw.get("run", "r1"), kw.get("call", "c1"), token, session),
    )


async def rows(w):
    async with w.maker() as db:
        return (await db.execute(select(ToolInvocation))).scalars().all()


async def high_water(w, session=SESSION):
    async with w.maker() as db:
        return (await db.execute(select(EngineFence.high_water).where(EngineFence.session_id == session))).scalar_one_or_none()


# ---- AE3: idempotency -----------------------------------------------------------


async def test_same_key_twice_runs_effect_once_and_answers_equal_AE3(gw):
    r1 = await invoke(gw, token=7)
    r2 = await invoke(gw, token=7)
    assert r1.status_code == r2.status_code == 200
    assert r1.json() == r2.json()
    assert r1.json()["isError"] is False
    assert gw.effects.counts["spy_write"] == 1
    assert r1.headers["cache-control"] == "no-store"
    [row] = await rows(gw)
    assert row.status == "completed" and row.fence_token == 7 and row.session_id == SESSION
    assert row.outcome == r1.json()


async def test_user_comes_from_the_conversation_not_the_engine(gw):
    r = await invoke(gw)
    assert str(gw.user_a) in r.json()["content"]


async def test_same_key_different_args_is_422(gw):
    assert (await invoke(gw, args={"path": "a"})).status_code == 200
    r = await invoke(gw, args={"path": "b"})
    assert r.status_code == 422 and r.json() == {"error": "idempotency_key_reuse"}
    assert gw.effects.counts["spy_write"] == 1


async def test_same_key_other_tool_is_422(gw):
    await invoke(gw, "spy_write")
    r = await invoke(gw, "spy_idem")
    assert r.status_code == 422
    assert gw.effects.counts["spy_idem"] == 0


async def test_tool_error_is_stored_and_a_write_is_never_retried(gw):
    gw.effects.fail["spy_write"] = True
    r1 = await invoke(gw)
    assert r1.status_code == 200 and r1.json()["isError"] is True
    assert r1.json()["details"]["error"] == "RuntimeError"
    gw.effects.fail["spy_write"] = False
    r2 = await invoke(gw)
    assert r2.json() == r1.json()
    assert gw.effects.counts["spy_write"] == 1  # the failed call was not run again


# ---- AE2: fencing -----------------------------------------------------------------


async def test_stale_token_is_409_with_no_effect_and_no_outcome_AE2(gw):
    assert (await invoke(gw, token=7, call="c1")).status_code == 200
    r = await invoke(gw, token=6, call="c2")
    assert r.status_code == 409
    assert r.json() == {"error": "fence_rejected", "highWater": 7}
    assert gw.effects.counts["spy_write"] == 1
    assert [x.idempotency_key for x in await rows(gw)] == ["r1:c1"]  # c2 left no row
    assert await high_water(gw) == 7


async def test_stale_token_is_refused_even_for_a_completed_key(gw):
    await invoke(gw, token=6)
    await gw.client.get("/internal/tools/manifest", headers=fence_only(7))
    r = await invoke(gw, token=6)
    assert r.status_code == 409 and r.json()["highWater"] == 7


async def test_manifest_with_higher_token_raises_the_mark_for_later_invokes(gw):
    m = await gw.client.get("/internal/tools/manifest", headers=fence_only(7))
    assert m.status_code == 200
    assert await high_water(gw) == 7
    r = await invoke(gw, token=6)
    assert r.status_code == 409 and r.json()["highWater"] == 7
    assert gw.effects.counts["spy_write"] == 0 and await rows(gw) == []


async def test_reconcile_with_higher_token_raises_the_mark(gw):
    rec = await gw.client.get("/internal/tools/invocations/r9:c9", headers=fence_only(9))
    assert rec.status_code == 404
    assert (await invoke(gw, token=8)).status_code == 409
    assert (await invoke(gw, token=9)).status_code == 200  # equal token: same owner, admitted


async def test_lower_token_manifest_and_reconcile_are_rejected(gw):
    await gw.client.get("/internal/tools/manifest", headers=fence_only(7))
    for url in ("/internal/tools/manifest", "/internal/tools/invocations/r1:c1"):
        r = await gw.client.get(url, headers=fence_only(5))
        assert r.status_code == 409 and r.json() == {"error": "fence_rejected", "highWater": 7}


async def test_the_mark_never_goes_down(gw):
    await gw.client.get("/internal/tools/manifest", headers=fence_only(7))
    await gw.client.get("/internal/tools/manifest", headers=fence_only(3))
    assert await high_water(gw) == 7


async def test_invoke_session_defaults_to_tenant_project(gw):
    key = f"{gw.tenant_a}/{gw.project_a}"
    assert (await invoke(gw, token=4, session=None)).status_code == 200
    assert await high_water(gw, key) == 4
    assert (await invoke(gw, token=3, session=None, call="c2")).status_code == 409


async def test_fence_without_session_on_manifest_and_reconcile_is_unchecked(gw):
    await gw.client.get("/internal/tools/manifest", headers=fence_only(7))
    r = await gw.client.get("/internal/tools/manifest", headers={**AUTH, "X-Engine-Fence": "1"})
    assert r.status_code == 200


async def test_no_fence_header_means_no_fence_row(gw):
    assert (await invoke(gw, session=None)).status_code == 200
    assert await high_water(gw) is None


async def test_malformed_fence_is_400(gw):
    for bad in ("abc", "-1", "1.5", ""):
        r = await gw.client.post(
            "/internal/tools/spy_write/invoke", json=body(gw),
            headers={**headers(), "X-Engine-Fence": bad},
        )
        assert r.status_code == 400 and r.json() == {"error": "invalid_fence"}, bad
    assert gw.effects.counts["spy_write"] == 0


async def test_raise_between_admission_and_commit_rejects_and_stores_nothing_AE4(gw, monkeypatch):
    """An invoke accepted just before a successor raised the mark causes no effect."""
    real_begin = invocations.begin

    async def begin_then_successor_raises(*a, **kw):
        started = await real_begin(*a, **kw)
        await gw.client.get("/internal/tools/manifest", headers=fence_only(7))
        return started

    monkeypatch.setattr(invocations, "begin", begin_then_successor_raises)
    r = await invoke(gw, token=6)
    assert r.status_code == 409 and r.json()["highWater"] == 7
    assert gw.effects.counts["spy_write"] == 0
    assert await rows(gw) == []  # the claim was withdrawn: reconcile's 404 stays true
    rec = await gw.client.get("/internal/tools/invocations/r1:c1", headers=fence_only(7))
    assert rec.status_code == 404


async def test_read_tool_rechecks_the_mark_at_start(gw, monkeypatch):
    real_begin = invocations.begin

    async def begin_then_successor_raises(*a, **kw):
        started = await real_begin(*a, **kw)
        await gw.client.get("/internal/tools/manifest", headers=fence_only(7))
        return started

    monkeypatch.setattr(invocations, "begin", begin_then_successor_raises)
    r = await invoke(gw, "spy_read", token=6)
    assert r.status_code == 409
    assert gw.effects.counts["spy_read"] == 0


# ---- reconcile ----------------------------------------------------------------------


async def test_reconcile_returns_the_stored_outcome(gw):
    r = await invoke(gw, token=6)
    rec = await gw.client.get("/internal/tools/invocations/r1:c1", headers=fence_only(6))
    assert rec.status_code == 200 and rec.json() == r.json()
    assert gw.effects.counts["spy_write"] == 1


async def test_reconcile_unknown_key_is_404(gw):
    rec = await gw.client.get("/internal/tools/invocations/nope:nope", headers=fence_only(1))
    assert rec.status_code == 404 and rec.json() == {"error": "not_found"}


async def test_reconcile_of_another_session_is_404(gw):
    await invoke(gw, token=6)
    rec = await gw.client.get("/internal/tools/invocations/r1:c1", headers=fence_only(1, "other-session"))
    assert rec.status_code == 404


async def test_reconcile_requires_the_service_token(gw):
    await invoke(gw)
    r = await gw.client.get("/internal/tools/invocations/r1:c1")
    assert r.status_code == 401


async def test_reconcile_of_an_in_flight_key_is_404_and_concurrent_duplicate_is_503(gw):
    gw.effects.gate["hold"] = True
    first = asyncio.create_task(invoke(gw, token=6))
    await asyncio.wait_for(gw.effects.gate["start"].wait(), 5)

    rec = await gw.client.get("/internal/tools/invocations/r1:c1", headers=fence_only(6))
    assert rec.status_code == 404  # in flight is not an outcome
    dup = await invoke(gw, token=6)
    assert dup.status_code == 503
    assert dup.headers["retry-after"] == "1"

    gw.effects.gate["release"].set()
    done = await first
    assert done.status_code == 200
    assert gw.effects.counts["spy_write"] == 1  # two writes never run twice
    again = await invoke(gw, token=6)
    assert again.json() == done.json()


async def test_effect_completes_after_the_client_disconnects(gw):
    gw.effects.gate["hold"] = True
    request = asyncio.create_task(invoke(gw, token=6))
    await asyncio.wait_for(gw.effects.gate["start"].wait(), 5)
    request.cancel()  # the engine timed out or died
    with suppress(asyncio.CancelledError):
        await request

    assert (await rows(gw))[0].status == "in_flight"
    gw.effects.gate["release"].set()
    await asyncio.wait_for(asyncio.gather(*list(tools._effects)), 5)

    assert gw.effects.counts["spy_write"] == 1
    rec = await gw.client.get("/internal/tools/invocations/r1:c1", headers=fence_only(6))
    assert rec.status_code == 200 and rec.json()["isError"] is False
    assert (await rows(gw))[0].status == "completed"


# ---- stale in-flight ------------------------------------------------------------------


async def age_claims(w, seconds):
    async with w.maker() as db:
        await db.execute(update(ToolInvocation).values(started_at=utcnow() - timedelta(seconds=seconds)))
        await db.commit()


async def stuck_claim(w, name, call="c1"):
    """Leave an in-flight claim behind, as a worker that died mid-effect would."""
    async with w.maker() as db:
        db.add(ToolInvocation(
            id=uuid.uuid4(), tenant_id=w.tenant_a, idempotency_key=f"r1:{call}", session_id=SESSION, tool=name,
            args_hash=invocations.args_hash({"path": "a"}), status="in_flight", started_at=utcnow(),
        ))
        await db.commit()


async def test_fresh_in_flight_claim_is_busy(gw):
    await stuck_claim(gw, "spy_idem")
    r = await invoke(gw, "spy_idem")
    assert r.status_code == 503 and gw.effects.counts["spy_idem"] == 0


async def test_stale_idempotent_claim_is_taken_over_and_rerun(gw):
    await stuck_claim(gw, "spy_idem")
    await age_claims(gw, 10 + invocations.STALE_GRACE_S + 5)
    r = await invoke(gw, "spy_idem")
    assert r.status_code == 200 and r.json()["isError"] is False
    assert gw.effects.counts["spy_idem"] == 1
    assert (await rows(gw))[0].status == "completed"


async def test_stale_non_idempotent_claim_is_answered_interrupted_not_rerun(gw):
    await stuck_claim(gw, "spy_write")
    await age_claims(gw, 10 + invocations.STALE_GRACE_S + 5)
    r = await invoke(gw, "spy_write")
    assert r.status_code == 200 and r.json() == INTERRUPTED_OUTCOME
    assert gw.effects.counts["spy_write"] == 0
    rec = await gw.client.get("/internal/tools/invocations/r1:c1", headers=fence_only(1))
    assert rec.json()["details"] == {"error": "interrupted", "outcome": "unknown"}


async def test_startup_sweep_interrupts_only_stale_non_idempotent_claims(gw):
    await stuck_claim(gw, "spy_write", "old-w")
    await stuck_claim(gw, "spy_idem", "old-i")
    await age_claims(gw, 3600)
    await stuck_claim(gw, "spy_write", "fresh-w")

    assert await interrupt_stale_tool_invocations(gw.maker) == 1
    by_key = {r.idempotency_key: r for r in await rows(gw)}
    assert by_key["r1:old-w"].status == "completed" and by_key["r1:old-w"].outcome == INTERRUPTED_OUTCOME
    assert by_key["r1:old-i"].status == "in_flight"
    assert by_key["r1:fresh-w"].status == "in_flight"
    rec = await gw.client.get("/internal/tools/invocations/r1:old-w", headers=fence_only(1))
    assert rec.status_code == 200 and rec.json()["details"]["outcome"] == "unknown"


# ---- request validation ---------------------------------------------------------------


async def test_unknown_tool_is_404(gw):
    r = await gw.client.post("/internal/tools/nope/invoke", json=body(gw), headers=headers())
    assert r.status_code == 404 and r.json() == {"error": "unknown_tool"}


async def test_interactive_only_tool_is_unknown(gw):
    r = await gw.client.post("/internal/tools/ask_user/invoke", json=body(gw), headers=headers())
    assert r.status_code == 404


async def test_key_must_equal_run_and_tool_call(gw):
    for key in ("r1:other", "r1", None):
        h = {**headers()}
        h.pop("Idempotency-Key")
        if key:
            h["Idempotency-Key"] = key
        r = await gw.client.post("/internal/tools/spy_write/invoke", json=body(gw), headers=h)
        assert r.status_code == 400 and r.json()["error"] == "invalid_request", key
    assert gw.effects.counts["spy_write"] == 0 and await rows(gw) == []


async def test_malformed_bodies_are_400(gw):
    bad = [
        {"runId": "r1", "toolCallId": "c1"},
        {**body(gw), "args": []},
        {**body(gw), "tenantId": 5},
        {**body(gw), "tenantId": "not-a-uuid"},
    ]
    for b in bad:
        r = await gw.client.post("/internal/tools/spy_write/invoke", json=b, headers=headers())
        assert r.status_code == 400 and r.json()["error"] == "invalid_request", b
    r = await gw.client.post("/internal/tools/spy_write/invoke", content=b"{nope", headers=headers())
    assert r.status_code == 400
    assert gw.effects.counts["spy_write"] == 0


async def test_arguments_are_validated_against_the_tool_signature(gw):
    for args in ({"path": 5}, {"bogus": 1}):
        r = await invoke(gw, args=args)
        assert r.status_code == 400 and r.json()["error"] == "invalid_request", args
    assert gw.effects.counts["spy_write"] == 0 and await rows(gw) == []


async def test_requires_the_service_token(gw):
    r = await gw.client.post("/internal/tools/spy_write/invoke", json=body(gw), headers={"Idempotency-Key": "r1:c1"})
    assert r.status_code == 401


async def test_rate_limited_call_releases_its_claim(gw, monkeypatch):
    async def limited(*a, **kw):
        raise ToolRateLimited(7)

    monkeypatch.setattr(tools, "execute_tool", limited)
    r = await invoke(gw)
    assert r.status_code == 429 and r.headers["retry-after"] == "7"
    assert await rows(gw) == []


# ---- tenant isolation (R3) -------------------------------------------------------------


async def test_another_tenant_cannot_read_or_reuse_a_key(gw):
    mine = await invoke(gw, tenant="a")
    theirs = await invoke(gw, tenant="b")  # same run:call, different tenant
    assert theirs.status_code == 422
    assert "content" not in theirs.json()
    assert gw.effects.counts["spy_write"] == 1
    assert [r.tenant_id for r in await rows(gw)] == [gw.tenant_a]
    assert mine.status_code == 200


async def test_project_of_another_tenant_is_404_without_running(gw):
    b = body(gw)
    b["projectId"] = str(gw.project_b)
    r = await gw.client.post("/internal/tools/spy_write/invoke", json=b, headers=headers())
    assert r.status_code == 404 and r.json() == {"error": "project_not_found"}
    assert gw.effects.counts["spy_write"] == 0 and await rows(gw) == []


async def test_conversation_of_another_tenant_is_404_without_running(gw):
    b = body(gw)
    b["conversationId"] = str(gw.conv_b)
    r = await gw.client.post("/internal/tools/spy_write/invoke", json=b, headers=headers())
    assert r.status_code == 404 and r.json() == {"error": "conversation_not_found"}
    assert gw.effects.counts["spy_write"] == 0 and await rows(gw) == []


async def test_unknown_or_disabled_tenant_is_404(gw):
    b = body(gw)
    b["tenantId"] = str(uuid.uuid4())
    assert (await gw.client.post("/internal/tools/spy_write/invoke", json=b, headers=headers())).status_code == 404
    async with gw.maker() as db:
        await db.execute(update(Tenant).where(Tenant.id == gw.tenant_a).values(is_active=False))
        await db.commit()
    assert (await invoke(gw)).status_code == 404
    assert gw.effects.counts["spy_write"] == 0


async def test_missing_conversation_is_400_once_the_tenant_uses_engine_conversations(gw):
    r = await invoke(gw, conversation=False)
    assert r.status_code == 400 and r.json() == {"error": "conversation_required"}
    assert gw.effects.counts["spy_write"] == 0 and await rows(gw) == []


async def test_missing_conversation_is_accepted_for_a_tenant_without_bindings(gw):
    async with gw.maker() as db:
        await db.execute(EngineConversation.__table__.delete().where(EngineConversation.tenant_id == gw.tenant_a))
        await db.commit()
    r = await invoke(gw, conversation=False)
    assert r.status_code == 200
    assert str(invocations.NIL_USER) in r.json()["content"]


# ---- connector target enforcement (conversation selections) ---------------------


async def _select_connectors(gw, connector_ids, providers=()):
    from app.models.connector import Connector

    async with gw.maker() as db:
        for cid, prov in providers:
            db.add(Connector(id=uuid.UUID(cid), user_id=gw.user_a, tenant_id=gw.tenant_a, connector_type="rest_api",
                             provider=prov, name=prov, config={}))
        await db.execute(update(EngineConversation).where(EngineConversation.conversation_id == gw.conv_a)
                         .values(selections={"connector_ids": connector_ids}))
        await db.commit()


@pytest.mark.parametrize("name", ["invoke_connector_tool", "get_connector_schema"])
async def test_connector_id_outside_selections_is_a_tool_error_not_http(gw, name):
    allowed, other = str(uuid.uuid4()), str(uuid.uuid4())
    await _select_connectors(gw, [allowed])
    r = await invoke(gw, name, args={"connector_id": other, "tool_name": "t"})
    assert r.status_code == 200
    out = r.json()
    assert out["isError"] is True and out["details"]["error"] == "connector_not_allowed"
    assert await rows(gw) == []  # nothing claimed


async def test_selected_connector_id_passes_the_gate(gw):
    allowed = str(uuid.uuid4())
    await _select_connectors(gw, [allowed])
    denial = await invocations.connector_denial(
        gw.factory, str(gw.tenant_a), str(gw.conv_a), "invoke_connector_tool", {"connector_id": allowed})
    assert denial is None


async def test_empty_selection_denies_all_and_missing_selections_are_unbound(gw):
    await _select_connectors(gw, [])
    r = await invoke(gw, "invoke_connector_tool", args={"connector_id": "x", "tool_name": "t"})
    assert r.json()["details"]["error"] == "connector_not_allowed"
    async with gw.maker() as db:
        await db.execute(update(EngineConversation).where(EngineConversation.conversation_id == gw.conv_a)
                         .values(selections=None))
        await db.commit()
    assert await invocations.connector_denial(
        gw.factory, str(gw.tenant_a), str(gw.conv_a), "invoke_connector_tool", {"connector_id": "x"}) is None


async def test_provider_wrappers_require_a_selected_connector_of_that_provider(gw):
    lin, snow = str(uuid.uuid4()), str(uuid.uuid4())
    await _select_connectors(gw, [lin], providers=[(lin, "linear"), (snow, "servicenow")])
    for name, provider_ok in (("linear_search_issues", True), ("linear_create_issue", True),
                              ("servicenow_search_incidents", False), ("servicenow_create_incident", False)):
        args = {"linear_search_issues": {"query": "q"}, "linear_create_issue": {"title": "t"},
                "servicenow_search_incidents": {"query": "q"},
                "servicenow_create_incident": {"short_description": "t"}}[name]
        denial = await invocations.connector_denial(gw.factory, str(gw.tenant_a), str(gw.conv_a), name, args)
        assert (denial is None) is provider_ok, name
        if not provider_ok:
            r = await invoke(gw, name, args=args, run=f"r-{name}")
            assert r.status_code == 200 and r.json()["details"]["error"] == "connector_not_allowed"
