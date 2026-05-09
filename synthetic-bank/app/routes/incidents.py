"""Incident routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Incident
from app.schemas import IncidentSchema, PaginatedResponse

router = APIRouter(tags=["incidents"])


@router.get("/", response_model=PaginatedResponse[IncidentSchema])
async def list_incidents(
    db: Annotated[AsyncSession, Depends(get_db)],
    status: str | None = Query(None, description="Filter by status: open, investigating, resolved, escalated"),
    severity: str | None = Query(None, description="Filter by severity: low, medium, high, critical"),
    customer_id: UUID | None = Query(None, description="Filter by customer ID (nullable on incidents)"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> PaginatedResponse[IncidentSchema]:
    query = select(Incident)
    count_query = select(func.count()).select_from(Incident)

    if status:
        query = query.where(Incident.status == status)
        count_query = count_query.where(Incident.status == status)
    if severity:
        query = query.where(Incident.severity == severity)
        count_query = count_query.where(Incident.severity == severity)
    if customer_id:
        query = query.where(Incident.customer_id == customer_id)
        count_query = count_query.where(Incident.customer_id == customer_id)

    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    query = query.order_by(Incident.reported_date.desc()).offset(offset).limit(limit)
    result = await db.execute(query)
    incidents = result.scalars().all()

    return PaginatedResponse(
        items=[IncidentSchema.model_validate(i) for i in incidents],
        total=total,
        limit=limit,
        offset=offset,
    )
