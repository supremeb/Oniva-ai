"""U6 / R4: review tickets are retired; reviews are ledger rows (legacy decision path removed by U15)."""
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models.compliance_audit_log import HumanReviewTicket
from app.models.tenant import Tenant
from app.models.tool_approval import ToolApproval
from app.models.user import User
from app.services import output_gating

TENANT = uuid.uuid4()
REVIEWER = uuid.uuid4()


@pytest.fixture
async def db(test_db):
    test_db.add(Tenant(id=TENANT, name="t", slug="t", settings={}))
    test_db.add(User(id=REVIEWER, tenant_id=TENANT, email="r@x.io", name="r", role="admin",
                     is_active=True))
    await test_db.commit()
    return test_db


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
