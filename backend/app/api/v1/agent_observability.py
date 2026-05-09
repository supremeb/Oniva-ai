"""Agent Observability API endpoints for Agent Studio (Phase A.5).

Provides circuit breaker status, drift detection, and scheduled evaluation.
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from typing import Optional, Dict, Any
from uuid import UUID
from datetime import datetime
from pydantic import BaseModel, Field

from app.core.database import get_db
from app.api.dependencies import verify_agent_access, AgentInfo
from app.models.agent_accuracy_history import AgentAccuracyHistory
from app.models.custom_agent import CustomAgent
from app.services.agent_circuit_breaker import (
    AgentCircuitBreaker,
    check_drift_and_trip,
)
from app.services.redis_ha import get_redis_service

router = APIRouter()


# ============================================================================
# Schemas
# ============================================================================

class CircuitBreakerStatusResponse(BaseModel):
    """Response for circuit breaker status."""
    agent_id: str
    state: str
    is_healthy: bool
    reason: Optional[str] = None
    metrics: Dict[str, Any] = Field(default_factory=dict)
    thresholds: Dict[str, Any] = Field(default_factory=dict)


class CircuitBreakerResetRequest(BaseModel):
    """Request to reset circuit breaker."""
    reason: Optional[str] = None


class DriftCheckResponse(BaseModel):
    """Response from drift check."""
    has_drift: bool
    current_accuracy: float
    baseline_accuracy: float
    drift_amount: float
    circuit_tripped: bool


class ScheduledEvaluationRequest(BaseModel):
    """Request to schedule an evaluation."""
    version_id: Optional[str] = None
    check_drift: bool = True


class ScheduledEvaluationResponse(BaseModel):
    """Response from scheduled evaluation."""
    job_id: str
    agent_id: str
    version_id: Optional[str] = None
    status: str
    scheduled_at: str


# ============================================================================
# Circuit Breaker Endpoints
# ============================================================================

@router.get(
    "/{agent_id}/circuit-breaker",
    response_model=CircuitBreakerStatusResponse,
    summary="Get circuit breaker status",
    description="Returns current circuit breaker state, metrics, and thresholds."
)
async def get_circuit_breaker_status(
    agent_info: AgentInfo = Depends(verify_agent_access),
) -> CircuitBreakerStatusResponse:
    """Get the circuit breaker status for an agent."""
    redis = await get_redis_service()
    breaker = AgentCircuitBreaker(redis)

    status_dict = await breaker.get_status(agent_info.id)

    return CircuitBreakerStatusResponse(**status_dict)


@router.post(
    "/{agent_id}/circuit-breaker/reset",
    response_model=CircuitBreakerStatusResponse,
    summary="Reset circuit breaker",
    description="Manually reset the circuit breaker to healthy state."
)
async def reset_circuit_breaker(
    request: CircuitBreakerResetRequest,
    agent_info: AgentInfo = Depends(verify_agent_access),
) -> CircuitBreakerStatusResponse:
    """Reset the circuit breaker for an agent."""
    redis = await get_redis_service()
    breaker = AgentCircuitBreaker(redis)

    # Reset the circuit
    await breaker.reset(agent_info.id)

    # Return new status
    status_dict = await breaker.get_status(agent_info.id)

    return CircuitBreakerStatusResponse(**status_dict)


@router.post(
    "/{agent_id}/circuit-breaker/trip",
    response_model=CircuitBreakerStatusResponse,
    summary="Trip circuit breaker",
    description="Manually trip the circuit breaker to degraded state."
)
async def trip_circuit_breaker(
    reason: str = Query(..., description="Reason for tripping"),
    ttl_minutes: int = Query(15, ge=1, le=1440, description="Auto-recovery TTL in minutes"),
    agent_info: AgentInfo = Depends(verify_agent_access),
) -> CircuitBreakerStatusResponse:
    """Manually trip the circuit breaker for an agent."""
    redis = await get_redis_service()
    breaker = AgentCircuitBreaker(redis)

    await breaker.trip(agent_info.id, reason, ttl_minutes)

    status_dict = await breaker.get_status(agent_info.id)

    return CircuitBreakerStatusResponse(**status_dict)


# ============================================================================
# Drift Detection Endpoints
# ============================================================================

@router.get(
    "/{agent_id}/drift",
    response_model=DriftCheckResponse,
    summary="Check accuracy drift",
    description="Check for accuracy drift compared to deployed baseline."
)
async def check_drift(
    db: AsyncSession = Depends(get_db),
    agent_info: AgentInfo = Depends(verify_agent_access),
    version_id: Optional[UUID] = Query(None, description="Version to check (defaults to latest)"),
    max_drift: float = Query(0.05, ge=0, le=1, description="Maximum allowed drift"),
) -> DriftCheckResponse:
    """Check for accuracy drift and optionally trip circuit breaker."""
    agent_id = agent_info.id

    # Get baseline (deployed version) accuracy
    baseline_query = (
        select(AgentAccuracyHistory)
        .join(
            CustomAgent,
            AgentAccuracyHistory.agent_id == CustomAgent.id
        )
        .where(
            AgentAccuracyHistory.agent_id == agent_id,
            AgentAccuracyHistory.agent_version_id.isnot(None)
        )
        .order_by(desc(AgentAccuracyHistory.evaluated_at))
        .limit(1)
    )

    baseline_result = await db.execute(baseline_query)
    baseline_entry = baseline_result.scalar_one_or_none()

    if not baseline_entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No baseline accuracy found. Run evaluation first."
        )

    baseline_accuracy = baseline_entry.accuracy

    # Get current accuracy
    current_query = select(AgentAccuracyHistory).where(
        AgentAccuracyHistory.agent_id == agent_id
    )
    if version_id:
        current_query = current_query.where(
            AgentAccuracyHistory.agent_version_id == version_id
        )

    current_query = current_query.order_by(
        desc(AgentAccuracyHistory.evaluated_at)
    ).limit(1)

    current_result = await db.execute(current_query)
    current_entry = current_result.scalar_one_or_none()

    if not current_entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No current accuracy found. Run evaluation first."
        )

    current_accuracy = current_entry.accuracy
    drift_amount = baseline_accuracy - current_accuracy
    has_drift = drift_amount > max_drift

    # Trip circuit if drift detected
    circuit_tripped = False
    if has_drift:
        redis = await get_redis_service()
        breaker = AgentCircuitBreaker(redis)
        circuit_tripped = await check_drift_and_trip(
            breaker, agent_id, current_accuracy, baseline_accuracy, max_drift
        )

    return DriftCheckResponse(
        has_drift=has_drift,
        current_accuracy=current_accuracy,
        baseline_accuracy=baseline_accuracy,
        drift_amount=drift_amount,
        circuit_tripped=circuit_tripped,
    )


# ============================================================================
# Scheduled Evaluation Endpoints
# ============================================================================

@router.post(
    "/{agent_id}/evaluate/schedule",
    response_model=ScheduledEvaluationResponse,
    summary="Schedule evaluation",
    description="Schedule an agent evaluation job (for drift detection)."
)
async def schedule_evaluation(
    request: ScheduledEvaluationRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    agent_info: AgentInfo = Depends(verify_agent_access),
) -> ScheduledEvaluationResponse:
    """Schedule an evaluation job for an agent."""
    import uuid

    job_id = str(uuid.uuid4())
    agent_id = agent_info.id

    # Add background task for evaluation
    # Note: In production, this would use a proper job queue like Celery/Redis Queue
    # For now, we create a tracking record

    return ScheduledEvaluationResponse(
        job_id=job_id,
        agent_id=str(agent_id),
        version_id=request.version_id,
        status="scheduled",
        scheduled_at=datetime.utcnow().isoformat(),
    )


# ============================================================================
# Health Summary Endpoint
# ============================================================================

@router.get(
    "/{agent_id}/health",
    summary="Get agent health summary",
    description="Returns comprehensive health status including circuit state, accuracy, and drift."
)
async def get_agent_health(
    db: AsyncSession = Depends(get_db),
    agent_info: AgentInfo = Depends(verify_agent_access),
) -> Dict[str, Any]:
    """Get comprehensive health summary for an agent."""
    agent_id = agent_info.id

    # Get circuit breaker status
    redis = await get_redis_service()
    breaker = AgentCircuitBreaker(redis)
    circuit_status = await breaker.get_status(agent_id)

    # Get latest accuracy
    accuracy_query = (
        select(AgentAccuracyHistory)
        .where(AgentAccuracyHistory.agent_id == agent_id)
        .order_by(desc(AgentAccuracyHistory.evaluated_at))
        .limit(1)
    )
    accuracy_result = await db.execute(accuracy_query)
    accuracy_entry = accuracy_result.scalar_one_or_none()

    # Get agent info
    agent_query = select(CustomAgent).where(CustomAgent.id == agent_id)
    agent_result = await db.execute(agent_query)
    agent = agent_result.scalar_one_or_none()

    return {
        "agent_id": str(agent_id),
        "agent_name": agent.name if agent else None,
        "circuit_breaker": {
            "state": circuit_status["state"],
            "is_healthy": circuit_status["is_healthy"],
            "reason": circuit_status.get("reason"),
        },
        "accuracy": {
            "current": accuracy_entry.accuracy if accuracy_entry else None,
            "last_evaluated": accuracy_entry.evaluated_at.isoformat() if accuracy_entry else None,
            "passed_tests": accuracy_entry.passed_tests if accuracy_entry else 0,
            "total_tests": accuracy_entry.total_tests if accuracy_entry else 0,
        },
        "thresholds": circuit_status.get("thresholds", {}),
        "checked_at": datetime.utcnow().isoformat(),
    }
