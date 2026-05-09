"""Workflow Execution API endpoint for Phase 2 agent-triggered workflows.

After the agent returns a pending_approval preview, the user
clicks Execute and this SSE endpoint streams per-node progress in real time.
"""

import asyncio
import json
import logging
import time
from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.auth.dependencies import get_current_user, get_current_tenant_id
from app.auth.providers.base import AuthUser
from app.core.database import AsyncSessionLocal, get_db, set_tenant_context
from app.models.agent_chat_session import AgentChatSession
from app.models.workflow import Workflow
from app.services.sse_builder import create_sse_builder
from app.services.workflow_orchestrator import WorkflowOrchestrator

logger = logging.getLogger(__name__)

router = APIRouter()

# Default execution timeout (seconds)
_DEFAULT_EXECUTION_TIMEOUT = 300


# --- Request schema ---

class WorkflowExecuteRequest(BaseModel):
    """Request body for executing a workflow after user approval."""

    workflow_id: str = Field(..., description="UUID of the workflow to execute")
    inputs: Dict[str, Any] = Field(
        default_factory=dict,
        description="Input variables for the workflow start node",
    )


# --- Endpoint ---

@router.post(
    "/agents/{agent_id}/chat/sessions/{session_id}/workflow-execute",
    tags=["agent-studio"],
)
async def execute_workflow_stream(
    agent_id: str,
    session_id: UUID,
    request: WorkflowExecuteRequest,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """
    Execute a workflow and stream per-node progress via SSE.

    This is Phase 2 of the two-phase workflow execution pattern:
    1. Agent calls workflow tool -> returns step preview with pending_approval
    2. User clicks Execute -> this endpoint streams live per-node progress

    SSE events emitted:
    - workflow_progress: per-node status updates (active/complete/error/halted)
    - workflow_complete: final result with all node outputs
    - error: on failure
    """
    from app.api.v1.agent_chat import parse_agent_id_to_uuid

    user_id = UUID(user.id)
    agent_uuid = parse_agent_id_to_uuid(agent_id)

    # Validate session ownership (auth + tenant RLS)
    result = await db.execute(
        select(AgentChatSession).where(
            AgentChatSession.id == session_id,
            AgentChatSession.agent_id == agent_uuid,
            AgentChatSession.user_id == user_id,
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat session not found",
        )

    # Parse and validate workflow_id
    try:
        workflow_uuid = UUID(request.workflow_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid workflow_id format",
        )

    # Load workflow with tenant RLS
    wf_result = await db.execute(
        select(Workflow).where(
            Workflow.id == workflow_uuid,
            Workflow.tenant_id == tenant_id,
        )
    )
    workflow = wf_result.scalar_one_or_none()
    if not workflow:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found",
        )

    workflow_spec = workflow.spec
    workflow_name = workflow.name
    input_variables = request.inputs

    async def _stream_workflow_execution():
        """Generator that streams SSE events for workflow execution."""
        builder = create_sse_builder()
        start_time = time.time()

        try:
            # Create a SEPARATE AsyncSession for workflow execution
            # to avoid RLS context destruction (documented gotcha)
            async with AsyncSessionLocal() as exec_db:
                await set_tenant_context(exec_db, str(tenant_id))
                # Also set user context for audit logging
                await exec_db.execute(
                    text(
                        "SELECT set_config('app.current_user_id', :uid, true)"
                    ),
                    {"uid": str(user_id)},
                )

                orchestrator = WorkflowOrchestrator(
                    db=exec_db,
                    user_id=str(user_id),
                    tenant_id=tenant_id,
                    trigger="agent_chat",
                )

                # Stream orchestrator events, translating to SSE
                async for event in orchestrator.execute_workflow_stream(
                    workflow_spec=workflow_spec,
                    input_variables=input_variables,
                    workflow_id=workflow_uuid,
                    save_logs=True,
                ):
                    event_type = event.get("type")
                    elapsed_ms = int((time.time() - start_time) * 1000)

                    if event_type == "progress":
                        # Node is starting execution
                        node_id = event.get("node_id", "")
                        yield builder.workflow_progress(
                            workflow_id=str(workflow_uuid),
                            step_id=node_id,
                            label=_node_label(workflow_spec, node_id),
                            status="active",
                            message="Executing...",
                            elapsed_ms=elapsed_ms,
                        )

                    elif event_type == "node_complete":
                        node_id = event.get("node_id", "")
                        result_data = event.get("result", {})
                        node_status = result_data.get("status", "success")

                        # Map orchestrator status to our SSE status
                        if node_status == "failed":
                            sse_status = "error"
                            message = result_data.get("error", "Node failed")
                        elif node_status == "halted":
                            sse_status = "halted"
                            message = "Human-in-the-loop approval required"
                        else:
                            sse_status = "complete"
                            message = "Done"

                        yield builder.workflow_progress(
                            workflow_id=str(workflow_uuid),
                            step_id=node_id,
                            label=_node_label(workflow_spec, node_id),
                            status=sse_status,
                            message=message,
                            elapsed_ms=elapsed_ms,
                        )

                    elif event_type == "node_error":
                        node_id = event.get("node_id", "")
                        yield builder.workflow_progress(
                            workflow_id=str(workflow_uuid),
                            step_id=node_id,
                            label=_node_label(workflow_spec, node_id),
                            status="error",
                            message=event.get("error", "Unknown error"),
                            elapsed_ms=elapsed_ms,
                        )

                    elif event_type == "complete":
                        # Final completion event
                        yield builder.workflow_complete(
                            workflow_id=str(workflow_uuid),
                            workflow_name=workflow_name,
                            results=event.get("results", {}),
                            executed_nodes=event.get("executed_nodes", 0),
                            total_nodes=event.get("total_nodes", 0),
                            elapsed_ms=elapsed_ms,
                        )

                    # node_started and node_input_received are intermediate
                    # events; we skip them to avoid noise (progress covers it)

        except asyncio.TimeoutError:
            elapsed_ms = int((time.time() - start_time) * 1000)
            yield builder.error(
                message=f"Workflow execution timed out after {_DEFAULT_EXECUTION_TIMEOUT}s",
                title="Execution Timeout",
                retryable=True,
            )
        except Exception as e:
            logger.error(f"Workflow execution error: {e}", exc_info=True)
            yield builder.error(
                message=str(e),
                title="Workflow Execution Error",
                retryable=False,
            )

    sse_headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Content-Encoding": "identity",
    }

    return EventSourceResponse(
        _stream_workflow_execution(),
        media_type="text/event-stream",
        ping=15,
        send_timeout=30.0,
        headers=sse_headers,
    )


def _node_label(workflow_spec: Dict[str, Any], node_id: str) -> str:
    """Extract a human-readable label for a node from the workflow spec."""
    for node in workflow_spec.get("nodes", []):
        if node.get("id") == node_id:
            data = node.get("data", {})
            return (
                data.get("label")
                or data.get("name")
                or node.get("type", "Unknown")
            )
    return node_id
