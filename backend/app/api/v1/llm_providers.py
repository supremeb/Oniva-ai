"""LLM Provider configuration API.

Phase 2: LLM Reliability - Provider Settings

CRUD operations for tenant-specific LLM provider configuration.
Supports built-in providers (OpenAI, Anthropic, OpenRouter) and
custom endpoints (vLLM, Ollama, Azure OpenAI).
"""

import asyncio
from typing import List, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.encryption import get_encryption_service
from app.auth.dependencies import get_current_user, get_current_tenant_id, require_role
from app.auth.providers.base import AuthUser
from app.models.llm_provider import LLMProvider
from app.config import get_settings
from app.security.url_validator import is_url_safe
from app.services.llm_gateway import verify_provider_connection, normalize_model_id

router = APIRouter()

# Provider rows hold the tenant's billing key: every write needs owner/admin.
_admin = Depends(require_role("owner", "admin"))


# ============================================================================
# Schemas
# ============================================================================

class ProviderCreate(BaseModel):
    """Create a new LLM provider."""
    name: str
    display_name: str
    provider_type: str  # "openai", "anthropic", "openrouter", "azure", "custom"
    api_base: Optional[str] = None
    api_key: Optional[str] = None
    models: List[str] = []
    default_model: Optional[str] = None
    embedding_model: Optional[str] = None
    workflow_builder_model: Optional[str] = None
    priority: int = 0
    is_enabled: bool = True


class ProviderUpdate(BaseModel):
    """Update an existing LLM provider."""
    display_name: Optional[str] = None
    api_base: Optional[str] = None
    api_key: Optional[str] = None  # Only update if provided
    models: Optional[List[str]] = None
    default_model: Optional[str] = None
    embedding_model: Optional[str] = None
    workflow_builder_model: Optional[str] = None
    priority: Optional[int] = None
    is_enabled: Optional[bool] = None


class ProviderResponse(BaseModel):
    """LLM provider response (excludes API key)."""
    id: UUID
    name: str
    display_name: str
    provider_type: str
    api_base: Optional[str]
    has_api_key: bool
    models: List[str]
    default_model: Optional[str]
    embedding_model: Optional[str]
    workflow_builder_model: Optional[str]
    priority: int
    is_enabled: bool
    is_default: bool

    class Config:
        from_attributes = True


class ProviderTestRequest(BaseModel):
    """Request to test provider connection."""
    provider_type: str
    api_base: Optional[str] = None
    api_key: str
    model: str = "gpt-4o-mini"


class ProviderTestResponse(BaseModel):
    """Result of provider connection test."""
    success: bool
    model: Optional[str] = None
    message: Optional[str] = None
    error: Optional[str] = None


class ProviderListResponse(BaseModel):
    """List of providers with total count."""
    providers: List[ProviderResponse]
    total: int


# ============================================================================
# Endpoints
# ============================================================================

@router.get("/llm-providers", response_model=ProviderListResponse)
async def list_providers(
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """
    List all LLM providers for current tenant.

    Providers are returned ordered by priority (lower values first).
    """
    stmt = (
        select(LLMProvider)
        .where(LLMProvider.tenant_id == tenant_id)
        .order_by(LLMProvider.priority)
    )
    result = await db.execute(stmt)
    providers = result.scalars().all()

    responses = [
        ProviderResponse(
            id=p.id,
            name=p.name,
            display_name=p.display_name,
            provider_type=p.provider_type,
            api_base=p.api_base,
            has_api_key=bool(p.api_key_encrypted),
            models=p.models or [],
            default_model=p.default_model,
            embedding_model=p.embedding_model,
            workflow_builder_model=p.workflow_builder_model,
            priority=p.priority,
            is_enabled=p.is_enabled,
            is_default=p.is_default,
        )
        for p in providers
    ]

    return ProviderListResponse(providers=responses, total=len(responses))


@router.post("/llm-providers", response_model=ProviderResponse, status_code=status.HTTP_201_CREATED, dependencies=[_admin])
async def create_provider(
    data: ProviderCreate,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """
    Add a new LLM provider.

    The API key will be encrypted before storage.
    Provider name must be unique within the tenant.
    """
    # Check for duplicate name
    stmt = select(LLMProvider).where(
        LLMProvider.tenant_id == tenant_id,
        LLMProvider.name == data.name,
    )
    result = await db.execute(stmt)
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Provider '{data.name}' already exists",
        )

    # Encrypt API key if provided
    encrypted_key = None
    if data.api_key:
        encryption = get_encryption_service()
        encrypted_key = encryption.encrypt(data.api_key)

    # Normalize model IDs to canonical provider/model format
    normalized_models = [normalize_model_id(m) for m in data.models] if data.models else []
    normalized_default_model = normalize_model_id(data.default_model) if data.default_model else None
    normalized_embedding_model = normalize_model_id(data.embedding_model) if data.embedding_model else None
    normalized_workflow_builder_model = normalize_model_id(data.workflow_builder_model) if data.workflow_builder_model else None

    # Create provider
    provider = LLMProvider(
        id=uuid4(),
        tenant_id=tenant_id,
        name=data.name,
        display_name=data.display_name,
        provider_type=data.provider_type,
        api_base=data.api_base,
        api_key_encrypted=encrypted_key,
        models=normalized_models,
        default_model=normalized_default_model,
        embedding_model=normalized_embedding_model,
        workflow_builder_model=normalized_workflow_builder_model,
        priority=data.priority,
        is_enabled=data.is_enabled,
    )

    db.add(provider)
    await db.flush()

    response = ProviderResponse(
        id=provider.id,
        name=provider.name,
        display_name=provider.display_name,
        provider_type=provider.provider_type,
        api_base=provider.api_base,
        has_api_key=bool(provider.api_key_encrypted),
        models=provider.models or [],
        default_model=provider.default_model,
        embedding_model=provider.embedding_model,
        workflow_builder_model=provider.workflow_builder_model,
        priority=provider.priority,
        is_enabled=provider.is_enabled,
        is_default=provider.is_default,
    )

    await db.commit()
    return response


class BrowseModelsResponse(BaseModel):
    """Available model from a provider's catalog."""
    id: str
    name: str
    context_length: Optional[int] = None
    pricing_prompt: Optional[str] = None
    pricing_completion: Optional[str] = None


@router.get("/llm-providers/browse-models", dependencies=[_admin])
async def browse_available_models(
    provider_type: str = "openrouter",
    user: AuthUser = Depends(get_current_user),
):
    """
    Fetch available models from a provider's catalog.

    Currently supports OpenRouter (public API).
    Returns model IDs, names, context lengths, and pricing.
    """
    if provider_type != "openrouter":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Browse not supported for provider '{provider_type}'. Only 'openrouter' is supported.",
        )

    import httpx

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get("https://openrouter.ai/api/v1/models")
            resp.raise_for_status()
            data = resp.json()

        models = []
        for m in data.get("data", []):
            model_id = m.get("id", "")
            name = m.get("name", model_id)
            ctx = m.get("context_length")
            pricing = m.get("pricing", {})
            prompt_price = pricing.get("prompt")
            completion_price = pricing.get("completion")

            models.append(BrowseModelsResponse(
                id=model_id,
                name=name,
                context_length=ctx,
                pricing_prompt=prompt_price,
                pricing_completion=completion_price,
            ))

        # Sort by name
        models.sort(key=lambda x: x.name.lower())
        return {"models": [m.model_dump() for m in models], "total": len(models)}

    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to fetch models from OpenRouter: {str(e)}",
        )


@router.get("/llm-providers/{provider_id}", response_model=ProviderResponse)
async def get_provider(
    provider_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """Get a specific LLM provider."""
    stmt = select(LLMProvider).where(
        LLMProvider.id == provider_id,
        LLMProvider.tenant_id == tenant_id,
    )
    result = await db.execute(stmt)
    provider = result.scalar_one_or_none()

    if not provider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Provider not found",
        )

    return ProviderResponse(
        id=provider.id,
        name=provider.name,
        display_name=provider.display_name,
        provider_type=provider.provider_type,
        api_base=provider.api_base,
        has_api_key=bool(provider.api_key_encrypted),
        models=provider.models or [],
        default_model=provider.default_model,
        embedding_model=provider.embedding_model,
        workflow_builder_model=provider.workflow_builder_model,
        priority=provider.priority,
        is_enabled=provider.is_enabled,
        is_default=provider.is_default,
    )


@router.patch("/llm-providers/{provider_id}", response_model=ProviderResponse, dependencies=[_admin])
async def update_provider(
    provider_id: UUID,
    data: ProviderUpdate,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """Update an LLM provider."""
    stmt = select(LLMProvider).where(
        LLMProvider.id == provider_id,
        LLMProvider.tenant_id == tenant_id,
    )
    result = await db.execute(stmt)
    provider = result.scalar_one_or_none()

    if not provider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Provider not found",
        )

    # Update fields
    if data.display_name is not None:
        provider.display_name = data.display_name
    if data.api_base is not None:
        provider.api_base = data.api_base if data.api_base else None
    if data.api_key is not None:
        encryption = get_encryption_service()
        provider.api_key_encrypted = encryption.encrypt(data.api_key)
    if data.models is not None:
        provider.models = [normalize_model_id(m) for m in data.models]
    if data.default_model is not None:
        provider.default_model = normalize_model_id(data.default_model)
    if data.embedding_model is not None:
        provider.embedding_model = normalize_model_id(data.embedding_model) if data.embedding_model else None
    if data.workflow_builder_model is not None:
        provider.workflow_builder_model = normalize_model_id(data.workflow_builder_model) if data.workflow_builder_model else None
    if data.priority is not None:
        provider.priority = data.priority
    if data.is_enabled is not None:
        provider.is_enabled = data.is_enabled

    await db.flush()

    response = ProviderResponse(
        id=provider.id,
        name=provider.name,
        display_name=provider.display_name,
        provider_type=provider.provider_type,
        api_base=provider.api_base,
        has_api_key=bool(provider.api_key_encrypted),
        models=provider.models or [],
        default_model=provider.default_model,
        embedding_model=provider.embedding_model,
        workflow_builder_model=provider.workflow_builder_model,
        priority=provider.priority,
        is_enabled=provider.is_enabled,
        is_default=provider.is_default,
    )

    await db.commit()
    return response


@router.delete("/llm-providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[_admin])
async def delete_provider(
    provider_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """Delete an LLM provider."""
    stmt = delete(LLMProvider).where(
        LLMProvider.id == provider_id,
        LLMProvider.tenant_id == tenant_id,
    )
    result = await db.execute(stmt)

    if result.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Provider not found",
        )

    await db.commit()


@router.post("/llm-providers/test", response_model=ProviderTestResponse, dependencies=[_admin])
async def test_provider(
    data: ProviderTestRequest,
    user: AuthUser = Depends(get_current_user),
):
    """
    Test connection to an LLM provider.

    Makes a minimal API call to verify the configuration works.
    Loopback / private / link-local / metadata hosts are rejected unless
    LLM_PROVIDER_ALLOW_PRIVATE_URLS is enabled.
    """
    if data.api_base and not get_settings().LLM_PROVIDER_ALLOW_PRIVATE_URLS:
        blocked = await asyncio.to_thread(is_url_safe, data.api_base)
        if blocked:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"api_base is not allowed: {blocked}",
            )
    result = await verify_provider_connection(
        provider_type=data.provider_type,
        api_base=data.api_base,
        api_key=data.api_key,
        model=data.model,
    )

    return ProviderTestResponse(**result)


@router.post("/llm-providers/reorder", status_code=status.HTTP_200_OK, dependencies=[_admin])
async def reorder_providers(
    provider_ids: List[UUID],
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """
    Reorder providers by setting priority based on list order.

    The first provider in the list will have priority 0 (highest),
    the second will have priority 1, etc.
    """
    for priority, provider_id in enumerate(provider_ids):
        stmt = (
            update(LLMProvider)
            .where(
                LLMProvider.id == provider_id,
                LLMProvider.tenant_id == tenant_id,
            )
            .values(priority=priority)
        )
        await db.execute(stmt)

    await db.commit()

    return {"status": "reordered", "count": len(provider_ids)}
