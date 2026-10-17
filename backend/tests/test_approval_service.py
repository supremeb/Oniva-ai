"""U2 - the approval ledger service (KTD3, KTD4, KTD5) on in-memory SQLite."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.auth.providers.base import AuthUser
from types import SimpleNamespace

from app.models.compliance_audit_log import AuditAction
from app.models.engine import EngineConversation
from app.models.tenant import Tenant
from app.models.tool_approval import ToolApproval
from app.models.user import User
from app.services.approval_service import ApprovalError, ApprovalService
from app.services.engine_client import EngineError

TENANT = uuid.uuid4()
OTHER_TENANT = uuid.uuid4()
PROJECT = uuid.uuid4()


class FakeAudit:
    """Records ``AuditChain.append`` calls (the chain's ARRAY columns need Postgres; U1 and the
    postgres tier test the real chain)."""

    def __init__(self):
        self.entries = []

    async def append(self, **kw):
        kw["action"] = getattr(kw["action"], "value", kw["action"])
        entry = SimpleNamespace(**kw)
        self.entries.append(entry)
        return entry


class FakeEngine:
    """Duck-typed EngineClient: scripted ``decide`` outcomes, recorded calls."""

    def __init__(self):
        self.decide_calls = []
        self.outcomes = []  # exceptions to raise, then success
        self.items = []

    async def decide(self, session_id, tenant_id, project_id, conversation_id, tool_task_id,
                     decision, note, principal):
        self.decide_calls.append({"task": tool_task_id, "decision": decision, "note": note,
                                  "principal": principal, "conversation": str(conversation_id)})
        if self.outcomes:
            out = self.outcomes.pop(0)
            if isinstance(out, Exception):
                raise out
            return out
        return {"approval": {"status": "approved" if decision == "approve" else "denied",
                             "decision": {"decision": decision, "principal": principal,
                                          "note": note, "decidedAt": 1}}}

    async def approvals(self, *a):
        return self.items


def auth(user: User, role=None) -> AuthUser:
    return AuthUser(id=str(user.id), email=user.email, name=user.name, tenant_id=str(user.tenant_id),
                    tenant_slug="t", role=role or user.role, auth_provider="local")


@pytest.fixture
async def world(test_db):
    db = test_db
    db.add(Tenant(id=TENANT, name="t", slug="t", settings={}))
    db.add(Tenant(id=OTHER_TENANT, name="o", slug="o", settings={}))
    users = {}
    for name, role, tenant in (("owner", "owner", TENANT), ("admin", "admin", TENANT),
                               ("admin2", "admin", TENANT), ("member", "member", TENANT),
                               ("member2", "member", TENANT), ("viewer", "viewer", TENANT),
                               ("stranger", "admin", OTHER_TENANT)):
        u = User(id=uuid.uuid4(), tenant_id=tenant, email=f"{name}@x.io", name=name, role=role, is_active=True)
        db.add(u)
        users[name] = u
    conv = EngineConversation(conversation_id=uuid.uuid4(), tenant_id=TENANT, project_id=PROJECT,
                              user_id=users["member"].id, session_id="s1")
    db.add(conv)
    await db.commit()

    class W:
        pass

    w = W()
    w.db, w.users, w.conv, w.engine = db, users, conv, FakeEngine()
    w.audit = FakeAudit()
    w.svc = ApprovalService(db, audit=w.audit)
    w.n = 0

    async def pending(requested_by="member", approver_role="member", task=None, **kw):
        w.n += 1
        return await w.svc.record_pending(
            tenant_id=TENANT, conversation_id=conv.conversation_id,
            tool_task_id=task or f"task_{w.n}", engine_conversation_id=str(conv.conversation_id),
            run_id=f"run_{w.n}", tool_call_id=f"call_{w.n}", tool_name="execute_python",
            arguments={"code": "print(1)"}, arguments_hash="h" * 64, reason="needs a human",
            approver_role=approver_role, requested_by=users[requested_by].id, project_id=PROJECT, **kw)

    async def decide(user, row, decision="approve", note=None, who_role=None):
        return await w.svc.decide(user=auth(users[user], who_role), conv=conv, row=row,
                                  decision=decision, note=note, engine=w.engine)

    async def audits(action):
        return [e for e in w.audit.entries if e.action == action.value]

    w.pending, w.decide, w.audits = pending, decide, audits
    return w


# --- can_decide matrix (KTD4) --------------------------------------------------

@pytest.mark.parametrize("who,row_role,requester,ok", [
    ("viewer", "member", "viewer", False),
    ("member", "member", "member", True),       # the requester, member-level tool
    ("member2", "member", "member", False),     # not the requester
    ("member", "admin", "member", False),       # admin-level row
    ("admin", "member", "member", True),
    ("admin", "admin", "member", True),
    ("owner", "admin", "member", True),
])
async def test_can_decide_matrix(world, who, row_role, requester, ok):
    row = await world.pending(requested_by=requester, approver_role=row_role)
    if ok:
        await world.svc.can_decide(auth(world.users[who]), row)
    else:
        with pytest.raises(ApprovalError) as ei:
            await world.svc.can_decide(auth(world.users[who]), row)
        assert (ei.value.status, ei.value.code) == (403, "not_authorized")


async def test_can_decide_other_tenant_is_404(world):
    row = await world.pending()
    with pytest.raises(ApprovalError) as ei:
        await world.svc.can_decide(auth(world.users["stranger"]), row)
    assert ei.value.status == 404


async def test_admin_row_self_approval_forbidden_with_two_admin_or_owner(world):
    row = await world.pending(requested_by="admin", approver_role="admin")
    with pytest.raises(ApprovalError) as ei:
        await world.svc.can_decide(auth(world.users["admin"]), row)
    assert (ei.value.status, ei.value.code) == (403, "self_approval_forbidden")


async def test_owner_counts_toward_the_two(world):
    world.users["admin2"].is_active = False
    await world.db.commit()
    row = await world.pending(requested_by="admin", approver_role="admin")
    with pytest.raises(ApprovalError) as ei:  # admin + owner = 2
        await world.svc.can_decide(auth(world.users["admin"]), row)
    assert ei.value.code == "self_approval_forbidden"


async def test_single_admin_or_owner_may_self_approve_and_audit_says_so(world):
    for name in ("admin2", "owner"):
        world.users[name].is_active = False
    await world.db.commit()
    row = await world.pending(requested_by="admin", approver_role="admin")
    await world.decide("admin", row)
    assert row.status == "approved" and row.self_approved is True
    (entry,) = await world.audits(AuditAction.APPROVAL_DECIDED)
    assert entry.extra_metadata["self_approved"] is True


async def test_require_separate_approver_forces_it_on_every_row(world):
    tenant = await world.db.get(Tenant, TENANT)
    tenant.settings = {"approvals": {"require_separate_approver": True}}
    await world.db.commit()
    row = await world.pending(requested_by="member", approver_role="member")
    with pytest.raises(ApprovalError) as ei:
        await world.svc.can_decide(auth(world.users["member"]), row)
    assert ei.value.code == "self_approval_forbidden"
    await world.svc.can_decide(auth(world.users["admin"]), row)  # someone else is fine


# --- record_pending -------------------------------------------------------------

async def test_record_pending_is_idempotent_on_root_and_task(world):
    a = await world.pending(task="t1")
    b = await world.svc.record_pending(
        tenant_id=TENANT, conversation_id=world.conv.conversation_id, tool_task_id="t1",
        engine_conversation_id="child-conv", run_id="other", tool_call_id="other")
    assert a.id == b.id
    rows = (await world.db.execute(select(ToolApproval))).scalars().all()
    assert len(rows) == 1


async def test_record_pending_is_idempotent_on_run_and_call(world):
    a = await world.pending()
    b = await world.svc.record_pending(
        tenant_id=TENANT, conversation_id=world.conv.conversation_id, tool_task_id="different",
        run_id=a.run_id, tool_call_id=a.tool_call_id)
    assert a.id == b.id


async def test_record_pending_scrubs_arguments(world):
    row = await world.svc.record_pending(
        tenant_id=TENANT, conversation_id=world.conv.conversation_id, tool_task_id="s",
        arguments={"query": "hi", "api_key": "abc", "nested": {"Password": "x", "ok": 1},
                   "cmd": "curl -H 'k: sk-ant-api03-" + "A" * 40 + "'"})
    p = row.arguments_preview
    assert p["query"] == "hi" and "api_key" not in p and p["nested"] == {"ok": 1}
    assert "sk-ant-api03" not in p["cmd"]


# --- decide (KTD5) -------------------------------------------------------------

async def test_decide_approve_goes_pending_deciding_approved_with_one_audit(world):
    row = await world.pending()
    statuses = []
    real = world.engine.decide

    async def spy(*a, **k):
        statuses.append(row.status)  # the row is `deciding` while the engine is called
        return await real(*a, **k)

    world.engine.decide = spy
    result = await world.decide("member", row, "approve", note="ok")
    assert statuses == ["deciding"]
    assert row.status == "approved" and row.decision == "approve"
    assert row.decided_by == world.users["member"].id and row.note == "ok"
    assert len(world.engine.decide_calls) == 1
    assert world.engine.decide_calls[0]["principal"] == str(world.users["member"].id)
    assert result["approval"]["status"] == "approved"
    (entry,) = await world.audits(AuditAction.APPROVAL_DECIDED)
    assert entry.extra_metadata["actor_id"] == str(world.users["member"].id)
    assert entry.extra_metadata["actor_role"] == "member"
    assert entry.extra_metadata["tool_name"] == "execute_python"
    assert entry.extra_metadata["self_approved"] is True


async def test_denied_decision_sets_denied(world):
    row = await world.pending()
    await world.decide("admin", row, "deny")
    assert row.status == "denied" and row.decision == "deny" and row.self_approved is False


def _rejected(reason, item=None):
    body = {"error": "approval_rejected", "reason": reason}
    if item is not None:
        body["approval"] = item
    return EngineError(409, "approval_rejected", body)


async def test_already_decided_by_someone_else_records_engine_principal_and_decision(world):
    row = await world.pending()
    item = {"status": "denied", "toolTaskId": row.tool_task_id,
            "decision": {"decision": "deny", "principal": str(world.users["admin"].id),
                         "note": "no", "decidedAt": 5}}
    world.engine.outcomes = [_rejected("already_decided", item)]
    with pytest.raises(ApprovalError) as ei:
        await world.decide("member", row, "approve")
    assert (ei.value.status, ei.value.code) == (409, "already_decided")
    assert row.status == "denied" and row.decision == "deny"
    assert row.decided_by == world.users["admin"].id
    (entry,) = await world.audits(AuditAction.APPROVAL_DECIDED)
    assert entry.extra_metadata["actor_id"] == str(world.users["admin"].id)
    assert entry.extra_metadata["actor_role"] == "admin"
    assert entry.decision == "deny"


async def test_already_decided_by_the_caller_with_same_decision_is_200(world):
    row = await world.pending()
    item = {"status": "approved", "toolTaskId": row.tool_task_id,
            "decision": {"decision": "approve", "principal": str(world.users["member"].id),
                         "note": None, "decidedAt": 5}}
    world.engine.outcomes = [_rejected("already_decided", item)]
    result = await world.decide("member", row, "approve")
    assert result == {"approval": item}
    assert row.status == "approved" and row.decided_by == world.users["member"].id
    assert len(await world.audits(AuditAction.APPROVAL_DECIDED)) == 1


async def test_already_decided_without_snapshot_reads_the_engine_document(world):
    row = await world.pending()
    world.engine.items = [{"status": "approved", "toolTaskId": row.tool_task_id,
                           "decision": {"decision": "approve", "principal": str(world.users["admin"].id),
                                        "note": None, "decidedAt": 5}}]
    world.engine.outcomes = [_rejected("already_decided")]
    with pytest.raises(ApprovalError):
        await world.decide("member", row, "deny")
    assert row.status == "approved" and row.decided_by == world.users["admin"].id


async def test_engine_expired_rejection_sets_row_expired_410(world):
    row = await world.pending()
    world.engine.outcomes = [_rejected("expired")]
    with pytest.raises(ApprovalError) as ei:
        await world.decide("member", row)
    assert ei.value.status == 410 and row.status == "expired"
    assert len(await world.audits(AuditAction.APPROVAL_EXPIRED)) == 1
    assert not await world.audits(AuditAction.APPROVAL_DECIDED)


async def test_engine_not_waiting_rejection_sets_row_cancelled_409(world):
    row = await world.pending()
    world.engine.outcomes = [_rejected("not_waiting")]
    with pytest.raises(ApprovalError) as ei:
        await world.decide("member", row)
    assert (ei.value.status, ei.value.code) == (409, "not_waiting") and row.status == "cancelled"


@pytest.mark.parametrize("err", [EngineError(502, "engine_unreachable"), EngineError(504, "engine_timeout"),
                                 EngineError(409, "not_owner")])
async def test_non_definitive_engine_outcome_leaves_deciding_and_only_decider_retries(world, err):
    row = await world.pending()
    world.engine.outcomes = [err]
    with pytest.raises(EngineError):
        await world.decide("member", row, "approve", note="n")
    assert row.status == "deciding" and not await world.audits(AuditAction.APPROVAL_DECIDED)

    with pytest.raises(ApprovalError) as ei:  # even an admin cannot take it over
        await world.decide("admin", row, "deny")
    assert (ei.value.status, ei.value.code) == (409, "decision_in_progress")
    assert len(world.engine.decide_calls) == 1

    await world.decide("member", row, "deny")  # retry repeats the same inputs, not the new ones
    assert world.engine.decide_calls[1]["decision"] == "approve"
    assert world.engine.decide_calls[1]["note"] == "n"
    assert row.status == "approved" and len(await world.audits(AuditAction.APPROVAL_DECIDED)) == 1


async def test_second_decider_after_claim_gets_409(world):
    row = await world.pending()
    other = await world.db.get(ToolApproval, row.id)
    assert await world.svc._claim(row, world.users["admin"].id, "approve", None, False)
    with pytest.raises(ApprovalError) as ei:
        await world.svc.decide(user=auth(world.users["owner"]), conv=world.conv, row=other,
                               decision="deny", note=None, engine=world.engine)
    assert ei.value.status == 409 and not world.engine.decide_calls


async def test_decide_refused_writes_nothing_and_never_calls_engine(world):
    row = await world.pending()
    with pytest.raises(ApprovalError):
        await world.decide("viewer", row)
    assert row.status == "pending" and not world.engine.decide_calls
    assert not await world.audits(AuditAction.APPROVAL_DECIDED)


# --- expiry and closing ---------------------------------------------------------

async def test_expire_overdue_marks_pending_rows_and_audits(world):
    now = datetime.now(timezone.utc)
    late = await world.pending(expires_at=now - timedelta(minutes=1))
    fresh = await world.pending(expires_at=now + timedelta(hours=1))
    no_ttl = await world.pending()
    assert await world.svc.expire_overdue(TENANT) == 1
    assert (late.status, fresh.status, no_ttl.status) == ("expired", "pending", "pending")
    (entry,) = await world.audits(AuditAction.APPROVAL_EXPIRED)
    assert entry.extra_metadata["approval_id"] == str(late.id)


async def test_mark_closed_by_conversation_and_by_run(world):
    a, b, c = await world.pending(), await world.pending(), await world.pending()
    decided = await world.pending()
    await world.decide("admin", decided)
    b.status = "deciding"
    await world.db.commit()
    assert await world.svc.mark_closed(TENANT, run_id=a.run_id, reason="run failed") == 1
    assert a.status == "cancelled" and b.status == "deciding"
    assert await world.svc.mark_closed(TENANT, conversation_id=world.conv.conversation_id) == 2
    assert (b.status, c.status, decided.status) == ("cancelled", "cancelled", "approved")
    assert len(await world.audits(AuditAction.APPROVAL_CANCELLED)) == 3


async def test_finalize_from_engine_never_writes_a_second_entry(world):
    row = await world.pending()
    item = {"status": "approved", "decision": {"decision": "approve", "principal": str(world.users["admin"].id),
                                                "note": None, "decidedAt": 5}}
    await world.svc.finalize_from_engine(row, item)
    await world.svc.finalize_from_engine(row, item)
    await world.db.commit()
    assert len(await world.audits(AuditAction.APPROVAL_DECIDED)) == 1
