"""U6 / R10-R12 / AE5: POST /internal/models/policy decides model, key and admission."""
import logging
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from app.config import Settings
from app.core.database import get_system_db
from app.core.encryption import get_encryption_service
from app.internal_api import models as policy_mod
from app.internal_api.router import mount_internal_router
from app.models.llm_provider import LLMProvider
from app.models.routing_policy import RoutingPolicy
from app.models.tenant import Tenant
from app.models.usage_quota import UsageQuota
from app.services import llm_gateway

GATEWAY = "g" * 12 + "Gateway-Token-0123456789-abcdefXYZ"
ENGINE_SVC = "e" * 12 + "Engine-Svc-Token-0123456789-abcdXY"
SECRET = "Zk3vN8qLw1RtYb6HdPx9CfUe2MaS5JoG7iVn4TlKq0A"
AUTH = {"Authorization": f"Bearer {GATEWAY}"}
ANTHROPIC_KEY = "sk-ant-tenant-KEY-0123456789abcdef"
OPENAI_KEY = "sk-openai-tenant-KEY-0123456789abcdef"
PLATFORM_KEY = "sk-ant-PLATFORM-KEY-should-never-leak"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr("app.core.database.set_tenant_context", AsyncMock())
    monkeypatch.setattr(llm_gateway.settings, "ANTHROPIC_API_KEY", PLATFORM_KEY, raising=False)
    monkeypatch.setattr(llm_gateway.settings, "OPENAI_API_KEY", PLATFORM_KEY, raising=False)
    # SQLite has no ARRAY: drop the list default on llm_providers.models
    monkeypatch.setattr(LLMProvider.__table__.c.models, "default", None)
    llm_gateway._tenant_provider_cache.clear()
    policy_mod._decisions.clear()


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


async def make_tenant(db, active=True) -> uuid.UUID:
    t = Tenant(id=uuid.uuid4(), name="T", slug=f"t-{uuid.uuid4().hex[:8]}", is_active=active, settings={})
    db.add(t)
    await db.commit()
    return t.id


async def add_provider(db, tenant, ptype="anthropic", key=ANTHROPIC_KEY, priority=0, default_model=None):
    db.add(LLMProvider(
        tenant_id=tenant, name=f"{ptype}-{priority}", display_name=ptype, provider_type=ptype,
        api_key_encrypted=get_encryption_service().encrypt(key) if key else None,
        default_model=default_model, priority=priority, is_enabled=True,
    ))
    await db.commit()


def body(tenant, **kw):
    b = {
        "tenantId": str(tenant), "projectId": "p1", "runId": "r1", "round": 0, "purpose": "generation",
        "requestId": f"req-{uuid.uuid4().hex[:6]}", "agent": {}, "supportedProviders": ["anthropic"],
        "estimate": {"inputTokens": 100, "maxOutputTokens": 1000},
    }
    b.update(kw)
    return b


async def post(client, payload):
    return await client.post("/internal/models/policy", json=payload, headers=AUTH)


async def test_tenant_anthropic_provider_returns_bare_model_and_its_key(client, test_db):
    t = await make_tenant(test_db)
    await add_provider(test_db, t, default_model="anthropic/claude-sonnet-4-5")
    r = await post(client, body(t))
    j = r.json()
    assert r.status_code == 200 and j["ok"] is True
    assert j["provider"] == "anthropic" and j["model"] == "claude-sonnet-5-5"
    assert j["apiKey"] == ANTHROPIC_KEY and j["maxTokens"] == 1000
    assert j["expiresAt"] > 0 and j["requestId"].startswith("req-")
    assert r.headers["cache-control"] == "no-store"


async def test_only_unsupported_provider_is_no_supported_provider(client, test_db):
    t = await make_tenant(test_db)
    await add_provider(test_db, t, ptype="openai", key=OPENAI_KEY)
    j = (await post(client, body(t))).json()
    assert j == {"ok": False, "code": "no_supported_provider", "reason": j["reason"], "retryable": False}
    assert OPENAI_KEY not in str(j)


async def test_no_key_never_uses_platform_key(client, test_db):
    t = await make_tenant(test_db)
    await add_provider(test_db, t, key=None)  # provider row exists, env has a platform key
    r = await post(client, body(t))
    assert r.json()["code"] == "no_key" and r.json()["ok"] is False
    assert PLATFORM_KEY not in r.text
    t2 = await make_tenant(test_db)  # no provider rows at all
    r2 = await post(client, body(t2))
    assert r2.json()["code"] == "no_key" and PLATFORM_KEY not in r2.text


async def test_over_quota_denies_without_key_AE5(client, test_db):
    t = await make_tenant(test_db)
    await add_provider(test_db, t)
    test_db.add(UsageQuota(
        tenant_id=t, scope_type="tenant", scope_id=t, quota_type="llm_tokens", limit_value=1000,
        current_usage=900, period="lifetime", period_start=datetime.now(timezone.utc),
    ))
    await test_db.commit()
    r = await post(client, body(t))
    j = r.json()
    assert r.status_code == 200 and j["ok"] is False and j["code"] == "quota_exceeded"
    assert "apiKey" not in j and ANTHROPIC_KEY not in r.text


async def test_disabled_tenant_is_denied(client, test_db):
    t = await make_tenant(test_db, active=False)
    await add_provider(test_db, t)
    j = (await post(client, body(t))).json()
    assert j["code"] == "tenant_disabled" and ANTHROPIC_KEY not in str(j)


async def test_model_hint_tier_compaction_and_routing_policy(client, test_db):
    t = await make_tenant(test_db)
    await add_provider(test_db, t, default_model="anthropic/claude-sonnet-4-5")
    hint = (await post(client, body(t, agent={"model": "claude-opus-4-1"}))).json()
    assert hint["model"] == "claude-opus-4-1"
    tier = (await post(client, body(t, agent={"tier": "high"}))).json()
    assert tier["model"] == "claude-opus-5-5"
    comp = (await post(client, body(t, purpose="compaction", agent={"model": "claude-opus-4-1"}))).json()
    assert comp["model"] == "claude-haiku-5-5"
    test_db.add(RoutingPolicy(
        tenant_id=t, name="g", task_type="generation", model_id="anthropic/claude-3-5-haiku", provider="anthropic",
        priority=1, is_active=True,
    ))
    await test_db.commit()
    routed = (await post(client, body(t))).json()
    assert routed["model"] == "claude-3-5-haiku"


async def test_priority_and_supported_filter_pick_provider(client, test_db):
    t = await make_tenant(test_db)
    await add_provider(test_db, t, ptype="openai", key=OPENAI_KEY, priority=0, default_model="openai/gpt-4o")
    await add_provider(test_db, t, ptype="anthropic", priority=1)
    j = (await post(client, body(t, supportedProviders=["anthropic", "openai"]))).json()
    assert j["provider"] == "openai" and j["apiKey"] == OPENAI_KEY
    j = (await post(client, body(t, supportedProviders=["anthropic"]))).json()
    assert j["provider"] == "anthropic" and j["apiKey"] == ANTHROPIC_KEY
    assert j["model"].startswith("claude")


async def test_logs_never_contain_key_or_authorization(client, test_db, caplog):
    t = await make_tenant(test_db)
    await add_provider(test_db, t)
    with caplog.at_level(logging.DEBUG):
        await post(client, body(t))
    assert caplog.records
    assert ANTHROPIC_KEY not in caplog.text and GATEWAY not in caplog.text


async def test_same_request_id_returns_same_decision(client, test_db):
    t = await make_tenant(test_db)
    await add_provider(test_db, t)
    b = body(t, requestId="fixed")
    first = await post(client, b)
    second = await post(client, b)
    assert first.json() == second.json() and first.json()["ok"] is True
    assert second.headers["cache-control"] == "no-store"


async def test_requires_service_token(client, test_db):
    t = await make_tenant(test_db)
    r = await client.post("/internal/models/policy", json=body(t))
    assert r.status_code in (401, 403)
