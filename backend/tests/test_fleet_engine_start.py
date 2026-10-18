"""Plan 003 U7: Fleet runs start on the engine (SQLite tier, fake engine)."""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.api.v1 import background_jobs as bj
from app.models.background_job import BackgroundJob, JobType
from app.models.custom_agent import CustomAgent
from app.models.engine import EngineConversation
from app.schemas.background_job import BackgroundJobCreate
from app.services import fleet_engine as fe
from app.services import run_handlers as rh
from app.services import run_reconciler as rr
from app.services.automation_scope import run_conversation_id
from app.services.engine_client import EngineError
from tests.internal.test_automation_scope import seed
from tests.internal.test_invoke import maker, factory  # noqa: F401  (fixtures)


class FakeEngine:
    """Engine semantics that matter here: a repeated create is 409, a repeated key is deduplicated."""

    def __init__(self):
        self.directory = SimpleNamespace(default_session_id="s1")
        self.created = {}
        self.submissions = {}
        self.create_calls = 0
        self.submit_calls = 0
        self.down = False

    async def create_conversation(self, session, tenant, project, conversation, agent=None):
        self.create_calls += 1
        if self.down:
            raise EngineError(502, "engine_unreachable")
        if conversation in self.created:
            raise EngineError(409, "conflict")
        self.created[conversation] = agent

    async def submit(self, session, tenant, project, conversation, body, key):
        self.submit_calls += 1
        if self.down:
            raise EngineError(502, "engine_unreachable")
        self.submissions.setdefault(key, {"body": body, "conversation": conversation})
        return {"submissionId": "sub-1", "conversationId": str(conversation)}


@pytest.fixture(autouse=True)
def _wiring(monkeypatch):
    rh.reset()
    fe.register_hooks()
    monkeypatch.setattr(fe, "build_fleet_agent_config", fe.build_fleet_agent_config)
    yield
    rh.reset()


@pytest.fixture
def engine(monkeypatch):
    eng = FakeEngine()
    monkeypatch.setattr(fe, "get_engine_client", lambda: eng)
    return eng


@pytest.fixture
def wired(monkeypatch, factory):  # noqa: F811
    """Fleet module tenant sessions use the test database; the manifest accepts every skill."""
    monkeypatch.setattr(fe, "tenant_factory", lambda tenant_id: factory)
    import app.services.fleet_engine_agent as fa

    # SQLite cannot bind ARRAY values: the row is stored without skills and they are supplied here.
    async def load_agent(db, agent_id):
        row = await db.get(CustomAgent, agent_id)
        return SimpleNamespace(name=row.name, system_prompt=row.system_prompt, model=row.model, config={},
                               skills=["web_search", "create_task"], resource_constraints=row.resource_constraints)

    monkeypatch.setattr(fe, "load_agent", load_agent)
    monkeypatch.setattr(fe, "build_fleet_agent_config",
                        lambda agent, job_id, now_ms, connector_summary=None: fa.build_fleet_agent_config(
                            agent, job_id, now_ms, connector_summary, manifest_names=["web_search"]))


async def make_job(maker, *, status="pending", input_data=None, created_ago=0, agent_kw=None):  # noqa: F811
    (t,), (u,) = await seed(maker)
    async with maker() as db:
        agent_id = uuid.uuid4()
        # Core insert: explicit NULLs for the ARRAY columns SQLite cannot bind.
        await db.execute(CustomAgent.__table__.insert().values(
            id=agent_id, user_id=u, tenant_id=t, name="a", system_prompt="Be useful.",
            skills=None, connector_ids=None, knowledge_base_ids=None, workflow_ids=None, compliance_tags=None,
            resource_constraints={"max_tool_calls": 7, "timeout_seconds": 60}, **(agent_kw or {})))
        agent = SimpleNamespace(id=agent_id)
        await db.commit()
        job = BackgroundJob(
            id=uuid.uuid4(), tenant_id=t, user_id=u, agent_id=agent.id, job_type=JobType.BACKGROUND_AGENT.value,
            status=status, trigger="manual", input_data=fe.mark_engine(input_data or {"task": "do it"}),
            execution_trace=[], created_at=datetime.now(timezone.utc) - timedelta(seconds=created_ago))
        db.add(job)
        await db.commit()
    return t, u, agent.id, job.id


async def reload(maker, job_id):  # noqa: F811
    async with maker() as db:
        return await db.get(BackgroundJob, job_id)


async def test_start_creates_one_conversation_and_one_submission(maker, factory, engine, wired):
    t, u, a, job_id = await make_job(maker)
    result = await fe.start_fleet_run(factory, job_id)
    assert result.status == "started"
    assert engine.create_calls == 1 and engine.submit_calls == 1
    conv = run_conversation_id("fleet", job_id)
    assert list(engine.submissions) == [f"fleet:{job_id}"]
    sub = engine.submissions[f"fleet:{job_id}"]
    assert sub["body"] == {"text": "do it", "requestId": f"fleet:{job_id}"} and sub["conversation"] == conv
    cfg = engine.created[conv]
    assert cfg["maxRounds"] == 7 and cfg["tools"] == ["web_search"]
    assert cfg["metadata"] == {"notify": "backend", "runKind": "fleet", "jobId": str(job_id)}
    job = await reload(maker, job_id)
    assert job.status == "running" and job.started_at is not None
    assert job.conversation_id == conv and job.session_id == str(job_id)
    assert job.input_data["engine"] == "oniva-engine" and job.error is None
    async with maker() as db:
        row = (await db.execute(select(EngineConversation))).scalars().one()
    assert (row.run_kind, row.run_ref_id) == ("fleet", job_id) and row.archived_at is not None


async def test_a_repeated_start_makes_no_second_conversation_or_submission(maker, factory, engine, wired):
    _, _, _, job_id = await make_job(maker)
    await fe.start_fleet_run(factory, job_id)
    again = await fe.start_fleet_run(factory, job_id)
    assert again.status == "already_started"
    assert engine.create_calls == 1 and engine.submit_calls == 1


async def test_a_crash_after_the_submit_is_repaired_without_a_second_run(maker, factory, engine, wired):
    """Engine has the run but the job row was never updated: the retry hits 409 and the same key."""
    _, _, _, job_id = await make_job(maker)
    await fe.start_fleet_run(factory, job_id)
    async with maker() as db:
        job = await db.get(BackgroundJob, job_id)
        job.status, job.conversation_id = "pending", None
        await db.commit()
    result = await fe.start_fleet_run(factory, job_id)
    assert result.status == "started"
    assert len(engine.created) == 1 and len(engine.submissions) == 1
    assert (await reload(maker, job_id)).status == "running"


async def test_no_asyncio_task_is_spawned(maker, factory, engine, wired):
    _, _, _, job_id = await make_job(maker)
    before = asyncio.all_tasks()
    await fe.start_fleet_run(factory, job_id)
    assert asyncio.all_tasks() == before


async def test_engine_unreachable_leaves_the_job_pending_with_the_error(maker, factory, engine, wired):
    _, _, _, job_id = await make_job(maker)
    engine.down = True
    result = await fe.start_fleet_run(factory, job_id)
    assert result.status == "failed" and result.attempts == 1
    job = await reload(maker, job_id)
    assert job.status == "pending" and "engine_unavailable" in job.error
    assert job.input_data["start_attempts"] == 1 and job.conversation_id is None
    result = await fe.start_fleet_run(factory, job_id)
    assert result.attempts == 2 and (await reload(maker, job_id)).input_data["start_attempts"] == 2


async def test_task_text_ignores_the_engine_bookkeeping_keys():
    assert fe.task_text({"engine": "oniva-engine", "start_attempts": 2}) == "Execute your configured task."
    assert fe.task_text({"message": "hi", "engine": "oniva-engine"}) == "hi"
    assert fe.task_text({"x": 1, "engine": "oniva-engine"}) == '{"x": 1}'


# ---- dispatch sites -----------------------------------------------------------------------------


async def test_manual_create_with_the_flag_on_uses_the_engine_and_no_asyncio_task(
        maker, factory, engine, wired, monkeypatch):
    t, u, a, _ = await make_job(maker)
    monkeypatch.setattr(fe, "engine_fleet_enabled", lambda tenant: True)
    legacy = []
    import app.services.background_agent_executor as exe
    monkeypatch.setattr(exe, "run_background_agent", lambda **kw: legacy.append(kw))
    before = asyncio.all_tasks()
    async with maker() as db:
        out = await bj.create_job(
            BackgroundJobCreate(job_type="background_agent", agent_id=a, input_data={"task": "go"}),
            db=db, user=SimpleNamespace(id=str(u)), tenant_id=t)
    assert legacy == [] and asyncio.all_tasks() == before
    assert engine.create_calls == 1 and engine.submit_calls == 1
    assert out.id != uuid.UUID(int=0) and out.status == "running"
    key = f"fleet:{out.id}"
    assert engine.submissions[key]["body"]["text"] == "go"


async def test_manual_create_with_the_flag_off_keeps_the_legacy_path(maker, factory, engine, wired, monkeypatch):
    t, u, a, _ = await make_job(maker)
    monkeypatch.setattr(fe, "engine_fleet_enabled", lambda tenant: False)
    called = []

    async def legacy(**kw):
        called.append(kw["job_id"])

    import app.services.background_agent_executor as exe
    monkeypatch.setattr(exe, "run_background_agent", legacy)
    async with maker() as db:
        out = await bj.create_job(
            BackgroundJobCreate(job_type="background_agent", agent_id=a, input_data={"task": "go"}),
            db=db, user=SimpleNamespace(id=str(u)), tenant_id=t)
        await asyncio.sleep(0)
    assert engine.create_calls == 0 and out.status == "pending" and "engine" not in (out.input_data or {})
    assert called == [out.id]


async def test_manual_create_with_the_engine_down_still_returns_the_pending_job(
        maker, factory, engine, wired, monkeypatch):
    t, u, a, _ = await make_job(maker)
    monkeypatch.setattr(fe, "engine_fleet_enabled", lambda tenant: True)
    engine.down = True
    async with maker() as db:
        out = await bj.create_job(
            BackgroundJobCreate(job_type="background_agent", agent_id=a),
            db=db, user=SimpleNamespace(id=str(u)), tenant_id=t)
    assert out.status == "pending" and "engine_unavailable" in out.error


# ---- registry hooks -----------------------------------------------------------------------------

SETTINGS = SimpleNamespace(RUN_RECONCILE_AFTER_S=120, RUN_RECONCILE_INTERVAL_S=60.0,
                           RUN_START_RETRY_MAX=3, RUN_START_RETRY_BASE_S=30.0)


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def reconcile(monkeypatch, factory, engine):  # noqa: F811
    views = {}

    async def read_snapshot(_e, session, tenant, project, conversation):
        if conversation not in views:
            raise EngineError(404, "not_found")
        return views[conversation]

    monkeypatch.setattr(rr, "read_snapshot", read_snapshot)
    clock = Clock()
    rc = rr.RunReconciler(factory, SETTINGS, engine_factory=lambda: engine, clock=clock)
    return rc, clock, views


async def test_find_stuck_returns_only_old_pending_engine_jobs_without_a_conversation(maker, factory, engine, wired):
    _, _, _, stuck = await make_job(maker, created_ago=600)
    await make_job(maker, created_ago=5)                       # too young
    await make_job(maker, status="running", created_ago=600)   # not pending
    t, u, a, legacy = await make_job(maker, created_ago=600)   # legacy job: no engine mark
    async with maker() as db:
        j = await db.get(BackgroundJob, legacy)
        j.input_data = {"task": "x"}
        await db.commit()
    async with factory() as db:
        refs = await fe.find_stuck(db, timedelta(seconds=120))
    assert [r.ref_id for r in refs] == [stuck] and refs[0].kind == "fleet"


async def test_start_missing_starts_the_job_exactly_once_then_already_started(
        maker, factory, engine, wired, reconcile):
    rc, _, views = reconcile
    _, _, _, job_id = await make_job(maker, created_ago=600)
    counts = await rc.run_once()
    assert counts["started"] == 1 and engine.submit_calls == 1
    assert (await reload(maker, job_id)).status == "running"
    # The next pass finds nothing stuck; calling the hook again is a no-op.
    ref = rh.RunRef(kind="fleet", ref_id=job_id, tenant_id=(await reload(maker, job_id)).tenant_id)
    assert (await fe.start_missing(ref)).status == "already_started"
    assert engine.create_calls == 1 and engine.submit_calls == 1


async def test_after_the_bound_the_job_fails_with_engine_start_failed(maker, factory, engine, wired, reconcile):
    rc, clock, _ = reconcile
    _, _, _, job_id = await make_job(maker, created_ago=600)
    engine.down = True
    for _ in range(3):
        await rc.run_once()
        clock.t += 10_000
    job = await reload(maker, job_id)
    assert job.status == "failed" and job.error == "engine_start_failed" and job.completed_at is not None
    submits = engine.submit_calls
    await rc.run_once()
    assert engine.submit_calls == submits  # no longer stuck


async def test_fail_hook_never_moves_a_terminal_job(maker, factory, engine, wired):
    t, _, _, job_id = await make_job(maker, status="completed")
    await fe.fail(rh.RunRef(kind="fleet", ref_id=job_id, tenant_id=t), "engine_run_missing")
    assert (await reload(maker, job_id)).status == "completed"


def test_bootstrap_registers_the_fleet_hooks():
    rh.reset()
    import importlib
    import sys
    sys.modules.pop("app.services.fleet_engine", None)
    rh.bootstrap()
    assert "fleet" in rh.kinds()
    for name in ("start_missing", "find_stuck", "fail"):
        assert rh.hook("fleet", name) is not None
    importlib.import_module("app.services.fleet_engine")
