"""Global exception handlers for FastAPI application.

Handles custom exceptions and converts them to standardized HTTP responses.
"""
from fastapi import Request
from fastapi.responses import JSONResponse

from app.exceptions import EntityNotFoundError, PermissionDeniedError, CircuitBreakerOpenError
from app.services.quota_service import QuotaExceededError


async def entity_not_found_handler(request: Request, exc: EntityNotFoundError) -> JSONResponse:
    """Handle entity not found errors → 404 Not Found."""
    return JSONResponse(
        status_code=404,
        content={
            "detail": str(exc),
            "error": "not_found",
            "entity_type": exc.entity_type,
            "entity_id": str(exc.entity_id),
        }
    )


async def permission_denied_handler(request: Request, exc: PermissionDeniedError) -> JSONResponse:
    """Handle RBAC permission denied errors → 403 Forbidden."""
    return JSONResponse(
        status_code=403,
        content={
            "detail": str(exc),
            "error": "permission_denied",
            "operation": exc.operation,
            "required_role": exc.required_role,
        }
    )


async def circuit_breaker_handler(request: Request, exc: CircuitBreakerOpenError) -> JSONResponse:
    """Handle circuit breaker open errors → 503 Service Unavailable."""
    return JSONResponse(
        status_code=503,
        content={
            "detail": f"Agent is degraded: {exc.reason}",
            "error": "circuit_breaker_open",
            "agent_id": str(exc.agent_id),
        },
        headers={"Retry-After": "900"}  # 15 minutes
    )


async def quota_exceeded_handler(request: Request, exc: QuotaExceededError) -> JSONResponse:
    """Handle quota exceeded errors → 429 Too Many Requests."""
    return JSONResponse(
        status_code=429,
        content={
            "error": "quota_exceeded",
            "quota_type": exc.quota_type,
            "current": exc.current,
            "limit": exc.limit,
            "message": exc.message,
        },
    )
