"""
Agent Generation API — SSE endpoint for chat-first agent creation (Phase A).

POST /api/v1/agents/generate -> SSE stream of agent configuration events.
POST /api/v1/agents/clarify  -> Clarifying questions before generation.
"""
import logging
from typing import AsyncGenerator
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.auth.dependencies import get_current_user_id, get_current_tenant_id
from app.config import get_settings
from app.core.database import get_db, get_db_streaming
from app.schemas.agent_generation import (
    GenerateAgentRequest,
    ClarifyRequest,
    ClarifyResponse,
)
from app.services.agent_generator import AgentGeneratorService
from app.services.llm_gateway import resolve_effective_provider

logger = logging.getLogger(__name__)

router = APIRouter()


def _resolve_provider_or_fallback(provider):
    """Extract provider fields, or fall back to env vars for bootstrap."""
    if provider:
        return provider.api_key, provider.model, provider.provider_type, provider.api_base

    # Bootstrap fallback — only used before any providers are configured
    settings = get_settings()
    api_key = settings.OPENROUTER_API_KEY
    if not api_key:
        return None, None, None, None
    return api_key, settings.DEFAULT_LLM_MODEL or "openai/gpt-4o", "openrouter", settings.OPENROUTER_BASE_URL


@router.post("/clarify", response_model=ClarifyResponse)
async def clarify_agent(
    body: ClarifyRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    user_id: UUID = Depends(get_current_user_id),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """Generate clarifying questions before agent creation."""
    provider = await resolve_effective_provider(db, tenant_id)
    api_key, model, provider_type, api_base = _resolve_provider_or_fallback(provider)

    if not api_key:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=400,
            detail="No LLM provider configured. Please add one in Settings > AI Models.",
        )

    service = AgentGeneratorService(db)
    return await service.generate_clarifying_questions(
        description=body.description,
        context=body.context,
        api_key=api_key,
        model=model,
        provider_type=provider_type,
        api_base=api_base,
    )


@router.post("/generate")
async def generate_agent(
    request: Request,
    body: GenerateAgentRequest,
    # Streaming route: the SSE generator keeps using this session.
    db: AsyncSession = Depends(get_db_streaming),
    user_id: UUID = Depends(get_current_user_id),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """Generate an agent configuration from a natural language description via SSE."""
    # Pre-fetch ALL provider values while RLS tenant context is still active.
    # The SSE generator outlives the request's DB session — resolved values
    # must be captured as plain scalars before the stream() closure begins.
    provider = await resolve_effective_provider(db, tenant_id)
    prefetched_key, prefetched_model, prefetched_type, prefetched_base = _resolve_provider_or_fallback(provider)

    service = AgentGeneratorService(db)

    async def stream() -> AsyncGenerator[dict, None]:
        if not prefetched_key:
            yield {
                "event": "generation_error",
                "data": '{"message": "No LLM provider configured. Please add one in Settings > AI Models."}',
            }
            return

        async for event in service.generate_agent_stream(
            description=body.description,
            tenant_id=tenant_id,
            api_key=prefetched_key,
            model=prefetched_model,
            provider_type=prefetched_type,
            api_base=prefetched_base,
            clarification_context=body.clarification_context,
        ):
            # Check client disconnect
            if await request.is_disconnected():
                return
            yield event

    return EventSourceResponse(
        stream(),
        media_type="text/event-stream",
        ping=15,
        send_timeout=30.0,
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
