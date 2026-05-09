"""Chat Sessions API endpoints for managing persistent chat history."""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from typing import Optional, Literal
from uuid import UUID
from datetime import datetime

from app.models.chat_session import ChatSession
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
)

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


@router.get("", response_model=ChatSessionListResponse)
async def list_chat_sessions(
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
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

    response = session_to_response(session)
    return ChatSessionDetailResponse(
        **response.model_dump(),
        agent_name=agent_name,
        workflow_name=workflow_name,
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

    # Add message (server-side JSONB append)
    message = await session.add_message(
        role=request.message.role,
        content=request.message.content,
        metadata=request.message.metadata,
        db=db,
    )

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
    session.last_message_at = None
    session.updated_at = datetime.utcnow()

    await db.flush()
    await db.refresh(session)

    return session_to_response(session)


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
