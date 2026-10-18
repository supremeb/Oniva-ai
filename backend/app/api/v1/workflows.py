"""
Workflow API endpoints
Supports workflow execution, persistence, and streaming
"""

import asyncio
import json
import logging
import uuid
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Depends, Header, Query
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
import sqlalchemy as sa
from sqlalchemy import select, func, or_, and_, text
from sqlalchemy.orm import defer

from app.core.database import get_db
from app.models.workflow_permission import WorkflowPermission, GranteeType, PermissionLevel
from app.services.workflow_validator import WorkflowValidator
from app.services.workflow_orchestrator import WorkflowOrchestrator
from app.services.durable_workflow_engine import DurableWorkflowEngine
from app.services import workflow_engine_runs as engine_runs
from app.services import workflow_batch_engine as batch_engine
from app.services import workflow_stream_adapter
from app.services.webhook_service import (
    WebhookConfig, get_webhook_service
)
from app.models.workflow import Workflow, WorkflowExecution
from app.models.workflow_batch import WorkflowBatch, WorkflowBatchItem, BatchStatus, BatchItemStatus
from app.auth.dependencies import get_current_user, get_current_tenant_id, get_current_user_id
from app.auth.providers.base import AuthUser
from app.api.dependencies.quota import require_quota
from app.models.usage_quota import QuotaType
from app.services.quota_service import QuotaService
from app.config import get_settings
from app.security.url_validator import is_url_safe

logger = logging.getLogger(__name__)
settings = get_settings()

# Default timeout for manual/API workflow executions (seconds)
_MANUAL_EXECUTION_TIMEOUT = 3600

router = APIRouter()


# --- Pydantic Models ---

class WorkflowNode(BaseModel):
    id: str
    type: str
    data: Dict[str, Any]
    position: Dict[str, float]


class WorkflowEdge(BaseModel):
    id: str
    source: str
    target: str
    sourceHandle: Optional[str] = None
    targetHandle: Optional[str] = None


class WorkflowSpec(BaseModel):
    nodes: List[WorkflowNode]
    edges: List[WorkflowEdge]


class WorkflowCreate(BaseModel):
    name: str = "Untitled Workflow"
    description: Optional[str] = None
    spec: WorkflowSpec


class WorkflowUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    spec: Optional[WorkflowSpec] = None


class WorkflowResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: Optional[str]
    spec: Dict[str, Any]
    created_at: datetime
    updated_at: datetime
    # Sharing info (Enterprise)
    is_shared: bool = False
    is_owner: bool = True
    owner_id: Optional[uuid.UUID] = None
    owner_name: Optional[str] = None
    permission: Optional[str] = None  # 'view', 'execute', 'edit', 'admin' for shared workflows
    share_source: Optional[str] = None  # 'direct', 'role', 'organization' for shared workflows

    class Config:
        from_attributes = True


class WebhookConfigRequest(BaseModel):
    """Webhook configuration for workflow completion callbacks.

    Enterprise feature: Enable async notifications when workflows complete.
    """
    url: str = Field(..., description="Webhook URL to POST completion event to")
    secret: Optional[str] = Field(None, description="HMAC-SHA256 secret for signature verification")
    headers: Optional[Dict[str, str]] = Field(None, description="Custom headers to include")


class ExecuteWorkflowRequest(BaseModel):
    """Request to execute a workflow"""
    nodes: List[WorkflowNode]
    edges: List[WorkflowEdge]
    inputs: Optional[Dict[str, str]] = None  # Input variables for execution
    workflow_id: Optional[uuid.UUID] = None  # ID of the workflow being executed (for logging)
    # Enterprise feature: Webhook callback on completion
    webhook: Optional[WebhookConfigRequest] = Field(
        None,
        description="Webhook configuration for async completion callbacks"
    )


class WorkflowOutputNode(BaseModel):
    """Single output from an Output node in workflow.

    Part of Phase 3: User Experience improvements.
    """
    node_id: str
    node_name: str
    output_type: str  # "text", "json", "markdown", "table"
    content: Any
    timestamp: datetime


class WorkflowResult(BaseModel):
    status: str
    message: str
    results: Dict[str, Any] = {}
    executed_nodes: int = 0
    total_nodes: int = 0
    # NEW: Structured outputs from Output nodes
    outputs: List[WorkflowOutputNode] = []
    # Set for runs that execute on the engine (plan 003 U15)
    execution_id: Optional[str] = None


class WorkflowListItem(BaseModel):
    """Workflow metadata for list views — excludes spec (50-200KB) to reduce payload."""
    id: uuid.UUID
    name: str
    description: Optional[str]
    created_at: datetime
    updated_at: datetime
    is_shared: bool = False
    is_owner: bool = True
    owner_id: Optional[uuid.UUID] = None
    owner_name: Optional[str] = None
    permission: Optional[str] = None
    share_source: Optional[str] = None

    class Config:
        from_attributes = True


class PaginatedWorkflowResponse(BaseModel):
    """Paginated list of workflows"""
    items: List[WorkflowListItem]
    total: int
    skip: int
    limit: int
    has_more: bool


# --- Helper Functions ---

def _detect_output_type(content: Any) -> str:
    """Detect the type of output content for rendering.

    Args:
        content: The output content to analyze

    Returns:
        Output type: "text", "json", "markdown", or "table"
    """
    if isinstance(content, dict) or isinstance(content, list):
        return "json"

    if not isinstance(content, str):
        return "text"

    content_str = str(content)

    # Check for markdown indicators
    markdown_indicators = [
        content_str.startswith("# "),  # Header
        content_str.startswith("## "),
        content_str.startswith("### "),
        "```" in content_str,  # Code block
        "\n- " in content_str,  # List
        "\n* " in content_str,  # List
        "\n1. " in content_str,  # Numbered list
        "**" in content_str,  # Bold
        "__" in content_str,  # Bold/italic
    ]

    if any(markdown_indicators):
        return "markdown"

    # Check for table format (markdown table or TSV)
    lines = content_str.split("\n")
    if len(lines) >= 2:
        # Markdown table: | col1 | col2 |
        if "|" in lines[0] and "|" in lines[1]:
            return "table"
        # TSV format
        if "\t" in lines[0] and "\t" in lines[1]:
            return "table"

    return "text"


def _extract_workflow_outputs(
    results: Dict[str, Any],
    nodes_dict: Dict[str, WorkflowNode]
) -> List[WorkflowOutputNode]:
    """Extract structured outputs from workflow execution results.

    Filters results to only include Output nodes and formats them
    for display in the chat interface.

    Args:
        results: Raw execution results from orchestrator
        nodes_dict: Mapping of node_id to WorkflowNode

    Returns:
        List of WorkflowOutputNode for display
    """
    outputs = []

    for node_id, result_data in results.items():
        node = nodes_dict.get(node_id)
        if not node:
            continue

        # Check if this is an Output node
        node_type = node.type.lower() if node.type else ""
        if node_type not in ["output", "end"]:
            continue

        # Extract output content
        output = result_data.get("output", {}) if isinstance(result_data, dict) else {}
        content = output.get("content") or output.get("text") or output.get("result")

        # Skip if no content
        if content is None:
            # Try to get from the result directly
            content = result_data.get("output") if isinstance(result_data, dict) else None
            if content is None:
                continue

        # Get node name from data
        node_name = node.data.get("label") or node.data.get("name") or f"Output {node_id[:8]}"

        # Detect output type
        output_type = _detect_output_type(content)

        outputs.append(WorkflowOutputNode(
            node_id=node_id,
            node_name=node_name,
            output_type=output_type,
            content=content,
            timestamp=datetime.utcnow()
        ))

    return outputs


# --- Permission Helpers ---

async def _require_execute_permission(
    workflow_id: uuid.UUID,
    user_id: uuid.UUID,
    user_role: str,
    tenant_id: uuid.UUID,
    db: AsyncSession,
) -> None:
    """Verify user has execute permission on workflow. Raises 403/404."""
    query = select(Workflow).where(
        Workflow.id == workflow_id,
        Workflow.tenant_id == tenant_id,
    )
    result = await db.execute(query)
    workflow = result.scalars().first()

    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    # Owner always has execute permission
    if workflow.user_id == user_id:
        return

    # Check shared permission
    perm_info = await _get_user_effective_permission(
        workflow_id, user_id, user_role, tenant_id, db
    )
    if not perm_info:
        raise HTTPException(status_code=403, detail="You don't have access to this workflow")

    # Need execute or higher
    hierarchy = {"view": 0, "execute": 1, "edit": 2, "admin": 3}
    if hierarchy.get(perm_info[0], -1) < hierarchy["execute"]:
        raise HTTPException(status_code=403, detail="You need execute permission to run this workflow")


async def _engine_run_response(
    db: AsyncSession,
    *,
    workflow: Any,
    workflow_spec: Dict[str, Any],
    inputs: Dict[str, Any],
    nodes_dict: Dict[str, Any],
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    trigger: str,
    request_key: Optional[str],
    version_id: Optional[uuid.UUID] = None,
    label: str = "Workflow",
):
    """Start the run on the engine and wait (bounded) for it: ``WorkflowResult`` when it finishes,
    ``202 {execution_id, status}`` while it is still going (plan 003 U15)."""
    execution, task_id = await engine_runs.start_workflow_run(
        db, workflow, workflow_spec, inputs, user_id=user_id, tenant_id=tenant_id,
        trigger=trigger, request_key=request_key, version_id=version_id,
    )
    outcome = None
    if execution.status == "running":
        outcome = await engine_runs.await_finished(db, execution, task_id)
        if outcome is None:
            return JSONResponse(status_code=202, content={"execution_id": str(execution.id), "status": "running"})
        await db.refresh(execution)
        status_value = engine_runs.outcome_status(outcome)
    elif execution.status in engine_runs.TERMINAL:
        status_value = execution.status
    else:  # queued: the start has not gone through yet; the reconciler owns it
        return JSONResponse(status_code=202, content={"execution_id": str(execution.id), "status": execution.status})
    results = engine_runs.results_from_trace(execution)
    outputs = _extract_workflow_outputs(results=results, nodes_dict=nodes_dict)
    executed = engine_runs.executed_count(results)
    total = execution.total_nodes or len(nodes_dict)
    return WorkflowResult(
        status=status_value,
        message=f"{label} executed: {executed}/{total} nodes",
        results=results,
        executed_nodes=executed,
        total_nodes=total,
        outputs=outputs,
        execution_id=str(execution.id),
    )


# --- Endpoints ---

@router.post("/execute", response_model=WorkflowResult)
async def execute_workflow(
    request: ExecuteWorkflowRequest,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    user_id: uuid.UUID = Depends(get_current_user_id),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    _quota=Depends(require_quota(QuotaType.EXECUTIONS)),
):
    """
    Execute a workflow from React Flow graph.
    Uses node executors for all Dify-compatible node types.
    RLS ensures tenant isolation automatically.

    Returns structured outputs from Output nodes for display in chat.

    Note: When USE_DURABLE_WORKFLOWS is enabled, automatically uses
    DurableWorkflowEngine for crash recovery and checkpointing.
    """
    # SSRF validation on webhook URL if provided
    if request.webhook:
        ssrf_error = is_url_safe(request.webhook.url)
        if ssrf_error:
            raise HTTPException(status_code=400, detail=f"Invalid webhook URL: {ssrf_error}")

    # Authorization: check execute permission if workflow_id provided
    if request.workflow_id:
        await _require_execute_permission(
            request.workflow_id, user_id, user.role, tenant_id, db
        )

    if await engine_runs.tenant_uses_engine_workflows(db, tenant_id):
        # Engine tenants: an engine run, no in-process orchestrator (plan 003 U15). Errors map to
        # 400 (invalid workflow) and 502 (engine unavailable) instead of the legacy blanket 500.
        input_variables = dict(request.inputs or {})
        if "sys.query" not in input_variables and "query" in input_variables:
            input_variables["sys.query"] = input_variables["query"]
        engine_result = await _engine_run_response(
            db,
            workflow=request.workflow_id,
            workflow_spec={
                "nodes": [n.model_dump() for n in request.nodes],
                "edges": [e.model_dump() for e in request.edges],
            },
            inputs=input_variables,
            nodes_dict={n.id: n for n in request.nodes},
            user_id=user_id, tenant_id=tenant_id, trigger="manual", request_key=idempotency_key,
        )
        if request.webhook and isinstance(engine_result, WorkflowResult):
            await get_webhook_service().send_completion_webhook(
                config=WebhookConfig(
                    url=request.webhook.url, secret=request.webhook.secret, headers=request.webhook.headers or {}
                ),
                execution_id=engine_result.execution_id or str(uuid.uuid4()),
                tenant_id=str(tenant_id),
                workflow_id=str(request.workflow_id) if request.workflow_id else None,
                status=engine_result.status,
                results=engine_result.results,
                executed_nodes=engine_result.executed_nodes,
                total_nodes=engine_result.total_nodes,
                message=engine_result.message,
            )
        return engine_result

    try:
        # Convert inputs to workflow variables
        input_variables = request.inputs or {}

        # Add sys.query if provided
        if "sys.query" in input_variables:
            pass  # Already there
        elif "query" in input_variables:
            input_variables["sys.query"] = input_variables["query"]

        # Build node lookup for extracting Output node data
        nodes_dict = {n.id: n for n in request.nodes}

        workflow_spec_dict = {
            "nodes": [n.model_dump() for n in request.nodes],
            "edges": [e.model_dump() for e in request.edges]
        }

        # Use durable engine if feature flag is enabled (C4 fix: make durability default)
        if settings.USE_DURABLE_WORKFLOWS:
            try:
                engine = DurableWorkflowEngine(
                    db=db,
                    user_id=str(user_id),
                    tenant_id=tenant_id
                )
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
            result = await asyncio.wait_for(
                engine.execute_workflow(
                    workflow_spec=workflow_spec_dict,
                    input_variables=input_variables,
                    workflow_id=request.workflow_id,
                ),
                timeout=_MANUAL_EXECUTION_TIMEOUT,
            )
        else:
            # Fallback to standard orchestrator if flag disabled
            orchestrator = WorkflowOrchestrator(
                db=db,
                user_id=str(user_id),
                tenant_id=tenant_id,
                trigger="manual"
            )
            result = await asyncio.wait_for(
                orchestrator.execute_workflow(
                    workflow_spec=workflow_spec_dict,
                    input_variables=input_variables,
                ),
                timeout=_MANUAL_EXECUTION_TIMEOUT,
            )

        # Extract outputs from Output nodes
        outputs = _extract_workflow_outputs(
            results=result.get("results", {}),
            nodes_dict=nodes_dict
        )

        workflow_result = WorkflowResult(
            status=result["status"],
            message=f"Workflow executed: {result['executed_nodes']}/{result['total_nodes']} nodes",
            results=result["results"],
            executed_nodes=result["executed_nodes"],
            total_nodes=result["total_nodes"],
            outputs=outputs
        )

        # Send webhook callback if configured (fire-and-forget)
        if request.webhook:
            webhook_config = WebhookConfig(
                url=request.webhook.url,
                secret=request.webhook.secret,
                headers=request.webhook.headers or {}
            )
            execution_id = str(uuid.uuid4())  # Generate execution ID
            webhook_service = get_webhook_service()

            # Fire-and-forget: don't block response
            await webhook_service.send_completion_webhook(
                config=webhook_config,
                execution_id=execution_id,
                tenant_id=str(tenant_id),
                workflow_id=str(request.workflow_id) if request.workflow_id else None,
                status=result["status"],
                results=result["results"],
                executed_nodes=result["executed_nodes"],
                total_nodes=result["total_nodes"],
                message=workflow_result.message
            )

        return workflow_result
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=f"Workflow execution timed out after {_MANUAL_EXECUTION_TIMEOUT} seconds"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/execute-stream")
async def execute_workflow_stream(
    request: ExecuteWorkflowRequest,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    user_id: uuid.UUID = Depends(get_current_user_id),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    _quota=Depends(require_quota(QuotaType.EXECUTIONS)),
):
    """
    Execute workflow with streaming updates.
    Returns Server-Sent Events for real-time progress.
    RLS ensures tenant isolation automatically.
    """
    # Authorization: check execute permission if workflow_id provided
    if request.workflow_id:
        await _require_execute_permission(
            request.workflow_id, user_id, user.role, tenant_id, db
        )

    input_variables = request.inputs or {}
    if "query" in input_variables and "sys.query" not in input_variables:
        input_variables["sys.query"] = input_variables["query"]

    if await engine_runs.tenant_uses_engine_workflows(db, tenant_id):
        # Engine tenants (plan 003 U17): start the run before the stream opens, so an invalid workflow (400)
        # or an unavailable engine (502) is a real error response, then follow the engine watch.
        execution, task_id = await engine_runs.start_workflow_run(
            db, request.workflow_id,
            {"nodes": [n.model_dump() for n in request.nodes], "edges": [e.model_dump() for e in request.edges]},
            dict(input_variables), user_id=user_id, tenant_id=tenant_id, trigger="manual",
        )
        updates = workflow_stream_adapter.watch_events(engine_runs.tenant_factory(tenant_id), execution.id, task_id)

        async def engine_event_stream():
            try:
                async for update in updates:
                    yield f"data: {json.dumps(update)}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'error': str(e) or type(e).__name__})}\n\n"

        return StreamingResponse(engine_event_stream(), media_type="text/event-stream")

    orchestrator = WorkflowOrchestrator(
        db=db,
        user_id=str(user_id),
        tenant_id=tenant_id,
        trigger="manual"
    )

    async def event_stream():
        try:
            async for update in orchestrator.execute_workflow_stream(
                workflow_spec={
                    "nodes": [n.model_dump() for n in request.nodes],
                    "edges": [e.model_dump() for e in request.edges]
                },
                input_variables=input_variables,
                workflow_id=request.workflow_id,
                save_logs=True
            ):
                yield f"data: {json.dumps(update)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
    )


async def _get_user_effective_permission(
    workflow_id: uuid.UUID,
    user_id: uuid.UUID,
    user_role: str,
    tenant_id: uuid.UUID,
    db: AsyncSession
) -> Optional[tuple[str, str]]:
    """Get the highest permission level a user has on a workflow.

    Returns tuple of (permission, source) or None if no access.
    Source is 'direct', 'role', or 'organization'.
    """
    now = datetime.now(timezone.utc)

    # Query all applicable permissions
    query = select(WorkflowPermission).where(
        WorkflowPermission.workflow_id == workflow_id,
        WorkflowPermission.tenant_id == tenant_id,
        or_(
            # Direct user permission
            and_(
                WorkflowPermission.grantee_type == GranteeType.USER.value,
                WorkflowPermission.grantee_id == str(user_id)
            ),
            # Role permission
            and_(
                WorkflowPermission.grantee_type == GranteeType.ROLE.value,
                WorkflowPermission.grantee_id == user_role
            ),
            # Organization permission
            WorkflowPermission.grantee_type == GranteeType.ORGANIZATION.value
        ),
        # Not expired
        or_(
            WorkflowPermission.expires_at.is_(None),
            WorkflowPermission.expires_at > now
        )
    )

    result = await db.execute(query)
    permissions = result.scalars().all()

    if not permissions:
        return None

    # Find highest permission and its source
    hierarchy = {"view": 0, "execute": 1, "edit": 2, "admin": 3}
    highest = None
    highest_level = -1
    source = None

    # Priority: direct > role > organization
    source_priority = {"user": 3, "role": 2, "organization": 1}

    for perm in permissions:
        level = hierarchy.get(perm.permission, -1)
        if level > highest_level:
            highest_level = level
            highest = perm.permission
            if perm.grantee_type == GranteeType.USER.value:
                source = "direct"
            elif perm.grantee_type == GranteeType.ROLE.value:
                source = "role"
            else:
                source = "organization"

    return (highest, source) if highest else None


@router.get("", response_model=PaginatedWorkflowResponse)
async def list_workflows(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    include_shared: bool = Query(default=True, description="Include workflows shared with you"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """
    List all saved workflows for the current tenant with pagination.

    By default includes both owned workflows and workflows shared with you.
    Set include_shared=false to see only owned workflows.
    """
    user_id = uuid.UUID(user.id)
    now = datetime.now(timezone.utc)

    # Build the base condition for owned workflows
    owned_condition = and_(
        Workflow.user_id == user_id,
        Workflow.tenant_id == tenant_id
    )

    if include_shared:
        # Get IDs of workflows shared with this user
        shared_query = select(WorkflowPermission.workflow_id).where(
            WorkflowPermission.tenant_id == tenant_id,
            or_(
                # Direct user permission
                and_(
                    WorkflowPermission.grantee_type == GranteeType.USER.value,
                    WorkflowPermission.grantee_id == user.id
                ),
                # Role permission
                and_(
                    WorkflowPermission.grantee_type == GranteeType.ROLE.value,
                    WorkflowPermission.grantee_id == user.role
                ),
                # Organization permission
                WorkflowPermission.grantee_type == GranteeType.ORGANIZATION.value
            ),
            # Not expired
            or_(
                WorkflowPermission.expires_at.is_(None),
                WorkflowPermission.expires_at > now
            )
        ).distinct()

        shared_result = await db.execute(shared_query)
        shared_workflow_ids = [row[0] for row in shared_result.fetchall()]

        # Include owned OR shared workflows
        if shared_workflow_ids:
            combined_condition = or_(
                owned_condition,
                and_(
                    Workflow.id.in_(shared_workflow_ids),
                    Workflow.tenant_id == tenant_id,
                    Workflow.user_id != user_id  # Exclude owned from shared list
                )
            )
        else:
            combined_condition = owned_condition
    else:
        combined_condition = owned_condition

    # Get total count
    count_query = select(func.count()).select_from(Workflow).where(combined_condition)
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Get paginated items — defer spec column (50-200KB per row) since
    # the list view only needs metadata. Detail/edit endpoints load full spec.
    query = (
        select(Workflow)
        .options(defer(Workflow.spec))
        .where(combined_condition)
        .order_by(Workflow.updated_at.desc())
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(query)
    workflows = result.scalars().all()

    # Batch-load sharing info to avoid N+1 queries
    workflow_ids = [wf.id for wf in workflows]

    # Batch query 1: share counts per workflow (for owned workflows)
    share_counts: Dict[uuid.UUID, int] = {}
    if workflow_ids:
        share_count_query = (
            select(
                WorkflowPermission.workflow_id,
                func.count().label("cnt"),
            )
            .where(WorkflowPermission.workflow_id.in_(workflow_ids))
            .group_by(WorkflowPermission.workflow_id)
        )
        share_result = await db.execute(share_count_query)
        share_counts = {row.workflow_id: row.cnt for row in share_result.all()}

    # Batch query 2: user's permission per workflow (for shared workflows)
    user_perms: Dict[uuid.UUID, tuple] = {}
    shared_wf_ids = [wf.id for wf in workflows if wf.user_id != user_id]
    if shared_wf_ids:
        now = datetime.now(timezone.utc)
        perm_query = select(WorkflowPermission).where(
            WorkflowPermission.workflow_id.in_(shared_wf_ids),
            WorkflowPermission.tenant_id == tenant_id,
            or_(
                and_(
                    WorkflowPermission.grantee_type == GranteeType.USER.value,
                    WorkflowPermission.grantee_id == user.id,
                ),
                and_(
                    WorkflowPermission.grantee_type == GranteeType.ROLE.value,
                    WorkflowPermission.grantee_id == user.role,
                ),
                WorkflowPermission.grantee_type == GranteeType.ORGANIZATION.value,
            ),
            or_(
                WorkflowPermission.expires_at.is_(None),
                WorkflowPermission.expires_at > now,
            ),
        )
        perm_result = await db.execute(perm_query)
        all_perms = perm_result.scalars().all()

        # Find highest permission per workflow
        hierarchy = {"view": 0, "execute": 1, "edit": 2, "admin": 3}
        for perm in all_perms:
            wf_id = perm.workflow_id
            level = hierarchy.get(perm.permission, -1)
            existing = user_perms.get(wf_id)
            if existing is None or level > hierarchy.get(existing[0], -1):
                if perm.grantee_type == GranteeType.USER.value:
                    source = "direct"
                elif perm.grantee_type == GranteeType.ROLE.value:
                    source = "role"
                else:
                    source = "organization"
                user_perms[wf_id] = (perm.permission, source)

    # Enrich with sharing info (no extra queries)
    enriched_items = []
    for wf in workflows:
        is_owner = wf.user_id == user_id

        item = WorkflowListItem(
            id=wf.id,
            name=wf.name,
            description=wf.description,
            created_at=wf.created_at,
            updated_at=wf.updated_at,
            is_owner=is_owner,
            owner_id=wf.user_id,
        )

        if is_owner:
            item.is_shared = share_counts.get(wf.id, 0) > 0
        else:
            perm_info = user_perms.get(wf.id)
            if perm_info:
                item.permission, item.share_source = perm_info
            item.is_shared = True

        enriched_items.append(item)

    return PaginatedWorkflowResponse(
        items=enriched_items,
        total=total,
        skip=skip,
        limit=limit,
        has_more=(skip + limit) < total
    )


@router.get("/{workflow_id}", response_model=WorkflowResponse)
async def get_workflow(
    workflow_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """
    Get a workflow by ID.

    Returns workflow if user is owner OR has shared access.
    """
    user_id = uuid.UUID(user.id)

    # First try to find the workflow with tenant isolation
    query = select(Workflow).where(
        Workflow.id == workflow_id,
        Workflow.tenant_id == tenant_id
    )
    result = await db.execute(query)
    workflow = result.scalars().first()

    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    is_owner = workflow.user_id == user_id

    # If not owner, check for shared access
    if not is_owner:
        perm_info = await _get_user_effective_permission(
            workflow_id, user_id, user.role, tenant_id, db
        )
        if not perm_info:
            raise HTTPException(status_code=403, detail="You don't have access to this workflow")

        return WorkflowResponse(
            id=workflow.id,
            name=workflow.name,
            description=workflow.description,
            spec=workflow.spec,
            created_at=workflow.created_at,
            updated_at=workflow.updated_at,
            is_owner=False,
            is_shared=True,
            owner_id=workflow.user_id,
            permission=perm_info[0],
            share_source=perm_info[1]
        )

    # Owner - check if shared with others
    share_count_query = select(func.count()).select_from(WorkflowPermission).where(
        WorkflowPermission.workflow_id == workflow.id
    )
    share_count_result = await db.execute(share_count_query)
    share_count = share_count_result.scalar() or 0

    return WorkflowResponse(
        id=workflow.id,
        name=workflow.name,
        description=workflow.description,
        spec=workflow.spec,
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
        is_owner=True,
        is_shared=share_count > 0,
        owner_id=workflow.user_id
    )


@router.post("", response_model=WorkflowResponse)
async def create_workflow(
    workflow_in: WorkflowCreate,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """
    Save a new workflow for the current tenant.
    tenant_id is explicitly set; RLS enforces INSERT restriction.

    Validates workflow for cycles and complexity limits before saving.
    """
    # Enterprise: Validate workflow before saving
    try:
        spec_dict = workflow_in.spec.model_dump()
        WorkflowValidator.validate_workflow(spec_dict)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    now = datetime.now(timezone.utc)
    workflow = Workflow(
        user_id=uuid.UUID(user.id),
        tenant_id=tenant_id,
        name=workflow_in.name,
        description=workflow_in.description,
        spec=workflow_in.spec.model_dump(),
        created_at=now,
        updated_at=now
    )

    db.add(workflow)
    await db.commit()
    # Skip refresh - with expire_on_commit=False and RLS, refresh can fail
    # after commit loses the SET LOCAL tenant context
    return workflow


@router.put("/{workflow_id}", response_model=WorkflowResponse)
async def update_workflow(
    workflow_id: uuid.UUID,
    workflow_in: WorkflowUpdate,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """
    Update an existing workflow.

    Requires owner OR edit/admin permission on the workflow.
    """
    user_id = uuid.UUID(user.id)

    # Find workflow with tenant isolation only
    query = select(Workflow).where(
        Workflow.id == workflow_id,
        Workflow.tenant_id == tenant_id
    )
    result = await db.execute(query)
    workflow = result.scalars().first()

    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    is_owner = workflow.user_id == user_id

    # Check permissions if not owner
    if not is_owner:
        perm_info = await _get_user_effective_permission(
            workflow_id, user_id, user.role, tenant_id, db
        )
        if not perm_info:
            raise HTTPException(status_code=403, detail="You don't have access to this workflow")

        # Need edit or admin permission to update
        if not PermissionLevel.includes(PermissionLevel(perm_info[0]), PermissionLevel.EDIT):
            raise HTTPException(status_code=403, detail="You need edit permission to modify this workflow")

    if workflow_in.name is not None:
        workflow.name = workflow_in.name
    if workflow_in.description is not None:
        workflow.description = workflow_in.description
    if workflow_in.spec is not None:
        new_spec = workflow_in.spec.model_dump()

        # Enterprise: Validate workflow before updating
        try:
            WorkflowValidator.validate_workflow(new_spec)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

        logger.info(f"[UPDATE WORKFLOW DEBUG] workflow_id={workflow_id}")
        logger.info(f"[UPDATE WORKFLOW DEBUG] old spec: nodes={len(workflow.spec.get('nodes', []))}, edges={len(workflow.spec.get('edges', []))}")
        logger.info(f"[UPDATE WORKFLOW DEBUG] new spec: nodes={len(new_spec.get('nodes', []))}, edges={len(new_spec.get('edges', []))}")
        workflow.spec = new_spec

    # Explicitly set updated_at to ensure it's always updated (timezone-aware)
    workflow.updated_at = datetime.now(timezone.utc)

    await db.commit()
    # Skip refresh - with expire_on_commit=False and RLS, refresh can fail
    logger.info(f"[UPDATE WORKFLOW DEBUG] Committed workflow update for {workflow_id}")
    return workflow


@router.delete("/{workflow_id}", response_model=Dict[str, str])
async def delete_workflow(
    workflow_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """
    Delete a workflow.

    Only the owner OR users with admin permission can delete a workflow.
    """
    user_id = uuid.UUID(user.id)

    # Find workflow with tenant isolation only
    query = select(Workflow).where(
        Workflow.id == workflow_id,
        Workflow.tenant_id == tenant_id
    )
    result = await db.execute(query)
    workflow = result.scalars().first()

    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    is_owner = workflow.user_id == user_id

    # Only owners or admin permission holders can delete
    if not is_owner:
        perm_info = await _get_user_effective_permission(
            workflow_id, user_id, user.role, tenant_id, db
        )
        if not perm_info:
            raise HTTPException(status_code=403, detail="You don't have access to this workflow")

        # Need admin permission to delete (not just edit)
        if not PermissionLevel.includes(PermissionLevel(perm_info[0]), PermissionLevel.ADMIN):
            raise HTTPException(status_code=403, detail="Only the owner or admins can delete this workflow")

    # Delete dependent records first (no CASCADE on FK constraints)
    from sqlalchemy import delete as sql_delete
    dependent_tables = [
        'workflow_versions', 'workflow_permissions', 'workflow_executions',
        'workflow_sessions', 'workflow_entities', 'workflow_batches',
        'scheduled_workflows', 'webhook_triggers', 'alert_rules',
        'alert_history', 'background_jobs', 'chat_sessions',
    ]
    for table_name in dependent_tables:
        try:
            await db.execute(
                text(f"DELETE FROM {table_name} WHERE workflow_id = :wid"),
                {"wid": str(workflow_id)}
            )
        except Exception:
            pass  # Table may not exist or column name differs

    await db.delete(workflow)
    await db.commit()

    return {"id": str(workflow_id), "message": "Workflow deleted"}


# ============================================
# Execute Saved Workflow Endpoint
# ============================================

class RunWorkflowRequest(BaseModel):
    """Request to run a saved workflow by ID."""
    inputs: Optional[Dict[str, str]] = None
    version: Optional[int] = None  # Specific version number to run (defaults to active/latest)
    version_id: Optional[uuid.UUID] = None  # Explicit version UUID (takes precedence over version number)
    response_mode: Optional[str] = "blocking"  # "blocking" or "streaming"
    user: Optional[str] = None  # User identifier for logging


@router.post("/{workflow_id}/run", response_model=WorkflowResult)
async def run_saved_workflow(
    workflow_id: uuid.UUID,
    request: RunWorkflowRequest,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    user_id: uuid.UUID = Depends(get_current_user_id),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    _quota=Depends(require_quota(QuotaType.EXECUTIONS)),
):
    """
    Execute a saved workflow by ID.

    This endpoint is designed for API consumers who want to run a published workflow
    without needing to provide the full workflow specification.

    Parameters:
    - workflow_id: UUID of the saved workflow
    - inputs: Key-value pairs for workflow input variables
    - version: Optional version number (defaults to active published version)
    - response_mode: "blocking" (default) or "streaming"
    - user: Optional user identifier for logging

    Returns the same WorkflowResult as /execute endpoint.
    """
    # Authorization: check execute permission
    await _require_execute_permission(
        workflow_id, user_id, user.role, tenant_id, db
    )

    from app.models.workflow import Workflow, WorkflowVersion

    # Fetch the workflow
    query = select(Workflow).where(
        Workflow.id == workflow_id,
        Workflow.tenant_id == tenant_id
    )
    result = await db.execute(query)
    workflow = result.scalars().first()

    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    # Determine which spec to use and track version for audit
    workflow_spec = None
    resolved_version_id: Optional[uuid.UUID] = None

    # Get active version for comparison
    active_query = select(WorkflowVersion).where(
        WorkflowVersion.workflow_id == workflow_id,
        WorkflowVersion.is_active == True,
        WorkflowVersion.tenant_id == tenant_id
    )
    active_result = await db.execute(active_query)
    active_version = active_result.scalars().first()

    if request.version_id is not None:
        # Explicit version_id takes precedence
        version_query = select(WorkflowVersion).where(
            WorkflowVersion.id == request.version_id,
            WorkflowVersion.workflow_id == workflow_id,
            WorkflowVersion.tenant_id == tenant_id
        )
        version_result = await db.execute(version_query)
        version = version_result.scalars().first()

        if not version:
            raise HTTPException(
                status_code=404,
                detail="Version not found"
            )

        workflow_spec = version.spec
        resolved_version_id = version.id

    elif request.version is not None:
        # Fetch specific version by number
        version_query = select(WorkflowVersion).where(
            WorkflowVersion.workflow_id == workflow_id,
            WorkflowVersion.version_number == request.version,
            WorkflowVersion.tenant_id == tenant_id
        )
        version_result = await db.execute(version_query)
        version = version_result.scalars().first()

        if not version:
            raise HTTPException(
                status_code=404,
                detail=f"Version {request.version} not found for this workflow"
            )

        workflow_spec = version.spec
        resolved_version_id = version.id

    else:
        # Default to active version, or fall back to draft
        if active_version:
            workflow_spec = active_version.spec
            resolved_version_id = active_version.id
        else:
            # Fall back to current workflow spec (draft)
            workflow_spec = workflow.spec
            # resolved_version_id stays None for draft executions

    if not workflow_spec or not workflow_spec.get("nodes"):
        raise HTTPException(
            status_code=400,
            detail="Workflow has no nodes to execute"
        )

    # Convert spec to node/edge objects
    nodes = [WorkflowNode(**n) for n in workflow_spec.get("nodes", [])]
    edges = [WorkflowEdge(**e) for e in workflow_spec.get("edges", [])]

    if await engine_runs.tenant_uses_engine_workflows(db, tenant_id):
        # Engine tenants: an engine run of exactly the resolved spec (plan 003 U15).
        input_variables = dict(request.inputs or {})
        if "sys.query" not in input_variables and "query" in input_variables:
            input_variables["sys.query"] = input_variables["query"]
        return await _engine_run_response(
            db,
            workflow=workflow,
            workflow_spec={"nodes": [n.model_dump() for n in nodes], "edges": [e.model_dump() for e in edges]},
            inputs=input_variables,
            nodes_dict={n.id: n for n in nodes},
            user_id=user_id, tenant_id=tenant_id, trigger="api", request_key=idempotency_key,
            version_id=resolved_version_id, label=f"Workflow '{workflow.name}'",
        )

    # Execute the workflow
    try:
        # Convert inputs to workflow variables
        input_variables = request.inputs or {}

        # Add sys.query if provided
        if "sys.query" in input_variables:
            pass  # Already there
        elif "query" in input_variables:
            input_variables["sys.query"] = input_variables["query"]

        # Build node lookup for extracting Output node data
        nodes_dict = {n.id: n for n in nodes}

        workflow_spec_dict = {
            "nodes": [n.model_dump() for n in nodes],
            "edges": [e.model_dump() for e in edges]
        }

        # Use durable engine if feature flag is enabled (C4 fix: make durability default)
        if settings.USE_DURABLE_WORKFLOWS:
            try:
                engine = DurableWorkflowEngine(
                    db=db,
                    user_id=str(user_id),
                    tenant_id=tenant_id
                )
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
            result = await engine.execute_workflow(
                workflow_spec=workflow_spec_dict,
                input_variables=input_variables,
                workflow_id=workflow_id,
                workflow_version_id=resolved_version_id
            )
        else:
            # Fallback to standard orchestrator if flag disabled
            orchestrator = WorkflowOrchestrator(
                db=db,
                user_id=str(user_id),
                tenant_id=tenant_id,
                trigger="api"  # Mark as API-triggered execution
            )
            result = await orchestrator.execute_workflow(
                workflow_spec=workflow_spec_dict,
                input_variables=input_variables,
                workflow_id=workflow_id,
                workflow_version_id=resolved_version_id
            )

        # Extract outputs from Output nodes
        outputs = _extract_workflow_outputs(
            results=result.get("results", {}),
            nodes_dict=nodes_dict
        )

        return WorkflowResult(
            status=result["status"],
            message=f"Workflow '{workflow.name}' executed: {result['executed_nodes']}/{result['total_nodes']} nodes",
            results=result["results"],
            executed_nodes=result["executed_nodes"],
            total_nodes=result["total_nodes"],
            outputs=outputs
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# Durable Workflow Endpoints
# ============================================

class CheckpointResponse(BaseModel):
    """Response for a single checkpoint."""
    id: str
    step_number: int
    step_name: str
    node_id: str
    status: str
    has_output: bool
    error: Optional[str] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    elapsed_ms: Optional[int] = None


class CheckpointListResponse(BaseModel):
    """Response for list of checkpoints."""
    workflow_uuid: str
    checkpoints: List[CheckpointResponse]
    total: int
    can_recover: bool


class RecoverWorkflowRequest(BaseModel):
    """Request to recover a crashed workflow."""
    workflow_spec: WorkflowSpec
    inputs: Optional[Dict[str, str]] = None


class ReplayResponse(BaseModel):
    """Response for workflow replay (audit)."""
    workflow_uuid: str
    steps: List[Dict[str, Any]]
    total_steps: int


class DurableWorkflowResult(WorkflowResult):
    """Extended result for durable workflows."""
    dbos_workflow_uuid: Optional[str] = None
    durable: bool = False
    checkpoints: int = 0


@router.post("/execute-durable", response_model=DurableWorkflowResult)
async def execute_durable_workflow(
    request: ExecuteWorkflowRequest,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    user_id: uuid.UUID = Depends(get_current_user_id),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    _quota=Depends(require_quota(QuotaType.EXECUTIONS)),
):
    """
    Execute a workflow with DBOS checkpointing for crash recovery.

    When USE_DURABLE_WORKFLOWS is enabled:
    - Each node execution is checkpointed
    - On crash, workflow can resume from last checkpoint
    - Full audit trail available via replay endpoint

    Returns additional fields:
    - dbos_workflow_uuid: UUID for recovery/replay
    - durable: True if checkpointing was active
    - checkpoints: Number of checkpoints created
    """
    # Authorization: check execute permission if workflow_id provided
    if request.workflow_id:
        await _require_execute_permission(
            request.workflow_id, user_id, user.role, tenant_id, db
        )

    try:
        try:
            engine = DurableWorkflowEngine(
                db=db,
                user_id=str(user_id),
                tenant_id=tenant_id
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        input_variables = request.inputs or {}
        if "query" in input_variables and "sys.query" not in input_variables:
            input_variables["sys.query"] = input_variables["query"]

        result = await engine.execute_workflow(
            workflow_spec={
                "nodes": [n.model_dump() for n in request.nodes],
                "edges": [e.model_dump() for e in request.edges]
            },
            input_variables=input_variables
        )

        return DurableWorkflowResult(
            status=result["status"],
            message=f"Workflow executed: {result['executed_nodes']}/{result['total_nodes']} nodes",
            results=result["results"],
            executed_nodes=result["executed_nodes"],
            total_nodes=result["total_nodes"],
            dbos_workflow_uuid=result.get("dbos_workflow_uuid"),
            durable=result.get("durable", False),
            checkpoints=result.get("checkpoints", 0)
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{workflow_uuid}/checkpoints", response_model=CheckpointListResponse)
async def get_workflow_checkpoints(
    workflow_uuid: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user_id),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """
    List all checkpoints for a durable workflow.

    Used to:
    - Monitor workflow progress
    - Identify recovery point after crash
    - Audit workflow execution history
    """
    if not settings.USE_DURABLE_WORKFLOWS:
        raise HTTPException(
            status_code=400,
            detail="Durable workflows not enabled. Set USE_DURABLE_WORKFLOWS=true"
        )

    try:
        engine = DurableWorkflowEngine(
            db=db,
            user_id=str(user_id),
            tenant_id=tenant_id
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    checkpoints = await engine.get_checkpoints(workflow_uuid)

    # Determine if recovery is possible
    can_recover = any(
        cp["status"] == "completed" for cp in checkpoints
    ) and any(
        cp["status"] in ["running", "failed"] for cp in checkpoints
    )

    return CheckpointListResponse(
        workflow_uuid=str(workflow_uuid),
        checkpoints=[CheckpointResponse(**cp) for cp in checkpoints],
        total=len(checkpoints),
        can_recover=can_recover
    )


@router.post("/{workflow_uuid}/recover", response_model=DurableWorkflowResult)
async def recover_workflow(
    workflow_uuid: uuid.UUID,
    request: RecoverWorkflowRequest,
    db: AsyncSession = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user_id),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """
    Recover a crashed workflow from its last checkpoint.

    Requires:
    - Original workflow specification
    - Original input variables

    The workflow will resume from the step after the last successful checkpoint.
    """
    if not settings.USE_DURABLE_WORKFLOWS:
        raise HTTPException(
            status_code=400,
            detail="Durable workflows not enabled. Set USE_DURABLE_WORKFLOWS=true"
        )

    try:
        try:
            engine = DurableWorkflowEngine(
                db=db,
                user_id=str(user_id),
                tenant_id=tenant_id
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        input_variables = request.inputs or {}

        result = await engine.recover_workflow(
            dbos_workflow_uuid=workflow_uuid,
            workflow_spec=request.workflow_spec.model_dump(),
            input_variables=input_variables
        )

        return DurableWorkflowResult(
            status=result["status"],
            message=f"Workflow recovered: {result['executed_nodes']}/{result['total_nodes']} nodes",
            results=result["results"],
            executed_nodes=result["executed_nodes"],
            total_nodes=result["total_nodes"],
            dbos_workflow_uuid=result.get("dbos_workflow_uuid"),
            durable=result.get("durable", False),
            checkpoints=result.get("checkpoints", 0)
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{workflow_uuid}/replay", response_model=ReplayResponse)
async def replay_workflow(
    workflow_uuid: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user_id),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """
    Replay workflow execution for regulatory audit.

    Returns complete history of all checkpoints with inputs/outputs.
    This is a read-only operation for compliance review.

    Required for:
    - Regulatory audits
    - Debugging failed workflows
    - Compliance reporting
    """
    if not settings.USE_DURABLE_WORKFLOWS:
        raise HTTPException(
            status_code=400,
            detail="Durable workflows not enabled. Set USE_DURABLE_WORKFLOWS=true"
        )

    try:
        engine = DurableWorkflowEngine(
            db=db,
            user_id=str(user_id),
            tenant_id=tenant_id
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    steps = await engine.replay_workflow(workflow_uuid)

    return ReplayResponse(
        workflow_uuid=str(workflow_uuid),
        steps=steps,
        total_steps=len(steps)
    )


# ============================================
# Workflow Logs & Monitoring Endpoints
# ============================================

class WorkflowLogSummary(BaseModel):
    """Summary of a workflow execution for logs list."""
    id: str
    workflow_id: Optional[str]
    status: str
    trigger: str
    started_at: Optional[str]
    completed_at: Optional[str]
    duration_ms: Optional[int]
    total_tokens: int
    nodes_executed: int
    total_nodes: Optional[int] = None
    error: Optional[str]
    progress: int
    # Engine conversation that runs this execution (plan 003 U26): the UI follows its watch while the run is live.
    engine_conversation_id: Optional[str] = None


class WorkflowLogDetail(WorkflowLogSummary):
    """Detailed workflow execution log."""
    input_data: Optional[Dict[str, Any]]
    result: Optional[Dict[str, Any]]
    execution_trace: List[Dict[str, Any]]


class WorkflowLogsResponse(BaseModel):
    """Paginated workflow logs response."""
    logs: List[WorkflowLogSummary]
    next_cursor: Optional[str]
    limit: int
    has_more: bool


class MonitoringMetrics(BaseModel):
    """Monitoring metrics for workflows."""
    time_range: str
    total_executions: int
    successful: int
    failed: int
    running: int
    success_rate: float
    avg_duration_ms: Optional[int]


class TopWorkflow(BaseModel):
    """Top workflow by execution count."""
    workflow_id: str
    workflow_name: Optional[str]
    execution_count: int
    failure_count: int
    success_rate: float


class TopWorkflowsResponse(BaseModel):
    """Response for top workflows."""
    workflows: List[TopWorkflow]


class RecentFailure(BaseModel):
    """Recent workflow failure."""
    id: str
    workflow_id: str
    workflow_name: Optional[str]
    error: str
    failed_at: str
    trigger: str


class RecentFailuresResponse(BaseModel):
    """Response for recent failures."""
    failures: List[RecentFailure]


def _parse_time_range(time_range: str) -> datetime:
    """Parse time range string to datetime."""
    from datetime import timedelta
    now = datetime.utcnow()

    ranges = {
        '1h': timedelta(hours=1),
        '24h': timedelta(hours=24),
        '7d': timedelta(days=7),
        '30d': timedelta(days=30),
    }

    delta = ranges.get(time_range, timedelta(hours=24))
    return now - delta


@router.get("/{workflow_id}/logs", response_model=WorkflowLogsResponse)
async def get_workflow_logs(
    workflow_id: uuid.UUID,
    status: Optional[str] = Query(None, description="Filter by status: running, success, failed"),
    trigger: Optional[str] = Query(None, description="Filter by trigger: manual, api, scheduled"),
    time_range: Optional[str] = Query("24h", description="Time range: 1h, 24h, 7d, 30d"),
    cursor: Optional[str] = Query(None, description="Cursor for pagination"),
    limit: int = Query(20, ge=1, le=100, description="Number of results"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """
    Get paginated workflow execution logs.

    Supports filtering by status, trigger type, and time range.
    Uses cursor-based pagination for efficient fetching.
    """
    # Build query
    query = (
        select(WorkflowExecution)
        .where(WorkflowExecution.workflow_id == workflow_id)
        .where(WorkflowExecution.tenant_id == tenant_id)
    )

    # Apply filters
    if status:
        # Map 'success' to 'completed' for API compatibility
        db_status = 'completed' if status == 'success' else status
        query = query.where(WorkflowExecution.status == db_status)

    if trigger:
        query = query.where(WorkflowExecution.trigger == trigger)

    if time_range:
        start_time = _parse_time_range(time_range)
        query = query.where(WorkflowExecution.started_at >= start_time)

    # Handle cursor pagination
    if cursor:
        try:
            cursor_time = datetime.fromisoformat(cursor)
            query = query.where(WorkflowExecution.started_at < cursor_time)
        except ValueError:
            pass

    # Order and limit
    query = query.order_by(WorkflowExecution.started_at.desc()).limit(limit + 1)

    result = await db.execute(query)
    executions = list(result.scalars().all())

    # Check if there are more results
    has_more = len(executions) > limit
    if has_more:
        executions = executions[:limit]

    # Build response
    logs = [WorkflowLogSummary(**ex.to_log_summary()) for ex in executions]

    # Get next cursor
    next_cursor = None
    if has_more and executions:
        next_cursor = executions[-1].started_at.isoformat()

    return WorkflowLogsResponse(
        logs=logs,
        next_cursor=next_cursor,
        limit=limit,
        has_more=has_more
    )


# NOTE: /stream must be defined BEFORE /{run_id} to prevent "stream" from matching as a UUID
@router.get("/{workflow_id}/logs/stream")
async def stream_workflow_logs(
    workflow_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
):
    """
    SSE endpoint for real-time log updates.

    Streams new log entries and status updates for the workflow.
    Uses the middleware to authenticate via query param token for SSE.
    """
    import asyncio
    from datetime import timedelta
    from sqlalchemy import desc

    async def event_generator():
        heartbeat_interval = 15  # seconds
        poll_interval = 2  # seconds
        max_idle_time = 30 * 60  # 30 minutes
        last_activity = datetime.utcnow()
        last_seen_ids: set = set()

        try:
            # Initial fetch to populate known IDs
            initial_query = (
                select(WorkflowExecution.id)
                .where(WorkflowExecution.workflow_id == workflow_id)
                .where(WorkflowExecution.tenant_id == tenant_id)
                .where(WorkflowExecution.started_at >= datetime.utcnow() - timedelta(hours=1))
                .order_by(desc(WorkflowExecution.started_at))
                .limit(50)
            )

            result = await db.execute(initial_query)
            last_seen_ids = {row[0] for row in result.all()}

            heartbeat_counter = 0

            while True:
                # Check for timeout
                if (datetime.utcnow() - last_activity).total_seconds() > max_idle_time:
                    yield f"event: timeout\ndata: {{\"message\": \"Stream closed due to inactivity\"}}\n\n"
                    break

                # Poll for new/updated executions
                poll_query = (
                    select(WorkflowExecution)
                    .where(WorkflowExecution.workflow_id == workflow_id)
                    .where(WorkflowExecution.tenant_id == tenant_id)
                    .where(WorkflowExecution.started_at >= datetime.utcnow() - timedelta(hours=1))
                    .order_by(desc(WorkflowExecution.started_at))
                    .limit(20)
                )

                result = await db.execute(poll_query)
                executions = result.scalars().all()

                for ex in executions:
                    # Check if this is a new execution or has updates (running status)
                    is_new = ex.id not in last_seen_ids
                    is_running = ex.status == "running"

                    if is_new or is_running:
                        last_seen_ids.add(ex.id)
                        last_activity = datetime.utcnow()

                        log_data = {
                            "id": str(ex.id),
                            "workflow_id": str(ex.workflow_id),
                            "status": ex.status,
                            "trigger": ex.trigger,
                            "progress": getattr(ex, 'progress', 0),
                            "started_at": ex.started_at.isoformat() if ex.started_at else None,
                            "completed_at": ex.completed_at.isoformat() if ex.completed_at else None,
                            "duration_ms": ex.duration_ms,
                            "total_tokens": ex.total_tokens,
                            "nodes_executed": ex.nodes_executed,
                            "error": ex.error,
                        }
                        yield f"data: {json.dumps(log_data)}\n\n"

                # Heartbeat every N poll cycles
                heartbeat_counter += 1
                if heartbeat_counter >= (heartbeat_interval // poll_interval):
                    heartbeat_counter = 0
                    yield f"event: heartbeat\ndata: {{\"timestamp\": \"{datetime.utcnow().isoformat()}\"}}\n\n"

                await asyncio.sleep(poll_interval)

        except asyncio.CancelledError:
            # Client disconnected
            pass
        except Exception as e:
            yield f"event: error\ndata: {{\"error\": \"{str(e)}\", \"retry\": 3000}}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        }
    )


@router.get("/{workflow_id}/logs/{run_id}", response_model=WorkflowLogDetail)
async def get_workflow_log_detail(
    workflow_id: uuid.UUID,
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """
    Get detailed execution log for a specific workflow run.

    Includes full execution trace with per-node details.
    """
    query = (
        select(WorkflowExecution)
        .where(WorkflowExecution.id == run_id)
        .where(WorkflowExecution.workflow_id == workflow_id)
        .where(WorkflowExecution.tenant_id == tenant_id)
    )

    result = await db.execute(query)
    execution = result.scalars().first()

    if not execution:
        raise HTTPException(status_code=404, detail="Execution log not found")

    return WorkflowLogDetail(**execution.to_log_detail())


@router.get("/monitoring/metrics", response_model=MonitoringMetrics)
async def get_monitoring_metrics(
    time_range: str = Query("24h", description="Time range: 1h, 24h, 7d, 30d"),
    workflow_id: Optional[uuid.UUID] = Query(None, description="Filter by specific workflow"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """
    Get monitoring metrics for workflow executions.

    Returns aggregated stats for the specified time range.
    """
    start_time = _parse_time_range(time_range)

    # Build base query
    base_filter = [
        WorkflowExecution.tenant_id == tenant_id,
        WorkflowExecution.started_at >= start_time
    ]
    if workflow_id:
        base_filter.append(WorkflowExecution.workflow_id == workflow_id)

    # Total executions
    total_query = select(func.count()).select_from(WorkflowExecution).where(*base_filter)
    total_result = await db.execute(total_query)
    total_executions = total_result.scalar() or 0

    # Successful executions
    success_query = select(func.count()).select_from(WorkflowExecution).where(
        *base_filter,
        WorkflowExecution.status == 'completed'
    )
    success_result = await db.execute(success_query)
    successful = success_result.scalar() or 0

    # Failed executions
    failed_query = select(func.count()).select_from(WorkflowExecution).where(
        *base_filter,
        WorkflowExecution.status == 'failed'
    )
    failed_result = await db.execute(failed_query)
    failed = failed_result.scalar() or 0

    # Running executions
    running_query = select(func.count()).select_from(WorkflowExecution).where(
        *base_filter,
        WorkflowExecution.status == 'running'
    )
    running_result = await db.execute(running_query)
    running = running_result.scalar() or 0

    # Average duration
    avg_query = select(func.avg(WorkflowExecution.duration_ms)).where(
        *base_filter,
        WorkflowExecution.duration_ms.isnot(None)
    )
    avg_result = await db.execute(avg_query)
    avg_duration = avg_result.scalar()

    # Calculate success rate
    success_rate = (successful / total_executions * 100) if total_executions > 0 else 0.0

    return MonitoringMetrics(
        time_range=time_range,
        total_executions=total_executions,
        successful=successful,
        failed=failed,
        running=running,
        success_rate=round(success_rate, 2),
        avg_duration_ms=int(avg_duration) if avg_duration else None
    )


@router.get("/monitoring/top-workflows", response_model=TopWorkflowsResponse)
async def get_top_workflows(
    time_range: str = Query("24h", description="Time range: 1h, 24h, 7d, 30d"),
    limit: int = Query(10, ge=1, le=50, description="Number of results"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """
    Get top workflows by execution count.
    """
    start_time = _parse_time_range(time_range)

    # Query for top workflows with execution counts
    query = (
        select(
            WorkflowExecution.workflow_id,
            func.count().label('execution_count'),
            func.sum(func.cast(WorkflowExecution.status == 'failed', sa.Integer)).label('failure_count')
        )
        .where(
            WorkflowExecution.tenant_id == tenant_id,
            WorkflowExecution.started_at >= start_time,
            WorkflowExecution.workflow_id.isnot(None)
        )
        .group_by(WorkflowExecution.workflow_id)
        .order_by(func.count().desc())
        .limit(limit)
    )

    result = await db.execute(query)
    rows = result.all()

    # Get workflow names
    workflow_ids = [row.workflow_id for row in rows]
    if workflow_ids:
        workflows_query = select(Workflow.id, Workflow.name).where(Workflow.id.in_(workflow_ids))
        workflows_result = await db.execute(workflows_query)
        workflow_names = {str(w.id): w.name for w in workflows_result.all()}
    else:
        workflow_names = {}

    # Build response
    workflows = []
    for row in rows:
        exec_count = row.execution_count
        fail_count = row.failure_count or 0
        success_rate = ((exec_count - fail_count) / exec_count * 100) if exec_count > 0 else 0.0

        workflows.append(TopWorkflow(
            workflow_id=str(row.workflow_id),
            workflow_name=workflow_names.get(str(row.workflow_id)),
            execution_count=exec_count,
            failure_count=fail_count,
            success_rate=round(success_rate, 2)
        ))

    return TopWorkflowsResponse(workflows=workflows)


@router.get("/monitoring/recent-failures", response_model=RecentFailuresResponse)
async def get_recent_failures(
    limit: int = Query(10, ge=1, le=50, description="Number of results"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """
    Get recent workflow failures.
    """
    query = (
        select(WorkflowExecution)
        .where(
            WorkflowExecution.tenant_id == tenant_id,
            WorkflowExecution.status == 'failed'
        )
        .order_by(WorkflowExecution.completed_at.desc())
        .limit(limit)
    )

    result = await db.execute(query)
    executions = result.scalars().all()

    # Get workflow names
    workflow_ids = [ex.workflow_id for ex in executions if ex.workflow_id]
    if workflow_ids:
        workflows_query = select(Workflow.id, Workflow.name).where(Workflow.id.in_(workflow_ids))
        workflows_result = await db.execute(workflows_query)
        workflow_names = {str(w.id): w.name for w in workflows_result.all()}
    else:
        workflow_names = {}

    failures = [
        RecentFailure(
            id=str(ex.id),
            workflow_id=str(ex.workflow_id) if ex.workflow_id else "",
            workflow_name=workflow_names.get(str(ex.workflow_id)) if ex.workflow_id else None,
            error=ex.error or "Unknown error",
            failed_at=(ex.completed_at or ex.started_at).isoformat(),
            trigger=ex.trigger or "manual"
        )
        for ex in executions
    ]

    return RecentFailuresResponse(failures=failures)


# --- Batch Execution Endpoints (Enterprise) ---

class BatchExecutionInput(BaseModel):
    """Single execution input in a batch."""
    inputs: Dict[str, Any] = Field(default_factory=dict)


class BatchExecuteRequest(BaseModel):
    """Request to execute workflows in batch."""
    workflow_id: uuid.UUID = Field(..., description="Workflow to execute")
    executions: List[BatchExecutionInput] = Field(..., min_length=1)  # the 1000 cap is a 400 in the handler
    parallel_limit: Optional[int] = Field(10, ge=1, le=50, description="Max parallel executions")
    name: Optional[str] = Field(None, max_length=255, description="Batch name")
    webhook_url: Optional[str] = Field(None, description="Webhook URL for completion notification")
    webhook_secret: Optional[str] = Field(None, description="Webhook HMAC secret")


class BatchExecuteResponse(BaseModel):
    """Response from batch execution creation."""
    batch_id: uuid.UUID
    status: str
    total_count: int
    message: str


class BatchStatusResponse(BaseModel):
    """Batch status response."""
    id: uuid.UUID
    workflow_id: Optional[uuid.UUID]
    name: Optional[str]
    status: str
    total_count: int
    completed_count: int
    failed_count: int
    pending_count: int
    progress_percent: float
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    created_at: datetime


class BatchItemResponse(BaseModel):
    """Single batch item response."""
    id: uuid.UUID
    sequence: int
    status: str
    inputs: Dict[str, Any]
    results: Optional[Dict[str, Any]]
    error_message: Optional[str]
    execution_time_ms: Optional[int]


@router.post("/execute-batch", response_model=BatchExecuteResponse)
async def execute_batch(
    request: BatchExecuteRequest,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """
    Execute multiple workflow instances in a single API call.

    Enterprise feature for bulk processing:
    - Max 1000 executions per batch
    - Parallel execution with configurable limit
    - Progress tracking and status monitoring
    - Optional webhook notification on completion

    With the engine workflows flag on, the batch is one engine run: a single fan-out node over the items with
    ``parallel = parallel_limit`` (clamped to the engine's maximum) and ``failFast`` off. Use GET /batches/{id}
    to monitor progress.
    """
    # Validate batch size before spending quota on it
    if len(request.executions) > batch_engine.MAX_ITEMS:
        raise HTTPException(
            status_code=400,
            detail="Maximum 1000 executions per batch"
        )

    # Inline quota check — dynamic estimated_usage from batch size
    quota_svc = QuotaService(db)
    await quota_svc.check_and_raise(
        uuid.UUID(user.tenant_id),
        uuid.UUID(user.id),
        QuotaType.EXECUTIONS,
        estimated_usage=len(request.executions),
    )

    # SSRF validation on webhook URL if provided
    if request.webhook_url:
        ssrf_error = is_url_safe(request.webhook_url)
        if ssrf_error:
            raise HTTPException(status_code=400, detail=f"Invalid webhook URL: {ssrf_error}")

    # Authorization: check execute permission on the workflow
    await _require_execute_permission(
        request.workflow_id, uuid.UUID(user.id), user.role, tenant_id, db
    )

    # Fetch workflow for batch creation
    workflow_query = select(Workflow).where(
        Workflow.id == request.workflow_id,
        Workflow.tenant_id == tenant_id
    )
    result = await db.execute(workflow_query)
    workflow = result.scalars().first()

    if not workflow:
        raise HTTPException(
            status_code=404,
            detail="Workflow not found"
        )

    use_engine = await engine_runs.tenant_uses_engine_workflows(db, tenant_id)
    if use_engine:
        try:
            await batch_engine.compile_batch(db, tenant_id, request.workflow_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    parallel_limit = batch_engine.clamp_parallel(request.parallel_limit or 10)

    # Create batch record
    batch = WorkflowBatch(
        tenant_id=tenant_id,
        workflow_id=request.workflow_id,
        name=request.name or f"Batch execution of {workflow.name}",
        parallel_limit=parallel_limit,
        total_count=len(request.executions),
        pending_count=len(request.executions),
        status=BatchStatus.QUEUED,
        webhook_url=request.webhook_url,
        webhook_secret=request.webhook_secret,
        created_by=uuid.UUID(user.id)
    )
    db.add(batch)
    await db.flush()  # Get batch ID

    # Create batch items
    for i, exec_input in enumerate(request.executions):
        item = WorkflowBatchItem(
            batch_id=batch.id,
            sequence=i,
            inputs=exec_input.inputs,
            status=BatchItemStatus.PENDING
        )
        db.add(item)
    if use_engine:
        db.add(batch_engine.new_execution(batch, user.id))

    await db.commit()

    logger.info(
        f"Created batch {batch.id} with {len(request.executions)} executions "
        f"for workflow {request.workflow_id}"
    )

    status = "queued"
    if use_engine:
        # A start the engine could not take stays queued; the reconciler's ``start_missing`` retries it.
        started = await batch_engine.start_batch_run(engine_runs.tenant_factory(tenant_id), batch.id)
        if started.status != "failed":
            status = "running"

    return BatchExecuteResponse(
        batch_id=batch.id,
        status=status,
        total_count=len(request.executions),
        message=f"Batch {status} with {len(request.executions)} executions"
    )


@router.get("/batches/{batch_id}", response_model=BatchStatusResponse)
async def get_batch_status(
    batch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """
    Get batch execution status.
    """
    query = select(WorkflowBatch).where(
        WorkflowBatch.id == batch_id,
        WorkflowBatch.tenant_id == tenant_id
    )
    result = await db.execute(query)
    batch = result.scalars().first()

    if not batch:
        raise HTTPException(
            status_code=404,
            detail="Batch not found"
        )

    await batch_engine.refresh_progress(db, batch)

    return BatchStatusResponse(
        id=batch.id,
        workflow_id=batch.workflow_id,
        name=batch.name,
        status=batch.status.value,
        total_count=batch.total_count,
        completed_count=batch.completed_count,
        failed_count=batch.failed_count,
        pending_count=batch.pending_count,
        progress_percent=batch.progress_percent,
        started_at=batch.started_at,
        completed_at=batch.completed_at,
        created_at=batch.created_at
    )


@router.get("/batches/{batch_id}/items", response_model=List[BatchItemResponse])
async def list_batch_items(
    batch_id: uuid.UUID,
    status: Optional[str] = Query(None, description="Filter by status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """
    List items in a batch with optional status filter.
    """
    # Verify batch exists and belongs to tenant
    batch_query = select(WorkflowBatch).where(
        WorkflowBatch.id == batch_id,
        WorkflowBatch.tenant_id == tenant_id
    )
    result = await db.execute(batch_query)
    batch = result.scalars().first()

    if not batch:
        raise HTTPException(
            status_code=404,
            detail="Batch not found"
        )

    await batch_engine.refresh_progress(db, batch)

    # Query items
    items_query = select(WorkflowBatchItem).where(
        WorkflowBatchItem.batch_id == batch_id
    )

    if status:
        try:
            status_enum = BatchItemStatus(status)
            items_query = items_query.where(WorkflowBatchItem.status == status_enum)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status. Valid values: {[s.value for s in BatchItemStatus]}"
            )

    items_query = items_query.order_by(WorkflowBatchItem.sequence).offset(skip).limit(limit)

    result = await db.execute(items_query)
    items = result.scalars().all()

    return [
        BatchItemResponse(
            id=item.id,
            sequence=item.sequence,
            status=item.status.value,
            inputs=item.inputs,
            results=item.results,
            error_message=item.error_message,
            execution_time_ms=item.execution_time_ms
        )
        for item in items
    ]


@router.post("/batches/{batch_id}/cancel")
async def cancel_batch(
    batch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """
    Cancel a running or queued batch.

    Pending items will be marked as skipped. For an engine batch the run is aborted (running items stop
    too); items that already finished keep their results.
    """
    query = select(WorkflowBatch).where(
        WorkflowBatch.id == batch_id,
        WorkflowBatch.tenant_id == tenant_id
    )
    result = await db.execute(query)
    batch = result.scalars().first()

    if not batch:
        raise HTTPException(
            status_code=404,
            detail="Batch not found"
        )

    if batch.status in (BatchStatus.COMPLETED, BatchStatus.FAILED, BatchStatus.CANCELLED):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel batch in {batch.status.value} status"
        )

    if not await batch_engine.cancel_batch_run(db, batch):
        # No engine run (created with the flag off): nothing is running, so skip the pending items.
        batch.status = BatchStatus.CANCELLED
        batch.completed_at = datetime.now(timezone.utc)
        from sqlalchemy import update
        await db.execute(
            update(WorkflowBatchItem)
            .where(
                WorkflowBatchItem.batch_id == batch_id,
                WorkflowBatchItem.status == BatchItemStatus.PENDING
            )
            .values(status=BatchItemStatus.SKIPPED)
        )

    await db.commit()

    logger.info(f"Cancelled batch {batch_id}")

    return {"message": "Batch cancelled", "batch_id": str(batch_id)}


# ============================================
# Execution Cancellation Endpoint (Enterprise)
# ============================================

@router.get("/executions/{execution_id}/waiting-nodes")
async def list_waiting_nodes(
    execution_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
) -> Dict[str, Any]:
    """Nodes of a run parked on a human decision (plan 003 U21), for the canvas.

    Each has the node id, the wake time (the SLA, or null when open-ended), the approval ledger id and the
    ids the decision route needs (``POST /engine/conversations/{conversationId}/approvals/{taskId}/decision``).
    The run's owner and anyone who may decide approvals see them; another tenant's run is a 404.
    """
    from app.auth.permissions import has_permission
    from app.models.tool_approval import OPEN_STATUSES, ToolApproval

    execution = (await db.execute(select(WorkflowExecution).where(
        WorkflowExecution.id == execution_id, WorkflowExecution.tenant_id == tenant_id))).scalar_one_or_none()
    if execution is None:
        raise HTTPException(status_code=404, detail="Execution not found")
    if str(execution.user_id) != str(user.id) and not has_permission(user.role, "approvals:decide"):
        raise HTTPException(status_code=403, detail="You don't have access to this execution")
    rows = (await db.execute(
        select(ToolApproval).where(
            ToolApproval.tenant_id == tenant_id, ToolApproval.run_id == str(execution_id),
            ToolApproval.kind == "review", ToolApproval.status.in_(OPEN_STATUSES),
        ).order_by(ToolApproval.created_at, ToolApproval.id))).scalars().all()
    return {
        "executionId": str(execution_id),
        "nodes": [
            {
                "nodeId": (row.arguments_preview or {}).get("nodeId"),
                "kind": "human_in_loop",
                "title": row.tool_name,
                "status": row.status,
                "wakeAt": row.expires_at.isoformat() if row.expires_at else None,
                "approvalId": str(row.id),
                "conversationId": str(row.conversation_id) if row.conversation_id else None,
                "taskId": row.tool_task_id,
            }
            for row in rows
        ],
    }


@router.post("/{execution_id}/cancel")
async def cancel_execution(
    execution_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
) -> Dict[str, str]:
    """
    Cancel a running workflow execution.

    Enterprise feature: Stop in-flight executions gracefully.
    Current node completes, then execution stops with 'cancelled' status.

    Authorization:
    - Users can cancel their own executions
    - Admins can cancel any execution in their tenant

    Args:
        execution_id: ID of the execution to cancel
        db: Database session
        user: Current authenticated user
        tenant_id: Current tenant ID

    Returns:
        Status message

    Raises:
        HTTPException 404: Execution not found
        HTTPException 403: User not authorized to cancel
        HTTPException 422: Execution already completed/failed/cancelled
    """
    user_id = uuid.UUID(user.id)

    # Find execution with tenant isolation
    query = select(WorkflowExecution).where(
        WorkflowExecution.id == execution_id,
        WorkflowExecution.tenant_id == tenant_id
    )
    result = await db.execute(query)
    execution = result.scalar_one_or_none()

    if not execution:
        raise HTTPException(
            status_code=404,
            detail="Execution not found"
        )

    # Check authorization - owner or admin
    is_owner = execution.user_id == user_id
    is_admin = user.role == "admin"

    if not is_owner and not is_admin:
        raise HTTPException(
            status_code=403,
            detail="You can only cancel your own executions"
        )

    # Check if execution is in cancellable state
    if execution.status in ['completed', 'failed', 'cancelled']:
        raise HTTPException(
            status_code=422,
            detail=f"Cannot cancel execution with status: {execution.status}"
        )

    if engine_runs.is_engine_execution(execution):
        # Engine run: a real abort of the run's conversation (plan 003 U15, KTD9). An abort the engine
        # could not take yet is retried by the reconciler; the row is cancelled either way.
        await engine_runs.cancel_workflow_run(db, execution, "Execution cancelled by user")
    else:
        # Legacy run: the orchestrator checks this status before each node.
        execution.status = 'cancelled'
        execution.completed_at = datetime.now(timezone.utc)
        execution.error = "Execution cancelled by user"
    await db.commit()

    logger.info(f"Execution {execution_id} cancelled by user {user_id}")

    return {
        "status": "cancelled",
        "message": "Execution cancelled successfully",
        "execution_id": str(execution_id)
    }