"""
Sandbox API endpoints for E2B code execution.

Endpoints:
- POST /api/v1/sandbox/execute - Trigger sandbox execution (creates review ticket)
- GET /api/v1/sandbox/{execution_id} - Get execution status + results
"""

import logging
import uuid
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.auth.dependencies import get_current_tenant_id
from app.models.sandbox_execution import SandboxExecution, SandboxExecutionStatus
from app.schemas.sandbox import SandboxExecuteRequest, SandboxExecutionResponse
from app.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sandbox", tags=["sandbox"])


@router.post("/execute", response_model=SandboxExecutionResponse)
async def execute_sandbox(
    request: SandboxExecuteRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """
    Trigger sandbox execution.

    Creates a review ticket and a sandbox_executions record.
    Returns immediately with pending_review status.
    The node executor handles review polling and actual execution.
    """
    settings = get_settings()

    if not settings.ENABLE_SANDBOX:
        raise HTTPException(
            status_code=400,
            detail="Sandbox execution is disabled. Set ENABLE_SANDBOX=True.",
        )

    if not settings.E2B_API_KEY:
        raise HTTPException(
            status_code=400,
            detail="E2B API key not configured. Set E2B_API_KEY.",
        )

    execution = SandboxExecution(
        id=uuid.uuid4(),
        session_id=request.session_id,
        tenant_id=tenant_id,
        sandbox_id="pending",
        code=request.code,
        status=SandboxExecutionStatus.PENDING_REVIEW.value,
    )

    db.add(execution)
    await db.flush()
    await db.refresh(execution)

    return SandboxExecutionResponse(
        id=str(execution.id),
        session_id=str(execution.session_id),
        tenant_id=str(execution.tenant_id),
        sandbox_id=execution.sandbox_id,
        code=execution.code,
        status=execution.status,
        created_at=execution.created_at,
    )


@router.get("/{execution_id}", response_model=SandboxExecutionResponse)
async def get_execution(
    execution_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """Get sandbox execution status and results."""
    result = await db.execute(
        select(SandboxExecution)
        .where(SandboxExecution.id == execution_id)
        .where(SandboxExecution.tenant_id == tenant_id)
    )
    execution = result.scalar_one_or_none()

    if not execution:
        raise HTTPException(status_code=404, detail="Execution not found")

    return SandboxExecutionResponse(
        id=str(execution.id),
        session_id=str(execution.session_id),
        tenant_id=str(execution.tenant_id),
        sandbox_id=execution.sandbox_id,
        code=execution.code,
        status=execution.status,
        stdout=execution.stdout,
        stderr=execution.stderr,
        duration_seconds=execution.duration_seconds,
        created_at=execution.created_at,
        completed_at=execution.completed_at,
    )
