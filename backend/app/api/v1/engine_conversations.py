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
from app.models.project import Project
from app.models.tenant import Tenant
from app.services.approval_service import ApprovalError, ApprovalService
from app.services.automation_scope import is_automation_project
from app.services.engine_chat_agent import AgentUnavailable, Selections, build_engine_chat_agent
from app.services.chat_service import set_engine_conversation_title, touch_engine_conversation
from app.services.feature_flags import engine_chat_enabled
from app.services.engine_client import EngineClient, EngineError, get_engine_client
from app.services.project_service import ProjectService
from app.services.usage_summary import conversation_cost

logger = logging.getLogger(__name__)


async def require_engine_chat(
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
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
    archived: Optional[bool] = None  # False restores an archived conversation; True archives it

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


class AnswerPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    optionIds: Optional[list[str]] = None
    text: Optional[str] = None


class AnswerBody(BaseModel):
    # Extra keys (e.g. a client-supplied principal) are ignored on purpose (R16).
    model_config = ConfigDict(extra="ignore")
    answer: AnswerPayload


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


def _require_active(conv: EngineConversation) -> None:
    if conv.archived_at is not None:
        raise HTTPException(status_code=409, detail="This conversation is archived. Restore it to continue.")


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
    db: AsyncSession = Depends(get_db, scope="function"),
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
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
):
    """Rename, pin and/or (un)archive. Listing metadata only: it does not touch the engine or ``updated_at``."""
    conv = await _binding(db, conversation_id, user)
    if conv.archived_at is not None:
        if body.archived is not False:
            raise HTTPException(status_code=404, detail="Conversation not found")
        conv.archived_at = None
    elif body.archived:
        conv.archived_at = datetime.now(timezone.utc)
    if body.title is not None:
        conv.title = body.title
    if body.pinned is not None:
        conv.pinned = body.pinned
    await db.flush()
    return {"conversationId": str(conv.conversation_id), "title": conv.title, "pinned": bool(conv.pinned)}


@router.delete("/{conversation_id}", status_code=204)
async def archive_conversation(
    conversation_id: UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
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
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    engine: EngineClient = Depends(get_engine_client),
):
    """Change the selections mid-conversation: omitted fields keep their stored value.

    The engine applies the new config from the next run; the record is updated after the engine
    accepts it, so a failed update leaves both unchanged.
    """
    conv = await _binding(db, conversation_id, user)
    _require_active(conv)
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
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    engine: EngineClient = Depends(get_engine_client),
):
    conv = await _binding(db, conversation_id, user)
    _require_active(conv)
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
    db: AsyncSession = Depends(get_db, scope="function"),
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
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    engine: EngineClient = Depends(get_engine_client),
):
    conv = await _binding(db, conversation_id, user)
    try:
        result = await engine.abort(conv.session_id, conv.tenant_id, conv.project_id,
                                    conv.conversation_id, body.reason if body else None)
    except EngineError as e:
        raise _engine_http_error(e) from None
    # The engine accepted: nothing it was waiting on can be decided any more (KTD11).
    await ApprovalService(db).mark_closed(conv.tenant_id, conversation_id=conv.conversation_id, reason="aborted")
    return result


@router.delete("/{conversation_id}/submissions/{submission_id}")
async def cancel_submission(
    conversation_id: UUID,
    submission_id: str,
    db: AsyncSession = Depends(get_db, scope="function"),
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


def _legacy_cutoff_ms(request: Request) -> Optional[float]:
    raw = getattr(getattr(request.app.state, "engine_chat_settings", None), "APPROVALS_LEGACY_CUTOFF", None)
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        logger.error("APPROVALS_LEGACY_CUTOFF is not an ISO timestamp; legacy fallback disabled")
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp() * 1000


async def _may_decide_for_automation(db: AsyncSession, conv: EngineConversation, user: AuthUser) -> bool:
    """A run's conversation lives in the tenant's hidden automation project, which nobody joins: tenant admins and
    owners (the ledger row still decides who may decide what) are let in for those, so a workflow review that
    its requester cannot approve himself can be decided by an admin."""
    if user.role not in ("admin", "owner") or conv.run_kind is None:
        return False
    project = await db.get(Project, conv.project_id)
    return project is not None and is_automation_project(project.settings)


@router.post("/{conversation_id}/approvals/{tool_task_id}/decision")
async def decide_approval(
    conversation_id: UUID,
    tool_task_id: str,
    body: DecisionBody,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    engine: EngineClient = Depends(get_engine_client),
):
    """Decide a tool-call approval. The ledger row decides who may (KTD4); the engine decides what runs.

    Legacy fallback (KTD3, delete one release after U5 ships together with
    ``APPROVALS_LEGACY_CUTOFF``): an approval created before the ledger existed has no row. It may
    still be decided by the conversation owner, a tenant admin or owner, but only when the engine
    item's ``requestedAt`` is before the cutoff. A missing row for anything newer, or an unset
    cutoff, is 409 ``approval_not_indexed``.
    """
    conv = await db.get(EngineConversation, conversation_id)
    if conv is None or str(conv.tenant_id) != str(user.tenant_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    if await ProjectService(db).get_user_role(conv.project_id, UUID(str(user.id)), conv.tenant_id) is None \
            and not await _may_decide_for_automation(db, conv, user):
        raise HTTPException(status_code=403, detail="Not allowed on this conversation")
    svc = ApprovalService(db)
    row = await svc.get_by_task(conv.tenant_id, conv.conversation_id, tool_task_id)
    try:
        if row is not None:
            return await svc.decide(user=user, conv=conv, row=row, decision=body.decision,
                                    note=body.note, engine=engine)
        return await _decide_legacy(request, conv, user, tool_task_id, body, engine)
    except ApprovalError as e:
        raise HTTPException(e.status, e.code) from None
    except EngineError as e:
        raise _engine_http_error(e) from None


@router.post("/{conversation_id}/questions/{tool_task_id}/answer")
async def answer_question(
    conversation_id: UUID,
    tool_task_id: str,
    body: AnswerBody,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    engine: EngineClient = Depends(get_engine_client),
):
    """Answer an ``ask_user`` question. The conversation's owner or a tenant admin or owner may; the
    ledger row carries the protocol state and the audit entry (U11)."""
    conv = await db.get(EngineConversation, conversation_id)
    if conv is None or str(conv.tenant_id) != str(user.tenant_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    if await ProjectService(db).get_user_role(conv.project_id, UUID(str(user.id)), conv.tenant_id) is None:
        raise HTTPException(status_code=403, detail="Not allowed on this conversation")
    svc = ApprovalService(db)
    row = await svc.get_by_task(conv.tenant_id, conv.conversation_id, tool_task_id)
    try:
        if row is None:
            raise ApprovalError(404, "question_not_found")
        return await svc.answer(user=user, conv=conv, row=row,
                                answer=body.answer.model_dump(exclude_none=True), engine=engine)
    except ApprovalError as e:
        raise HTTPException(e.status, e.code) from None
    except EngineError as e:
        raise _engine_http_error(e) from None


async def _decide_legacy(request: Request, conv: EngineConversation, user: AuthUser, tool_task_id: str,
                         body: DecisionBody, engine: EngineClient) -> dict:
    if str(conv.user_id) != str(user.id) and user.role not in ("admin", "owner"):
        raise ApprovalError(403, "not_authorized")
    cutoff = _legacy_cutoff_ms(request)
    if cutoff is None:
        logger.warning("approval_not_indexed tenant=%s conversation=%s task=%s (no cutoff)",
                       conv.tenant_id, conv.conversation_id, tool_task_id)
        raise ApprovalError(409, "approval_not_indexed")
    items = await engine.approvals(conv.session_id, conv.tenant_id, conv.project_id, conv.conversation_id)
    item = next((i for i in items if i.get("toolTaskId") == tool_task_id), None)
    requested_at = item.get("requestedAt") if item else None
    if not isinstance(requested_at, (int, float)) or requested_at >= cutoff:
        logger.warning("approval_not_indexed tenant=%s conversation=%s task=%s",
                       conv.tenant_id, conv.conversation_id, tool_task_id)
        raise ApprovalError(409, "approval_not_indexed")
    return await engine.decide(conv.session_id, conv.tenant_id, conv.project_id, conv.conversation_id,
                               tool_task_id, body.decision, body.note, principal=str(user.id))


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
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
):
    """Ledger-priced cost and tokens for the conversation, grouped by run (the client never prices)."""
    conv = await _binding(db, conversation_id, user)
    return await conversation_cost(db, conv.tenant_id, conv.conversation_id)
