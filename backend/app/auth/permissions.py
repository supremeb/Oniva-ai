"""Permission system for role-based access control.

Implements a simple permission system without database storage.
Permissions are defined in code and checked at runtime.

4-Role System:
- owner: Full access + billing + can delete org
- admin: Manage users, settings, all Member permissions
- member: Create/edit/execute workflows and agents
- viewer: Read-only access to shared resources
"""
from typing import Set, Callable

from fastapi import Depends, HTTPException, status

from app.auth.dependencies import get_current_user
from app.auth.providers.base import AuthUser


# =========================================
# Permission Definitions
# =========================================

ROLE_PERMISSIONS: dict[str, Set[str]] = {
    "owner": {"*"},  # Full access to everything
    "admin": {
        # Workflow permissions
        "workflows:create",
        "workflows:read",
        "workflows:update",
        "workflows:delete",
        "workflows:execute",
        # Agent permissions
        "agents:create",
        "agents:read",
        "agents:update",
        "agents:delete",
        "agents:execute",
        # User management
        "users:read",
        "users:manage",
        # Settings
        "settings:read",
        "settings:manage",
        # Connectors
        "connectors:read",
        "connectors:manage",
        # API Keys
        "api_keys:read",
        "api_keys:manage",
        # Approvals: decide any tool-call approval in the tenant
        "approvals:decide",
    },
    "member": {
        # Workflow permissions
        "workflows:create",
        "workflows:read",
        "workflows:update",
        "workflows:execute",
        "workflows:delete_own",  # Can only delete own workflows
        # Agent permissions
        "agents:create",
        "agents:read",
        "agents:update",
        "agents:execute",
        "agents:delete_own",  # Can only delete own agents
        # Limited settings access
        "settings:read",
        # Connectors: browse the catalog and see own tenant's status (manage is admin+)
        "connectors:read",
        # Limited API key access
        "api_keys:read",
        "api_keys:manage_own",
        # Approvals: decide only approvals the member requested, on member-level tools
        "approvals:decide_own",
    },
    "viewer": {
        # Read-only access
        "workflows:read",
        "agents:read",
        "settings:read",
        "connectors:read",
    },
}


# =========================================
# Permission Checking
# =========================================

def has_permission(role: str, permission: str) -> bool:
    """
    Check if a role has a specific permission.

    Args:
        role: User's role (owner, admin, member, viewer)
        permission: Permission string (e.g., "workflows:create")

    Returns:
        True if role has the permission

    Examples:
        has_permission("owner", "anything") -> True (owner has "*")
        has_permission("admin", "users:manage") -> True
        has_permission("viewer", "workflows:create") -> False
    """
    perms = ROLE_PERMISSIONS.get(role, set())

    # Owner has all permissions
    if "*" in perms:
        return True

    # Direct permission match
    if permission in perms:
        return True

    # Wildcard permission (e.g., "workflows:*" matches "workflows:create")
    resource = permission.split(":")[0]
    if f"{resource}:*" in perms:
        return True

    return False


def get_permissions_for_role(role: str) -> Set[str]:
    """Get all permissions for a role."""
    perms = ROLE_PERMISSIONS.get(role, set())
    if "*" in perms:
        # Expand to all known permissions
        all_perms = set()
        for role_perms in ROLE_PERMISSIONS.values():
            all_perms.update(role_perms)
        all_perms.discard("*")
        return all_perms
    return perms.copy()


# =========================================
# FastAPI Dependencies
# =========================================

def require_permission(permission: str):
    """
    FastAPI dependency that checks for a specific permission.

    Usage:
        @router.post("/workflows")
        async def create_workflow(
            user: AuthUser = Depends(require_permission("workflows:create"))
        ):
            ...
    """
    async def permission_checker(user: AuthUser = Depends(get_current_user)) -> AuthUser:
        if not has_permission(user.role, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission denied. Required: {permission}"
            )
        return user

    return permission_checker


def require_any_permission(*permissions: str):
    """
    FastAPI dependency that checks for any of the specified permissions.

    Usage:
        @router.get("/workflows")
        async def list_workflows(
            user: AuthUser = Depends(require_any_permission("workflows:read", "workflows:create"))
        ):
            ...
    """
    async def permission_checker(user: AuthUser = Depends(get_current_user)) -> AuthUser:
        for permission in permissions:
            if has_permission(user.role, permission):
                return user

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Permission denied. Required one of: {', '.join(permissions)}"
        )

    return permission_checker


def require_permission_or_own(
    permission: str,
    resource_loader: Callable,
    owner_field: str = "created_by",
):
    """
    FastAPI dependency: check full permission OR _own permission + ownership.

    If the user's role grants the base permission (e.g. "workflows:delete"),
    access is allowed. Otherwise, checks for the _own variant
    (e.g. "workflows:delete_own") and verifies the user owns the resource.

    Usage:
        async def load_workflow(db, resource_id):
            return await db.get(Workflow, resource_id)

        @router.delete("/workflows/{resource_id}")
        async def delete_workflow(
            user: AuthUser = Depends(require_permission_or_own(
                "workflows:delete_own", load_workflow
            ))
        ):
            ...
    """
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.core.database import get_db

    async def dependency(
        resource_id: str,
        user: AuthUser = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> AuthUser:
        base_perm = permission.replace("_own", "")
        if has_permission(user.role, base_perm):
            return user

        if has_permission(user.role, permission):
            resource = await resource_loader(db, resource_id)
            if resource and str(getattr(resource, owner_field, None)) == str(user.id):
                return user

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Permission denied. Required: {permission}",
        )

    return dependency


def require_all_permissions(*permissions: str):
    """
    FastAPI dependency that checks for all specified permissions.

    Usage:
        @router.delete("/workflows/{id}")
        async def delete_workflow(
            user: AuthUser = Depends(require_all_permissions("workflows:delete", "workflows:update"))
        ):
            ...
    """
    async def permission_checker(user: AuthUser = Depends(get_current_user)) -> AuthUser:
        missing = [p for p in permissions if not has_permission(user.role, p)]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission denied. Missing: {', '.join(missing)}"
            )
        return user

    return permission_checker


# =========================================
# Role Hierarchy Helpers
# =========================================

ROLE_HIERARCHY = {
    "owner": 4,
    "admin": 3,
    "member": 2,
    "viewer": 1,
}


def is_higher_role(role1: str, role2: str) -> bool:
    """Check if role1 is higher than role2 in hierarchy."""
    return ROLE_HIERARCHY.get(role1, 0) > ROLE_HIERARCHY.get(role2, 0)


def is_higher_or_equal_role(role1: str, role2: str) -> bool:
    """Check if role1 is higher than or equal to role2 in hierarchy."""
    return ROLE_HIERARCHY.get(role1, 0) >= ROLE_HIERARCHY.get(role2, 0)


def can_manage_role(actor_role: str, target_role: str) -> bool:
    """
    Check if actor can manage users with target role.

    Rules:
    - Owner can manage anyone
    - Admin can manage Member and Viewer only
    - Member and Viewer cannot manage anyone
    """
    if actor_role == "owner":
        return True
    if actor_role == "admin":
        return target_role in ("member", "viewer")
    return False


# =========================================
# Permission Constants for Frontend
# =========================================

# These can be sent to frontend for UI permission checks
PERMISSIONS_LIST = [
    # Workflows
    {"code": "workflows:create", "name": "Create Workflows", "description": "Create new workflows"},
    {"code": "workflows:read", "name": "View Workflows", "description": "View workflows"},
    {"code": "workflows:update", "name": "Edit Workflows", "description": "Edit existing workflows"},
    {"code": "workflows:delete", "name": "Delete Workflows", "description": "Delete any workflow"},
    {"code": "workflows:execute", "name": "Execute Workflows", "description": "Run workflows"},

    # Agents
    {"code": "agents:create", "name": "Create Agents", "description": "Create new agents"},
    {"code": "agents:read", "name": "View Agents", "description": "View agents"},
    {"code": "agents:update", "name": "Edit Agents", "description": "Edit existing agents"},
    {"code": "agents:delete", "name": "Delete Agents", "description": "Delete any agent"},
    {"code": "agents:execute", "name": "Execute Agents", "description": "Run agents"},

    # Users
    {"code": "users:read", "name": "View Users", "description": "View user list"},
    {"code": "users:manage", "name": "Manage Users", "description": "Add, remove, and modify users"},

    # Settings
    {"code": "settings:read", "name": "View Settings", "description": "View organization settings"},
    {"code": "settings:manage", "name": "Manage Settings", "description": "Modify organization settings"},

    # Connectors
    {"code": "connectors:read", "name": "View Connectors", "description": "View connector configurations"},
    {"code": "connectors:manage", "name": "Manage Connectors", "description": "Configure connectors"},

    # API Keys
    {"code": "api_keys:read", "name": "View API Keys", "description": "View API key list"},
    {"code": "api_keys:manage", "name": "Manage API Keys", "description": "Create and delete API keys"},
]


def get_role_permissions_for_frontend(role: str) -> dict:
    """
    Get permissions data formatted for frontend.

    Returns a dict with role info and permissions list.
    """
    perms = get_permissions_for_role(role)
    return {
        "role": role,
        "is_owner": role == "owner",
        "is_admin": role in ("owner", "admin"),
        "can_manage_users": role in ("owner", "admin"),
        "permissions": list(perms),
    }
