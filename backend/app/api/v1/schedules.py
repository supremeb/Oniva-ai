"""
Scheduled Workflow API endpoints
Supports cron-based workflow scheduling with timezone support

Part of Workflow Nodes & Features Expansion
"""

import logging
import uuid
from typing import List, Dict, Any, Optional
from datetime import datetime

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field, field_validator
import pytz
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.core.database import get_db
from app.models.workflow import Workflow
from app.models.scheduled_workflow import ScheduledWorkflow, ScheduledWorkflowExecution, ScheduleStatus
from app.auth.dependencies import get_current_user, get_current_tenant_id, get_current_user_id, require_scope
from app.auth.providers.base import AuthUser
from app.services.workflow_scheduler import WorkflowSchedulerService

logger = logging.getLogger(__name__)
router = APIRouter()


# --- Pydantic Models ---

class ScheduleCreate(BaseModel):
    """Request to create a new schedule"""
    workflow_id: uuid.UUID
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    schedule_type: str = Field(default="cron", description="cron, interval, or one_time")

    # Cron expression (for cron type)
    cron_expression: Optional[str] = Field(
        None,
        description="Cron expression (e.g., '0 9 * * 1-5' for weekdays at 9am)"
    )

    # Interval settings (for interval type)
    interval_seconds: Optional[int] = Field(
        None,
        ge=60,
        description="Interval in seconds (min 60)"
    )

    # One-time execution (for one_time type)
    scheduled_time: Optional[datetime] = Field(
        None,
        description="ISO datetime for one-time execution"
    )

    # Timezone
    timezone: str = Field(default="UTC", description="Timezone for schedule interpretation")

    # Input variables
    input_variables: Dict[str, Any] = Field(default_factory=dict)

    # Execution limits
    max_executions: Optional[int] = Field(
        None,
        ge=1,
        description="Maximum number of executions (null = unlimited)"
    )
    max_concurrent: int = Field(default=1, ge=1, le=10)
    timeout_seconds: int = Field(default=3600, ge=60, le=86400)

    # Error handling
    retry_on_failure: bool = False
    max_retries: int = Field(default=3, ge=0, le=10)
    retry_delay_seconds: int = Field(default=60, ge=10, le=3600)

    @field_validator('schedule_type')
    @classmethod
    def validate_schedule_type(cls, v: str) -> str:
        valid_types = ['cron', 'interval', 'one_time']
        if v not in valid_types:
            raise ValueError(f"schedule_type must be one of {valid_types}")
        return v

    @field_validator('timezone')
    @classmethod
    def validate_timezone(cls, v: str) -> str:
        if v not in pytz.all_timezones:
            raise ValueError(f"Invalid timezone: '{v}'. Must be a valid IANA timezone.")
        return v


class ScheduleUpdate(BaseModel):
    """Request to update an existing schedule"""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    schedule_type: Optional[str] = None
    cron_expression: Optional[str] = None
    interval_seconds: Optional[int] = Field(None, ge=60)
    scheduled_time: Optional[datetime] = None
    timezone: Optional[str] = None
    input_variables: Optional[Dict[str, Any]] = None
    max_executions: Optional[int] = Field(None, ge=1)
    max_concurrent: Optional[int] = Field(None, ge=1, le=10)
    timeout_seconds: Optional[int] = Field(None, ge=60, le=86400)
    retry_on_failure: Optional[bool] = None
    max_retries: Optional[int] = Field(None, ge=0, le=10)
    retry_delay_seconds: Optional[int] = Field(None, ge=10, le=3600)

    @field_validator('schedule_type')
    @classmethod
    def validate_schedule_type(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            valid_types = ['cron', 'interval', 'one_time']
            if v not in valid_types:
                raise ValueError(f"schedule_type must be one of {valid_types}")
        return v

    @field_validator('timezone')
    @classmethod
    def validate_timezone(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in pytz.all_timezones:
            raise ValueError(f"Invalid timezone: '{v}'. Must be a valid IANA timezone.")
        return v


class ScheduleResponse(BaseModel):
    """Response for a scheduled workflow"""
    id: uuid.UUID
    workflow_id: uuid.UUID
    name: str
    description: Optional[str]
    schedule_type: str
    cron_expression: Optional[str]
    interval_seconds: Optional[int]
    scheduled_time: Optional[datetime]
    timezone: str
    status: str
    is_enabled: bool
    input_variables: Dict[str, Any]
    max_executions: Optional[int]
    execution_count: int
    max_concurrent: int
    timeout_seconds: int
    retry_on_failure: bool
    max_retries: int
    retry_delay_seconds: int
    last_run_at: Optional[datetime]
    last_run_status: Optional[str]
    last_error: Optional[str]
    next_run_at: Optional[datetime]
    human_readable_schedule: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class PaginatedScheduleResponse(BaseModel):
    """Paginated list of schedules"""
    items: List[ScheduleResponse]
    total: int
    skip: int
    limit: int
    has_more: bool


class ScheduleExecutionResponse(BaseModel):
    """Response for a schedule execution record"""
    id: uuid.UUID
    schedule_id: uuid.UUID
    workflow_execution_id: Optional[uuid.UUID]
    engine_conversation_id: Optional[uuid.UUID] = None  # set when the run started on the engine (plan 003 U19)
    scheduled_for: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    status: str
    result_summary: Dict[str, Any]
    error: Optional[str]
    retry_count: int
    created_at: datetime

    class Config:
        from_attributes = True


class PaginatedExecutionResponse(BaseModel):
    """Paginated list of executions"""
    items: List[ScheduleExecutionResponse]
    total: int
    skip: int
    limit: int
    has_more: bool


class TriggerResponse(BaseModel):
    """Response for manual trigger"""
    message: str
    execution_id: uuid.UUID
    scheduled_for: datetime


# --- Helper Functions ---

def _schedule_to_response(schedule: ScheduledWorkflow) -> ScheduleResponse:
    """Convert ScheduledWorkflow model to response"""
    return ScheduleResponse(
        id=schedule.id,
        workflow_id=schedule.workflow_id,
        name=schedule.name,
        description=schedule.description,
        schedule_type=schedule.schedule_type.value if schedule.schedule_type else "cron",
        cron_expression=schedule.cron_expression,
        interval_seconds=schedule.interval_seconds,
        scheduled_time=schedule.scheduled_time,
        timezone=schedule.timezone or "UTC",
        status=schedule.status.value if schedule.status else "active",
        is_enabled=schedule.is_enabled,
        input_variables=schedule.input_variables or {},
        max_executions=schedule.max_executions,
        execution_count=schedule.execution_count or 0,
        max_concurrent=schedule.max_concurrent or 1,
        timeout_seconds=schedule.timeout_seconds or 3600,
        retry_on_failure=schedule.retry_on_failure or False,
        max_retries=schedule.max_retries or 3,
        retry_delay_seconds=schedule.retry_delay_seconds or 60,
        last_run_at=schedule.last_run_at,
        last_run_status=schedule.last_run_status,
        last_error=schedule.last_error,
        next_run_at=schedule.next_run_at,
        human_readable_schedule=schedule.get_human_readable_schedule(),
        created_at=schedule.created_at,
        updated_at=schedule.updated_at,
    )


def _execution_to_response(execution: ScheduledWorkflowExecution) -> ScheduleExecutionResponse:
    """Convert ScheduledWorkflowExecution model to response"""
    summary = execution.result_summary or {}
    return ScheduleExecutionResponse(
        id=execution.id,
        schedule_id=execution.schedule_id,
        workflow_execution_id=execution.workflow_execution_id,
        engine_conversation_id=summary.get("engine_conversation_id"),
        scheduled_for=execution.scheduled_for,
        started_at=execution.started_at,
        completed_at=execution.completed_at,
        status=execution.status or "pending",
        result_summary=execution.result_summary or {},
        error=execution.error,
        retry_count=execution.retry_count or 0,
        created_at=execution.created_at,
    )


# --- Endpoints ---

@router.post("", response_model=ScheduleResponse, status_code=201,
             dependencies=[Depends(require_scope("workflows:execute"))])
async def create_schedule(
    request: ScheduleCreate,
    db: AsyncSession = Depends(get_db, scope="function"),
    user_id: uuid.UUID = Depends(get_current_user_id),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """
    Create a new scheduled workflow.

    Supports three schedule types:
    - cron: Standard cron expression (e.g., "0 9 * * 1-5" for weekdays at 9am)
    - interval: Execute every N seconds
    - one_time: Execute once at a specific time
    """
    scheduler = WorkflowSchedulerService(db)

    found = await db.execute(select(Workflow.id).where(
        Workflow.id == request.workflow_id, Workflow.tenant_id == tenant_id))
    if found.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Workflow not found")

    try:
        schedule = await scheduler.create_schedule(
            workflow_id=request.workflow_id,
            user_id=user_id,
            tenant_id=tenant_id,
            name=request.name,
            description=request.description,
            schedule_type=request.schedule_type,
            cron_expression=request.cron_expression,
            interval_seconds=request.interval_seconds,
            scheduled_time=request.scheduled_time,
            timezone=request.timezone,
            input_variables=request.input_variables,
            max_executions=request.max_executions,
            max_concurrent=request.max_concurrent,
            timeout_seconds=request.timeout_seconds,
            retry_on_failure=request.retry_on_failure,
            max_retries=request.max_retries,
            retry_delay_seconds=request.retry_delay_seconds,
        )

        return _schedule_to_response(schedule)

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("Failed to create schedule")
        raise HTTPException(status_code=500, detail="Failed to create schedule")


@router.get("", response_model=PaginatedScheduleResponse,
            dependencies=[Depends(require_scope("workflows:read"))])
async def list_schedules(
    workflow_id: Optional[uuid.UUID] = Query(None, description="Filter by workflow ID"),
    status: Optional[str] = Query(None, description="Filter by status: active, paused, disabled, completed, error"),
    is_enabled: Optional[bool] = Query(None, description="Filter by enabled state"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """
    List all scheduled workflows for the current tenant with pagination.
    Supports filtering by workflow_id, status, and enabled state.
    """
    user_id = uuid.UUID(user.id)

    # Build base query
    base_filter = [
        ScheduledWorkflow.tenant_id == tenant_id,
        ScheduledWorkflow.user_id == user_id
    ]

    if workflow_id:
        base_filter.append(ScheduledWorkflow.workflow_id == workflow_id)

    if status:
        try:
            status_enum = ScheduleStatus(status)
            base_filter.append(ScheduledWorkflow.status == status_enum)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status. Must be one of: active, paused, disabled, completed, error"
            )

    if is_enabled is not None:
        base_filter.append(ScheduledWorkflow.is_enabled == is_enabled)

    # Get total count
    count_query = select(func.count()).select_from(ScheduledWorkflow).where(*base_filter)
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Get paginated items
    query = (
        select(ScheduledWorkflow)
        .where(*base_filter)
        .order_by(ScheduledWorkflow.updated_at.desc())
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(query)
    schedules = result.scalars().all()

    return PaginatedScheduleResponse(
        items=[_schedule_to_response(s) for s in schedules],
        total=total,
        skip=skip,
        limit=limit,
        has_more=(skip + limit) < total
    )


@router.get("/{schedule_id}", response_model=ScheduleResponse,
            dependencies=[Depends(require_scope("workflows:read"))])
async def get_schedule(
    schedule_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """
    Get a scheduled workflow by ID.
    RLS ensures only tenant's schedules are accessible.
    """
    user_id = uuid.UUID(user.id)

    query = select(ScheduledWorkflow).where(
        ScheduledWorkflow.id == schedule_id,
        ScheduledWorkflow.user_id == user_id,
        ScheduledWorkflow.tenant_id == tenant_id
    )
    result = await db.execute(query)
    schedule = result.scalars().first()

    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")

    return _schedule_to_response(schedule)


@router.put("/{schedule_id}", response_model=ScheduleResponse,
            dependencies=[Depends(require_scope("workflows:write"))])
async def update_schedule(
    schedule_id: uuid.UUID,
    request: ScheduleUpdate,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """
    Update an existing scheduled workflow.
    Only non-null fields in the request will be updated.
    """
    user_id = uuid.UUID(user.id)
    scheduler = WorkflowSchedulerService(db)

    # Build update dict from non-null fields
    updates = {}
    for field, value in request.model_dump(exclude_unset=True).items():
        if value is not None:
            updates[field] = value

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    try:
        schedule = await scheduler.update_schedule(
            schedule_id=schedule_id,
            user_id=user_id,
            tenant_id=tenant_id,
            **updates
        )

        if not schedule:
            raise HTTPException(status_code=404, detail="Schedule not found")

        return _schedule_to_response(schedule)

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update schedule: {str(e)}")


@router.delete("/{schedule_id}",
               dependencies=[Depends(require_scope("workflows:write"))])
async def delete_schedule(
    schedule_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """Delete a scheduled workflow and its execution history."""
    user_id = uuid.UUID(user.id)
    scheduler = WorkflowSchedulerService(db)

    try:
        success = await scheduler.delete_schedule(
            schedule_id=schedule_id,
            user_id=user_id,
            tenant_id=tenant_id
        )

        if not success:
            raise HTTPException(status_code=404, detail="Schedule not found")

        return {"id": str(schedule_id), "message": "Schedule deleted"}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete schedule: {str(e)}")


@router.post("/{schedule_id}/pause", response_model=ScheduleResponse,
             dependencies=[Depends(require_scope("workflows:write"))])
async def pause_schedule(
    schedule_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """Pause a scheduled workflow. Sets status to 'paused' and disables execution."""
    user_id = uuid.UUID(user.id)
    scheduler = WorkflowSchedulerService(db)

    try:
        schedule = await scheduler.pause_schedule(
            schedule_id=schedule_id,
            user_id=user_id,
            tenant_id=tenant_id
        )

        if not schedule:
            raise HTTPException(status_code=404, detail="Schedule not found")

        return _schedule_to_response(schedule)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to pause schedule: {str(e)}")


@router.post("/{schedule_id}/resume", response_model=ScheduleResponse,
             dependencies=[Depends(require_scope("workflows:execute"))])
async def resume_schedule(
    schedule_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """Resume a paused scheduled workflow. Recalculates next_run_at from now."""
    user_id = uuid.UUID(user.id)
    scheduler = WorkflowSchedulerService(db)

    try:
        schedule = await scheduler.resume_schedule(
            schedule_id=schedule_id,
            user_id=user_id,
            tenant_id=tenant_id
        )

        if not schedule:
            raise HTTPException(status_code=404, detail="Schedule not found")

        return _schedule_to_response(schedule)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to resume schedule: {str(e)}")


@router.post("/{schedule_id}/trigger", response_model=TriggerResponse,
             dependencies=[Depends(require_scope("workflows:execute"))])
async def trigger_schedule(
    schedule_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """
    Manually trigger a scheduled workflow to run immediately.
    This will create an execution record and run the workflow without
    affecting the regular schedule.
    """
    user_id = uuid.UUID(user.id)
    scheduler = WorkflowSchedulerService(db)

    try:
        execution = await scheduler.trigger_now(
            schedule_id=schedule_id,
            user_id=user_id,
            tenant_id=tenant_id
        )

        if not execution:
            raise HTTPException(status_code=404, detail="Schedule not found")

        return TriggerResponse(
            message="Workflow triggered successfully",
            execution_id=execution.id,
            scheduled_for=execution.scheduled_for
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to trigger workflow: {str(e)}")


@router.get("/{schedule_id}/executions", response_model=PaginatedExecutionResponse,
            dependencies=[Depends(require_scope("workflows:read"))])
async def list_schedule_executions(
    schedule_id: uuid.UUID,
    status: Optional[str] = Query(None, description="Filter by status: pending, running, completed, failed, skipped"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """
    List execution history for a scheduled workflow.
    Returns paginated list of executions ordered by scheduled_for (newest first).
    """
    user_id = uuid.UUID(user.id)

    # Verify schedule exists and belongs to user
    schedule_query = select(ScheduledWorkflow).where(
        ScheduledWorkflow.id == schedule_id,
        ScheduledWorkflow.user_id == user_id,
        ScheduledWorkflow.tenant_id == tenant_id
    )
    schedule_result = await db.execute(schedule_query)
    schedule = schedule_result.scalars().first()

    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")

    # Build base filter
    base_filter = [
        ScheduledWorkflowExecution.schedule_id == schedule_id,
        ScheduledWorkflowExecution.tenant_id == tenant_id
    ]

    if status:
        base_filter.append(ScheduledWorkflowExecution.status == status)

    # Get total count
    count_query = select(func.count()).select_from(ScheduledWorkflowExecution).where(*base_filter)
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Get paginated items
    query = (
        select(ScheduledWorkflowExecution)
        .where(*base_filter)
        .order_by(ScheduledWorkflowExecution.scheduled_for.desc())
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(query)
    executions = result.scalars().all()

    return PaginatedExecutionResponse(
        items=[_execution_to_response(e) for e in executions],
        total=total,
        skip=skip,
        limit=limit,
        has_more=(skip + limit) < total
    )


@router.get("/{schedule_id}/executions/{execution_id}", response_model=ScheduleExecutionResponse,
            dependencies=[Depends(require_scope("workflows:read"))])
async def get_schedule_execution(
    schedule_id: uuid.UUID,
    execution_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """
    Get details of a specific schedule execution.
    """
    user_id = uuid.UUID(user.id)

    # Verify schedule exists and belongs to user
    schedule_query = select(ScheduledWorkflow).where(
        ScheduledWorkflow.id == schedule_id,
        ScheduledWorkflow.user_id == user_id,
        ScheduledWorkflow.tenant_id == tenant_id
    )
    schedule_result = await db.execute(schedule_query)
    schedule = schedule_result.scalars().first()

    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")

    # Get execution
    query = select(ScheduledWorkflowExecution).where(
        ScheduledWorkflowExecution.id == execution_id,
        ScheduledWorkflowExecution.schedule_id == schedule_id,
        ScheduledWorkflowExecution.tenant_id == tenant_id
    )
    result = await db.execute(query)
    execution = result.scalars().first()

    if not execution:
        raise HTTPException(status_code=404, detail="Execution not found")

    return _execution_to_response(execution)
