"""
LLM Gateway Service - Centralized LLM control with failover.

Phase 2: LLM Reliability - Thin wrapper around LiteLLM with:
- Tenant-specific provider configuration from Settings
- Circuit breaker for provider health (reuses redis_ha.py)
- User-defined fallback chains
- Support for custom endpoints (vLLM, Ollama, Azure)
- Usage logging for cost attribution

Design principles (from review):
- Reuse existing CircuitBreaker from redis_ha.py
- No custom rate limiting (rely on provider limits)
- Use LiteLLM's built-in cost calculation
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, NamedTuple, Optional, Tuple
from uuid import UUID, uuid4

from cachetools import TTLCache

import litellm
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.encryption import get_encryption_service
from app.models.llm_provider import LLMProvider
from app.services.redis_ha import CircuitBreaker, CircuitBreakerConfig

logger = logging.getLogger(__name__)
settings = get_settings()

# Suppress litellm's noisy debug logging
litellm.suppress_debug_info = True
# Drop unsupported params (e.g. 'usage') for providers that reject them
litellm.drop_params = True

# Patch litellm OpenRouter transform to remove 'usage' field that newer
# OpenRouter API rejects (litellm 1.83.x bug)
try:
    from litellm.llms.openrouter.chat.transformation import OpenrouterConfig as _ORC
    _orig_transform = _ORC.transform_request
    def _patched_transform(self, *args, **kwargs):
        result = _orig_transform(self, *args, **kwargs)
        result.pop("usage", None)
        return result
    _ORC.transform_request = _patched_transform
except Exception:
    pass


# ============================================================================
# Request/Response Models
# ============================================================================

class ToolCallResponse(BaseModel):
    """A single tool call from an LLM response."""
    id: str
    name: str
    arguments: dict[str, Any]

    @field_validator("arguments", mode="before")
    @classmethod
    def parse_arguments(cls, v: Any) -> dict[str, Any]:
        """LiteLLM returns arguments as JSON string, not dict."""
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                # Some models (e.g. Qwen) produce malformed JSON for long
                # code arguments. Return a raw_content fallback so the tool
                # call still reaches the engine (which can handle the error
                # gracefully) instead of failing the entire LLM call.
                logger.warning("Failed to parse tool call arguments, using raw fallback: %s", v[:200])
                return {"_raw_content": v}
        return v


class LLMRequest(BaseModel):
    """LLM completion request."""
    messages: list[dict[str, Any]]
    model: str
    temperature: float = 0.7
    max_tokens: int | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | None = None

    # Tracking context
    workflow_id: UUID | None = None
    node_id: str | None = None
    agent_id: UUID | None = None


class LLMResponse(BaseModel):
    """LLM completion response."""
    content: str
    model: str
    provider_name: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    tool_calls: list[ToolCallResponse] | None = None


class LLMStreamChunk(BaseModel):
    """A single chunk from a streaming LLM response."""
    content: str
    finish_reason: str | None = None


class AllProvidersFailedError(Exception):
    """All providers in fallback chain failed."""

    def __init__(self, message: str, errors: List[str]):
        super().__init__(message)
        self.errors = errors


# ============================================================================
# Provider Configuration
# ============================================================================

@dataclass
class ProviderConfig:
    """Runtime provider configuration."""
    id: UUID
    name: str
    provider_type: str
    api_base: Optional[str]
    api_key: str
    default_model: Optional[str]
    priority: int
    workflow_builder_model: Optional[str] = None


async def get_tenant_providers(
    db: AsyncSession,
    tenant_id: UUID,
) -> List[ProviderConfig]:
    """Load enabled providers for tenant, ordered by priority."""
    from app.core.database import set_tenant_context
    # Ensure RLS context is set — without it, RLS policies return 0 rows
    # even when filtering by tenant_id explicitly.
    await set_tenant_context(db, str(tenant_id))

    stmt = (
        select(LLMProvider)
        .where(
            LLMProvider.tenant_id == tenant_id,
            LLMProvider.is_enabled == True,
        )
        .order_by(LLMProvider.priority)
    )

    result = await db.execute(stmt)
    providers = result.scalars().all()

    encryption = get_encryption_service()
    configs = []

    for p in providers:
        # Decrypt API key if stored
        api_key = None
        if p.api_key_encrypted:
            try:
                api_key = encryption.decrypt(p.api_key_encrypted)
            except Exception as e:
                logger.warning(f"Failed to decrypt API key for provider {p.name}: {e}")

        # Fall back to environment variables for built-in providers
        if not api_key:
            if p.provider_type == "openai":
                api_key = settings.OPENAI_API_KEY
            elif p.provider_type == "anthropic":
                api_key = settings.ANTHROPIC_API_KEY
            elif p.provider_type == "openrouter":
                api_key = settings.OPENROUTER_API_KEY

        # Only include providers with valid keys
        if api_key:
            configs.append(ProviderConfig(
                id=p.id,
                name=p.name,
                provider_type=p.provider_type,
                api_base=p.api_base,
                api_key=api_key,
                default_model=p.default_model,
                priority=p.priority,
                workflow_builder_model=p.workflow_builder_model,
            ))
        else:
            logger.warning(f"Skipping provider {p.name}: no API key available")

    return configs


# ============================================================================
# Unified Key Resolution (replaces 10+ scattered _get_api_key() functions)
# ============================================================================

# In-memory cache for tenant providers (bounded, short TTL, invalidated on CRUD)
# NOTE: Decrypted API keys live here — in-memory only, never send to Redis.
_tenant_provider_cache: TTLCache = TTLCache(maxsize=200, ttl=300)  # 5-min TTL, max 200 tenants


def _invalidate_provider_cache(tenant_id: UUID) -> None:
    """Invalidate the in-memory provider cache for a tenant."""
    cache_key = str(tenant_id)
    _tenant_provider_cache.pop(cache_key, None)


async def _get_cached_tenant_providers(
    db: AsyncSession,
    tenant_id: UUID,
) -> List[ProviderConfig]:
    """Get tenant providers with in-memory caching."""
    cache_key = str(tenant_id)

    cached = _tenant_provider_cache.get(cache_key)
    if cached is not None:
        return cached

    providers = await get_tenant_providers(db, tenant_id)
    _tenant_provider_cache[cache_key] = providers
    return providers


class EffectiveProvider(NamedTuple):
    """Resolved provider config for direct API calls (not LiteLLM)."""
    api_key: str
    model: str
    provider_type: str
    api_base: Optional[str]


async def resolve_effective_provider(
    db: AsyncSession,
    tenant_id: UUID,
    model_field: str = "default_model",
) -> Optional[EffectiveProvider]:
    """
    Resolve the tenant's LLM provider config for direct API calls.

    Returns the first enabled provider's key, model, type, and base URL.
    Does NOT apply LiteLLM routing prefixes — returns raw model strings
    suitable for AsyncOpenAI/AsyncAnthropic direct calls.

    Args:
        db: Database session with tenant context
        tenant_id: Tenant UUID
        model_field: Which model field to use ('default_model' or 'workflow_builder_model')

    Returns:
        EffectiveProvider tuple, or None if no providers are configured.
        When None, callers should either return an error or fall back to env vars
        for bootstrap scenarios.

    Note:
        Uses _get_cached_tenant_providers (5-min TTL cache). Provider key
        decryption failures already fall back to env vars inside
        get_tenant_providers(), so callers should NOT add redundant env var
        checks — only fall back when this function returns None (no providers
        at all).
    """
    providers = await _get_cached_tenant_providers(db, tenant_id)
    if not providers:
        return None

    # Select first provider by priority (matching workflow_ai.py behavior)
    selected = providers[0]

    # Extract model: try requested field, fall back to default_model, then hardcoded fallback
    model = getattr(selected, model_field, None) or selected.default_model or "openai/gpt-4o"

    return EffectiveProvider(
        api_key=selected.api_key,
        model=model,
        provider_type=selected.provider_type,
        api_base=selected.api_base,
    )


async def resolve_llm_key(
    db: AsyncSession,
    tenant_id: UUID,
    provider_type: str,
) -> Optional[str]:
    """
    Resolve an API key for a specific LLM provider.

    Single-function replacement for all scattered _get_api_key() functions.
    Queries llm_providers table (cached), falls back to env vars.

    Args:
        db: Database session
        tenant_id: Tenant ID
        provider_type: Provider type (e.g., "openai", "anthropic", "openrouter")

    Returns:
        Decrypted API key, or None if not found
    """
    providers = await _get_cached_tenant_providers(db, tenant_id)
    for p in providers:
        if p.provider_type == provider_type:
            return p.api_key
    return None


async def resolve_all_llm_keys(
    db: AsyncSession,
    tenant_id: UUID,
) -> Dict[str, str]:
    """
    Resolve all LLM provider keys for a tenant.

    Replaces the 3-query loop in BaseAgent.run() and similar patterns.

    Args:
        db: Database session
        tenant_id: Tenant ID

    Returns:
        Dict of provider_type -> decrypted API key
    """
    providers = await _get_cached_tenant_providers(db, tenant_id)
    return {p.provider_type: p.api_key for p in providers}


async def resolve_tenant_default_model(
    db: AsyncSession,
    tenant_id: UUID,
) -> Optional[str]:
    """
    Resolve the tenant's default model from their highest-priority LLM provider.

    Returns the default_model from the first (highest-priority) enabled provider
    that has one configured, or None if no provider has a default model.
    """
    providers = await _get_cached_tenant_providers(db, tenant_id)
    for p in providers:
        if p.default_model:
            return p.default_model
    return None


async def resolve_provider_config(
    db: AsyncSession,
    tenant_id: UUID,
    provider_type: str,
) -> Optional[ProviderConfig]:
    """
    Resolve full provider config (key + base_url + model).

    Args:
        db: Database session
        tenant_id: Tenant ID
        provider_type: Provider type

    Returns:
        ProviderConfig or None
    """
    providers = await _get_cached_tenant_providers(db, tenant_id)
    for p in providers:
        if p.provider_type == provider_type:
            return p
    return None


async def resolve_key_with_fallback(
    db: AsyncSession,
    tenant_id: Optional[UUID],
    model_id: str,
) -> Tuple[Optional[str], str]:
    """
    Resolve an API key for a model, falling back to OpenRouter if the
    specific provider key isn't available.

    When falling back to OpenRouter, the model_id is prefixed with
    'openrouter/' so that litellm.acompletion() and create_litellm_model()
    route the request through OpenRouter's proxy.

    Args:
        db: Database session
        tenant_id: Tenant UUID (may be None for anonymous)
        model_id: Normalized model ID (e.g. "openai/gpt-4o-mini")

    Returns:
        (api_key, effective_model_id) – api_key may be None if nothing found
    """
    provider_type = get_provider_from_model(model_id)

    # 1. Try tenant-configured key for the exact provider
    api_key = await resolve_llm_key(db, tenant_id, provider_type) if tenant_id else None
    if api_key:
        return api_key, model_id

    # 2. Fallback: env var for the exact provider
    settings = get_settings()
    env_keys = {
        "openai": settings.OPENAI_API_KEY,
        "anthropic": settings.ANTHROPIC_API_KEY,
        "openrouter": settings.OPENROUTER_API_KEY,
    }
    api_key = env_keys.get(provider_type)
    if api_key:
        return api_key, model_id

    # 3. Fallback: OpenRouter as universal proxy
    if provider_type != "openrouter":
        or_key = await resolve_llm_key(db, tenant_id, "openrouter") if tenant_id else None
        if not or_key:
            or_key = settings.OPENROUTER_API_KEY
        if or_key:
            effective = model_id if model_id.startswith("openrouter/") else f"openrouter/{model_id}"
            return or_key, effective

    return None, model_id


async def resolve_model_and_key(
    db: AsyncSession,
    tenant_id: Optional[UUID],
    api_keys: Optional[Dict[str, str]] = None,
    model_override: Optional[str] = None,
    project_default_model: Optional[str] = None,
    agent_default_model: Optional[str] = None,
) -> Tuple[str, str, str]:
    """
    Resolve the effective model, API key, and provider type for an agent invocation.

    Model cascade: model_override > project_default > tenant_default > agent_default

    Args:
        db: Database session
        tenant_id: Tenant UUID (may be None for anonymous)
        api_keys: Already-resolved provider key dict (from resolve_all_llm_keys).
                  If None, key lookup falls back to env vars only.
        model_override: Explicit per-request model override
        project_default_model: Project-level default model
        agent_default_model: Agent's own default model (self.model)

    Returns:
        (effective_model, api_key, provider_type)

    Raises:
        ValueError: if no API key can be found for the resolved model
    """
    # 1. Determine effective model via cascade
    effective_model = agent_default_model or settings.DEFAULT_LLM_MODEL
    if model_override:
        effective_model = normalize_model_id(model_override)
    elif project_default_model:
        effective_model = normalize_model_id(project_default_model)
    elif tenant_id:
        tenant_default = await resolve_tenant_default_model(db, tenant_id)
        if tenant_default:
            effective_model = normalize_model_id(tenant_default)

    # Ensure model is normalized
    effective_model = normalize_model_id(effective_model)
    provider_type = get_provider_from_model(effective_model)

    # 2. Resolve API key: api_keys dict first, then env var fallback
    resolved_keys = api_keys or {}
    api_key = resolved_keys.get(provider_type)

    if not api_key:
        # Try OpenRouter as universal fallback
        or_key = resolved_keys.get("openrouter")
        if not or_key:
            or_key = settings.OPENROUTER_API_KEY
        if or_key:
            api_key = or_key
            if not effective_model.startswith("openrouter/"):
                effective_model = f"openrouter/{effective_model}"
            # Re-derive provider after potential model mutation
            provider_type = get_provider_from_model(effective_model)
        else:
            # Last resort: bare env var keys
            api_key = settings.OPENAI_API_KEY or settings.ANTHROPIC_API_KEY

    if not api_key:
        raise ValueError("No API key configured. Add your key in Settings > AI Models.")

    return effective_model, api_key, provider_type


def create_litellm_model(
    model_id: str,
    api_key: str,
    api_base: Optional[str] = None,
    enable_thinking: bool = False,
):
    """
    Create a pydantic-ai compatible model that routes through LiteLLM.

    For pydantic-ai agents, we create an OpenAIModel with LiteLLM-compatible
    parameters. LiteLLM handles provider routing via the model prefix.

    Args:
        model_id: Model in LiteLLM format (e.g., "openai/gpt-4o", "anthropic/claude-3-5-sonnet")
        api_key: Decrypted API key for the provider
        api_base: Optional custom API base URL
        enable_thinking: Enable extended thinking for Anthropic models

    Returns:
        A pydantic-ai model instance
    """
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider
    from openai import AsyncOpenAI

    # Determine provider from model_id prefix
    provider = model_id.split("/")[0] if "/" in model_id else "openai"
    model_name = model_id.split("/", 1)[1] if "/" in model_id else model_id

    # Route to the correct provider
    if provider == "anthropic":
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.providers.anthropic import AnthropicProvider
        from anthropic import AsyncAnthropic
        client = AsyncAnthropic(
            api_key=api_key,
            **({"base_url": api_base} if api_base else {}),
        )
        return AnthropicModel(model_name, provider=AnthropicProvider(anthropic_client=client))
    elif provider == "openrouter":
        # OpenRouter uses OpenAI-compatible API
        client = AsyncOpenAI(
            api_key=api_key,
            base_url=api_base or settings.OPENROUTER_BASE_URL,
        )
        # model_name is what comes after "openrouter/" e.g. "openai/gpt-4o-mini"
        # Free-tier OpenRouter models may not support tool_choice='required' —
        # their provider endpoints often only handle 'auto'. Fall back gracefully.
        from pydantic_ai.models.openai import OpenAIModelProfile
        profile = OpenAIModelProfile(
            openai_supports_tool_choice_required=not model_name.endswith(":free"),
        )
        return OpenAIChatModel(model_name, provider=OpenAIProvider(openai_client=client), profile=profile)
    elif provider == "google":
        # Google models via pydantic-ai's Google model
        try:
            from pydantic_ai.models.google import GoogleModel
            from pydantic_ai.providers.google import GoogleProvider
            return GoogleModel(model_name, provider=GoogleProvider(api_key=api_key))
        except ImportError:
            # Fall back to OpenAI-compatible if Google model not available
            client = AsyncOpenAI(api_key=api_key)
            return OpenAIChatModel(model_name, provider=OpenAIProvider(openai_client=client))
    else:
        # OpenAI and other OpenAI-compatible providers
        client = AsyncOpenAI(
            api_key=api_key,
            **({"base_url": api_base} if api_base else {}),
        )
        return OpenAIChatModel(model_name, provider=OpenAIProvider(openai_client=client))


def normalize_model_id(model_id: str) -> str:
    """
    Normalize a model ID to LiteLLM canonical format: provider/model-name.

    Handles various input formats:
    - "gpt-4o" -> "openai/gpt-4o"
    - "openai:gpt-4o" -> "openai/gpt-4o"
    - "anthropic/claude-3-5-sonnet" -> "anthropic/claude-3-5-sonnet" (already normalized)
    - "google-gla:gemini-2.0-flash" -> "google/gemini-2.0-flash"
    - "claude-3-5-sonnet" -> "anthropic/claude-3-5-sonnet"

    Returns:
        Model ID in provider/model format
    """
    if not model_id:
        return settings.DEFAULT_LLM_MODEL

    # Already in provider/model format (e.g. "google/gemini-2.0-flash-exp:free",
    # "openai/gpt-4o", "openrouter/google/gemini-2.0-flash-exp").
    # The `:free` / `:extended` suffixes are valid OpenRouter pricing tiers
    # and must be preserved — they are part of the model identifier.
    if "/" in model_id:
        return model_id

    # Pydantic-ai format (provider:model) e.g. "openai:gpt-4o", "google-gla:gemini-2.0-flash"
    if ":" in model_id:
        provider, model = model_id.split(":", 1)
        # Normalize provider names
        provider = provider.replace("-gla", "")  # google-gla -> google
        return f"{provider}/{model}"

    # Bare model name - infer provider
    if model_id.startswith("gpt-") or model_id.startswith("o1") or model_id.startswith("o3"):
        return f"openai/{model_id}"
    elif model_id.startswith("claude"):
        return f"anthropic/{model_id}"
    elif model_id.startswith("gemini"):
        return f"google/{model_id}"
    else:
        # Default: assume OpenAI-compatible
        return f"openai/{model_id}"


def get_provider_from_model(model_id: str) -> str:
    """Extract provider type from a model ID."""
    normalized = normalize_model_id(model_id)
    return normalized.split("/")[0]


# ============================================================================
# Circuit Breaker Cache
# ============================================================================

# In-memory circuit breaker cache (per provider key)
_circuit_breakers: Dict[str, CircuitBreaker] = {}


def get_provider_circuit_breaker(key: str) -> CircuitBreaker:
    """Get or create circuit breaker for a provider."""
    if key not in _circuit_breakers:
        config = CircuitBreakerConfig(
            failure_threshold=3,
            success_threshold=2,
            timeout_seconds=30.0,
        )
        _circuit_breakers[key] = CircuitBreaker(key, config)
    return _circuit_breakers[key]


# ============================================================================
# LLM Gateway
# ============================================================================

class LLMGateway:
    """
    Centralized LLM Gateway with user-defined fallback chains.

    Features:
    - Tenant-specific provider configuration from Settings
    - Support for custom endpoints (vLLM, Ollama, Azure)
    - Circuit breaker per provider (reuses redis_ha.py pattern)
    - Automatic fallback on failure
    - Usage logging for cost tracking
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def complete(
        self,
        request: LLMRequest,
        tenant_id: UUID,
        user_id: Optional[UUID] = None,
    ) -> LLMResponse:
        """
        Execute LLM completion with automatic failover.

        Tries each provider in the tenant's fallback chain until one succeeds.
        """
        providers = await get_tenant_providers(self.db, tenant_id)

        if not providers:
            raise AllProvidersFailedError(
                "No LLM providers configured. Add providers in Settings.",
                errors=["No providers configured"],
            )

        errors: List[str] = []

        for provider in providers:
            # Check circuit breaker
            circuit_key = f"llm:{tenant_id}:{provider.name}"
            circuit = get_provider_circuit_breaker(circuit_key)

            if not await circuit.can_execute():
                error_msg = f"{provider.name}: circuit breaker open"
                errors.append(error_msg)
                logger.info(f"Skipping provider: {error_msg}")
                continue

            start_time = time.time()

            try:
                # Build LiteLLM call parameters
                litellm_params = self._build_litellm_params(request, provider)

                # Execute call with retry on transient errors
                # Retry wraps only this single provider attempt, not the outer fallback chain.
                # 400/401/403 = permanent errors, fail immediately.
                # 429/500/503/timeout/network = transient, retry with exponential backoff + jitter.
                _MAX_LLM_RETRIES = 3
                for _retry_attempt in range(_MAX_LLM_RETRIES + 1):
                    try:
                        response = await litellm.acompletion(**litellm_params)
                        break  # Success
                    except Exception as _retry_exc:
                        _status = getattr(_retry_exc, 'status_code', getattr(_retry_exc, 'code', None))
                        # Don't retry client errors — they won't succeed on retry
                        if _status in (400, 401, 403):
                            raise
                        if _retry_attempt == _MAX_LLM_RETRIES:
                            raise  # Exhausted retries, propagate to outer handler
                        _wait = min(2 ** _retry_attempt + random.random(), 30)
                        logger.warning(
                            f"LLM call to {provider.name} failed (attempt {_retry_attempt + 1}/{_MAX_LLM_RETRIES}), "
                            f"retrying in {_wait:.1f}s: {_retry_exc}"
                        )
                        await asyncio.sleep(_wait)

                # Record success
                await circuit.record_success()

                # Log cache stats for monitoring
                if settings.USE_PROVIDER_CACHING:
                    self._log_cache_stats(response, provider.provider_type)

                latency_ms = int((time.time() - start_time) * 1000)

                # Calculate cost using LiteLLM's built-in function
                try:
                    cost = litellm.completion_cost(completion_response=response)
                except Exception:
                    # Fallback: estimate based on tokens
                    cost = self._estimate_cost(
                        response.usage.prompt_tokens,
                        response.usage.completion_tokens,
                        response.model,
                    )

                # Log usage asynchronously (don't block response)
                await self._log_usage(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    provider=provider,
                    request=request,
                    response=response,
                    cost=cost,
                    latency_ms=latency_ms,
                )

                # Extract tool calls if present
                raw_tool_calls = response.choices[0].message.tool_calls
                tool_calls = [
                    ToolCallResponse(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=tc.function.arguments,
                    )
                    for tc in raw_tool_calls
                ] if raw_tool_calls else None

                return LLMResponse(
                    content=response.choices[0].message.content or "",
                    model=response.model,
                    provider_name=provider.name,
                    input_tokens=response.usage.prompt_tokens,
                    output_tokens=response.usage.completion_tokens,
                    cost_usd=cost,
                    latency_ms=latency_ms,
                    tool_calls=tool_calls,
                )

            except Exception as e:
                error_msg = f"{provider.name}: {str(e)}"
                errors.append(error_msg)
                logger.warning(f"LLM call failed: {error_msg}")

                # Record failure for circuit breaker
                await circuit.record_failure(e)

                # Continue to next provider
                continue

        # All providers failed
        raise AllProvidersFailedError(
            f"All {len(providers)} providers failed",
            errors=errors,
        )

    async def stream(
        self,
        request: LLMRequest,
        tenant_id: UUID,
        user_id: UUID | None = None,
    ) -> AsyncGenerator[LLMStreamChunk, None]:
        """Stream LLM completion with automatic failover."""
        providers = await get_tenant_providers(self.db, tenant_id)

        if not providers:
            raise AllProvidersFailedError(
                "No LLM providers configured.",
                errors=["No providers configured"],
            )

        errors: list[str] = []

        for provider in providers:
            circuit_key = f"llm:{tenant_id}:{provider.name}"
            circuit = get_provider_circuit_breaker(circuit_key)

            if not await circuit.can_execute():
                errors.append(f"{provider.name}: circuit breaker open")
                continue

            try:
                litellm_params = self._build_litellm_params(request, provider)
                litellm_params["stream"] = True

                response = await litellm.acompletion(**litellm_params)
                await circuit.record_success()

                async for chunk in response:
                    delta = chunk.choices[0].delta
                    if delta.content:
                        yield LLMStreamChunk(content=delta.content)
                    if chunk.choices[0].finish_reason:
                        yield LLMStreamChunk(
                            content="",
                            finish_reason=chunk.choices[0].finish_reason,
                        )
                return  # Stream complete — exit provider loop

            except Exception as e:
                errors.append(f"{provider.name}: {str(e)}")
                logger.warning(f"LLM stream failed: {provider.name}: {e}")
                await circuit.record_failure(e)
                continue

        raise AllProvidersFailedError(
            f"All {len(providers)} providers failed for stream",
            errors=errors,
        )

    def _build_litellm_params(
        self,
        request: LLMRequest,
        provider: ProviderConfig,
    ) -> dict[str, Any]:
        """Build parameters for LiteLLM call."""
        params: dict[str, Any] = {
            "messages": request.messages,
            "model": request.model,
            "temperature": request.temperature,
            "api_key": provider.api_key,
        }

        if request.max_tokens:
            params["max_tokens"] = request.max_tokens

        # Pass tools and tool_choice from request
        if request.tools:
            params["tools"] = request.tools
        if request.tool_choice:
            params["tool_choice"] = request.tool_choice

        # Custom endpoint URL
        if provider.api_base:
            params["api_base"] = provider.api_base

        # Provider-specific model prefixes for LiteLLM
        model = request.model
        if provider.provider_type == "openrouter" and not model.startswith("openrouter/"):
            params["model"] = f"openrouter/{model}"
        elif provider.provider_type == "anthropic" and not model.startswith("anthropic/"):
            # LiteLLM requires anthropic/ prefix for Anthropic models
            if model.startswith("claude"):
                params["model"] = f"anthropic/{model}"

        # Apply provider-native caching for Anthropic
        if settings.USE_PROVIDER_CACHING and provider.provider_type == "anthropic":
            anthropic_params = self._prepare_anthropic_request(
                messages=request.messages,
                tools=request.tools,
            )
            # Update messages with filtered version (system prompt extracted)
            params["messages"] = anthropic_params["messages"]
            # Add system prompt as separate parameter if present
            if anthropic_params["system"]:
                params["system"] = anthropic_params["system"]
            # Add cached tools if present
            if anthropic_params["tools"]:
                params["tools"] = anthropic_params["tools"]

        return params

    def _estimate_cost(
        self,
        input_tokens: int,
        output_tokens: int,
        model: str,
    ) -> float:
        """Fallback cost estimation when LiteLLM can't calculate."""
        # Conservative estimates per 1M tokens
        cost_per_1m = {
            "gpt-4o": {"input": 2.5, "output": 10.0},
            "gpt-4o-mini": {"input": 0.15, "output": 0.6},
            "claude-3-5-sonnet": {"input": 3.0, "output": 15.0},
            "claude-3-haiku": {"input": 0.25, "output": 1.25},
        }

        # Find matching model or use default
        model_lower = model.lower()
        pricing = None
        for key, prices in cost_per_1m.items():
            if key in model_lower:
                pricing = prices
                break

        if not pricing:
            pricing = {"input": 1.0, "output": 2.0}  # Default conservative

        input_cost = (input_tokens / 1_000_000) * pricing["input"]
        output_cost = (output_tokens / 1_000_000) * pricing["output"]

        return input_cost + output_cost

    # ========================================================================
    # Provider-Native Prompt Caching (P2 Cost Optimization)
    # ========================================================================

    def _extract_system_prompt(
        self, messages: List[Dict[str, Any]]
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """Extract system prompt from messages for Anthropic API.

        Anthropic requires system prompts as a separate top-level parameter,
        not embedded in the messages array. This extracts the first system
        message and returns remaining messages.

        Returns:
            Tuple of (system_prompt, filtered_messages)
        """
        system_prompt = ""
        filtered_messages = []

        for msg in messages:
            if msg.get("role") == "system":
                # Concatenate multiple system messages (rare but possible)
                system_prompt += msg.get("content", "") + "\n"
            else:
                filtered_messages.append(msg)

        return system_prompt.strip(), filtered_messages

    def _prepare_anthropic_request(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Add cache_control markers for Anthropic prompt caching.

        Anthropic's prompt caching can save up to 90% on cached tokens.
        This method adds the required cache_control markers to system
        prompts and tool definitions.
        """
        # Extract system prompt from messages
        system_prompt, filtered_messages = self._extract_system_prompt(messages)

        # Cache system prompt (90% savings on repeated calls)
        system = None
        if system_prompt:
            system = [{
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"}
            }]

        # Anthropic requires cache_control on the LAST item in each cacheable block.
        # Only the last tool needs the marker - all preceding tools are implicitly cached.
        # See: https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching
        cached_tools = None
        if tools:
            cached_tools = list(tools)  # Create new list to avoid mutating caller's tools
            cached_tools[-1] = {
                **cached_tools[-1],
                "cache_control": {"type": "ephemeral"}
            }

        return {
            "system": system,
            "messages": filtered_messages,
            "tools": cached_tools,
        }

    def _log_cache_stats(self, response: Any, provider: str) -> None:
        """Log cache performance for monitoring."""
        if provider == "anthropic":
            usage = response.usage
            cache_read = getattr(usage, 'cache_read_input_tokens', None)

            if cache_read is None:
                logger.debug(
                    "Anthropic response missing cache_read_input_tokens - "
                    "caching may not be enabled"
                )
                return

            total_input = usage.input_tokens
            hit_rate = cache_read / total_input if total_input > 0 else 0

            logger.info(
                f"Anthropic cache: hit_rate={hit_rate:.1%} "
                f"cached={cache_read} total={total_input}"
            )

        elif provider == "openai":
            usage = response.usage
            details = getattr(usage, 'prompt_tokens_details', None)
            if details:
                cached = getattr(details, 'cached_tokens', 0)
                logger.info(
                    f"OpenAI cache: cached={cached} total={usage.prompt_tokens}"
                )

    def _hash_prompt(self, messages: List[Dict[str, Any]]) -> str:
        """Create deterministic hash of messages for cache key.

        Uses SHA-256 for collision resistance. Messages are serialized with
        sorted keys to ensure consistent hashing regardless of dict ordering.

        Returns:
            First 16 characters of SHA-256 hash (sufficient for cache keys)
        """
        # Sort dict keys for deterministic ordering
        normalized = json.dumps(messages, sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(normalized.encode()).hexdigest()[:16]

    async def _log_usage(
        self,
        tenant_id: UUID,
        user_id: Optional[UUID],
        provider: ProviderConfig,
        request: LLMRequest,
        response: Any,
        cost: float,
        latency_ms: int,
    ) -> None:
        """Log usage record to database."""
        from app.models.llm_usage import LLMUsageRecord

        # Extract cache stats per provider
        cache_read = 0
        cache_creation = 0

        match provider.provider_type:
            case "anthropic":
                cache_read = getattr(response.usage, 'cache_read_input_tokens', 0) or 0
                cache_creation = getattr(response.usage, 'cache_creation_input_tokens', 0) or 0
            case "openai":
                details = getattr(response.usage, 'prompt_tokens_details', None)
                if details:
                    cache_read = getattr(details, 'cached_tokens', 0) or 0
            case _:
                pass

        record = LLMUsageRecord(
            id=uuid4(),
            tenant_id=tenant_id,
            user_id=user_id,
            provider_id=provider.id,
            provider_name=provider.name,
            model=response.model,
            input_tokens=response.usage.prompt_tokens,
            output_tokens=response.usage.completion_tokens,
            cost_usd=cost,
            latency_ms=latency_ms,
            workflow_id=request.workflow_id,
            node_id=request.node_id,
            agent_id=request.agent_id,
            cache_creation_tokens=cache_creation,
            cache_read_tokens=cache_read,
            status="success",
        )

        self.db.add(record)
        # Commit happens with the request transaction

        # Langfuse integration (fire-and-forget, feature-flagged)
        if settings.LANGFUSE_ENABLED:
            from app.services.langfuse_integration import trace_to_langfuse
            trace_to_langfuse(
                call_id=str(record.id),
                provider=provider.provider_type,
                model=response.model,
                input_tokens=response.usage.prompt_tokens,
                output_tokens=response.usage.completion_tokens,
                latency_ms=latency_ms,
                status="success",
                workflow_id=str(request.workflow_id) if request.workflow_id else None,
                agent_type=request.agent_id,
            )


# ============================================================================
# Helper Functions
# ============================================================================

async def verify_provider_connection(
    provider_type: str,
    api_base: Optional[str],
    api_key: str,
    model: str = "gpt-4o-mini",
) -> Dict[str, Any]:
    """
    Test connection to an LLM provider.

    Used by Settings UI to verify provider configuration works.
    """
    try:
        params = {
            "messages": [{"role": "user", "content": "Say 'ok'"}],
            "model": model,
            "api_key": api_key,
            "max_tokens": 5,
        }

        if api_base:
            params["api_base"] = api_base

        if provider_type == "openrouter" and not model.startswith("openrouter/"):
            params["model"] = f"openrouter/{model}"
        elif provider_type == "anthropic" and model.startswith("claude"):
            params["model"] = f"anthropic/{model}"

        response = await litellm.acompletion(**params)

        return {
            "success": True,
            "model": response.model,
            "message": "Connection successful",
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e),
        }


async def get_default_provider(
    db: AsyncSession,
    tenant_id: UUID,
) -> ProviderConfig | None:
    """Get the default (highest priority) provider for a tenant."""
    providers = await get_tenant_providers(db, tenant_id)
    return providers[0] if providers else None


# ============================================================================
# Model Tier Resolution (Deep Work Engine)
# ============================================================================

MODEL_FAMILY_TIERS: dict[str, dict[str, str]] = {
    "anthropic": {"high": "anthropic/claude-opus-4-5", "mid": "anthropic/claude-sonnet-4-5", "low": "anthropic/claude-haiku-4-5"},
    "openai": {"high": "openai/gpt-4o", "mid": "openai/gpt-4o-mini", "low": "openai/gpt-4o-mini"},
    "openrouter": {"high": "openrouter/anthropic/claude-sonnet-4-5", "mid": "openrouter/google/gemini-2.5-flash", "low": "openrouter/anthropic/claude-haiku-4-5"},
}


async def resolve_model_for_tier(
    db: AsyncSession,
    tenant_id: UUID,
    tier: Literal["high", "mid", "low"],
    agent_config: dict | None = None,
) -> str:
    """Resolve the best available model for a given tier."""
    # 1. Agent Studio explicit config
    if agent_config and (models := agent_config.get("deep_work_models")):
        if model := models.get(tier):
            return model

    # 2. Infer from tenant's primary model family
    primary = await resolve_tenant_default_model(db, tenant_id)
    if primary:
        family = get_provider_from_model(primary)
        if family_tiers := MODEL_FAMILY_TIERS.get(family):
            return family_tiers.get(tier, primary)

    # 3. Infer from tenant's provider type (e.g. deepseek model on openrouter)
    providers = await _get_cached_tenant_providers(db, tenant_id)
    if providers:
        provider_type = providers[0].provider_type
        if family_tiers := MODEL_FAMILY_TIERS.get(provider_type):
            return family_tiers.get(tier, get_settings().DEFAULT_LLM_MODEL)

    # 4. System fallback
    return get_settings().DEFAULT_LLM_MODEL


async def resolve_model_for_policy(
    db: AsyncSession,
    tenant_id: UUID,
    task_type: str,
    output_type: str | None = None,
    agent_config: dict | None = None,
) -> str:
    """Resolve the best model for a task type using routing policies.

    Resolution order:
    1. Agent Studio override (agent_config["deep_work_models"][task_type])
    2. RoutingPolicy exact match (task_type + output_type)
    3. RoutingPolicy task-type match (task_type, output_type=NULL)
    4. Fallback to resolve_model_for_tier("mid")
    """
    # 1. Agent Studio explicit override
    if agent_config and (models := agent_config.get("deep_work_models")):
        if model := models.get(task_type):
            return model

    # 2-3. RoutingPolicy lookup
    try:
        from app.models.routing_policy import RoutingPolicy

        # Try exact match first (task_type + output_type)
        if output_type:
            result = await db.execute(
                select(RoutingPolicy)
                .where(
                    RoutingPolicy.tenant_id == tenant_id,
                    RoutingPolicy.task_type == task_type,
                    RoutingPolicy.output_type == output_type,
                    RoutingPolicy.is_active == True,  # noqa: E712
                )
                .order_by(RoutingPolicy.priority.desc())
                .limit(1)
            )
            policy = result.scalar_one_or_none()
            if policy and policy.model_id != "auto":
                return policy.model_id

        # Fall back to task_type-only match
        result = await db.execute(
            select(RoutingPolicy)
            .where(
                RoutingPolicy.tenant_id == tenant_id,
                RoutingPolicy.task_type == task_type,
                RoutingPolicy.output_type.is_(None),
                RoutingPolicy.is_active == True,  # noqa: E712
            )
            .order_by(RoutingPolicy.priority.desc())
            .limit(1)
        )
        policy = result.scalar_one_or_none()
        if policy and policy.model_id != "auto":
            return policy.model_id
    except Exception:
        logger.warning("RoutingPolicy lookup failed, falling back to tier", exc_info=True)

    # 4. Fallback to mid tier
    return await resolve_model_for_tier(db, tenant_id, "mid", agent_config)


# ============================================================================
# Engine model policy helpers (internal API; no LiteLLM loop is touched)
# ============================================================================

async def get_tenant_own_key_providers(
    db: AsyncSession,
    tenant_id: UUID,
) -> List[ProviderConfig]:
    """Enabled providers whose key is the tenant's own stored key, ordered by priority.

    Unlike get_tenant_providers this never falls back to platform environment keys and
    bypasses the in-memory cache, so a revoked key stops working immediately.
    """
    from app.core.database import set_tenant_context

    await set_tenant_context(db, str(tenant_id))
    result = await db.execute(
        select(LLMProvider)
        .where(LLMProvider.tenant_id == tenant_id, LLMProvider.is_enabled == True)  # noqa: E712
        .order_by(LLMProvider.priority)
    )
    encryption = get_encryption_service()
    configs: List[ProviderConfig] = []
    for p in result.scalars().all():
        if not p.api_key_encrypted:
            continue
        try:
            api_key = encryption.decrypt(p.api_key_encrypted)
        except Exception:
            logger.warning("Failed to decrypt API key for provider %s", p.name)
            continue
        if api_key:
            configs.append(ProviderConfig(
                id=p.id, name=p.name, provider_type=p.provider_type, api_base=p.api_base,
                api_key=api_key, default_model=p.default_model, priority=p.priority or 0,
                workflow_builder_model=p.workflow_builder_model,
            ))
    return configs


def strip_provider_prefix(model_id: str) -> str:
    """'anthropic/claude-sonnet-4-5' -> 'claude-sonnet-4-5' (the bare id a provider adapter expects)."""
    normalized = normalize_model_id(model_id)
    return normalized.split("/", 1)[1] if "/" in normalized else normalized
