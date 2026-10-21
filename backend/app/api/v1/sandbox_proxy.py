"""Sandbox LLM Proxy — OpenAI-compatible endpoint for sandbox token auth.

Sandboxes set OPENAI_BASE_URL to point here. ChatOpenAI sends requests
with the opaque sandbox token as the Bearer token. We validate, forward
via LLMGateway, and return an OpenAI-format response.
"""

import hashlib
import logging
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.database import get_db
from app.schemas.sandbox_proxy import (
    SandboxProxyChatChoice,
    SandboxProxyChatMessage,
    SandboxProxyChatRequest,
    SandboxProxyChatResponse,
    SandboxProxyChatUsage,
)
from app.services.llm_gateway import LLMGateway, LLMRequest
from app.services.sandbox_executor import _MAX_COST_PER_EXECUTION
from app.services.sandbox_tokens import (
    increment_sandbox_token_usage,
    validate_sandbox_token,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sandbox-proxy")


def _bare(model: str) -> str:
    return model.rsplit("/", 1)[-1].strip().lower()


def _model_allowed(model: str) -> bool:
    """Allow-list from SANDBOX_ALLOWED_MODELS (default: DEFAULT_LLM_MODEL only)."""
    settings = get_settings()
    raw = (getattr(settings, "SANDBOX_ALLOWED_MODELS", "") or "").strip()
    allowed = [m.strip() for m in raw.split(",") if m.strip()] or [settings.DEFAULT_LLM_MODEL]
    wanted = model.strip().lower()
    return any(wanted == a.lower() or _bare(wanted) == _bare(a) for a in allowed)


async def _extract_proxy_token(request: Request) -> str:
    """Extract sandbox token from Authorization header."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer sbx_tok_"):
        raise HTTPException(status_code=401, detail="Invalid proxy token")
    return auth.removeprefix("Bearer ")


@router.post("/v1/chat/completions", response_model=SandboxProxyChatResponse)
async def proxy_chat_completions(
    body: SandboxProxyChatRequest,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """OpenAI-compatible proxy for sandbox LLM calls."""
    # 1. Extract and validate token
    token = await _extract_proxy_token(request)
    meta = await validate_sandbox_token(token)
    if meta is None:
        raise HTTPException(status_code=401, detail="Expired or revoked token")

    # 2. Model allow-list (sandbox code must not pick arbitrary/expensive models)
    if not _model_allowed(body.model):
        logger.warning("SANDBOX_PROXY_DENIED reason=model model=%s session=%s", body.model, meta.session_id)
        raise HTTPException(status_code=403, detail="Model not allowed in sandbox")

    # 3. Real usage-based cost cap per execution
    if meta.cost_usd >= _MAX_COST_PER_EXECUTION:
        logger.warning(
            "SANDBOX_PROXY_DENIED reason=cost_cap session=%s spent=%.4f cap=%.2f",
            meta.session_id, meta.cost_usd, _MAX_COST_PER_EXECUTION,
        )
        raise HTTPException(status_code=429, detail="Sandbox execution LLM cost cap exceeded")

    # 4. Forward via LLMGateway
    tenant_id = UUID(meta.tenant_id)
    user_id = UUID(meta.user_id)

    gateway = LLMGateway(db)
    llm_request = LLMRequest(
        messages=[{"role": m.role, "content": m.content} for m in body.messages],
        model=body.model,
        temperature=body.temperature or 0.7,
        max_tokens=body.max_tokens,
    )

    try:
        llm_response = await gateway.complete(llm_request, tenant_id=tenant_id, user_id=user_id)
    except Exception as e:
        logger.error("Sandbox proxy LLM call failed: %s", e)
        raise HTTPException(status_code=502, detail="LLM provider error")

    # 5. Increment usage (fire-and-forget on failure)
    total_tokens = llm_response.input_tokens + llm_response.output_tokens
    try:
        await increment_sandbox_token_usage(
            token, total_tokens, cost_usd=llm_response.cost_usd,
        )
    except Exception:
        logger.warning("Failed to increment sandbox token usage", exc_info=True)

    # 6. Audit log (structured logging — SHA-256 token hash, not plaintext)
    token_hash = hashlib.sha256(token.encode()).hexdigest()[:16]
    logger.info(
        "SANDBOX_PROXY_CALL tenant=%s user=%s session=%s token_hash=%s model=%s tokens=%d cost=%.6f",
        meta.tenant_id,
        meta.user_id,
        meta.session_id,
        token_hash,
        llm_response.model,
        total_tokens,
        llm_response.cost_usd,
    )

    # 7. Return OpenAI-format response
    return SandboxProxyChatResponse(
        id=f"sbx-{uuid4().hex[:24]}",
        choices=[
            SandboxProxyChatChoice(
                index=0,
                message=SandboxProxyChatMessage(
                    role="assistant",
                    content=llm_response.content,
                ),
                finish_reason="stop",
            ),
        ],
        usage=SandboxProxyChatUsage(
            prompt_tokens=llm_response.input_tokens,
            completion_tokens=llm_response.output_tokens,
            total_tokens=total_tokens,
        ),
    )
