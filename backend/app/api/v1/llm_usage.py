"""LLM Usage tracking API.

Phase 2: LLM Reliability - Usage Analytics

Endpoints for retrieving LLM usage statistics with SQL aggregations.
No pre-computed daily tables - uses SQL GROUP BY for flexibility.
"""

from datetime import date, timedelta
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.auth.dependencies import get_current_user, get_current_tenant_id, require_role
from app.auth.providers.base import AuthUser
from app.models.llm_usage import LLMUsageRecord

router = APIRouter()


# ============================================================================
# Schemas
# ============================================================================

class UsageSummary(BaseModel):
    """Aggregate usage statistics."""
    total_requests: int
    total_tokens: int
    total_cost_usd: float
    avg_latency_ms: float


class DailyUsage(BaseModel):
    """Daily aggregated usage."""
    date: str
    requests: int
    tokens: int
    cost_usd: float


class ProviderUsage(BaseModel):
    """Per-provider aggregated usage."""
    provider_name: str
    requests: int
    tokens: int
    cost_usd: float


class ModelUsage(BaseModel):
    """Per-model aggregated usage."""
    model: str
    provider_name: str
    requests: int
    tokens: int
    cost_usd: float


class UsageResponse(BaseModel):
    """Complete usage report with breakdowns."""
    period_start: str
    period_end: str
    summary: UsageSummary
    by_date: List[DailyUsage]
    by_provider: List[ProviderUsage]
    by_model: List[ModelUsage]


# ============================================================================
# Endpoints
# ============================================================================

@router.get("/llm-usage/summary", response_model=UsageResponse, dependencies=[Depends(require_role("owner", "admin"))])
async def get_usage_summary(
    days: int = Query(30, ge=1, le=90, description="Number of days to include"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """
    Get LLM usage summary with SQL aggregations.

    Returns:
    - Overall summary (total requests, tokens, cost, avg latency)
    - Daily breakdown
    - Per-provider breakdown
    - Per-model breakdown
    """
    end_date = date.today()
    start_date = end_date - timedelta(days=days)

    # Summary aggregation
    summary_stmt = select(
        func.count(LLMUsageRecord.id).label("total_requests"),
        func.coalesce(
            func.sum(LLMUsageRecord.input_tokens + LLMUsageRecord.output_tokens),
            0
        ).label("total_tokens"),
        func.coalesce(func.sum(LLMUsageRecord.cost_usd), 0).label("total_cost"),
        func.coalesce(func.avg(LLMUsageRecord.latency_ms), 0).label("avg_latency"),
    ).where(
        LLMUsageRecord.tenant_id == tenant_id,
        func.date(LLMUsageRecord.created_at) >= start_date,
    )

    result = await db.execute(summary_stmt)
    summary_row = result.one()

    # Daily aggregation
    daily_stmt = select(
        func.date(LLMUsageRecord.created_at).label("date"),
        func.count(LLMUsageRecord.id).label("requests"),
        func.coalesce(
            func.sum(LLMUsageRecord.input_tokens + LLMUsageRecord.output_tokens),
            0
        ).label("tokens"),
        func.coalesce(func.sum(LLMUsageRecord.cost_usd), 0).label("cost"),
    ).where(
        LLMUsageRecord.tenant_id == tenant_id,
        func.date(LLMUsageRecord.created_at) >= start_date,
    ).group_by(
        func.date(LLMUsageRecord.created_at)
    ).order_by(
        func.date(LLMUsageRecord.created_at)
    )

    daily_result = await db.execute(daily_stmt)
    daily_rows = daily_result.all()

    # Provider aggregation
    provider_stmt = select(
        LLMUsageRecord.provider_name,
        func.count(LLMUsageRecord.id).label("requests"),
        func.coalesce(
            func.sum(LLMUsageRecord.input_tokens + LLMUsageRecord.output_tokens),
            0
        ).label("tokens"),
        func.coalesce(func.sum(LLMUsageRecord.cost_usd), 0).label("cost"),
    ).where(
        LLMUsageRecord.tenant_id == tenant_id,
        func.date(LLMUsageRecord.created_at) >= start_date,
    ).group_by(
        LLMUsageRecord.provider_name
    ).order_by(
        func.sum(LLMUsageRecord.cost_usd).desc()
    )

    provider_result = await db.execute(provider_stmt)
    provider_rows = provider_result.all()

    # Model aggregation
    model_stmt = select(
        LLMUsageRecord.model,
        LLMUsageRecord.provider_name,
        func.count(LLMUsageRecord.id).label("requests"),
        func.coalesce(
            func.sum(LLMUsageRecord.input_tokens + LLMUsageRecord.output_tokens),
            0
        ).label("tokens"),
        func.coalesce(func.sum(LLMUsageRecord.cost_usd), 0).label("cost"),
    ).where(
        LLMUsageRecord.tenant_id == tenant_id,
        func.date(LLMUsageRecord.created_at) >= start_date,
    ).group_by(
        LLMUsageRecord.model,
        LLMUsageRecord.provider_name,
    ).order_by(
        func.sum(LLMUsageRecord.cost_usd).desc()
    ).limit(20)  # Top 20 models

    model_result = await db.execute(model_stmt)
    model_rows = model_result.all()

    return UsageResponse(
        period_start=start_date.isoformat(),
        period_end=end_date.isoformat(),
        summary=UsageSummary(
            total_requests=summary_row.total_requests or 0,
            total_tokens=int(summary_row.total_tokens or 0),
            total_cost_usd=float(summary_row.total_cost or 0),
            avg_latency_ms=float(summary_row.avg_latency or 0),
        ),
        by_date=[
            DailyUsage(
                date=row.date.isoformat() if hasattr(row.date, 'isoformat') else str(row.date),
                requests=row.requests,
                tokens=int(row.tokens or 0),
                cost_usd=float(row.cost or 0),
            )
            for row in daily_rows
        ],
        by_provider=[
            ProviderUsage(
                provider_name=row.provider_name,
                requests=row.requests,
                tokens=int(row.tokens or 0),
                cost_usd=float(row.cost or 0),
            )
            for row in provider_rows
        ],
        by_model=[
            ModelUsage(
                model=row.model,
                provider_name=row.provider_name,
                requests=row.requests,
                tokens=int(row.tokens or 0),
                cost_usd=float(row.cost or 0),
            )
            for row in model_rows
        ],
    )


@router.get("/llm-usage/workflow/{workflow_id}", response_model=UsageSummary)
async def get_workflow_usage(
    workflow_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """
    Get LLM usage for a specific workflow.

    Useful for cost attribution per workflow.
    """
    stmt = select(
        func.count(LLMUsageRecord.id).label("total_requests"),
        func.coalesce(
            func.sum(LLMUsageRecord.input_tokens + LLMUsageRecord.output_tokens),
            0
        ).label("total_tokens"),
        func.coalesce(func.sum(LLMUsageRecord.cost_usd), 0).label("total_cost"),
        func.coalesce(func.avg(LLMUsageRecord.latency_ms), 0).label("avg_latency"),
    ).where(
        LLMUsageRecord.tenant_id == tenant_id,
        LLMUsageRecord.workflow_id == workflow_id,
    )

    result = await db.execute(stmt)
    row = result.one()

    return UsageSummary(
        total_requests=row.total_requests or 0,
        total_tokens=int(row.total_tokens or 0),
        total_cost_usd=float(row.total_cost or 0),
        avg_latency_ms=float(row.avg_latency or 0),
    )
