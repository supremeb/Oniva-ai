"""U9 / R17 / AE7: GET /engine/conversations/{id}/cost sums the usage ledger by run."""
import uuid
from datetime import datetime, timedelta, timezone

from app.models.llm_usage import LLMUsageRecord
from app.models.tenant import Tenant
from app.services.usage_summary import conversation_cost
from tests.internal.test_engine_conversations import TENANT_A, TENANT_B, env  # noqa: F401


async def _tenant(db) -> uuid.UUID:
    t = Tenant(id=uuid.uuid4(), name="T", slug=f"t-{uuid.uuid4().hex[:8]}", is_active=True, settings={})
    db.add(t)
    await db.commit()
    return t.id


def _row(tenant, conv, run, rid, cost, tin, tout, at, status="success"):
    return LLMUsageRecord(
        tenant_id=tenant, provider_name="anthropic", model="m", input_tokens=tin, output_tokens=tout,
        cost_usd=cost, latency_ms=1, request_id=rid, run_id=run, conversation_id=conv, status=status,
        created_at=at)


async def test_two_runs_sum_and_total_equals_rows(test_db):
    tenant, conv = await _tenant(test_db), uuid.uuid4()
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    test_db.add_all([
        _row(tenant, conv, "r2", "r2:0:generation", "0.002", 30, 5, t0 + timedelta(minutes=5)),
        _row(tenant, conv, "r1", "r1:0:generation", "0.010", 100, 20, t0),
        _row(tenant, conv, "r1", "r1:1:generation", "0.005", 50, 10, t0 + timedelta(seconds=3)),
        _row(tenant, uuid.uuid4(), "rx", "rx:0:generation", "9", 1, 1, t0),  # other conversation
    ])
    await test_db.commit()
    out = await conversation_cost(test_db, tenant, conv)
    assert [r["runId"] for r in out["runs"]] == ["r1", "r2"]
    assert out["runs"][0] == {**out["runs"][0], "usd": 0.015, "inputTokens": 150, "outputTokens": 30,
                              "unpriced": False}
    assert out["runs"][1]["usd"] == 0.002
    assert out["totalUsd"] == 0.017 and out["inputTokens"] == 180 and out["outputTokens"] == 35
    assert out["unpriced"] is False


async def test_unpriced_row_flags_run_and_adds_no_dollars(test_db):
    tenant, conv = await _tenant(test_db), uuid.uuid4()
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    test_db.add_all([
        _row(tenant, conv, "r1", "r1:0:generation", "0.010", 100, 20, t0),
        _row(tenant, conv, "r1", "r1:1:generation", "0", 40, 4, t0 + timedelta(seconds=1), status="unpriced"),
    ])
    await test_db.commit()
    out = await conversation_cost(test_db, tenant, conv)
    assert out["runs"][0]["unpriced"] is True and out["runs"][0]["usd"] == 0.01
    assert out["runs"][0]["inputTokens"] == 140 and out["unpriced"] is True


async def test_other_tenant_rows_not_counted(test_db):
    ta, tb, conv = await _tenant(test_db), await _tenant(test_db), uuid.uuid4()
    test_db.add(_row(ta, conv, "r1", "r1:0:generation", "1", 1, 1, datetime(2026, 1, 1, tzinfo=timezone.utc)))
    await test_db.commit()
    assert (await conversation_cost(test_db, tb, conv))["runs"] == []


def test_route_returns_summary_for_bound_conversation(env, monkeypatch):  # noqa: F811
    seen = {}

    async def fake(db, tenant_id, conversation_id):
        seen["args"] = (str(tenant_id), str(conversation_id))
        return {"totalUsd": 0.5, "inputTokens": 1, "outputTokens": 2, "unpriced": False, "runs": []}

    from app.api.v1 import engine_conversations as ec
    monkeypatch.setattr(ec, "conversation_cost", fake)
    cid = env.seed()
    r = env.http.get(f"/api/v1/engine/conversations/{cid}/cost")
    assert r.status_code == 200 and r.json()["totalUsd"] == 0.5
    assert seen["args"] == (TENANT_A, cid)
    assert env.calls == []  # ledger only, the engine is not consulted


def test_route_404_for_other_tenant_and_403_for_non_owner(env):  # noqa: F811
    other = env.seed(tenant=TENANT_B)
    assert env.http.get(f"/api/v1/engine/conversations/{other}/cost").status_code == 404
    not_mine = env.seed(owner=str(uuid.uuid4()))
    assert env.http.get(f"/api/v1/engine/conversations/{not_mine}/cost").status_code == 403
