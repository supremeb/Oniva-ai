"""U7 / R13 / AE6: POST /internal/models/usage prices, dedupes and counts quota once."""
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select

from app.config import Settings
from app.config.llm_pricing import LLM_PRICING
from app.core.database import get_system_db
from app.internal_api.router import mount_internal_router
from app.models.llm_provider import LLMProvider
from app.models.llm_usage import LLMUsageRecord
from app.models.project import Project
from app.models.tenant import Tenant
from app.models.usage_quota import UsageQuota
from app.models.user import User

GATEWAY = "g" * 12 + "Gateway-Token-0123456789-abcdefXYZ"
ENGINE_SVC = "e" * 12 + "Engine-Svc-Token-0123456789-abcdXY"
SECRET = "Zk3vN8qLw1RtYb6HdPx9CfUe2MaS5JoG7iVn4TlKq0A"
AUTH = {"Authorization": f"Bearer {GATEWAY}"}
MODEL = "claude-sonnet-4-5"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr("app.core.database.set_tenant_context", AsyncMock())
    monkeypatch.setattr(LLMProvider.__table__.c.models, "default", None)


@pytest.fixture
async def client(test_db):
    app = FastAPI()
    mount_internal_router(
        app, Settings(_env_file=None, SECRET_KEY=SECRET, ENGINE_GATEWAY_TOKEN=GATEWAY, ENGINE_SERVICE_TOKEN=ENGINE_SVC)
    )

    async def _db():
        yield test_db

    app.dependency_overrides[get_system_db] = _db
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def make_tenant(db) -> uuid.UUID:
    t = Tenant(id=uuid.uuid4(), name="T", slug=f"t-{uuid.uuid4().hex[:8]}", is_active=True, settings={})
    db.add(t)
    await db.commit()
    return t.id


async def add_quota(db, tenant, used=0):
    db.add(UsageQuota(
        tenant_id=tenant, scope_type="tenant", scope_id=tenant, quota_type="llm_tokens",
        limit_value=10_000_000, current_usage=used, period="lifetime", period_start=datetime.now(timezone.utc),
    ))
    await db.commit()


async def quota_used(db, tenant) -> int:
    await db.commit()
    db.expire_all()
    return (await db.execute(select(UsageQuota.current_usage).where(UsageQuota.tenant_id == tenant))).scalar_one()


async def rows(db, tenant):
    db.expire_all()
    return (await db.execute(select(LLMUsageRecord).where(LLMUsageRecord.tenant_id == tenant))).scalars().all()


def body(tenant, **kw):
    b = {
        "tenantId": str(tenant), "runId": "r1", "requestId": f"r1:0:generation-{uuid.uuid4().hex[:6]}",
        "provider": "anthropic", "model": MODEL, "latencyMs": 120, "status": "success",
        "usage": {"inputTokens": 1000, "outputTokens": 500, "cacheReadTokens": 0, "cacheWriteTokens": 0},
    }
    b.update(kw)
    return b


async def post(client, payload, headers=AUTH):
    return await client.post("/internal/models/usage", json=payload, headers=headers)


async def test_same_request_id_twice_one_record_one_quota_increment_AE6(client, test_db):
    t = await make_tenant(test_db)
    await add_quota(test_db, t)
    payload = body(t, costUsd=999.0, cost_usd=999.0)  # caller cost must be ignored
    r1 = await post(client, payload)
    r2 = await post(client, payload)
    assert r1.status_code == 200 and r1.json() == {"recorded": True}
    assert r2.status_code == 200 and r2.json() == {"recorded": False}
    recs = await rows(test_db, t)
    assert len(recs) == 1
    p = LLM_PRICING[MODEL]
    assert float(recs[0].cost_usd) == pytest.approx((1000 * p["input"] + 500 * p["output"]) / 1e6)
    assert recs[0].status == "success" and recs[0].run_id == "r1" and recs[0].request_id == payload["requestId"]
    assert await quota_used(test_db, t) == 1500


async def test_cache_tokens_priced_at_cache_rates(client, test_db):
    t = await make_tenant(test_db)
    u = {"inputTokens": 100, "outputTokens": 50, "cacheReadTokens": 10_000, "cacheWriteTokens": 2_000}
    await post(client, body(t, usage=u))
    rec = (await rows(test_db, t))[0]
    p = LLM_PRICING[MODEL]
    expected = (100 * p["input"] + 50 * p["output"] + 10_000 * p["input"] * 0.1 + 2_000 * p["input"] * 1.25) / 1e6
    assert float(rec.cost_usd) == pytest.approx(expected)
    assert rec.cache_read_tokens == 10_000 and rec.cache_creation_tokens == 2_000


async def test_unpriced_model_records_zero_cost_unpriced(client, test_db, caplog):
    t = await make_tenant(test_db)
    await add_quota(test_db, t)
    with caplog.at_level("WARNING"):
        r = await post(client, body(t, model="mystery-model-9"))
    assert r.json() == {"recorded": True}
    rec = (await rows(test_db, t))[0]
    assert rec.status == "unpriced" and float(rec.cost_usd) == 0
    assert "unpriced" in caplog.text
    assert await quota_used(test_db, t) == 1500


async def test_failed_status_is_stored(client, test_db):
    t = await make_tenant(test_db)
    await post(client, body(t, status="failed"))
    assert (await rows(test_db, t))[0].status == "failed"


async def test_project_in_other_tenant_is_404_and_writes_nothing(client, test_db):
    a = await make_tenant(test_db)
    b = await make_tenant(test_db)
    await add_quota(test_db, b)
    pid = uuid.uuid4()
    owner = User(id=uuid.uuid4(), tenant_id=a, email=f"{uuid.uuid4().hex[:6]}@x.io", name="O")
    test_db.add(owner)
    await test_db.commit()
    test_db.add(Project(id=pid, tenant_id=a, name="P", owner_id=owner.id))
    await test_db.commit()
    r = await post(client, body(b, projectId=str(pid)))
    assert r.status_code == 404
    assert await rows(test_db, b) == []
    assert await quota_used(test_db, b) == 0


async def test_same_request_id_in_two_tenants_are_separate(client, test_db):
    a, b = await make_tenant(test_db), await make_tenant(test_db)
    payload = body(a, requestId="shared-id")
    assert (await post(client, payload)).json() == {"recorded": True}
    assert (await post(client, {**payload, "tenantId": str(b)})).json() == {"recorded": True}
    total = (await test_db.execute(select(func.count()).select_from(LLMUsageRecord))).scalar_one()
    assert total == 2


async def test_provider_id_links_tenant_provider_row(client, test_db):
    t = await make_tenant(test_db)
    pr = LLMProvider(tenant_id=t, name="a", display_name="a", provider_type="anthropic", priority=0, is_enabled=True)
    test_db.add(pr)
    await test_db.commit()
    await post(client, body(t))
    assert (await rows(test_db, t))[0].provider_id == pr.id


async def test_requires_service_token(client, test_db):
    t = await make_tenant(test_db)
    assert (await post(client, body(t), headers={})).status_code == 401
    assert (await post(client, body(t), headers={"Authorization": "Bearer nope"})).status_code == 401
    assert await rows(test_db, t) == []
