"""U7 - orphan reconciliation (KTD11): ledger rows never outlive their engine item (AE4)."""
from datetime import datetime, timedelta, timezone

import pytest

from app.models.compliance_audit_log import AuditAction
from app.models.engine import EngineConversation
from app.services import approval_service as aps
from app.services.engine_client import EngineError
from tests.test_approval_service import FakeAudit, FakeEngine, TENANT, auth, world  # noqa: F401  (fixtures)


@pytest.fixture(autouse=True)
def _throttle():
    aps.reset_reconcile_throttle()
    yield
    aps.reset_reconcile_throttle()


class ReadEngine(FakeEngine):
    def __init__(self):
        super().__init__()
        self.reads = 0
        self.error = None

    async def approvals(self, *a):
        self.reads += 1
        if self.error:
            raise self.error
        return self.items


def item(row, status, principal=None, decision=None):
    out = {"toolTaskId": row.tool_task_id, "status": status}
    if principal:
        out["decision"] = {"decision": decision, "principal": str(principal), "note": "ok", "decidedAt": 1000}
    return out


async def age(w, row, seconds):
    row.created_at = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    row.updated_at = row.created_at
    await w.db.commit()


@pytest.mark.parametrize("status,expected,action", [
    ("expired", "expired", AuditAction.APPROVAL_EXPIRED),
    ("cancelled", "cancelled", AuditAction.APPROVAL_CANCELLED),
])
async def test_engine_item_expired_or_cancelled_closes_the_row(world, status, expected, action):
    eng = ReadEngine()
    row = await world.pending()
    eng.items = [item(row, status)]
    assert await world.svc.reconcile(TENANT, eng) == 1
    await world.db.refresh(row)
    assert row.status == expected
    assert len(await world.audits(action)) == 1


async def test_ttl_zero_row_is_closed_by_reconcile_not_by_the_time_sweep(world):
    eng = ReadEngine()
    row = await world.pending()
    assert row.expires_at is None
    eng.items = [item(row, "cancelled")]
    assert await world.svc.expire_overdue(TENANT) == 0
    await world.db.refresh(row)
    assert row.status == "pending"
    await world.svc.reconcile(TENANT, eng)
    await world.db.refresh(row)
    assert row.status == "cancelled"


async def test_missing_item_is_closed_after_the_grace_window_only(world):
    eng = ReadEngine()
    old, young = await world.pending(), await world.pending()
    await age(world, old, 300)
    await age(world, young, 30)
    eng.items = []
    await world.svc.reconcile(TENANT, eng)
    await world.db.refresh(old)
    await world.db.refresh(young)
    assert old.status == "cancelled" and young.status == "pending"


async def test_conversation_gone_404_closes_rows(world):
    eng = ReadEngine()
    eng.error = EngineError(404, "conversation_not_found")
    row = await world.pending()
    await world.svc.reconcile(TENANT, eng)
    await world.db.refresh(row)
    assert row.status == "cancelled"


async def test_conversation_row_missing_closes_rows(world):
    eng = ReadEngine()
    row = await world.pending()
    await world.db.delete(await world.db.get(EngineConversation, world.conv.conversation_id))
    await world.db.commit()
    await world.svc.reconcile(TENANT, eng)
    await world.db.refresh(row)
    assert row.status == "cancelled" and eng.reads == 0


@pytest.mark.parametrize("start", ["pending", "deciding"])
@pytest.mark.parametrize("engine_status,row_status,dec", [("approved", "approved", "approve"), ("denied", "denied", "deny")])
async def test_decided_engine_item_finalises_with_the_engine_principal(world, start, engine_status, row_status, dec):
    eng = ReadEngine()
    row = await world.pending(requested_by="member")
    if start == "deciding":
        row.status, row.decided_by, row.decision = "deciding", world.users["member"].id, "deny"
        await world.db.commit()
    eng.items = [item(row, engine_status, principal=world.users["admin"].id, decision=dec)]
    await world.svc.reconcile(TENANT, eng)
    await world.db.refresh(row)
    assert row.status == row_status and row.decision == dec
    assert row.decided_by == world.users["admin"].id  # never the stale claimant
    entries = await world.audits(AuditAction.APPROVAL_DECIDED)
    assert len(entries) == 1 and entries[0].extra_metadata["actor_id"] == str(world.users["admin"].id)


async def test_engine_unreachable_changes_nothing(world):
    eng = ReadEngine()
    eng.error = EngineError(502, "engine_unreachable")
    row = await world.pending()
    await age(world, row, 3600)
    assert await world.svc.reconcile(TENANT, eng) == 0
    await world.db.refresh(row)
    assert row.status == "pending" and world.audit.entries == []


async def test_pending_item_leaves_the_row_alone(world):
    eng = ReadEngine()
    row = await world.pending()
    await age(world, row, 3600)
    eng.items = [item(row, "pending")]
    assert await world.svc.reconcile(TENANT, eng) == 0
    await world.db.refresh(row)
    assert row.status == "pending"


async def test_rate_limit_one_engine_pass_per_30_seconds(world):
    eng = ReadEngine()
    await world.pending()
    now = datetime.now(timezone.utc)
    await world.svc.reconcile(TENANT, eng, now=now)
    await world.svc.reconcile(TENANT, eng, now=now + timedelta(seconds=10))
    assert eng.reads == 1
    await world.svc.reconcile(TENANT, eng, now=now + timedelta(seconds=31))
    assert eng.reads == 2


async def test_pass_reads_at_most_20_conversations(world):
    eng = ReadEngine()
    for _ in range(22):
        conv = EngineConversation(conversation_id=__import__("uuid").uuid4(), tenant_id=TENANT,
                                  project_id=world.conv.project_id, user_id=world.users["member"].id, session_id="s1")
        world.db.add(conv)
        await world.db.commit()
        await world.svc.record_pending(tenant_id=TENANT, conversation_id=conv.conversation_id,
                                       tool_task_id="t", requested_by=world.users["member"].id)
    await world.db.commit()
    await world.svc.reconcile(TENANT, eng)
    assert eng.reads == 20


async def test_stale_deciding_row_is_settled_by_the_sweep_but_a_fresh_one_is_not(world):
    eng = ReadEngine()
    stale, fresh = await world.pending(), await world.pending()
    for r, secs in ((stale, 120), (fresh, 5)):
        r.status, r.decided_by, r.decision = "deciding", world.users["admin"].id, "approve"
        r.updated_at = datetime.now(timezone.utc) - timedelta(seconds=secs)
    await world.db.commit()
    eng.items = [item(stale, "approved", world.users["admin"].id, "approve"),
                 item(fresh, "approved", world.users["admin"].id, "approve")]
    await world.svc.expire_overdue(TENANT, engine=eng)
    await world.db.refresh(stale)
    await world.db.refresh(fresh)
    assert stale.status == "approved" and fresh.status == "deciding"


async def test_overdue_pending_row_is_swept_to_expired(world):
    row = await world.pending(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    assert await world.svc.expire_overdue(TENANT) == 1
    await world.db.refresh(row)
    assert row.status == "expired"
    assert len(await world.audits(AuditAction.APPROVAL_EXPIRED)) == 1
