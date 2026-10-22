"""U31: the chat curation sweep. Off means no work at all; on, it starts one curator run per idle chat state,
idempotently, bounded per tenant per day, and only for conversations whose project has memory on.

Fixtures come from tests/internal (the SQLite world of the gateway tests). The advisory-lock behaviour is
tested on Postgres in tests/postgres/test_memory_curation_pg.py.
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import update

from app.config import Settings
from app.models.deep_work_run import DeepWorkRun
from app.models.engine import EngineConversation
from app.services import memory_curator as mc
from tests.internal import test_invoke as ti
from tests.internal.test_invoke import effects, factory, gw, maker  # noqa: F401  (fixtures)
from tests.internal.test_memory_curate_routes import SETTINGS, FakeEngine, mem  # noqa: F401  (fixtures)


def settings(**kw):
    return Settings(**SETTINGS, MEMORY_CURATOR_MODE="auto", MEMORY_CURATE_IDLE_S=900, **kw)


def sweep(w, **kw):
    now = datetime.now(timezone.utc) + timedelta(hours=1)  # every seeded conversation has been idle for an hour
    return mc.CurationSweep(w.factory, kw.pop("settings", settings()), engine_factory=lambda: w.engine,
                            clock=lambda: now)


async def test_off_does_no_work_at_all(mem):
    def boom():
        raise AssertionError("the database was touched")

    s = mc.CurationSweep(boom, Settings(**SETTINGS), engine_factory=boom)
    assert await s.run_once() == {"checked": 0, "started": 0}
    s.start()
    assert s._task is None  # the loop is not even created while off
    await s.stop()


async def test_an_idle_chat_in_a_memory_project_is_started_once(mem):
    s = sweep(mem)
    first = await s.run_once()
    assert first == {"checked": 2, "started": 1, "skipped": 1}  # tenant b's chat has memory off
    ((session, tenant, project, body, key),) = mem.engine.started
    assert (tenant, project) == (str(mem.tenant_a), str(mem.project_a))
    assert body["kind"] == "memory_curate" and body["input"]["conversationId"] == str(mem.conv_a)
    assert body["input"]["runId"].startswith("u") and key == f"curate:{mem.conv_a}:{body['input']['runId']}"
    assert body["id"] == str(mc.curator_conversation_id(mem.conv_a, body["input"]["runId"]))
    # same state: no second start from the same sweep ...
    assert await s.run_once() == {"checked": 0, "started": 0, "skipped": 0}
    assert len(mem.engine.started) == 1
    # ... nor from a fresh one (another replica, a restart) once the run's prepare call has been recorded
    from app.internal_api import invocations

    run_id = body["input"]["runId"]
    began = await invocations.begin(
        mem.factory, key=f"curate:{mem.conv_a}:{run_id}:prepare", tenant_id=str(mem.tenant_a), session_id=ti.SESSION,
        tool=mc.TOOL_PREPARE, arguments_hash="h", fence_token=None, timeout_ms=1000, idempotent=True)
    assert began.kind == "run"
    assert (await sweep(mem).run_once())["started"] == 0
    assert len(mem.engine.started) == 1


async def test_a_new_turn_is_a_new_run(mem):
    s = sweep(mem)
    await s.run_once()
    async with mem.maker() as db:
        await db.execute(update(EngineConversation).where(EngineConversation.conversation_id == mem.conv_a)
                         .values(updated_at=datetime.now(timezone.utc) + timedelta(minutes=5)))
        await db.commit()
    again = await s.run_once()
    assert again["started"] == 1
    runs = [b["input"]["runId"] for _, _, _, b, _ in mem.engine.started]
    assert len(set(runs)) == 2


async def test_only_idle_chats_of_enabled_projects_are_candidates(mem):
    async with mem.maker() as db:
        # tenant b: memory off. A third conversation of tenant a: archived. A fourth: an automation run.
        # A fifth: a Deep Work run (curated on completion instead).
        extra = [uuid.uuid4() for _ in range(3)]
        db.add(EngineConversation(conversation_id=extra[0], tenant_id=mem.tenant_a, project_id=mem.project_a,
                                  user_id=mem.user_a, session_id=ti.SESSION, archived_at=datetime.now(timezone.utc)))
        db.add(EngineConversation(conversation_id=extra[1], tenant_id=mem.tenant_a, project_id=mem.project_a,
                                  user_id=mem.user_a, session_id=ti.SESSION, run_kind="workflow",
                                  run_ref_id=uuid.uuid4()))
        db.add(EngineConversation(conversation_id=extra[2], tenant_id=mem.tenant_a, project_id=mem.project_a,
                                  user_id=mem.user_a, session_id=ti.SESSION))
        await db.flush()
        db.add(DeepWorkRun(id=uuid.uuid4(), tenant_id=mem.tenant_a, project_id=mem.project_a, user_id=mem.user_a,
                           conversation_id=extra[2], query="q", status="completed", phase="done"))
        await db.commit()
    await sweep(mem).run_once()
    assert [b["input"]["conversationId"] for _, _, _, b, _ in mem.engine.started] == [str(mem.conv_a)]


async def test_a_conversation_active_within_the_idle_window_is_left_alone(mem):
    now = datetime.now(timezone.utc)
    s = mc.CurationSweep(mem.factory, settings(), engine_factory=lambda: mem.engine, clock=lambda: now)
    assert (await s.run_once())["checked"] == 0  # updated moments ago
    assert mem.engine.started == []


async def test_the_daily_cap_bounds_starts_per_tenant(mem):
    async with mem.maker() as db:
        for _ in range(2):
            db.add(EngineConversation(conversation_id=uuid.uuid4(), tenant_id=mem.tenant_a,
                                      project_id=mem.project_a, user_id=mem.user_a, session_id=ti.SESSION))
        await db.commit()
    s = sweep(mem, settings=settings(MEMORY_CURATE_MAX_RUNS_PER_TENANT_DAY=2))
    out = await s.run_once()
    assert out["started"] == 2 and out["skipped"] == 2 and len(mem.engine.started) == 2


async def test_a_failed_start_is_retried_on_the_next_pass(mem):
    from app.services.engine_client import EngineError

    real = mem.engine.start_run

    async def down(*a, **k):
        raise EngineError(502, "engine_unreachable")

    mem.engine.start_run = down
    s = sweep(mem)
    assert (await s.run_once())["started"] == 0
    mem.engine.start_run = real
    assert (await s.run_once())["started"] == 1


async def test_the_engine_without_the_task_kind_starts_nothing(mem):
    mem.engine.kinds = []
    assert (await sweep(mem).run_once())["started"] == 0
    assert mem.engine.started == []
