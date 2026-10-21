"""FastAPI dependencies for authentication."""
from typing import Optional, List
from uuid import UUID
from fastapi import Depends, HTTPException, Request, status
import ipaddress

from app.auth.providers.base import AuthUser
from app.auth.scopes import check_scope


# Role-to-scope mapping for JWT-authenticated requests.
# Scopes mirror the ROLE_PERMISSIONS hierarchy in permissions.py.
_ROLE_SCOPES = {
    "viewer": {"workflows:read", "agents:read", "connectors:read", "settings:read"},
    "member": {
        "workflows:read", "workflows:write", "workflows:execute",
        "agents:read", "agents:write",
        "connectors:read", "settings:read",
        "jobs:read", "batches:read",
    },
    "admin": {"*:*"},
    "owner": {"*:*"},
}


# Demo user for TESTING ONLY - never auto-authenticate with this
# Use explicit test fixtures or login flows instead
_DEMO_USER_FOR_TESTS = AuthUser(
    id="00000000-0000-0000-0000-000000000001",
    email="admin@demo.local",
    name="Demo Admin",
    role="admin",
    tenant_id="11111111-1111-1111-1111-111111111111",
    tenant_slug="demo",
    auth_provider="stub"
)


async def get_current_user(request: Request) -> AuthUser:
    """
    Get current authenticated user from request state.

    SECURITY: Always requires explicit authentication via valid JWT token.
    Never falls back to demo user - use proper test fixtures for testing.

    Raises:
        HTTPException 401: If not authenticated
    """
    user: Optional[AuthUser] = getattr(request.state, 'user', None)

    if not user:
        # SECURITY FIX: Never auto-authenticate - require explicit token
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


async def get_current_user_id(request: Request) -> UUID:
    """Get current user ID (for backward compatibility)."""
    user = await get_current_user(request)
    return UUID(user.id)


async def get_current_tenant_id(request: Request) -> UUID:
    """Get current tenant ID for database queries."""
    user = await get_current_user(request)
    return UUID(user.tenant_id)


async def get_optional_user(request: Request) -> Optional[AuthUser]:
    """Get current user if authenticated, None otherwise."""
    return getattr(request.state, 'user', None)


def role_satisfies(user_role: str, allowed_roles: tuple) -> bool:
    """True when ``user_role`` is one of ``allowed_roles`` or ranks above the lowest of them.

    Roles form a hierarchy (owner > admin > member > viewer): whatever an admin may do, an owner
    may do, and whatever a member may do, an admin may do. Roles outside the hierarchy only match
    by name.
    """
    if user_role in allowed_roles:
        return True
    from app.auth.permissions import ROLE_HIERARCHY

    ranks = [ROLE_HIERARCHY[r] for r in allowed_roles if r in ROLE_HIERARCHY]
    user_rank = ROLE_HIERARCHY.get(user_role)
    return bool(ranks) and user_rank is not None and user_rank >= min(ranks)


def require_role(*allowed_roles: str):
    """
    Dependency factory for role-based access control.

    The listed roles are a minimum: higher roles pass too (owner > admin > member > viewer),
    so ``require_role("admin")`` admits owners and ``require_role("owner", "admin")`` is the
    same check.

    Usage:
        @router.delete("/", dependencies=[Depends(require_role("admin"))])
        async def delete_something():
            ...
    """
    async def role_checker(user: AuthUser = Depends(get_current_user)):
        if not role_satisfies(user.role, allowed_roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of roles: {', '.join(allowed_roles)}"
            )
        return user
    return role_checker


async def get_current_user_with_query_token(
    request: Request,
) -> AuthUser:
    """
    Get current authenticated user, supporting token in query parameter.

    This is needed for SSE/EventSource connections which cannot send
    Authorization headers. The middleware handles extracting the token
    from query params if present.

    Raises:
        HTTPException 401: If not authenticated
    """
    # The middleware now handles both header and query param tokens
    user: Optional[AuthUser] = getattr(request.state, 'user', None)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


async def get_current_tenant_id_with_query_token(
    request: Request,
) -> UUID:
    """Get current tenant ID, supporting token in query parameter for SSE."""
    user = await get_current_user_with_query_token(request)
    return UUID(user.tenant_id)


def require_feature(flag_name: str):
    """
    Dependency factory for per-tenant feature flag enforcement.

    Returns 403 if the feature flag is disabled for the current tenant.
    Uses the feature_flags service which resolves: tenant override → global → false.

    Usage:
        @router.get("/compliance/reports", dependencies=[Depends(require_feature("ENABLE_COMPLIANCE"))])
        async def get_reports():
            ...
    """
    async def feature_checker(request: Request, user: AuthUser = Depends(get_current_user)):
        from app.services.feature_flags import get_feature
        from app.models.tenant import Tenant
        from sqlalchemy import select

        # Get the tenant object for feature resolution
        db = getattr(request.state, 'db', None)
        if db:
            try:
                async with db.begin_nested():
                    result = await db.execute(
                        select(Tenant).where(Tenant.id == UUID(user.tenant_id))
                    )
                    tenant = result.scalar_one_or_none()
            except Exception:
                tenant = None
        else:
            tenant = None

        if tenant and not get_feature(tenant, flag_name):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Feature '{flag_name}' is not enabled for this tenant"
            )
        # If we can't resolve the tenant, fall through (don't block)
        return user
    return feature_checker


def require_platform_operator():
    """
    Dependency factory for platform operator access.

    Only users with is_platform_operator=True (mapped from User.is_superuser)
    can access cross-tenant management endpoints.

    Usage:
        @router.put("/admin/tenants/{id}/features/{flag}")
        async def toggle_feature(operator = Depends(require_platform_operator())):
            ...
    """
    async def operator_checker(user: AuthUser = Depends(get_current_user)):
        if not user.is_platform_operator:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Platform operator access required"
            )
        return user
    return operator_checker


def require_scope(required_scope: str):
    """
    Dependency factory for scope-based access control with API keys.

    Validates that the current user's API key (if used) has the required scope.
    Works with both JWT tokens (assume all scopes) and API keys (check scopes).

    Usage:
        @router.post("/execute", dependencies=[Depends(require_scope("workflows:execute"))])
        async def execute_workflow():
            ...

    Args:
        required_scope: Scope required for this operation (e.g., "workflows:execute")

    Returns:
        Dependency function that validates scope

    Raises:
        HTTPException 403: If scope not granted
    """
    async def scope_checker(
        request: Request,
        user: AuthUser = Depends(get_current_user)
    ):
        # Check if request has API key scope info
        api_key_scopes = getattr(request.state, 'api_key_scopes', None)

        if api_key_scopes is not None:
            # This request used an API key - check scopes
            if not check_scope(required_scope, api_key_scopes):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Scope '{required_scope}' not granted. API key scopes: {api_key_scopes}"
                )
        else:
            # JWT token request - derive scopes from user's role
            role_scopes = _ROLE_SCOPES.get(user.role, set())
            if not check_scope(required_scope, list(role_scopes)):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Scope '{required_scope}' not granted for role '{user.role}'"
                )

        return user
    return scope_checker


def get_client_ip(request: Request) -> str:
    """Extract client IP from request, handling X-Forwarded-For.

    Args:
        request: FastAPI request

    Returns:
        Client IP address as string
    """
    # Check X-Forwarded-For header (reverse proxy)
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # X-Forwarded-For format: client, proxy1, proxy2
        # Take the first IP (original client)
        return forwarded.split(",")[0].strip()

    # Direct connection
    return request.client.host if request.client else "0.0.0.0"


def is_ip_in_whitelist(client_ip: str, allowed_ips: List[str]) -> bool:
    """Check if client IP is in whitelist (supports CIDR notation).

    Args:
        client_ip: IP address to check
        allowed_ips: List of allowed IPs (can include CIDR ranges)

    Returns:
        True if IP is allowed

    Examples:
        >>> is_ip_in_whitelist("192.168.1.100", ["192.168.1.100"])
        True
        >>> is_ip_in_whitelist("192.168.1.100", ["192.168.1.0/24"])
        True
        >>> is_ip_in_whitelist("10.0.0.1", ["192.168.1.0/24"])
        False
    """
    try:
        client_addr = ipaddress.ip_address(client_ip)

        for allowed in allowed_ips:
            # Check if it's a network (CIDR) or single IP
            if '/' in allowed:
                # CIDR range
                network = ipaddress.ip_network(allowed, strict=False)
                if client_addr in network:
                    return True
            else:
                # Single IP
                if str(client_addr) == allowed:
                    return True

        return False
    except ValueError:
        # Invalid IP format
        return False
