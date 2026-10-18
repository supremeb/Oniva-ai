"""Plan 003 U16: the workflow finalize route, the read model and the ``workflow`` ``on_finished`` hook."""
import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.llm_usage import LLMUsageRecord
from app.models.scheduled_workflow import ScheduledWorkflow, ScheduledWorkflowExecution
from app.models.workflow import WorkflowExecution
from app.services import run_handlers as rh
from app.services import run_reconciler as rr
from app.services import workflow_engine_runs as wer
from app.services import workflow_run_sync as wrs
from tests.internal import test_workflow_nodes as tw
from tests.internal.test_invoke import factory, maker  # noqa: F401  (fixtures)
from tests.internal.test_workflow_nodes import counts, wf  # noqa: F401  (fixtures)

FIXTURES = tw.FIXTURES


@pytest.fixture(autouse=True)
def tenants(monkeypatch, factory):  # noqa: F811
    monkeypatch.setattr(wer, "tenant_factory", lambda tid: factory)


async def bind(w, **fields):
    async with w.maker() as db:
        row = await db.get(WorkflowExecution, w.run)
        row.engine_conversation_id = w.conv_a
        row.started_at = datetime.now(timezone.utc) - timedelta(seconds=5)
        for k, v in fields.items():
            setattr(row, k, v)
        await db.commit()


async def row(w) -> WorkflowExecution:
    async with w.maker() as db:
        return await db.get(WorkflowExecution, w.run)


async def finalize(w, status="completed", summaries=None, error=None, *, r=0, token=None, key=None):
    body = {**tw.scope(w), "contractVersion": 1, "status": status, "nodeSummaries": summaries or {"t": "success"},
            "attempts": 1}
    if error is not None:
        body["error"] = error
    headers = {**tw.AUTH, "Idempotency-Key": key or f"{w.run}:r{r}:finalize", "X-Engine-Session": tw.SESSION}
    if token is not None:
        headers["X-Engine-Fence"] = str(token)
    return await w.client.post(f"/internal/workflows/runs/{w.run}/finalize", json=body, headers=headers)


async def add_usage(w, input_tokens, output_tokens, conversation=None):
    async with w.maker() as db:
        db.add(LLMUsageRecord(tenant_id=w.tenant_a, provider_name="p", model="m", input_tokens=input_tokens,
                              output_tokens=output_tokens, cost_usd=Decimal("0"), latency_ms=1,
                              conversation_id=conversation or w.conv_a, request_id=str(uuid.uuid4())))
        await db.commit()


def step(node, status="success", **extra):
    return {"node_id": node, "run_attempt": 0, "attempt": 0, "status": status, "output": {}, "tokens_used": 0, **extra}


async def trace(w, *steps):
    async with w.maker() as db:
        r = await db.get(WorkflowExecution, w.run)
        r.execution_trace = list(steps)
        await db.commit()


async def test_a_branch_run_finalises_completed_with_skipped_nodes_recorded_and_not_counted(wf):
    await bind(wf)
    await trace(wf, step("a"), step("b"))
    r = await finalize(wf, "completed", {"a": "success", "b": "success", "c": "skipped"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    got = await row(wf)
    assert got.status == "completed" and got.nodes_executed == 2 and got.completed_at is not None
    assert got.results["c"]["status"] == "skipped" and got.results["a"]["status"] == "success"
    assert got.duration_ms is not None and got.duration_ms >= 4000


async def test_a_failed_node_finalises_failed_with_its_error(wf):
    await bind(wf)
    await trace(wf, step("n1"), step("n2", "failed", error="boom"))
    await finalize(wf, "failed", {"n1": "success", "n2": "failed", "n3": "skipped"},
                   {"nodeId": "n2", "message": "boom"})
    got = await row(wf)
    assert got.status == "failed" and got.error == "boom" and got.results["n2"]["error"] == "boom"
    assert got.nodes_executed == 2


async def test_total_tokens_is_the_ledger_sum_for_the_runs_conversation(wf):
    await bind(wf)
    await add_usage(wf, 100, 20)
    await add_usage(wf, 5, 5)
    await add_usage(wf, 999, 1, conversation=wf.conv_b)  # another conversation
    await finalize(wf)
    assert (await row(wf)).total_tokens == 130


async def test_without_ledger_rows_the_trace_total_stays(wf):
    await bind(wf, total_tokens=7)
    await finalize(wf)
    assert (await row(wf)).total_tokens == 7


async def test_a_repeated_finalize_changes_nothing(wf):
    await bind(wf)
    await finalize(wf, "completed")
    first = await row(wf)
    again = await finalize(wf, "failed", {"t": "failed"}, {"message": "late"}, r=1)
    assert again.status_code == 200
    second = await row(wf)
    assert (second.status, second.error, second.completed_at, second.results) == (
        first.status, first.error, first.completed_at, first.results)


async def test_a_cancelled_row_is_not_overwritten(wf):
    await bind(wf, status="cancelled", error="Execution cancelled by user")
    assert (await finalize(wf, "completed")).status_code == 200
    got = await row(wf)
    assert got.status == "cancelled" and got.error == "Execution cancelled by user"


async def test_finalize_validates_key_body_fence_and_run(wf):
    await bind(wf)
    assert (await finalize(wf, key="x")).status_code == 400
    assert (await finalize(wf, key=f"{uuid.uuid4()}:r0:finalize")).status_code == 400
    bad = await wf.client.post(
        f"/internal/workflows/runs/{wf.run}/finalize", json={**tw.scope(wf), "status": "nope"},
        headers={**tw.AUTH, "Idempotency-Key": f"{wf.run}:r0:finalize", "X-Engine-Session": tw.SESSION})
    assert bad.status_code == 422 and bad.json() == {"error": "invalid_request"}
    assert (await finalize(wf, token=9)).status_code == 200
    r = await finalize(wf, token=3)
    assert r.status_code == 409 and r.json()["error"] == "fence_rejected"
    missing = await wf.client.post(
        f"/internal/workflows/runs/{uuid.uuid4()}/finalize", json={}, headers={**tw.AUTH, "Idempotency-Key": "x"})
    assert missing.status_code == 400


async def test_finalize_requires_the_service_token(wf):
    r = await wf.client.post(f"/internal/workflows/runs/{wf.run}/finalize", json={})
    assert r.status_code == 401


async def test_the_fixture_body_finalizes(wf):
    await bind(wf)
    body = {**tw.scope(wf), **json.loads((FIXTURES / "finalize.valid.json").read_text())}
    r = await wf.client.post(
        f"/internal/workflows/runs/{wf.run}/finalize", json=body,
        headers={**tw.AUTH, "Idempotency-Key": f"{wf.run}:r0:finalize", "X-Engine-Session": tw.SESSION})
    assert r.status_code == 200
    got = await row(wf)
    assert got.status == "failed" and got.error == "boom"


async def test_trace_steps_are_not_duplicated_after_a_replayed_finish(wf):
    await tw.call(wf, "t")
    await tw.call(wf, "t")
    await finalize(wf, "completed", {"t": "success"})
    got = await row(wf)
    assert len(got.execution_trace) == 1 and got.results["t"]["status"] == "success"


async def test_the_scheduled_execution_row_is_finalised_with_the_run(wf):
    await bind(wf, trigger="scheduled")
    sid = uuid.uuid4()
    async with wf.maker() as db:
        db.add(ScheduledWorkflow(id=sid, workflow_id=wf.wf_id, user_id=wf.user_a, tenant_id=wf.tenant_a, name="s",
                                 cron_expression="* * * * *"))
        await db.commit()
        db.add(ScheduledWorkflowExecution(schedule_id=sid, workflow_execution_id=wf.run, tenant_id=wf.tenant_a,
                                          scheduled_for=datetime.now(timezone.utc), status="running"))
        await db.commit()
    await finalize(wf, "failed", {"t": "failed"}, {"message": "bad"})
    async with wf.maker() as db:
        sched = (await db.execute(select(ScheduledWorkflowExecution))).scalar_one()
    assert sched.status == "failed" and sched.error == "bad" and sched.completed_at is not None


# ---- registry hook ----------------------------------------------------------------------------


def rebootstrap():
    import importlib
    mods = [importlib.import_module(name) for name in rh.BOOTSTRAP_MODULES]  # registers on a first import
    rh.reset()
    for mod in mods:
        importlib.reload(mod)


def ref_of(w):
    return rh.RunRef(kind="workflow", ref_id=w.run, tenant_id=w.tenant_a, conversation_id=w.conv_a)


def push(status="done", reason=None):
    return {"source": "push", "status": status, "reason": reason}


async def test_registry_has_workflow_on_finished_after_bootstrap():
    rebootstrap()
    assert "workflow" in rh.bootstrap()
    assert rh.hook("workflow", "on_finished") is wrs.on_finished
    assert rh.missing_on_finished() == []


async def test_on_finished_settles_a_running_row_without_a_finalize_once(wf):
    await bind(wf)
    await trace(wf, step("a"), step("b"))
    await wrs.on_finished(ref_of(wf), push("done"))
    got = await row(wf)
    assert got.status == "completed" and got.nodes_executed == 2
    stamp = got.completed_at
    await wrs.on_finished(ref_of(wf), push("done"))
    assert (await row(wf)).completed_at == stamp


async def test_on_finished_with_a_failed_step_fails_the_row(wf):
    await bind(wf)
    await trace(wf, step("a"), step("b", "failed", error="oops"))
    await wrs.on_finished(ref_of(wf), push("done"))
    got = await row(wf)
    assert got.status == "failed" and got.error == "oops"


async def test_on_finished_maps_an_abort_to_cancelled(wf):
    await bind(wf)
    await wrs.on_finished(ref_of(wf), push("unanswered", "aborted"))
    assert (await row(wf)).status == "cancelled"


async def test_on_finished_fault_fails_a_running_row(wf):
    await bind(wf)
    await wrs.on_finished(ref_of(wf), push("unanswered", "boom"))
    got = await row(wf)
    assert got.status == "failed" and got.error == "boom"


async def test_on_finished_never_moves_a_cancelled_or_completed_row(wf):
    await bind(wf, status="cancelled", error="stop")
    await wrs.on_finished(ref_of(wf), push("done"))
    assert (await row(wf)).status == "cancelled"
    await bind(wf, status="completed")
    await wrs.on_finished(ref_of(wf), push("unanswered", "aborted"))
    assert (await row(wf)).status == "completed"


async def test_on_finished_ignores_a_queued_row_and_an_active_snapshot(wf):
    await bind(wf, status="queued")
    await wrs.on_finished(ref_of(wf), push("done"))
    assert (await row(wf)).status == "queued"
    await bind(wf, status="running")
    await wrs.on_finished(ref_of(wf), {"source": "reconcile", "runStatus": "running"})
    assert (await row(wf)).status == "running"


async def test_the_reconciler_applies_a_settled_run_to_a_stuck_row_once(wf, factory, monkeypatch):  # noqa: F811
    from types import SimpleNamespace
    await bind(wf, started_at=datetime.now(timezone.utc) - timedelta(hours=2))
    await trace(wf, step("a"))
    rebootstrap()
    rh.bootstrap()
    monkeypatch.setattr(wer, "tenant_factory", lambda tid: factory)  # the reload restored the real one

    async def read_snapshot(*a):
        return {"run": {"active": False}, "inbox": {"queue": []}, "tasks": {}}

    monkeypatch.setattr(rr, "read_snapshot", read_snapshot)
    settings = SimpleNamespace(RUN_RECONCILE_AFTER_S=1, RUN_RECONCILE_INTERVAL_S=60.0,
                               RUN_START_RETRY_MAX=5, RUN_START_RETRY_BASE_S=30.0)
    rec = rr.RunReconciler(factory, settings, engine_factory=lambda: SimpleNamespace(
        directory=SimpleNamespace(default_session_id="s1")))
    await rec.run_once()
    got = await row(wf)
    assert got.status == "completed" and got.nodes_executed == 1
    stamp = got.completed_at
    await rec.run_once()
    assert (await row(wf)).completed_at == stamp


# ---- a settled run is not judged by its trace alone ----------------------------------------------


def run_view(status, **nodes):
    return {"run": {"active": False}, "tasks": {"t-run": {"kind": "workflow_run", "status": status, "nodes": nodes}}}


def with_view(monkeypatch, view):
    async def read(*a):
        return view

    monkeypatch.setattr(wrs, "read_snapshot", read)
    from types import SimpleNamespace
    monkeypatch.setattr(wrs, "get_engine_client",
                        lambda: SimpleNamespace(directory=SimpleNamespace(default_session_id="s1")))


def bound_ref(w):
    return rh.RunRef(kind="workflow", ref_id=w.run, tenant_id=w.tenant_a, project_id=uuid.uuid4(),
                     conversation_id=w.conv_a)


def spec(*ids):
    return {wrs.wer.SPEC_KEY: {"nodes": [{"id": i} for i in ids]}}


async def test_a_faulted_run_task_fails_the_row_even_when_the_trace_is_clean(wf, monkeypatch):
    await bind(wf)
    await trace(wf, step("a"))
    with_view(monkeypatch, run_view("faulted"))
    await wrs.on_finished(bound_ref(wf), push("done"))
    got = await row(wf)
    assert got.status == "failed" and got.error


async def test_an_aborted_run_task_cancels_the_row(wf, monkeypatch):
    await bind(wf)
    await trace(wf, step("a"))
    with_view(monkeypatch, run_view("aborted"))
    await wrs.on_finished(bound_ref(wf), push("done"))
    assert (await row(wf)).status == "cancelled"


async def test_a_node_missing_from_the_trace_fails_the_row_with_a_clear_error(wf, monkeypatch):
    await bind(wf, input_data=spec("a", "b"))
    await trace(wf, step("a"))
    with_view(monkeypatch, run_view("done"))
    await wrs.on_finished(bound_ref(wf), push("done"))
    got = await row(wf)
    assert got.status == "failed" and "b" in got.error


async def test_a_skipped_node_without_a_step_is_not_missing(wf, monkeypatch):
    await bind(wf, input_data=spec("a", "b"))
    await trace(wf, step("a"))
    with_view(monkeypatch, run_view("done", a="success", b="skipped"))
    await wrs.on_finished(bound_ref(wf), push("done"))
    assert (await row(wf)).status == "completed"
