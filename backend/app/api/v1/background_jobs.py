"""Background Jobs API with SSE streaming for real-time progress updates."""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from typing import Optional, AsyncGenerator
from uuid import UUID
from datetime import datetime
import asyncio
import json
import logging

import httpx

from app.models.background_job import BackgroundJob, JobStatus as ModelJobStatus
from app.models.custom_agent import CustomAgent
from app.models.workflow import Workflow
from app.core.database import get_db
from app.services import fleet_engine
from app.auth.dependencies import get_current_user, get_current_tenant_id
from app.auth.providers.base import AuthUser
from app.schemas.background_job import (
    JobStatus,
    JobType,
    BackgroundJobCreate,
    BackgroundJobResponse,
    BackgroundJobListResponse,
    BackgroundJobDetailResponse,
    CancelJobRequest,
    CancelJobResponse,
    JobProgressUpdate,
    JobCompletedEvent,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def job_to_response(job: BackgroundJob) -> BackgroundJobResponse:
    """Convert BackgroundJob model to response schema."""
    return BackgroundJobResponse(
        id=job.id,
        tenant_id=job.tenant_id,
        user_id=job.user_id,
        workflow_id=job.workflow_id,
        agent_id=job.agent_id,
        job_type=job.job_type,
        status=job.status,
        progress=job.progress,
        input_data=job.input_data,
        result=job.result,
        error=job.error,
        execution_trace=job.execution_trace or [],
        started_at=job.started_at,
        completed_at=job.completed_at,
        created_at=job.created_at,
        duration_ms=job.duration_ms,
    )


@router.post("/", response_model=BackgroundJobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(
    job_data: BackgroundJobCreate,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id)
):
    """
    Create a new background job.

    Either workflow_id or agent_id should be provided based on job type.
    """
    user_id = UUID(user.id)

    # Validate workflow/agent exists if provided
    if job_data.workflow_id:
        result = await db.execute(
            select(Workflow).where(
                Workflow.id == job_data.workflow_id,
                Workflow.user_id == user_id
            )
        )
        if not result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workflow not found"
            )

    if job_data.agent_id:
        result = await db.execute(
            select(CustomAgent).where(
                CustomAgent.id == job_data.agent_id,
                CustomAgent.user_id == user_id
            )
        )
        if not result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Agent not found"
            )

    use_engine = bool(
        job_data.job_type.value == "background_agent" and job_data.agent_id
        and await fleet_engine.tenant_uses_engine_fleet(db, tenant_id)
    )

    # Create job
    job = BackgroundJob(
        tenant_id=tenant_id,
        user_id=user_id,
        workflow_id=job_data.workflow_id,
        agent_id=job_data.agent_id,
        job_type=job_data.job_type.value,
        status=ModelJobStatus.PENDING.value,
        trigger="manual",
        input_data=fleet_engine.mark_engine(job_data.input_data) if use_engine else job_data.input_data,
        execution_trace=[],
    )

    db.add(job)
    await db.commit()
    await db.refresh(job)

    if use_engine:
        # Engine tenants: a conversation plus one submission, no asyncio task. A start that cannot
        # reach the engine leaves the job pending and the reconciler retries it (plan 003 U7).
        await fleet_engine.start_after_commit(tenant_id, job.id)
        await db.refresh(job)
    # Spawn background agent executor for background_agent jobs (legacy path, flag off)
    elif job_data.job_type.value == "background_agent" and job_data.agent_id:
        from app.services.background_agent_executor import run_background_agent
        asyncio.create_task(
            run_background_agent(
                job_id=job.id,
                agent_id=job_data.agent_id,
                tenant_id=tenant_id,
                user_id=user_id,
                input_data=job_data.input_data,
            )
        )

    return job_to_response(job)


@router.get("/", response_model=BackgroundJobListResponse)
async def list_jobs(
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    status_filter: Optional[JobStatus] = Query(None, alias="status"),
    job_type: Optional[JobType] = None,
    workflow_id: Optional[UUID] = None,
    agent_id: Optional[UUID] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """
    List background jobs with filtering and pagination.
    """
    user_id = UUID(user.id)

    # Build query
    query = select(BackgroundJob).where(BackgroundJob.user_id == user_id)
    count_query = select(func.count()).select_from(BackgroundJob).where(BackgroundJob.user_id == user_id)

    # Apply filters
    if status_filter:
        query = query.where(BackgroundJob.status == status_filter.value)
        count_query = count_query.where(BackgroundJob.status == status_filter.value)

    if job_type:
        query = query.where(BackgroundJob.job_type == job_type.value)
        count_query = count_query.where(BackgroundJob.job_type == job_type.value)

    if workflow_id:
        query = query.where(BackgroundJob.workflow_id == workflow_id)
        count_query = count_query.where(BackgroundJob.workflow_id == workflow_id)

    if agent_id:
        query = query.where(BackgroundJob.agent_id == agent_id)
        count_query = count_query.where(BackgroundJob.agent_id == agent_id)

    # Get total count
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Apply ordering and pagination
    query = query.order_by(desc(BackgroundJob.created_at))
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    jobs = result.scalars().all()

    return BackgroundJobListResponse(
        jobs=[job_to_response(j) for j in jobs],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/active", response_model=BackgroundJobListResponse)
async def list_active_jobs(
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
):
    """
    List currently running or pending jobs.
    """
    user_id = UUID(user.id)

    query = (
        select(BackgroundJob)
        .where(
            BackgroundJob.user_id == user_id,
            BackgroundJob.status.in_([
                ModelJobStatus.PENDING.value,
                ModelJobStatus.RUNNING.value
            ])
        )
        .order_by(desc(BackgroundJob.created_at))
    )

    result = await db.execute(query)
    jobs = result.scalars().all()

    return BackgroundJobListResponse(
        jobs=[job_to_response(j) for j in jobs],
        total=len(jobs),
        page=1,
        page_size=len(jobs),
    )


@router.get("/{job_id}", response_model=BackgroundJobDetailResponse)
async def get_job(
    job_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user)
):
    """
    Get a specific job with full details.
    """
    user_id = UUID(user.id)

    result = await db.execute(
        select(BackgroundJob).where(
            BackgroundJob.id == job_id,
            BackgroundJob.user_id == user_id
        )
    )
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found"
        )

    # Get workflow/agent names
    workflow_name = None
    agent_name = None

    if job.workflow_id:
        workflow_result = await db.execute(
            select(Workflow.name).where(Workflow.id == job.workflow_id)
        )
        workflow_name = workflow_result.scalar_one_or_none()

    if job.agent_id:
        agent_result = await db.execute(
            select(CustomAgent.name).where(CustomAgent.id == job.agent_id)
        )
        agent_name = agent_result.scalar_one_or_none()

    response = job_to_response(job)
    return BackgroundJobDetailResponse(
        **response.model_dump(),
        workflow_name=workflow_name,
        agent_name=agent_name,
    )


@router.post("/{job_id}/cancel", response_model=CancelJobResponse)
async def cancel_job(
    job_id: UUID,
    request: CancelJobRequest = None,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user)
):
    """
    Cancel a running or pending job.
    """
    user_id = UUID(user.id)

    result = await db.execute(
        select(BackgroundJob).where(
            BackgroundJob.id == job_id,
            BackgroundJob.user_id == user_id
        )
    )
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found"
        )

    if job.is_completed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Job has already completed"
        )

    reason = request.reason if request and request.reason else None
    if fleet_engine.is_engine_job(job):
        # Stop the engine run first; an outage still cancels the job and leaves the abort for the reconciler.
        settled = await fleet_engine.abort_run(db, job, reason)
        fleet_engine.set_abort_pending(job, not settled)
    job.cancel()
    if reason:
        job.error = f"Cancelled: {reason}"

    await db.commit()
    await db.refresh(job)

    return CancelJobResponse(
        success=True,
        job_id=job.id,
        status=job.status,
        message="Job cancelled successfully"
    )


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job(
    job_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user)
):
    """
    Delete a completed job.
    """
    user_id = UUID(user.id)

    result = await db.execute(
        select(BackgroundJob).where(
            BackgroundJob.id == job_id,
            BackgroundJob.user_id == user_id
        )
    )
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found"
        )

    if not job.is_completed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete a running job. Cancel it first."
        )

    await db.delete(job)
    await db.commit()


_LIVE_STATUSES = (ModelJobStatus.PENDING.value, ModelJobStatus.RUNNING.value)


class _LiveState:
    """What the live engine phase already sent, so the row-polling phase continues without repeats."""

    def __init__(self) -> None:
        self.progress = -1
        self.steps = 0


async def _engine_live_events(job: BackgroundJob, tenant_id: UUID, live: _LiveState) -> AsyncGenerator[str, None]:
    """Live view of an engine-run job: follow the engine watch and emit ``progress`` and ``step`` events.

    Ends when the run settles, the watch stream ends, or the engine cannot be reached; the caller then
    continues with the row (the ``on_finished`` hook writes the terminal state and the terminal event).
    """
    from app.core.database import AsyncSessionLocal, set_tenant_context
    from app.services import fleet_run_sync as frs
    from app.services.deep_work import engine_view
    from app.services.engine_client import EngineError, get_engine_client

    if job.conversation_id is None:
        return
    try:
        async with AsyncSessionLocal() as bdb:
            await set_tenant_context(bdb, str(tenant_id))
            bound = await fleet_engine.binding(bdb, job)
        if bound is None:
            return
        upstream = await get_engine_client().watch_stream(
            bound[0], tenant_id, bound[1], job.conversation_id)
    except (EngineError, OSError, TimeoutError) as e:
        logger.warning("job %s: engine watch unavailable, polling the row: %s", job.id, e)
        return

    live.progress = job.progress or 0
    update = JobProgressUpdate(job_id=job.id, status=job.status, progress=live.progress,
                               message=f"Progress: {live.progress}%", timestamp=datetime.utcnow())
    yield f"event: progress\ndata: {json.dumps(update.model_dump(), default=str)}\n\n"
    view = None
    buffer = ""
    try:
        async for raw in upstream.aiter_raw():
            buffer += raw.decode("utf-8", "replace").replace("\r\n", "\n")
            frames, buffer = engine_view.parse_frames(buffer)
            for frame in frames:
                if "snapshot" in frame:
                    view = frame["snapshot"]
                elif view is not None and "patch" in frame:
                    view = engine_view.apply_patch(view, frame["patch"])
                else:
                    continue
                steps = frs.build_trace(view, None)
                for step in steps[live.steps:]:
                    yield f"event: step\ndata: {json.dumps(step, default=str)}\n\n"
                live.steps = max(live.steps, len(steps))
                if not frs._active(view):
                    return
    except (EngineError, OSError, TimeoutError, httpx.HTTPError) as e:
        logger.warning("job %s: engine watch dropped, polling the row: %s", job.id, e)
    finally:
        await upstream.aclose()


async def _poll_events(job_id: UUID, tenant_id: UUID, last_progress: int, last_trace_count: int) -> AsyncGenerator[str, None]:
    """Poll the job row and emit progress, step and terminal events."""
    from app.core.database import AsyncSessionLocal, set_tenant_context


    while True:
        # Use a fresh session per poll to avoid RLS context loss after commit
        async with AsyncSessionLocal() as poll_db:
            await set_tenant_context(poll_db, str(tenant_id))
            poll_result = await poll_db.execute(
                select(BackgroundJob).where(BackgroundJob.id == job_id)
            )
            job = poll_result.scalar_one_or_none()
            if not job:
                break

            # Snapshot values from the session-bound object
            job_progress = job.progress
            job_status = job.status
            job_result = job.result
            job_error = job.error
            job_trace = job.execution_trace or []
            job_completed_at = job.completed_at
            job_duration_ms = getattr(job, 'duration_ms', None) or 0

        # Check for progress updates (outside session - using snapshots)
        if job_progress != last_progress:
            last_progress = job_progress
            update = JobProgressUpdate(
                job_id=job_id,
                status=job_status,
                progress=job_progress,
                message=f"Progress: {job_progress}%",
                timestamp=datetime.utcnow(),
            )
            yield f"event: progress\ndata: {json.dumps(update.model_dump(), default=str)}\n\n"

        # Check for new trace steps
        if len(job_trace) > last_trace_count:
            for step in job_trace[last_trace_count:]:
                yield f"event: step\ndata: {json.dumps(step, default=str)}\n\n"
            last_trace_count = len(job_trace)

        # Check for completion
        if job_status == ModelJobStatus.COMPLETED.value:
            completed = JobCompletedEvent(
                job_id=job_id,
                status=job_status,
                result=job_result,
                duration_ms=job_duration_ms,
                execution_trace=job_trace,
                completed_at=job_completed_at or datetime.utcnow(),
            )
            yield f"event: completed\ndata: {json.dumps(completed.model_dump(), default=str)}\n\n"
            break

        if job_status == ModelJobStatus.FAILED.value:
            failed = JobCompletedEvent(
                job_id=job_id,
                status=job_status,
                error=job_error,
                duration_ms=job_duration_ms,
                execution_trace=job_trace,
                completed_at=job_completed_at or datetime.utcnow(),
            )
            yield f"event: failed\ndata: {json.dumps(failed.model_dump(), default=str)}\n\n"
            break

        if job_status == ModelJobStatus.CANCELLED.value:
            cancelled = JobCompletedEvent(
                job_id=job_id,
                status=job_status,
                error=job_error,
                duration_ms=job_duration_ms,
                execution_trace=job_trace,
                completed_at=job_completed_at or datetime.utcnow(),
            )
            yield f"event: cancelled\ndata: {json.dumps(cancelled.model_dump(), default=str)}\n\n"
            break

        # Wait before next poll
        await asyncio.sleep(0.5)


@router.get("/{job_id}/stream")
async def stream_job_progress(
    job_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """
    Stream real-time job progress updates via Server-Sent Events (SSE).

    This endpoint returns an event stream with job progress updates.
    Events:
    - progress: Periodic progress updates
    - step: When a new execution step completes
    - completed: When job finishes successfully
    - failed: When job fails
    - cancelled: When job is cancelled
    """
    user_id = UUID(user.id)

    # Verify job exists and belongs to user
    result = await db.execute(
        select(BackgroundJob).where(
            BackgroundJob.id == job_id,
            BackgroundJob.user_id == user_id,
            BackgroundJob.tenant_id == tenant_id
        )
    )
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found"
        )

    async def event_generator() -> AsyncGenerator[str, None]:
        """Generate SSE events for job progress."""
        last_progress, last_trace_count = -1, 0
        if fleet_engine.is_engine_job(job) and job.status in _LIVE_STATUSES:
            live = _LiveState()
            async for chunk in _engine_live_events(job, tenant_id, live):
                yield chunk
            last_progress, last_trace_count = live.progress, live.steps
        async for chunk in _poll_events(job_id, tenant_id, last_progress, last_trace_count):
            yield chunk

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        }
    )


@router.get("/workflow/{workflow_id}/history", response_model=BackgroundJobListResponse)
async def get_workflow_job_history(
    workflow_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    limit: int = Query(10, ge=1, le=50),
):
    """
    Get recent job history for a specific workflow.
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

    # Get jobs
    query = (
        select(BackgroundJob)
        .where(
            BackgroundJob.user_id == user_id,
            BackgroundJob.workflow_id == workflow_id
        )
        .order_by(desc(BackgroundJob.created_at))
        .limit(limit)
    )

    result = await db.execute(query)
    jobs = result.scalars().all()

    # Get total count
    count_result = await db.execute(
        select(func.count())
        .select_from(BackgroundJob)
        .where(
            BackgroundJob.user_id == user_id,
            BackgroundJob.workflow_id == workflow_id
        )
    )
    total = count_result.scalar() or 0

    return BackgroundJobListResponse(
        jobs=[job_to_response(j) for j in jobs],
        total=total,
        page=1,
        page_size=limit,
    )


@router.get("/agent/{agent_id}/history", response_model=BackgroundJobListResponse)
async def get_agent_job_history(
    agent_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    limit: int = Query(10, ge=1, le=50),
):
    """
    Get recent job history for a specific agent.
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

    # Get jobs
    query = (
        select(BackgroundJob)
        .where(
            BackgroundJob.user_id == user_id,
            BackgroundJob.agent_id == agent_id
        )
        .order_by(desc(BackgroundJob.created_at))
        .limit(limit)
    )

    result = await db.execute(query)
    jobs = result.scalars().all()

    # Get total count
    count_result = await db.execute(
        select(func.count())
        .select_from(BackgroundJob)
        .where(
            BackgroundJob.user_id == user_id,
            BackgroundJob.agent_id == agent_id
        )
    )
    total = count_result.scalar() or 0

    return BackgroundJobListResponse(
        jobs=[job_to_response(j) for j in jobs],
        total=total,
        page=1,
        page_size=limit,
    )
