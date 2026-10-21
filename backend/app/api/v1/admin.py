"""Org Admin Dashboard API endpoints (Phase D).

Provides org-level overview metrics, agent fleet management,
and per-agent permission controls.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.logging import get_logger
from app.services.org_metrics import OrgMetricsService
from app.schemas.admin import (
    OrgOverviewResponse,
    AgentFleetResponse,
    AgentDetailResponse,
    PermissionGrantRequest,
    PermissionResponse,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


def _get_tenant_id(request: Request) -> UUID:
    """Extract tenant_id from request or raise 401."""
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Tenant context required")
    return UUID(str(tenant_id))


# ============================================================================
# Org Overview
# ============================================================================

@router.get("/overview", response_model=OrgOverviewResponse)
async def get_org_overview(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Get high-level org dashboard metrics."""
    tenant_id = _get_tenant_id(request)
    service = OrgMetricsService(db)
    data = await service.get_overview(tenant_id)
    return OrgOverviewResponse(**data)


# ============================================================================
# Agent Fleet
# ============================================================================

@router.get("/agents", response_model=AgentFleetResponse)
async def get_agent_fleet(
    request: Request,
    sort_by: str = Query("created_at", pattern="^(name|created_at|updated_at)$"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Get paginated agent fleet with feedback stats."""
    tenant_id = _get_tenant_id(request)
    service = OrgMetricsService(db)
    data = await service.get_agent_fleet(
        tenant_id=tenant_id,
        sort_by=sort_by,
        sort_order=sort_order,
        limit=limit,
        offset=offset,
    )
    return AgentFleetResponse(**data)


@router.get("/agents/{agent_id}", response_model=AgentDetailResponse)
async def get_agent_detail(
    agent_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Get detailed metrics for a single agent."""
    tenant_id = _get_tenant_id(request)
    service = OrgMetricsService(db)
    data = await service.get_agent_detail(agent_id, tenant_id)
    if not data:
        raise HTTPException(status_code=404, detail="Agent not found")
    return AgentDetailResponse(**data)


# ============================================================================
# Agent Permissions
# ============================================================================

@router.get("/agents/{agent_id}/permissions", response_model=list[PermissionResponse])
async def get_agent_permissions(
    agent_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Get all permissions for an agent."""
    tenant_id = _get_tenant_id(request)
    service = OrgMetricsService(db)
    perms = await service.get_agent_permissions(agent_id, tenant_id)
    return [PermissionResponse(**p) for p in perms]


@router.post("/permissions", response_model=PermissionResponse, status_code=201)
async def grant_permission(
    data: PermissionGrantRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Grant a team permission on an agent."""
    tenant_id = _get_tenant_id(request)
    service = OrgMetricsService(db)
    try:
        perm = await service.set_agent_permission(
            agent_id=UUID(data.agent_id),
            permission_type=data.permission_type,
            team_id=UUID(data.team_id),
            tenant_id=tenant_id,
        )
        return PermissionResponse(**perm)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/permissions/{permission_id}", status_code=204)
async def revoke_permission(
    permission_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Revoke a specific permission."""
    tenant_id = _get_tenant_id(request)
    service = OrgMetricsService(db)
    deleted = await service.revoke_agent_permission(permission_id, tenant_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Permission not found")
