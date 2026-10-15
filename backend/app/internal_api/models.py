"""Model policy: per-request model, key and admission decision for the engine (U6)."""
import logging
import time
import uuid
from typing import Any, Dict, List, Literal, Optional

from cachetools import TTLCache
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_system_db
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
            return p, strip_provider_prefix(model)
    p = providers[0]
    tier = "low" if req.purpose == "compaction" else (req.agent.tier or "mid")
    fallback = MODEL_FAMILY_TIERS.get(p.provider_type, {}).get(tier) or p.default_model
    return p, strip_provider_prefix(fallback) if fallback else strip_provider_prefix(model)


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

    max_tokens = min(req.estimate.max_output_tokens or DEFAULT_MAX_TOKENS, MAX_TOKENS_CAP)
    estimated = (req.estimate.input_tokens or 0) + max_tokens
    allowed, reason = await QuotaService(db).check_quota(
        req.tenant_id, _NO_USER, QuotaType.LLM_TOKENS, estimated
    )
    if not allowed:
        return _deny("quota_exceeded", reason or "Token quota exceeded.")

    provider, model = _pick(supported, await _choose_model(db, req), req)
    allow = PolicyAllow(
        provider=provider.provider_type,
        model=model,
        api_key=SecretStr(provider.api_key),
        max_tokens=max_tokens,
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
