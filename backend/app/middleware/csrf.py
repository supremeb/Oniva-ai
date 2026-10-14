"""CSRF protection middleware using double-submit cookie pattern.

Required because JWT tokens are now stored in httpOnly cookies,
which are automatically sent with every request — making the app
vulnerable to CSRF without this protection.

Pattern: On login, set a non-httpOnly CSRF cookie. The frontend reads
it and sends it back as the X-CSRF-Token header. The middleware
validates that header matches cookie on state-changing requests.
"""
import logging
import secrets

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from app.auth.middleware import is_public_route
from app.config import get_settings

logger = logging.getLogger(__name__)

CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "x-csrf-token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# Paths exempt from CSRF (login sets the cookie, public paths don't need it)
CSRF_EXEMPT_PATHS = {
    "/api/v1/auth/login",
    "/api/v1/auth/signup",
    "/api/v1/auth/callback",
    "/api/v1/auth/providers",
    "/api/v1/auth/stub-users",
    "/api/v1/auth/check-email",
    "/api/v1/auth/refresh",  # Exempt: uses SameSite=strict path-restricted refresh token cookie as CSRF proof
    "/api/v1/onboarding/check-email",
    "/api/v1/onboarding/register",
    "/health",
    "/health/ready",
    "/health/live",
    "/docs",
    "/openapi.json",
    "/redoc",
}

CSRF_EXEMPT_PREFIXES = [
    "/api/v1/auth/check-email/",
    "/api/v1/auth/callback/",
    "/api/v1/auth/login/",
    "/api/v1/onboarding/check-slug/",
    "/docs",
    "/redoc",
]


def generate_csrf_token() -> str:
    """Generate a cryptographically secure CSRF token."""
    return secrets.token_urlsafe(32)


class CSRFMiddleware(BaseHTTPMiddleware):
    """Double-submit cookie CSRF protection.

    Safe methods (GET, HEAD, OPTIONS) are always allowed.
    State-changing methods (POST, PUT, PATCH, DELETE) require
    the X-CSRF-Token header to match the csrf_token cookie.

    API key requests (X-API-Key header) are exempt since they
    don't use cookie-based auth.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        settings = get_settings()

        # Skip CSRF in non-cookie auth flows (API key auth)
        if request.headers.get("x-api-key"):
            return await call_next(request)

        # Skip CSRF for Bearer token auth (direct API clients, not browser)
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            return await call_next(request)

        # Safe methods don't need CSRF validation
        if request.method in SAFE_METHODS:
            return await call_next(request)

        # Check exempt paths
        path = request.url.path
        # Routes on the auth allow-list authenticate themselves (login, HMAC
        # webhooks, OAuth client credentials, SAML, proxy tokens) and carry no
        # ambient cookie authority, so a CSRF token cannot apply to them.
        if is_public_route(request.method, path):
            return await call_next(request)
        if path in CSRF_EXEMPT_PATHS:
            return await call_next(request)
        for prefix in CSRF_EXEMPT_PREFIXES:
            if path.startswith(prefix):
                return await call_next(request)

        # Validate CSRF token: header must match cookie
        cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
        header_token = request.headers.get(CSRF_HEADER_NAME)

        if not cookie_token or not header_token:
            return JSONResponse(
                status_code=403,
                content={"detail": "CSRF token missing"}
            )

        if not secrets.compare_digest(cookie_token, header_token):
            return JSONResponse(
                status_code=403,
                content={"detail": "CSRF token mismatch"}
            )

        return await call_next(request)
