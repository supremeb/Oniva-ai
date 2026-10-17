"""U21: a human-in-loop node parks on a ledger row; Phase 5's decide path signals the node task (R6, KTD7)."""
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from app.api.v1 import engine_conversations as ec
from app.auth.dependencies import get_current_user
from app.config import Settings
from app.core.database import get_db
from app.internal_api import workflow_contract as wc
from app.models.engine import EngineConversation
from app.models.tenant import Tenant
from app.models.tool_approval import ToolApproval
from app.node_executors import NodeExecutionContext, NodeExecutionResult
from app.node_executors.human_in_loop_executor import HumanInLoopExecutor
from app.services import approval_service as aps
from app.services import workflow_wait_decisions as wwd
from app.services.approval_service import ApprovalError, ApprovalService
from app.services.engine_client import EngineClient, EngineError, get_engine_client
from tests.test_approval_service import FakeAudit, TENANT, PROJECT, auth, world  # noqa: F401  (fixtures)

NODE_TASK = "task-run:r0:n1"
SERVICE = Path(__file__).resolve().parents[2] / "app" / "node_executors" / "human_in_loop_executor.py"


class SignalEngine:
    """Duck-typed EngineClient: records ``signal_task``; scripted failures."""

    def __init__(self):
        self.signals = []
        self.outcomes = []
        self.decide_calls = []

    async def signal_task(self, session_id, tenant_id, project_id, conversation_id, task_id, key, payload=None):
        self.signals.append({"task": task_id, "key": key, "payload": payload, "conversation": str(conversation_id)})
        if self.outcomes:
            out = self.outcomes.pop(0)
            if isinstance(out, Exception):
                raise out
        return {"delivered": True}

    async def decide(self, *a, **kw):  # pragma: no cover - must never be called for a workflow wait
        self.decide_calls.append(a)
        raise AssertionError("EngineClient.decide must not be used for a workflow wait")


@pytest.fixture
async def wf(world, monkeypatch):
    """The shared approvals world plus a workflow run conversation and an execution id."""
    # Services built inside the code under test get the recording audit (the real chain needs Postgres).
    monkeypatch.setattr(aps, "AuditChain", lambda db, kms=None: world.audit)
    conv = EngineConversation(conversation_id=uuid.uuid4(), tenant_id=TENANT, project_id=PROJECT,
                              user_id=world.users["member"].id, session_id="s1", run_kind="workflow",
                              run_ref_id=uuid.uuid4())
    world.db.add(conv)
    await world.db.commit()
    world.wconv, world.run_id = conv, str(conv.run_ref_id)
    world.sig = SignalEngine()
    world.svc = ApprovalService(world.db, audit=world.audit)
    return world


def ctx(wf, node_task=NODE_TASK, user="member", **data):
    return NodeExecutionContext(
        node_id="n1", node_type="human_in_loop", node_data=data, inputs={}, variables={},
        user_id=str(wf.users[user].id), tenant_id=str(TENANT), session_id=str(wf.wconv.conversation_id),
        workflow_id=str(uuid.uuid4()), workflow_run_id=wf.run_id, db=wf.db,
        node_task_id=node_task, run_attempt=0,
    )


async def rows(wf):
    return list((await wf.db.execute(select(ToolApproval))).scalars().all())


def fin(**over):
    return wc.validate_finish_input({"contractVersion": 1, "kind": "wait", **over})


async def park(wf, **data):
    body = await HumanInLoopExecutor().prepare(ctx(wf, **data), wf.db)
    assert not isinstance(body, NodeExecutionResult), body
    row = (await rows(wf))[0]
    return body, row


# ---- prepare --------------------------------------------------------------------------------

async def test_prepare_returns_a_wait_body_with_a_signal_and_the_sla(wf):
    before = datetime.now(timezone.utc)
    body = await HumanInLoopExecutor().prepare(ctx(wf, title="Ship it?", slaHours=2), wf.db)
    parsed = wc.validate_prepare_response(body)
    assert parsed.kind == "wait" and parsed.signal == "decision:n1"
    assert parsed.durationMs == 7 * 24 * 3600 * 1000  # the maxWaitSeconds cap bounds a node with no SLA
    sla = datetime.fromtimestamp(parsed.slaUntilEpochMs / 1000, timezone.utc)
    assert abs((sla - before - timedelta(hours=2)).total_seconds()) < 30


async def test_prepare_records_exactly_one_review_ledger_row_and_a_replay_adds_none(wf):
    c = ctx(wf, title="Ship it?", description="Release 4", slaHours=1, approverType="role",
            approverConfig={"role": "admin"})
    first = await HumanInLoopExecutor().prepare(c, wf.db)
    second = await HumanInLoopExecutor().prepare(c, wf.db)
    assert first == second
    (row,) = await rows(wf)
    assert row.kind == "review" and row.status == "pending"
    assert row.tool_task_id == NODE_TASK and row.run_id == wf.run_id
    assert str(row.conversation_id) == str(wf.wconv.conversation_id)
    assert row.engine_conversation_id == str(wf.wconv.conversation_id)
    assert row.approver_role == "admin" and row.tool_name == "Ship it?" and row.reason == "Release 4"
    assert str(row.requested_by) == str(wf.users["member"].id) and str(row.project_id) == str(PROJECT)
    assert row.expires_at is not None


async def test_prepare_without_an_sla_leaves_the_row_open_ended(wf):
    _, row = await park(wf, title="t")
    assert row.expires_at is None and row.approver_role == "member"


async def test_prepare_fails_without_the_node_task_id_or_with_a_bad_sla(wf):
    no_task = await HumanInLoopExecutor().prepare(ctx(wf, node_task=None), wf.db)
    assert no_task.status == "failed" and "node task" in no_task.error
    bad = await HumanInLoopExecutor().prepare(ctx(wf, slaHours=-3), wf.db)
    assert bad.status == "failed" and "slaHours" in bad.error
    over = await HumanInLoopExecutor().prepare(ctx(wf, slaHours=24 * 8), wf.db)
    assert over.status == "failed" and "exceeds maximum" in over.error
    assert await rows(wf) == []


async def test_the_replay_input_is_gone(wf):
    assert "__approval_decision" not in SERVICE.read_text()
    c = ctx(wf, title="t")
    c.inputs["__approval_decision"] = {"decision": "approve"}
    body = await HumanInLoopExecutor().prepare(c, wf.db)  # resumes by parking, not by trusting an input
    assert wc.validate_prepare_response(body).kind == "wait"


# ---- deciding through Phase 5's path -------------------------------------------------------

async def decide(wf, who, decision="approve", note=None, row=None, role=None):
    row = row or (await rows(wf))[0]
    return await wf.svc.decide(user=auth(wf.users[who], role), conv=wf.wconv, row=row, decision=decision,
                               note=note, engine=wf.sig)


async def test_an_unauthorised_user_is_403_and_nothing_is_signalled(wf):
    await park(wf, title="t")
    with pytest.raises(ApprovalError) as ei:
        await decide(wf, "member2")
    assert (ei.value.status, ei.value.code) == (403, "not_authorized")
    assert wf.sig.signals == []
    assert (await rows(wf))[0].status == "pending"


async def test_a_viewer_and_another_tenant_cannot_decide(wf):
    await park(wf, title="t")
    with pytest.raises(ApprovalError) as v:
        await decide(wf, "viewer")
    assert v.value.status == 403
    with pytest.raises(ApprovalError) as s:
        await decide(wf, "stranger")
    assert s.value.status == 404
    assert wf.sig.signals == []


async def test_an_authorised_approve_signals_the_node_once_with_the_decision(wf):
    await park(wf, title="t")
    await decide(wf, "admin", note="looks fine")
    assert len(wf.sig.signals) == 1 and wf.sig.decide_calls == []
    sig = wf.sig.signals[0]
    assert sig["task"] == NODE_TASK and sig["key"] == "decision:n1"
    assert sig["conversation"] == str(wf.wconv.conversation_id)
    payload = sig["payload"]
    assert payload["decision"] == "approve" and payload["optionId"] == "approve"
    assert payload["decidedBy"] == str(wf.users["admin"].id) and payload["comment"] == "looks fine"
    row = (await rows(wf))[0]
    assert row.status == "approved" and row.decided_by == wf.users["admin"].id
    assert len(await wf.audits(aps.AuditAction.APPROVAL_DECIDED)) == 1


async def test_the_requester_may_decide_a_member_level_wait(wf):
    await park(wf, title="t")
    await decide(wf, "member")
    assert len(wf.sig.signals) == 1


async def test_deny_maps_to_the_reject_option(wf):
    await park(wf, title="t")
    await decide(wf, "admin", decision="deny")
    payload = wf.sig.signals[0]["payload"]
    assert payload["decision"] == "deny" and payload["optionId"] == "reject"
    assert (await rows(wf))[0].status == "denied"


async def test_custom_options_map_approve_and_deny_to_their_ids(wf):
    await park(wf, title="t", options=[{"id": "ship", "label": "Ship"}, {"id": "hold", "label": "Hold"}])
    await decide(wf, "admin", decision="deny")
    assert wf.sig.signals[0]["payload"]["optionId"] == "hold"


async def test_first_decision_wins_and_a_second_is_409(wf):
    await park(wf, title="t")
    await decide(wf, "admin")
    with pytest.raises(ApprovalError) as ei:
        await decide(wf, "owner", decision="deny")
    assert (ei.value.status, ei.value.code) == (409, "already_decided")
    assert len(wf.sig.signals) == 1


async def test_a_signal_the_engine_rejects_means_the_node_is_no_longer_waiting(wf):
    await park(wf, title="t")
    wf.sig.outcomes = [EngineError(409, "signal_rejected")]
    with pytest.raises(ApprovalError) as ei:
        await decide(wf, "admin")
    assert (ei.value.status, ei.value.code) == (409, "not_waiting")
    assert (await rows(wf))[0].status == "cancelled"


async def test_an_engine_outage_leaves_the_row_deciding_and_a_retry_completes(wf):
    await park(wf, title="t")
    wf.sig.outcomes = [EngineError(502, "engine_unreachable")]
    with pytest.raises(EngineError):
        await decide(wf, "admin")
    row = (await rows(wf))[0]
    assert row.status == "deciding"
    with pytest.raises(ApprovalError) as other:
        await decide(wf, "owner")  # someone else is mid-decision
    assert other.value.code == "decision_in_progress"
    await decide(wf, "admin")
    assert (await rows(wf))[0].status == "approved" and len(wf.sig.signals) == 2


async def test_a_retry_whose_first_signal_landed_is_treated_as_delivered(wf):
    await park(wf, title="t")
    wf.sig.outcomes = [EngineError(504, "timeout")]
    with pytest.raises(EngineError):
        await decide(wf, "admin")
    wf.sig.outcomes = [EngineError(409, "signal_rejected")]  # key already delivered by the first call
    await decide(wf, "admin")
    assert (await rows(wf))[0].status == "approved"


async def test_deciding_after_the_sla_is_410_and_signals_nothing(wf):
    await park(wf, title="t", slaHours=1)
    row = (await rows(wf))[0]
    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    await wf.db.commit()
    with pytest.raises(ApprovalError) as ei:
        await decide(wf, "admin")
    assert (ei.value.status, ei.value.code) == (410, "approval_expired")
    assert wf.sig.signals == [] and (await rows(wf))[0].status == "expired"


async def test_a_cancelled_or_expired_row_cannot_be_decided(wf):
    await park(wf, title="t")
    await wf.svc.mark_closed(TENANT, run_id=wf.run_id, reason="cancelled")
    with pytest.raises(ApprovalError) as ei:
        await decide(wf, "admin")
    assert (ei.value.status, ei.value.code) == (409, "not_waiting")


# ---- cancel / completion close the row -------------------------------------------------------

async def test_cancelling_the_run_closes_the_open_row(wf):
    from app.services import workflow_engine_runs as wer

    await park(wf, title="t")

    class Exec:
        status = "running"
        id = uuid.UUID(wf.run_id)
        tenant_id = TENANT
        engine_conversation_id = wf.wconv.conversation_id
        input_data = {}
        completed_at = None
        error = None

    async def no_abort(*a, **kw):
        return True

    orig = wer.abort_run
    wer.abort_run = no_abort
    try:
        assert await wer.cancel_workflow_run(wf.db, Exec(), "stop") is True
    finally:
        wer.abort_run = orig
    assert (await rows(wf))[0].status == "cancelled"


async def test_mark_closed_can_skip_its_own_commit(wf):
    await park(wf, title="t")
    n = await wf.svc.mark_closed(TENANT, run_id=wf.run_id, reason="run failed", commit=False)
    assert n == 1 and (await rows(wf))[0].status == "cancelled"


# ---- finish ---------------------------------------------------------------------------------

async def test_finish_maps_an_approve_signal_to_the_option_handle(wf):
    c = ctx(wf, title="t")
    payload = {"decision": "approve", "optionId": "approve", "decidedBy": "u1", "comment": "ok",
               "decidedAt": "2026-10-17T10:00:00+00:00", "approvalId": "a1"}
    result = await HumanInLoopExecutor().finish(c, wf.db, fin(wakeReason="signal", signalPayload=payload))
    assert result.status == "success" and result.next_node_id == "approve"
    assert result.output["decision"] == "approve" and result.output["decided_by"] == "u1"
    assert result.output["comment"] == "ok" and result.output["output"] == "approve"


async def test_finish_honours_decision_routes(wf):
    c = ctx(wf, title="t", decisionRoutes={"approve": "node-ship"})
    payload = {"decision": "approve", "optionId": "approve"}
    result = await HumanInLoopExecutor().finish(c, wf.db, fin(wakeReason="signal", signalPayload=payload))
    assert result.next_node_id == "node-ship"


async def test_finish_rejects_a_signal_that_is_not_a_decision(wf):
    c = ctx(wf, title="t")
    result = await HumanInLoopExecutor().finish(c, wf.db, fin(wakeReason="signal", signalPayload={"x": 1}))
    assert result.status == "failed"


async def test_sla_expiry_fails_the_node_by_default_and_expires_the_row(wf):
    await park(wf, title="t", slaHours=1)
    row = (await rows(wf))[0]
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=5)
    await wf.db.commit()
    c = ctx(wf, title="t", slaHours=1)
    result = await HumanInLoopExecutor().finish(c, wf.db, fin(wakeReason="deadline"))
    assert result.status == "failed" and "SLA" in result.error
    assert (await rows(wf))[0].status == "expired"


async def test_sla_expiry_follows_the_escalation_decision(wf):
    c = ctx(wf, title="t", slaHours=1, escalationEnabled=True, escalationConfig={"onExpire": "reject"})
    result = await HumanInLoopExecutor().finish(c, wf.db, fin(wakeReason="deadline"))
    assert result.status == "success" and result.next_node_id == "reject"
    assert result.output["decision"] == "sla_expired" and result.output["decided_by"] == "system:sla"


async def test_sla_expiry_routes_through_decision_routes(wf):
    c = ctx(wf, title="t", slaHours=1, decisionRoutes={"sla_expired": "node-escalate"})
    result = await HumanInLoopExecutor().finish(c, wf.db, fin(wakeReason="deadline"))
    assert result.status == "success" and result.next_node_id == "node-escalate"


# ---- the real decision route, signal on the wire -----------------------------------------------

SETTINGS = Settings(
    _env_file=None, SECRET_KEY="Zk3vN8qLw1RtYb6HdPx9CfUe2MaS5JoG7iVn4TlKq0A",
    ENGINE_SERVICE_TOKEN="e" * 12 + "Engine-Svc-Token-0123456789-abcdXY", ENGINE_URL="http://engine.test",
    ENGINE_SESSION_ID="s1", ENABLE_ENGINE_API=True, ENABLE_ENGINE_CHAT=True)


class Wire:
    def __init__(self, wf):
        self.wf, self.requests = wf, []
        self.client = EngineClient(SETTINGS, transport=httpx.MockTransport(self.handle))

    def handle(self, req):
        self.requests.append(req)
        if req.url.path.endswith("/signal"):
            return httpx.Response(200, json={"delivered": True})
        return httpx.Response(404, json={})


@pytest.fixture
async def route(wf, monkeypatch):
    async def role(self, project_id, user_id, tenant_id):
        return "member"

    monkeypatch.setattr(ec.ProjectService, "__init__", lambda self, db: None)
    monkeypatch.setattr(ec.ProjectService, "get_user_role", role)
    monkeypatch.setattr(ec, "ApprovalService", lambda d: ApprovalService(d, audit=wf.audit))
    tenant = await wf.db.get(Tenant, TENANT)
    tenant.settings = {"features": {"ENABLE_ENGINE_CHAT": True}}
    await wf.db.commit()
    wire = Wire(wf)
    app = FastAPI()
    ec.mount_engine_conversations(app, SETTINGS)

    async def _db():
        yield wf.db

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_engine_client] = lambda: wire.client
    wf.as_user = lambda name: app.dependency_overrides.__setitem__(get_current_user, lambda: auth(wf.users[name]))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        wire.http = c
        yield wire


async def test_the_existing_decision_route_resumes_a_workflow_wait(wf, route):
    await park(wf, title="t")
    url = f"/api/v1/engine/conversations/{wf.wconv.conversation_id}/approvals/{NODE_TASK}/decision"
    wf.as_user("member2")
    denied = await route.http.post(url, json={"decision": "approve"})
    assert denied.status_code == 403, denied.text
    assert route.requests == []
    wf.as_user("admin")
    ok = await route.http.post(url, json={"decision": "approve", "note": "go"})
    assert ok.status_code == 200, ok.text
    (req,) = route.requests
    assert req.url.path == f"/v1/conversations/{wf.wconv.conversation_id}/tasks/{NODE_TASK}/signal"
    body = json.loads(req.content)
    assert body["key"] == "decision:n1" and body["payload"]["decision"] == "approve"
    again = await route.http.post(url, json={"decision": "deny"})
    assert again.status_code == 409 and len(route.requests) == 1


# ---- inbox and waiting-nodes -------------------------------------------------------------------

async def test_the_row_shows_in_the_inbox_with_a_decide_action(wf):
    from app.services.inbox_service import InboxService

    await park(wf, title="Approve release")
    items, _cursor = await InboxService(wf.db, approvals=wf.svc).list_for_user(auth(wf.users["admin"]))
    (item,) = items
    assert item["kind"] == "review" and item["title"] == "Approve release"
    (action,) = item["actions"]
    assert action["type"] == "decide"
    assert action["endpoint"] == (
        f"/api/v1/engine/conversations/{wf.wconv.conversation_id}/approvals/{NODE_TASK}/decision")


async def test_waiting_nodes_lists_parked_human_nodes_tenant_scoped(wf):
    from app.api.v1 import workflows as wfapi
    from app.models.workflow import WorkflowExecution

    await park(wf, title="t", slaHours=3)
    ex = WorkflowExecution(id=uuid.UUID(wf.run_id), tenant_id=TENANT, workflow_id=uuid.uuid4(),
                           user_id=wf.users["member"].id, status="running", input_data={})
    wf.db.add(ex)
    await wf.db.commit()
    out = await wfapi.list_waiting_nodes(uuid.UUID(wf.run_id), db=wf.db, user=auth(wf.users["member"]),
                                         tenant_id=TENANT)
    (node,) = out["nodes"]
    row = (await rows(wf))[0]
    assert node["nodeId"] == "n1" and node["approvalId"] == str(row.id) and node["wakeAt"]
    assert node["conversationId"] == str(wf.wconv.conversation_id) and node["taskId"] == NODE_TASK
    assert node["kind"] == "human_in_loop"
    with pytest.raises(Exception) as other_tenant:
        await wfapi.list_waiting_nodes(uuid.UUID(wf.run_id), db=wf.db, user=auth(wf.users["stranger"]),
                                       tenant_id=uuid.uuid4())
    assert other_tenant.value.status_code == 404
    with pytest.raises(Exception) as outsider:
        await wfapi.list_waiting_nodes(uuid.UUID(wf.run_id), db=wf.db, user=auth(wf.users["member2"]),
                                       tenant_id=TENANT)
    assert outsider.value.status_code == 403
    await wf.svc.mark_closed(TENANT, run_id=wf.run_id)
    done = await wfapi.list_waiting_nodes(uuid.UUID(wf.run_id), db=wf.db, user=auth(wf.users["admin"]),
                                          tenant_id=TENANT)
    assert done["nodes"] == []


def test_the_hook_is_registered_for_workflow_reviews():
    assert aps.get_decision_hook("review", "workflow") is wwd.signal_node
    assert aps.get_decision_hook("tool_call", "workflow") is None
