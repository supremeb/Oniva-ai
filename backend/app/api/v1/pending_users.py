"""Pending users API endpoints.

Admin can add users who will then be able to sign up.
"""
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_role
from app.auth.providers.base import AuthUser
from app.core.database import get_db
from app.models.tenant import Tenant
from app.services.user_management import UserManagementService

router = APIRouter()


# =========================================
# Schemas
# =========================================

class AddPendingUserRequest(BaseModel):
    """Request to add a pending user."""
    email: EmailStr
    role: str = "member"  # admin, member, viewer


class PendingUserResponse(BaseModel):
    """Pending user response."""
    id: str
    email: str
    role: str
    added_by: Optional[str] = None
    created_at: str

    class Config:
        from_attributes = True


class PendingUsersListResponse(BaseModel):
    """List of pending users."""
    pending_users: List[PendingUserResponse]
    total: int


# =========================================
# Endpoints
# =========================================

@router.get(
    "",
    response_model=PendingUsersListResponse,
    summary="List pending users",
    description="List all pending users for the organization (admin only)"
)
async def list_pending_users(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    user: AuthUser = Depends(require_role("owner", "admin")),
    db: AsyncSession = Depends(get_db)
):
    """List pending users who haven't completed signup yet."""
    service = UserManagementService(db)
    tenant_id = UUID(user.tenant_id)

    pending_users, total = await service.list_pending_users(
        tenant_id=tenant_id,
        limit=limit,
        offset=offset
    )

    return PendingUsersListResponse(
        pending_users=[
            PendingUserResponse(
                id=str(pu.id),
                email=pu.email,
                role=pu.role,
                added_by=str(pu.added_by) if pu.added_by else None,
                created_at=pu.created_at.isoformat() if pu.created_at else None
            )
            for pu in pending_users
        ],
        total=total
    )


@router.post(
    "",
    response_model=PendingUserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add pending user",
    description="Add a user to the organization who can then sign up (admin only)"
)
async def add_pending_user(
    request: Request,
    body: AddPendingUserRequest,
    user: AuthUser = Depends(require_role("owner", "admin")),
    db: AsyncSession = Depends(get_db)
):
    """
    Add a user email to the organization's pending list.

    The user will then be able to sign up at /signup with this email.
    """
    service = UserManagementService(db)
    tenant_id = UUID(user.tenant_id)

    # Validate role
    if body.role not in ('admin', 'member', 'viewer'):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid role. Must be admin, member, or viewer"
        )

    # Admin cannot add other admins
    if user.role == 'admin' and body.role == 'admin':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admins cannot add other admin users"
        )

    # Get tenant name for email
    from sqlalchemy import select
    tenant_result = await db.execute(
        select(Tenant).where(Tenant.id == tenant_id)
    )
    tenant = tenant_result.scalars().first()

    try:
        pending_user = await service.add_pending_user(
            tenant_id=tenant_id,
            email=body.email,
            role=body.role,
            added_by=UUID(user.id),
            org_name=tenant.name if tenant else None,
            inviter_name=user.name
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

    return PendingUserResponse(
        id=str(pending_user.id),
        email=pending_user.email,
        role=pending_user.role,
        added_by=str(pending_user.added_by) if pending_user.added_by else None,
        created_at=pending_user.created_at.isoformat() if pending_user.created_at else None
    )


@router.delete(
    "/{pending_user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove pending user",
    description="Remove a user from the pending list (admin only)"
)
async def delete_pending_user(
    request: Request,
    pending_user_id: UUID,
    user: AuthUser = Depends(require_role("owner", "admin")),
    db: AsyncSession = Depends(get_db)
):
    """Remove a pending user before they sign up."""
    service = UserManagementService(db)
    tenant_id = UUID(user.tenant_id)

    success = await service.delete_pending_user(
        pending_user_id=pending_user_id,
        tenant_id=tenant_id
    )

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pending user not found"
        )

    return None
