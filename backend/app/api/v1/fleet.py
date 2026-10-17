"""Fleet API — Background agent run history and monitoring.

Provides paginated run history for all background agent executions
within a tenant. Feature-gated by ENABLE_FLEET_DASHBOARD.
"""

import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

import redis.exceptions
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_tenant_id, get_current_user, require_feature
from app.auth.providers.base import AuthUser
from app.core.database import get_db
from app.models.background_job import BackgroundJob, JobType
from app.models.custom_agent import CustomAgent
from app.services import fleet_engine
from app.services.engine_client import EngineError
from app.services.intervention import send_intervention

logger = logging.getLogger(__name__)

router = APIRouter()


# ========================================
# Schemas
# ========================================

class FleetRunItem(BaseModel):
    id: str
    agent_id: Optional[str] = None
    agent_name: Optional[str] = None
    agent_icon: Optional[str] = None
    job_type: str
    status: str
    trigger: str
    triggered_by: Optional[dict] = None
    progress: int
    conversation_id: Optional[str] = None
    input_data: Optional[dict] = None
    result: Optional[dict] = None
    error: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    created_at: str
    duration_ms: Optional[int] = None
    total_tokens: Optional[int] = None


class FleetRunsResponse(BaseModel):
    items: list[FleetRunItem]
    total: int
    page: int
    page_size: int
    has_more: bool


# ========================================
# Endpoints
# ========================================

@router.get(
    "/fleet/runs",
    response_model=FleetRunsResponse,
    summary="List background agent run history",
    dependencies=[Depends(require_feature("ENABLE_FLEET_DASHBOARD"))],
)
async def list_fleet_runs(
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db),
    status: Optional[str] = Query(None, description="Filter by status"),
    agent_id: Optional[UUID] = Query(None, description="Filter by agent"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """Paginated run history for all background agent jobs in the tenant."""
    # Build query — tenant_id scoping is the primary isolation boundary
    query = (
        select(BackgroundJob)
        .where(BackgroundJob.tenant_id == tenant_id)
        .where(BackgroundJob.job_type == JobType.BACKGROUND_AGENT.value)
        .order_by(desc(BackgroundJob.created_at))
    )

    if status:
        query = query.where(BackgroundJob.status == status)
    if agent_id:
        query = query.where(BackgroundJob.agent_id == agent_id)

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    # Paginate
    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)
    result = await db.execute(query)
    jobs = result.scalars().all()

    # Load agent names
    agent_ids = {j.agent_id for j in jobs if j.agent_id}
    agent_map = {}
    if agent_ids:
        agents_result = await db.execute(
            select(CustomAgent).where(
                CustomAgent.id.in_(agent_ids),
                CustomAgent.tenant_id == tenant_id,
            )
        )
        for agent in agents_result.scalars().all():
            agent_map[str(agent.id)] = {"name": agent.name, "icon": agent.icon}

    items = []
    for job in jobs:
        agent_info = agent_map.get(str(job.agent_id), {})
        engine_job = fleet_engine.is_engine_job(job)
        items.append(FleetRunItem(
            id=str(job.id),
            agent_id=str(job.agent_id) if job.agent_id else None,
            agent_name=agent_info.get("name"),
            agent_icon=agent_info.get("icon"),
            job_type=job.job_type,
            status=job.status,
            trigger=job.trigger,
            triggered_by=job.triggered_by,
            progress=job.progress,
            conversation_id=str(job.conversation_id) if engine_job and job.conversation_id else None,
            input_data=fleet_engine.public_input_data(job.input_data) if engine_job else job.input_data,
            result=job.result,
            error=job.error,
            started_at=job.started_at.isoformat() if job.started_at else None,
            completed_at=job.completed_at.isoformat() if job.completed_at else None,
            created_at=job.created_at.isoformat() if job.created_at else "",
            duration_ms=job.duration_ms,
            total_tokens=job.total_tokens,
        ))

    return FleetRunsResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        has_more=(offset + page_size) < total,
    )


# ========================================
# Live Intervention
# ========================================

class InterventionRequest(BaseModel):
    message: str


class InterventionResponse(BaseModel):
    message: str
    sender: str
    timestamp: str


@router.post(
    "/fleet/runs/{job_id}/intervene",
    response_model=InterventionResponse,
    summary="Inject a message into a running background agent",
    dependencies=[Depends(require_feature("ENABLE_FLEET_DASHBOARD"))],
)
async def intervene_in_agent(
    job_id: UUID,
    body: InterventionRequest,
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    """Send an intervention message to a running background agent.

    The agent will receive this message before its next iteration
    and incorporate it as an additional instruction.
    """
    # Verify job exists, is running, and belongs to this tenant
    result = await db.execute(
        select(BackgroundJob).where(
            BackgroundJob.id == job_id,
            BackgroundJob.tenant_id == tenant_id,
            BackgroundJob.status == "running",
        )
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(404, "Job not found or not running")

    if fleet_engine.is_engine_job(job):
        try:
            sent = await fleet_engine.steer_run(db, job, body.message)
        except (EngineError, OSError, TimeoutError):
            raise HTTPException(503, "Intervention service temporarily unavailable")
        if not sent:
            raise HTTPException(404, "Job not found or not running")
        return InterventionResponse(
            message=body.message, sender=user.email,
            timestamp=datetime.now(timezone.utc).isoformat())

    try:
        intervention = await send_intervention(
            job_id=str(job_id),
            message=body.message,
            sender=user.email,
        )
    except redis.exceptions.RedisError:
        raise HTTPException(503, "Intervention service temporarily unavailable")

    return InterventionResponse(**intervention)
