"""U6 / R4, AE7: review tickets are retired; reviews are ledger rows; legacy tickets close their copy."""
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.compliance_audit_log import HumanReviewTicket
from app.models.tenant import Tenant
from app.models.tool_approval import ToolApproval
from app.models.user import User
from app.services import output_gating
from app.services.review_queue import (
    ReviewDecisionInput,
    ReviewQueueService,
    ReviewTicketCreate,
)

TENANT = uuid.uuid4()
REVIEWER = uuid.uuid4()


@pytest.fixture
async def db(test_db):
    test_db.add(Tenant(id=TENANT, name="t", slug="t", settings={}))
    test_db.add(User(id=REVIEWER, tenant_id=TENANT, email="r@x.io", name="r", role="admin",
                     is_active=True))
    await test_db.commit()
    return test_db


async def test_create_ticket_is_retired(db):
    with pytest.raises(HTTPException) as e:
        await ReviewQueueService(db).create_ticket(
            tenant_id=str(TENANT),
            data=ReviewTicketCreate(title="t", description="d", risk_level="high",
                                    review_type="code_execution", agent_id="a"))
    assert e.value.status_code == 410 and e.value.detail == "review_tickets_retired"
    assert (await db.execute(select(HumanReviewTicket))).scalars().all() == []


async def _legacy_ticket_with_copy(db, status="pending"):
    ticket = HumanReviewTicket(
        id=uuid.uuid4(), tenant_id=TENANT, agent_id="a", title="T", description="D", risk_level="high",
        priority="normal", review_type="output", review_data={}, status=status)
    db.add(ticket)
    await db.flush()
    copy = ToolApproval(id=uuid.uuid4(), tenant_id=TENANT, kind="review", status="pending",
                        legacy_ticket_id=ticket.id, approver_role="admin")
    db.add(copy)
    await db.commit()
    return ticket, copy


@pytest.mark.parametrize("decision,status,ledger_decision", [
    ("approved", "approved", "approve"), ("rejected", "denied", "deny")])
async def test_deciding_a_legacy_ticket_closes_its_ledger_copy(db, decision, status, ledger_decision):
    ticket, copy = await _legacy_ticket_with_copy(db)
    reviewer = str(REVIEWER)
    await ReviewQueueService(db).submit_decision(
        str(ticket.id), str(TENANT), reviewer, ReviewDecisionInput(decision=decision, notes="ok"))
    await db.refresh(copy)
    assert (copy.status, copy.decision, str(copy.decided_by), copy.note) == (
        status, ledger_decision, reviewer, "ok")
    assert copy.decided_at is not None


async def test_escalating_leaves_the_ledger_copy_open(db):
    ticket, copy = await _legacy_ticket_with_copy(db)
    await ReviewQueueService(db).submit_decision(
        str(ticket.id), str(TENANT), str(REVIEWER), ReviewDecisionInput(decision="escalated"))
    await db.refresh(copy)
    assert copy.status == "pending"


async def test_output_gating_failure_creates_review_row_and_no_ticket(db):
    await output_gating._route_to_review(
        uuid.uuid4(), uuid.uuid4(), "Agent X", "some long result", 40, 80, db, TENANT)
    rows = (await db.execute(select(ToolApproval))).scalars().all()
    assert len(rows) == 1
    assert rows[0].kind == "review" and rows[0].status == "pending" and rows[0].tenant_id == TENANT
    assert "40/100" in rows[0].reason
    assert rows[0].arguments_preview["score"] == 40
    assert (await db.execute(select(HumanReviewTicket))).scalars().all() == []


async def test_wide_research_batch_creates_review_row_and_no_ticket(db, monkeypatch):
    from app.api.v1 import wide_research

    class StopHere(Exception):
        pass

    class Orchestrator:
        def __init__(self, *a, **k):
            pass

        async def execute(self, **k):
            raise StopHere

    monkeypatch.setattr(wide_research, "WideResearchOrchestrator", Orchestrator)
    request = SimpleNamespace(items=[{"i": n} for n in range(wide_research._APPROVAL_THRESHOLD + 1)],
                              task_template="research {item}", agent_id="agent-1", session_id=None,
                              max_parallel=2, timeout_per_item=30)
    with pytest.raises(StopHere):
        await wide_research.execute_wide_research(request, db=db, tenant_id=TENANT, user_id=REVIEWER)
    rows = (await db.execute(select(ToolApproval))).scalars().all()
    assert len(rows) == 1 and rows[0].kind == "review"
    assert rows[0].requested_by == REVIEWER
    assert (await db.execute(select(HumanReviewTicket))).scalars().all() == []


def test_sandbox_execute_routes_are_gone():
    from app.main import app

    paths = app.openapi()["paths"]
    assert "/api/v1/sandbox/execute" not in paths
    assert "/api/v1/sandbox/{execution_id}" not in paths
    assert not any(p.startswith("/api/v1/sandbox/") for p in paths)
