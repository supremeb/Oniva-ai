"""POST /internal/workspace/offload: the engine stores a large tool result out of context (plan 2026-10-21-001 U15).

The contract is the engine's (engine/src/tools/gateway.ts ``offload`` and the clauses in
engine/test/support/fake-gateway.ts). ``OffloadStore.put`` is idempotent by ``runId:toolCallId`` (the
``Idempotency-Key`` header). The file lives in the conversation store keyed by ``conversationId``, the
top-level conversation; ``engineConversationId`` (the child that made the call) is informational. The
path is built here from validated ids; the caller never supplies one. Binding (tenant, project,
conversation) is verified against ``engine_conversations`` and writes run under the same fence as
write tools. Answers ``200 {path, bytes}``.
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
    toolName: str
    tenantId: str
    projectId: str
    # The top-level conversation: the storage key and the binding that is verified.
    conversationId: str
    # The conversation that made the call (a subagent's child); informational only.
    engineConversationId: Optional[str] = None
    text: str
    # Optional extension for compaction archives; the engine's tool-result puts omit it.
    kind: Literal["tool_result", "history"] = "tool_result"


def _invalid() -> GatewayError:
    return GatewayError(400, {"error": "invalid_request"})


@router.post("/offload")
async def offload(request: Request, factory: SessionFactory = Depends(get_session_factory)) -> JSONResponse:
    token, header_session = fence_headers(request)
    # Like the engine's fake, a stale fence is answered before the body is judged.
    if token is not None and header_session:
        await admit_fence(factory, header_session, token)
    try:
        raw = await request.json()
    except ValueError:
        raise _invalid()
    try:
        body = OffloadBody.model_validate(raw)
    except ValidationError:
        raise _invalid()
    required = (body.runId, body.toolCallId, body.toolName, body.tenantId, body.projectId, body.conversationId)
    key = request.headers.get("idempotency-key")
    if not all(required) or key != f"{body.runId}:{body.toolCallId}" or len(key) > 255:
        raise _invalid()

    session_id = header_session or f"{body.tenantId}/{body.projectId}"
    if token is not None and not header_session:
        await admit_fence(factory, session_id, token)
    await invocations.resolve_binding(factory, body.tenantId, body.projectId, body.conversationId)

    put = cw.put_tool_result if body.kind == "tool_result" else cw.put_history
    guard = hold_write_fence(factory, session_id, token) if token is not None else nullcontext()
    try:
        async with guard:
            result = await put(body.tenantId, body.conversationId, body.toolCallId, body.text)
    except cw.TooLarge:
        raise GatewayError(413, {"error": "too_large"})
    except cw.WorkspaceError:
        raise _invalid()
    return JSONResponse({"path": result.path, "bytes": result.size_bytes}, headers={"Cache-Control": "no-store"})
