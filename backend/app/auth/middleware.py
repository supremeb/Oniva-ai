"""Authentication middleware for FastAPI.

SECURITY: This middleware handles JWT token validation.
- No auto-authentication in any environment
- All non-public paths require valid tokens
- Tokens are checked against blacklist for revocation
"""
import logging
from typing import Optional
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.auth.providers.base import AuthUser
from app.auth.providers.stub import StubAuthProvider
from app.auth.token_blacklist import token_blacklist

logger = logging.getLogger(__name__)

# Initialize auth provider (can be swapped via config)
auth_provider = StubAuthProvider()

# SECURITY: Removed DEV_USER - no auto-authentication
# Use proper test fixtures or login flows for testing


class AuthMiddleware(BaseHTTPMiddleware):
    """
    Authentication middleware that:
    1. Extracts JWT token from Authorization header
    2. Validates token with auth provider
    3. Sets user and tenant context on request.state

    Public paths (no auth required):
    - /health
    - /docs, /openapi.json, /redoc
    - /api/v1/auth/login
    - /api/v1/auth/stub-users (dev only)
    """

    PUBLIC_PATHS = {
        "/",
        "/health",
        "/docs",
        "/openapi.json",
        "/redoc",
        # Auth public endpoints
        "/api/v1/auth/login",
        "/api/v1/auth/providers",
        "/api/v1/auth/stub-users",
        "/api/v1/auth/signup",
        # Onboarding public endpoints
        "/api/v1/onboarding/check-email",
        "/api/v1/onboarding/register",
    }

    PUBLIC_PREFIXES = [
        "/docs",
        "/redoc",
        # Auth check-email: /api/v1/auth/check-email/{email}
        "/api/v1/auth/check-email/",
        # Onboarding slug check: /api/v1/onboarding/check-slug/{slug}
        "/api/v1/onboarding/check-slug/",
    ]

    async def dispatch(self, request: Request, call_next):
        # Skip auth for public paths
        path = request.url.path
        if path in self.PUBLIC_PATHS:
            return await call_next(request)

        # Skip auth for public prefixes
        for prefix in self.PUBLIC_PREFIXES:
            if path.startswith(prefix):
                return await call_next(request)

        # Extract token from (in priority order):
        # 1. Authorization header (API clients, mobile apps)
        # 2. httpOnly cookie (browser sessions)
        # 3. Query parameter (SSE/EventSource fallback)
        auth_header = request.headers.get("Authorization")
        token: Optional[str] = None
        user: Optional[AuthUser] = None
        token_payload: Optional[dict] = None

        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:]  # Remove "Bearer " prefix
        elif "access_token" in request.cookies:
            token = request.cookies.get("access_token")
        elif "token" in request.query_params:
            # Fallback to query parameter for SSE endpoints
            token = request.query_params.get("token")

        if token:
            user, token_payload = await auth_provider.validate_token_with_payload(token)

            # SECURITY: Check if token has been revoked (individual JTI blacklist)
            if user and token_payload:
                jti = token_payload.get("jti")
                if jti and await token_blacklist.is_blacklisted(jti):
                    logger.warning(f"Revoked token used: {jti[:8]}...")
                    user = None  # Treat as unauthenticated

            # SECURITY: Check tokens-invalid-before (bulk user invalidation)
            if user and token_payload:
                user_id = token_payload.get("sub")
                token_iat = token_payload.get("iat")
                if user_id and token_iat and await token_blacklist.is_user_invalidated(user_id, token_iat):
                    logger.warning(f"Token issued before invalidation for user {str(user_id)[:8]}...")
                    user = None  # Treat as unauthenticated

        # SECURITY: No auto-authentication in any environment
        # All non-public endpoints require valid Bearer token

        # Set user context on request state
        request.state.user = user
        request.state.tenant_id = user.tenant_id if user else None
        request.state.token_payload = token_payload  # For logout/revocation

        return await call_next(request)
