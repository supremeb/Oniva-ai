"""Observability API endpoints.

Provides unified observability data for the dashboard:
- Dashboard metrics (latency, errors, token usage) from in-memory ObservabilityService
- Cross-workflow trace search from BackgroundJob table
- Bulk agent health (circuit breaker + accuracy) in one call
"""
import uuid
import logging
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.auth.dependencies import get_current_user, get_current_tenant_id
from app.auth.providers.base import AuthUser
from app.models.background_job import BackgroundJob
from app.models.custom_agent import CustomAgent
from app.models.agent_accuracy_history import AgentAccuracyHistory
from app.models.llm_usage import LLMUsageRecord
from app.services.observability import get_observability_service
from app.services.agent_circuit_breaker import AgentCircuitBreaker
from app.services.redis_ha import get_redis_service

logger = logging.getLogger(__name__)

router = APIRouter()


# --- Schemas ---

class LatencyMetrics(BaseModel):
    p50: float
    p95: float
    p99: float
    count: int
    mean: float


class ErrorRateMetrics(BaseModel):
    total_calls: int
    error_count: int
    error_rate: float
    success_rate: float


class TokenUsageByGroup(BaseModel):
    group: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    calls: int
    cost_usd: float
    avg_latency_ms: float


class ObservabilityDashboardResponse(BaseModel):
    latency: LatencyMetrics
    error_rate: ErrorRateMetrics
    token_usage_by_model: list[TokenUsageByGroup]
    window_minutes: int
    generated_at: datetime


class ExecutionTraceStep(BaseModel):
    model_config = {"extra": "allow"}
    # Workflow orchestrator fields
    step_id: str | None = None
    node_id: str | None = None
    node_type: str | None = None
    node_name: str | None = None
    status: str | None = None
    input: dict | None = None
    output: dict | None = None
    error: str | None = None
    reasoning: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    duration_ms: float | None = None
    token_usage: dict | None = None
    # Agent executor fields
    step: int | None = None
    type: str | None = None  # "tool_call" | "text_response"
    tool_name: str | None = None
    tool_args: dict | None = None
    tool_result_preview: str | None = None
    content_preview: str | None = None
    timestamp: str | None = None


class TraceSearchResult(BaseModel):
    run_id: uuid.UUID
    workflow_id: uuid.UUID | None
    workflow_name: str | None
    agent_name: str | None
    source: str  # "chat", "workflow", "background_agent", "deep_work"
    status: str
    created_at: datetime
    completed_at: datetime | None
    duration_ms: float | None
    total_steps: int
    execution_trace: list[ExecutionTraceStep]
    error: str | None
    input_data: dict | None
    result: dict | None


class LLMCallTrace(BaseModel):
    id: uuid.UUID
    model: str
    provider: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    status: str
    agent_id: str | None
    workflow_id: str | None
    created_at: datetime


class TraceSearchResponse(BaseModel):
    traces: list[TraceSearchResult]
    llm_calls: list[LLMCallTrace]
    total: int


class AgentHealthSummary(BaseModel):
    agent_id: str
    agent_name: str
    agent_type: str
    circuit_breaker_status: Literal["healthy", "degraded", "unknown"]
    accuracy: float | None
    has_drift: bool | None
    last_evaluation_at: datetime | None


class AllAgentsHealthResponse(BaseModel):
    agents: list[AgentHealthSummary]
    total: int


class CacheMetricsResponse(BaseModel):
    total_calls: int
    cache_hit_calls: int
    cache_hit_ratio: float
    total_cache_read_tokens: int
    total_cache_creation_tokens: int
    window_minutes: int
    generated_at: datetime


class ToolMetricsGroup(BaseModel):
    group: str
    calls: int
    error_count: int
    error_rate: float
    latency_p50: float
    latency_p95: float
    latency_p99: float
    latency_mean: float


class ToolMetricsResponse(BaseModel):
    metrics: list[ToolMetricsGroup]
    window_minutes: int
    group_by: str
    generated_at: datetime


# --- Endpoints ---

@router.get("/dashboard-metrics", response_model=ObservabilityDashboardResponse)
async def get_dashboard_metrics(
    window_minutes: int = Query(60, ge=1, le=10080),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
) -> ObservabilityDashboardResponse:
    """Get observability dashboard metrics (latency, errors, token usage).

    Queries the llm_usage_records table for persistent, restart-safe metrics.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
    base_filter = and_(
        LLMUsageRecord.tenant_id == tenant_id,
        LLMUsageRecord.created_at >= cutoff,
    )

    # --- Latency + error rate in one query ---
    stats_q = select(
        func.count().label("total"),
        func.count().filter(LLMUsageRecord.status == "failed").label("errors"),
        func.avg(LLMUsageRecord.latency_ms).label("mean"),
        func.percentile_cont(0.5).within_group(LLMUsageRecord.latency_ms).label("p50"),
        func.percentile_cont(0.95).within_group(LLMUsageRecord.latency_ms).label("p95"),
        func.percentile_cont(0.99).within_group(LLMUsageRecord.latency_ms).label("p99"),
    ).where(base_filter)
    row = (await db.execute(stats_q)).one()

    total = row.total or 0
    errors = row.errors or 0

    latency = LatencyMetrics(
        p50=float(row.p50 or 0),
        p95=float(row.p95 or 0),
        p99=float(row.p99 or 0),
        mean=float(row.mean or 0),
        count=total,
    )
    error_rate = ErrorRateMetrics(
        total_calls=total,
        error_count=errors,
        error_rate=errors / total if total > 0 else 0,
        success_rate=(total - errors) / total if total > 0 else 1.0,
    )

    # --- Token usage grouped by model ---
    token_q = (
        select(
            LLMUsageRecord.model,
            func.count().label("calls"),
            func.sum(LLMUsageRecord.input_tokens).label("input_tokens"),
            func.sum(LLMUsageRecord.output_tokens).label("output_tokens"),
            func.sum(LLMUsageRecord.cost_usd).label("cost_usd"),
            func.avg(LLMUsageRecord.latency_ms).label("avg_latency_ms"),
        )
        .where(base_filter)
        .group_by(LLMUsageRecord.model)
        .order_by(func.count().desc())
    )
    token_rows = (await db.execute(token_q)).all()

    token_by_model = [
        TokenUsageByGroup(
            group=_clean_model_name(r.model or "unknown"),
            calls=r.calls,
            input_tokens=r.input_tokens or 0,
            output_tokens=r.output_tokens or 0,
            total_tokens=(r.input_tokens or 0) + (r.output_tokens or 0),
            cost_usd=float(r.cost_usd or 0),
            avg_latency_ms=float(r.avg_latency_ms or 0),
        )
        for r in token_rows
    ]

    return ObservabilityDashboardResponse(
        latency=latency,
        error_rate=error_rate,
        token_usage_by_model=token_by_model,
        window_minutes=window_minutes,
        generated_at=datetime.now(timezone.utc),
    )


@router.get("/cache-metrics", response_model=CacheMetricsResponse)
async def get_cache_metrics(
    window_minutes: int = Query(60, ge=1, le=10080),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
) -> CacheMetricsResponse:
    """Get KV-cache hit/miss metrics from LLM calls.

    Tracks Anthropic cache_creation/cache_read tokens and OpenAI
    cached_tokens. Returns hit ratio and total token counts.
    """
    service = get_observability_service()
    raw = service.get_cache_metrics(window_minutes, tenant_id=str(tenant_id))
    return CacheMetricsResponse(
        **raw,
        window_minutes=window_minutes,
        generated_at=datetime.now(timezone.utc),
    )


@router.get("/tool-metrics", response_model=ToolMetricsResponse)
async def get_tool_metrics(
    window_minutes: int = Query(60, ge=1, le=10080),
    group_by: Literal["tool_name", "agent_type", "tool_category"] = Query("tool_name"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
) -> ToolMetricsResponse:
    """Get tool execution metrics grouped by tool_name, agent_type, or tool_category.

    Data comes from the in-memory ObservabilityService. Volatile — lost on restart.
    Returns per-group latency percentiles, error rates, and call counts.
    """
    service = get_observability_service()
    raw = service.get_tool_metrics(window_minutes, group_by=group_by, tenant_id=str(tenant_id))

    metrics = [
        ToolMetricsGroup(group=k, **v)
        for k, v in raw.items()
    ]

    return ToolMetricsResponse(
        metrics=metrics,
        window_minutes=window_minutes,
        group_by=group_by,
        generated_at=datetime.now(timezone.utc),
    )


def _clean_model_name(model: str) -> str:
    """Strip provider prefixes from provider-prefixed model names for display."""
    for prefix in ("openrouter/openai/", "openrouter/anthropic/", "openrouter/google/",
                   "openrouter/deepseek/", "openrouter/qwen/", "openrouter/meta-llama/",
                   "openrouter/", "anthropic/", "openai/"):
        if model.startswith(prefix):
            return model[len(prefix):]
    return model


@router.get("/traces", response_model=TraceSearchResponse)
async def search_traces(
    status: str | None = Query(None),
    workflow_id: uuid.UUID | None = Query(None),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
) -> TraceSearchResponse:
    """Search execution traces and LLM call history.

    Returns both execution-level traces (jobs) and individual LLM calls.
    Default date range: last 7 days if not specified.
    """
    now = datetime.now(timezone.utc)
    if date_from is None:
        date_from = now - timedelta(days=7)
    if date_to is None:
        date_to = now + timedelta(days=1)  # Generous upper bound for clock skew

    # --- Execution traces from BackgroundJob ---
    job_conditions = [
        BackgroundJob.tenant_id == tenant_id,
        BackgroundJob.created_at >= date_from,
        BackgroundJob.created_at <= date_to,
    ]
    if status:
        job_conditions.append(BackgroundJob.status == status)
    if workflow_id:
        job_conditions.append(BackgroundJob.workflow_id == workflow_id)

    data_q = (
        select(BackgroundJob)
        .options(selectinload(BackgroundJob.workflow), selectinload(BackgroundJob.agent))
        .where(and_(*job_conditions))
        .order_by(BackgroundJob.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(data_q)
    jobs = result.scalars().all()

    # Map job_type to friendly source name
    source_map = {
        "background_agent": "background_agent",
        "deep_work": "deep_work",
        "workflow_execution": "workflow",
        "agent_chat": "chat",
    }

    traces = []
    for job in jobs:
        duration = None
        if job.started_at and job.completed_at:
            duration = (job.completed_at - job.started_at).total_seconds() * 1000

        # Parse execution trace steps
        raw_trace = job.execution_trace or []
        trace_steps = [
            ExecutionTraceStep(**step) if isinstance(step, dict) else step
            for step in raw_trace
        ]

        traces.append(TraceSearchResult(
            run_id=job.id,
            workflow_id=job.workflow_id,
            workflow_name=job.workflow.name if job.workflow else None,
            agent_name=job.agent.name if job.agent else None,
            source=source_map.get(job.job_type, job.job_type),
            status=job.status,
            created_at=job.created_at,
            completed_at=job.completed_at,
            duration_ms=duration,
            total_steps=len(raw_trace),
            execution_trace=trace_steps,
            error=job.error,
            input_data=job.input_data,
            result=job.result,
        ))

    # --- LLM call traces from llm_usage_records ---
    llm_date_to = date_to
    llm_conditions = [
        LLMUsageRecord.tenant_id == tenant_id,
        LLMUsageRecord.created_at >= date_from,
        LLMUsageRecord.created_at <= llm_date_to,
    ]
    llm_q = (
        select(LLMUsageRecord)
        .where(and_(*llm_conditions))
        .order_by(LLMUsageRecord.created_at.desc())
        .limit(200)
    )
    llm_result = await db.execute(llm_q)
    llm_records = llm_result.scalars().all()

    llm_calls = [
        LLMCallTrace(
            id=r.id,
            model=_clean_model_name(r.model),
            provider=r.provider_name,
            input_tokens=r.input_tokens,
            output_tokens=r.output_tokens,
            cost_usd=float(r.cost_usd),
            latency_ms=r.latency_ms,
            status=r.status or "success",
            agent_id=str(r.agent_id) if r.agent_id else None,
            workflow_id=str(r.workflow_id) if r.workflow_id else None,
            created_at=r.created_at,
        )
        for r in llm_records
    ]

    total = len(traces) + len(llm_calls)

    return TraceSearchResponse(traces=traces, llm_calls=llm_calls, total=total)


@router.get("/agent-health", response_model=AllAgentsHealthResponse)
async def get_all_agents_health(
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
) -> AllAgentsHealthResponse:
    """Get health status of all agents in one call.

    Combines circuit breaker status (Redis MGET) with latest accuracy
    (PostgreSQL) for each custom agent in the tenant.
    """
    # Load all custom agents for this tenant
    agents_q = (
        select(CustomAgent)
        .where(CustomAgent.tenant_id == tenant_id)
        .order_by(CustomAgent.name)
    )
    result = await db.execute(agents_q)
    agents = result.scalars().all()

    if not agents:
        return AllAgentsHealthResponse(agents=[], total=0)

    agent_ids = [a.id for a in agents]

    # Bulk circuit breaker status via Redis MGET
    cb_statuses: dict = {}
    try:
        redis = await get_redis_service()
        breaker = AgentCircuitBreaker(redis)
        cb_statuses = await breaker.get_bulk_status(agent_ids)
    except Exception as e:
        logger.warning(f"Redis unavailable for circuit breaker status: {e}")
        # Graceful degradation — mark all as unknown

    # Latest accuracy per agent via subquery
    latest_eval_subq = (
        select(
            AgentAccuracyHistory.agent_id,
            func.max(AgentAccuracyHistory.evaluated_at).label("latest_at"),
        )
        .where(AgentAccuracyHistory.tenant_id == tenant_id)
        .group_by(AgentAccuracyHistory.agent_id)
        .subquery()
    )
    accuracy_q = (
        select(AgentAccuracyHistory)
        .join(
            latest_eval_subq,
            and_(
                AgentAccuracyHistory.agent_id == latest_eval_subq.c.agent_id,
                AgentAccuracyHistory.evaluated_at == latest_eval_subq.c.latest_at,
            ),
        )
        .where(AgentAccuracyHistory.tenant_id == tenant_id)
    )
    acc_result = await db.execute(accuracy_q)
    accuracy_map = {
        str(a.agent_id): a for a in acc_result.scalars().all()
    }

    summaries = []
    for agent in agents:
        aid = str(agent.id)
        cb = cb_statuses.get(aid, {})
        acc_entry = accuracy_map.get(aid)

        has_drift = None
        if acc_entry and acc_entry.accuracy_delta is not None:
            # Drift detected if accuracy dropped more than 5%
            has_drift = acc_entry.accuracy_delta < -0.05

        summaries.append(AgentHealthSummary(
            agent_id=aid,
            agent_name=agent.name,
            agent_type=agent.agent_type or "custom",
            circuit_breaker_status=cb.get("state", "healthy"),
            accuracy=acc_entry.accuracy if acc_entry else None,
            has_drift=has_drift,
            last_evaluation_at=acc_entry.evaluated_at if acc_entry else None,
        ))

    return AllAgentsHealthResponse(agents=summaries, total=len(summaries))


class SessionCleanupResponse(BaseModel):
    deleted: int
    skipped: int
    errors: int


@router.post("/session-cleanup", response_model=SessionCleanupResponse)
async def trigger_session_cleanup(
    retention_days: int = Query(None, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
) -> SessionCleanupResponse:
    """Trigger session cleanup to delete stale sessions.

    Deletes sessions older than SESSION_RETENTION_DAYS (configurable) that are
    in completed/failed state with no active background jobs. Idempotent — safe
    to call multiple times. Intended to be called by a cron scheduler daily.
    """
    from app.config import get_settings
    from app.services.session_storage import cleanup_stale_sessions

    settings = get_settings()
    days = retention_days or settings.SESSION_RETENTION_DAYS
    result = await cleanup_stale_sessions(db=db, retention_days=days)
    return SessionCleanupResponse(**result)
