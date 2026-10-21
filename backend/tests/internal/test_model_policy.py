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


async def test_deep_work_worker_request_goes_through_the_token_quota(client, test_db):
    """U7 / KTD7: a Deep Work subagent request is ordinary policy traffic, so the tenant LLM
    token check bounds it exactly like the orchestrator's. The engine sends the worker's resolved
    model as ``agent.model`` and reports the subagent's usage under ``agentRole``."""
    t = await make_tenant(test_db)
    await add_provider(test_db, t)
    worker = body(t, agent={"model": "claude-haiku-4-5"}, agentRole="research_worker")
    assert (await post(client, worker)).json()["ok"] is True  # within quota: allowed as usual
    test_db.add(UsageQuota(
        tenant_id=t, scope_type="tenant", scope_id=t, quota_type="llm_tokens", limit_value=1000,
        current_usage=900, period="lifetime", period_start=datetime.now(timezone.utc),
    ))
    await test_db.commit()
    r = await post(client, body(t, agent={"model": "claude-haiku-4-5"}, agentRole="research_worker"))
    j = r.json()
    assert j["ok"] is False and j["code"] == "quota_exceeded"
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


# ---------------------------------------------------------------------------
# OpenRouter: tenant whose only stored key is OpenRouter's
# ---------------------------------------------------------------------------

OR_KEY = "sk-or-tenant-KEY-0123456789abcdef"
DEEPSEEK = "deepseek/deepseek-v4.1-flash"


def or_body(tenant, **kw):
    return body(tenant, supportedProviders=["anthropic", "openrouter"], **kw)


async def test_openrouter_tiers_and_compaction_use_dotted_openrouter_slugs(client, test_db):
    t = await make_tenant(test_db)
    await add_provider(test_db, t, ptype="openrouter", key=OR_KEY)
    for tier, slug in [
        ("high", "anthropic/claude-opus-5.5"),
        ("mid", "anthropic/claude-sonnet-5.5"),
        ("low", "anthropic/claude-haiku-5.5"),
    ]:
        j = (await post(client, or_body(t, agent={"tier": tier}))).json()
        assert j["ok"] is True and j["provider"] == "openrouter" and j["apiKey"] == OR_KEY
        assert j["model"] == slug
    default = (await post(client, or_body(t))).json()
    assert default["model"] == "anthropic/claude-sonnet-5.5"
    comp = (await post(client, or_body(t, purpose="compaction", agent={"tier": "high"}))).json()
    assert comp["model"] == "anthropic/claude-haiku-5.5"


async def test_openrouter_not_chosen_when_engine_does_not_support_it(client, test_db):
    t = await make_tenant(test_db)
    await add_provider(test_db, t, ptype="openrouter", key=OR_KEY)
    j = (await post(client, body(t))).json()
    assert j["ok"] is False and j["code"] == "no_supported_provider" and OR_KEY not in str(j)


@pytest.mark.parametrize("stored", [DEEPSEEK, f"openrouter/{DEEPSEEK}"])
async def test_openrouter_default_model_pins_chat_and_every_tier(client, test_db, stored):
    t = await make_tenant(test_db)
    await add_provider(test_db, t, ptype="openrouter", key=OR_KEY, default_model=stored)
    for agent in [{}, {"tier": "high"}, {"tier": "mid"}, {"tier": "low"}]:
        j = (await post(client, or_body(t, agent=agent))).json()
        assert j["provider"] == "openrouter" and j["model"] == DEEPSEEK
    comp = (await post(client, or_body(t, purpose="compaction"))).json()
    assert comp["model"] == DEEPSEEK


async def test_deep_work_tier_models_honour_the_openrouter_pin(client, test_db):
    """Deep Work sends an explicit per-tier model, so the pin must reach tier resolution itself;
    otherwise a DeepSeek-pinned tenant runs intake/plan/orchestrator on Claude via OpenRouter."""
    from app.services.engine_deep_work_agent import resolve_deep_work_models
    from app.services.llm_gateway import resolve_model_for_tier

    t = await make_tenant(test_db)
    await add_provider(test_db, t, ptype="openrouter", key=OR_KEY, default_model=DEEPSEEK)
    for tier in ("high", "mid", "low"):
        assert await resolve_model_for_tier(test_db, t, tier) == DEEPSEEK
    models = await resolve_deep_work_models(test_db, t, None, None)
    for model in filter(None, (models.high, models.mid, models.low, models.worker)):
        j = (await post(client, or_body(t, agent={"model": model}))).json()
        assert j["provider"] == "openrouter" and j["model"] == DEEPSEEK, (model, j)


async def test_openrouter_explicit_model_hint_still_wins_over_pin(client, test_db):
    t = await make_tenant(test_db)
    await add_provider(test_db, t, ptype="openrouter", key=OR_KEY, default_model=DEEPSEEK)
    j = (await post(client, or_body(t, agent={"model": "openrouter/openai/gpt-4o-mini"}))).json()
    assert j["model"] == "openai/gpt-4o-mini"
    j = (await post(client, or_body(t, agent={"model": "openrouter/anthropic/claude-opus-5-5"}))).json()
    assert j["model"] == "anthropic/claude-opus-5.5"


async def test_pin_applies_only_when_openrouter_is_the_preferred_provider(client, test_db):
    t = await make_tenant(test_db)
    await add_provider(test_db, t, ptype="anthropic", priority=0)
    await add_provider(test_db, t, ptype="openrouter", key=OR_KEY, priority=1, default_model=DEEPSEEK)
    j = (await post(client, or_body(t))).json()
    assert j["provider"] == "anthropic" and j["model"] == "claude-sonnet-5-5"


def test_to_openrouter_model_id_normalises_claude_versions_only():
    f = llm_gateway.to_openrouter_model_id
    assert f("openrouter/anthropic/claude-opus-5-5") == "anthropic/claude-opus-5.5"
    assert f("anthropic/claude-3-5-haiku") == "anthropic/claude-3.5-haiku"
    assert f("anthropic/claude-opus-4-1-20250805") == "anthropic/claude-opus-4.1-20250805"
    assert f("anthropic/claude-sonnet-4.5") == "anthropic/claude-sonnet-4.5"
    assert f("openrouter/deepseek/deepseek-v4.1-flash") == DEEPSEEK
    assert f("openai/gpt-4o-mini") == "openai/gpt-4o-mini"
    assert f("openrouter/google/gemini-2.0-flash-exp:free") == "google/gemini-2.0-flash-exp:free"


def test_openrouter_ids_are_priced():
    from app.config.llm_pricing import calculate_cost_with_cache, find_model_pricing

    ds = find_model_pricing(DEEPSEEK)
    assert ds == {"input": 0.036, "output": 0.6435, "cache_read": 0.0054}
    assert calculate_cost_with_cache(ds, 1_000_000, 1_000_000, 1_000_000) == pytest.approx(0.036 + 0.6435 + 0.0054)
    for slug, base in [
        ("anthropic/claude-opus-5.5", "claude-opus-5-5"),
        ("anthropic/claude-sonnet-5.5", "claude-sonnet-5-5"),
        ("anthropic/claude-haiku-5.5", "claude-haiku-5-5"),
        ("anthropic/claude-sonnet-4.5", "claude-sonnet-4-5"),
    ]:
        assert find_model_pricing(slug) is find_model_pricing(base) and find_model_pricing(slug) is not None
    assert find_model_pricing("openai/gpt-4o-mini") is not None


async def test_a_deep_work_run_gets_the_larger_output_budget(client, test_db):
    """Without an estimate a request gets the chat default; a Deep Work run (orchestrator, and its workers, which
    report the run's root conversation) gets DEEP_WORK_MAX_OUTPUT_TOKENS, since reasoning eats into the budget."""
    from app.config import get_settings
    from app.models.engine import EngineConversation

    t = await make_tenant(test_db)
    await add_provider(test_db, t)
    dw, chat = uuid.uuid4(), uuid.uuid4()
    for cid, sel in ((dw, {"deep_work": True}), (chat, {})):
        test_db.add(EngineConversation(conversation_id=cid, tenant_id=t, project_id=uuid.uuid4(), user_id=uuid.uuid4(),
                                       session_id="s", selections=sel))
    await test_db.commit()
    no_estimate = {"estimate": {}}
    deep = (await post(client, body(t, conversationId=str(dw), **no_estimate))).json()
    plain = (await post(client, body(t, conversationId=str(chat), **no_estimate))).json()
    unknown = (await post(client, body(t, conversationId="not-a-uuid", **no_estimate))).json()
    assert deep["maxTokens"] == get_settings().DEEP_WORK_MAX_OUTPUT_TOKENS > plain["maxTokens"]
    assert plain["maxTokens"] == unknown["maxTokens"] == 8192
    # an explicit estimate still wins
    assert (await post(client, body(t, conversationId=str(dw)))).json()["maxTokens"] == 1000

