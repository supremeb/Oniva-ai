"""Plan 003 U19: scheduled workflow executions start on the engine with run-level retry (SQLite tier, fake engine)."""
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.services.engine_client import EngineError
from app.models.scheduled_workflow import ScheduledWorkflow, ScheduledWorkflowExecution
from app.models.workflow import WorkflowExecution
from app.services import run_handlers as rh
from app.services import run_reconciler as rr
from app.services import scheduled_run_hooks as srh
from app.services import workflow_engine_runs as wer
from app.services import workflow_run_sync as wrs
from app.services.workflow_scheduler import WorkflowSchedulerService
from tests.internal.test_invoke import factory, maker  # noqa: F401  (fixtures)
from tests.test_workflow_engine_start import w  # noqa: F401  (fixture: tenant, user, workflow, fake engine)


def rebootstrap():
    import importlib
    mods = [importlib.import_module(name) for name in rh.BOOTSTRAP_MODULES]
    rh.reset()
    for mod in mods:
        importlib.reload(mod)


async def add_schedule(w, **kw):
    sid = uuid.uuid4()
    async with w.maker() as db:
        db.add(ScheduledWorkflow(id=sid, workflow_id=w.wf, user_id=w.user, tenant_id=w.tenant, name="s",
                                 cron_expression="0 9 * * *", **kw))
        await db.commit()
    return sid


async def add_exec(w, sid, status="running", age_s=3600):
    eid = uuid.uuid4()
    async with w.maker() as db:
        db.add(ScheduledWorkflowExecution(
            id=eid, schedule_id=sid, tenant_id=w.tenant, scheduled_for=datetime.now(timezone.utc), status=status,
            started_at=datetime.now(timezone.utc) - timedelta(seconds=age_s),
            created_at=datetime.now(timezone.utc) - timedelta(seconds=age_s)))
        await db.commit()
    return eid


async def start(w, eid):
    async with w.maker() as db:
        return await srh.start_scheduled(db, eid, factory=w.factory, engine=w.engine)


async def sched(w, eid):
    async with w.maker() as db:
        return await db.get(ScheduledWorkflowExecution, eid)


async def wf_execs(w):
    async with w.maker() as db:
        return (await db.execute(select(WorkflowExecution))).scalars().all()


async def test_start_runs_one_engine_run_with_retry_and_deadline(w):
    sid = await add_schedule(w, retry_on_failure=True, max_retries=2, retry_delay_seconds=60, timeout_seconds=600)
    eid = await add_exec(w, sid, "pending")
    res = await start(w, eid)
    assert res.status == "started"
    [start_call] = w.engine.starts
    inp = start_call["body"]["input"]
    assert inp["retry"] == {"max": 2, "delayMs": 60000}
    assert inp["deadlineMs"] > int(datetime.now(timezone.utc).timestamp() * 1000)
    [ex] = await wf_execs(w)
    row = await sched(w, eid)
    assert row.status == "running" and row.workflow_execution_id == ex.id and ex.trigger == "scheduled"
    assert row.result_summary["engine_conversation_id"] == str(ex.engine_conversation_id)
    # a repeated start is the same run (same key, no second execution)
    again = await start(w, eid)
    assert again.status in ("started", "already_started")
    assert len(await wf_execs(w)) == 1
    assert {s["key"] for s in w.engine.starts} == {f"workflow:{ex.id}"}


async def test_retry_on_failure_false_runs_once(w):
    sid = await add_schedule(w, retry_on_failure=False, max_retries=2)
    eid = await add_exec(w, sid, "pending")
    await start(w, eid)
    assert w.engine.starts[0]["body"]["input"]["retry"]["max"] == 0


async def finalize_run(w, status, attempts, error=None):
    [ex] = await wf_execs(w)
    async with w.maker() as db:
        row = (await db.execute(select(WorkflowExecution).where(WorkflowExecution.id == ex.id))).scalar_one()
        await wrs.apply_final(db, row, status, summaries={"t": "success" if status == "completed" else "failed"},
                              error=error, attempts=attempts)
        await db.commit()


async def test_ae5_fail_once_then_complete_shows_retry_count_1(w):
    sid = await add_schedule(w, retry_on_failure=True, max_retries=2, retry_delay_seconds=60)
    eid = await add_exec(w, sid)
    await start(w, eid)
    await finalize_run(w, "completed", attempts=2)
    row = await sched(w, eid)
    assert row.status == "completed" and row.retry_count == 1
    async with w.maker() as db:
        s = await db.get(ScheduledWorkflow, sid)
    assert s.last_run_status == "completed" and s.last_run_at is not None and s.last_error is None


async def test_failing_after_all_attempts_is_failed_with_retry_count(w):
    sid = await add_schedule(w, retry_on_failure=True, max_retries=2, retry_delay_seconds=60)
    eid = await add_exec(w, sid)
    await start(w, eid)
    await finalize_run(w, "failed", attempts=3, error={"message": "boom"})
    row = await sched(w, eid)
    assert row.status == "failed" and row.retry_count == 2 and "boom" in row.error and "3 attempts" in row.error
    async with w.maker() as db:
        s = await db.get(ScheduledWorkflow, sid)
    assert s.last_run_status == "failed" and "boom" in s.last_error


async def test_find_stuck_then_start_missing_starts_once_and_is_already_started_after(w):
    sid = await add_schedule(w)
    eid = await add_exec(w, sid, "running")
    async with w.maker() as db:
        refs = await srh.find_stuck(db, timedelta(seconds=60))
    assert [r.ref_id for r in refs] == [eid]
    ref = refs[0]
    res = await srh.start_missing(ref)
    assert res.status == "started" and len(await wf_execs(w)) == 1
    # now the execution exists: not stuck for this kind, and a repeat is a no-op on the engine
    async with w.maker() as db:
        assert await srh.find_stuck(db, timedelta(seconds=60)) == []
    ex_before = len(w.engine.runs)
    again = await srh.start_missing(ref)
    assert again.status in ("started", "already_started") and len(w.engine.runs) == ex_before
    assert len(await wf_execs(w)) == 1


async def test_find_stuck_skips_young_linked_and_non_engine_tenants(w):
    sid = await add_schedule(w)
    await add_exec(w, sid, "running", age_s=1)
    linked = await add_exec(w, sid, "running")
    await start(w, linked)
    async with w.maker() as db:
        assert await srh.find_stuck(db, timedelta(seconds=60)) == []


async def test_engine_down_counts_start_attempts_and_the_bound_fails_the_execution(w, monkeypatch):
    sid = await add_schedule(w)
    eid = await add_exec(w, sid, "running")
    w.engine.down = True
    r1 = await srh.start_missing(rh.RunRef(kind="scheduled", ref_id=eid, tenant_id=w.tenant))
    assert r1.status == "failed" and r1.attempts == 1
    assert (await sched(w, eid)).result_summary["start_attempts"] == 1
    # the queued execution now belongs to the workflow kind; its bounded retries end it as engine_start_failed
    rebootstrap()
    monkeypatch.setattr(wer, "tenant_factory", lambda tid: w.factory)
    monkeypatch.setattr(srh.wer, "tenant_factory", lambda tid: w.factory)
    settings = SimpleNamespace(RUN_RECONCILE_AFTER_S=0, RUN_RECONCILE_INTERVAL_S=60.0,
                               RUN_START_RETRY_MAX=2, RUN_START_RETRY_BASE_S=0.0)
    async with w.maker() as db:
        for ex in (await db.execute(select(WorkflowExecution))).scalars():
            ex.started_at = datetime.now(timezone.utc) - timedelta(hours=1)
        await db.commit()
    async def no_conversation(*a):
        raise EngineError(404, "not_found")  # the engine never saw the conversation

    monkeypatch.setattr(rr, "read_snapshot", no_conversation)
    rec = rr.RunReconciler(w.factory, settings, engine_factory=lambda: w.engine)
    for _ in range(4):
        await rec.run_once()
    row = await sched(w, eid)
    assert row.status == "failed" and "engine_start_failed" in row.error
    [ex] = await wf_execs(w)
    assert ex.status == "failed"


async def test_fail_hook_ends_an_unstarted_execution(w):
    sid = await add_schedule(w)
    eid = await add_exec(w, sid, "running")
    await srh.fail(rh.RunRef(kind="scheduled", ref_id=eid, tenant_id=w.tenant), "engine_start_failed")
    row = await sched(w, eid)
    assert row.status == "failed" and row.error == "engine_start_failed"


async def test_missing_workflow_fails_the_execution_without_a_run(w):
    sid = await add_schedule(w)
    async with w.maker() as db:
        wf = await db.get(__import__("app.models.workflow", fromlist=["Workflow"]).Workflow, w.wf)
        await db.delete(wf)
        await db.commit()
    eid = await add_exec(w, sid, "pending")
    await start(w, eid)
    row = await sched(w, eid)
    assert row.status == "failed" and "Workflow" in row.error and w.engine.starts == []


async def test_trigger_now_starts_a_run_and_returns_the_execution(w, monkeypatch):
    sid = await add_schedule(w)
    monkeypatch.setattr(srh.wer, "get_engine_client", lambda: w.engine)
    async with w.maker() as db:
        out = await WorkflowSchedulerService(db).trigger_now(sid, w.user, w.tenant)
        assert out is not None and out.status == "running" and out.workflow_execution_id is not None
    assert len(w.engine.starts) == 1 and len(await wf_execs(w)) == 1


def test_scheduled_hooks_are_bootstrapped():
    rebootstrap()
    assert "scheduled" in rh.kinds()
    for hook in ("start_missing", "find_stuck", "fail"):
        assert rh.hook("scheduled", hook) is not None
    assert rh.missing_on_finished() == []


async def test_dispatch_immediate_on_an_engine_tenant_starts_without_a_task_or_semaphore(w, monkeypatch):
    from contextlib import asynccontextmanager

    import app.core.database as database
    from app.services.workflow_scheduler import WorkflowSchedulerDaemon

    sid = await add_schedule(w)
    eid = await add_exec(w, sid, "pending")
    monkeypatch.setattr(srh.wer, "get_engine_client", lambda: w.engine)

    @asynccontextmanager
    async def tenant_db(tenant_id):
        async with w.factory() as db:
            yield db

    monkeypatch.setattr(database, "get_db_for_tenant", tenant_db)
    WorkflowSchedulerDaemon._instance = None
    d = WorkflowSchedulerDaemon()
    d._initialized = True
    d._semaphore = None  # would raise if the legacy path were taken
    d._active_tasks = set()

    async def yes(tenant_id):
        return True

    monkeypatch.setattr(d, "_uses_engine", yes)
    async with w.maker() as db:
        schedule = await db.get(ScheduledWorkflow, sid)
        execution = await db.get(ScheduledWorkflowExecution, eid)
    try:
        await d.dispatch_immediate(schedule, execution)
    finally:
        WorkflowSchedulerDaemon._instance = None
    assert len(w.engine.starts) == 1 and not d._active_tasks
    assert (await sched(w, eid)).status == "running"
