"""Chat Sessions API endpoints for managing persistent chat history."""
import logging
import uuid
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import String, cast, delete, select, func, desc, literal, null, union_all
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from typing import Optional, Literal, Union
from uuid import UUID
from datetime import datetime

from app.models.chat_session import ChatSession, ChatSessionMessage
from app.models.engine import EngineConversation
from app.models.custom_agent import CustomAgent
from app.models.workflow import Workflow
from app.core.database import get_db
from app.auth.dependencies import get_current_user, get_current_tenant_id
from app.auth.providers.base import AuthUser
from app.schemas.chat_session import (
    ChatSessionCreate,
    ChatSessionUpdate,
    ChatSessionResponse,
    ChatSessionListResponse,
    ChatSessionDetailResponse,
    AddMessageRequest,
    AddMessageResponse,
    ContinueResponse,
    ConversationListItem,
    ConversationListResponse,
    TranscriptMessage,
)
from app.api.v1.engine_conversations import _engine_http_error, require_engine_chat
from app.services.chat_backfill import append_message_row, ensure_backfilled
from app.services.chat_service import build_continue_summary
from app.services.engine_chat_agent import AgentUnavailable, Selections, build_engine_chat_agent
from app.services.engine_client import EngineClient, EngineError, get_engine_client
from app.services.project_service import ProjectService

logger = logging.getLogger(__name__)

router = APIRouter()


def session_to_response(session: ChatSession) -> ChatSessionResponse:
    """Convert ChatSession model to response schema."""
    return ChatSessionResponse(
        id=session.id,
        tenant_id=session.tenant_id,
        user_id=session.user_id,
        agent_id=session.agent_id,
        workflow_id=session.workflow_id,
        title=session.title,
        context_type=session.context_type,
        messages=session.messages or [],
        message_count=session.message_count,
        is_draft=session.is_draft,
        is_pinned=session.is_pinned,
        created_at=session.created_at,
        updated_at=session.updated_at,
        last_message_at=session.last_message_at,
    )


@router.post("", response_model=ChatSessionResponse, status_code=status.HTTP_201_CREATED)
async def create_chat_session(
    session_data: ChatSessionCreate,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id)
):
    """
    Create a new chat session.

    Either agent_id or workflow_id must be provided based on context_type.
    """
    user_id = UUID(user.id)

    # Validate context (agent_id is optional for built-in agents like General Assistant)
    if session_data.context_type == "workflow" and not session_data.workflow_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="workflow_id is required for workflow context"
        )

    # Verify agent/workflow exists and belongs to user
    if session_data.agent_id:
        result = await db.execute(
            select(CustomAgent).where(
                CustomAgent.id == session_data.agent_id,
                CustomAgent.user_id == user_id
            )
        )
        if not result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Agent not found"
            )

    if session_data.workflow_id:
        result = await db.execute(
            select(Workflow).where(
                Workflow.id == session_data.workflow_id,
                Workflow.user_id == user_id
            )
        )
        if not result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workflow not found"
            )

    # Convert initial messages
    initial_messages = []
    if session_data.messages:
        for msg in session_data.messages:
            initial_messages.append({
                "role": msg.role,
                "content": msg.content,
                "timestamp": datetime.utcnow().isoformat(),
                "metadata": msg.metadata or {}
            })

    # Create session
    session = ChatSession(
        tenant_id=tenant_id,
        user_id=user_id,
        agent_id=session_data.agent_id,
        workflow_id=session_data.workflow_id,
        title=session_data.title,
        context_type=session_data.context_type,
        messages=initial_messages,
        is_draft=session_data.is_draft,
        last_message_at=datetime.utcnow() if initial_messages else None,
    )

    db.add(session)
    await db.flush()
    await db.refresh(session)

    return session_to_response(session)


async def _list_union(db: AsyncSession, user_id: UUID, page: int, page_size: int) -> ConversationListResponse:
    """Legacy sessions plus engine conversations, newest first; ``(updated_at, id)`` keeps pages stable."""
    legacy = select(
        ChatSession.id.label("id"),
        literal("legacy", String).label("source"),
        ChatSession.title.label("title"),
        ChatSession.updated_at.label("updated_at"),
        ChatSession.project_id.label("project_id"),
        cast(null(), PGUUID(as_uuid=True)).label("legacy_session_id"),
        ChatSession.is_pinned.label("pinned"),
    ).where(ChatSession.user_id == user_id, ChatSession.is_draft == False)  # noqa: E712
    engine = select(
        EngineConversation.conversation_id.label("id"),
        literal("engine", String).label("source"),
        EngineConversation.title.label("title"),
        EngineConversation.updated_at.label("updated_at"),
        EngineConversation.project_id.label("project_id"),
        EngineConversation.legacy_session_id.label("legacy_session_id"),
        EngineConversation.pinned.label("pinned"),
    ).where(EngineConversation.user_id == user_id, EngineConversation.archived_at.is_(None))
    u = union_all(legacy, engine).subquery()

    total = (await db.execute(select(func.count()).select_from(u))).scalar() or 0
    rows = (await db.execute(
        select(u).order_by(desc(u.c.updated_at), desc(u.c.id))
        .offset((page - 1) * page_size).limit(page_size)
    )).all()
    return ConversationListResponse(
        items=[
            ConversationListItem(
                id=r.id, source=r.source,
                title=r.title or ("New conversation" if r.source == "engine" else "Untitled"),
                updated_at=r.updated_at, project_id=r.project_id,
                legacy_session_id=r.legacy_session_id, pinned=bool(r.pinned),
            )
            for r in rows
        ],
        total=total, page=page, page_size=page_size,
    )


@router.get("", response_model=Union[ChatSessionListResponse, ConversationListResponse])
async def list_chat_sessions(
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    include: Optional[Literal["engine"]] = Query(
        None, description="'engine' returns the union list of legacy sessions and engine conversations "
        "(agent/workflow/draft/pinned filters do not apply to it)"),
    context_type: Optional[Literal["agent", "workflow"]] = None,
    agent_id: Optional[UUID] = None,
    workflow_id: Optional[UUID] = None,
    is_draft: Optional[bool] = None,
    is_pinned: Optional[bool] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """
    List chat sessions with filtering and pagination.
    """
    user_id = UUID(user.id)

    if include == "engine":
        return await _list_union(db, user_id, page, page_size)

    # Build query
    query = select(ChatSession).where(ChatSession.user_id == user_id)
    count_query = select(func.count()).select_from(ChatSession).where(ChatSession.user_id == user_id)

    # Apply filters
    if context_type:
        query = query.where(ChatSession.context_type == context_type)
        count_query = count_query.where(ChatSession.context_type == context_type)

    if agent_id:
        query = query.where(ChatSession.agent_id == agent_id)
        count_query = count_query.where(ChatSession.agent_id == agent_id)

    if workflow_id:
        query = query.where(ChatSession.workflow_id == workflow_id)
        count_query = count_query.where(ChatSession.workflow_id == workflow_id)

    if is_draft is not None:
        query = query.where(ChatSession.is_draft == is_draft)
        count_query = count_query.where(ChatSession.is_draft == is_draft)

    if is_pinned is not None:
        query = query.where(ChatSession.is_pinned == is_pinned)
        count_query = count_query.where(ChatSession.is_pinned == is_pinned)

    # Get total count
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Apply ordering and pagination
    query = query.order_by(desc(ChatSession.updated_at))
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    sessions = result.scalars().all()

    return ChatSessionListResponse(
        sessions=[session_to_response(s) for s in sessions],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{session_id}", response_model=ChatSessionDetailResponse)
async def get_chat_session(
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user)
):
    """
    Get a specific chat session with full details.
    """
    user_id = UUID(user.id)

    result = await db.execute(
        select(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.user_id == user_id
        )
    )
    session = result.scalar_one_or_none()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat session not found"
        )

    # Get agent/workflow name
    agent_name = None
    workflow_name = None

    if session.agent_id:
        agent_result = await db.execute(
            select(CustomAgent.name).where(CustomAgent.id == session.agent_id)
        )
        agent_name = agent_result.scalar_one_or_none()

    if session.workflow_id:
        workflow_result = await db.execute(
            select(Workflow.name).where(Workflow.id == session.workflow_id)
        )
        workflow_name = workflow_result.scalar_one_or_none()

    # Read model: backfill lazily, then serve the renderable transcript from it.
    await ensure_backfilled(db, session)
    rows = (await db.execute(
        select(ChatSessionMessage).where(ChatSessionMessage.session_id == session.id)
        .order_by(ChatSessionMessage.seq)
    )).scalars().all()
    transcript = [
        TranscriptMessage(
            seq=r.seq, role=r.role, content=r.content, parts=r.parts,
            parts_valid=r.parts_valid, created_at=r.created_at,
        )
        for r in rows
    ]

    response = session_to_response(session)
    return ChatSessionDetailResponse(
        **response.model_dump(),
        agent_name=agent_name,
        workflow_name=workflow_name,
        transcript=transcript,
    )


@router.patch("/{session_id}", response_model=ChatSessionResponse)
async def update_chat_session(
    session_id: UUID,
    session_data: ChatSessionUpdate,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user)
):
    """
    Update a chat session's metadata.
    """
    user_id = UUID(user.id)

    result = await db.execute(
        select(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.user_id == user_id
        )
    )
    session = result.scalar_one_or_none()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat session not found"
        )

    # Update fields via safe_update (S25: blocks tenant_id, id, etc.)
    from app.utils.safe_update import safe_update
    from app.schemas.chat_session import ChatSessionUpdate
    update_data = session_data.model_dump(exclude_unset=True)
    safe_update(session, update_data, ChatSessionUpdate)

    session.updated_at = datetime.utcnow()

    await db.flush()
    await db.refresh(session)

    return session_to_response(session)


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat_session(
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user)
):
    """
    Delete a chat session.
    """
    user_id = UUID(user.id)

    result = await db.execute(
        select(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.user_id == user_id
        )
    )
    session = result.scalar_one_or_none()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat session not found"
        )

    await db.delete(session)
    await db.flush()


@router.post("/{session_id}/messages", response_model=AddMessageResponse)
async def add_message(
    session_id: UUID,
    request: AddMessageRequest,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user)
):
    """
    Add a message to an existing chat session.
    """
    user_id = UUID(user.id)

    result = await db.execute(
        select(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.user_id == user_id
        )
    )
    session = result.scalar_one_or_none()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat session not found"
        )

    # Bring older history into the read model before appending (keeps seq gap-free).
    await ensure_backfilled(db, session)

    # Add message (server-side JSONB append), dual-written to the read model in this transaction
    message = await session.add_message(
        role=request.message.role,
        content=request.message.content,
        metadata=request.message.metadata,
        db=db,
    )
    await append_message_row(db, session.tenant_id, session.id, message)

    # Auto-generate title if first user message and no title
    if not session.title and request.message.role == "user":
        session.title = session.generate_title()

    await db.flush()
    await db.refresh(session)

    return AddMessageResponse(
        session_id=session.id,
        message=message,
        message_count=session.message_count,
    )


@router.post("/{session_id}/clear", response_model=ChatSessionResponse)
async def clear_messages(
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user)
):
    """
    Clear all messages from a chat session.
    """
    user_id = UUID(user.id)

    result = await db.execute(
        select(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.user_id == user_id
        )
    )
    session = result.scalar_one_or_none()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat session not found"
        )

    session.messages = []
    await db.execute(delete(ChatSessionMessage).where(ChatSessionMessage.session_id == session.id))
    session.last_message_at = None
    session.updated_at = datetime.utcnow()

    await db.flush()
    await db.refresh(session)

    return session_to_response(session)


class ContinueBody(BaseModel):
    # Only selections are accepted (as on engine conversation create); extra keys are ignored.
    model_config = ConfigDict(extra="ignore")
    project_id: UUID
    selections: Optional[Selections] = None


@router.post(
    "/{session_id}/continue",
    response_model=ContinueResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_engine_chat)],
)
async def continue_chat_session(
    session_id: UUID,
    body: ContinueBody,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    engine: EngineClient = Depends(get_engine_client),
):
    """Continue a legacy session on the engine (KTD8): a new conversation seeded with a summary.

    The legacy rows are not touched. The summary (policy low tier, or a deterministic recap when
    that call fails) is written as the first, display-only entry with request id
    ``legacy_session:<id>``. Repeating the call returns the conversation already started.
    """
    user_id = UUID(user.id)
    tenant_id = UUID(str(user.tenant_id))

    session = (await db.execute(
        select(ChatSession).where(ChatSession.id == session_id, ChatSession.user_id == user_id)
    )).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Chat session not found")

    existing = (await db.execute(
        select(EngineConversation).where(
            EngineConversation.legacy_session_id == session_id,
            EngineConversation.user_id == user_id,
        ).order_by(EngineConversation.created_at)
    )).scalars().first()
    if existing is not None:
        return ContinueResponse(
            conversation_id=existing.conversation_id, project_id=existing.project_id,
            legacy_session_id=session_id, summary_source="recap", existing=True)

    if await ProjectService(db).get_user_role(body.project_id, user_id, tenant_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")

    await ensure_backfilled(db, session)
    rows = (await db.execute(
        select(ChatSessionMessage).where(ChatSessionMessage.session_id == session_id)
        .order_by(ChatSessionMessage.seq)
    )).scalars().all()
    summary, summary_source = await build_continue_summary(
        db, tenant_id=tenant_id, user_id=user_id,
        transcript=[{"role": r.role, "content": r.content, "parts": r.parts} for r in rows],
    )

    try:
        built = await build_engine_chat_agent(
            db, tenant_id=tenant_id, user=user, project_id=body.project_id,
            selections=body.selections or Selections(),
            continuation_summary=summary)
    except AgentUnavailable as e:
        raise HTTPException(
            404, "Agent not found" if str(e) == "agent_not_found" else "Agent not accessible") from None

    engine_session_id = engine.directory.default_session_id
    conversation_id = uuid.uuid4()
    source = f"legacy_session:{session_id}"
    try:
        await engine.create_conversation(
            engine_session_id, tenant_id, body.project_id, conversation_id, built.agent)
        await engine.submit(
            engine_session_id, tenant_id, body.project_id, conversation_id,
            {"text": summary, "type": "write", "requestId": source}, source)
    except EngineError as e:
        raise _engine_http_error(e) from None
    db.add(EngineConversation(
        conversation_id=conversation_id, tenant_id=tenant_id, project_id=body.project_id,
        user_id=user_id, session_id=engine_session_id, selections=built.selections,
        title=session.title, legacy_session_id=session_id, continuation_summary=summary))
    await db.flush()
    return ContinueResponse(
        conversation_id=conversation_id, project_id=body.project_id,
        legacy_session_id=session_id, summary_source=summary_source)


@router.get("/agent/{agent_id}/recent", response_model=ChatSessionListResponse)
async def get_recent_agent_sessions(
    agent_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    limit: int = Query(10, ge=1, le=50),
):
    """
    Get recent chat sessions for a specific agent.
    """
    user_id = UUID(user.id)

    # Verify agent exists
    agent_result = await db.execute(
        select(CustomAgent).where(
            CustomAgent.id == agent_id,
            CustomAgent.user_id == user_id
        )
    )
    if not agent_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found"
        )

    # Get recent sessions
    query = (
        select(ChatSession)
        .where(
            ChatSession.user_id == user_id,
            ChatSession.agent_id == agent_id,
            ChatSession.is_draft == False
        )
        .order_by(desc(ChatSession.updated_at))
        .limit(limit)
    )

    result = await db.execute(query)
    sessions = result.scalars().all()

    # Get total count
    count_result = await db.execute(
        select(func.count())
        .select_from(ChatSession)
        .where(
            ChatSession.user_id == user_id,
            ChatSession.agent_id == agent_id,
            ChatSession.is_draft == False
        )
    )
    total = count_result.scalar() or 0

    return ChatSessionListResponse(
        sessions=[session_to_response(s) for s in sessions],
        total=total,
        page=1,
        page_size=limit,
    )


@router.get("/workflow/{workflow_id}/recent", response_model=ChatSessionListResponse)
async def get_recent_workflow_sessions(
    workflow_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    limit: int = Query(10, ge=1, le=50),
):
    """
    Get recent chat sessions for a specific workflow.
    """
    user_id = UUID(user.id)

    # Verify workflow exists
    workflow_result = await db.execute(
        select(Workflow).where(
            Workflow.id == workflow_id,
            Workflow.user_id == user_id
        )
    )
    if not workflow_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found"
        )

    # Get recent sessions
    query = (
        select(ChatSession)
        .where(
            ChatSession.user_id == user_id,
            ChatSession.workflow_id == workflow_id
        )
        .order_by(desc(ChatSession.updated_at))
        .limit(limit)
    )

    result = await db.execute(query)
    sessions = result.scalars().all()

    # Get total count
    count_result = await db.execute(
        select(func.count())
        .select_from(ChatSession)
        .where(
            ChatSession.user_id == user_id,
            ChatSession.workflow_id == workflow_id
        )
    )
    total = count_result.scalar() or 0

    return ChatSessionListResponse(
        sessions=[session_to_response(s) for s in sessions],
        total=total,
        page=1,
        page_size=limit,
    )
