"""
Tenant context middleware for PostgreSQL Row-Level Security.

Sets the app.current_tenant session variable based on authenticated user,
enabling automatic tenant isolation via RLS policies.
"""
from typing import Optional
from uuid import UUID
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.logging import get_logger

logger = get_logger(__name__)


class TenantMiddleware(BaseHTTPMiddleware):
    """
    Middleware that sets tenant context for database connections.

    Flow:
    1. Auth middleware sets request.state.user (with tenant_id)
    2. This middleware reads tenant_id from user
    3. Sets request.state.tenant_id for database session dependency
    4. Database sessions use this to set PostgreSQL session variable for RLS

    Configuration:
    - fail_open=True: Allow requests without tenant context (dev mode)
    - fail_open=False: Log warnings for requests without tenant context (prod)
    """

    def __init__(self, app, fail_open: bool = False):
        super().__init__(app)
        self.fail_open = fail_open

    async def dispatch(self, request: Request, call_next):
        # Get tenant_id from authenticated user (set by AuthMiddleware)
        user = getattr(request.state, 'user', None)
        tenant_id: Optional[str] = None

        if user and hasattr(user, 'tenant_id'):
            tenant_id = user.tenant_id

        # Store tenant_id on request state for database session dependency
        request.state.tenant_id = tenant_id

        # Store additional context for audit triggers
        if user:
            client_ip = None
            if request.client:
                client_ip = request.client.host
            request.state.audit_context = {
                "user_id": user.id,
                "user_email": user.email,
                "ip_address": client_ip,
                "user_agent": request.headers.get("user-agent", "")[:500],
            }
        else:
            request.state.audit_context = {}

        if tenant_id:
            logger.debug(f"Tenant context set: {tenant_id} for {request.url.path}")
        elif not self.fail_open:
            # Only log warning for non-public paths (public paths won't have user)
            path = request.url.path
            if not self._is_public_path(path):
                logger.warning(f"Request without tenant context: {path}")

        response = await call_next(request)
        return response

    def _is_public_path(self, path: str) -> bool:
        """Check if path is public (no auth required)."""
        public_paths = {
            "/",
            "/health",
            "/docs",
            "/openapi.json",
            "/redoc",
            "/api/v1/auth/login",
            "/api/v1/auth/stub-users",
        }
        public_prefixes = ["/docs", "/redoc"]

        if path in public_paths:
            return True

        for prefix in public_prefixes:
            if path.startswith(prefix):
                return True

        return False


def validate_tenant_id(tenant_id: str) -> bool:
    """
    Validate tenant_id is a proper UUID to prevent SQL injection.

    Args:
        tenant_id: String that should be a valid UUID

    Returns:
        True if valid UUID, False otherwise
    """
    try:
        UUID(tenant_id)
        return True
    except (ValueError, AttributeError):
        return False
