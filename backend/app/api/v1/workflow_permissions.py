"""Workflow Permissions API endpoints.

Enterprise feature: Enable workflow collaboration with granular permission
controls. Supports sharing with users, roles, and organization-wide access.

- Phase 3: Collaboration
"""
import logging
import uuid
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Depends, status, Query
from sqlalchemy import select, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.auth.dependencies import get_current_user, get_current_tenant_id
from app.auth.providers.base import AuthUser
from app.models.workflow import Workflow
from app.models.workflow_permission import WorkflowPermission, GranteeType, PermissionLevel
from app.models.user import User
from app.schemas.workflow_permission import (
    WorkflowPermissionCreate,
    WorkflowPermissionUpdate,
    WorkflowPermissionResponse,
    WorkflowPermissionBulkCreate,
    WorkflowPermissionBulkDelete,
    WorkflowShareSummary,
    CheckPermissionRequest,
    CheckPermissionResponse,
    SharedWorkflowResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# --- Helper Functions ---

async def get_workflow_or_404(
    workflow_id: uuid.UUID,
    tenant_id: uuid.UUID,
    db: AsyncSession
) -> Workflow:
    """Get a workflow by ID or raise 404."""
    query = select(Workflow).where(
        Workflow.id == workflow_id,
        Workflow.tenant_id == tenant_id
    )
    result = await db.execute(query)
    workflow = result.scalar_one_or_none()

    if not workflow:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found"
        )
    return workflow


async def check_user_can_share(
    workflow: Workflow,
    user: AuthUser,
    db: AsyncSession
) -> bool:
    """Check if user can share this workflow (owner or admin permission)."""
    # Owner can always share
    if str(workflow.user_id) == user.id:
        return True

    # Admins can share any workflow in their tenant
    if user.role == "admin":
        return True

    # Check for admin permission on workflow
    perm = await get_user_effective_permission(
        workflow.id,
        uuid.UUID(user.id),
        user.role,
        db
    )
    return perm is not None and PermissionLevel.includes(
        PermissionLevel(perm),
        PermissionLevel.ADMIN
    )


async def get_user_effective_permission(
    workflow_id: uuid.UUID,
    user_id: uuid.UUID,
    user_role: str,
    db: AsyncSession
) -> Optional[str]:
    """Get the highest permission level a user has on a workflow.

    Checks in order of precedence:
    1. Direct user permission
    2. Role-based permission
    3. Organization-wide permission

    Returns the highest permission level found, or None if no access.
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)

    # Query all applicable permissions
    query = select(WorkflowPermission).where(
        WorkflowPermission.workflow_id == workflow_id,
        or_(
            # Direct user permission
            and_(
                WorkflowPermission.grantee_type == GranteeType.USER.value,
                WorkflowPermission.grantee_id == str(user_id)
            ),
            # Role permission
            and_(
                WorkflowPermission.grantee_type == GranteeType.ROLE.value,
                WorkflowPermission.grantee_id == user_role
            ),
            # Organization permission
            WorkflowPermission.grantee_type == GranteeType.ORGANIZATION.value
        ),
        # Not expired
        or_(
            WorkflowPermission.expires_at.is_(None),
            WorkflowPermission.expires_at > now
        )
    )

    result = await db.execute(query)
    permissions = result.scalars().all()

    if not permissions:
        return None

    # Find highest permission
    hierarchy = {"view": 0, "execute": 1, "edit": 2, "admin": 3}
    highest = None
    highest_level = -1

    for perm in permissions:
        level = hierarchy.get(perm.permission, -1)
        if level > highest_level:
            highest_level = level
            highest = perm.permission

    return highest


async def enrich_permission_response(
    permission: WorkflowPermission,
    db: AsyncSession
) -> WorkflowPermissionResponse:
    """Enrich a permission with grantee and granter info."""
    response = WorkflowPermissionResponse(
        id=permission.id,
        workflow_id=permission.workflow_id,
        grantee_type=permission.grantee_type,
        grantee_id=permission.grantee_id,
        permission=permission.permission,
        granted_by=permission.granted_by,
        granted_at=permission.granted_at,
        expires_at=permission.expires_at,
        is_expired=permission.is_expired(),
        created_at=permission.created_at
    )

    # Enrich grantee info
    if permission.grantee_type == GranteeType.USER.value:
        try:
            user_query = select(User).where(User.id == uuid.UUID(permission.grantee_id))
            user_result = await db.execute(user_query)
            user = user_result.scalar_one_or_none()
            if user:
                response.grantee_name = user.name or user.email
                response.grantee_email = user.email
        except ValueError:
            pass
    elif permission.grantee_type == GranteeType.ROLE.value:
        response.grantee_name = permission.grantee_id.title()
    elif permission.grantee_type == GranteeType.ORGANIZATION.value:
        response.grantee_name = "Everyone in organization"

    # Enrich granter info
    if permission.granted_by:
        granter_query = select(User).where(User.id == permission.granted_by)
        granter_result = await db.execute(granter_query)
        granter = granter_result.scalar_one_or_none()
        if granter:
            response.granter_name = granter.name or granter.email

    return response


# --- API Endpoints ---

@router.get("/{workflow_id}/permissions", response_model=List[WorkflowPermissionResponse])
async def list_workflow_permissions(
    workflow_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """
    List all permissions for a workflow.

    Requires owner or admin permission on the workflow.
    """
    workflow = await get_workflow_or_404(workflow_id, tenant_id, db)

    # Check access - owner, tenant admin, or has admin permission
    if str(workflow.user_id) != user.id and user.role != "admin":
        effective = await get_user_effective_permission(
            workflow_id, uuid.UUID(user.id), user.role, db
        )
        if not effective or not PermissionLevel.includes(
            PermissionLevel(effective), PermissionLevel.ADMIN
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only workflow owners or admins can view permissions"
            )

    query = select(WorkflowPermission).where(
        WorkflowPermission.workflow_id == workflow_id,
        WorkflowPermission.tenant_id == tenant_id
    ).order_by(WorkflowPermission.granted_at.desc())

    result = await db.execute(query)
    permissions = result.scalars().all()

    return [await enrich_permission_response(p, db) for p in permissions]


@router.post("/{workflow_id}/permissions", response_model=WorkflowPermissionResponse, status_code=status.HTTP_201_CREATED)
async def create_workflow_permission(
    workflow_id: uuid.UUID,
    request: WorkflowPermissionCreate,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """
    Share a workflow with a user, role, or entire organization.

    Permission levels (hierarchy - higher includes lower):
    - view: Read-only access to workflow
    - execute: Can run the workflow
    - edit: Can modify the workflow
    - admin: Full control including sharing
    """
    workflow = await get_workflow_or_404(workflow_id, tenant_id, db)

    # Check if user can share
    if not await check_user_can_share(workflow, user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to share this workflow"
        )

    # Prevent self-share
    if request.grantee_type == "user" and request.grantee_id == user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot share workflow with yourself"
        )

    # Check for existing permission
    existing_query = select(WorkflowPermission).where(
        WorkflowPermission.workflow_id == workflow_id,
        WorkflowPermission.grantee_type == request.grantee_type,
        WorkflowPermission.grantee_id == request.grantee_id
    )
    existing_result = await db.execute(existing_query)
    existing = existing_result.scalar_one_or_none()

    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Permission already exists for this grantee. Use PATCH to update."
        )

    # Validate user exists if sharing with user
    if request.grantee_type == "user":
        user_query = select(User).where(
            User.id == uuid.UUID(request.grantee_id),
            User.tenant_id == tenant_id
        )
        user_result = await db.execute(user_query)
        target_user = user_result.scalar_one_or_none()
        if not target_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )

    # Create permission
    permission = WorkflowPermission(
        tenant_id=tenant_id,
        workflow_id=workflow_id,
        grantee_type=request.grantee_type,
        grantee_id=request.grantee_id,
        permission=request.permission,
        granted_by=uuid.UUID(user.id),
        expires_at=request.expires_at
    )

    db.add(permission)
    await db.commit()
    await db.refresh(permission)

    logger.info(
        f"Workflow {workflow_id} shared with {request.grantee_type}:{request.grantee_id} "
        f"({request.permission}) by user {user.id}"
    )

    return await enrich_permission_response(permission, db)


@router.post("/{workflow_id}/permissions/bulk", response_model=List[WorkflowPermissionResponse], status_code=status.HTTP_201_CREATED)
async def bulk_create_permissions(
    workflow_id: uuid.UUID,
    request: WorkflowPermissionBulkCreate,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """
    Share a workflow with multiple users/roles at once.

    Maximum 50 permissions per request.
    """
    workflow = await get_workflow_or_404(workflow_id, tenant_id, db)

    if not await check_user_can_share(workflow, user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to share this workflow"
        )

    created = []
    for perm_request in request.permissions:
        # Skip self-share
        if perm_request.grantee_type == "user" and perm_request.grantee_id == user.id:
            continue

        # Check for existing
        existing_query = select(WorkflowPermission).where(
            WorkflowPermission.workflow_id == workflow_id,
            WorkflowPermission.grantee_type == perm_request.grantee_type,
            WorkflowPermission.grantee_id == perm_request.grantee_id
        )
        existing_result = await db.execute(existing_query)
        if existing_result.scalar_one_or_none():
            continue  # Skip existing

        permission = WorkflowPermission(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            grantee_type=perm_request.grantee_type,
            grantee_id=perm_request.grantee_id,
            permission=perm_request.permission,
            granted_by=uuid.UUID(user.id),
            expires_at=perm_request.expires_at
        )
        db.add(permission)
        created.append(permission)

    await db.commit()

    # Refresh all created permissions
    for perm in created:
        await db.refresh(perm)

    logger.info(f"Bulk shared workflow {workflow_id} with {len(created)} grantees by user {user.id}")

    return [await enrich_permission_response(p, db) for p in created]


@router.patch("/{workflow_id}/permissions/{permission_id}", response_model=WorkflowPermissionResponse)
async def update_workflow_permission(
    workflow_id: uuid.UUID,
    permission_id: uuid.UUID,
    request: WorkflowPermissionUpdate,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """
    Update a workflow permission.

    Only the permission level and expiration can be updated.
    """
    workflow = await get_workflow_or_404(workflow_id, tenant_id, db)

    if not await check_user_can_share(workflow, user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to manage workflow sharing"
        )

    query = select(WorkflowPermission).where(
        WorkflowPermission.id == permission_id,
        WorkflowPermission.workflow_id == workflow_id,
        WorkflowPermission.tenant_id == tenant_id
    )
    result = await db.execute(query)
    permission = result.scalar_one_or_none()

    if not permission:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Permission not found"
        )

    if request.permission is not None:
        permission.permission = request.permission
    if request.expires_at is not None:
        permission.expires_at = request.expires_at

    await db.commit()
    await db.refresh(permission)

    logger.info(f"Updated permission {permission_id} on workflow {workflow_id} by user {user.id}")

    return await enrich_permission_response(permission, db)


@router.delete("/{workflow_id}/permissions/{permission_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow_permission(
    workflow_id: uuid.UUID,
    permission_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """
    Remove a workflow permission (unshare).
    """
    workflow = await get_workflow_or_404(workflow_id, tenant_id, db)

    if not await check_user_can_share(workflow, user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to manage workflow sharing"
        )

    query = select(WorkflowPermission).where(
        WorkflowPermission.id == permission_id,
        WorkflowPermission.workflow_id == workflow_id,
        WorkflowPermission.tenant_id == tenant_id
    )
    result = await db.execute(query)
    permission = result.scalar_one_or_none()

    if not permission:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Permission not found"
        )

    await db.delete(permission)
    await db.commit()

    logger.info(f"Deleted permission {permission_id} on workflow {workflow_id} by user {user.id}")


@router.delete("/{workflow_id}/permissions/bulk", status_code=status.HTTP_204_NO_CONTENT)
async def bulk_delete_permissions(
    workflow_id: uuid.UUID,
    request: WorkflowPermissionBulkDelete,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """
    Remove multiple workflow permissions at once.
    """
    workflow = await get_workflow_or_404(workflow_id, tenant_id, db)

    if not await check_user_can_share(workflow, user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to manage workflow sharing"
        )

    for perm_id in request.permission_ids:
        query = select(WorkflowPermission).where(
            WorkflowPermission.id == perm_id,
            WorkflowPermission.workflow_id == workflow_id,
            WorkflowPermission.tenant_id == tenant_id
        )
        result = await db.execute(query)
        permission = result.scalar_one_or_none()
        if permission:
            await db.delete(permission)

    await db.commit()

    logger.info(f"Bulk deleted {len(request.permission_ids)} permissions on workflow {workflow_id}")


@router.get("/{workflow_id}/share-summary", response_model=WorkflowShareSummary)
async def get_workflow_share_summary(
    workflow_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """
    Get a summary of who a workflow is shared with.

    Useful for displaying share status in workflow list views.
    """
    workflow = await get_workflow_or_404(workflow_id, tenant_id, db)

    # Check if user can view this workflow
    is_owner = str(workflow.user_id) == user.id
    effective_permission = None
    if not is_owner:
        effective_permission = await get_user_effective_permission(
            workflow_id, uuid.UUID(user.id), user.role, db
        )
        if not effective_permission and user.role != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have access to this workflow"
            )

    # Get permissions
    query = select(WorkflowPermission).where(
        WorkflowPermission.workflow_id == workflow_id,
        WorkflowPermission.tenant_id == tenant_id
    )
    result = await db.execute(query)
    permissions = result.scalars().all()

    # Get owner info
    owner_query = select(User).where(User.id == workflow.user_id)
    owner_result = await db.execute(owner_query)
    owner = owner_result.scalar_one_or_none()

    # Build summary
    org_permission = None
    roles = []
    user_count = 0

    for perm in permissions:
        if perm.is_expired():
            continue
        if perm.grantee_type == GranteeType.ORGANIZATION.value:
            org_permission = perm.permission
        elif perm.grantee_type == GranteeType.ROLE.value:
            roles.append(perm.grantee_id)
        elif perm.grantee_type == GranteeType.USER.value:
            user_count += 1

    return WorkflowShareSummary(
        workflow_id=workflow_id,
        owner_id=workflow.user_id,
        owner_name=owner.name if owner else None,
        is_shared=len(permissions) > 0,
        share_count=len(permissions),
        org_wide=org_permission is not None,
        org_permission=org_permission,
        roles=roles,
        user_count=user_count,
        permissions=[await enrich_permission_response(p, db) for p in permissions] if is_owner or user.role == "admin" else []
    )


@router.post("/{workflow_id}/check-permission", response_model=CheckPermissionResponse)
async def check_permission(
    workflow_id: uuid.UUID,
    request: CheckPermissionRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """
    Check if a user has a specific permission on a workflow.

    Used for permission checks before operations.
    """
    workflow = await get_workflow_or_404(workflow_id, tenant_id, db)

    # Owner has all permissions
    if workflow.user_id == request.user_id:
        return CheckPermissionResponse(
            has_permission=True,
            reason="User is the workflow owner",
            effective_permission="admin",
            grant_source="owner"
        )

    # Get effective permission
    effective = await get_user_effective_permission(
        workflow_id, request.user_id, request.user_role, db
    )

    if not effective:
        return CheckPermissionResponse(
            has_permission=False,
            reason="No permission granted for this workflow",
            effective_permission=None,
            grant_source=None
        )

    # Check if effective permission includes required
    has_perm = PermissionLevel.includes(
        PermissionLevel(effective),
        PermissionLevel(request.required_permission)
    )

    # Determine grant source
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    # Check direct user permission first
    direct_query = select(WorkflowPermission).where(
        WorkflowPermission.workflow_id == workflow_id,
        WorkflowPermission.grantee_type == GranteeType.USER.value,
        WorkflowPermission.grantee_id == str(request.user_id),
        or_(
            WorkflowPermission.expires_at.is_(None),
            WorkflowPermission.expires_at > now
        )
    )
    direct_result = await db.execute(direct_query)
    if direct_result.scalar_one_or_none():
        grant_source = "direct"
    else:
        # Check role
        role_query = select(WorkflowPermission).where(
            WorkflowPermission.workflow_id == workflow_id,
            WorkflowPermission.grantee_type == GranteeType.ROLE.value,
            WorkflowPermission.grantee_id == request.user_role,
            or_(
                WorkflowPermission.expires_at.is_(None),
                WorkflowPermission.expires_at > now
            )
        )
        role_result = await db.execute(role_query)
        if role_result.scalar_one_or_none():
            grant_source = "role"
        else:
            grant_source = "organization"

    return CheckPermissionResponse(
        has_permission=has_perm,
        reason=f"User has {effective} permission via {grant_source}",
        effective_permission=effective,
        grant_source=grant_source
    )


@router.get("/shared-with-me", response_model=List[SharedWorkflowResponse])
async def list_shared_workflows(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """
    List workflows shared with the current user.

    Returns workflows where the user has permissions via:
    - Direct user share
    - Role-based share
    - Organization-wide share

    Excludes workflows the user owns.
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    # Get all workflows shared with this user (direct, role, or org)
    permission_query = select(WorkflowPermission).where(
        WorkflowPermission.tenant_id == tenant_id,
        or_(
            # Direct user permission
            and_(
                WorkflowPermission.grantee_type == GranteeType.USER.value,
                WorkflowPermission.grantee_id == user.id
            ),
            # Role permission
            and_(
                WorkflowPermission.grantee_type == GranteeType.ROLE.value,
                WorkflowPermission.grantee_id == user.role
            ),
            # Organization permission
            WorkflowPermission.grantee_type == GranteeType.ORGANIZATION.value
        ),
        # Not expired
        or_(
            WorkflowPermission.expires_at.is_(None),
            WorkflowPermission.expires_at > now
        )
    )

    result = await db.execute(permission_query)
    permissions = result.scalars().all()

    # Get unique workflow IDs (excluding owned workflows)
    workflow_ids = set()
    permission_map = {}  # workflow_id -> best permission

    for perm in permissions:
        workflow_ids.add(perm.workflow_id)
        # Track highest permission per workflow
        if perm.workflow_id not in permission_map:
            permission_map[perm.workflow_id] = perm
        else:
            existing = permission_map[perm.workflow_id]
            hierarchy = {"view": 0, "execute": 1, "edit": 2, "admin": 3}
            if hierarchy.get(perm.permission, -1) > hierarchy.get(existing.permission, -1):
                permission_map[perm.workflow_id] = perm

    if not workflow_ids:
        return []

    # Get workflows (excluding owned)
    workflows_query = select(Workflow).where(
        Workflow.id.in_(workflow_ids),
        Workflow.tenant_id == tenant_id,
        Workflow.user_id != uuid.UUID(user.id)
    ).offset(skip).limit(limit)

    workflows_result = await db.execute(workflows_query)
    workflows = workflows_result.scalars().all()

    # Build response
    responses = []
    for wf in workflows:
        perm = permission_map.get(wf.id)
        if not perm:
            continue

        # Get owner name
        owner_query = select(User).where(User.id == wf.user_id)
        owner_result = await db.execute(owner_query)
        owner = owner_result.scalar_one_or_none()

        # Determine grant source
        if perm.grantee_type == GranteeType.USER.value:
            grant_source = "direct"
        elif perm.grantee_type == GranteeType.ROLE.value:
            grant_source = "role"
        else:
            grant_source = "organization"

        responses.append(SharedWorkflowResponse(
            id=wf.id,
            name=wf.name,
            description=wf.description,
            owner_id=wf.user_id,
            owner_name=owner.name if owner else None,
            permission=perm.permission,
            grant_source=grant_source,
            shared_at=perm.granted_at,
            expires_at=perm.expires_at
        ))

    return responses
