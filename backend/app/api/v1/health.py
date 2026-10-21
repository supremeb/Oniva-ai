"""
Health Check API - Production readiness endpoints

Phase 2 (P2.2): Enhanced with Redis HA health checks
"""

import logging
from datetime import datetime
from typing import Dict, Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.core.database import get_db
from app.config import get_settings
from app.services.redis_ha import get_redis_service

logger = logging.getLogger(__name__)
router = APIRouter()
settings = get_settings()


@router.get("/health")
async def health_check() -> Dict[str, Any]:
    """
    Basic health check - returns OK if service is running.
    """
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "service": "oniva-api"
    }


@router.get("/health/ready")
async def readiness_check(
    db: AsyncSession = Depends(get_db, scope="function")
) -> Dict[str, Any]:
    """
    Detailed readiness check - verifies database and Redis.
    """
    checks = {}
    overall_status = "healthy"
    
    # Check database
    try:
        result = await db.execute(text("SELECT 1"))
        result.scalar()
        checks["database"] = {"status": "healthy"}
    except Exception as e:
        checks["database"] = {"status": "unhealthy", "error": str(e)}
        overall_status = "unhealthy"
        logger.error(f"Database health check failed: {e}")
    
    # Check Redis (using Redis HA service)
    try:
        redis_service = await get_redis_service()
        redis_status = await redis_service.get_status()

        if redis_status.get("healthy", False) or await redis_service.health_check():
            checks["redis"] = {
                "status": "healthy",
                "mode": "sentinel" if redis_status.get("is_sentinel_mode") else "single",
            }

            # Add circuit breaker status if available
            if "circuit_breaker" in redis_status:
                cb_state = redis_status["circuit_breaker"]["state"]
                checks["redis"]["circuit_breaker"] = cb_state
                if cb_state == "open":
                    checks["redis"]["status"] = "degraded"
                    if overall_status == "healthy":
                        overall_status = "degraded"
        else:
            checks["redis"] = {
                "status": "degraded",
                "circuit_breaker": redis_status.get("circuit_breaker", {}).get("state", "unknown"),
            }
            if overall_status == "healthy":
                overall_status = "degraded"

    except Exception as e:
        checks["redis"] = {"status": "degraded", "error": str(e)}
        if overall_status == "healthy":
            overall_status = "degraded"
        logger.warning(f"Redis health check failed: {e}")
    
    # Check Scheduler Daemon
    try:
        from app.services.workflow_scheduler import get_scheduler_daemon
        daemon = get_scheduler_daemon()
        if daemon.is_running:
            checks["scheduler"] = daemon.status
        else:
            checks["scheduler"] = {"status": "not_running"}
    except Exception as e:
        checks["scheduler"] = {"status": "error", "error": str(e)}

    return {
        "status": overall_status,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "checks": checks
    }


@router.get("/health/live")
async def liveness_check() -> Dict[str, str]:
    """Simple liveness check for container orchestration."""
    return {"status": "alive"}
