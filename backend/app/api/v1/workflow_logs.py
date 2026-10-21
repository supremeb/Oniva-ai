"""
Workflow Logs API endpoints.

Provides endpoints for:
- Listing workflow execution logs with filtering and pagination
- Getting detailed execution traces for specific runs
- Real-time log streaming via SSE
- Enterprise monitoring metrics

Part of : Enterprise-grade workflow monitoring.
"""
import asyncio
import json
import uuid
from typing import Optional
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func, desc, or_, String, case
from sqlalchemy.orm import selectinload

from app.core.database import get_db, get_db_for_tenant
from app.models.background_job import BackgroundJob
from app.models.workflow import WorkflowVersion
from app.models.workflow import Workflow
from app.auth.dependencies import get_current_tenant_id, get_current_user_id, get_current_user
from app.auth.providers.base import AuthUser
from app.schemas.background_job import JobStatus
from app.schemas.workflow_logs import (
    WorkflowLogSummary,
    WorkflowLogListResponse,
    WorkflowLogDetailResponse,
    ExecutionTraceStep,
    MonitoringMetrics,
    TopWorkflow,
    TopWorkflowsResponse,
    RecentFailure,
    RecentFailuresResponse,
)

router = APIRouter()


# ============================================
# Permission helper
# ============================================

async def _require_workflow_access(
    workflow_id: uuid.UUID,
    user_id: uuid.UUID,
    user_role: str,
    tenant_id: uuid.UUID,
    db: AsyncSession,
) -> None:
    """Verify user has at least view access to the workflow. Raises 404/403."""
    from app.models.workflow_permission import WorkflowPermission, GranteeType

    wf = await db.execute(
        select(Workflow).where(Workflow.id == workflow_id, Workflow.tenant_id == tenant_id)
    )
    workflow = wf.scalars().first()
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    # Owner always has access
    if workflow.user_id == user_id:
        return

    # Check shared permissions
    now = datetime.now(timezone.utc)
    perm = await db.execute(
        select(WorkflowPermission).where(
            WorkflowPermission.workflow_id == workflow_id,
            WorkflowPermission.tenant_id == tenant_id,
            or_(
                and_(
                    WorkflowPermission.grantee_type == GranteeType.USER.value,
                    WorkflowPermission.grantee_id == str(user_id),
                ),
                and_(
                    WorkflowPermission.grantee_type == GranteeType.ROLE.value,
                    WorkflowPermission.grantee_id == user_role,
                ),
                WorkflowPermission.grantee_type == GranteeType.ORGANIZATION.value,
            ),
            or_(
                WorkflowPermission.expires_at.is_(None),
                WorkflowPermission.expires_at > now,
            ),
        ).limit(1)
    )
    if not perm.scalars().first():
        raise HTTPException(status_code=403, detail="You don't have access to this workflow's logs")


# ============================================
# Constants
# ============================================

# Time range mapping to hours
TIME_RANGE_HOURS = {
    "1h": 1,
    "24h": 24,
    "7d": 168,
    "30d": 720,
}

# Frontend to backend status mapping using JobStatus enum
FRONTEND_STATUS_MAP = {
    "success": JobStatus.COMPLETED.value,
    "failed": JobStatus.FAILED.value,
    "running": JobStatus.RUNNING.value,
}

# SSE configuration constants
SSE_HEARTBEAT_INTERVAL = 15  # seconds between heartbeats
SSE_POLL_INTERVAL = 2  # seconds between log polls
SSE_MAX_IDLE_TIME = 30 * 60  # 30 minutes before stream timeout
SSE_MAX_SEEN_IDS = 1000  # Maximum tracked IDs for memory bounds
SSE_INITIAL_FETCH_LIMIT = 50  # Initial jobs to fetch
SSE_POLL_LIMIT = 20  # Jobs to fetch per poll


def utc_now() -> datetime:
    """Return current UTC time as timezone-aware datetime."""
    return datetime.now(timezone.utc)


@router.get("/{workflow_id}/logs", response_model=WorkflowLogListResponse)
async def get_workflow_logs(
    workflow_id: uuid.UUID,
    status: Optional[str] = Query(None, pattern="^(running|success|failed)$"),
    trigger: Optional[str] = Query(None, pattern="^(manual|api|scheduled)$"),
    time_range: str = Query("7d", pattern="^(1h|24h|7d|30d)$"),
    search: Optional[str] = Query(None, max_length=200),
    version: Optional[int] = Query(None, ge=1, le=10000, description="Filter by workflow version number"),
    cursor: Optional[str] = Query(None, description="Cursor for pagination (created_at:id)"),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    user_id: uuid.UUID = Depends(get_current_user_id),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
):
    """
    Get paginated workflow execution logs with filtering.

    Uses cursor-based pagination for O(1) performance at scale.

    Filters:
    - status: running, success, failed
    - trigger: manual, api, scheduled
    - time_range: 1h, 24h, 7d, 30d
    - search: Search in error messages and job ID
    - version: Filter by workflow version number
    """
    await _require_workflow_access(workflow_id, user_id, user.role, tenant_id, db)

    # Calculate time cutoff
    cutoff = utc_now() - timedelta(hours=TIME_RANGE_HOURS[time_range])

    # Build base query with eager loading to prevent N+1 queries
    query = select(BackgroundJob).options(
        selectinload(BackgroundJob.workflow_version)
    ).where(
        and_(
            BackgroundJob.workflow_id == workflow_id,
            BackgroundJob.tenant_id == tenant_id,
            BackgroundJob.created_at >= cutoff,
        )
    )

    # Apply status filter (map frontend names to backend status using enum)
    if status:
        query = query.where(BackgroundJob.status == FRONTEND_STATUS_MAP[status])

    # Apply trigger filter
    if trigger:
        query = query.where(BackgroundJob.trigger == trigger)

    # Apply version filter via join to workflow_versions
    if version is not None:
        query = query.join(
            WorkflowVersion,
            BackgroundJob.workflow_version_id == WorkflowVersion.id
        ).where(WorkflowVersion.version_number == version)

    # Apply search filter (sanitize to prevent SQL injection in LIKE)
    if search:
        sanitized_search = search.replace("%", r"\%").replace("_", r"\_")
        query = query.where(
            or_(
                BackgroundJob.error.ilike(f"%{sanitized_search}%", escape="\\"),
                BackgroundJob.id.cast(String).ilike(f"%{sanitized_search}%", escape="\\")
            )
        )

    # Cursor-based pagination (O(1) instead of O(n) offset)
    if cursor:
        try:
            cursor_time, cursor_id = cursor.split(":")
            cursor_datetime = datetime.fromisoformat(cursor_time)
            cursor_uuid = uuid.UUID(cursor_id)
            query = query.where(
                or_(
                    BackgroundJob.created_at < cursor_datetime,
                    and_(
                        BackgroundJob.created_at == cursor_datetime,
                        BackgroundJob.id < cursor_uuid
                    )
                )
            )
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid cursor format")

    # Order and limit (fetch one extra to determine has_more)
    query = query.order_by(
        desc(BackgroundJob.created_at),
        desc(BackgroundJob.id)
    ).limit(limit + 1)

    result = await db.execute(query)
    jobs = result.scalars().all()

    # Determine if there are more results
    has_more = len(jobs) > limit
    if has_more:
        jobs = jobs[:limit]

    # Build next cursor
    next_cursor = None
    if has_more and jobs:
        last_job = jobs[-1]
        next_cursor = f"{last_job.created_at.isoformat()}:{last_job.id}"

    # Convert to response format
    logs = [
        WorkflowLogSummary(
            id=str(job.id),
            workflow_id=str(job.workflow_id),
            workflow_version_id=str(job.workflow_version_id) if job.workflow_version_id else None,
            version_number=job.workflow_version.version_number if job.workflow_version else None,
            status=job.status,
            trigger=job.trigger,
            started_at=job.started_at.isoformat() if job.started_at else None,
            completed_at=job.completed_at.isoformat() if job.completed_at else None,
            duration_ms=job.duration_ms,
            total_tokens=job.total_tokens,
            nodes_executed=job.nodes_executed,
            error=job.error,
            progress=job.progress,
        )
        for job in jobs
    ]

    return WorkflowLogListResponse(
        logs=logs,
        next_cursor=next_cursor,
        limit=limit,
        has_more=has_more,
    )


@router.get("/{workflow_id}/latest-execution", response_model=WorkflowLogDetailResponse)
async def get_latest_execution(
    workflow_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    user_id: uuid.UUID = Depends(get_current_user_id),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
):
    """
    Get the most recent completed execution for a workflow.

    Used by the frontend to restore node statuses and results on page refresh.
    Returns 404 if no completed execution exists.
    """
    await _require_workflow_access(workflow_id, user_id, user.role, tenant_id, db)

    query = select(BackgroundJob).options(
        selectinload(BackgroundJob.workflow_version)
    ).where(
        and_(
            BackgroundJob.workflow_id == workflow_id,
            BackgroundJob.tenant_id == tenant_id,
            BackgroundJob.status.in_([JobStatus.COMPLETED.value, JobStatus.FAILED.value]),
        )
    ).order_by(desc(BackgroundJob.created_at)).limit(1)

    result = await db.execute(query)
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(status_code=404, detail="No completed execution found")

    execution_trace = []
    if job.execution_trace:
        for step in job.execution_trace:
            if isinstance(step, dict):
                execution_trace.append(ExecutionTraceStep(
                    step_id=step.get("step_id", ""),
                    node_id=step.get("node_id", ""),
                    node_type=step.get("node_type", "unknown"),
                    node_name=step.get("node_name"),
                    status=step.get("status", "completed"),
                    started_at=step.get("started_at"),
                    completed_at=step.get("completed_at"),
                    duration_ms=step.get("duration_ms"),
                    input=step.get("input"),
                    output=step.get("output"),
                    error=step.get("error"),
                    token_usage=step.get("token_usage"),
                    reasoning=step.get("reasoning"),
                ))

    return WorkflowLogDetailResponse(
        id=str(job.id),
        workflow_id=str(job.workflow_id),
        workflow_version_id=str(job.workflow_version_id) if job.workflow_version_id else None,
        version_number=job.workflow_version.version_number if job.workflow_version else None,
        status=job.status,
        trigger=job.trigger,
        started_at=job.started_at.isoformat() if job.started_at else None,
        completed_at=job.completed_at.isoformat() if job.completed_at else None,
        duration_ms=job.duration_ms,
        input_data=job.input_data,
        result=job.result,
        error=job.error,
        execution_trace=execution_trace,
        total_tokens=job.total_tokens,
        nodes_executed=job.nodes_executed,
        progress=job.progress,
    )


# NOTE: /stream must be defined BEFORE /{run_id} to prevent "stream" from matching as a UUID
@router.get("/{workflow_id}/logs/stream")
async def stream_workflow_logs(
    workflow_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    user_id: uuid.UUID = Depends(get_current_user_id),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
):
    """
    SSE endpoint for real-time log updates.

    Streams new log entries and status updates for the workflow.

    Features:
    - 15-second heartbeat to prevent proxy/load balancer timeouts
    - Polling-based (Redis pub/sub can be added for scale)
    - 30-minute timeout on inactivity
    - Fresh DB sessions per poll to avoid stale data
    - Bounded memory for seen IDs
    - Sanitized error messages
    """
    # Verify user has access to this workflow
    await _require_workflow_access(workflow_id, user_id, user.role, tenant_id, db)

    # H1: Verify workflow exists and belongs to tenant before streaming
    workflow_query = select(Workflow.id).where(
        and_(
            Workflow.id == workflow_id,
            Workflow.tenant_id == tenant_id,
        )
    )
    result = await db.execute(workflow_query)
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Workflow not found")

    # Capture tenant_id as string for use in generator
    tenant_id_str = str(tenant_id)

    async def event_generator():
        last_activity = utc_now()
        last_seen_ids: set = set()

        try:
            # Initial fetch to populate known IDs - use fresh session (C1)
            async with get_db_for_tenant(tenant_id_str) as session:
                initial_query = select(BackgroundJob.id).where(
                    and_(
                        BackgroundJob.workflow_id == workflow_id,
                        BackgroundJob.tenant_id == tenant_id,
                        BackgroundJob.created_at >= utc_now() - timedelta(hours=1),
                    )
                ).order_by(desc(BackgroundJob.created_at)).limit(SSE_INITIAL_FETCH_LIMIT)

                result = await session.execute(initial_query)
                last_seen_ids = {row[0] for row in result.all()}

            heartbeat_counter = 0

            while True:
                # Check for timeout
                if (utc_now() - last_activity).total_seconds() > SSE_MAX_IDLE_TIME:
                    yield f"event: timeout\ndata: {{\"message\": \"Stream closed due to inactivity\"}}\n\n"
                    break

                # C1: Use fresh DB session for each poll to avoid stale data
                async with get_db_for_tenant(tenant_id_str) as session:
                    # Poll for new/updated jobs
                    poll_query = select(BackgroundJob).where(
                        and_(
                            BackgroundJob.workflow_id == workflow_id,
                            BackgroundJob.tenant_id == tenant_id,
                            BackgroundJob.created_at >= utc_now() - timedelta(hours=1),
                        )
                    ).order_by(desc(BackgroundJob.created_at)).limit(SSE_POLL_LIMIT)

                    result = await session.execute(poll_query)
                    jobs = result.scalars().all()

                    for job in jobs:
                        # Check if this is a new job or has updates (running status)
                        is_new = job.id not in last_seen_ids
                        is_running = job.status == JobStatus.RUNNING.value

                        if is_new or is_running:
                            last_seen_ids.add(job.id)
                            last_activity = utc_now()

                            log_data = {
                                "id": str(job.id),
                                "workflow_id": str(job.workflow_id),
                                "status": job.status,
                                "trigger": job.trigger,
                                "progress": job.progress,
                                "started_at": job.started_at.isoformat() if job.started_at else None,
                                "completed_at": job.completed_at.isoformat() if job.completed_at else None,
                                "duration_ms": job.duration_ms,
                                "total_tokens": job.total_tokens,
                                "nodes_executed": job.nodes_executed,
                                "error": job.error,
                            }
                            yield f"data: {json.dumps(log_data)}\n\n"

                # H7: Bound memory - prune oldest IDs if limit exceeded
                if len(last_seen_ids) > SSE_MAX_SEEN_IDS:
                    # Keep only the most recent half
                    last_seen_ids = set(list(last_seen_ids)[-SSE_MAX_SEEN_IDS // 2:])

                # Heartbeat every N poll cycles
                heartbeat_counter += 1
                if heartbeat_counter >= (SSE_HEARTBEAT_INTERVAL // SSE_POLL_INTERVAL):
                    heartbeat_counter = 0
                    yield f"event: heartbeat\ndata: {{\"timestamp\": \"{utc_now().isoformat()}\"}}\n\n"

                await asyncio.sleep(SSE_POLL_INTERVAL)

        except asyncio.CancelledError:
            # Client disconnected
            pass
        except Exception:
            # C2: Sanitize error - don't leak internal exception details
            yield f"event: error\ndata: {{\"error\": \"An internal error occurred\", \"retry\": 3000}}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        }
    )


@router.get("/{workflow_id}/logs/{run_id}", response_model=WorkflowLogDetailResponse)
async def get_workflow_log_detail(
    workflow_id: uuid.UUID,
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    user_id: uuid.UUID = Depends(get_current_user_id),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
):
    """
    Get detailed execution trace for a specific workflow run.

    Includes full input/output data for each node execution step.
    """
    await _require_workflow_access(workflow_id, user_id, user.role, tenant_id, db)
    query = select(BackgroundJob).options(
        selectinload(BackgroundJob.workflow_version)
    ).where(
        and_(
            BackgroundJob.id == run_id,
            BackgroundJob.workflow_id == workflow_id,
            BackgroundJob.tenant_id == tenant_id,
        )
    )

    result = await db.execute(query)
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(status_code=404, detail="Log not found")

    # Parse execution trace into typed steps
    execution_trace = []
    if job.execution_trace:
        for step in job.execution_trace:
            if isinstance(step, dict):
                execution_trace.append(ExecutionTraceStep(
                    step_id=step.get("step_id", ""),
                    node_id=step.get("node_id", ""),
                    node_type=step.get("node_type", "unknown"),
                    node_name=step.get("node_name"),
                    status=step.get("status", "completed"),
                    started_at=step.get("started_at"),
                    completed_at=step.get("completed_at"),
                    duration_ms=step.get("duration_ms"),
                    input=step.get("input"),
                    output=step.get("output"),
                    error=step.get("error"),
                    token_usage=step.get("token_usage"),
                    reasoning=step.get("reasoning"),
                ))

    return WorkflowLogDetailResponse(
        id=str(job.id),
        workflow_id=str(job.workflow_id),
        workflow_version_id=str(job.workflow_version_id) if job.workflow_version_id else None,
        version_number=job.workflow_version.version_number if job.workflow_version else None,
        status=job.status,
        trigger=job.trigger,
        started_at=job.started_at.isoformat() if job.started_at else None,
        completed_at=job.completed_at.isoformat() if job.completed_at else None,
        duration_ms=job.duration_ms,
        input_data=job.input_data,
        result=job.result,
        error=job.error,
        execution_trace=execution_trace,
        total_tokens=job.total_tokens,
        nodes_executed=job.nodes_executed,
        progress=job.progress,
    )


# ============================================
# Monitoring Endpoints
# ============================================

@router.get("/monitoring/metrics", response_model=MonitoringMetrics)
async def get_monitoring_metrics(
    time_range: str = Query("24h", pattern="^(1h|24h|7d|30d)$"),
    workflow_id: Optional[uuid.UUID] = Query(None),
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
):
    """
    Get aggregated monitoring metrics for the tenant.

    Optionally filter by specific workflow.
    """
    cutoff = utc_now() - timedelta(hours=TIME_RANGE_HOURS[time_range])

    # Base filter
    base_filter = and_(
        BackgroundJob.tenant_id == tenant_id,
        BackgroundJob.created_at >= cutoff,
    )
    if workflow_id:
        base_filter = and_(base_filter, BackgroundJob.workflow_id == workflow_id)

    # Aggregate query - use JobStatus enum values for consistency
    query = select(
        func.count().label('total_executions'),
        func.count().filter(BackgroundJob.status == JobStatus.COMPLETED.value).label('successful'),
        func.count().filter(BackgroundJob.status == JobStatus.FAILED.value).label('failed'),
        func.count().filter(BackgroundJob.status == JobStatus.RUNNING.value).label('running'),
        func.avg(
            case(
                (
                    BackgroundJob.completed_at.isnot(None),
                    func.extract('epoch', BackgroundJob.completed_at - BackgroundJob.started_at) * 1000
                ),
                else_=None
            )
        ).label('avg_duration_ms'),
    ).where(base_filter)

    result = await db.execute(query)
    row = result.one()

    total = row.total_executions or 0
    successful = row.successful or 0
    success_rate = (successful / total * 100) if total > 0 else 0

    return MonitoringMetrics(
        time_range=time_range,
        total_executions=total,
        successful=successful,
        failed=row.failed or 0,
        running=row.running or 0,
        success_rate=round(success_rate, 1),
        avg_duration_ms=int(row.avg_duration_ms) if row.avg_duration_ms else None,
    )


@router.get("/monitoring/top-workflows", response_model=TopWorkflowsResponse)
async def get_top_workflows(
    time_range: str = Query("24h", pattern="^(1h|24h|7d|30d)$"),
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
):
    """
    Get top workflows by execution count.
    """
    cutoff = utc_now() - timedelta(hours=TIME_RANGE_HOURS[time_range])

    # Query with workflow name join
    query = (
        select(
            BackgroundJob.workflow_id,
            Workflow.name.label('workflow_name'),
            func.count().label('execution_count'),
            func.count().filter(BackgroundJob.status == JobStatus.FAILED.value).label('failure_count'),
        )
        .join(Workflow, BackgroundJob.workflow_id == Workflow.id, isouter=True)
        .where(
            and_(
                BackgroundJob.tenant_id == tenant_id,
                BackgroundJob.created_at >= cutoff,
                BackgroundJob.workflow_id.isnot(None),
            )
        )
        .group_by(BackgroundJob.workflow_id, Workflow.name)
        .order_by(func.count().desc())
        .limit(limit)
    )

    result = await db.execute(query)
    rows = result.all()

    workflows = []
    for row in rows:
        exec_count = row.execution_count
        fail_count = row.failure_count
        success_rate = ((exec_count - fail_count) / exec_count * 100) if exec_count > 0 else 0

        workflows.append(TopWorkflow(
            workflow_id=str(row.workflow_id),
            workflow_name=row.workflow_name,
            execution_count=exec_count,
            failure_count=fail_count,
            success_rate=round(success_rate, 1),
        ))

    return TopWorkflowsResponse(workflows=workflows)


@router.get("/monitoring/recent-failures", response_model=RecentFailuresResponse)
async def get_recent_failures(
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
):
    """
    Get most recent failed executions.
    """
    query = (
        select(
            BackgroundJob.id,
            BackgroundJob.workflow_id,
            Workflow.name.label('workflow_name'),
            BackgroundJob.error,
            BackgroundJob.completed_at,
            BackgroundJob.trigger,
        )
        .join(Workflow, BackgroundJob.workflow_id == Workflow.id, isouter=True)
        .where(
            and_(
                BackgroundJob.tenant_id == tenant_id,
                BackgroundJob.status == JobStatus.FAILED.value,
            )
        )
        .order_by(BackgroundJob.created_at.desc())
        .limit(limit)
    )

    result = await db.execute(query)
    rows = result.all()

    failures = [
        RecentFailure(
            id=str(row.id),
            workflow_id=str(row.workflow_id) if row.workflow_id else "",
            workflow_name=row.workflow_name,
            error=row.error or "Unknown error",
            failed_at=row.completed_at.isoformat() if row.completed_at else "",
            trigger=row.trigger or "manual",
        )
        for row in rows
    ]

    return RecentFailuresResponse(failures=failures)


# ============================================
# Time Series (Phase 1: Observability Dashboard)
# ============================================

from pydantic import BaseModel as PydanticBaseModel


class TimeSeriesPoint(PydanticBaseModel):
    period: datetime
    count: int
    successful: int
    failed: int


class MonitoringTimeSeries(PydanticBaseModel):
    points: list[TimeSeriesPoint]
    granularity: str


@router.get("/{workflow_id}/monitoring/time-series", response_model=MonitoringTimeSeries)
async def get_monitoring_time_series(
    workflow_id: uuid.UUID,
    time_range: str = Query("24h", pattern="^(1h|24h|7d|30d)$"),
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    user_id: uuid.UUID = Depends(get_current_user_id),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
) -> MonitoringTimeSeries:
    """Get bucketed execution counts for sparkline charts.

    Returns time-series data grouped by hour (1h/24h) or day (7d/30d).
    Uses created_at (not nullable started_at) for grouping.
    """
    await _require_workflow_access(workflow_id, user_id, user.role, tenant_id, db)

    hours = TIME_RANGE_HOURS[time_range]
    granularity = "hour" if hours <= 24 else "day"
    cutoff = utc_now() - timedelta(hours=hours)

    query = (
        select(
            func.date_trunc(granularity, BackgroundJob.created_at).label("period"),
            func.count().label("count"),
            func.count().filter(BackgroundJob.status == JobStatus.COMPLETED.value).label("successful"),
            func.count().filter(BackgroundJob.status == JobStatus.FAILED.value).label("failed"),
        )
        .where(
            and_(
                BackgroundJob.tenant_id == tenant_id,
                BackgroundJob.workflow_id == workflow_id,
                BackgroundJob.created_at >= cutoff,
            )
        )
        .group_by("period")
        .order_by("period")
    )

    result = await db.execute(query)
    rows = result.all()

    points = [
        TimeSeriesPoint(
            period=row.period,
            count=row.count,
            successful=row.successful,
            failed=row.failed,
        )
        for row in rows
    ]

    return MonitoringTimeSeries(points=points, granularity=granularity)
