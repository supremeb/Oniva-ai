"""Authentication middleware for FastAPI.

SECURITY: This middleware is DENY-BY-DEFAULT (KTD3).
- No auto-authentication in any environment
- Every request needs a valid token unless (method, path) matches PUBLIC_ROUTES
- Tokens are checked against blacklist for revocation
- Unauthenticated requests get 401 here, before any route code runs, so a
  router that forgets its auth dependency is still closed.
"""
import logging
import re
from typing import Optional
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.auth.providers.base import AuthUser
from app.auth.providers.stub import StubAuthProvider
from app.auth.token_blacklist import token_blacklist

logger = logging.getLogger(__name__)

# Initialize auth provider (can be swapped via config)
auth_provider = StubAuthProvider()

# SECURITY: Removed DEV_USER - no auto-authentication
# Use proper test fixtures or login flows for testing


def _is_production() -> bool:
    from app.config import get_settings

    return get_settings().ENVIRONMENT in ("production", "prod")


_SEG = r"[^/]+"

# The ONLY routes reachable without a user token. (methods, anchored path regex)
# Everything else is 401. Keep in sync with PINNED_PUBLIC in
# tests/security/test_auth_default_deny.py.
PUBLIC_ROUTES: list[tuple[frozenset[str], "re.Pattern[str]"]] = [
    (frozenset(methods.split(",")), re.compile(f"^{pattern}$"))
    for methods, pattern in [
        # Liveness / readiness
        ("GET", "/"),
        ("GET", "/health"),
        ("GET", "/health/live"),
        ("GET", "/health/ready"),
        # Auth bootstrap
        ("POST", "/api/v1/auth/login"),
        ("GET", f"/api/v1/auth/login/{_SEG}"),
        ("POST", f"/api/v1/auth/callback/{_SEG}"),
        ("POST", "/api/v1/auth/refresh"),
        ("GET", "/api/v1/auth/providers"),
        ("GET", "/api/v1/auth/stub-users"),  # handler 404s unless DEBUG
        ("GET", f"/api/v1/auth/check-email/{_SEG}"),
        ("POST", "/api/v1/auth/signup"),
        # SAML: authenticated by the signed assertion / IdP flow
        ("GET", "/api/v1/auth/saml/(status|metadata|login|logout|sls)"),
        ("POST", "/api/v1/auth/saml/(acs|sls)"),
        # Onboarding bootstrap (create-org is NOT public)
        ("POST", "/api/v1/onboarding/(check-email|register)"),
        ("GET", f"/api/v1/onboarding/check-slug/{_SEG}"),
        # Machine-authenticated by their own credentials
        ("POST", "/api/v1/oauth/token"),  # client_id/secret
        ("POST", f"/api/v1/webhooks/{_SEG}"),  # per-trigger HMAC / key
        ("POST", "/api/v1/sandbox-proxy/v1/chat/completions"),  # sbx_tok_ proxy token
        ("POST", "/api/v1/csp-report"),  # browser-originated
    ]
]

# Interactive docs: public outside production only.
_DOCS_ROUTES = [
    (frozenset({"GET", "HEAD"}), re.compile(f"^{p}$"))
    for p in ("/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json")
]


def is_public_route(method: str, path: str) -> bool:
    """True if (method, path) may be served without a user token."""
    method = method.upper()
    for methods, rx in PUBLIC_ROUTES:
        if (method in methods or (method == "HEAD" and "GET" in methods)) and rx.match(path):
            return True
    if not _is_production():
        for methods, rx in _DOCS_ROUTES:
            if method in methods and rx.match(path):
                return True
    return False


class AuthMiddleware(BaseHTTPMiddleware):
    """
    Deny-by-default authentication middleware:
    1. Extracts JWT token from Authorization header, cookie, or ?token= (SSE)
    2. Validates token with auth provider and revocation lists
    3. Sets user and tenant context on request.state
    4. Returns 401 if no user resolved and the route is not in PUBLIC_ROUTES
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        method = request.method

        # CORS preflight carries no credentials by design and returns no data.
        if method == "OPTIONS":
            return await call_next(request)

        public = is_public_route(method, path)

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

        # SECURITY: deny by default. Public routes do their own authentication
        # (login, HMAC, proxy token...) or need none.
        if user is None and not public:
            return JSONResponse(
                status_code=401,
                content={"detail": "Authentication required"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Set user context on request state
        request.state.user = user
        request.state.tenant_id = user.tenant_id if user else None
        request.state.token_payload = token_payload  # For logout/revocation

        return await call_next(request)
