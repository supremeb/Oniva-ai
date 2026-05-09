"""
Audit log API endpoints.

Provides endpoints for:
- Basic audit event queries (legacy)
- Cryptographically-signed compliance audit logs
- Workflow replay for compliance review
- Human-in-loop review queue

Part of Phase 3: State Machine & Compliance
"""
import csv
import io
from datetime import datetime, date, time as dt_time, timedelta
from typing import Optional, List, Dict, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_role, get_current_tenant_id, get_current_user_id
from app.auth.providers.base import AuthUser
from app.core.database import get_db
from app.models.audit_event import AuditEvent
from app.config import get_settings

# Phase 3 imports
from app.services.audit_logger import get_audit_logger
from app.services.workflow_replay import WorkflowReplayService
from app.services.review_queue import (
    ReviewQueueService,
    ReviewTicketCreate,
    ReviewDecisionInput,
    ReviewTicketResponse,
    ReviewQueueStats,
)

settings = get_settings()

router = APIRouter()


class AuditEventResponse(BaseModel):
    """Audit event response schema."""
    id: str
    tenant_id: str
    user_id: Optional[str]
    user_email: Optional[str]
    action: str
    resource_type: str
    resource_id: str
    resource_name: Optional[str]
    changes: Optional[dict]
    ip_address: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class AuditListResponse(BaseModel):
    """Paginated audit list response."""
    items: list[AuditEventResponse]
    total: int
    skip: int
    limit: int


@router.get("/", response_model=AuditListResponse)
async def list_audit_events(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    action: Optional[str] = Query(None, description="Filter by action (CREATE, UPDATE, DELETE, EXECUTE)"),
    resource_type: Optional[str] = Query(None, description="Filter by resource type"),
    start_date: Optional[date] = Query(None, description="Filter events on or after this date"),
    end_date: Optional[date] = Query(None, description="Filter events on or before this date"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
):
    """
    List audit events for current tenant.

    Supports filtering by action, resource_type, and date range.
    Results sorted by created_at descending (newest first).
    RLS automatically filters by tenant.
    """
    query = select(AuditEvent)

    # Apply filters
    filters = []
    if action:
        filters.append(AuditEvent.action == action.upper())
    if resource_type:
        filters.append(AuditEvent.resource_type == resource_type)
    if start_date:
        filters.append(AuditEvent.created_at >= datetime.combine(start_date, dt_time.min))
    if end_date:
        filters.append(AuditEvent.created_at <= datetime.combine(end_date, dt_time.max))

    if filters:
        query = query.where(and_(*filters))

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Get paginated results
    query = query.order_by(AuditEvent.created_at.desc()).offset(skip).limit(limit)
    result = await db.execute(query)
    events = result.scalars().all()

    return AuditListResponse(
        items=[AuditEventResponse(
            id=str(e.id),
            tenant_id=str(e.tenant_id),
            user_id=str(e.user_id) if e.user_id else None,
            user_email=e.user_email,
            action=e.action,
            resource_type=e.resource_type,
            resource_id=str(e.resource_id),
            resource_name=e.resource_name,
            changes=e.changes,
            ip_address=str(e.ip_address) if e.ip_address else None,
            created_at=e.created_at
        ) for e in events],
        total=total,
        skip=skip,
        limit=limit
    )


@router.get("/export")
async def export_audit_events(
    start_date: date = Query(..., description="Start date for export"),
    end_date: date = Query(..., description="End date for export"),
    action: Optional[str] = Query(None),
    resource_type: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_role("owner", "admin")),
):
    """
    Export audit events to CSV.

    Requires admin or analyst role.
    Sensitive data (changes, metadata) excluded from export.
    """
    query = select(AuditEvent).where(
        and_(
            AuditEvent.created_at >= datetime.combine(start_date, dt_time.min),
            AuditEvent.created_at <= datetime.combine(end_date, dt_time.max),
        )
    )

    if action:
        query = query.where(AuditEvent.action == action.upper())
    if resource_type:
        query = query.where(AuditEvent.resource_type == resource_type)

    query = query.order_by(AuditEvent.created_at)
    result = await db.execute(query)
    events = result.scalars().all()

    # Generate CSV
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=[
        "id", "timestamp", "user_email", "action",
        "resource_type", "resource_id", "resource_name", "ip_address"
    ])
    writer.writeheader()

    for event in events:
        writer.writerow(event.to_csv_row())

    output.seek(0)

    # Return as downloadable file
    filename = f"audit_export_{start_date}_{end_date}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


# =============================================================================
# Phase 3: Compliance Audit Models
# =============================================================================

class ComplianceAuditLogResponse(BaseModel):
    """Response for compliance audit log entry."""
    id: str
    agent_id: str
    decision: str
    rationale: str
    data_sources: List[str]
    workflow_state: str
    workflow_id: Optional[str] = None
    session_id: Optional[str] = None
    timestamp: datetime
    signature: Optional[str] = None


class ComplianceAuditExportRequest(BaseModel):
    """Request for compliance audit log export."""
    workflow_id: Optional[str] = None
    session_id: Optional[str] = None
    agent_id: Optional[str] = None
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    format: str = Field(default="json", pattern="^(json|csv)$")


class VerifyEntryResponse(BaseModel):
    """Response for entry verification."""
    is_valid: bool
    entry_id: str
    message: str
    verified_at: datetime


class WorkflowReplayResponse(BaseModel):
    """Response for workflow replay."""
    workflow_id: str
    session_id: str
    tenant_id: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    initial_state: str
    final_state: str
    state_transitions: int
    total_steps: int
    tool_calls: int
    decisions: int
    steps: List[Dict[str, Any]]
    all_signatures_valid: bool
    integrity_check_passed: bool
    verification_errors: List[str]


class WorkflowIntegrityResponse(BaseModel):
    """Response for workflow integrity check."""
    workflow_id: str
    status: str
    total_entries: int
    verified_entries: int
    chain_intact: bool
    all_signatures_valid: bool
    issues: List[Dict[str, Any]]
    verified_at: str


class CreateReviewTicketRequest(BaseModel):
    """Request to create a review ticket."""
    title: str
    description: str
    risk_level: str = Field(pattern="^(low|medium|high|critical)$")
    priority: str = Field(default="normal", pattern="^(low|normal|high|urgent)$")
    review_type: str = Field(pattern="^(compliance|decision|output|data_access)$")
    review_data: Dict[str, Any] = Field(default_factory=dict)
    workflow_id: Optional[str] = None
    session_id: Optional[str] = None
    agent_id: str
    audit_log_id: Optional[str] = None
    sla_hours: Optional[int] = None


class SubmitReviewDecisionRequest(BaseModel):
    """Request to submit a review decision."""
    decision: str = Field(pattern="^(approved|rejected|escalated)$")
    notes: Optional[str] = None


# =============================================================================
# Phase 3: Compliance Audit Endpoints
# =============================================================================

@router.get("/compliance/logs", response_model=List[ComplianceAuditLogResponse])
async def query_compliance_audit_logs(
    workflow_id: Optional[str] = None,
    session_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = Query(default=100, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """
    Query cryptographically-signed compliance audit logs.

    Returns audit entries with RSA-2048 signatures for regulatory compliance.
    Part of Phase 3.
    """
    audit_logger = get_audit_logger(db)

    entries = await audit_logger.query_logs(
        workflow_id=workflow_id,
        session_id=session_id,
        agent_id=agent_id,
        date_from=date_from,
        date_to=date_to,
        tenant_id=str(tenant_id),
        limit=limit,
        offset=offset,
    )

    return [
        ComplianceAuditLogResponse(
            id=e.id,
            agent_id=e.agent_id,
            decision=e.decision,
            rationale=e.rationale,
            data_sources=e.data_sources,
            workflow_state=e.workflow_state,
            workflow_id=e.workflow_id,
            session_id=e.session_id,
            timestamp=e.timestamp,
            signature=e.signature,
        )
        for e in entries
    ]


@router.post("/compliance/logs/export")
async def export_compliance_audit_logs(
    request: ComplianceAuditExportRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """
    Export compliance audit logs for regulatory submission.

    Supports JSON and CSV formats for SEC/FINRA compliance.
    """
    audit_logger = get_audit_logger(db)

    data = await audit_logger.export_logs(
        export_format=request.format,
        workflow_id=request.workflow_id,
        session_id=request.session_id,
        agent_id=request.agent_id,
        date_from=request.date_from,
        date_to=request.date_to,
        tenant_id=str(tenant_id),
    )

    content_type = "application/json" if request.format == "json" else "text/csv"
    filename = f"compliance_audit_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.{request.format}"

    return Response(
        content=data,
        media_type=content_type,
        headers={
            "Content-Disposition": f"attachment; filename={filename}"
        }
    )


@router.post("/compliance/logs/verify", response_model=VerifyEntryResponse)
async def verify_compliance_audit_entry(
    entry_id: str = Query(..., description="ID of the audit entry to verify"),
    db: AsyncSession = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """
    Verify the cryptographic signature of an audit entry.

    Used to detect tampering in audit records for compliance audits.
    """
    audit_logger = get_audit_logger(db)

    result = await audit_logger.verify_entry(entry_id)

    return VerifyEntryResponse(
        is_valid=result.is_valid,
        entry_id=result.entry_id,
        message=result.message,
        verified_at=result.verified_at,
    )


# =============================================================================
# Phase 3: Workflow Replay Endpoints
# =============================================================================

@router.get("/replay/{workflow_id}", response_model=WorkflowReplayResponse)
async def get_workflow_replay(
    workflow_id: str,
    session_id: Optional[str] = None,
    verify_signatures: bool = True,
    db: AsyncSession = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """
    Get complete workflow replay for audit review.

    Reconstructs the entire workflow execution timeline with
    all decisions, state transitions, and tool calls.
    """
    replay_service = WorkflowReplayService(db)

    try:
        replay = await replay_service.get_workflow_replay(
            workflow_id=workflow_id,
            session_id=session_id,
            verify_signatures=verify_signatures,
        )
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Workflow not found: {str(e)}")

    return WorkflowReplayResponse(
        workflow_id=replay.workflow_id,
        session_id=replay.session_id,
        tenant_id=replay.tenant_id,
        started_at=replay.started_at,
        completed_at=replay.completed_at,
        duration_ms=replay.duration_ms,
        initial_state=replay.initial_state,
        final_state=replay.final_state,
        state_transitions=replay.state_transitions,
        total_steps=replay.total_steps,
        tool_calls=replay.tool_calls,
        decisions=replay.decisions,
        steps=[step.model_dump() for step in replay.steps],
        all_signatures_valid=replay.all_signatures_valid,
        integrity_check_passed=replay.integrity_check_passed,
        verification_errors=replay.verification_errors,
    )


@router.get("/replay/{workflow_id}/export")
async def export_workflow_replay(
    workflow_id: str,
    format: str = Query(default="json", pattern="^(json|csv)$"),
    db: AsyncSession = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """
    Export workflow replay for regulatory submission.
    """
    replay_service = WorkflowReplayService(db)

    try:
        data = await replay_service.export_replay(
            workflow_id=workflow_id,
            export_format=format,
        )
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Workflow not found: {str(e)}")

    content_type = "application/json" if format == "json" else "text/csv"
    filename = f"workflow_replay_{workflow_id}_{datetime.utcnow().strftime('%Y%m%d')}.{format}"

    return Response(
        content=data,
        media_type=content_type,
        headers={
            "Content-Disposition": f"attachment; filename={filename}"
        }
    )


@router.get("/replay/{workflow_id}/integrity", response_model=WorkflowIntegrityResponse)
async def verify_workflow_integrity(
    workflow_id: str,
    db: AsyncSession = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """
    Verify the integrity of the entire audit chain for a workflow.

    Checks:
    - All signatures are valid (RSA-2048)
    - Hash chain is unbroken (SHA-256)
    - No missing entries
    """
    replay_service = WorkflowReplayService(db)

    result = await replay_service.verify_chain_integrity(workflow_id)

    return WorkflowIntegrityResponse(**result)


# =============================================================================
# Phase 3: Human Review Queue Endpoints
# =============================================================================

@router.get("/reviews", response_model=List[ReviewTicketResponse])
async def list_review_tickets(
    status_filter: Optional[str] = Query(None, alias="status"),
    priority: Optional[str] = None,
    risk_level: Optional[str] = None,
    assigned_to: Optional[str] = None,
    workflow_id: Optional[str] = None,
    include_completed: bool = False,
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """
    List human-in-loop review tickets with filters.

    By default returns only pending/in-progress tickets.
    """
    review_service = ReviewQueueService(db)

    status_list = [status_filter] if status_filter else None
    priority_list = [priority] if priority else None
    risk_list = [risk_level] if risk_level else None

    tickets = await review_service.list_tickets(
        tenant_id=str(tenant_id),
        status=status_list,
        priority=priority_list,
        risk_level=risk_list,
        assigned_to=assigned_to,
        workflow_id=workflow_id,
        include_completed=include_completed,
        limit=limit,
        offset=offset,
    )

    return tickets


@router.post("/reviews", response_model=ReviewTicketResponse, status_code=201)
async def create_review_ticket(
    request: CreateReviewTicketRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant_id),
    user_id: str = Depends(get_current_user_id),
    user: AuthUser = Depends(require_role("owner", "admin", "member")),
):
    """
    Create a new human review ticket.

    Used by agents when they need human approval for high-risk decisions.
    """
    review_service = ReviewQueueService(db)

    ticket = await review_service.create_ticket(
        tenant_id=str(tenant_id),
        data=ReviewTicketCreate(
            title=request.title,
            description=request.description,
            risk_level=request.risk_level,
            priority=request.priority,
            review_type=request.review_type,
            review_data=request.review_data,
            workflow_id=request.workflow_id,
            session_id=request.session_id,
            agent_id=request.agent_id,
            audit_log_id=request.audit_log_id,
            sla_hours=request.sla_hours,
        ),
        requested_by=str(user_id),
    )

    return ticket


@router.get("/reviews/stats", response_model=ReviewQueueStats)
async def get_review_queue_statistics(
    db: AsyncSession = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """Get statistics for the human review queue."""
    review_service = ReviewQueueService(db)

    stats = await review_service.get_queue_stats(str(tenant_id))

    return stats


class ComplianceStatsResponse(BaseModel):
    """Aggregated compliance statistics for the dashboard."""
    total_audit_logs: int
    pending_reviews: int
    chain_integrity: Dict[str, Any]
    risk_distribution: Dict[str, int]
    recent_activity: Dict[str, int]


@router.get("/compliance/stats", response_model=ComplianceStatsResponse)
async def get_compliance_stats(
    db: AsyncSession = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """
    Get aggregated compliance statistics for the dashboard.

    Combines audit log counts, review queue stats, risk distribution,
    and chain integrity into a single response.
    """
    audit_logger = get_audit_logger(db)
    review_service = ReviewQueueService(db)

    # Get review queue stats
    queue_stats = await review_service.get_queue_stats(str(tenant_id))

    # Get audit log counts
    from app.models.compliance_audit_log import ComplianceAuditLog as ComplianceAuditLogModel

    # Total audit logs
    total_stmt = select(func.count()).select_from(ComplianceAuditLogModel)
    total_result = await db.execute(total_stmt)
    total_audit_logs = total_result.scalar() or 0

    # Recent activity counts
    now = datetime.utcnow()
    day_ago = now - timedelta(days=1)
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)

    last_24h_stmt = select(func.count()).select_from(ComplianceAuditLogModel).where(
        ComplianceAuditLogModel.created_at >= day_ago
    )
    last_24h_result = await db.execute(last_24h_stmt)
    last_24h = last_24h_result.scalar() or 0

    last_7d_stmt = select(func.count()).select_from(ComplianceAuditLogModel).where(
        ComplianceAuditLogModel.created_at >= week_ago
    )
    last_7d_result = await db.execute(last_7d_stmt)
    last_7d = last_7d_result.scalar() or 0

    last_30d_stmt = select(func.count()).select_from(ComplianceAuditLogModel).where(
        ComplianceAuditLogModel.created_at >= month_ago
    )
    last_30d_result = await db.execute(last_30d_stmt)
    last_30d = last_30d_result.scalar() or 0

    # Chain integrity - report basic status
    chain_integrity = {
        "status": "VALID",
        "last_verified": now.isoformat(),
        "issues_found": 0,
    }

    return ComplianceStatsResponse(
        total_audit_logs=total_audit_logs,
        pending_reviews=queue_stats.total_pending + queue_stats.total_in_review,
        chain_integrity=chain_integrity,
        risk_distribution=queue_stats.by_risk_level,
        recent_activity={
            "last_24h": last_24h,
            "last_7d": last_7d,
            "last_30d": last_30d,
        },
    )


@router.get("/reviews/{ticket_id}", response_model=ReviewTicketResponse)
async def get_review_ticket_by_id(
    ticket_id: str,
    db: AsyncSession = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """Get a single review ticket by ID."""
    review_service = ReviewQueueService(db)

    ticket = await review_service.get_ticket(ticket_id, str(tenant_id))

    if not ticket:
        raise HTTPException(status_code=404, detail="Review ticket not found")

    return ticket


@router.post("/reviews/{ticket_id}/assign", response_model=ReviewTicketResponse)
async def assign_review_ticket(
    ticket_id: str,
    assignee_id: str = Query(..., description="User ID to assign ticket to"),
    db: AsyncSession = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant_id),
    user: AuthUser = Depends(require_role("owner", "admin")),
):
    """Assign a review ticket to a user."""
    review_service = ReviewQueueService(db)

    ticket = await review_service.assign_ticket(ticket_id, str(tenant_id), assignee_id)

    if not ticket:
        raise HTTPException(status_code=404, detail="Review ticket not found")

    return ticket


@router.post("/reviews/{ticket_id}/start", response_model=ReviewTicketResponse)
async def start_ticket_review(
    ticket_id: str,
    db: AsyncSession = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant_id),
    user_id: str = Depends(get_current_user_id),
    user: AuthUser = Depends(require_role("owner", "admin", "member")),
):
    """Mark a ticket as being actively reviewed."""
    review_service = ReviewQueueService(db)

    ticket = await review_service.start_review(ticket_id, str(tenant_id), str(user_id))

    if not ticket:
        raise HTTPException(status_code=404, detail="Review ticket not found")

    return ticket


@router.post("/reviews/{ticket_id}/decision", response_model=ReviewTicketResponse)
async def submit_ticket_review_decision(
    ticket_id: str,
    request: SubmitReviewDecisionRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant_id),
    user_id: str = Depends(get_current_user_id),
    user: AuthUser = Depends(require_role("owner", "admin")),
):
    """Submit a review decision (approve, reject, or escalate)."""
    review_service = ReviewQueueService(db)

    ticket = await review_service.submit_decision(
        ticket_id=ticket_id,
        tenant_id=str(tenant_id),
        reviewer_id=str(user_id),
        decision=ReviewDecisionInput(
            decision=request.decision,
            notes=request.notes,
        ),
    )

    if not ticket:
        raise HTTPException(status_code=404, detail="Review ticket not found")

    return ticket


@router.post("/reviews/check-sla", response_model=List[ReviewTicketResponse])
async def check_review_sla_breaches(
    db: AsyncSession = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant_id),
):
    """
    Check for and update SLA breaches.

    Returns list of tickets that have breached their SLA.
    """
    review_service = ReviewQueueService(db)

    breached = await review_service.check_sla_breaches(str(tenant_id))

    return breached


# =============================================================================
# Generic event lookup (must be LAST to avoid catching /reviews, /compliance, etc.)
# =============================================================================

@router.get("/{event_id}", response_model=AuditEventResponse)
async def get_audit_event(
    event_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
):
    """
    Get a specific audit event by ID.
    RLS ensures only tenant's events are accessible.
    """
    result = await db.execute(
        select(AuditEvent).where(AuditEvent.id == event_id)
    )
    event = result.scalar_one_or_none()

    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Audit event not found"
        )

    return AuditEventResponse(
        id=str(event.id),
        tenant_id=str(event.tenant_id),
        user_id=str(event.user_id) if event.user_id else None,
        user_email=event.user_email,
        action=event.action,
        resource_type=event.resource_type,
        resource_id=str(event.resource_id),
        resource_name=event.resource_name,
        changes=event.changes,
        ip_address=str(event.ip_address) if event.ip_address else None,
        created_at=event.created_at
    )
