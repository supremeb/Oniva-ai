"""Plan 003 U18: /execute-batch on the engine (SQLite tier, fake engine, real routes and node gateway).

The engine itself (fan-out lanes, the parallel bound, checkpoint size) is covered in ``engine/test``; here the
fake engine records what the backend asks of it and the tests play the engine's part against the real node
gateway: ``prepare`` creates the item rows, the item children are settled on those rows, and the reconciler
hook or the finished push applies the result.
"""
import hashlib
import hmac
import importlib
import inspect
import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy import func, select, update

from app.api.v1 import workflows as wf_api
from app.auth.dependencies import get_current_tenant_id, get_current_user, get_current_user_id
from app.core.database import get_db
from app.internal_api import workflow_contract as wc
from app.internal_api import workflow_nodes as wn
from app.models.engine import EngineConversation
from app.models.tenant import Tenant
from app.models.user import User
from app.models.workflow import Workflow, WorkflowExecution
from app.models.workflow_batch import BatchItemStatus, BatchStatus, WorkflowBatch, WorkflowBatchItem
from app.services import run_handlers as rh
from app.services import workflow_batch_engine as be
from app.services import workflow_engine_runs as wer
from app.services.automation_scope import run_conversation_id
from app.services.engine_client import EngineError
from tests.internal.test_invoke import factory, maker  # noqa: F401  (fixtures)

SPEC = {
    "nodes": [
        {"id": "s", "type": "start", "data": {}, "position": {"x": 0.0, "y": 0.0}},
        {"id": "t", "type": "template", "data": {"template": "hi {{name}}"}, "position": {"x": 1.0, "y": 0.0}},
        {"id": "o", "type": "output", "data": {}, "position": {"x": 2.0, "y": 0.0}},
    ],
    "edges": [{"id": "e1", "source": "s", "target": "t"}, {"id": "e2", "source": "t", "target": "o"}],
}
SECRET = "whsec-Zk3vN8qLw1RtYb6"


class FakeEngine:
    def __init__(self):
        self.directory = SimpleNamespace(default_session_id="s1", session_for=lambda tenant, kind: "s1")
        self.starts, self.aborts, self.runs = [], [], {}
        self.down = False
        self.abort_down = False

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


class World:
    pass


@pytest.fixture
async def w(maker, factory, monkeypatch):  # noqa: F811
    world = World()
    world.maker, world.factory = maker, factory
    world.engine = FakeEngine()
    monkeypatch.setattr(wer, "get_engine_client", lambda: world.engine)
    monkeypatch.setattr("app.services.automation_scope.get_engine_client", lambda: world.engine)
    monkeypatch.setattr(wer, "tenant_factory", lambda tenant_id: factory)
    monkeypatch.setattr(wer, "engine_workflows_enabled",
                        lambda tenant, settings=None: bool(((tenant.settings or {}).get("features") or {}).get("ENABLE_ENGINE_WORKFLOWS")))
    world.tenant, world.user = uuid.uuid4(), uuid.uuid4()
    world.other_tenant, world.other_user = uuid.uuid4(), uuid.uuid4()
    world.wf, world.foreign_wf = uuid.uuid4(), uuid.uuid4()
    async with maker() as db:
        for t, flag in ((world.tenant, True), (world.other_tenant, True)):
            db.add(Tenant(id=t, name="t", slug=f"t-{t.hex[:6]}", is_active=True,
                          settings={"features": {"ENABLE_ENGINE_WORKFLOWS": flag}}))
        await db.commit()
        db.add(User(id=world.user, tenant_id=world.tenant, email="a@x.io", name="u"))
        db.add(User(id=world.other_user, tenant_id=world.other_tenant, email="b@x.io", name="u"))
        await db.commit()
        db.add(Workflow(id=world.wf, user_id=world.user, tenant_id=world.tenant, name="W", spec=SPEC))
        db.add(Workflow(id=world.foreign_wf, user_id=world.other_user, tenant_id=world.other_tenant, name="F", spec=SPEC))
        await db.commit()

    # Webhooks: capture the request instead of the network.
    world.hooks = []

    def handler(request: httpx.Request) -> httpx.Response:
        world.hooks.append(request)
        return httpx.Response(200)

    class CapturingService(be.WebhookService):
        async def _get_client(self):
            if self._client is None:
                self._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return self._client

    monkeypatch.setattr(be, "WebhookService", CapturingService)
    monkeypatch.setattr("app.services.webhook_service.is_url_safe", lambda url: None)
    monkeypatch.setattr(wf_api, "is_url_safe", lambda url: None)
    return world


def make_app(w):
    app = FastAPI()
    app.include_router(wf_api.router, prefix="/api/v1/workflows")

    async def db():
        async with w.maker() as session:
            yield session

    app.dependency_overrides[get_db] = db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=str(w.user), role="member", email="u@x.io", tenant_id=str(w.tenant))
    app.dependency_overrides[get_current_user_id] = lambda: w.user
    app.dependency_overrides[get_current_tenant_id] = lambda: w.tenant
    return app


def client_for(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=60)


def payload(n, **extra):
    return {"workflow_id": None, "executions": [{"inputs": {"name": f"n{i}"}} for i in range(n)], **extra}


async def execute(w, n, *, workflow=None, **extra):
    body = {**payload(n, **extra), "workflow_id": str(workflow or w.wf)}
    async with client_for(make_app(w)) as c:
        return await c.post("/api/v1/workflows/execute-batch", json=body)


async def batch_of(w, batch_id):
    async with w.maker() as db:
        return await db.get(WorkflowBatch, uuid.UUID(str(batch_id)))


async def count(w, model, *where):
    async with w.maker() as db:
        return (await db.execute(select(func.count()).select_from(model).where(*where))).scalar_one()


def item_ids(batch_id, n):
    return [be.item_execution_id(batch_id, i) for i in range(n)]


async def prepare(w, batch_id, run_attempt=0):
    """The gateway's fan-out prepare for the batch node, as the engine would call it."""
    scope = await wn.bind_run(w.factory, uuid.UUID(str(batch_id)), wn.BodyScope())
    view = await wn.load_run(lambda tid: w.factory(), w.tenant, uuid.UUID(str(batch_id)), be.NODE_ID)
    return await wn.run_prepare(lambda tid: w.factory(), view, scope, str(batch_id), run_attempt)


async def settle_items(w, batch_id, n, *, failed=(), running=(), cancelled=(), pending=()):
    """Play the item children: every item completes with a result, except the listed ones."""
    now = datetime.now(timezone.utc)
    async with w.maker() as db:
        for i, iid in enumerate(item_ids(batch_id, n)):
            row = await db.get(WorkflowExecution, iid)
            if i in pending:
                continue
            if i in running:
                row.status = "running"
            elif i in cancelled:
                row.status, row.completed_at = "cancelled", now
            elif i in failed:
                row.status, row.error, row.completed_at = "failed", f"item {i} failed", now
            else:
                row.status, row.results, row.completed_at = "completed", {"greeting": f"hi n{i}"}, now
        await db.commit()


async def finished_raw(w, batch_id, snapshot):
    batch = await batch_of(w, batch_id)
    await be.on_finished(rh.RunRef(kind="batch", ref_id=batch.id, tenant_id=batch.tenant_id), snapshot)


async def finished(w, batch_id, status="completed"):
    batch = await batch_of(w, batch_id)
    ref = rh.RunRef(kind="batch", ref_id=batch.id, tenant_id=batch.tenant_id)
    await be.on_finished(ref, {"runStatus": status})


# ---- start ------------------------------------------------------------------------------------------


async def test_execute_batch_is_one_engine_run_with_a_single_fanout_node(w):
    r = await execute(w, 25, parallel_limit=5)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "running" and body["total_count"] == 25
    batch = await batch_of(w, body["batch_id"])
    assert batch.status == BatchStatus.RUNNING and batch.started_at is not None
    assert await count(w, WorkflowBatchItem) == 25
    [start] = w.engine.starts
    assert start["key"] == f"batch:{batch.id}"
    assert start["body"]["id"] == str(run_conversation_id("batch", batch.id)) and start["body"]["kind"] == "workflow_run"
    run_input = start["body"]["input"]
    assert run_input["runId"] == str(batch.id)
    [node] = run_input["graph"]["nodes"]
    assert node["id"] == "batch" and node["type"] == "batch" and node["mayFanOut"] is True
    assert node["policy"]["maxRetries"] == 0
    assert [n["id"] for n in run_input["graph"]["workflows"][str(w.wf)]["nodes"]] == ["s", "t", "o"]
    async with w.maker() as db:
        execution = await db.get(WorkflowExecution, batch.id)
        conv = (await db.execute(select(EngineConversation).where(
            EngineConversation.conversation_id == run_conversation_id("batch", batch.id)))).scalar_one()
    assert execution.status == "running" and execution.trigger == "batch"
    assert conv.run_kind == "batch" and conv.run_ref_id == batch.id


async def test_prepare_creates_item_rows_and_returns_the_parallel_limit_and_no_payloads(w):
    batch_id = (await execute(w, 25, parallel_limit=5)).json()["batch_id"]
    body = wc.validate_prepare_response(await prepare(w, batch_id))
    assert body.kind == "fanout" and body.count == 25 and body.parallel == 5 and body.failFast is False
    assert body.target.workflowId == str(w.wf)
    assert "n7" not in json.dumps(body.model_dump())  # no item input in the body
    assert await count(w, WorkflowExecution, WorkflowExecution.id != uuid.UUID(batch_id)) == 25
    async with w.maker() as db:
        row = await db.get(WorkflowExecution, be.item_execution_id(batch_id, 7))
    assert row.input_data["name"] == "n7" and row.input_data["__parent_run_id"] == batch_id
    assert row.workflow_id == w.wf
    await prepare(w, batch_id)  # a node retry (a new key, same run attempt) finds the rows
    assert await count(w, WorkflowExecution, WorkflowExecution.id != uuid.UUID(batch_id)) == 25


async def test_parallel_limit_is_clamped_to_the_engine_maximum(w):
    batch_id = (await execute(w, 3, parallel_limit=50)).json()["batch_id"]
    assert (await prepare(w, batch_id))["parallel"] == wc.MAX_PARALLEL
    assert be.clamp_parallel(500) == wc.MAX_PARALLEL and be.clamp_parallel(0) == 1


# ---- settle -----------------------------------------------------------------------------------------


async def test_25_items_complete_and_the_counts_are_applied(w):
    batch_id = (await execute(w, 25, parallel_limit=5)).json()["batch_id"]
    await prepare(w, batch_id)
    await settle_items(w, batch_id, 25)
    await finished(w, batch_id)
    batch = await batch_of(w, batch_id)
    assert batch.status == BatchStatus.COMPLETED and batch.completed_count == 25
    assert batch.failed_count == 0 and batch.pending_count == 0 and batch.completed_at is not None
    async with client_for(make_app(w)) as c:
        status = (await c.get(f"/api/v1/workflows/batches/{batch_id}")).json()
        items = (await c.get(f"/api/v1/workflows/batches/{batch_id}/items?limit=50")).json()
    assert status["status"] == "completed" and status["completed_count"] == 25 and status["progress_percent"] == 100
    assert [i["status"] for i in items] == ["completed"] * 25
    assert items[3]["results"] == {"greeting": "hi n3"} and items[3]["inputs"] == {"name": "n3"}
    async with w.maker() as db:
        assert (await db.get(WorkflowExecution, uuid.UUID(batch_id))).status == "completed"


async def test_an_item_failure_fails_only_that_item_and_the_batch_is_partially_failed(w):
    batch_id = (await execute(w, 5)).json()["batch_id"]
    await prepare(w, batch_id)
    await settle_items(w, batch_id, 5, failed=(2,))
    await finished(w, batch_id)
    batch = await batch_of(w, batch_id)
    assert batch.status == BatchStatus.PARTIALLY_FAILED and batch.completed_count == 4 and batch.failed_count == 1
    async with w.maker() as db:
        items = (await db.execute(select(WorkflowBatchItem).order_by(WorkflowBatchItem.sequence))).scalars().all()
    assert [i.status for i in items] == [BatchItemStatus.COMPLETED] * 2 + [BatchItemStatus.FAILED] + [BatchItemStatus.COMPLETED] * 2
    assert items[2].error_message == "item 2 failed" and items[0].results == {"greeting": "hi n0"}


async def test_when_every_item_fails_the_batch_is_failed(w):
    batch_id = (await execute(w, 3)).json()["batch_id"]
    await prepare(w, batch_id)
    await settle_items(w, batch_id, 3, failed=(0, 1, 2))
    await finished(w, batch_id)
    assert (await batch_of(w, batch_id)).status == BatchStatus.FAILED


async def test_progress_is_visible_while_the_batch_runs(w):
    batch_id = (await execute(w, 10, parallel_limit=2)).json()["batch_id"]
    await prepare(w, batch_id)
    await settle_items(w, batch_id, 10, running=(4, 5), pending=tuple(range(6, 10)))
    async with client_for(make_app(w)) as c:
        status = (await c.get(f"/api/v1/workflows/batches/{batch_id}")).json()
    assert status["status"] == "running" and status["completed_count"] == 4 and status["pending_count"] == 6


async def test_an_engine_run_that_failed_marks_unfinished_items_failed(w):
    batch_id = (await execute(w, 4)).json()["batch_id"]
    await prepare(w, batch_id)
    await settle_items(w, batch_id, 4, pending=(2, 3))
    await finished(w, batch_id, status="failed")
    batch = await batch_of(w, batch_id)
    assert batch.status == BatchStatus.PARTIALLY_FAILED and batch.completed_count == 2 and batch.failed_count == 2
    assert batch.error_message == "engine_run_failed"


async def test_on_finished_applies_a_settled_run_once_and_a_terminal_batch_never_moves(w):
    batch_id = (await execute(w, 3, webhook_url="https://hooks.example.com/b", webhook_secret=SECRET)).json()["batch_id"]
    await prepare(w, batch_id)
    await settle_items(w, batch_id, 3)
    await finished(w, batch_id)
    done_at = (await batch_of(w, batch_id)).completed_at
    await finished(w, batch_id, status="failed")  # a late, conflicting settle
    batch = await batch_of(w, batch_id)
    assert batch.status == BatchStatus.COMPLETED and batch.completed_at == done_at and len(w.hooks) == 1


# ---- webhook ----------------------------------------------------------------------------------------


async def test_the_completion_webhook_fires_once_with_a_valid_signature(w):
    batch_id = (await execute(w, 3, webhook_url="https://hooks.example.com/b", webhook_secret=SECRET)).json()["batch_id"]
    await prepare(w, batch_id)
    await settle_items(w, batch_id, 3, failed=(1,))
    await finished(w, batch_id)
    await finished(w, batch_id)
    [request] = w.hooks
    raw = request.content
    expected = "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    assert request.headers["x-signature"] == expected and request.headers["x-signature-256"] == expected
    sent = json.loads(raw)
    assert sent["event_type"] == "batch.partially_failed" and sent["execution_id"] == batch_id
    assert sent["results"]["completed_count"] == 2 and sent["results"]["failed_count"] == 1
    assert str(request.url) == "https://hooks.example.com/b"


async def test_no_webhook_without_a_url(w):
    batch_id = (await execute(w, 2)).json()["batch_id"]
    await prepare(w, batch_id)
    await settle_items(w, batch_id, 2)
    await finished(w, batch_id)
    assert w.hooks == []


# ---- cancel -----------------------------------------------------------------------------------------


async def test_cancel_aborts_the_run_skips_pending_items_and_keeps_finished_results(w):
    batch_id = (await execute(w, 10, parallel_limit=3)).json()["batch_id"]
    await prepare(w, batch_id)
    await settle_items(w, batch_id, 10, running=(3, 4, 5), pending=tuple(range(6, 10)))
    async with client_for(make_app(w)) as c:
        r = await c.post(f"/api/v1/workflows/batches/{batch_id}/cancel")
        again = await c.post(f"/api/v1/workflows/batches/{batch_id}/cancel")
    assert r.status_code == 200 and again.status_code == 400
    assert w.engine.aborts == [(run_conversation_id("batch", batch_id), "Batch cancelled by user")]
    batch = await batch_of(w, batch_id)
    assert batch.status == BatchStatus.CANCELLED and batch.completed_count == 3 and batch.pending_count == 0
    async with w.maker() as db:
        items = (await db.execute(select(WorkflowBatchItem).order_by(WorkflowBatchItem.sequence))).scalars().all()
        rows = {i: (await db.get(WorkflowExecution, be.item_execution_id(batch_id, i))).status for i in range(10)}
    assert [i.status for i in items[:3]] == [BatchItemStatus.COMPLETED] * 3
    assert {i.status for i in items[3:]} == {BatchItemStatus.SKIPPED}
    assert {rows[i] for i in range(3, 10)} == {"cancelled"} and items[0].results == {"greeting": "hi n0"}
    await finished(w, batch_id, status="cancelled")  # the engine's settle changes nothing
    assert (await batch_of(w, batch_id)).status == BatchStatus.CANCELLED and w.hooks == []


async def test_an_abort_the_engine_could_not_take_is_retried_by_start_missing(w):
    batch_id = (await execute(w, 3)).json()["batch_id"]
    w.engine.abort_down = True
    async with client_for(make_app(w)) as c:
        assert (await c.post(f"/api/v1/workflows/batches/{batch_id}/cancel")).status_code == 200
    assert (await batch_of(w, batch_id)).status == BatchStatus.CANCELLED and w.engine.aborts == []
    old = datetime.now(timezone.utc) - timedelta(hours=1)
    async with w.maker() as db:
        await db.execute(update(WorkflowBatch).values(completed_at=old, updated_at=old))
        await db.commit()
    async with w.maker() as db:
        [ref] = await be.find_stuck(db, timedelta(seconds=120))
    assert ref.extra == {"abort_pending": True}
    w.engine.abort_down = False
    assert (await be.start_missing(ref)).status == "already_started"
    assert len(w.engine.aborts) == 1
    async with w.maker() as db:
        assert await be.find_stuck(db, timedelta(seconds=120)) == []


async def test_cancelling_a_batch_that_was_created_with_the_flag_off_skips_its_pending_items(w):
    async with w.maker() as db:
        tenant = await db.get(Tenant, w.tenant)
        tenant.settings = {}
        await db.commit()
    r = await execute(w, 3)
    assert r.json()["status"] == "queued" and w.engine.starts == []
    batch_id = r.json()["batch_id"]
    async with client_for(make_app(w)) as c:
        assert (await c.post(f"/api/v1/workflows/batches/{batch_id}/cancel")).status_code == 200
    assert (await batch_of(w, batch_id)).status == BatchStatus.CANCELLED
    assert w.engine.aborts == [] and await count(w, WorkflowExecution) == 0
    async with w.maker() as db:
        items = (await db.execute(select(WorkflowBatchItem))).scalars().all()
    assert {i.status for i in items} == {BatchItemStatus.SKIPPED}


# ---- limits -----------------------------------------------------------------------------------------


async def test_a_batch_over_1000_is_a_400_and_creates_nothing(w):
    r = await execute(w, 1001)
    assert r.status_code == 400 and "1000" in r.text
    assert await count(w, WorkflowBatch) == 0 and w.engine.starts == []


async def test_a_foreign_workflow_is_404(w):
    r = await execute(w, 2, workflow=w.foreign_wf)
    assert r.status_code == 404
    assert await count(w, WorkflowBatch) == 0 and w.engine.starts == []


async def test_a_batch_status_of_another_tenant_is_404(w):
    batch_id = (await execute(w, 2)).json()["batch_id"]
    app = make_app(w)
    app.dependency_overrides[get_current_tenant_id] = lambda: w.other_tenant
    async with client_for(app) as c:
        assert (await c.get(f"/api/v1/workflows/batches/{batch_id}")).status_code == 404
        assert (await c.post(f"/api/v1/workflows/batches/{batch_id}/cancel")).status_code == 404


async def test_the_quota_check_is_kept_and_runs_before_anything_is_created(w, monkeypatch):
    from app.services.quota_service import QuotaService

    seen = []

    async def check(self, tenant, user, quota_type, estimated_usage=1):
        seen.append(estimated_usage)
        raise HTTPException(status_code=429, detail="quota exceeded")

    monkeypatch.setattr(QuotaService, "check_and_raise", check)
    r = await execute(w, 7)
    assert r.status_code == 429 and seen == [7]
    assert await count(w, WorkflowBatch) == 0 and w.engine.starts == []


async def test_an_invalid_workflow_is_a_400_and_creates_nothing(w):
    async with w.maker() as db:
        (await db.get(Workflow, w.wf)).spec = {"nodes": SPEC["nodes"][:2], "edges": [
            {"id": "a", "source": "s", "target": "t"}, {"id": "b", "source": "t", "target": "s"}]}
        await db.commit()
    r = await execute(w, 2)
    assert r.status_code == 400
    assert await count(w, WorkflowBatch) == 0 and w.engine.starts == []


# ---- 1000 items -------------------------------------------------------------------------------------


async def test_a_batch_of_1000_items_at_parallel_10_completes_without_item_payloads_in_engine_state(w):
    r = await execute(w, 1000, parallel_limit=10)
    assert r.status_code == 200, r.text
    batch_id = r.json()["batch_id"]
    assert (await batch_of(w, batch_id)).total_count == 1000

    # What the engine is handed and what it checkpoints: ids, counts and the graph, never items.
    [start] = w.engine.starts
    started = json.dumps(start["body"])
    assert len(started) < 8 * 1024 and "n999" not in started and "name" not in started

    response = await prepare(w, batch_id)
    body = wc.validate_prepare_response(response)
    serialised = json.dumps(response)
    assert body.count == 1000 and body.parallel == 10 and body.failFast is False
    assert len(serialised.encode()) < wc.MAX_BODY_BYTES and len(serialised) < 1024 and "n999" not in serialised
    assert await count(w, WorkflowExecution, WorkflowExecution.id != uuid.UUID(batch_id)) == 1000

    # Settle the 1000 item rows in one statement, as the children's finalize would.
    async with w.maker() as db:
        await db.execute(update(WorkflowExecution).where(WorkflowExecution.id.in_(item_ids(batch_id, 1000)))
                         .values(status="completed", results={"ok": True}, completed_at=datetime.now(timezone.utc)))
        await db.commit()

    # The fanout node's finish returns counts only (no per-item result crosses the engine).
    scope = await wn.bind_run(w.factory, uuid.UUID(batch_id), wn.BodyScope())
    view = await wn.load_run(lambda tid: w.factory(), w.tenant, uuid.UUID(batch_id), be.NODE_ID)
    fin = wc.FinishInput.model_validate({
        "contractVersion": wc.CONTRACT_VERSION, "kind": "fanout",
        "fanout": {"count": 1000, "completed": 1000, "failed": 0, "cancelled": 0}})
    final = await wn.run_finish(lambda tid: w.factory(), view, scope, batch_id, 0, fin)
    assert final["status"] == "success" and final["output"] == {"count": 1000, "success_count": 1000, "error_count": 0}

    await finished(w, batch_id)
    batch = await batch_of(w, batch_id)
    assert batch.status == BatchStatus.COMPLETED and batch.completed_count == 1000 and batch.pending_count == 0
    async with w.maker() as db:
        done = (await db.execute(select(func.count()).select_from(WorkflowBatchItem).where(
            WorkflowBatchItem.status == BatchItemStatus.COMPLETED))).scalar_one()
    assert done == 1000


async def test_a_batch_node_failing_items_does_not_fail_the_fanout_node(w):
    batch_id = (await execute(w, 3)).json()["batch_id"]
    await prepare(w, batch_id)
    await settle_items(w, batch_id, 3, failed=(0,))
    scope = await wn.bind_run(w.factory, uuid.UUID(batch_id), wn.BodyScope())
    view = await wn.load_run(lambda tid: w.factory(), w.tenant, uuid.UUID(batch_id), be.NODE_ID)
    fin = wc.FinishInput.model_validate({
        "contractVersion": wc.CONTRACT_VERSION, "kind": "fanout",
        "fanout": {"count": 3, "completed": 2, "failed": 1, "cancelled": 0, "firstError": "item 0 failed"}})
    final = await wn.run_finish(lambda tid: w.factory(), view, scope, batch_id, 0, fin)
    assert final["status"] == "success" and final["output"]["error_count"] == 1


# ---- handler registry -------------------------------------------------------------------------------


async def test_the_batch_kind_registers_on_finished_and_start_missing_and_bootstraps():
    rh.reset()
    importlib.reload(be)
    assert rh.hook("batch", "on_finished") is not None and rh.hook("batch", "start_missing") is not None
    assert rh.hook("batch", "find_stuck") is not None and rh.hook("batch", "fail") is not None
    assert "app.services.workflow_batch_engine" in rh.BOOTSTRAP_MODULES
    rh.reset()
    importlib.reload(be)
    assert "batch" in rh.bootstrap()
    rh.reset()
    for name in rh.BOOTSTRAP_MODULES:
        importlib.reload(importlib.import_module(name))


async def test_a_batch_row_with_no_engine_run_is_started_once_by_start_missing(w):
    w.engine.down = True
    r = await execute(w, 4)
    assert r.status_code == 200 and r.json()["status"] == "queued"  # accepted; the start is the reconciler's
    batch_id = uuid.UUID(r.json()["batch_id"])
    assert (await batch_of(w, batch_id)).status == BatchStatus.QUEUED and len(w.engine.starts) == 1
    w.engine.down = False
    rh.reset()
    importlib.reload(be)
    ref = rh.RunRef(kind="batch", ref_id=batch_id, tenant_id=w.tenant)
    first = await rh.hook("batch", "start_missing")(ref)
    second = await rh.hook("batch", "start_missing")(ref)
    assert first.status == "started" and first.task_id and second.status == "already_started"
    assert len(w.engine.starts) == 2 and len(w.engine.runs) == 1  # the same deterministic run
    assert w.engine.starts[1]["key"] == f"batch:{batch_id}" == w.engine.starts[0]["key"]
    assert (await batch_of(w, batch_id)).status == BatchStatus.RUNNING
    rh.reset()
    for name in rh.BOOTSTRAP_MODULES:
        importlib.reload(importlib.import_module(name))


async def test_start_missing_counts_failed_attempts_on_the_run_row(w):
    w.engine.down = True
    batch_id = uuid.UUID((await execute(w, 2)).json()["batch_id"])
    ref = rh.RunRef(kind="batch", ref_id=batch_id, tenant_id=w.tenant)
    result = await be.start_missing(ref)
    again = await be.start_missing(ref)
    assert result.status == "failed" and result.error == "engine_unreachable" and result.attempts == 1
    assert again.attempts == 2
    async with w.maker() as db:
        assert (await db.get(WorkflowExecution, batch_id)).input_data["start_attempts"] == 2


async def test_find_stuck_lists_quiet_engine_batches_only_and_the_workflow_kind_ignores_them(w):
    batch_id = uuid.UUID((await execute(w, 2)).json()["batch_id"])
    async with w.maker() as db:
        assert await be.find_stuck(db, timedelta(seconds=120)) == []  # just touched
    old = datetime.now(timezone.utc) - timedelta(hours=1)
    async with w.maker() as db:
        await db.execute(update(WorkflowBatch).values(updated_at=old))
        await db.execute(update(WorkflowExecution).values(started_at=old))
        await db.commit()
    async with w.maker() as db:
        [ref] = await be.find_stuck(db, timedelta(seconds=120))
        assert (ref.kind, ref.ref_id, ref.tenant_id) == ("batch", batch_id, w.tenant)
        assert await wer.find_stuck(db, timedelta(seconds=120)) == []


async def test_fail_is_terminal_once_and_skips_the_open_items(w):
    batch_id = uuid.UUID((await execute(w, 3, webhook_url="https://hooks.example.com/b", webhook_secret=SECRET)).json()["batch_id"])
    ref = rh.RunRef(kind="batch", ref_id=batch_id, tenant_id=w.tenant)
    await be.fail(ref, "engine_start_failed")
    await be.fail(ref, "engine_start_failed")
    batch = await batch_of(w, batch_id)
    assert batch.status == BatchStatus.FAILED and batch.error_message == "engine_start_failed"
    assert len(w.hooks) == 1 and json.loads(w.hooks[0].content)["event_type"] == "batch.failed"
    async with w.maker() as db:
        assert (await db.get(WorkflowExecution, batch_id)).status == "failed"


def test_the_route_no_longer_leaves_batches_queued_forever():
    source = inspect.getsource(wf_api.execute_batch)
    assert "TODO" not in source and "Celery" not in source


async def test_a_run_retry_prepares_item_rows_of_its_own(w):
    batch_id = (await execute(w, 4)).json()["batch_id"]
    await prepare(w, batch_id)
    await prepare(w, batch_id, run_attempt=1)
    assert await count(w, WorkflowExecution, WorkflowExecution.id != uuid.UUID(batch_id)) == 8
    assert be.item_execution_id(batch_id, 0) == wc.fanout_item_id(uuid.UUID(batch_id), be.NODE_ID, 0)


async def test_a_raw_push_status_maps_through_the_run_status_not_the_push_word(w):
    batch_id = (await execute(w, 3)).json()["batch_id"]
    await prepare(w, batch_id)
    await settle_items(w, batch_id, 3)
    await finished_raw(w, batch_id, {"source": "push", "status": "done"})
    assert (await batch_of(w, batch_id)).status == BatchStatus.COMPLETED
    other = (await execute(w, 2)).json()["batch_id"]
    await prepare(w, other)
    await settle_items(w, other, 2, failed=(0, 1))
    await finished_raw(w, other, {"source": "push", "status": "unanswered", "reason": "boom"})
    failed = await batch_of(w, other)
    assert failed.status == BatchStatus.FAILED and failed.error_message == "boom"


async def test_the_runs_finalize_settles_the_batch_without_the_reconciler(w):
    from app.services import workflow_run_sync as wrs

    batch_id = (await execute(w, 3, webhook_url="https://hooks.example.com/b", webhook_secret=SECRET)).json()["batch_id"]
    await prepare(w, batch_id)
    await settle_items(w, batch_id, 3)
    body = SimpleNamespace(status="completed", nodeSummaries={}, error=None, attempts=1)
    assert await wrs.finalize(lambda tid: w.factory(), w.tenant, batch_id, body) is True
    batch = await batch_of(w, batch_id)
    assert batch.status == BatchStatus.COMPLETED and batch.completed_count == 3 and len(w.hooks) == 1
    assert await wrs.finalize(lambda tid: w.factory(), w.tenant, batch_id, body) is False  # a replay moves nothing
    assert len(w.hooks) == 1
