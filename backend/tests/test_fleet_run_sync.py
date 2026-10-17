"""Plan 003 U9: the fleet ``on_finished`` hook (SQLite tier, fake engine)."""
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models.background_job import BackgroundJob
from app.models.engine import EngineConversation
from app.services import fleet_engine as fe
from app.services import fleet_run_sync as frs
from app.services import run_handlers as rh
from app.services import run_reconciler as rr
from tests.internal.test_invoke import maker, factory  # noqa: F401  (fixtures)
from tests.test_fleet_cancel_engine import Engine, cancel, started  # noqa: F401
from tests.test_fleet_engine_start import make_job, reload, wired  # noqa: F401

USAGE = {"requests": 2, "total": {"requests": 2, "inputTokens": 30, "outputTokens": 12}}


@pytest.fixture(autouse=True)
def _wiring():
    rh.reset()
    fe.register_hooks()
    frs.register_hooks()
    yield
    rh.reset()


@pytest.fixture
def engine(monkeypatch):
    eng = Engine()
    monkeypatch.setattr(fe, "get_engine_client", lambda: eng)
    return eng


@pytest.fixture
def delivered(monkeypatch):
    calls = []

    async def deliver(job, text):
        calls.append((job.id, text))

    monkeypatch.setattr(frs, "deliver", deliver)
    return calls


def view_for(job_id, *, error=None, active=False):
    entries = [{"kind": "user", "payload": {"requestId": fe.request_id(job_id)}},
               {"kind": "assistant", "payload": {"message": {"blocks": [
                   {"type": "text", "text": "Searching."},
                   {"type": "tool_call", "id": "c1", "name": "web_search", "input": {"q": "x"}}]}}},
               {"kind": "tool_result", "payload": {"toolCallId": "c1", "output": "found it"}}]
    if error:
        entries.append({"kind": "run_error", "payload": {"error": error}})
    else:
        entries.append({"kind": "assistant", "payload": {"message": {"blocks": [{"type": "text", "text": "All done."}]}}})
    return {"run": {"active": active}, "inbox": {"queue": []}, "tasks": {}, "entries": entries, "usage": USAGE}


async def ref_for(maker, job_id):  # noqa: F811
    async with maker() as db:
        job = await db.get(BackgroundJob, job_id)
        row = (await db.execute(select(EngineConversation).where(
            EngineConversation.conversation_id == job.conversation_id))).scalars().one()
    return rh.RunRef(kind="fleet", ref_id=job_id, tenant_id=job.tenant_id, project_id=row.project_id,
                     session_id=row.session_id, conversation_id=job.conversation_id)


def push(job_id, status="done", reason=None, **extra):
    return {"source": "push", "status": status, "reason": reason, "usage": USAGE, **extra}


async def test_a_done_run_completes_with_result_tokens_trace_and_delivers_once(
        maker, factory, engine, wired, delivered):  # noqa: F811
    t, u, job_id = await started(maker, factory, engine, wired)
    ref = await ref_for(maker, job_id)
    snap = push(job_id, view=view_for(job_id))
    await frs.on_finished(ref, snap)
    await frs.on_finished(ref, snap)
    job = await reload(maker, job_id)
    assert job.status == "completed" and job.progress == 100 and job.completed_at is not None
    assert job.result["output"] == "All done."
    assert job.total_tokens == 42
    kinds = [s["node_type"] for s in job.execution_trace]
    assert kinds == ["text_response", "tool_call", "text_response", "usage"]
    assert job.execution_trace[0]["content_preview"] == "Searching."
    assert job.execution_trace[1]["tool_name"] == "web_search"
    assert job.execution_trace[1]["tool_result_preview"] == "found it"
    assert delivered == [(job_id, "All done.")]


async def test_max_rounds_run_error_fails_with_a_readable_message(maker, factory, engine, wired, delivered):  # noqa: F811
    _, _, job_id = await started(maker, factory, engine, wired)
    ref = await ref_for(maker, job_id)
    view = view_for(job_id, error={"kind": "max_rounds", "message": "max_rounds"})
    await frs.on_finished(ref, push(job_id, "unanswered", "max_rounds", view=view))
    job = await reload(maker, job_id)
    assert job.status == "failed" and job.error == frs.MAX_ROUNDS_MESSAGE
    assert delivered[0][1].startswith("Agent execution failed")


async def test_an_aborted_run_with_no_cancel_marks_cancelled_without_delivery(
        maker, factory, engine, wired, delivered):  # noqa: F811
    _, _, job_id = await started(maker, factory, engine, wired)
    ref = await ref_for(maker, job_id)
    await frs.on_finished(ref, push(job_id, "unanswered", "aborted", view=view_for(job_id)))
    assert (await reload(maker, job_id)).status == "cancelled" and delivered == []


async def test_a_finished_event_after_cancel_leaves_the_job_cancelled(
        maker, factory, engine, wired, delivered):  # noqa: F811
    t, u, job_id = await started(maker, factory, engine, wired)
    ref = await ref_for(maker, job_id)
    await cancel(maker, u, t, job_id, "stop")
    before = await reload(maker, job_id)
    await frs.on_finished(ref, push(job_id, view=view_for(job_id)))
    job = await reload(maker, job_id)
    assert job.status == "cancelled" and job.error == before.error == "Cancelled: stop"
    assert job.result is None and delivered == []
    assert len(engine.aborts) == 1  # the run was idle: nothing to re-abort


async def test_other_terminal_rows_are_never_moved(maker, factory, engine, wired, delivered):  # noqa: F811
    _, _, job_id = await started(maker, factory, engine, wired)
    ref = await ref_for(maker, job_id)
    async with maker() as db:
        (await db.get(BackgroundJob, job_id)).status = "failed"
        await db.commit()
    await frs.on_finished(ref, push(job_id, view=view_for(job_id)))
    assert (await reload(maker, job_id)).status == "failed" and delivered == []


async def test_a_cancelled_job_whose_run_is_still_active_gets_the_abort_again(
        maker, factory, engine, wired, delivered):  # noqa: F811
    t, u, job_id = await started(maker, factory, engine, wired)
    ref = await ref_for(maker, job_id)
    engine.abort_down = True
    await cancel(maker, u, t, job_id)
    assert (await reload(maker, job_id)).input_data["abort_pending"] is True
    active = {"source": "reconcile", "view": view_for(job_id, active=True)}
    await frs.on_finished(ref, active)  # engine still down: mark stays
    assert (await reload(maker, job_id)).input_data["abort_pending"] is True
    engine.abort_down = False
    await frs.on_finished(ref, active)
    job = await reload(maker, job_id)
    assert len(engine.aborts) == 1 and "abort_pending" not in job.input_data and job.status == "cancelled"


async def test_a_pending_job_without_a_conversation_is_started(maker, factory, engine, wired, delivered):  # noqa: F811
    t, _, _, job_id = await make_job(maker)
    ref = rh.RunRef(kind="fleet", ref_id=job_id, tenant_id=t)
    await frs.on_finished(ref, push(job_id))
    job = await reload(maker, job_id)
    assert job.status == "running" and job.conversation_id is not None and engine.submit_calls == 1


async def test_a_still_active_run_is_left_running(maker, factory, engine, wired, delivered):  # noqa: F811
    _, _, job_id = await started(maker, factory, engine, wired)
    ref = await ref_for(maker, job_id)
    await frs.on_finished(ref, push(job_id, view=view_for(job_id, active=True)))
    assert (await reload(maker, job_id)).status == "running"


async def test_the_push_reads_the_view_when_the_snapshot_has_none(
        maker, factory, engine, wired, delivered, monkeypatch):  # noqa: F811
    _, _, job_id = await started(maker, factory, engine, wired)
    ref = await ref_for(maker, job_id)

    async def read_view(r):
        return view_for(job_id)

    monkeypatch.setattr(frs, "read_view", read_view)
    await frs.on_finished(ref, push(job_id))
    assert (await reload(maker, job_id)).status == "completed"


async def test_registry_has_fleet_on_finished_and_the_reconciler_settles_a_stuck_row_once(
        maker, factory, engine, wired, delivered, monkeypatch):  # noqa: F811
    assert "app.services.fleet_run_sync" in rh.BOOTSTRAP_MODULES
    assert "fleet" in rh.bootstrap()
    # workflow registers start_missing/find_stuck/fail (U15); its on_finished is U16's.
    assert [k for k in rh.missing_on_finished() if k != "workflow"] == []
    assert rh.hook("fleet", "on_finished") is frs.on_finished
    _, _, job_id = await started(maker, factory, engine, wired)
    async with maker() as db:  # long quiet: looks stuck as a running row
        (await db.get(BackgroundJob, job_id)).created_at -= timedelta(hours=2)
        await db.commit()

    async def stuck(session, older_than):
        return [await ref_for(maker, job_id)]

    async def read_snapshot(*a):
        return view_for(job_id)

    monkeypatch.setattr(rr, "read_snapshot", read_snapshot)
    monkeypatch.setitem(rh._registry["fleet"], "find_stuck", stuck)
    settings = SimpleNamespace(RUN_RECONCILE_AFTER_S=1, RUN_RECONCILE_INTERVAL_S=60.0,
                               RUN_START_RETRY_MAX=5, RUN_START_RETRY_BASE_S=30.0)
    rec = rr.RunReconciler(factory, settings, engine_factory=lambda: engine)
    await rec.run_once()
    await rec.run_once()
    assert (await reload(maker, job_id)).status == "completed"
    assert len(delivered) == 1
