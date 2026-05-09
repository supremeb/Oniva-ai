"""Compliance alert routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import ComplianceAlert
from app.schemas import ComplianceAlertSchema, PaginatedResponse

router = APIRouter(tags=["compliance"])


@router.get("/", response_model=PaginatedResponse[ComplianceAlertSchema])
async def list_compliance_alerts(
    db: Annotated[AsyncSession, Depends(get_db)],
    severity: str | None = Query(None, description="Filter by severity: low, medium, high, critical"),
    status: str | None = Query(None, description="Filter by status: open, investigating, resolved, dismissed"),
    customer_id: UUID | None = Query(None, description="Filter by customer ID"),
    alert_type: str | None = Query(
        None,
        description="Filter by type: suspicious_activity, sanctions_match, pep_match, unusual_transaction, expired_review",
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> PaginatedResponse[ComplianceAlertSchema]:
    query = select(ComplianceAlert)
    count_query = select(func.count()).select_from(ComplianceAlert)

    if severity:
        query = query.where(ComplianceAlert.severity == severity)
        count_query = count_query.where(ComplianceAlert.severity == severity)
    if status:
        query = query.where(ComplianceAlert.status == status)
        count_query = count_query.where(ComplianceAlert.status == status)
    if customer_id:
        query = query.where(ComplianceAlert.customer_id == customer_id)
        count_query = count_query.where(ComplianceAlert.customer_id == customer_id)
    if alert_type:
        query = query.where(ComplianceAlert.alert_type == alert_type)
        count_query = count_query.where(ComplianceAlert.alert_type == alert_type)

    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    query = query.order_by(ComplianceAlert.triggered_date.desc()).offset(offset).limit(limit)
    result = await db.execute(query)
    alerts = result.scalars().all()

    return PaginatedResponse(
        items=[ComplianceAlertSchema.model_validate(a) for a in alerts],
        total=total,
        limit=limit,
        offset=offset,
    )
