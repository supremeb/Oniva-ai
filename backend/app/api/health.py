"""
Health Check API - Production readiness endpoints

Provides health checks for load balancers and monitoring.
"""

import logging
from datetime import datetime
from typing import Dict, Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
import redis.asyncio as redis

from app.core.database import get_db
from app.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter()
settings = get_settings()


@router.get("/health")
async def health_check() -> Dict[str, Any]:
    """
    Basic health check - returns OK if service is running.
    Used by load balancers for quick liveness checks.
    """
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "service": "oniva-api"
    }


@router.get("/health/ready")
async def readiness_check(
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """
    Detailed readiness check - verifies all dependencies.
    Used by Kubernetes/orchestrators for readiness probes.
    """
    checks = {}
    overall_status = "healthy"
    
    # Check database
    try:
        result = await db.execute(text("SELECT 1"))
        result.scalar()
        checks["database"] = {"status": "healthy", "latency_ms": 0}
    except Exception as e:
        checks["database"] = {"status": "unhealthy", "error": str(e)}
        overall_status = "unhealthy"
        logger.error(f"Database health check failed: {e}")
    
    # Check Redis
    try:
        redis_client = redis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True
        )
        await redis_client.ping()
        await redis_client.close()
        checks["redis"] = {"status": "healthy"}
    except Exception as e:
        checks["redis"] = {"status": "unhealthy", "error": str(e)}
        overall_status = "degraded"  # Redis failure is not critical
        logger.warning(f"Redis health check failed: {e}")
    
    return {
        "status": overall_status,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "checks": checks,
        "version": getattr(settings, 'APP_VERSION', '1.0.0')
    }


@router.get("/health/live")
async def liveness_check() -> Dict[str, str]:
    """
    Simple liveness check.
    If this fails, the container should be restarted.
    """
    return {"status": "alive"}
