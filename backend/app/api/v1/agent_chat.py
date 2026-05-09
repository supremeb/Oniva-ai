"""Agent Chat API endpoints for Agent Studio testing.

Manages chat sessions for testing agents with feedback collection.
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.responses import StreamingResponse
from sse_starlette.sse import EventSourceResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from typing import Optional, List
from uuid import UUID, uuid4
from datetime import datetime
import json
import logging

from app.models.agent_chat_session import AgentChatSession
from app.models.agent_test_case import AgentTestCase
from app.models.agent_version import AgentVersion
from app.core.database import get_db
from app.auth.dependencies import get_current_user, get_current_tenant_id
from app.auth.providers.base import AuthUser
from app.api.dependencies import verify_agent_access, AgentInfo
from app.api.dependencies.quota import require_quota
from app.models.usage_quota import QuotaType
from app.services.agent_circuit_breaker import AgentCircuitBreaker
from app.services.redis_ha import get_redis_service
from app.core.builtin_agents import slug_to_uuid, uuid_to_slug

# Import built-in agents
from app.agents.research import research_agent
from app.agents.data_retrieval import data_retrieval_agent
from app.agents.compliance import compliance_agent
from app.agents.analysis import analysis_agent

from app.schemas.agent_studio import (
    CreateChatSessionRequest,
    ChatSessionSummary,
    ChatSessionResponse,
    SendMessageRequest,
    FeedbackRequest,
    FeedbackResponse,
    ChatMessage,
)
from app.services.llm_gateway import normalize_model_id

logger = logging.getLogger(__name__)

# Built-in agent registry
BUILTIN_AGENT_MAP = {
    "research": research_agent,
    "data-retrieval": data_retrieval_agent,
    "compliance": compliance_agent,
    "analysis": analysis_agent,
}

router = APIRouter()


def parse_agent_id_to_uuid(agent_id: str) -> UUID:
    """
    Convert agent_id string to UUID.
    For custom agents (valid UUIDs), returns the UUID directly.
    For built-in agents (string slugs), returns their stable UUID.
    """
    # Try stable built-in UUID first (handles both slugs and UUIDs)
    resolved = slug_to_uuid(agent_id)
    try:
        return UUID(resolved)
    except ValueError:
        raise ValueError(f"Invalid agent ID: {agent_id}")


def session_to_summary(session: AgentChatSession) -> ChatSessionSummary:
    """Convert AgentChatSession model to summary schema."""
    return ChatSessionSummary(
        id=session.id,
        agent_id=session.agent_id,
        agent_version_id=session.agent_version_id,
        name=session.name,
        status=session.status,
        total_messages=session.total_messages or 0,
        positive_feedback_count=session.positive_feedback_count or 0,
        negative_feedback_count=session.negative_feedback_count or 0,
        created_at=session.created_at,
        updated_at=session.updated_at,
        last_message_at=session.last_message_at,
    )


def session_to_response(session: AgentChatSession) -> ChatSessionResponse:
    """Convert AgentChatSession model to full response schema."""
    messages = []
    for msg in (session.messages or []):
        messages.append(ChatMessage(
            id=UUID(msg['id']) if isinstance(msg.get('id'), str) else msg.get('id', uuid4()),
            role=msg['role'],
            content=msg['content'],
            timestamp=datetime.fromisoformat(msg['timestamp']) if isinstance(msg.get('timestamp'), str) else msg.get('timestamp', datetime.utcnow()),
            feedback=msg.get('feedback'),
            test_case_id=UUID(msg['test_case_id']) if msg.get('test_case_id') else None,
            tokens_used=msg.get('tokens_used'),
            latency_ms=msg.get('latency_ms'),
        ))

    return ChatSessionResponse(
        id=session.id,
        tenant_id=session.tenant_id,
        user_id=session.user_id,
        agent_id=session.agent_id,
        agent_version_id=session.agent_version_id,
        name=session.name,
        status=session.status,
        messages=messages,
        total_messages=session.total_messages or 0,
        total_tokens_used=session.total_tokens_used or 0,
        total_latency_ms=session.total_latency_ms or 0,
        positive_feedback_count=session.positive_feedback_count or 0,
        negative_feedback_count=session.negative_feedback_count or 0,
        created_at=session.created_at,
        updated_at=session.updated_at,
        last_message_at=session.last_message_at,
    )


@router.post("/agents/{agent_id}/chat/sessions", response_model=ChatSessionResponse, status_code=status.HTTP_201_CREATED)
async def create_chat_session(
    agent_id: str,
    request: CreateChatSessionRequest,
    agent: AgentInfo = Depends(verify_agent_access),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id)
):
    """
    Create a new chat session for testing an agent.
    Supports both custom agents (UUID) and built-in agents (string ID).
    """
    user_id = UUID(user.id)
    agent_uuid = parse_agent_id_to_uuid(agent_id)

    # Verify version if specified (only for custom agents)
    if request.agent_version_id and not agent.is_builtin:
        version_result = await db.execute(
            select(AgentVersion).where(
                AgentVersion.id == request.agent_version_id,
                AgentVersion.agent_id == agent_uuid
            )
        )
        if not version_result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Agent version not found"
            )

    # Create session
    session = AgentChatSession(
        tenant_id=tenant_id,
        user_id=user_id,
        agent_id=agent_uuid,
        agent_version_id=request.agent_version_id if not agent.is_builtin else None,
        name=request.name,
        messages=[],
    )

    db.add(session)
    await db.flush()
    await db.refresh(session)

    return session_to_response(session)


@router.get("/agents/{agent_id}/chat/sessions", response_model=List[ChatSessionSummary])
async def list_chat_sessions(
    agent_id: str,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status_filter: Optional[str] = Query(None, alias="status"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user)
):
    """
    List chat sessions for an agent.
    """
    user_id = UUID(user.id)
    agent_uuid = parse_agent_id_to_uuid(agent_id)

    # Build query
    query = select(AgentChatSession).where(
        AgentChatSession.agent_id == agent_uuid,
        AgentChatSession.user_id == user_id
    )

    if status_filter:
        query = query.where(AgentChatSession.status == status_filter)

    query = query.order_by(desc(AgentChatSession.updated_at))
    query = query.offset(offset).limit(limit)

    result = await db.execute(query)
    sessions = result.scalars().all()

    return [session_to_summary(s) for s in sessions]


@router.get("/agents/{agent_id}/chat/sessions/{session_id}", response_model=ChatSessionResponse)
async def get_chat_session(
    agent_id: str,
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user)
):
    """
    Get a specific chat session with all messages.
    """
    user_id = UUID(user.id)
    agent_uuid = parse_agent_id_to_uuid(agent_id)

    result = await db.execute(
        select(AgentChatSession).where(
            AgentChatSession.id == session_id,
            AgentChatSession.agent_id == agent_uuid,
            AgentChatSession.user_id == user_id
        )
    )
    session = result.scalar_one_or_none()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat session not found"
        )

    return session_to_response(session)


@router.post("/agents/{agent_id}/chat/sessions/{session_id}/messages")
async def send_message(
    agent_id: str,
    session_id: UUID,
    request: SendMessageRequest,
    agent: AgentInfo = Depends(verify_agent_access),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
    _quota=Depends(require_quota(QuotaType.LLM_TOKENS)),
):
    """
    Send a message and get agent response.

    Returns a streaming response with the agent's reply.
    Enhanced mode emits rich SSE events (tool calls, reasoning, sources).
    Legacy mode emits delta/message/done/error events.
    """
    from app.core.database import AsyncSessionLocal, set_tenant_context
    from app.services.agent_resolver import resolve_custom_agent, wire_skills_and_connectors, build_message_history
    from app.services.chat_service import store_memory_async, extract_response_text, format_error
    from app.services.sse_builder import create_sse_builder
    from app.config import get_settings
    from pydantic_ai.run import AgentRunResultEvent
    import time

    settings = get_settings()
    user_id = UUID(user.id)
    agent_uuid = parse_agent_id_to_uuid(agent_id)
    enhanced = request.enhancedMode or False

    # Circuit breaker check for custom agents (skip for built-in agents)
    if not agent.is_builtin:
        redis = await get_redis_service()
        breaker = AgentCircuitBreaker(redis)
        await breaker.check_health(agent_uuid)

    # Get session
    result = await db.execute(
        select(AgentChatSession).where(
            AgentChatSession.id == session_id,
            AgentChatSession.agent_id == agent_uuid,
            AgentChatSession.user_id == user_id
        )
    )
    session = result.scalar_one_or_none()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat session not found"
        )

    # Add user message
    user_message_id = str(uuid4())
    session.add_message(
        message_id=user_message_id,
        role="user",
        content=request.content,
    )

    # Flush (not commit) to keep RLS tenant context alive
    await db.flush()

    start_time = time.time()

    async def generate_response():
        """Generate streaming response from agent."""
        response_content = ""
        tokens_used = 0
        assistant_message_id = str(uuid4())
        builder = create_sse_builder()
        usage_dict = {}

        try:
            if agent.is_builtin:
                # === Built-in agent path (unchanged) ===
                builtin_agent = BUILTIN_AGENT_MAP.get(uuid_to_slug(agent_id))
                if builtin_agent:
                    result = await builtin_agent.run(
                        request.content, db=db, user_id=user_id
                    )
                    final_data = result.output
                    response_content = extract_response_text(final_data)

                    # Stream in chunks
                    chunk_size = 20
                    for i in range(0, len(response_content), chunk_size):
                        chunk = response_content[i:i + chunk_size]
                        if enhanced:
                            yield builder.text_delta(chunk)
                        else:
                            yield f"data: {json.dumps({'type': 'delta', 'content': chunk})}\n\n"

                    if hasattr(result, 'usage') and result.usage:
                        tokens_used = getattr(result.usage, 'total_tokens', 0)
                else:
                    response_content = f"Agent '{agent_id}' is not available."
                    if enhanced:
                        yield builder.text_delta(response_content)
                    else:
                        yield f"data: {json.dumps({'type': 'delta', 'content': response_content})}\n\n"

            elif agent.custom_agent:
                # === Custom agent path — full BaseAgent infrastructure ===
                custom_agent_obj = agent.custom_agent

                # Resolve agent via shared function (with version support)
                resolved_agent, agent_tools, connector_ids, knowledge_base_ids, agent_model, workflow_ids = (
                    await resolve_custom_agent(custom_agent_obj)
                )

                # Wire skills + connectors + workflows
                extra_tools, connectors_dict = await wire_skills_and_connectors(
                    agent=resolved_agent,
                    skill_ids=list(custom_agent_obj.skills or []),
                    connector_ids=connector_ids or None,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    user_session_id=str(session_id),
                    workflow_ids=workflow_ids or None,
                )
                # Merge agent_tools (from skills config) with extra_tools (deduplicate)
                existing_names = {getattr(t, '__name__', '') for t in extra_tools}
                for tool in agent_tools:
                    if getattr(tool, '__name__', '') not in existing_names:
                        extra_tools.append(tool)

                # Create filesystem session
                if settings.USE_SESSION_FILESYSTEM:
                    try:
                        from app.services.session_storage import get_session_storage
                        storage = get_session_storage()
                        await storage.get_or_create_session(
                            str(tenant_id), str(session_id), str(user_id)
                        )
                    except Exception as e:
                        logger.warning(f"Failed to create filesystem session: {e}")

                # Build message history from session messages
                history_messages = [
                    {"role": m["role"], "content": m["content"]}
                    for m in (session.messages or [])
                    if m.get("role") in ("user", "assistant") and m.get("content")
                ]
                message_history = build_message_history(history_messages) if history_messages else None

                # Stream via BaseAgent with own DB session (avoids RLS context loss)
                async with AsyncSessionLocal() as stream_db:
                    await set_tenant_context(stream_db, str(tenant_id))
                    # Also set user context for audit logging
                    await stream_db.execute(
                        __import__('sqlalchemy').text(
                            "SELECT set_config('app.current_user_id', :uid, true)"
                        ),
                        {"uid": str(user_id)},
                    )

                    final_result = None
                    async for event in resolved_agent.stream_events(
                        user_message=request.content,
                        db=stream_db,
                        user_id=user_id,
                        session_id=str(session_id),
                        tenant_id=tenant_id,
                        extra_tools=extra_tools,
                        connectors=connectors_dict,
                        message_history=message_history,
                        active_knowledge_base_ids=knowledge_base_ids or None,
                    ):
                        event_type = type(event).__name__

                        if event_type == "PartStartEvent":
                            part = event.part
                            part_type = type(part).__name__

                            if part_type == "TextPart":
                                pass  # Text handled via PartDeltaEvent
                            elif part_type == "ToolCallPart":
                                tool_call_id = part.tool_call_id or str(uuid4())
                                tool_name = part.tool_name
                                if enhanced:
                                    yield builder.tool_call_start(tool_call_id, tool_name)

                        elif event_type == "PartDeltaEvent":
                            delta = event.delta
                            delta_type = type(delta).__name__
                            if delta_type == "TextPartDelta":
                                text = delta.content_delta
                                response_content += text
                                if enhanced:
                                    yield builder.text_delta(text)
                                else:
                                    yield f"data: {json.dumps({'type': 'delta', 'content': text})}\n\n"

                        elif event_type == "FunctionToolCallEvent":
                            if enhanced:
                                tool_call_id = getattr(event, 'tool_call_id', None) or str(uuid4())
                                tool_name = getattr(event, 'tool_name', 'unknown')
                                args = getattr(event, 'args', None)
                                try:
                                    args_dict = json.loads(args) if isinstance(args, str) else args
                                except (json.JSONDecodeError, TypeError):
                                    args_dict = None
                                yield builder.tool_call_start(tool_call_id, tool_name, args=args_dict)

                        elif event_type == "FunctionToolResultEvent":
                            if enhanced:
                                from app.services.sse_builder import ToolCallStatus
                                tool_call_id = getattr(event.result, 'tool_call_id', None) or str(uuid4())
                                result_data = event.result.content
                                try:
                                    if isinstance(result_data, str):
                                        result_data = json.loads(result_data)
                                except (json.JSONDecodeError, TypeError):
                                    pass

                                # Detect workflow tool pending_approval → emit workflow_preview
                                if (
                                    isinstance(result_data, dict)
                                    and result_data.get("status") == "pending_approval"
                                ):
                                    yield builder.workflow_preview(result_data)

                                yield builder.tool_call_complete(
                                    tool_call_id, ToolCallStatus.SUCCESS, result=result_data
                                )

                        elif isinstance(event, AgentRunResultEvent):
                            final_result = event.result
                            if hasattr(final_result, 'usage') and callable(final_result.usage):
                                u = final_result.usage()
                                usage_dict = {
                                    "input_tokens": getattr(u, 'request_tokens', 0) or 0,
                                    "output_tokens": getattr(u, 'response_tokens', 0) or 0,
                                }
                                tokens_used = usage_dict.get("input_tokens", 0) + usage_dict.get("output_tokens", 0)

                    # If structured output with no text deltas, extract text
                    if final_result and not response_content:
                        response_content = extract_response_text(final_result.output)
                        if enhanced:
                            yield builder.text_delta(response_content)
                        else:
                            yield f"data: {json.dumps({'type': 'delta', 'content': response_content})}\n\n"

                    # Persist assistant message on stream_db (not request-scoped db)
                    stream_session = await stream_db.execute(
                        select(AgentChatSession).where(AgentChatSession.id == session_id)
                    )
                    stream_session_obj = stream_session.scalar_one_or_none()
                    if stream_session_obj:
                        latency_ms = int((time.time() - start_time) * 1000)
                        if tokens_used == 0:
                            tokens_used = len(response_content.split()) * 2
                        stream_session_obj.add_message(
                            message_id=assistant_message_id,
                            role="assistant",
                            content=response_content,
                            tokens_used=tokens_used,
                            latency_ms=latency_ms,
                        )
                        await stream_db.commit()

                    # Fire-and-forget episodic memory
                    store_memory_async(
                        resolved_agent, tenant_id, user_id,
                        request.content, response_content,
                        session_id, None,
                    )

            else:
                response_content = "Custom agent configuration not found."
                if enhanced:
                    yield builder.text_delta(response_content)
                else:
                    yield f"data: {json.dumps({'type': 'delta', 'content': response_content})}\n\n"

            # Estimate tokens if not available
            if tokens_used == 0:
                tokens_used = len(response_content.split()) * 2

            latency_ms = int((time.time() - start_time) * 1000)

            # Done event
            if enhanced:
                yield builder.done(
                    message_id=assistant_message_id,
                    session_id=str(session_id),
                    model=getattr(agent.custom_agent, 'model', 'unknown') if agent.custom_agent else 'builtin',
                    usage=usage_dict,
                )
            else:
                # Legacy: persist for built-in agents (custom agents persisted above on stream_db)
                if agent.is_builtin:
                    session.add_message(
                        message_id=assistant_message_id,
                        role="assistant",
                        content=response_content,
                        tokens_used=tokens_used,
                        latency_ms=latency_ms,
                    )
                    await db.commit()

                yield f"data: {json.dumps({'type': 'message', 'message_id': assistant_message_id, 'content': response_content, 'tokens_used': tokens_used, 'latency_ms': latency_ms})}\n\n"
                yield f"data: {json.dumps({'type': 'done'})}\n\n"

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Agent chat error: {error_msg}", exc_info=True)

            # Persist user message on stream_db even on failure
            try:
                async with AsyncSessionLocal() as err_db:
                    await set_tenant_context(err_db, str(tenant_id))
                    err_session = await err_db.execute(
                        select(AgentChatSession).where(AgentChatSession.id == session_id)
                    )
                    err_session_obj = err_session.scalar_one_or_none()
                    if err_session_obj:
                        await err_db.commit()
            except Exception:
                pass

            error_msg = format_error(error_msg)

            if enhanced:
                yield builder.error(message=error_msg, title="Agent Error", retryable="rate limit" in error_msg.lower())
            else:
                yield f"data: {json.dumps({'type': 'error', 'error': error_msg})}\n\n"

    sse_headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Content-Encoding": "identity",
    }

    if enhanced:
        # EventSourceResponse properly serializes builder dicts {event, id, data}
        return EventSourceResponse(
            generate_response(),
            media_type="text/event-stream",
            ping=15,
            send_timeout=30.0,
            headers=sse_headers,
        )
    else:
        # Legacy raw "data: {...}\n\n" format
        return StreamingResponse(
            generate_response(),
            media_type="text/event-stream",
            headers=sse_headers,
        )


@router.post("/agents/{agent_id}/chat/sessions/{session_id}/messages/{message_id}/feedback", response_model=FeedbackResponse)
async def submit_feedback(
    agent_id: str,
    session_id: UUID,
    message_id: UUID,
    request: FeedbackRequest,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id)
):
    """
    Submit feedback on an agent response.

    Creates a test case automatically based on the feedback:
    - Positive feedback: Creates a positive test case with expected output
    - Negative feedback: Creates a negative test case
    """
    user_id = UUID(user.id)
    agent_uuid = parse_agent_id_to_uuid(agent_id)

    # Get session
    result = await db.execute(
        select(AgentChatSession).where(
            AgentChatSession.id == session_id,
            AgentChatSession.agent_id == agent_uuid,
            AgentChatSession.user_id == user_id
        )
    )
    session = result.scalar_one_or_none()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat session not found"
        )

    # Find the message
    message = session.get_message_by_id(str(message_id))
    if not message:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Message not found"
        )

    if message.get('role') != 'assistant':
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Can only provide feedback on assistant messages"
        )

    # Get the user message that preceded this response
    user_message = session.get_previous_message(str(message_id))
    if not user_message:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not find preceding user message"
        )

    # Create test case from feedback
    test_case = AgentTestCase(
        tenant_id=tenant_id,
        agent_id=agent_uuid,
        name=AgentTestCase.generate_name_from_input(user_message.get('content', '')),
        input=user_message.get('content', ''),
        expected_output=message.get('content') if request.feedback == 'positive' else None,
        source='chat_feedback',
        source_session_id=session_id,
        source_message_id=message_id,
        is_positive=(request.feedback == 'positive'),
        evaluation_type='llm_judge',
    )

    if request.notes:
        test_case.description = request.notes

    db.add(test_case)

    # Update message with feedback
    session.set_message_feedback(str(message_id), request.feedback, str(test_case.id))

    await db.flush()
    await db.refresh(test_case)

    return FeedbackResponse(
        message_id=message_id,
        feedback=request.feedback,
        test_case_id=test_case.id,
        test_case_name=test_case.name or "Unnamed test case",
        test_case_created=True,
    )


@router.delete("/agents/{agent_id}/chat/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat_session(
    agent_id: str,
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user)
):
    """
    Archive (soft delete) a chat session.
    Supports both custom agents (UUID) and built-in agents (string ID).
    """
    user_id = UUID(user.id)
    agent_uuid = parse_agent_id_to_uuid(agent_id)

    result = await db.execute(
        select(AgentChatSession).where(
            AgentChatSession.id == session_id,
            AgentChatSession.agent_id == agent_uuid,
            AgentChatSession.user_id == user_id
        )
    )
    session = result.scalar_one_or_none()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat session not found"
        )

    # Soft delete by changing status
    session.status = 'archived'
    session.updated_at = datetime.utcnow()

    await db.commit()
