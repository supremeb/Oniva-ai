"""
Wide Research API endpoints.

Endpoints:
- POST /api/v1/wide-research/execute   - Start wide research
- GET  /api/v1/wide-research/{id}      - Get status + results
- POST /api/v1/wide-research/{id}/abort - Abort running research
"""

import logging
from typing import Dict
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.auth.dependencies import get_current_tenant_id, get_current_user_id
from app.schemas.wide_research import (
    WideResearchExecuteRequest,
    WideResearchResponse,
)
from app.services.wide_research_orchestrator import (
    WideResearchOrchestrator,
    _APPROVAL_THRESHOLD,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/wide-research", tags=["wide-research"])

# In-memory tracking of active orchestrators (keyed by execution_id)
_active_orchestrators: Dict[str, WideResearchOrchestrator] = {}


@router.post("/execute", response_model=WideResearchResponse)
async def execute_wide_research(
    request: WideResearchExecuteRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: UUID = Depends(get_current_tenant_id),
    user_id: UUID = Depends(get_current_user_id),
):
    """
    Start wide research execution.

    Processes items in parallel through a templated agent task.
    For batches > approval threshold, records a review item in the approval ledger.
    """
    if len(request.items) > _APPROVAL_THRESHOLD:
        # Record a review item in the approval ledger for large batches
        try:
            from app.services.approval_service import ApprovalService

            await ApprovalService(db).record_pending(
                tenant_id=tenant_id, conversation_id=None, kind="review", tool_name="Wide research batch",
                reason=f"Wide research: {len(request.items)} items. Batch processing requires human approval.",
                arguments={
                    "item_count": len(request.items),
                    "agent_id": request.agent_id,
                    "session_id": str(request.session_id) if request.session_id else None,
                    "task_template_preview": request.task_template[:200],
                },
                requested_by=user_id, approver_role="admin",
            )
            await db.commit()
            # The batch proceeds; the review item is advisory until output review resumes workflows.
            logger.info(
                f"Wide research batch ({len(request.items)} items) "
                f"exceeds approval threshold ({_APPROVAL_THRESHOLD})"
            )
        except Exception as e:
            logger.warning(f"Failed to record wide research review: {e}")

    orchestrator = WideResearchOrchestrator(db)

    result = await orchestrator.execute(
        task_template=request.task_template,
        items=request.items,
        agent_id=request.agent_id,
        tenant_id=str(tenant_id),
        user_id=str(user_id),
        session_id=request.session_id,
        max_parallel=request.max_parallel,
        timeout_per_item=request.timeout_per_item,
    )

    # Track orchestrator for potential abort
    _active_orchestrators[result.execution_id] = orchestrator

    # Run synthesis if requested
    if request.synthesize and result.completed > 0:
        try:
            synthesis_result = await orchestrator.synthesize(
                result=result,
                tenant_id=str(tenant_id),
                user_id=str(user_id),
            )
            result.synthesis = synthesis_result.get("synthesis")
        except Exception as e:
            logger.warning(f"Synthesis failed: {e}")

    return WideResearchResponse(
        execution_id=result.execution_id,
        status=result.status,
        total_items=result.total_items,
        completed=result.completed,
        failed=result.failed,
        skipped=result.skipped,
        items=[
            {
                "item_index": item.item_index,
                "item": item.item,
                "status": item.status,
                "result": item.result,
                "error": item.error,
                "duration_seconds": item.duration_seconds,
            }
            for item in result.items
        ],
        synthesis=result.synthesis,
        duration_seconds=result.duration_seconds,
    )


@router.get("/{execution_id}", response_model=WideResearchResponse)
async def get_wide_research(
    execution_id: str,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """
    Get wide research status and results.

    Results are stored in workspace JSON files. This endpoint reads
    from the workspace to return the current state.
    """
    try:
        from app.services.session_storage import get_session_storage
        storage = get_session_storage()

        # Search for the results file across sessions
        # In practice, the session_id would be in the request context
        raise HTTPException(
            status_code=501,
            detail="Direct result retrieval not yet implemented. Use workspace API to read results.",
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{execution_id}/abort")
async def abort_wide_research(execution_id: str):
    """Abort a running wide research execution."""
    orchestrator = _active_orchestrators.get(execution_id)
    if not orchestrator:
        raise HTTPException(
            status_code=404,
            detail="Execution not found or already completed",
        )

    orchestrator.abort()
    del _active_orchestrators[execution_id]

    return {"status": "aborted", "execution_id": execution_id}
