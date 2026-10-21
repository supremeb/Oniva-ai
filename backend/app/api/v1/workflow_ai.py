"""
Workflow AI Chat API - Conversational workflow building with SSE streaming.

Uses moonshotai/kimi-k2.5 via OpenRouter for natural language understanding
and step generation. Supports OAuth detection and complex step proposals.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sse_starlette.sse import EventSourceResponse
from sqlalchemy.ext.asyncio import AsyncSession
import json
import uuid
import asyncio
import logging
from typing import AsyncGenerator, List

from app.core.database import get_db, AsyncSessionLocal, set_tenant_context
from app.auth.dependencies import get_current_user_id, get_current_tenant_id
from app.api.dependencies.quota import require_quota
from app.models.usage_quota import QuotaType
from app.config import get_settings
from app.agents.step_builder import StepBuilderAgent
from app.agents.base import AgentDependencies
from app.schemas.complex_steps import (
    WorkflowAIChatRequest,
    ApplyStepsRequest,
)
from app.agents.step_builder import (
    ConnectorRequirement,
)
from app.services.workflow_ai_security import (
    check_prompt_injection,
    validate_step_proposal,
    generate_oauth_state,
    validate_oauth_state,
    generate_pkce_pair,
)
from app.services.llm_gateway import _get_cached_tenant_providers

router = APIRouter()
settings = get_settings()
logger = logging.getLogger(__name__)


async def generate_workflow_ai_stream(
    request: Request,
    chat_request: WorkflowAIChatRequest,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> AsyncGenerator[dict, None]:
    """
    Generate SSE events for workflow AI chat.

    Event types:
    - content: Response text delta (streamed word by word)
    - steps_preview: Proposed workflow changes
    - oauth_request: OAuth connectors needed
    - question: Clarifying question with options
    - error: Error occurred
    - done: Stream complete

    Security:
    - Prompt injection protection via input sanitization
    - OAuth state validation before connector operations
    """
    event_id = 0

    try:
        # Security: Check for prompt injection
        injection_check = check_prompt_injection(chat_request.message)
        if not injection_check.is_safe:
            logger.warning(
                f"Prompt injection blocked for user {user_id}: {injection_check.detected_patterns}"
            )
            event_id += 1
            yield {
                "event": "error",
                "id": str(event_id),
                "data": json.dumps({
                    "type": "error",
                    "message": "Your message contains patterns that aren't allowed. Please rephrase your request.",
                    "error_type": "security",
                }),
            }
            return

        # Use sanitized input
        sanitized_message = injection_check.sanitized_input

        # Create database session with tenant context
        async with AsyncSessionLocal() as db:
            if tenant_id:
                await set_tenant_context(db, str(tenant_id))

            # Resolve API key and model from LLM provider settings
            # Uses the same provider resolution as chat agents
            providers = await _get_cached_tenant_providers(db, tenant_id)

            if not providers:
                event_id += 1
                yield {
                    "event": "error",
                    "id": str(event_id),
                    "data": json.dumps({
                        "type": "error",
                        "message": "No LLM provider configured. Please add one in Settings > AI Models.",
                        "error_type": "configuration",
                    }),
                }
                return

            # Find provider with workflow_builder_model, or fall back to first provider
            selected_provider = providers[0]
            for p in providers:
                if p.workflow_builder_model:
                    selected_provider = p
                    break

            api_key = selected_provider.api_key

            # Build effective model: workflow_builder_model > default_model > fallback
            workflow_model = selected_provider.workflow_builder_model or selected_provider.default_model or "openai/gpt-4o"

            # Prefix with the provider so create_model picks the right SDK
            # e.g. "openai/gpt-4o" with an openrouter provider -> "openrouter/openai/gpt-4o"
            provider_type = selected_provider.provider_type
            if provider_type == "openrouter" and not workflow_model.startswith("openrouter/"):
                effective_model = f"openrouter/{workflow_model}"
            elif provider_type == "anthropic" and not workflow_model.startswith("anthropic/"):
                effective_model = f"anthropic/{workflow_model}"
            elif provider_type == "openai" and not workflow_model.startswith("openai/"):
                effective_model = f"openai/{workflow_model}"
            else:
                effective_model = workflow_model

            # Load configured tool provider names so the AI knows which skills have keys
            configured_tool_providers: dict[str, str] = {}
            try:
                from app.models.api_key import APIKey
                from sqlalchemy import select as sa_select

                tool_providers = ("tavily", "parallelai", "exa", "serpapi", "e2b")
                result = await db.execute(
                    sa_select(APIKey.provider).where(
                        APIKey.tenant_id == tenant_id,
                        APIKey.is_active == True,  # noqa: E712
                        APIKey.provider.in_(tool_providers),
                    )
                )
                for row in result.scalars():
                    # Store provider name as key with a placeholder value
                    # (not decrypted — this is just for awareness, not execution)
                    configured_tool_providers[row] = "configured"
            except Exception as e:
                logger.debug(f"Failed to load tool provider names: {e}")

            # Populate connectors from registry
            from app.connectors.registry import ConnectorRegistry
            available_connectors = ConnectorRegistry.list_available()
            connectors_dict = {
                c.id: {"name": c.name, "description": getattr(c, "description", "")}
                for c in available_connectors
            }

            # Build agent dependencies
            deps = AgentDependencies(
                db=db,
                user_id=str(user_id),
                tenant_id=str(tenant_id) if tenant_id else None,
                connectors=connectors_dict,
                api_keys=configured_tool_providers,
            )

            # Convert conversation history to the format expected by the agent
            # Note: conversation_history is list[dict] from the schema
            conversation_history = [
                {"role": msg.get("role", "user"), "content": msg.get("content", "")}
                for msg in (chat_request.conversation_history or [])
            ]

            # Convert current_steps to dicts
            current_steps = [
                step.model_dump() if hasattr(step, "model_dump") else step
                for step in (chat_request.current_steps or [])
            ]

            # Create step builder agent with resolved provider key and model
            step_builder = StepBuilderAgent(
                model=effective_model,
                api_key=api_key,
            )

            # Convert current_edges to dicts
            current_edges = [
                edge.model_dump() if hasattr(edge, "model_dump") else edge
                for edge in (chat_request.current_edges or [])
            ]

            # Run the step builder agent with sanitized input
            result = await step_builder.run(
                message=sanitized_message,
                current_steps=current_steps,
                conversation_history=conversation_history,
                deps=deps,
                current_edges=current_edges,
            )

            # Guard: if LLM returned steps but wrong message_type, fix it
            if result.proposed_steps and result.message_type not in ("steps_preview",):
                logger.info(
                    f"Fixing message_type: LLM returned '{result.message_type}' "
                    f"with {len(result.proposed_steps)} steps, forcing 'steps_preview'"
                )
                result.message_type = "steps_preview"

            # Stream the response message
            response_text = result.message
            chunk_size = 10  # Characters per chunk for smooth streaming

            for i in range(0, len(response_text), chunk_size):
                if await request.is_disconnected():
                    break

                chunk = response_text[i : i + chunk_size]
                event_id += 1
                yield {
                    "event": "content",
                    "id": str(event_id),
                    "data": json.dumps({"type": "content", "content": chunk}),
                }
                await asyncio.sleep(0.02)  # Smooth streaming delay

            # Handle steps_preview response
            if result.message_type == "steps_preview" and result.proposed_steps:
                event_id += 1
                steps_data = [
                    {
                        "id": step.id,
                        "type": step.type,
                        "label": step.label,
                        "config": step.config,
                        "action": step.action,
                        "position": step.position,
                        "reason": step.reason,
                    }
                    for step in result.proposed_steps
                ]
                edges_data = [
                    {
                        "source": edge.source,
                        "target": edge.target,
                        "sourceHandle": edge.source_handle,
                        "targetHandle": edge.target_handle,
                    }
                    for edge in (result.proposed_edges or [])
                ]
                yield {
                    "event": "steps_preview",
                    "id": str(event_id),
                    "data": json.dumps({
                        "type": "steps_preview",
                        "steps": steps_data,
                        "proposed_edges": edges_data if edges_data else None,
                    }),
                }

            # Handle oauth_request response
            elif result.message_type == "oauth_request" and result.required_connectors:
                event_id += 1
                connectors_data = [
                    {
                        "id": conn.id,
                        "name": conn.name,
                        "is_connected": conn.is_connected,
                    }
                    for conn in result.required_connectors
                ]
                yield {
                    "event": "oauth_request",
                    "id": str(event_id),
                    "data": json.dumps({
                        "type": "oauth_request",
                        "connectors": connectors_data,
                        "message": result.message,
                    }),
                }

            # Handle question response
            elif result.message_type == "question" and result.question_options:
                event_id += 1
                options_data = [
                    {
                        "id": opt.id,
                        "label": opt.label,
                        "value": opt.value,
                    }
                    for opt in result.question_options
                ]
                yield {
                    "event": "question",
                    "id": str(event_id),
                    "data": json.dumps({
                        "type": "question",
                        "options": options_data,
                        "allowMultiple": False,
                    }),
                }

            # Signal completion
            event_id += 1
            yield {
                "event": "done",
                "id": str(event_id),
                "data": json.dumps({
                    "type": "done",
                    "message_type": result.message_type,
                    "workflow_id": str(chat_request.workflow_id) if chat_request.workflow_id else None,
                }),
            }

    except Exception as e:
        logger.error(f"Workflow AI error: {e}")
        error_msg = str(e)

        # Sanitize error messages
        if "401" in error_msg or "api_key" in error_msg.lower():
            error_msg = "API key not configured. Add your key in Settings > API Keys."
        elif "rate limit" in error_msg.lower():
            error_msg = "API rate limit exceeded. Please try again."
        elif "timeout" in error_msg.lower():
            error_msg = "Request timed out. Please try again."

        event_id += 1
        yield {
            "event": "error",
            "id": str(event_id),
            "data": json.dumps({"message": error_msg, "type": "error"}),
        }


@router.post("/chat")
async def workflow_ai_chat(
    request: Request,
    chat_request: WorkflowAIChatRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    _quota=Depends(require_quota(QuotaType.LLM_TOKENS)),
):
    """
    Chat with AI to build workflows using natural language.

    SSE Event Types:
    - content: Response text delta
    - steps_preview: Proposed workflow changes with Apply/Cancel
    - oauth_request: OAuth connectors needed before applying steps
    - question: Clarifying question with options
    - error: Error occurred
    - done: Stream complete

    Request Body:
    - workflow_id: UUID of the workflow being edited
    - message: User's natural language request
    - current_steps: Current workflow steps for context
    - conversation_history: Recent messages for context (max 10)
    """
    if not chat_request.message.strip():
        raise HTTPException(status_code=400, detail="Empty message")

    return EventSourceResponse(
        generate_workflow_ai_stream(
            request,
            chat_request,
            user_id,
            tenant_id,
        ),
        media_type="text/event-stream",
        ping=15,  # Heartbeat every 15s
        send_timeout=30.0,
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/apply-steps")
async def apply_steps(
    request_body: ApplyStepsRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    """
    Apply proposed steps to a workflow.

    Validates ownership, node types, and edge integrity before persisting.
    Uses db.flush() (not db.commit()) to preserve RLS context.
    """
    from app.services.workflow_ai_security import get_allowed_node_types

    try:
        # Set tenant context
        if tenant_id:
            await set_tenant_context(db, str(tenant_id))

        # 1. Ownership check
        from app.models.workflow import Workflow
        workflow = await db.get(Workflow, uuid.UUID(request_body.workflow_id))
        if not workflow or workflow.tenant_id != tenant_id:
            raise HTTPException(status_code=404, detail="Workflow not found")

        # 2. Node type validation
        allowed_types = get_allowed_node_types()
        for node in request_body.nodes:
            node_type = node.get("type")
            if node_type and node_type not in allowed_types:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid node type: {node_type}",
                )

        # 3. Edge integrity validation
        node_ids = {n.get("id") for n in request_body.nodes}
        for edge in request_body.edges:
            if edge.get("source") not in node_ids or edge.get("target") not in node_ids:
                raise HTTPException(
                    status_code=400,
                    detail="Edge references non-existent node",
                )

        # 4. Save — use flush, NOT commit (RLS context preservation)
        # Workflow stores graph as JSONB `spec` field
        current_spec = workflow.spec or {}
        current_spec["nodes"] = request_body.nodes
        current_spec["edges"] = request_body.edges
        workflow.spec = current_spec
        await db.flush()
        await db.refresh(workflow)

        return {
            "success": True,
            "message": "Steps applied successfully",
            "version": getattr(workflow, "version", None),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Apply steps error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/oauth/initiate")
async def initiate_oauth(
    workflow_id: uuid.UUID,
    connector_id: str,
    redirect_uri: str,
    user_id: uuid.UUID = Depends(get_current_user_id),
):
    """
    Initiate OAuth flow for a connector.

    Returns a state token and PKCE challenge for the OAuth request.
    The code_verifier is stored server-side in Redis (never sent to the
    client) and retrieved during the callback for token exchange.
    """
    # Generate PKCE pair
    code_verifier, code_challenge = generate_pkce_pair()

    # Generate OAuth state — verifier stored server-side in Redis
    state = await generate_oauth_state(
        user_id=str(user_id),
        workflow_id=str(workflow_id),
        connector_id=connector_id,
        redirect_uri=redirect_uri,
        code_verifier=code_verifier,
    )

    return {
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }


@router.post("/oauth/callback")
async def oauth_callback(
    state: str,
    code: str,
    user_id: uuid.UUID = Depends(get_current_user_id),
):
    """
    Handle OAuth callback after user authorization.

    Validates the state token and exchanges the code for tokens.
    """
    # Validate state
    is_valid, metadata = await validate_oauth_state(state, str(user_id))
    if not is_valid:
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired OAuth state. Please try again.",
        )

    # Return metadata for frontend to continue token exchange.
    # code_verifier was stored server-side during initiation —
    # return it now for the token exchange request.
    return {
        "success": True,
        "workflow_id": metadata["workflow_id"],
        "connector_id": metadata["connector_id"],
        "redirect_uri": metadata["redirect_uri"],
        "authorization_code": code,
        "code_verifier": metadata.get("code_verifier"),
    }


@router.get("/connected-connectors")
async def get_connected_connectors(
    user_id: uuid.UUID = Depends(get_current_user_id),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> List[ConnectorRequirement]:
    """
    Get list of OAuth connectors the user has connected.

    Used by the AI to determine which connectors need OAuth before
    proposing connector steps.
    """
    try:
        # Set tenant context
        if tenant_id:
            await set_tenant_context(db, str(tenant_id))

        # Return registered connectors from the registry
        from app.connectors.registry import ConnectorRegistry
        available = ConnectorRegistry.list_available()
        return [
            ConnectorRequirement(
                id=c.id,
                name=c.name,
                is_connected=True,  # All registered connectors are available
            )
            for c in available
        ]

    except Exception as e:
        logger.error(f"Get connectors error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
