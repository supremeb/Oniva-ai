"""
Compliance API Endpoints
Provides access to compliance templates and execution
"""

import uuid
from typing import List, Dict, Any, Optional
from datetime import datetime

from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
import json

from app.core.database import get_db
from app.auth.dependencies import get_current_user, get_current_tenant_id, get_current_user_id
from app.auth.providers.base import AuthUser
from app.templates.compliance import get_compliance_templates, get_template_by_id
from app.services.compliance_engine import (
    ComplianceEngine,
)
from app.services import workflow_engine_runs as engine_runs
from app.services.workflow_orchestrator import WorkflowOrchestrator
from app.models.compliance_execution import (
    ComplianceExecution,
    ComplianceScreeningResult,
    ComplianceStatus,
    ReviewDecision,
)
from sqlalchemy import select, and_, desc

router = APIRouter()


# --- Pydantic Models ---

class ComplianceTemplateResponse(BaseModel):
    """Response model for a compliance template"""
    id: str
    name: str
    description: str
    version: str
    category: str
    regulatory_regimes: List[str]
    estimated_duration_seconds: int
    tags: List[str]


class ComplianceTemplateDetailResponse(ComplianceTemplateResponse):
    """Detailed response including the workflow spec"""
    spec: Dict[str, Any]
    sample_input: Dict[str, Any]


class SanctionsScreeningRequest(BaseModel):
    """Request for direct sanctions screening"""
    name: str = Field(..., description="Name to screen")
    date_of_birth: Optional[str] = Field(None, description="Date of birth (YYYY-MM-DD)")
    country: Optional[str] = Field(None, description="Country code or name")
    aliases: Optional[List[str]] = Field(default=[], description="Alternative names/aliases")
    match_threshold: float = Field(default=0.85, ge=0.5, le=1.0)


class PEPScreeningRequest(BaseModel):
    """Request for direct PEP screening"""
    name: str = Field(..., description="Name to screen")
    country: Optional[str] = Field(None, description="Country code or name")
    match_threshold: float = Field(default=0.80, ge=0.5, le=1.0)


class ScreeningResponse(BaseModel):
    """Response from screening checks"""
    check_type: str
    passed: bool
    match_found: bool
    match_count: int
    matches: List[Dict[str, Any]]
    risk_level: str
    flags: List[str]
    details: Dict[str, Any]
    timestamp: str


class ExecuteTemplateRequest(BaseModel):
    """Request to execute a compliance template"""
    template_id: str
    inputs: Dict[str, Any] = Field(..., description="Input variables for the template")


class ComplianceExecutionResponse(BaseModel):
    """Response from compliance template execution"""
    execution_id: str
    template_id: str
    template_name: str
    status: str
    results: Dict[str, Any]
    executed_nodes: int
    total_nodes: int
    timestamp: str


# --- Endpoints ---

@router.get("/templates", response_model=List[ComplianceTemplateResponse])
async def list_compliance_templates(
    category: Optional[str] = Query(None, description="Filter by category"),
    tag: Optional[str] = Query(None, description="Filter by tag"),
    user: AuthUser = Depends(get_current_user)
):
    """
    List all available compliance workflow templates.
    """
    templates = get_compliance_templates()

    # Filter by category if provided
    if category:
        templates = [t for t in templates if t.get("category") == category]

    # Filter by tag if provided
    if tag:
        templates = [t for t in templates if tag in t.get("tags", [])]

    return [
        ComplianceTemplateResponse(
            id=t["id"],
            name=t["name"],
            description=t["description"],
            version=t["version"],
            category=t["category"],
            regulatory_regimes=t["regulatory_regimes"],
            estimated_duration_seconds=t["estimated_duration_seconds"],
            tags=t["tags"]
        )
        for t in templates
    ]


@router.get("/templates/{template_id}", response_model=ComplianceTemplateDetailResponse)
async def get_compliance_template(
    template_id: str,
    user: AuthUser = Depends(get_current_user)
):
    """
    Get a specific compliance template by ID, including the workflow spec.
    """
    template = get_template_by_id(template_id)

    if not template:
        raise HTTPException(status_code=404, detail=f"Template '{template_id}' not found")

    return ComplianceTemplateDetailResponse(
        id=template["id"],
        name=template["name"],
        description=template["description"],
        version=template["version"],
        category=template["category"],
        regulatory_regimes=template["regulatory_regimes"],
        estimated_duration_seconds=template["estimated_duration_seconds"],
        tags=template["tags"],
        spec=template["spec"],
        sample_input=template.get("sample_input", {})
    )


@router.post("/templates/{template_id}/execute", response_model=ComplianceExecutionResponse)
async def execute_compliance_template(
    template_id: str,
    request: ExecuteTemplateRequest,
    db: AsyncSession = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user_id),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
):
    """
    Execute a compliance workflow template with provided inputs.
    Returns execution results including risk assessment.
    """
    template = get_template_by_id(template_id)

    if not template:
        raise HTTPException(status_code=404, detail=f"Template '{template_id}' not found")

    if await engine_runs.tenant_uses_engine_workflows(db, tenant_id):
        # Engine tenants: the template runs as an engine workflow run (plan 003 U15).
        execution, task_id = await engine_runs.start_workflow_run(
            db, None, template["spec"], dict(request.inputs or {}),
            user_id=user_id, tenant_id=tenant_id, trigger="compliance",
        )
        outcome = await engine_runs.await_finished(db, execution, task_id)
        await db.refresh(execution)
        results = engine_runs.results_from_trace(execution)
        return ComplianceExecutionResponse(
            execution_id=str(execution.id),
            template_id=template_id,
            template_name=template["name"],
            status=engine_runs.outcome_status(outcome) if outcome is not None else execution.status,
            results=results,
            executed_nodes=engine_runs.executed_count(results),
            total_nodes=execution.total_nodes or len(template["spec"].get("nodes") or []),
            timestamp=datetime.utcnow().isoformat(),
        )

    try:
        # Create orchestrator and execute
        orchestrator = WorkflowOrchestrator(db=db, user_id=str(user_id))

        result = await orchestrator.execute_workflow(
            workflow_spec=template["spec"],
            input_variables=request.inputs
        )

        execution_id = str(uuid.uuid4())

        return ComplianceExecutionResponse(
            execution_id=execution_id,
            template_id=template_id,
            template_name=template["name"],
            status=result["status"],
            results=result["results"],
            executed_nodes=result["executed_nodes"],
            total_nodes=result["total_nodes"],
            timestamp=datetime.utcnow().isoformat()
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Execution failed: {str(e)}")


@router.post("/templates/{template_id}/execute-stream")
async def execute_compliance_template_stream(
    template_id: str,
    request: ExecuteTemplateRequest,
    db: AsyncSession = Depends(get_db),
    user_id: uuid.UUID = Depends(get_current_user_id),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
):
    """
    Execute a compliance workflow template with streaming progress updates.
    Returns Server-Sent Events for real-time progress.
    """
    template = get_template_by_id(template_id)

    if not template:
        raise HTTPException(status_code=404, detail=f"Template '{template_id}' not found")

    if await engine_runs.tenant_uses_engine_workflows(db, tenant_id):
        # Started before the stream opens so a bad workflow (400) or engine outage (502) is a real error.
        execution, task_id = await engine_runs.start_workflow_run(
            db, None, template["spec"], dict(request.inputs or {}),
            user_id=user_id, tenant_id=tenant_id, trigger="compliance",
        )
        updates = engine_runs.execution_events(engine_runs.tenant_factory(tenant_id), execution.id, task_id)
    else:
        orchestrator = WorkflowOrchestrator(db=db, user_id=str(user_id))
        updates = orchestrator.execute_workflow_stream(
            workflow_spec=template["spec"],
            input_variables=request.inputs
        )

    async def event_stream():
        try:
            async for update in updates:
                yield f"data: {json.dumps(update)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
    )


@router.post("/screen/sanctions", response_model=ScreeningResponse)
async def screen_sanctions(
    request: SanctionsScreeningRequest,
    user: AuthUser = Depends(get_current_user)
):
    """
    Direct sanctions screening without a full workflow.
    Quick check against OFAC, UN, EU, UK sanctions lists.
    """
    engine = ComplianceEngine()

    result = await engine.run_sanctions_screening(
        name=request.name,
        dob=request.date_of_birth,
        country=request.country,
        aliases=request.aliases,
        config={"match_threshold": request.match_threshold}
    )

    return ScreeningResponse(
        check_type=result.check_type.value,
        passed=result.passed,
        match_found=result.match_found,
        match_count=len(result.matches),
        matches=[
            {
                "source_database": m.source_database,
                "matched_name": m.matched_name,
                "match_score": m.match_score,
                "match_type": m.match_type,
                "entry_id": m.entry_id,
                "additional_info": m.additional_info
            }
            for m in result.matches
        ],
        risk_level=result.risk_level.value,
        flags=result.flags,
        details=result.details,
        timestamp=result.timestamp
    )


@router.post("/screen/pep", response_model=ScreeningResponse)
async def screen_pep(
    request: PEPScreeningRequest,
    user: AuthUser = Depends(get_current_user)
):
    """
    Direct PEP (Politically Exposed Person) screening.
    Check if an individual is a PEP or related to one.
    """
    engine = ComplianceEngine()

    result = await engine.run_pep_screening(
        name=request.name,
        country=request.country,
        config={"match_threshold": request.match_threshold}
    )

    return ScreeningResponse(
        check_type=result.check_type.value,
        passed=result.passed,
        match_found=result.match_found,
        match_count=len(result.matches),
        matches=[
            {
                "source_database": m.source_database,
                "matched_name": m.matched_name,
                "match_score": m.match_score,
                "match_type": m.match_type,
                "entry_id": m.entry_id,
                "additional_info": m.additional_info
            }
            for m in result.matches
        ],
        risk_level=result.risk_level.value,
        flags=result.flags,
        details=result.details,
        timestamp=result.timestamp
    )


@router.get("/jurisdictions/high-risk")
async def get_high_risk_jurisdictions(
    user: AuthUser = Depends(get_current_user)
):
    """
    Get list of high-risk jurisdictions (FATF grey/black lists).
    """
    return {
        "jurisdictions": [
            {"code": code, "name": name}
            for code, name in ComplianceEngine.HIGH_RISK_JURISDICTIONS.items()
        ],
        "source": "FATF High-Risk and Non-Cooperative Jurisdictions",
        "last_updated": "2026-01-01"
    }


@router.post("/jurisdictions/check")
async def check_jurisdiction_risk(
    country: str = Query(..., description="Country code or name to check"),
    user: AuthUser = Depends(get_current_user)
):
    """
    Check if a specific country is considered high-risk.
    """
    engine = ComplianceEngine()
    result = await engine.check_jurisdiction_risk(country)

    return {
        "country": country,
        "is_high_risk": result.match_found,
        "risk_level": result.risk_level.value,
        "details": result.details
    }


# ===========================================
# Review Queue Models
# ===========================================

class ReviewQueueResponse(BaseModel):
    """Response model for review queue items"""
    id: str
    template_id: str
    template_name: Optional[str]
    subject_id: str
    subject_type: str
    subject_name: Optional[str]
    status: str
    risk_score: Optional[float]
    risk_level: Optional[str]
    recommendation: Optional[str]
    created_at: str
    needs_review: bool


class ReviewQueueDetailResponse(ReviewQueueResponse):
    """Detailed response for a single review item"""
    results: Dict[str, Any]
    execution_trace: List[Dict[str, Any]]
    screening_results: List[Dict[str, Any]]


class SubmitReviewRequest(BaseModel):
    """Request to submit a review decision"""
    decision: str = Field(..., description="Review decision: approved, rejected, escalated")
    notes: Optional[str] = Field(None, description="Optional review notes")


class ReviewResponse(BaseModel):
    """Response after submitting a review"""
    execution_id: str
    status: str
    review_decision: str
    reviewed_at: str
    message: str


# ===========================================
# Review Queue Endpoints
# ===========================================

@router.get("/review-queue", response_model=List[ReviewQueueResponse])
async def list_review_queue(
    status: Optional[str] = Query("manual_review", description="Filter by status"),
    risk_level: Optional[str] = Query(None, description="Filter by risk level"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    user: AuthUser = Depends(get_current_user)
):
    """
    List compliance executions in the review queue.
    By default, returns items requiring manual review.
    """
    # Build query
    conditions = [ComplianceExecution.tenant_id == tenant_id]

    if status:
        conditions.append(ComplianceExecution.status == status)

    if risk_level:
        conditions.append(ComplianceExecution.risk_level == risk_level)

    query = (
        select(ComplianceExecution)
        .where(and_(*conditions))
        .order_by(desc(ComplianceExecution.created_at))
        .offset(offset)
        .limit(limit)
    )

    result = await db.execute(query)
    executions = result.scalars().all()

    return [
        ReviewQueueResponse(
            id=str(e.id),
            template_id=e.template_id,
            template_name=e.template_name,
            subject_id=e.subject_id,
            subject_type=e.subject_type,
            subject_name=e.subject_name,
            status=e.status,
            risk_score=float(e.risk_score) if e.risk_score else None,
            risk_level=e.risk_level,
            recommendation=e.recommendation,
            created_at=e.created_at.isoformat() if e.created_at else "",
            needs_review=e.needs_review
        )
        for e in executions
    ]


@router.get("/review-queue/stats")
async def get_review_queue_stats(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    user: AuthUser = Depends(get_current_user)
):
    """
    Get statistics about the review queue.
    """
    from sqlalchemy import func as sql_func

    # Count by status
    status_query = (
        select(
            ComplianceExecution.status,
            sql_func.count(ComplianceExecution.id).label('count')
        )
        .where(ComplianceExecution.tenant_id == tenant_id)
        .group_by(ComplianceExecution.status)
    )
    status_result = await db.execute(status_query)
    status_counts = {row.status: row.count for row in status_result}

    # Count by risk level (for manual_review items)
    risk_query = (
        select(
            ComplianceExecution.risk_level,
            sql_func.count(ComplianceExecution.id).label('count')
        )
        .where(and_(
            ComplianceExecution.tenant_id == tenant_id,
            ComplianceExecution.status == ComplianceStatus.MANUAL_REVIEW.value
        ))
        .group_by(ComplianceExecution.risk_level)
    )
    risk_result = await db.execute(risk_query)
    risk_counts = {row.risk_level: row.count for row in risk_result if row.risk_level}

    return {
        "total_pending_review": status_counts.get(ComplianceStatus.MANUAL_REVIEW.value, 0),
        "total_completed": status_counts.get(ComplianceStatus.COMPLETED.value, 0),
        "total_failed": status_counts.get(ComplianceStatus.FAILED.value, 0),
        "by_status": status_counts,
        "pending_by_risk_level": risk_counts
    }


@router.get("/review-queue/{execution_id}", response_model=ReviewQueueDetailResponse)
async def get_review_item(
    execution_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    user: AuthUser = Depends(get_current_user)
):
    """
    Get detailed information about a specific compliance execution for review.
    """
    query = (
        select(ComplianceExecution)
        .where(and_(
            ComplianceExecution.id == execution_id,
            ComplianceExecution.tenant_id == tenant_id
        ))
    )
    result = await db.execute(query)
    execution = result.scalar_one_or_none()

    if not execution:
        raise HTTPException(status_code=404, detail="Execution not found")

    # Get screening results
    screening_query = (
        select(ComplianceScreeningResult)
        .where(ComplianceScreeningResult.execution_id == execution_id)
    )
    screening_result = await db.execute(screening_query)
    screening_results = [r.to_dict() for r in screening_result.scalars().all()]

    return ReviewQueueDetailResponse(
        id=str(execution.id),
        template_id=execution.template_id,
        template_name=execution.template_name,
        subject_id=execution.subject_id,
        subject_type=execution.subject_type,
        subject_name=execution.subject_name,
        status=execution.status,
        risk_score=float(execution.risk_score) if execution.risk_score else None,
        risk_level=execution.risk_level,
        recommendation=execution.recommendation,
        created_at=execution.created_at.isoformat() if execution.created_at else "",
        needs_review=execution.needs_review,
        results=execution.results or {},
        execution_trace=execution.execution_trace or [],
        screening_results=screening_results
    )


@router.post("/review-queue/{execution_id}/review", response_model=ReviewResponse)
async def submit_review(
    execution_id: uuid.UUID,
    request: SubmitReviewRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    user_id: uuid.UUID = Depends(get_current_user_id),
    user: AuthUser = Depends(get_current_user)
):
    """
    Submit a review decision for a compliance execution.
    """
    # Validate decision
    valid_decisions = [d.value for d in ReviewDecision]
    if request.decision not in valid_decisions:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid decision. Must be one of: {valid_decisions}"
        )

    # Get execution
    query = (
        select(ComplianceExecution)
        .where(and_(
            ComplianceExecution.id == execution_id,
            ComplianceExecution.tenant_id == tenant_id
        ))
    )
    result = await db.execute(query)
    execution = result.scalar_one_or_none()

    if not execution:
        raise HTTPException(status_code=404, detail="Execution not found")

    if execution.status != ComplianceStatus.MANUAL_REVIEW.value:
        raise HTTPException(
            status_code=400,
            detail=f"Execution is not in manual_review status. Current status: {execution.status}"
        )

    if execution.reviewed_at:
        raise HTTPException(
            status_code=400,
            detail="Execution has already been reviewed"
        )

    # Submit review
    execution.submit_review(
        reviewer_id=user_id,
        decision=request.decision,
        notes=request.notes
    )

    await db.commit()
    await db.refresh(execution)

    return ReviewResponse(
        execution_id=str(execution.id),
        status=execution.status,
        review_decision=execution.review_decision,
        reviewed_at=execution.reviewed_at.isoformat() if execution.reviewed_at else "",
        message=f"Review submitted successfully. Decision: {request.decision}"
    )


@router.get("/executions", response_model=List[ReviewQueueResponse])
async def list_executions(
    status: Optional[str] = Query(None, description="Filter by status"),
    subject_id: Optional[str] = Query(None, description="Filter by subject ID"),
    template_id: Optional[str] = Query(None, description="Filter by template ID"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    user: AuthUser = Depends(get_current_user)
):
    """
    List all compliance executions with optional filters.
    """
    conditions = [ComplianceExecution.tenant_id == tenant_id]

    if status:
        conditions.append(ComplianceExecution.status == status)
    if subject_id:
        conditions.append(ComplianceExecution.subject_id == subject_id)
    if template_id:
        conditions.append(ComplianceExecution.template_id == template_id)

    query = (
        select(ComplianceExecution)
        .where(and_(*conditions))
        .order_by(desc(ComplianceExecution.created_at))
        .offset(offset)
        .limit(limit)
    )

    result = await db.execute(query)
    executions = result.scalars().all()

    return [
        ReviewQueueResponse(
            id=str(e.id),
            template_id=e.template_id,
            template_name=e.template_name,
            subject_id=e.subject_id,
            subject_type=e.subject_type,
            subject_name=e.subject_name,
            status=e.status,
            risk_score=float(e.risk_score) if e.risk_score else None,
            risk_level=e.risk_level,
            recommendation=e.recommendation,
            created_at=e.created_at.isoformat() if e.created_at else "",
            needs_review=e.needs_review
        )
        for e in executions
    ]


@router.get("/executions/{execution_id}")
async def get_execution(
    execution_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    user: AuthUser = Depends(get_current_user)
):
    """
    Get detailed information about a specific compliance execution.
    """
    query = (
        select(ComplianceExecution)
        .where(and_(
            ComplianceExecution.id == execution_id,
            ComplianceExecution.tenant_id == tenant_id
        ))
    )
    result = await db.execute(query)
    execution = result.scalar_one_or_none()

    if not execution:
        raise HTTPException(status_code=404, detail="Execution not found")

    return execution.to_dict()
