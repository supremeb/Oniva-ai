"""Agent Learning System API endpoints.

3 endpoints:
- GET  /api/v1/agent-learnings       - List learnings (with filters)
- PATCH /api/v1/agent-learnings/{id} - Update status (active/archived)
- DELETE /api/v1/agent-learnings/{id} - Delete learning
"""
import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, get_current_tenant_id, AuthUser
from app.config import get_settings
from app.core.database import get_db
from app.schemas.agent_learning import (
    AgentLearningListResponse,
    AgentLearningResponse,
    AgentLearningUpdate,
)
from app.services.agent_learning import get_agent_learning_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent-learnings")


def _check_feature_enabled() -> None:
    """Guard: raise 404 if agent learning feature is disabled."""
    settings = get_settings()
    if not getattr(settings, 'USE_AGENT_LEARNING', False):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent learning feature is not enabled",
        )


@router.get("", response_model=AgentLearningListResponse)
async def list_learnings(
    agent_id: Optional[UUID] = Query(None, description="Filter by agent ID"),
    learning_status: Optional[str] = Query(None, alias="status", description="Filter by status"),
    pattern_type: Optional[str] = Query(None, description="Filter by pattern type"),
    limit: int = Query(50, ge=1, le=250, description="Max results"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """List agent learnings with optional filters."""
    _check_feature_enabled()
    service = get_agent_learning_service()

    items, total = await service.list_learnings(
        db=db,
        tenant_id=tenant_id,
        agent_id=agent_id,
        status=learning_status,
        pattern_type=pattern_type,
        limit=limit,
        offset=offset,
    )

    return AgentLearningListResponse(
        items=[AgentLearningResponse.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.patch("/{learning_id}", response_model=AgentLearningResponse)
async def update_learning(
    learning_id: UUID,
    update: AgentLearningUpdate,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """Update a learning's status (active/archived)."""
    _check_feature_enabled()
    service = get_agent_learning_service()

    success = await service.update_learning_status(
        db=db,
        tenant_id=tenant_id,
        learning_id=learning_id,
        status=update.status,
    )

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Learning not found",
        )

    # Re-fetch the updated learning
    from sqlalchemy import select
    from app.models.agent_learning import AgentLearning
    result = await db.execute(
        select(AgentLearning).where(
            AgentLearning.id == learning_id,
            AgentLearning.tenant_id == tenant_id,
        )
    )
    learning = result.scalars().first()
    return AgentLearningResponse.model_validate(learning)


@router.delete("/{learning_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_learning(
    learning_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """Delete a learning."""
    _check_feature_enabled()
    service = get_agent_learning_service()

    success = await service.delete_learning(
        db=db,
        tenant_id=tenant_id,
        learning_id=learning_id,
    )

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Learning not found",
        )
