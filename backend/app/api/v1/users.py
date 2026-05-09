"""User management API endpoints.

Provides user listing, role management, and user lifecycle operations.
"""
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_role
from app.auth.providers.base import AuthUser
from app.core.database import get_db
from app.services.user_management import UserManagementService

router = APIRouter()


# =========================================
# Schemas
# =========================================

class UserResponse(BaseModel):
    """User response."""
    id: str
    email: str
    name: Optional[str] = None
    role: str
    is_active: bool
    auth_provider: str
    added_by: Optional[str] = None
    added_at: Optional[str] = None
    created_at: str

    class Config:
        from_attributes = True


class UsersListResponse(BaseModel):
    """List of users."""
    users: List[UserResponse]
    total: int


class ChangeRoleRequest(BaseModel):
    """Request to change user role."""
    role: str  # owner, admin, member, viewer


class UpdateUserRequest(BaseModel):
    """Request to update user profile."""
    name: Optional[str] = None


class ResetPasswordRequest(BaseModel):
    """Request for admin to reset user password."""
    new_password: str


class ChangePasswordRequest(BaseModel):
    """Request for user to change their own password."""
    current_password: str
    new_password: str


# =========================================
# User List Endpoints
# =========================================

@router.get(
    "",
    response_model=UsersListResponse,
    summary="List users",
    description="List all users in the organization (admin only)"
)
async def list_users(
    request: Request,
    search: Optional[str] = None,
    role: Optional[str] = None,
    is_active: Optional[bool] = None,
    limit: int = 50,
    offset: int = 0,
    user: AuthUser = Depends(require_role("owner", "admin")),
    db: AsyncSession = Depends(get_db)
):
    """List users with optional filtering."""
    service = UserManagementService(db)
    tenant_id = UUID(user.tenant_id)

    users, total = await service.list_users(
        tenant_id=tenant_id,
        search=search,
        role=role,
        is_active=is_active,
        limit=limit,
        offset=offset
    )

    return UsersListResponse(
        users=[
            UserResponse(
                id=str(u.id),
                email=u.email,
                name=u.name,
                role=u.role,
                is_active=u.is_active,
                auth_provider=u.auth_provider,
                added_by=str(u.added_by) if u.added_by else None,
                added_at=u.added_at.isoformat() if u.added_at else None,
                created_at=u.created_at.isoformat() if u.created_at else None
            )
            for u in users
        ],
        total=total
    )


@router.get(
    "/{user_id}",
    response_model=UserResponse,
    summary="Get user details",
    description="Get a specific user's details (admin only)"
)
async def get_user(
    request: Request,
    user_id: UUID,
    user: AuthUser = Depends(require_role("owner", "admin")),
    db: AsyncSession = Depends(get_db)
):
    """Get user details by ID."""
    service = UserManagementService(db)
    tenant_id = UUID(user.tenant_id)

    target_user = await service.get_user_by_id(user_id, tenant_id)
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    return UserResponse(
        id=str(target_user.id),
        email=target_user.email,
        name=target_user.name,
        role=target_user.role,
        is_active=target_user.is_active,
        auth_provider=target_user.auth_provider,
        added_by=str(target_user.added_by) if target_user.added_by else None,
        added_at=target_user.added_at.isoformat() if target_user.added_at else None,
        created_at=target_user.created_at.isoformat() if target_user.created_at else None
    )


# =========================================
# User Update Endpoints
# =========================================

@router.patch(
    "/{user_id}",
    response_model=UserResponse,
    summary="Update user",
    description="Update user profile (admin only for now)"
)
async def update_user(
    request: Request,
    user_id: UUID,
    body: UpdateUserRequest,
    user: AuthUser = Depends(require_role("owner", "admin")),
    db: AsyncSession = Depends(get_db)
):
    """Update user profile information."""
    service = UserManagementService(db)
    tenant_id = UUID(user.tenant_id)

    try:
        updated_user = await service.update_user(
            user_id=user_id,
            tenant_id=tenant_id,
            name=body.name
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

    return UserResponse(
        id=str(updated_user.id),
        email=updated_user.email,
        name=updated_user.name,
        role=updated_user.role,
        is_active=updated_user.is_active,
        auth_provider=updated_user.auth_provider,
        added_by=str(updated_user.added_by) if updated_user.added_by else None,
        added_at=updated_user.added_at.isoformat() if updated_user.added_at else None,
        created_at=updated_user.created_at.isoformat() if updated_user.created_at else None
    )


# =========================================
# Role Management
# =========================================

@router.post(
    "/{user_id}/role",
    response_model=UserResponse,
    summary="Change user role",
    description="Change a user's role (admin only)"
)
async def change_user_role(
    request: Request,
    user_id: UUID,
    body: ChangeRoleRequest,
    user: AuthUser = Depends(require_role("owner", "admin")),
    db: AsyncSession = Depends(get_db)
):
    """
    Change a user's role.

    - Owner can change anyone's role
    - Admin can only change Member/Viewer roles
    """
    service = UserManagementService(db)
    tenant_id = UUID(user.tenant_id)

    # Validate role
    if body.role not in ('owner', 'admin', 'member', 'viewer'):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid role. Must be owner, admin, member, or viewer"
        )

    try:
        updated_user = await service.change_user_role(
            user_id=user_id,
            new_role=body.role,
            changed_by=UUID(user.id),
            tenant_id=tenant_id
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except PermissionError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e)
        )

    return UserResponse(
        id=str(updated_user.id),
        email=updated_user.email,
        name=updated_user.name,
        role=updated_user.role,
        is_active=updated_user.is_active,
        auth_provider=updated_user.auth_provider,
        added_by=str(updated_user.added_by) if updated_user.added_by else None,
        added_at=updated_user.added_at.isoformat() if updated_user.added_at else None,
        created_at=updated_user.created_at.isoformat() if updated_user.created_at else None
    )


# =========================================
# User Activation/Deactivation
# =========================================

@router.post(
    "/{user_id}/deactivate",
    response_model=UserResponse,
    summary="Deactivate user",
    description="Deactivate a user account (admin only)"
)
async def deactivate_user(
    request: Request,
    user_id: UUID,
    user: AuthUser = Depends(require_role("owner", "admin")),
    db: AsyncSession = Depends(get_db)
):
    """Deactivate a user (soft disable)."""
    service = UserManagementService(db)
    tenant_id = UUID(user.tenant_id)

    try:
        updated_user = await service.deactivate_user(
            user_id=user_id,
            deactivated_by=UUID(user.id),
            tenant_id=tenant_id
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except PermissionError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e)
        )

    return UserResponse(
        id=str(updated_user.id),
        email=updated_user.email,
        name=updated_user.name,
        role=updated_user.role,
        is_active=updated_user.is_active,
        auth_provider=updated_user.auth_provider,
        added_by=str(updated_user.added_by) if updated_user.added_by else None,
        added_at=updated_user.added_at.isoformat() if updated_user.added_at else None,
        created_at=updated_user.created_at.isoformat() if updated_user.created_at else None
    )


@router.post(
    "/{user_id}/reactivate",
    response_model=UserResponse,
    summary="Reactivate user",
    description="Reactivate a deactivated user account (admin only)"
)
async def reactivate_user(
    request: Request,
    user_id: UUID,
    user: AuthUser = Depends(require_role("owner", "admin")),
    db: AsyncSession = Depends(get_db)
):
    """Reactivate a deactivated user."""
    service = UserManagementService(db)
    tenant_id = UUID(user.tenant_id)

    try:
        updated_user = await service.reactivate_user(
            user_id=user_id,
            reactivated_by=UUID(user.id),
            tenant_id=tenant_id
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except PermissionError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e)
        )

    return UserResponse(
        id=str(updated_user.id),
        email=updated_user.email,
        name=updated_user.name,
        role=updated_user.role,
        is_active=updated_user.is_active,
        auth_provider=updated_user.auth_provider,
        added_by=str(updated_user.added_by) if updated_user.added_by else None,
        added_at=updated_user.added_at.isoformat() if updated_user.added_at else None,
        created_at=updated_user.created_at.isoformat() if updated_user.created_at else None
    )


# =========================================
# Password Management
# =========================================

@router.post(
    "/{user_id}/reset-password",
    response_model=UserResponse,
    summary="Admin reset password",
    description="Admin resets a user's password (admin only)"
)
async def admin_reset_password(
    request: Request,
    user_id: UUID,
    body: ResetPasswordRequest,
    user: AuthUser = Depends(require_role("owner", "admin")),
    db: AsyncSession = Depends(get_db)
):
    """Admin resets a user's password."""
    service = UserManagementService(db)
    tenant_id = UUID(user.tenant_id)

    try:
        updated_user = await service.admin_reset_password(
            user_id=user_id,
            new_password=body.new_password,
            reset_by=UUID(user.id),
            tenant_id=tenant_id
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except PermissionError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e)
        )

    return UserResponse(
        id=str(updated_user.id),
        email=updated_user.email,
        name=updated_user.name,
        role=updated_user.role,
        is_active=updated_user.is_active,
        auth_provider=updated_user.auth_provider,
        added_by=str(updated_user.added_by) if updated_user.added_by else None,
        added_at=updated_user.added_at.isoformat() if updated_user.added_at else None,
        created_at=updated_user.created_at.isoformat() if updated_user.created_at else None
    )
