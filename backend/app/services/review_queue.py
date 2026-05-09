"""
Human-in-Loop Review Queue Service

Manages review tickets for high-risk agent decisions that require human approval.
Supports compliance workflows where certain decisions need manual verification.

Based on Section 3.4 - State Machine and Human Review.

Features:
- Create review tickets for high-risk decisions
- Assignment and workflow management
- SLA tracking and escalation
- Integration with state machine (HUMAN_REVIEW state)
- Notification support
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from uuid import UUID, uuid4
from enum import Enum

from pydantic import BaseModel
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings

logger = logging.getLogger(__name__)


# =============================================================================
# Models
# =============================================================================

class ReviewPriority(str, Enum):
    """Review ticket priority levels."""
    LOW = 'low'
    NORMAL = 'normal'
    HIGH = 'high'
    URGENT = 'urgent'


class ReviewStatus(str, Enum):
    """Review ticket status."""
    PENDING = 'pending'
    ASSIGNED = 'assigned'
    IN_REVIEW = 'in_review'
    APPROVED = 'approved'
    REJECTED = 'rejected'
    ESCALATED = 'escalated'


class ReviewType(str, Enum):
    """Types of reviews."""
    COMPLIANCE = 'compliance'
    DECISION = 'decision'
    OUTPUT = 'output'
    DATA_ACCESS = 'data_access'


class ReviewTicketCreate(BaseModel):
    """Data for creating a new review ticket."""
    title: str
    description: str
    risk_level: str  # low, medium, high, critical
    priority: str = 'normal'
    review_type: str
    review_data: Dict[str, Any] = {}

    # Context
    workflow_id: Optional[str] = None
    session_id: Optional[str] = None
    agent_id: str

    # Related audit entry
    audit_log_id: Optional[str] = None

    # SLA configuration
    sla_hours: Optional[int] = None


class ReviewDecisionInput(BaseModel):
    """Input for submitting a review decision."""
    decision: str  # approved, rejected, escalated
    notes: Optional[str] = None


class ReviewTicketResponse(BaseModel):
    """Response model for a review ticket."""
    id: str
    tenant_id: str
    workflow_id: Optional[str] = None
    session_id: Optional[str] = None
    agent_id: str
    requested_by: Optional[str] = None

    title: str
    description: str
    risk_level: str
    priority: str
    review_type: str
    review_data: Dict[str, Any] = {}

    status: str
    assigned_to: Optional[str] = None
    assigned_at: Optional[datetime] = None

    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    decision: Optional[str] = None
    review_notes: Optional[str] = None

    due_at: Optional[datetime] = None
    sla_breached: bool = False

    created_at: datetime
    updated_at: datetime


class ReviewQueueStats(BaseModel):
    """Statistics for the review queue."""
    total_pending: int
    total_in_review: int
    total_completed_today: int
    average_review_time_ms: Optional[int] = None
    sla_breach_count: int
    by_priority: Dict[str, int] = {}
    by_risk_level: Dict[str, int] = {}


# =============================================================================
# SLA Configuration
# =============================================================================

DEFAULT_SLA_HOURS = {
    'low': 48,      # 2 days for low risk
    'medium': 24,   # 1 day for medium risk
    'high': 8,      # 8 hours for high risk
    'critical': 2,  # 2 hours for critical risk
}


# =============================================================================
# Review Queue Service
# =============================================================================

class ReviewQueueService:
    """
    Service for managing human-in-loop review tickets.

    Provides:
    - Ticket creation and assignment
    - Review workflow management
    - SLA tracking and breach detection
    - Queue analytics
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.settings = get_settings()

    async def create_ticket(
        self,
        tenant_id: str,
        data: ReviewTicketCreate,
        requested_by: Optional[str] = None,
    ) -> ReviewTicketResponse:
        """
        Create a new review ticket.

        Args:
            tenant_id: Tenant ID
            data: Ticket creation data
            requested_by: User ID who requested the review

        Returns:
            Created review ticket
        """
        from app.models.compliance_audit_log import HumanReviewTicket

        # Calculate due date based on SLA
        sla_hours = data.sla_hours or DEFAULT_SLA_HOURS.get(data.risk_level, 24)
        due_at = datetime.utcnow() + timedelta(hours=sla_hours)

        ticket = HumanReviewTicket(
            id=uuid4(),
            tenant_id=UUID(tenant_id),
            workflow_id=UUID(data.workflow_id) if data.workflow_id else None,
            session_id=UUID(data.session_id) if data.session_id else None,
            agent_id=data.agent_id,
            requested_by=UUID(requested_by) if requested_by else None,
            title=data.title,
            description=data.description,
            risk_level=data.risk_level,
            priority=data.priority,
            review_type=data.review_type,
            review_data=data.review_data,
            audit_log_id=UUID(data.audit_log_id) if data.audit_log_id else None,
            status=ReviewStatus.PENDING.value,
            due_at=due_at,
        )

        self.db.add(ticket)
        await self.db.commit()
        await self.db.refresh(ticket)

        logger.info(
            f"Created review ticket: id={ticket.id}, title={data.title}, "
            f"risk={data.risk_level}, priority={data.priority}"
        )

        return self._to_response(ticket)

    async def get_ticket(
        self,
        ticket_id: str,
        tenant_id: str,
    ) -> Optional[ReviewTicketResponse]:
        """Get a single review ticket by ID."""
        from app.models.compliance_audit_log import HumanReviewTicket

        stmt = select(HumanReviewTicket).where(
            and_(
                HumanReviewTicket.id == UUID(ticket_id),
                HumanReviewTicket.tenant_id == UUID(tenant_id),
            )
        )

        result = await self.db.execute(stmt)
        ticket = result.scalar_one_or_none()

        if not ticket:
            return None

        return self._to_response(ticket)

    async def list_tickets(
        self,
        tenant_id: str,
        status: Optional[List[str]] = None,
        priority: Optional[List[str]] = None,
        risk_level: Optional[List[str]] = None,
        assigned_to: Optional[str] = None,
        workflow_id: Optional[str] = None,
        include_completed: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> List[ReviewTicketResponse]:
        """
        List review tickets with filters.

        Args:
            tenant_id: Tenant ID
            status: Filter by status(es)
            priority: Filter by priority(ies)
            risk_level: Filter by risk level(s)
            assigned_to: Filter by assignee
            workflow_id: Filter by workflow
            include_completed: Include completed tickets
            limit: Max results
            offset: Pagination offset

        Returns:
            List of review tickets
        """
        from app.models.compliance_audit_log import HumanReviewTicket

        conditions = [HumanReviewTicket.tenant_id == UUID(tenant_id)]

        if status:
            conditions.append(HumanReviewTicket.status.in_(status))
        elif not include_completed:
            conditions.append(
                HumanReviewTicket.status.in_([
                    ReviewStatus.PENDING.value,
                    ReviewStatus.ASSIGNED.value,
                    ReviewStatus.IN_REVIEW.value,
                ])
            )

        if priority:
            conditions.append(HumanReviewTicket.priority.in_(priority))

        if risk_level:
            conditions.append(HumanReviewTicket.risk_level.in_(risk_level))

        if assigned_to:
            conditions.append(HumanReviewTicket.assigned_to == UUID(assigned_to))

        if workflow_id:
            conditions.append(HumanReviewTicket.workflow_id == UUID(workflow_id))

        stmt = (
            select(HumanReviewTicket)
            .where(and_(*conditions))
            .order_by(
                # Sort by priority (urgent first), then by due date
                HumanReviewTicket.priority.desc(),
                HumanReviewTicket.due_at.asc(),
            )
            .limit(limit)
            .offset(offset)
        )

        result = await self.db.execute(stmt)
        tickets = result.scalars().all()

        return [self._to_response(t) for t in tickets]

    async def assign_ticket(
        self,
        ticket_id: str,
        tenant_id: str,
        assigned_to: str,
    ) -> Optional[ReviewTicketResponse]:
        """
        Assign a ticket to a reviewer.

        Args:
            ticket_id: Ticket ID
            tenant_id: Tenant ID
            assigned_to: User ID to assign to

        Returns:
            Updated ticket
        """
        from app.models.compliance_audit_log import HumanReviewTicket

        stmt = select(HumanReviewTicket).where(
            and_(
                HumanReviewTicket.id == UUID(ticket_id),
                HumanReviewTicket.tenant_id == UUID(tenant_id),
            )
        )

        result = await self.db.execute(stmt)
        ticket = result.scalar_one_or_none()

        if not ticket:
            return None

        ticket.assigned_to = UUID(assigned_to)
        ticket.assigned_at = datetime.utcnow()
        ticket.status = ReviewStatus.ASSIGNED.value

        await self.db.commit()
        await self.db.refresh(ticket)

        logger.info(f"Assigned ticket {ticket_id} to user {assigned_to}")

        return self._to_response(ticket)

    async def start_review(
        self,
        ticket_id: str,
        tenant_id: str,
        reviewer_id: str,
    ) -> Optional[ReviewTicketResponse]:
        """
        Mark a ticket as being actively reviewed.

        Args:
            ticket_id: Ticket ID
            tenant_id: Tenant ID
            reviewer_id: User ID of the reviewer

        Returns:
            Updated ticket
        """
        from app.models.compliance_audit_log import HumanReviewTicket

        stmt = select(HumanReviewTicket).where(
            and_(
                HumanReviewTicket.id == UUID(ticket_id),
                HumanReviewTicket.tenant_id == UUID(tenant_id),
            )
        )

        result = await self.db.execute(stmt)
        ticket = result.scalar_one_or_none()

        if not ticket:
            return None

        # Auto-assign if not already assigned
        if not ticket.assigned_to:
            ticket.assigned_to = UUID(reviewer_id)
            ticket.assigned_at = datetime.utcnow()

        ticket.status = ReviewStatus.IN_REVIEW.value

        await self.db.commit()
        await self.db.refresh(ticket)

        logger.info(f"Started review of ticket {ticket_id} by user {reviewer_id}")

        return self._to_response(ticket)

    async def submit_decision(
        self,
        ticket_id: str,
        tenant_id: str,
        reviewer_id: str,
        decision: ReviewDecisionInput,
    ) -> Optional[ReviewTicketResponse]:
        """
        Submit a review decision.

        Args:
            ticket_id: Ticket ID
            tenant_id: Tenant ID
            reviewer_id: User ID of the reviewer
            decision: The review decision

        Returns:
            Updated ticket
        """
        from app.models.compliance_audit_log import HumanReviewTicket

        stmt = select(HumanReviewTicket).where(
            and_(
                HumanReviewTicket.id == UUID(ticket_id),
                HumanReviewTicket.tenant_id == UUID(tenant_id),
            )
        )

        result = await self.db.execute(stmt)
        ticket = result.scalar_one_or_none()

        if not ticket:
            return None

        # Prevent self-review: ticket creator cannot submit decision on own ticket
        if ticket.requested_by and str(ticket.requested_by) == reviewer_id:
            from fastapi import HTTPException
            raise HTTPException(
                status_code=400,
                detail="Cannot review your own ticket"
            )

        ticket.reviewed_by = UUID(reviewer_id)
        ticket.reviewed_at = datetime.utcnow()
        ticket.decision = decision.decision
        ticket.review_notes = decision.notes
        ticket.status = ReviewStatus(decision.decision).value

        await self.db.commit()
        await self.db.refresh(ticket)

        logger.info(
            f"Review decision submitted: ticket={ticket_id}, "
            f"decision={decision.decision}, reviewer={reviewer_id}"
        )

        # TODO: Trigger workflow state transition based on decision
        # await self._trigger_workflow_transition(ticket, decision)

        return self._to_response(ticket)

    async def check_sla_breaches(
        self,
        tenant_id: str,
    ) -> List[ReviewTicketResponse]:
        """
        Check for and update SLA breaches.

        Returns:
            List of tickets that have breached SLA
        """
        from app.models.compliance_audit_log import HumanReviewTicket

        now = datetime.utcnow()

        # Find tickets past their due date that aren't completed
        stmt = select(HumanReviewTicket).where(
            and_(
                HumanReviewTicket.tenant_id == UUID(tenant_id),
                HumanReviewTicket.due_at < now,
                HumanReviewTicket.sla_breached == 'false',
                HumanReviewTicket.status.in_([
                    ReviewStatus.PENDING.value,
                    ReviewStatus.ASSIGNED.value,
                    ReviewStatus.IN_REVIEW.value,
                ]),
            )
        )

        result = await self.db.execute(stmt)
        breached_tickets = result.scalars().all()

        # Update breach status
        for ticket in breached_tickets:
            ticket.sla_breached = 'true'

        if breached_tickets:
            await self.db.commit()
            logger.warning(
                f"SLA breach detected: {len(breached_tickets)} tickets past due"
            )

        return [self._to_response(t) for t in breached_tickets]

    async def get_queue_stats(
        self,
        tenant_id: str,
    ) -> ReviewQueueStats:
        """
        Get statistics for the review queue.

        Args:
            tenant_id: Tenant ID

        Returns:
            Queue statistics
        """
        from app.models.compliance_audit_log import HumanReviewTicket

        tenant_filter = HumanReviewTicket.tenant_id == UUID(tenant_id)

        # Count pending
        pending_stmt = select(func.count()).select_from(HumanReviewTicket).where(
            and_(tenant_filter, HumanReviewTicket.status == ReviewStatus.PENDING.value)
        )
        pending_result = await self.db.execute(pending_stmt)
        total_pending = pending_result.scalar() or 0

        # Count in review
        in_review_stmt = select(func.count()).select_from(HumanReviewTicket).where(
            and_(
                tenant_filter,
                HumanReviewTicket.status.in_([
                    ReviewStatus.ASSIGNED.value,
                    ReviewStatus.IN_REVIEW.value,
                ]),
            )
        )
        in_review_result = await self.db.execute(in_review_stmt)
        total_in_review = in_review_result.scalar() or 0

        # Count completed today
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        completed_stmt = select(func.count()).select_from(HumanReviewTicket).where(
            and_(
                tenant_filter,
                HumanReviewTicket.reviewed_at >= today_start,
                HumanReviewTicket.status.in_([
                    ReviewStatus.APPROVED.value,
                    ReviewStatus.REJECTED.value,
                ]),
            )
        )
        completed_result = await self.db.execute(completed_stmt)
        total_completed_today = completed_result.scalar() or 0

        # Count SLA breaches
        breach_stmt = select(func.count()).select_from(HumanReviewTicket).where(
            and_(tenant_filter, HumanReviewTicket.sla_breached == 'true')
        )
        breach_result = await self.db.execute(breach_stmt)
        sla_breach_count = breach_result.scalar() or 0

        # Count by priority
        priority_stmt = (
            select(HumanReviewTicket.priority, func.count())
            .where(
                and_(
                    tenant_filter,
                    HumanReviewTicket.status.in_([
                        ReviewStatus.PENDING.value,
                        ReviewStatus.ASSIGNED.value,
                        ReviewStatus.IN_REVIEW.value,
                    ]),
                )
            )
            .group_by(HumanReviewTicket.priority)
        )
        priority_result = await self.db.execute(priority_stmt)
        by_priority = dict(priority_result.fetchall())

        # Count by risk level
        risk_stmt = (
            select(HumanReviewTicket.risk_level, func.count())
            .where(
                and_(
                    tenant_filter,
                    HumanReviewTicket.status.in_([
                        ReviewStatus.PENDING.value,
                        ReviewStatus.ASSIGNED.value,
                        ReviewStatus.IN_REVIEW.value,
                    ]),
                )
            )
            .group_by(HumanReviewTicket.risk_level)
        )
        risk_result = await self.db.execute(risk_stmt)
        by_risk_level = dict(risk_result.fetchall())

        return ReviewQueueStats(
            total_pending=total_pending,
            total_in_review=total_in_review,
            total_completed_today=total_completed_today,
            sla_breach_count=sla_breach_count,
            by_priority=by_priority,
            by_risk_level=by_risk_level,
        )

    def _to_response(self, ticket) -> ReviewTicketResponse:
        """Convert database model to response model."""
        return ReviewTicketResponse(
            id=str(ticket.id),
            tenant_id=str(ticket.tenant_id),
            workflow_id=str(ticket.workflow_id) if ticket.workflow_id else None,
            session_id=str(ticket.session_id) if ticket.session_id else None,
            agent_id=ticket.agent_id,
            requested_by=str(ticket.requested_by) if ticket.requested_by else None,
            title=ticket.title,
            description=ticket.description,
            risk_level=ticket.risk_level,
            priority=ticket.priority,
            review_type=ticket.review_type,
            review_data=ticket.review_data or {},
            status=ticket.status,
            assigned_to=str(ticket.assigned_to) if ticket.assigned_to else None,
            assigned_at=ticket.assigned_at,
            reviewed_by=str(ticket.reviewed_by) if ticket.reviewed_by else None,
            reviewed_at=ticket.reviewed_at,
            decision=ticket.decision,
            review_notes=ticket.review_notes,
            due_at=ticket.due_at,
            sla_breached=ticket.sla_breached == 'true',
            created_at=ticket.created_at,
            updated_at=ticket.updated_at,
        )


# =============================================================================
# Convenience Functions
# =============================================================================

async def create_review_ticket(
    db: AsyncSession,
    tenant_id: str,
    title: str,
    description: str,
    agent_id: str,
    risk_level: str,
    review_type: str,
    review_data: Optional[Dict[str, Any]] = None,
    workflow_id: Optional[str] = None,
    session_id: Optional[str] = None,
    requested_by: Optional[str] = None,
) -> ReviewTicketResponse:
    """
    Convenience function to create a review ticket.

    Used by agents when they need human approval.
    """
    service = ReviewQueueService(db)

    return await service.create_ticket(
        tenant_id=tenant_id,
        data=ReviewTicketCreate(
            title=title,
            description=description,
            risk_level=risk_level,
            review_type=review_type,
            review_data=review_data or {},
            workflow_id=workflow_id,
            session_id=session_id,
            agent_id=agent_id,
        ),
        requested_by=requested_by,
    )


async def get_pending_reviews(
    db: AsyncSession,
    tenant_id: str,
    reviewer_id: Optional[str] = None,
) -> List[ReviewTicketResponse]:
    """Get all pending reviews, optionally for a specific reviewer."""
    service = ReviewQueueService(db)

    return await service.list_tickets(
        tenant_id=tenant_id,
        status=[ReviewStatus.PENDING.value, ReviewStatus.ASSIGNED.value],
        assigned_to=reviewer_id,
    )
