"""Analytics API endpoints.

Enterprise feature: Cost attribution and usage analytics for billing
and optimization.

Provides:
- Cost trends over time
- Cost breakdown by workflow, user, model
- CSV export for billing integration

Uses materialized view `usage_costs_hourly` for performance.
"""
import logging
import uuid
import csv
import io
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, func, text, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.auth.dependencies import get_current_user, get_current_tenant_id
from app.auth.providers.base import AuthUser
from app.models.llm_usage import LLMUsageRecord

logger = logging.getLogger(__name__)

router = APIRouter()


# --- Schemas ---

class CacheMetricsResponse(BaseModel):
    """Cache hit rate metrics for LLM provider caching."""
    start_date: datetime
    end_date: datetime
    total_requests: int = Field(ge=0)
    cached_requests: int = Field(ge=0, description="Requests where cache_read_tokens > 0")
    hit_rate: float = Field(ge=0.0, le=1.0, description="Token-weighted: sum(cache_read_tokens) / sum(input_tokens)")
    total_cache_read_tokens: int = Field(ge=0)
    total_cache_creation_tokens: int = Field(ge=0)


class CostDataPoint(BaseModel):
    """Single data point in cost time series."""
    period: datetime
    total_cost_usd: float
    total_tokens: int
    api_calls: int
    avg_latency_ms: Optional[float] = None


class WorkflowCostBreakdown(BaseModel):
    """Cost breakdown for a single workflow."""
    workflow_id: Optional[uuid.UUID]
    workflow_name: Optional[str]
    total_cost_usd: float
    total_tokens: int
    execution_count: int
    avg_cost_per_execution: float


class UserCostBreakdown(BaseModel):
    """Cost breakdown for a single user."""
    user_id: uuid.UUID
    user_name: Optional[str]
    user_email: Optional[str]
    total_cost_usd: float
    total_tokens: int
    workflow_count: int


class ModelCostBreakdown(BaseModel):
    """Cost breakdown for a single model."""
    provider_name: str
    model: str
    total_cost_usd: float
    total_tokens: int
    api_calls: int
    avg_cost_per_call: float


class CostSummaryResponse(BaseModel):
    """Overall cost summary with time series."""
    start_date: datetime
    end_date: datetime
    total_cost_usd: float
    total_tokens: int
    total_api_calls: int
    avg_cost_per_day: float
    trends: List[CostDataPoint]


# --- Endpoints ---

@router.get("/cache-metrics", response_model=CacheMetricsResponse)
async def get_cache_metrics(
    start_date: datetime = Query(..., description="Start date (ISO format)"),
    end_date: datetime = Query(..., description="End date (ISO format)"),
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
) -> CacheMetricsResponse:
    """Get cache hit rate metrics for LLM provider caching.

    Queries raw llm_usage_records table with date range filter.
    Hit rate is token-weighted: sum(cache_read_tokens) / sum(input_tokens).
    """
    query = select(
        func.count().label('total_requests'),
        func.count().filter(LLMUsageRecord.cache_read_tokens > 0).label('cached_requests'),
        func.coalesce(func.sum(LLMUsageRecord.cache_read_tokens), 0).label('total_cache_read'),
        func.coalesce(func.sum(LLMUsageRecord.cache_creation_tokens), 0).label('total_cache_creation'),
        func.coalesce(func.sum(LLMUsageRecord.input_tokens), 0).label('total_input'),
    ).where(
        and_(
            LLMUsageRecord.tenant_id == tenant_id,
            LLMUsageRecord.created_at >= start_date,
            LLMUsageRecord.created_at <= end_date,
        )
    )

    result = await db.execute(query)
    row = result.one()

    total_input = int(row.total_input)
    total_cache_read = int(row.total_cache_read)
    # Providers disagree on whether input_tokens includes cached tokens (Anthropic excludes them),
    # so the raw ratio can exceed 1 and fail the response model (500). Clamp to [0, 1].
    hit_rate = min(1.0, total_cache_read / total_input) if total_input > 0 else 0.0

    return CacheMetricsResponse(
        start_date=start_date,
        end_date=end_date,
        total_requests=int(row.total_requests),
        cached_requests=int(row.cached_requests),
        hit_rate=hit_rate,
        total_cache_read_tokens=total_cache_read,
        total_cache_creation_tokens=int(row.total_cache_creation),
    )


@router.get("/cost-summary", response_model=CostSummaryResponse)
async def get_cost_summary(
    start_date: datetime = Query(..., description="Start date (ISO format)"),
    end_date: datetime = Query(..., description="End date (ISO format)"),
    group_by: str = Query("day", description="Aggregation period: hour, day, week, month"),
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
) -> CostSummaryResponse:
    """Get cost summary with time series trends.

    Aggregates costs from materialized view for performance.
    """
    # Query materialized view
    query = select(
        func.date_trunc(group_by, text("hour_bucket")).label('period'),
        func.sum(text("total_cost_usd")).label('total_cost'),
        func.sum(text("total_tokens")).label('total_tokens'),
        func.sum(text("api_call_count")).label('api_calls'),
        func.avg(text("avg_latency_ms")).label('avg_latency')
    ).select_from(
        text("usage_costs_hourly")
    ).where(
        and_(
            text("tenant_id = :tenant_id"),
            text("hour_bucket >= :start_date"),
            text("hour_bucket <= :end_date")
        )
    ).group_by('period').order_by('period')

    result = await db.execute(
        query,
        {"tenant_id": str(tenant_id), "start_date": start_date, "end_date": end_date}
    )

    rows = result.fetchall()

    trends = [
        CostDataPoint(
            period=row.period,
            total_cost_usd=float(row.total_cost or 0),
            total_tokens=int(row.total_tokens or 0),
            api_calls=int(row.api_calls or 0),
            avg_latency_ms=float(row.avg_latency) if row.avg_latency else None
        )
        for row in rows
    ]

    # Calculate totals
    total_cost = sum(t.total_cost_usd for t in trends)
    total_tokens = sum(t.total_tokens for t in trends)
    total_calls = sum(t.api_calls for t in trends)

    days = (end_date - start_date).days or 1
    avg_per_day = total_cost / days if days > 0 else total_cost

    return CostSummaryResponse(
        start_date=start_date,
        end_date=end_date,
        total_cost_usd=total_cost,
        total_tokens=total_tokens,
        total_api_calls=total_calls,
        avg_cost_per_day=avg_per_day,
        trends=trends
    )


@router.get("/cost-by-workflow", response_model=List[WorkflowCostBreakdown])
async def get_cost_by_workflow(
    start_date: datetime = Query(...),
    end_date: datetime = Query(...),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
) -> List[WorkflowCostBreakdown]:
    """Get top workflows by cost."""
    # Query with workflow join for names
    query = text("""
        SELECT
            w.id as workflow_id,
            w.name as workflow_name,
            SUM(uc.total_cost_usd) as total_cost,
            SUM(uc.total_tokens) as total_tokens,
            SUM(uc.api_call_count) as execution_count
        FROM usage_costs_hourly uc
        LEFT JOIN workflows w ON uc.workflow_id = w.id
        WHERE uc.tenant_id = :tenant_id
          AND uc.hour_bucket >= :start_date
          AND uc.hour_bucket <= :end_date
          AND uc.workflow_id IS NOT NULL
        GROUP BY w.id, w.name
        ORDER BY total_cost DESC
        LIMIT :limit
    """)

    result = await db.execute(
        query,
        {"tenant_id": str(tenant_id), "start_date": start_date, "end_date": end_date, "limit": limit}
    )

    rows = result.fetchall()

    return [
        WorkflowCostBreakdown(
            workflow_id=row.workflow_id,
            workflow_name=row.workflow_name,
            total_cost_usd=float(row.total_cost or 0),
            total_tokens=int(row.total_tokens or 0),
            execution_count=int(row.execution_count or 0),
            avg_cost_per_execution=float(row.total_cost or 0) / max(int(row.execution_count or 1), 1)
        )
        for row in rows
    ]


@router.get("/cost-by-user", response_model=List[UserCostBreakdown])
async def get_cost_by_user(
    start_date: datetime = Query(...),
    end_date: datetime = Query(...),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
) -> List[UserCostBreakdown]:
    """Get cost breakdown by user."""
    query = text("""
        SELECT
            u.id as user_id,
            u.name as user_name,
            u.email as user_email,
            SUM(uc.total_cost_usd) as total_cost,
            SUM(uc.total_tokens) as total_tokens,
            COUNT(DISTINCT uc.workflow_id) as workflow_count
        FROM usage_costs_hourly uc
        LEFT JOIN users u ON uc.user_id = u.id
        WHERE uc.tenant_id = :tenant_id
          AND uc.hour_bucket >= :start_date
          AND uc.hour_bucket <= :end_date
        GROUP BY u.id, u.name, u.email
        ORDER BY total_cost DESC
        LIMIT :limit
    """)

    result = await db.execute(
        query,
        {"tenant_id": str(tenant_id), "start_date": start_date, "end_date": end_date, "limit": limit}
    )

    rows = result.fetchall()

    return [
        UserCostBreakdown(
            user_id=row.user_id,
            user_name=row.user_name,
            user_email=row.user_email,
            total_cost_usd=float(row.total_cost or 0),
            total_tokens=int(row.total_tokens or 0),
            workflow_count=int(row.workflow_count or 0)
        )
        for row in rows
    ]


@router.get("/cost-by-model", response_model=List[ModelCostBreakdown])
async def get_cost_by_model(
    start_date: datetime = Query(...),
    end_date: datetime = Query(...),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
) -> List[ModelCostBreakdown]:
    """Get cost breakdown by provider and model."""
    query = text("""
        SELECT
            provider_name,
            model,
            SUM(total_cost_usd) as total_cost,
            SUM(total_tokens) as total_tokens,
            SUM(api_call_count) as api_calls
        FROM usage_costs_hourly
        WHERE tenant_id = :tenant_id
          AND hour_bucket >= :start_date
          AND hour_bucket <= :end_date
        GROUP BY provider_name, model
        ORDER BY total_cost DESC
        LIMIT :limit
    """)

    result = await db.execute(
        query,
        {"tenant_id": str(tenant_id), "start_date": start_date, "end_date": end_date, "limit": limit}
    )

    rows = result.fetchall()

    return [
        ModelCostBreakdown(
            provider_name=row.provider_name,
            model=row.model,
            total_cost_usd=float(row.total_cost or 0),
            total_tokens=int(row.total_tokens or 0),
            api_calls=int(row.api_calls or 0),
            avg_cost_per_call=float(row.total_cost or 0) / max(int(row.api_calls or 1), 1)
        )
        for row in rows
    ]


@router.get("/cost-export")
async def export_cost_data(
    start_date: datetime = Query(...),
    end_date: datetime = Query(...),
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """Export cost data as CSV for billing integration.

    Columns: date, tenant_id, user_email, workflow_name, provider,
    model, input_tokens, output_tokens, total_tokens, cost_usd
    """
    # Only admins can export costs
    if user.role not in ["admin", "owner"]:
        raise HTTPException(403, "Only admins can export cost data")

    # Query raw LLM usage records (not aggregated)
    query = text("""
        SELECT
            DATE(lur.created_at) as date,
            lur.tenant_id,
            u.email as user_email,
            u.name as user_name,
            w.name as workflow_name,
            lur.provider_name,
            lur.model,
            lur.input_tokens,
            lur.output_tokens,
            lur.input_tokens + lur.output_tokens as total_tokens,
            lur.cost_usd
        FROM llm_usage_records lur
        LEFT JOIN users u ON lur.user_id = u.id
        LEFT JOIN workflows w ON lur.workflow_id = w.id
        WHERE lur.tenant_id = :tenant_id
          AND lur.created_at >= :start_date
          AND lur.created_at <= :end_date
        ORDER BY lur.created_at
    """)

    result = await db.execute(
        query,
        {"tenant_id": str(tenant_id), "start_date": start_date, "end_date": end_date}
    )

    # Generate CSV
    output = io.StringIO()
    writer = csv.writer(output)

    # Write header
    writer.writerow([
        "date", "tenant_id", "user_email", "user_name", "workflow_name",
        "provider", "model", "input_tokens", "output_tokens", "total_tokens", "cost_usd"
    ])

    # Write rows
    for row in result:
        writer.writerow([
            row.date,
            row.tenant_id,
            row.user_email or "",
            row.user_name or "",
            row.workflow_name or "",
            row.provider_name,
            row.model,
            row.input_tokens,
            row.output_tokens,
            row.total_tokens,
            f"{row.cost_usd:.6f}"  # 6 decimal places for precision
        ])

    output.seek(0)

    # Return as CSV download
    filename = f"cost_export_{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}.csv"

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )
