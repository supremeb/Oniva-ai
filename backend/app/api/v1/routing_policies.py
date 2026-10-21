"""Routing Policies CRUD API — admin-configured task type → model mapping."""
import logging
import uuid
from typing import List

from fastapi import APIRouter, HTTPException, Depends, status
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.auth.dependencies import get_current_user, get_current_tenant_id, require_role
from app.auth.providers.base import AuthUser
from app.models.routing_policy import RoutingPolicy
from app.schemas.routing_policy import (
    RoutingPolicyCreate,
    RoutingPolicyUpdate,
    RoutingPolicyResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Default policies created by reset-defaults
_DEFAULT_POLICIES = [
    {"name": "Research — mid tier", "task_type": "research", "model_id": "auto", "provider": "auto", "priority": 0},
    {"name": "Analysis — mid tier", "task_type": "analysis", "model_id": "auto", "provider": "auto", "priority": 0},
    {"name": "Code Generation — mid tier", "task_type": "code_gen", "model_id": "auto", "provider": "auto", "priority": 0},
    {"name": "Extraction — low tier", "task_type": "extraction", "model_id": "auto", "provider": "auto", "priority": 0},
    {"name": "Browser — mid tier", "task_type": "browser", "model_id": "auto", "provider": "auto", "priority": 0},
    {"name": "Synthesis — high tier", "task_type": "synthesis", "model_id": "auto", "provider": "auto", "priority": 0},
]


@router.get(
    "",
    response_model=List[RoutingPolicyResponse],
    dependencies=[Depends(get_current_user)],
)
@router.get("/", include_in_schema=False, response_model=List[RoutingPolicyResponse],
            dependencies=[Depends(get_current_user)])
async def list_routing_policies(
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
):
    """List all routing policies for the current tenant."""
    result = await db.execute(
        select(RoutingPolicy)
        .where(RoutingPolicy.tenant_id == tenant_id)
        .order_by(RoutingPolicy.task_type, RoutingPolicy.priority.desc())
    )
    return result.scalars().all()


@router.post(
    "",
    response_model=RoutingPolicyResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role("admin"))],
)
@router.post("/", include_in_schema=False, response_model=RoutingPolicyResponse,
             status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_role("admin"))])
async def create_routing_policy(
    body: RoutingPolicyCreate,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
):
    """Create a new routing policy (admin only)."""
    policy = RoutingPolicy(
        tenant_id=tenant_id,
        **body.model_dump(),
    )
    db.add(policy)
    await db.flush()
    await db.refresh(policy)
    return policy


@router.put(
    "/{policy_id}",
    response_model=RoutingPolicyResponse,
    dependencies=[Depends(require_role("admin"))],
)
async def update_routing_policy(
    policy_id: uuid.UUID,
    body: RoutingPolicyUpdate,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
):
    """Update a routing policy (admin only)."""
    result = await db.execute(
        select(RoutingPolicy).where(
            RoutingPolicy.id == policy_id,
            RoutingPolicy.tenant_id == tenant_id,
        )
    )
    policy = result.scalar_one_or_none()
    if not policy:
        raise HTTPException(status_code=404, detail="Routing policy not found")

    from app.utils.safe_update import safe_update
    from app.schemas.routing_policy import RoutingPolicyUpdate
    updates = body.model_dump(exclude_unset=True)
    safe_update(policy, updates, RoutingPolicyUpdate)
    await db.flush()
    await db.refresh(policy)
    return policy


@router.delete(
    "/{policy_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_role("admin"))],
)
async def delete_routing_policy(
    policy_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
):
    """Delete a routing policy (admin only)."""
    result = await db.execute(
        select(RoutingPolicy).where(
            RoutingPolicy.id == policy_id,
            RoutingPolicy.tenant_id == tenant_id,
        )
    )
    policy = result.scalar_one_or_none()
    if not policy:
        raise HTTPException(status_code=404, detail="Routing policy not found")

    await db.delete(policy)
    await db.flush()


@router.post(
    "/reset-defaults",
    response_model=List[RoutingPolicyResponse],
    dependencies=[Depends(require_role("admin"))],
)
async def reset_defaults(
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
):
    """Delete all policies and recreate factory defaults (admin only)."""
    tid = tenant_id
    await db.execute(
        delete(RoutingPolicy).where(RoutingPolicy.tenant_id == tid)
    )
    policies = []
    for default in _DEFAULT_POLICIES:
        policy = RoutingPolicy(tenant_id=tid, **default)
        db.add(policy)
        policies.append(policy)
    await db.flush()
    for p in policies:
        await db.refresh(p)
    return policies
