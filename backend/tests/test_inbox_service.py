"""U7 - the inbox read model (KTD8): every adapter, visibility, ordering, paging, count parity."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.background_job import BackgroundJob
from app.models.compliance_execution import ComplianceExecution
from app.models.deep_work_run import DeepWorkRun
from app.models.project import Project
from app.services import approval_service as aps
from app.services.inbox_service import InboxService, InvalidInboxQuery, decode_cursor, parse_kinds
from tests.test_approval_service import TENANT, OTHER_TENANT, auth, world  # noqa: F401  (fixtures)

NOW = lambda: datetime.now(timezone.utc)  # noqa: E731


@pytest.fixture(autouse=True)
def _throttle():
    aps.reset_reconcile_throttle()


def svc(w):
    return InboxService(w.db, approvals=w.svc)


async def items_for(w, who, **kw):
    out, nxt = await svc(w).list_for_user(auth(w.users[who]), **kw)
    return out, nxt


async def add_extras(w):
    project = Project(id=uuid.uuid4(), tenant_id=TENANT, name="p", owner_id=w.users["member"].id)
    w.db.add(project)
    await w.db.commit()
    w.db.add(ComplianceExecution(
        id=uuid.uuid4(), tenant_id=TENANT, user_id=w.users["member"].id, template_id="kyc", template_version="1",
        template_name="KYC", subject_id="c1", subject_type="customer", subject_name="Acme", status="manual_review",
        risk_level="high", results={}, execution_trace=[]))
    w.db.add(BackgroundJob(id=uuid.uuid4(), tenant_id=TENANT, user_id=w.users["member"].id,
                           job_type="background_agent", status="failed", error="boom", completed_at=NOW()))
    w.db.add(DeepWorkRun(id=uuid.uuid4(), tenant_id=TENANT, project_id=project.id, user_id=w.users["member"].id,
                         conversation_id=w.conv.conversation_id, query="q", status="running",
                         awaiting_input={"action": "ask", "questions": [{"text": "Region?"}]}))
    await w.db.commit()


async def test_each_source_on_its_own_yields_one_correctly_shaped_item(world):
    await add_extras(world)
    row = await world.pending(requested_by="member")
    out, _ = await items_for(world, "admin")
    by = {i["kind"]: i for i in out}
    assert set(by) == {"approval", "compliance", "fleet"} and len(out) == 3  # deep work is the owner's own
    mine, _ = await items_for(world, "member", kinds=["deep_work"])
    by["deep_work"] = mine[0]
    assert len(mine) == 1
    a = by["approval"]
    assert a["source"] == {"type": "tool_approval", "id": str(row.id)}
    assert a["severity"] in ("medium", "high") and a["summary"] == "needs a human"
    assert a["actions"][0]["type"] == "decide" and a["actions"][0]["requiresRole"] == "member"
    assert a["actions"][0]["endpoint"].endswith(f"/approvals/{row.tool_task_id}/decision")
    assert by["compliance"]["severity"] == "high" and by["compliance"]["href"] == "/compliance"
    assert by["fleet"]["summary"] == "boom"
    assert by["deep_work"]["summary"] == "Region?"
    for i in out:
        assert {"id", "kind", "title", "summary", "severity", "createdAt", "source", "actions", "href"} <= set(i)
        assert not any(k.startswith("_") for k in i)


async def test_review_and_question_ledger_kinds(world):
    await world.pending(requested_by="member", kind="review")
    await world.pending(requested_by="member", kind="question")
    out, _ = await items_for(world, "member")
    assert sorted(i["kind"] for i in out) == ["question", "review"]


async def test_mixed_sources_ordered_by_due_then_recency(world):
    await add_extras(world)
    soon = await world.pending(expires_at=NOW() + timedelta(hours=1))
    later = await world.pending(expires_at=NOW() + timedelta(hours=5))
    out, _ = await items_for(world, "admin")
    ids = [i["source"]["id"] for i in out]
    assert ids[:2] == [str(soon.id), str(later.id)]
    undated = out[2:]
    assert all(i["dueAt"] is None for i in undated) and len(undated) == 2  # compliance and fleet (deep work is the member's own)
    created = [i["createdAt"] for i in undated]
    assert created == sorted(created, reverse=True)


async def test_visibility_member_own_admin_tenant_other_tenant_none(world):
    mine = await world.pending(requested_by="member")
    theirs = await world.pending(requested_by="member2")
    await add_extras(world)
    member, _ = await items_for(world, "member")
    assert {i["source"]["id"] for i in member if i["kind"] == "approval"} == {str(mine.id)}
    assert {i["kind"] for i in member} == {"approval", "fleet", "deep_work"}  # no compliance for a member
    for who in ("admin", "owner"):
        out, _ = await items_for(world, who)
        assert {str(mine.id), str(theirs.id)} <= {i["source"]["id"] for i in out}
        assert "compliance" in {i["kind"] for i in out}
    out, _ = await items_for(world, "stranger")
    assert out == []
    assert (await svc(world).count_for_user(auth(world.users["stranger"])))["total"] == 0


async def test_kinds_filter_and_parse(world):
    await add_extras(world)
    await world.pending()
    out, _ = await items_for(world, "admin", kinds=["approval"])
    assert {i["kind"] for i in out} == {"approval"}
    out, _ = await items_for(world, "admin", kinds=["fleet", "compliance"])
    assert {i["kind"] for i in out} == {"fleet", "compliance"}
    assert parse_kinds("approval, fleet") == ["approval", "fleet"] and parse_kinds(None) is None
    with pytest.raises(InvalidInboxQuery):
        parse_kinds("nope")
    with pytest.raises(InvalidInboxQuery):
        decode_cursor("garbage")


async def test_cursor_pagination_returns_each_item_once(world):
    rows = [await world.pending(expires_at=NOW() + timedelta(hours=i + 1)) for i in range(3)]
    for _ in range(4):
        await world.pending()
    seen, cursor = [], None
    while True:
        page, cursor = await items_for(world, "admin", limit=3, cursor=cursor)
        seen += [i["id"] for i in page]
        if cursor is None:
            break
    assert len(seen) == 7 and len(set(seen)) == 7
    assert seen[:3] == [f"approval:{r.id}" for r in rows]


async def test_expired_and_decided_rows_absent_and_overdue_is_swept(world):
    decided = await world.pending()
    decided.status = "approved"
    overdue = await world.pending(expires_at=NOW() - timedelta(minutes=1))
    live = await world.pending()
    await world.db.commit()
    out, _ = await items_for(world, "admin")
    assert [i["source"]["id"] for i in out] == [str(live.id)]
    await world.db.refresh(overdue)
    assert overdue.status == "expired"  # swept by the read, not just hidden


async def test_old_failed_fleet_job_is_absent(world):
    world.db.add(BackgroundJob(id=uuid.uuid4(), tenant_id=TENANT, user_id=world.users["member"].id,
                               job_type="background_agent", status="failed", completed_at=NOW() - timedelta(hours=80)))
    world.db.add(BackgroundJob(id=uuid.uuid4(), tenant_id=TENANT, user_id=world.users["member"].id,
                               job_type="background_agent", status="completed", completed_at=NOW()))
    await world.db.commit()
    out, _ = await items_for(world, "admin")
    assert out == []


async def test_deep_work_awaiting_input_appears_until_cleared_or_terminal(world):
    from app.services import deep_work_state as dws
    await add_extras(world)
    run = (await world.db.execute(__import__("sqlalchemy").select(DeepWorkRun))).scalars().one()
    out, _ = await items_for(world, "member", kinds=["deep_work"])
    assert len(out) == 1
    run.awaiting_input = None
    await world.db.commit()
    out, _ = await items_for(world, "member", kinds=["deep_work"])
    assert out == []
    run.awaiting_input, run.status = {"action": "ask"}, "cancelled"
    await world.db.commit()
    out, _ = await items_for(world, "member", kinds=["deep_work"])
    assert out == [] and dws  # a terminal run never lists


async def test_viewer_sees_own_items_without_a_decide_action(world):
    await world.pending(requested_by="viewer")
    out, _ = await items_for(world, "viewer")
    assert len(out) == 1 and not [a for a in out[0]["actions"] if a["type"] == "decide"]


async def test_self_approval_forbidden_row_greys_the_action_instead_of_hiding_it(world):
    # two admins and an admin-level row the first admin requested: they may decide in principle, not this one
    await world.pending(requested_by="admin", approver_role="admin")
    out, _ = await items_for(world, "admin")
    act = out[0]["actions"][0]
    assert act["type"] == "decide" and act["allowed"] is False and act["disabledReason"] == "self_approval_forbidden"
    out, _ = await items_for(world, "admin2")
    assert out[0]["actions"][0]["allowed"] is True and out[0]["actions"][0]["requiresRole"] == "admin"


async def test_count_agrees_with_list(world):
    await add_extras(world)
    await world.pending()
    await world.pending(kind="review")
    await world.pending(expires_at=NOW() - timedelta(minutes=1))
    for who in ("admin", "member", "viewer", "owner"):
        out, _ = await items_for(world, who)
        count = await svc(world).count_for_user(auth(world.users[who]))
        assert count["total"] == len(out), who
        for kind, n in count["byKind"].items():
            assert n == len([i for i in out if i["kind"] == kind]), (who, kind)
