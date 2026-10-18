"""Plan 003 U15: workflow runs start and cancel on the engine (SQLite tier, fake engine, real routes)."""
import inspect
import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy import func, select

from app.api.v1 import compliance as compliance_api
from app.api.v1 import workflows as wf_api
from app.auth.dependencies import get_current_tenant_id, get_current_user, get_current_user_id
from app.auth.providers.base import AuthUser
from app.core.database import get_db
from app.models.engine import EngineConversation
from app.models.tenant import Tenant
from app.models.user import User
from app.models.workflow import Workflow, WorkflowExecution
from app.services import run_handlers as rh
from app.services import run_reconciler as rr
from app.services import workflow_engine_runs as wer
from app.services import workflow_stream_adapter as wsa
from app.services.automation_scope import run_conversation_id
from app.services.engine_client import EngineError
from tests.contracts.streams import ScriptedWatch, run_view, snapshot_frames
from tests.internal.test_invoke import factory, maker  # noqa: F401  (fixtures)

SPEC = {
    "nodes": [
        {"id": "s", "type": "start", "data": {}, "position": {"x": 0.0, "y": 0.0}},
        {"id": "t", "type": "template", "data": {"template": "hi {{name}}"}, "position": {"x": 1.0, "y": 0.0}},
        {"id": "o", "type": "output", "data": {}, "position": {"x": 2.0, "y": 0.0}},
    ],
    "edges": [{"id": "e1", "source": "s", "target": "t"}, {"id": "e2", "source": "t", "target": "o"}],
}
DONE = {"type": "done", "value": {"status": "completed", "nodes": {"s": "success", "t": "success", "o": "success"}}}


class FakeEngine:
    """Engine semantics that matter here: ``POST /v1/runs`` answers the existing run for a repeated id."""

    def __init__(self, maker):
        self.maker = maker
        self.directory = SimpleNamespace(default_session_id="s1")
        self.starts = []
        self.runs = {}
        self.aborts = []
        self.down = False
        self.abort_down = False
        self.task = {"status": "terminal", "outcome": DONE}
        self.on_terminal = None
        self.watches = []
        self.resumes = []

    async def start_run(self, session, tenant, project, body, key):
        self.starts.append({"body": body, "key": key})
        if self.down:
            raise EngineError(502, "engine_unreachable")
        run = self.runs.setdefault(body["id"], {"conversationId": body["id"], "taskId": f"task-{len(self.runs) + 1}"})
        return dict(run)

    async def abort(self, session, tenant, project, conversation, reason=None):
        if self.abort_down:
            raise EngineError(502, "engine_unreachable")
        self.aborts.append((conversation, reason))
        return {"aborted": True}

    async def watch_stream(self, session, tenant, project, conversation, last_event_id=None, cursor=None):
        """The scripted response, or the default successful s -> t -> o run (its trace written first)."""
        self.resumes.append(last_event_id)
        if self.down:
            raise EngineError(502, "engine_unreachable")
        if self.watches:
            return self.watches.pop(0)
        if self.on_terminal is not None:
            await self.on_terminal(conversation)
        return ScriptedWatch(snapshot_frames([
            run_view({"s": "pending", "t": "pending", "o": "pending"}, running=("s",)),
            run_view({"s": "success", "t": "pending", "o": "pending"}, running=("t",)),
            run_view({"s": "success", "t": "success", "o": "pending"}, running=("o",)),
            run_view({"s": "success", "t": "success", "o": "success"}, status="done"),
        ]))

    async def get_task(self, session, tenant, project, conversation, task_id):
        if self.task.get("status") == "terminal" and self.on_terminal is not None:
            await self.on_terminal(conversation)
        return dict(self.task, id=task_id, kind="workflow_run", phase="x")


async def write_trace(maker, execution_id, steps):
    async with maker() as db:
        row = await db.get(WorkflowExecution, execution_id)
        row.execution_trace = [{"run_attempt": 0, "attempt": 0, "tokens_used": 0, **s} for s in steps]
        await db.commit()


class World:
    pass


@pytest.fixture
async def w(maker, factory, monkeypatch):  # noqa: F811
    world = World()
    world.maker, world.factory = maker, factory
    world.engine = FakeEngine(maker)
    monkeypatch.setattr(wer, "get_engine_client", lambda: world.engine)
    monkeypatch.setattr(wer, "tenant_factory", lambda tenant_id: factory)
    monkeypatch.setattr(wsa, "get_engine_client", lambda: world.engine)
    monkeypatch.setattr(wsa, "RECONNECT_DELAY_S", 0.0)
    monkeypatch.setattr(wer, "POLL_INTERVAL_S", 0.01)
    monkeypatch.setattr(wer, "SYNC_WAIT_S", 0.3)
    monkeypatch.setattr(wer, "engine_workflows_enabled",
                        lambda tenant, settings=None: bool(((tenant.settings or {}).get("features") or {}).get("ENABLE_ENGINE_WORKFLOWS")))
    world.tenant, world.user, world.other_user = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    world.wf = uuid.uuid4()
    async with maker() as db:
        db.add(Tenant(id=world.tenant, name="t", slug=f"t-{world.tenant.hex[:6]}", is_active=True,
                      settings={"features": {"ENABLE_ENGINE_WORKFLOWS": True}}))
        await db.commit()
        for u in (world.user, world.other_user):
            db.add(User(id=u, tenant_id=world.tenant, email=f"{u.hex[:6]}@x.io", name="u"))
        await db.commit()
        db.add(Workflow(id=world.wf, user_id=world.user, tenant_id=world.tenant, name="W", spec=SPEC))
        await db.commit()

    async def on_terminal(conversation):
        async with maker() as db:
            row = (await db.execute(select(WorkflowExecution).where(
                WorkflowExecution.engine_conversation_id == conversation))).scalar_one()
            if not row.execution_trace:
                row.execution_trace = [
                    {"node_id": "s", "status": "success", "output": {"query": "q"}, "run_attempt": 0, "attempt": 0,
                     "tokens_used": 0},
                    {"node_id": "t", "status": "success", "output": {"output": "hi"}, "run_attempt": 0, "attempt": 0,
                     "tokens_used": 3},
                    {"node_id": "o", "status": "success", "output": {"content": "hi"}, "run_attempt": 0, "attempt": 0,
                     "tokens_used": 0},
                ]
                await db.commit()

    world.engine.on_terminal = on_terminal
    yield world


def make_app(w, *, user=None, quota_error=None):
    """The real workflows and compliance routers with auth and the session replaced."""
    app = FastAPI()
    app.include_router(wf_api.router, prefix="/api/v1/workflows")
    app.include_router(compliance_api.router, prefix="/api/v1/compliance")
    acting = user or w.user

    async def db():
        async with w.maker() as session:
            yield session

    app.dependency_overrides[get_db] = db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=str(acting), role="member", email="u@x.io", tenant_id=str(w.tenant))
    app.dependency_overrides[get_current_user_id] = lambda: acting
    app.dependency_overrides[get_current_tenant_id] = lambda: w.tenant
    # Every handler declares the same quota dependency kind; each has its own callable.
    for handler in (wf_api.execute_workflow, wf_api.run_saved_workflow):
        quota_dep = inspect.signature(handler).parameters["_quota"].default.dependency

        async def passes():
            return None

        async def blocked():
            raise HTTPException(status_code=429, detail=quota_error)

        app.dependency_overrides[quota_dep] = blocked if quota_error is not None else passes
    return app


def client_for(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


EXECUTE = {"nodes": SPEC["nodes"], "edges": SPEC["edges"], "inputs": {"name": "Ada"}}


async def executions(w):
    async with w.maker() as db:
        return (await db.execute(select(WorkflowExecution))).scalars().all()


# ---- /execute ---------------------------------------------------------------------------------------


async def test_execute_creates_one_execution_and_one_engine_run_and_returns_the_result(w):
    async with client_for(make_app(w)) as c:
        r = await c.post("/api/v1/workflows/execute", json={**EXECUTE, "workflow_id": str(w.wf)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "completed" and body["executed_nodes"] == 3 and body["total_nodes"] == 3
    assert body["results"]["t"]["output"] == {"output": "hi"} and body["execution_id"]
    [row] = await executions(w)
    assert str(row.id) == body["execution_id"] and row.trigger == "manual" and row.status == "running"
    assert row.engine_conversation_id == run_conversation_id("workflow", row.id)
    [start] = w.engine.starts
    assert start["key"] == f"workflow:{row.id}" and start["body"]["id"] == str(row.engine_conversation_id)
    assert start["body"]["kind"] == "workflow_run"
    assert start["body"]["agent"]["metadata"]["notify"] == "backend"
    run_input = start["body"]["input"]
    assert run_input["runId"] == str(row.id) and [n["id"] for n in run_input["graph"]["nodes"]] == ["s", "t", "o"]
    assert row.input_data["name"] == "Ada" and row.input_data["__spec"]["nodes"] == SPEC["nodes"]


async def test_execute_replays_with_the_same_idempotency_key_without_a_second_execution(w):
    async with client_for(make_app(w)) as c:
        first = await c.post("/api/v1/workflows/execute", json={**EXECUTE, "workflow_id": str(w.wf)},
                             headers={"Idempotency-Key": "k-1"})
        second = await c.post("/api/v1/workflows/execute", json={**EXECUTE, "workflow_id": str(w.wf)},
                              headers={"Idempotency-Key": "k-1"})
        third = await c.post("/api/v1/workflows/execute", json={**EXECUTE, "workflow_id": str(w.wf)},
                             headers={"Idempotency-Key": "k-2"})
    assert first.json()["execution_id"] == second.json()["execution_id"] != third.json()["execution_id"]
    assert len(await executions(w)) == 2
    first_key = w.engine.starts[0]["key"]
    assert [s["key"] for s in w.engine.starts[:2]] == [first_key, first_key]  # the engine dedupes on it
    assert len(w.engine.runs) == 2 and second.status_code == 200


async def test_execute_returns_202_with_the_current_status_while_the_run_is_going(w):
    w.engine.task = {"status": "running"}
    async with client_for(make_app(w)) as c:
        r = await c.post("/api/v1/workflows/execute", json={**EXECUTE, "workflow_id": str(w.wf)})
    assert r.status_code == 202
    [row] = await executions(w)
    assert r.json() == {"execution_id": str(row.id), "status": "running"}


async def test_execute_without_a_saved_workflow_runs_the_posted_spec(w):
    async with client_for(make_app(w)) as c:
        r = await c.post("/api/v1/workflows/execute", json=EXECUTE)
    assert r.status_code == 200
    [row] = await executions(w)
    assert row.workflow_id is None and row.input_data["__spec"]["nodes"] == SPEC["nodes"]


async def test_the_run_executes_the_spec_it_started_with_even_if_the_workflow_is_edited(w):
    from app.internal_api import workflow_nodes as wn

    async with client_for(make_app(w)) as c:
        await c.post("/api/v1/workflows/execute", json={**EXECUTE, "workflow_id": str(w.wf)})
    [row] = await executions(w)
    async with w.maker() as db:
        (await db.get(Workflow, w.wf)).spec = {"nodes": [{"id": "z", "type": "start", "data": {}, "position": {}}], "edges": []}
        await db.commit()
    view = await wn.load_run(lambda tid: w.factory(), w.tenant, row.id, "t")
    assert view.node["type"] == "template" and "__spec" not in view.variables


async def test_an_invalid_workflow_is_a_400_and_creates_nothing(w):
    cyc = {"nodes": SPEC["nodes"][:2], "edges": [{"id": "a", "source": "s", "target": "t"},
                                                  {"id": "b", "source": "t", "target": "s"}]}
    async with client_for(make_app(w)) as c:
        r = await c.post("/api/v1/workflows/execute", json=cyc)
    assert r.status_code == 400 and "circular" in r.text
    assert await executions(w) == [] and w.engine.starts == []


async def test_flag_off_uses_the_legacy_path_and_never_touches_the_engine(w, monkeypatch):
    async with w.maker() as db:
        (await db.get(Tenant, w.tenant)).settings = {}
        await db.commit()
    calls = []

    class Legacy:
        def __init__(self, **kw):
            calls.append(kw)

        async def execute_workflow(self, **kw):
            return {"status": "completed", "results": {}, "executed_nodes": 0, "total_nodes": 3}

    monkeypatch.setattr(wf_api, "WorkflowOrchestrator", Legacy)
    monkeypatch.setattr(wf_api, "settings", SimpleNamespace(USE_DURABLE_WORKFLOWS=False))
    async with client_for(make_app(w)) as c:
        r = await c.post("/api/v1/workflows/execute", json={**EXECUTE, "workflow_id": str(w.wf)})
    assert r.status_code == 200 and r.json()["execution_id"] is None
    assert len(calls) == 1 and w.engine.starts == [] and await executions(w) == []


async def test_a_user_without_execute_permission_is_refused_before_any_engine_call(w):
    async with client_for(make_app(w, user=w.other_user)) as c:
        r = await c.post("/api/v1/workflows/execute", json={**EXECUTE, "workflow_id": str(w.wf)})
        r2 = await c.post(f"/api/v1/workflows/{w.wf}/run", json={"template_id": "x", "inputs": {}})
    assert r.status_code == 403 and r2.status_code == 403
    assert w.engine.starts == [] and await executions(w) == []


async def test_quota_exceeded_is_refused_before_any_engine_call(w):
    async with client_for(make_app(w, quota_error="quota exceeded")) as c:
        r = await c.post("/api/v1/workflows/execute", json={**EXECUTE, "workflow_id": str(w.wf)})
    assert r.status_code == 429
    assert w.engine.starts == [] and await executions(w) == []


async def test_an_engine_outage_on_start_is_a_502_and_the_execution_is_failed_not_queued(w):
    w.engine.down = True
    async with client_for(make_app(w)) as c:
        r = await c.post("/api/v1/workflows/execute", json={**EXECUTE, "workflow_id": str(w.wf)})
    assert r.status_code == 502
    [row] = await executions(w)
    assert r.json()["detail"] == {"error": "engine_unreachable", "execution_id": str(row.id)}
    assert row.status == "failed" and row.error == "engine_unreachable" and row.completed_at is not None


# ---- /{id}/run --------------------------------------------------------------------------------------


async def test_run_saved_workflow_starts_an_engine_run_of_the_resolved_version(w):
    from app.models.workflow import WorkflowVersion

    pinned = {"nodes": SPEC["nodes"][:2], "edges": SPEC["edges"][:1]}
    ver = uuid.uuid4()
    async with w.maker() as db:
        db.add(WorkflowVersion(id=ver, workflow_id=w.wf, tenant_id=w.tenant, version_number=1, spec=pinned,
                               is_active=True))
        await db.commit()
    async with client_for(make_app(w)) as c:
        r = await c.post(f"/api/v1/workflows/{w.wf}/run", json={"inputs": {"query": "hello"}},
                         headers={"Idempotency-Key": "run-1"})
    assert r.status_code == 200, r.text
    [row] = await executions(w)
    assert row.trigger == "api" and row.workflow_id == w.wf and row.input_data["workflow_version_id"] == str(ver)
    assert row.input_data["sys.query"] == "hello"
    assert [n["id"] for n in w.engine.starts[0]["body"]["input"]["graph"]["nodes"]] == ["s", "t"]
    assert "Workflow 'W' executed" in r.json()["message"]


# ---- cancel -----------------------------------------------------------------------------------------


async def running_execution(w, user=None):
    async with client_for(make_app(w)) as c:
        w.engine.task = {"status": "running"}
        r = await c.post("/api/v1/workflows/execute", json={**EXECUTE, "workflow_id": str(w.wf)})
    assert r.status_code == 202
    return uuid.UUID(r.json()["execution_id"])


async def test_cancel_aborts_the_engine_run_once_and_a_later_settle_does_not_change_it(w):
    execution_id = await running_execution(w)
    async with client_for(make_app(w)) as c:
        r = await c.post(f"/api/v1/workflows/{execution_id}/cancel")
        again = await c.post(f"/api/v1/workflows/{execution_id}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert again.status_code == 422  # already terminal: nothing is aborted twice
    assert w.engine.aborts == [(run_conversation_id("workflow", execution_id), "Execution cancelled by user")]
    async with w.maker() as db:
        row = await db.get(WorkflowExecution, execution_id)
        assert row.status == "cancelled" and row.error == "Execution cancelled by user" and row.completed_at
        assert "abort_pending" not in row.input_data
        # The engine's finalize arrives after the cancel and finds a terminal row.
        assert wer.settle(row, "completed") is False and row.status == "cancelled"


async def test_cancel_with_the_engine_down_still_cancels_and_leaves_the_abort_to_the_reconciler(w):
    execution_id = await running_execution(w)
    w.engine.abort_down = True
    async with client_for(make_app(w)) as c:
        r = await c.post(f"/api/v1/workflows/{execution_id}/cancel")
    assert r.status_code == 200
    async with w.maker() as db:
        row = await db.get(WorkflowExecution, execution_id)
        assert row.status == "cancelled" and row.input_data["abort_pending"] is True
        row.completed_at = datetime.now(timezone.utc) - timedelta(hours=1)
        await db.commit()
        [ref] = await wer.find_stuck(db, timedelta(minutes=2))
    assert ref.ref_id == execution_id and ref.extra == {"abort_pending": True}
    w.engine.abort_down = False
    monkey_result = await wer.start_missing(ref)
    assert monkey_result.status == "already_started"
    assert len(w.engine.aborts) == 1
    async with w.maker() as db:
        assert "abort_pending" not in (await db.get(WorkflowExecution, execution_id)).input_data


async def test_cancelling_a_legacy_execution_does_not_call_the_engine(w):
    row_id = uuid.uuid4()
    async with w.maker() as db:
        db.add(WorkflowExecution(id=row_id, workflow_id=w.wf, user_id=w.user, tenant_id=w.tenant, status="running",
                                 input_data={}, execution_trace=[]))
        await db.commit()
    async with client_for(make_app(w)) as c:
        r = await c.post(f"/api/v1/workflows/{row_id}/cancel")
    assert r.status_code == 200 and w.engine.aborts == []
    async with w.maker() as db:
        assert (await db.get(WorkflowExecution, row_id)).status == "cancelled"


# ---- registry hooks ----------------------------------------------------------------------------------


async def test_the_workflow_kind_registers_start_missing_find_stuck_and_fail_and_bootstraps():
    rh.reset()
    import importlib

    importlib.reload(wer)
    assert "app.services.workflow_engine_runs" in rh.BOOTSTRAP_MODULES
    assert rh.hook("workflow", "start_missing") is wer.start_missing
    assert rh.hook("workflow", "find_stuck") is wer.find_stuck and rh.hook("workflow", "fail") is wer.fail
    assert "workflow" in rh.bootstrap()
    rh.reset()


async def queued_row(w, *, age_hours=2):
    execution_id = uuid.uuid4()
    async with w.maker() as db:
        db.add(WorkflowExecution(
            id=execution_id, workflow_id=w.wf, user_id=w.user, tenant_id=w.tenant, status="queued",
            engine_conversation_id=run_conversation_id("workflow", execution_id), trigger="manual",
            input_data={"__spec": SPEC}, execution_trace=[], total_nodes=3,
            started_at=datetime.now(timezone.utc) - timedelta(hours=age_hours)))
        await db.commit()
    return execution_id


async def test_start_missing_starts_a_queued_row_once_and_a_second_pass_is_already_started(w):
    execution_id = await queued_row(w)
    async with w.maker() as db:
        [ref] = await wer.find_stuck(db, timedelta(minutes=2))
    assert ref.ref_id == execution_id and ref.kind == "workflow"
    first = await wer.start_missing(ref)
    second = await wer.start_missing(ref)
    assert first.status == "started" and first.task_id == "task-1"
    # The repeat re-issues the same idempotent start (the engine answers the existing run).
    assert second.status == "started" and second.task_id == "task-1"
    assert len(w.engine.runs) == 1 and {s["key"] for s in w.engine.starts} == {f"workflow:{execution_id}"}
    async with w.maker() as db:
        row = await db.get(WorkflowExecution, execution_id)
        assert row.status == "running"
        assert await wer.find_stuck(db, timedelta(hours=3)) == []  # young rows are left alone


async def test_start_missing_failure_is_bounded_by_the_reconciler_and_then_fails_the_row(w, monkeypatch):
    execution_id = await queued_row(w)
    w.engine.down = True
    rh.reset()
    rh.register("workflow", start_missing=wer.start_missing, find_stuck=wer.find_stuck, fail=wer.fail)
    settings = SimpleNamespace(RUN_RECONCILE_INTERVAL_S=60, RUN_RECONCILE_AFTER_S=60, RUN_START_RETRY_MAX=2,
                               RUN_START_RETRY_BASE_S=0)

    async def view(*a, **k):
        raise EngineError(404, "not_found")

    monkeypatch.setattr(rr, "read_snapshot", view)
    reconciler = rr.RunReconciler(w.factory, settings, engine_factory=lambda: w.engine)
    for _ in range(3):
        await reconciler.run_once()
    rh.reset()
    async with w.maker() as db:
        row = await db.get(WorkflowExecution, execution_id)
    assert row.status == "failed" and row.error == "engine_start_failed"


# ---- compliance ---------------------------------------------------------------------------------------


async def test_compliance_template_runs_on_the_engine_with_the_same_response_shape(w):
    from app.templates.compliance import get_compliance_templates

    template_id = get_compliance_templates()[0]["id"]
    async with client_for(make_app(w)) as c:
        r = await c.post(f"/api/v1/compliance/templates/{template_id}/execute", json={"template_id": "x", "inputs": {"name": "Ada"}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"execution_id", "template_id", "template_name", "status", "results", "executed_nodes",
                         "total_nodes", "timestamp"}
    assert body["template_id"] == template_id and body["status"] == "completed"
    [row] = await executions(w)
    assert row.trigger == "compliance" and row.workflow_id is None and body["execution_id"] == str(row.id)
    assert body["results"]["t"]["output"] == {"output": "hi"}
    assert w.engine.starts[0]["body"]["kind"] == "workflow_run"


async def test_compliance_stream_emits_orchestrator_shaped_events(w):
    from app.templates.compliance import get_compliance_templates

    template_id = get_compliance_templates()[0]["id"]
    async with client_for(make_app(w)) as c:
        r = await c.post(f"/api/v1/compliance/templates/{template_id}/execute-stream", json={"template_id": "x", "inputs": {}})
    assert r.status_code == 200
    events = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]
    types = [e["type"] for e in events]
    assert types[0] == "progress" and types[-1] == "complete"
    assert [e["node_id"] for e in events if e["type"] == "node_complete"] == ["s", "t", "o"]
    assert events[-1]["status"] == "completed" and events[-1]["executed_nodes"] == 3


async def test_compliance_engine_outage_is_a_502(w):
    from app.templates.compliance import get_compliance_templates

    template_id = get_compliance_templates()[0]["id"]
    w.engine.down = True
    async with client_for(make_app(w)) as c:
        r = await c.post(f"/api/v1/compliance/templates/{template_id}/execute", json={"template_id": "x", "inputs": {}})
        s = await c.post(f"/api/v1/compliance/templates/{template_id}/execute-stream", json={"template_id": "x", "inputs": {}})
    assert r.status_code == 502 and s.status_code == 502


# ---- the shared event stream (agent chat, compliance) ----------------------------------------------------


def test_outcome_status_maps_the_run_task_outcome():
    assert wer.outcome_status(DONE) == "completed"
    assert wer.outcome_status({"type": "done", "value": {"status": "failed"}}) == "failed"
    assert wer.outcome_status({"type": "done", "value": {"status": "cancelled"}}) == "cancelled"
    assert wer.outcome_status({"type": "aborted", "reason": "x"}) == "cancelled"
    assert wer.outcome_status({"type": "faulted", "message": "x"}) == "failed"
    assert wer.outcome_status({"type": "done", "value": {"status": "weird"}}) == "failed"


async def test_workflow_execution_row_count_stays_one_per_started_run(w):
    async with client_for(make_app(w)) as c:
        await c.post("/api/v1/workflows/execute", json=EXECUTE, headers={"Idempotency-Key": "x"})
        await c.post("/api/v1/workflows/execute", json=EXECUTE, headers={"Idempotency-Key": "x"})
    async with w.maker() as db:
        assert (await db.execute(select(func.count()).select_from(EngineConversation))).scalar_one() == 1


# ---- review fixes: reserved keys, user-scoped keys, retry clamp, insert race ----------------------------


async def start_direct(w, user, input=None, **kw):
    async with w.maker() as db:
        wf = await db.get(Workflow, w.wf)
        return await wer.start_workflow_run(db, wf, SPEC, input, user_id=user, tenant_id=w.tenant,
                                            trigger=kw.pop("trigger", "api"), **kw)


async def test_caller_input_cannot_set_reserved_run_keys(w):
    from app.internal_api import workflow_nodes as wn

    assert wer.SPEC_KEY == wn.SPEC_KEY and wer.RUN_OPTIONS_KEY == wn.RUN_OPTIONS_KEY
    assert all(wer.is_reserved_input_key(k) for k in (wn.SPEC_KEY, wn.RUN_OPTIONS_KEY, wn.INPUT_CLASS_KEY))
    hostile = {"name": "Ada", "__run_options": {"retry": {"max": 10**9, "delayMs": 0}}, "__spec": {"nodes": []},
               "__classification": "public", "workflow_version_id": "x", "abort_pending": True, "start_attempts": 99}
    execution, _ = await start_direct(w, w.user, hostile)
    data = execution.input_data
    assert data["name"] == "Ada" and data["__spec"]["nodes"] == SPEC["nodes"]
    assert "__run_options" not in data and "__classification" not in data
    assert not {"workflow_version_id", "abort_pending", "start_attempts"} & set(data)
    assert "retry" not in w.engine.starts[0]["body"]["input"]


def test_run_retries_are_clamped_in_the_run_input():
    from app.services.workflow_compiler import MAX_RUN_RETRIES, build_run_input

    compiled = {"errorMode": "stop", "nodes": [], "edges": []}
    body = build_run_input(uuid.uuid4(), compiled, retry={"max": 10**9, "delayMs": -5})
    assert body["retry"] == {"max": MAX_RUN_RETRIES, "delayMs": 0}


async def test_idempotency_key_is_scoped_to_the_user(w):
    a, _ = await start_direct(w, w.user, {"name": "A"}, request_key="k")
    b, _ = await start_direct(w, w.other_user, {"name": "B"}, request_key="k")
    again, _ = await start_direct(w, w.user, {"name": "A"}, request_key="k")
    assert a.id != b.id and again.id == a.id and b.input_data["name"] == "B"
    with pytest.raises(HTTPException) as exc:  # a forced shared id never exposes another user's run
        await start_direct(w, w.other_user, {}, request_key="k", execution_id=a.id)
    assert exc.value.status_code == 409


async def test_system_keys_are_not_user_scoped(w):
    a, _ = await start_direct(w, w.user, {}, request_key="scheduled:1", trigger="scheduled")
    assert a.id == wer.execution_id_for(w.tenant, w.wf, "scheduled:1")


async def test_concurrent_identical_starts_converge_on_one_row(w):
    import asyncio

    results = await asyncio.gather(*[start_direct(w, w.user, {}, request_key="race") for _ in range(4)])
    assert len({e.id for e, _ in results}) == 1 and len(await executions(w)) == 1


# ---- following a run past the sync window ---------------------------------------------------------------


@pytest.fixture
def hooks(monkeypatch):
    from app.services import workflow_run_sync as wrs

    h = Hooks()
    monkeypatch.setattr(wrs, "get_webhook_service", lambda: h)
    monkeypatch.setattr(wf_api, "is_url_safe", lambda url: None)
    return h


async def test_an_adhoc_202_run_is_followed_at_the_executions_route_without_leaking_bookkeeping(w, hooks):
    w.engine.task = {"status": "running"}
    async with client_for(make_app(w)) as c:
        accepted = await c.post("/api/v1/workflows/execute",
                                json={**EXECUTE, "webhook": {"url": "https://hooks.example.com/x", "secret": "s3cret"}})
        assert accepted.status_code == 202
        execution_id = accepted.json()["execution_id"]
        got = await c.get(f"/api/v1/workflows/executions/{execution_id}")
        missing = await c.get(f"/api/v1/workflows/executions/{uuid.uuid4()}")
    assert got.status_code == 200 and missing.status_code == 404
    body = got.json()
    assert body["id"] == execution_id and body["workflow_id"] is None and body["status"] == "running"
    assert body["input_data"] == {"name": "Ada"}  # no __spec / __run_options (the webhook secret)
    assert "s3cret" not in got.text


class Hooks:
    def __init__(self):
        self.calls = []

    async def send_completion_webhook(self, **kw):
        self.calls.append(kw)


async def settle_run(w, execution_id, status="completed", summaries=None):
    from app.services import workflow_run_sync as wrs

    async with w.maker() as db:
        row = await db.get(WorkflowExecution, uuid.UUID(execution_id))
        moved = await wrs.apply_final(db, row, status, summaries=summaries or {"t": "success"})
        await db.commit()
    return moved


async def test_the_completion_webhook_goes_out_once_when_a_run_past_the_window_settles(w, hooks):
    w.engine.task = {"status": "running"}
    async with client_for(make_app(w)) as c:
        r = await c.post("/api/v1/workflows/execute", json={
            **EXECUTE, "workflow_id": str(w.wf),
            "webhook": {"url": "https://hooks.example.com/x", "secret": "s3cret", "headers": {"X-A": "1"}}})
    assert r.status_code == 202
    assert hooks.calls == []  # nothing yet: the run is still going
    execution_id = r.json()["execution_id"]
    assert await settle_run(w, execution_id) is True
    assert await settle_run(w, execution_id, "failed") is False  # the row never moves twice
    [call] = hooks.calls
    assert call["config"].url == "https://hooks.example.com/x" and call["config"].secret == "s3cret"
    assert call["config"].headers == {"X-A": "1"}
    assert call["execution_id"] == execution_id and call["status"] == "success"
    assert call["workflow_id"] == str(w.wf) and call["tenant_id"] == str(w.tenant)


async def test_an_idempotent_replay_does_not_send_the_webhook_again(w, hooks):
    body = {**EXECUTE, "workflow_id": str(w.wf), "webhook": {"url": "https://hooks.example.com/x"}}
    async with client_for(make_app(w)) as c:
        first = await c.post("/api/v1/workflows/execute", json=body, headers={"Idempotency-Key": "wh-1"})
        eid = first.json()["execution_id"]
        await settle_run(w, eid)
        replay = await c.post("/api/v1/workflows/execute", json=body, headers={"Idempotency-Key": "wh-1"})
    assert replay.json()["execution_id"] == eid
    assert len(hooks.calls) == 1  # the one that moved the row; the replay sends nothing


async def test_a_webhook_failure_never_changes_the_settled_run(w, monkeypatch):
    from app.services import workflow_run_sync as wrs

    class Boom:
        async def send_completion_webhook(self, **kw):
            raise RuntimeError("down")

    monkeypatch.setattr(wrs, "get_webhook_service", lambda: Boom())
    monkeypatch.setattr(wf_api, "is_url_safe", lambda url: None)
    w.engine.task = {"status": "running"}
    async with client_for(make_app(w)) as c:
        r = await c.post("/api/v1/workflows/execute", json={**EXECUTE, "webhook": {"url": "https://hooks.example.com/x"}})
    assert await settle_run(w, r.json()["execution_id"]) is True


async def test_compliance_template_past_the_window_is_a_202_with_the_execution_id(w):
    from app.templates.compliance import get_compliance_templates

    w.engine.task = {"status": "running"}
    template_id = get_compliance_templates()[0]["id"]
    async with client_for(make_app(w)) as c:
        r = await c.post(f"/api/v1/compliance/templates/{template_id}/execute", json={"template_id": "x", "inputs": {}})
    assert r.status_code == 202
    [row] = await executions(w)
    assert r.json() == {"execution_id": str(row.id), "status": "running"}
