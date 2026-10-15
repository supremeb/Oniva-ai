"""Deep Work U3 / R9, R17, KTD11: guarded run state and lazy reconcile (SQLite; locks are in tests/postgres)."""
import pytest

from app.models.background_job import BackgroundJob
from app.services import deep_work_state as dws
from app.services.deep_work_state import EngineRunState
from tests.internal.test_invoke import effects, factory, gw, maker  # noqa: F401  (fixtures)


@pytest.fixture
async def run(gw):
    snap = await dws.create_run(
        gw.factory, tenant_id=gw.tenant_a, project_id=gw.project_a, user_id=gw.user_a,
        conversation_id=gw.conv_a, query="q",
    )
    return gw, snap


async def get(gw):
    return await dws.load_run(gw.factory, gw.tenant_a, gw.conv_a)


async def test_create_is_idempotent_per_root_conversation(run):
    gw, first = run
    again = await dws.create_run(
        gw.factory, tenant_id=gw.tenant_a, project_id=gw.project_a, user_id=gw.user_a,
        conversation_id=gw.conv_a, query="other",
    )
    assert again.id == first.id and again.query == "q"
    assert first.status == "running" and first.phase == "clarify" and first.round == 0 and first.is_engine


async def test_another_tenant_cannot_load_the_run(run):
    gw, _ = run
    assert await dws.load_run(gw.factory, gw.tenant_b, gw.conv_a) is None


async def test_phase_and_round_never_regress(run):
    gw, _ = run
    s = await dws.update_run(gw.factory, gw.tenant_a, gw.conv_a, phase="research", round=2)
    assert (s.phase, s.round) == ("research", 2)
    s = await dws.update_run(gw.factory, gw.tenant_a, gw.conv_a, phase="plan", round=1)  # a replayed older save
    assert (s.phase, s.round) == ("research", 2)
    s = await dws.update_run(gw.factory, gw.tenant_a, gw.conv_a, phase="research", round=3)
    assert (s.phase, s.round) == ("research", 3)


async def test_unknown_phase_or_status_is_rejected(run):
    gw, _ = run
    with pytest.raises(ValueError):
        await dws.update_run(gw.factory, gw.tenant_a, gw.conv_a, phase="bogus")
    with pytest.raises(ValueError):
        await dws.update_run(gw.factory, gw.tenant_a, gw.conv_a, status="bogus")


async def test_reflections_and_artifacts_append_once(run):
    gw, _ = run
    for _ in range(2):
        await dws.update_run(gw.factory, gw.tenant_a, gw.conv_a, reflection={"round": 1, "gaps": []}, artifact="report.md")
    s = await get(gw)
    assert s.reflections == [{"round": 1, "gaps": []}] and s.artifacts == ["report.md"]


async def test_two_workers_and_a_replay_keep_every_learning(run):
    gw, _ = run
    await dws.save_worker_learnings(gw.factory, gw.tenant_a, gw.conv_a, round=1, worker_id="w1", learnings=["a"])
    await dws.save_worker_learnings(gw.factory, gw.tenant_a, gw.conv_a, round=1, worker_id="w2", learnings=["b"])
    await dws.save_worker_learnings(gw.factory, gw.tenant_a, gw.conv_a, round=1, worker_id="w1", learnings=["a"])  # replay
    s = await get(gw)
    assert s.round == 1
    assert s.rounds == [{"round": 1, "workers": {"w1": ["a"], "w2": ["b"]}}]


async def test_terminal_status_freezes_the_run(run):
    gw, _ = run
    await dws.update_run(gw.factory, gw.tenant_a, gw.conv_a, status="cancelled")
    s = await dws.update_run(gw.factory, gw.tenant_a, gw.conv_a, status="completed", phase="done", round=5)
    assert (s.status, s.phase, s.round) == ("cancelled", "clarify", 0)
    s = await dws.save_worker_learnings(gw.factory, gw.tenant_a, gw.conv_a, round=1, worker_id="w1", learnings=["late"])
    assert s.rounds == []


def reader(state, calls):
    async def read():
        calls.append(1)
        return state
    return read


@pytest.mark.parametrize(
    "state, expected",
    [
        (EngineRunState(active=False, ended="run_error", error="boom"), "failed"),
        (EngineRunState(active=False, ended="aborted"), "cancelled"),
        (EngineRunState(active=False, ended="finished"), "failed"),  # ended before dw_finalize
        (EngineRunState(active=True), "running"),
        (EngineRunState(active=False, ended=None), "running"),
    ],
)
async def test_reconcile_maps_the_engine_state_once(run, state, expected):
    gw, _ = run
    calls = []
    s = await dws.reconcile(gw.factory, gw.tenant_a, gw.conv_a, reader(state, calls))
    assert s.status == expected
    if expected == "running":
        return
    again = await dws.reconcile(gw.factory, gw.tenant_a, gw.conv_a, reader(EngineRunState(active=False, ended="aborted"), calls))
    assert again.status == expected  # idempotent: a settled row is not rewritten
    assert len(calls) == 1  # and the engine is not asked again


async def test_reconcile_does_not_overwrite_completed(run):
    gw, _ = run
    await dws.update_run(gw.factory, gw.tenant_a, gw.conv_a, status="completed")
    s = await dws.reconcile(gw.factory, gw.tenant_a, gw.conv_a, reader(EngineRunState(active=False, ended="run_error"), []))
    assert s.status == "completed"


async def test_reconcile_settles_the_linked_job(gw):
    from app.models.background_job import JobStatus, JobType
    import uuid
    job = uuid.uuid4()
    async with gw.maker() as db:
        db.add(BackgroundJob(id=job, tenant_id=gw.tenant_a, user_id=gw.user_a, job_type=JobType.DEEP_WORK.value,
                             status=JobStatus.RUNNING.value, trigger="api"))
        await db.commit()
    await dws.create_run(gw.factory, tenant_id=gw.tenant_a, project_id=gw.project_a, user_id=gw.user_a,
                         conversation_id=gw.conv_a, query="q", job_id=job)
    await dws.reconcile(gw.factory, gw.tenant_a, gw.conv_a, reader(EngineRunState(active=False, ended="run_error", error="boom"), []))
    async with gw.maker() as db:
        j = await db.get(BackgroundJob, job)
    assert j.status == "failed" and j.error == "boom" and j.completed_at is not None
