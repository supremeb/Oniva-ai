"""Inline Feedback API endpoints (Phase E).

Allows users to submit thumbs up/down on agent responses
and retrieve feedback summaries.
"""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, func, case, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.logging import get_logger
from app.models.agent_feedback import AgentFeedback
from app.schemas.feedback import (
    FeedbackCreate,
    FeedbackResponse,
    FeedbackListResponse,
    FeedbackSummaryResponse,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/feedback", tags=["feedback"])


def _get_tenant_id(request: Request) -> UUID:
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Tenant context required")
    return UUID(str(tenant_id))


def _fb_to_response(fb: AgentFeedback) -> FeedbackResponse:
    return FeedbackResponse(**fb.to_dict())


# ============================================================================
# Submit Feedback
# ============================================================================

@router.post("", response_model=FeedbackResponse, status_code=201)
async def submit_feedback(
    data: FeedbackCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Submit thumbs up/down feedback on an agent response."""
    tenant_id = _get_tenant_id(request)
    user_id = getattr(request.state, "audit_context", {}).get("user_id")

    fb = AgentFeedback(
        tenant_id=tenant_id,
        agent_id=UUID(data.agent_id) if data.agent_id else None,
        user_id=UUID(str(user_id)) if user_id else None,
        session_id=data.session_id,
        message_index=data.message_index,
        rating=data.rating,
        feedback_text=data.feedback_text,
        agent_response_summary=data.agent_response_summary,
    )
    db.add(fb)
    await db.flush()
    logger.info(f"Feedback submitted: rating={data.rating}, agent={data.agent_id}")
    return _fb_to_response(fb)


# ============================================================================
# List Feedback
# ============================================================================

@router.get("", response_model=FeedbackListResponse)
async def list_feedback(
    request: Request,
    agent_id: Optional[str] = Query(None, description="Filter by agent UUID"),
    rating: Optional[str] = Query(None, pattern="^(positive|negative)$"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """List feedback entries with optional filters."""
    tenant_id = _get_tenant_id(request)

    query = select(AgentFeedback).where(AgentFeedback.tenant_id == tenant_id)

    if agent_id:
        query = query.where(AgentFeedback.agent_id == UUID(agent_id))
    if rating:
        query = query.where(AgentFeedback.rating == rating)

    # Count
    count_query = select(func.count()).select_from(query.subquery())
    count_result = await db.execute(count_query)
    total = count_result.scalar_one()

    # Paginate
    query = query.order_by(desc(AgentFeedback.created_at)).limit(limit).offset(offset)
    result = await db.execute(query)
    items = result.scalars().all()

    return FeedbackListResponse(
        feedback=[_fb_to_response(fb) for fb in items],
        count=total,
    )


# ============================================================================
# Feedback Summary
# ============================================================================

@router.get("/summary", response_model=FeedbackSummaryResponse)
async def get_feedback_summary(
    request: Request,
    agent_id: Optional[str] = Query(None, description="Agent UUID (null = all agents)"),
    db: AsyncSession = Depends(get_db),
):
    """Get aggregated feedback summary, optionally for a specific agent."""
    tenant_id = _get_tenant_id(request)

    conditions = [AgentFeedback.tenant_id == tenant_id]
    if agent_id:
        conditions.append(AgentFeedback.agent_id == UUID(agent_id))

    # Aggregate counts
    result = await db.execute(
        select(
            func.count(AgentFeedback.id),
            func.count(case((AgentFeedback.rating == "positive", 1))),
            func.count(case((AgentFeedback.rating == "negative", 1))),
        ).where(and_(*conditions))
    )
    row = result.one()
    total, positive, negative = row[0], row[1], row[2]
    satisfaction = round(positive / total * 100, 1) if total > 0 else None

    # Recent negative feedback texts
    neg_result = await db.execute(
        select(AgentFeedback.feedback_text)
        .where(
            and_(
                *conditions,
                AgentFeedback.rating == "negative",
                AgentFeedback.feedback_text.isnot(None),
            )
        )
        .order_by(desc(AgentFeedback.created_at))
        .limit(5)
    )
    recent_texts = [r[0] for r in neg_result.all() if r[0]]

    return FeedbackSummaryResponse(
        agent_id=agent_id,
        total=total,
        positive=positive,
        negative=negative,
        satisfaction_rate=satisfaction,
        recent_negative_texts=recent_texts,
    )


# ============================================================================
# Delete Feedback
# ============================================================================

@router.delete("/{feedback_id}", status_code=204)
async def delete_feedback(
    feedback_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Delete a feedback entry (admin use)."""
    tenant_id = _get_tenant_id(request)

    result = await db.execute(
        select(AgentFeedback).where(
            and_(
                AgentFeedback.id == feedback_id,
                AgentFeedback.tenant_id == tenant_id,
            )
        )
    )
    fb = result.scalar_one_or_none()
    if not fb:
        raise HTTPException(status_code=404, detail="Feedback not found")

    await db.delete(fb)
    await db.flush()
