"""Browser-facing engine conversation routes (plan 003, U8 / R14-R16, KTD12).

A thin authenticated proxy: the user is verified by the normal auth stack, the conversation
binding (tenant, project, owner, session) is read from ``engine_conversations``, and tenant /
project headers sent to the engine come from that binding, never from the request.
Mounted only when ``ENABLE_ENGINE_API`` is set and the engine is configured.
"""
import logging
import uuid
from typing import Any, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, get_current_user_with_query_token
from app.auth.providers.base import AuthUser
from app.config import Settings
from app.core.database import get_db
from app.models.engine import EngineConversation
from app.models.tenant import Tenant
from app.services.engine_chat_agent import AgentUnavailable, Selections, build_engine_chat_agent
from app.services.chat_service import set_engine_conversation_title, touch_engine_conversation
from app.services.feature_flags import engine_chat_enabled
from app.services.engine_client import EngineClient, EngineError, get_engine_client
from app.services.project_service import ProjectService
from app.services.usage_summary import conversation_cost

logger = logging.getLogger(__name__)


async def require_engine_chat(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
) -> None:
    """Per-tenant gate (R3, defense in depth): 404 unless engine chat is on for the caller's tenant.

    Evaluated per request so a global-on deploy still honors a tenant being switched off.
    """
    tenant = await db.get(Tenant, UUID(str(user.tenant_id)))
    settings = getattr(request.app.state, "engine_chat_settings", None)
    if tenant is None or not engine_chat_enabled(tenant, settings):
        raise HTTPException(status_code=404, detail="Not found")


router = APIRouter(dependencies=[Depends(require_engine_chat)])


class CreateConversationBody(BaseModel):
    # Only selections are accepted: a client-supplied prompt or tool list is ignored (KTD2).
    model_config = ConfigDict(extra="ignore")
    project_id: UUID
    selections: Optional[Selections] = None


class SubmissionBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    text: Optional[str] = None
    content: Optional[Any] = None
    whenBusy: Optional[Literal["followUp", "steer", "reject"]] = None
    type: Optional[str] = None
    requestId: Optional[str] = None


class UpdateConversationBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: Optional[str] = None
    pinned: Optional[bool] = None

    @field_validator("title")
    @classmethod
    def _title(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = " ".join(v.split())
        if not v or len(v) > 255:
            raise ValueError("title must be 1-255 characters")
        return v


class AbortBody(BaseModel):
    reason: Optional[str] = None


class DecisionBody(BaseModel):
    # Extra keys (e.g. a client-supplied principal) are ignored on purpose (R16).
    model_config = ConfigDict(extra="ignore")
    decision: Literal["approve", "deny"]
    note: Optional[str] = None


def _is_admin(user: AuthUser) -> bool:
    return user.role == "admin"


async def _binding(db: AsyncSession, conversation_id: UUID, user: AuthUser) -> EngineConversation:
    """The conversation, only for its tenant (404 otherwise) and its owner or a tenant admin."""
    conv = await db.get(EngineConversation, conversation_id)
    if conv is None or str(conv.tenant_id) != str(user.tenant_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    if str(conv.user_id) != str(user.id) and not _is_admin(user):
        raise HTTPException(status_code=403, detail="Not allowed on this conversation")
    return conv


def _engine_http_error(e: EngineError) -> HTTPException:
    if e.status == 409 and e.error == "not_owner":
        return HTTPException(503, "Engine worker is not available, retry", headers={"Retry-After": "1"})
    if e.status in (502, 504):
        return HTTPException(e.status, "Engine unavailable")
    if e.status >= 500:
        return HTTPException(502, "Engine error")
    if e.status in (401, 403):
        return HTTPException(502, "Engine rejected the backend")  # service-auth fault, not the user's
    return HTTPException(e.status, e.error)


@router.post("", status_code=201)
async def create_conversation(
    body: CreateConversationBody,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    engine: EngineClient = Depends(get_engine_client),
):
    tenant_id = UUID(str(user.tenant_id))
    role = await ProjectService(db).get_user_role(body.project_id, UUID(str(user.id)), tenant_id)
    if role is None:
        raise HTTPException(404, "Project not found")
    try:
        built = await build_engine_chat_agent(
            db, tenant_id=tenant_id, user=user, project_id=body.project_id,
            selections=body.selections or Selections())
    except AgentUnavailable as e:
        raise HTTPException(404, "Agent not found" if str(e) == "agent_not_found" else "Agent not accessible") from None
    session_id = engine.directory.default_session_id
    conversation_id = uuid.uuid4()
    try:
        await engine.create_conversation(
            session_id, tenant_id, body.project_id, conversation_id, built.agent)
    except EngineError as e:
        raise _engine_http_error(e) from None
    db.add(EngineConversation(
        conversation_id=conversation_id, tenant_id=tenant_id, project_id=body.project_id,
        user_id=UUID(str(user.id)), session_id=session_id, selections=built.selections))
    await db.flush()
    return {"conversationId": str(conversation_id), "projectId": str(body.project_id),
            "selections": built.selections, "dropped": built.dropped}


@router.patch("/{conversation_id}")
async def update_conversation(
    conversation_id: UUID,
    body: UpdateConversationBody,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
):
    """Rename and/or pin. Listing metadata only: it does not touch the engine or ``updated_at``."""
    conv = await _binding(db, conversation_id, user)
    if conv.archived_at is not None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if body.title is not None:
        conv.title = body.title
    if body.pinned is not None:
        conv.pinned = body.pinned
    await db.flush()
    return {"conversationId": str(conv.conversation_id), "title": conv.title, "pinned": bool(conv.pinned)}


@router.delete("/{conversation_id}", status_code=204)
async def archive_conversation(
    conversation_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
):
    """Sidebar delete: archive (hide from listings). Engine data is kept. Idempotent."""
    conv = await _binding(db, conversation_id, user)
    if conv.archived_at is None:
        conv.archived_at = datetime.now(timezone.utc)
        await db.flush()


@router.patch("/{conversation_id}/selections")
async def update_selections(
    conversation_id: UUID,
    body: Selections,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    engine: EngineClient = Depends(get_engine_client),
):
    """Change the selections mid-conversation: omitted fields keep their stored value.

    The engine applies the new config from the next run; the record is updated after the engine
    accepts it, so a failed update leaves both unchanged.
    """
    conv = await _binding(db, conversation_id, user)
    merged = {**(conv.selections or {}), **body.model_dump(include=body.model_fields_set)}
    try:
        built = await build_engine_chat_agent(
            db, tenant_id=conv.tenant_id, user=user, project_id=conv.project_id,
            selections=Selections.model_validate(merged),
            continuation_summary=conv.continuation_summary)
    except AgentUnavailable as e:
        raise HTTPException(404, "Agent not found" if str(e) == "agent_not_found" else "Agent not accessible") from None
    try:
        await engine.update_agent(conv.session_id, conv.tenant_id, conv.project_id,
                                  conv.conversation_id, built.agent)
    except EngineError as e:
        raise _engine_http_error(e) from None
    conv.selections = built.selections
    await db.flush()
    return {"conversationId": str(conv.conversation_id), "selections": built.selections,
            "dropped": built.dropped}


@router.post("/{conversation_id}/submissions", status_code=202)
async def submit(
    conversation_id: UUID,
    body: SubmissionBody,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    engine: EngineClient = Depends(get_engine_client),
):
    conv = await _binding(db, conversation_id, user)
    payload = body.model_dump(exclude_none=True)
    # Any content part that is not text (e.g. an image) is untrusted input: the
    # engine taints the conversation so policy can require approval on what follows.
    if isinstance(body.content, list) and any(
        not (isinstance(part, dict) and part.get("type") == "text") for part in body.content
    ):
        payload["untrusted"] = True
    key = idempotency_key or body.requestId or str(uuid.uuid4())
    payload["requestId"] = body.requestId or key
    try:
        result = await engine.submit(conv.session_id, conv.tenant_id, conv.project_id,
                                     conv.conversation_id, payload, key)
    except EngineError as e:
        raise _engine_http_error(e) from None
    # Listing metadata: order by activity, and title from the first user message (kept if set).
    await touch_engine_conversation(db, conv.conversation_id)
    if conv.title is None and payload.get("type") != "write" and isinstance(body.text, str):
        await set_engine_conversation_title(db, conv.conversation_id, body.text)
    return result


@router.get("/{conversation_id}/watch")
async def watch(
    conversation_id: UUID,
    request: Request,
    cursor: Optional[str] = Query(default=None, pattern=r"^(0|[1-9][0-9]*)$"),
    last_event_id: Optional[str] = Header(default=None, alias="Last-Event-ID"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user_with_query_token),
    engine: EngineClient = Depends(get_engine_client),
):
    # Binding is re-checked on every (re)connect.
    conv = await _binding(db, conversation_id, user)
    try:
        upstream = await engine.watch_stream(
            conv.session_id, conv.tenant_id, conv.project_id, conv.conversation_id,
            last_event_id=last_event_id, cursor=cursor)
    except EngineError as e:
        raise _engine_http_error(e) from None

    async def relay():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()  # browser gone (generator cancelled/closed) or stream ended

    return StreamingResponse(
        relay(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/{conversation_id}/abort")
async def abort(
    conversation_id: UUID,
    body: Optional[AbortBody] = None,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    engine: EngineClient = Depends(get_engine_client),
):
    conv = await _binding(db, conversation_id, user)
    try:
        return await engine.abort(conv.session_id, conv.tenant_id, conv.project_id,
                                  conv.conversation_id, body.reason if body else None)
    except EngineError as e:
        raise _engine_http_error(e) from None


@router.delete("/{conversation_id}/submissions/{submission_id}")
async def cancel_submission(
    conversation_id: UUID,
    submission_id: str,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    engine: EngineClient = Depends(get_engine_client),
):
    """Withdraw a queued (not yet started) input; 409 once the engine has started it."""
    conv = await _binding(db, conversation_id, user)
    try:
        return await engine.cancel_submission(conv.session_id, conv.tenant_id, conv.project_id,
                                              conv.conversation_id, submission_id)
    except EngineError as e:
        raise _engine_http_error(e) from None


@router.post("/{conversation_id}/approvals/{tool_task_id}/decision")
async def decide_approval(
    conversation_id: UUID,
    tool_task_id: str,
    body: DecisionBody,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    engine: EngineClient = Depends(get_engine_client),
):
    # Owner or tenant admin only (checked in _binding); the principal is the verified user.
    conv = await _binding(db, conversation_id, user)
    try:
        return await engine.decide(conv.session_id, conv.tenant_id, conv.project_id,
                                   conv.conversation_id, tool_task_id, body.decision, body.note,
                                   principal=str(user.id))
    except EngineError as e:
        raise _engine_http_error(e) from None


def mount_engine_conversations(app: FastAPI, settings: Settings) -> bool:
    """Mount only when ENABLE_ENGINE_API and the engine is configured (fail closed)."""
    if not (settings.ENABLE_ENGINE_API and settings.ENGINE_URL and settings.ENGINE_SERVICE_TOKEN):
        return False
    app.state.engine_chat_settings = settings
    app.include_router(router, prefix="/api/v1/engine/conversations", tags=["engine"])
    return True


@router.get("/{conversation_id}/cost")
async def conversation_cost_summary(
    conversation_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
):
    """Ledger-priced cost and tokens for the conversation, grouped by run (the client never prices)."""
    conv = await _binding(db, conversation_id, user)
    return await conversation_cost(db, conv.tenant_id, conv.conversation_id)
