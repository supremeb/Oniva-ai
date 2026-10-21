"""POST /internal/workspace/offload: the engine stores a large tool result out of context (plan 2026-10-21-001 U15).

The engine's ``OffloadStore.put`` is idempotent by ``runId:toolCallId`` (the ``Idempotency-Key``
header, same shape as tool invokes). The file lives in the conversation store keyed by the root
conversation, at a path built here from validated ids; the caller never supplies a path. Binding
(tenant, project, conversation) is verified against ``engine_conversations`` and writes run under
the same fence as write tools.
"""
import logging
from contextlib import nullcontext
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError

from app.internal_api import invocations
from app.internal_api.fence import (
    GatewayError,
    SessionFactory,
    admit_fence,
    fence_headers,
    hold_write_fence,
)
from app.internal_api.invocations import get_session_factory
from app.services import conversation_workspace as cw

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/workspace")


class OffloadBody(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    runId: str
    # The tool call id for kind "tool_result"; the compaction id for kind "history".
    toolCallId: str
    tenantId: str
    projectId: str
    conversationId: Optional[str] = None
    content: str
    kind: Literal["tool_result", "history"] = "tool_result"


@router.post("/offload")
async def offload(request: Request, factory: SessionFactory = Depends(get_session_factory)) -> JSONResponse:
    try:
        body = OffloadBody.model_validate(await request.json())
    except (ValueError, ValidationError):
        raise GatewayError(400, {"error": "invalid_request"})
    key = request.headers.get("idempotency-key")
    if key != f"{body.runId}:{body.toolCallId}" or len(key) > 255:
        raise GatewayError(400, {"error": "invalid_request"})
    if not body.conversationId:
        raise GatewayError(400, {"error": "conversation_required"})

    token, header_session = fence_headers(request)
    session_id = header_session or f"{body.tenantId}/{body.projectId}"
    if token is not None:
        await admit_fence(factory, session_id, token)
    await invocations.resolve_binding(factory, body.tenantId, body.projectId, body.conversationId)

    put = cw.put_tool_result if body.kind == "tool_result" else cw.put_history
    guard = hold_write_fence(factory, session_id, token) if token is not None else nullcontext()
    try:
        async with guard:
            result = await put(body.tenantId, body.conversationId, body.toolCallId, body.content)
    except cw.TooLarge:
        raise GatewayError(413, {"error": "too_large", "maxBytes": cw.MAX_BYTES})
    except cw.WorkspaceError as exc:
        raise GatewayError(400, {"error": "invalid_request", "message": str(exc)})
    return JSONResponse(
        {"path": result.path, "created": result.created, "sizeBytes": result.size_bytes},
        headers={"Cache-Control": "no-store"},
    )
