"""Model policy: per-request model, key and admission decision for the engine (U6)."""
import logging
import time
import uuid
from typing import Any, Dict, List, Literal, Optional

from cachetools import TTLCache
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.config.llm_pricing import calculate_cost_with_cache, context_window_for, find_model_pricing
from app.core.database import get_system_db
from app.models.engine import EngineConversation
from app.models.llm_provider import LLMProvider
from app.models.llm_usage import LLMUsageRecord
from app.models.project import Project
from app.models.tenant import Tenant
from app.models.usage_quota import QuotaType
from app.services.llm_gateway import (
    MODEL_FAMILY_TIERS,
    ProviderConfig,
    get_provider_from_model,
    get_tenant_own_key_providers,
    resolve_model_for_policy,
    resolve_model_for_tier,
    strip_provider_prefix,
    to_openrouter_model_id,
)
from app.services.quota_service import QuotaService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/models")

DEFAULT_MAX_TOKENS = 8192
MAX_TOKENS_CAP = 64_000
KEY_TTL_MS = 5 * 60 * 1000
# The engine has no user; a nil user id matches no user-scoped quota, so the tenant quota decides.
_NO_USER = uuid.UUID(int=0)

# requestId -> decision, so a retried request gets the same answer (and the same expiresAt).
_decisions: TTLCache = TTLCache(maxsize=1024, ttl=60)


class _Camel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class AgentHint(_Camel):
    model: Optional[str] = None
    tier: Optional[Literal["high", "mid", "low"]] = None


class Estimate(_Camel):
    input_tokens: Optional[int] = Field(None, alias="inputTokens", ge=0)
    max_output_tokens: Optional[int] = Field(None, alias="maxOutputTokens", ge=1)


class PolicyRequest(_Camel):
    tenant_id: uuid.UUID = Field(alias="tenantId")
    project_id: Optional[str] = Field(None, alias="projectId")
    conversation_id: Optional[str] = Field(None, alias="conversationId")
    engine_conversation_id: Optional[str] = Field(None, alias="engineConversationId")  # informational
    run_id: str = Field(alias="runId")
    round: int = 0
    purpose: Literal["generation", "compaction"] = "generation"
    request_id: str = Field(alias="requestId", min_length=1)
    agent: AgentHint = Field(default_factory=AgentHint)
    supported_providers: List[str] = Field(default_factory=list, alias="supportedProviders")
    estimate: Estimate = Field(default_factory=Estimate)


class PolicyAllow(BaseModel):
    ok: Literal[True] = True
    provider: str
    model: str
    api_key: SecretStr
    max_tokens: int
    context_window: Optional[int] = None
    expires_at: int
    request_id: str


class PolicyDeny(BaseModel):
    ok: Literal[False] = False
    code: Literal["quota_exceeded", "no_key", "no_supported_provider", "tenant_disabled"]
    reason: str
    retryable: bool = False


def _deny(code: str, reason: str) -> Dict[str, Any]:
    return PolicyDeny(code=code, reason=reason).model_dump(mode="json")  # type: ignore[arg-type]


def _serialize_allow(a: PolicyAllow) -> Dict[str, Any]:
    """The only place the key leaves its SecretStr."""
    return {
        "ok": True,
        "provider": a.provider,
        "model": a.model,
        "apiKey": a.api_key.get_secret_value(),
        "maxTokens": a.max_tokens,
        **({"contextWindow": a.context_window} if a.context_window else {}),
        "expiresAt": a.expires_at,
        "requestId": a.request_id,
    }


async def _choose_model(db: AsyncSession, req: PolicyRequest) -> str:
    if req.purpose == "compaction":
        return await resolve_model_for_tier(db, req.tenant_id, "low")
    if req.agent.model:
        return req.agent.model
    if req.agent.tier:
        return await resolve_model_for_tier(db, req.tenant_id, req.agent.tier)
    return await resolve_model_for_policy(db, req.tenant_id, "generation")


def _pick(
    providers: List[ProviderConfig], model: str, req: PolicyRequest
) -> tuple[ProviderConfig, str]:
    """Provider for the model's family (by priority), else the best supported provider with its own model."""
    family = get_provider_from_model(model)
    for p in providers:
        if p.provider_type == family:
            return p, _bare_model(p, model)
    p = providers[0]
    tier = "low" if req.purpose == "compaction" else (req.agent.tier or "mid")
    if p.provider_type == "openrouter" and p.default_model:
        fallback: Optional[str] = p.default_model  # the tenant's own pick beats the Claude tier table
    else:
        fallback = MODEL_FAMILY_TIERS.get(p.provider_type, {}).get(tier) or p.default_model
    return p, _bare_model(p, fallback or model)


def _bare_model(p: ProviderConfig, model: str) -> str:
    """The model id as the provider's adapter expects it."""
    if p.provider_type == "openrouter":
        return to_openrouter_model_id(model)
    return strip_provider_prefix(model)


def _pinned_openrouter_model(supported: List[ProviderConfig], req: PolicyRequest) -> Optional[str]:
    """The tenant's OpenRouter default_model, when OpenRouter is its preferred provider and no model was requested.

    Tier-based requests (Deep Work, compaction) fall back to it too, so a tenant that pinned a model
    on its OpenRouter provider runs everything on that model instead of the Claude tier table.
    """
    first = supported[0]
    if first.provider_type != "openrouter" or not first.default_model or req.agent.model:
        return None
    return first.default_model


async def _default_max_tokens(db: AsyncSession, req: PolicyRequest) -> int:
    """The output budget when the engine sends no estimate: larger for a Deep Work run (its workers report the
    run's root conversation), the chat default otherwise."""
    try:
        conversation_id = uuid.UUID(req.conversation_id) if req.conversation_id else None
    except ValueError:
        conversation_id = None
    if conversation_id is not None:
        selections = (await db.execute(
            select(EngineConversation.selections).where(
                EngineConversation.conversation_id == conversation_id,
                EngineConversation.tenant_id == req.tenant_id,
            )
        )).scalar_one_or_none()
        if isinstance(selections, dict) and selections.get("deep_work"):
            return get_settings().DEEP_WORK_MAX_OUTPUT_TOKENS
    return DEFAULT_MAX_TOKENS


async def decide(db: AsyncSession, req: PolicyRequest) -> Dict[str, Any]:
    tenant = (await db.execute(select(Tenant).where(Tenant.id == req.tenant_id))).scalar_one_or_none()
    if tenant is None or not tenant.is_active:
        return _deny("tenant_disabled", "This organization is disabled.")

    providers = await get_tenant_own_key_providers(db, req.tenant_id)
    if not providers:
        return _deny("no_key", "No model API key is configured for this organization.")
    supported = [p for p in providers if p.provider_type in set(req.supported_providers)]
    if not supported:
        return _deny("no_supported_provider", "No configured provider is supported by the engine.")

    max_tokens = min(req.estimate.max_output_tokens or await _default_max_tokens(db, req), MAX_TOKENS_CAP)
    estimated = (req.estimate.input_tokens or 0) + max_tokens
    allowed, reason = await QuotaService(db).check_quota(
        req.tenant_id, _NO_USER, QuotaType.LLM_TOKENS, estimated
    )
    if not allowed:
        return _deny("quota_exceeded", reason or "Token quota exceeded.")

    pinned = _pinned_openrouter_model(supported, req)
    chosen = f"openrouter/{to_openrouter_model_id(pinned)}" if pinned else await _choose_model(db, req)
    provider, model = _pick(supported, chosen, req)
    allow = PolicyAllow(
        provider=provider.provider_type,
        model=model,
        api_key=SecretStr(provider.api_key),
        max_tokens=max_tokens,
        context_window=context_window_for(model),
        expires_at=int(time.time() * 1000) + KEY_TTL_MS,
        request_id=req.request_id,
    )
    logger.info(
        "model policy allow tenant=%s run=%s request=%s provider=%s model=%s",
        req.tenant_id, req.run_id, req.request_id, allow.provider, allow.model,
    )
    return _serialize_allow(allow)


@router.post("/policy")
async def model_policy(req: PolicyRequest, db: AsyncSession = Depends(get_system_db)) -> JSONResponse:
    cache_key = (str(req.tenant_id), req.request_id)
    body = _decisions.get(cache_key)
    if body is None:
        body = await decide(db, req)
        if body.get("ok"):
            _decisions[cache_key] = body
    return JSONResponse(body, headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------------------
# Usage ledger (U7)
# ---------------------------------------------------------------------------

class UsageTokens(_Camel):
    input_tokens: int = Field(0, alias="inputTokens", ge=0)
    output_tokens: int = Field(0, alias="outputTokens", ge=0)
    cache_read_tokens: int = Field(0, alias="cacheReadTokens", ge=0)
    cache_write_tokens: int = Field(0, alias="cacheWriteTokens", ge=0)


class UsageRequest(_Camel):
    # Any caller-supplied cost field is ignored (extra="ignore"): cost comes from Oniva pricing.
    tenant_id: uuid.UUID = Field(alias="tenantId")
    project_id: Optional[uuid.UUID] = Field(None, alias="projectId")
    conversation_id: Optional[uuid.UUID] = Field(None, alias="conversationId")
    engine_conversation_id: Optional[str] = Field(None, alias="engineConversationId")  # informational
    run_id: str = Field(alias="runId", min_length=1, max_length=128)
    request_id: str = Field(alias="requestId", min_length=1, max_length=255)
    provider: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=100)
    usage: UsageTokens = Field(default_factory=UsageTokens)
    latency_ms: Optional[int] = Field(None, alias="latencyMs", ge=0)
    status: Literal["success", "failed", "cached"] = "success"
    purpose: Literal["generation", "compaction"] = "generation"
    # Subagent tool name for child-conversation usage (e.g. research_worker); absent/"root" for the root.
    agent_role: Optional[str] = Field(None, alias="agentRole", min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")


async def record_usage(db: AsyncSession, req: UsageRequest) -> bool:
    """Insert the ledger row once per (tenant, requestId). Returns whether a new row was written."""
    user_id: uuid.UUID = _NO_USER
    if req.project_id is not None:
        found = (await db.execute(
            select(Project.id).where(Project.id == req.project_id, Project.tenant_id == req.tenant_id)
        )).scalar_one_or_none()
        if found is None:
            raise HTTPException(status_code=404, detail="project_not_found")
    if req.conversation_id is not None:
        conv = (await db.execute(
            select(EngineConversation).where(
                EngineConversation.conversation_id == req.conversation_id,
                EngineConversation.tenant_id == req.tenant_id,
            )
        )).scalar_one_or_none()
        if conv is None:
            raise HTTPException(status_code=404, detail="conversation_not_found")
        user_id = conv.user_id

    u = req.usage
    pricing = find_model_pricing(req.model)
    status = req.status
    if pricing is None:
        cost = 0.0
        if status == "success":
            status = "unpriced"
        logger.warning("usage for unpriced model tenant=%s model=%s request=%s", req.tenant_id, req.model, req.request_id)
    else:
        cost = calculate_cost_with_cache(
            pricing, u.input_tokens, u.output_tokens, u.cache_read_tokens, u.cache_write_tokens
        )

    provider_id = (await db.execute(
        select(LLMProvider.id)
        .where(LLMProvider.tenant_id == req.tenant_id, LLMProvider.provider_type == req.provider)
        .order_by(LLMProvider.priority)
        .limit(1)
    )).scalar_one_or_none()

    values = dict(
        id=uuid.uuid4(),
        tenant_id=req.tenant_id,
        user_id=user_id if user_id != _NO_USER else None,
        provider_id=provider_id,
        provider_name=req.provider,
        model=req.model,
        input_tokens=u.input_tokens,
        output_tokens=u.output_tokens,
        cost_usd=cost,
        latency_ms=req.latency_ms or 0,
        cache_creation_tokens=u.cache_write_tokens,
        cache_read_tokens=u.cache_read_tokens,
        request_id=req.request_id,
        run_id=req.run_id,
        conversation_id=req.conversation_id,
        purpose=req.purpose,
        agent_role=req.agent_role,
        status=status,
    )
    if db.get_bind().dialect.name == "postgresql":
        stmt = pg_insert(LLMUsageRecord).values(**values).on_conflict_do_nothing(
            index_elements=["tenant_id", "request_id"], index_where=text("request_id IS NOT NULL")
        )
    else:
        stmt = sqlite_insert(LLMUsageRecord).values(**values).on_conflict_do_nothing()
    result = await db.execute(stmt)
    if not result.rowcount:
        await db.commit()
        return False

    # Only a newly inserted row counts against the quota; the row and the counter commit together.
    total = u.input_tokens + u.output_tokens + u.cache_read_tokens + u.cache_write_tokens
    if total:
        await QuotaService(db).increment_usage(
            req.tenant_id, user_id, QuotaType.LLM_TOKENS, total
        )
    else:
        await db.commit()
    return True


@router.post("/usage")
async def model_usage(req: UsageRequest, db: AsyncSession = Depends(get_system_db)) -> JSONResponse:
    recorded = await record_usage(db, req)
    return JSONResponse({"recorded": recorded}, headers={"Cache-Control": "no-store"})
