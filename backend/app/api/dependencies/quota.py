"""Quota enforcement dependency for FastAPI endpoints.

Usage:
    @router.post("/execute")
    async def execute(
        ...,
        _quota=Depends(require_quota(QuotaType.EXECUTIONS)),
    ):
        ...
"""
import logging
from uuid import UUID
from collections.abc import Callable, Awaitable

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.auth.dependencies import get_current_user
from app.auth.providers.base import AuthUser
from app.models.usage_quota import QuotaType
from app.services.quota_service import QuotaService

logger = logging.getLogger(__name__)


def require_quota(
    quota_type: QuotaType,
    estimated_usage: int = 1,
) -> Callable[..., Awaitable[None]]:
    """Create a FastAPI dependency that enforces quota before the handler runs.

    Args:
        quota_type: Which quota to check (EXECUTIONS, API_CALLS, etc.)
        estimated_usage: Estimated cost of this operation

    Returns:
        FastAPI dependency function

    Note: QuotaExceededError propagates to the global exception handler
    registered in main.py (returns HTTP 429). No try/except needed here.

    Note: Both this dependency and the handler may declare Depends(get_db, scope="function").
    FastAPI caches dependencies per-request by default, so both receive the
    SAME AsyncSession instance — quota checks share the handler's transaction
    and tenant RLS context. Do NOT change get_db to use_cache=False.
    """
    async def _check(
        db: AsyncSession = Depends(get_db, scope="function"),
        current_user: AuthUser = Depends(get_current_user),
    ) -> None:
        quota_svc = QuotaService(db)
        await quota_svc.check_and_raise(
            UUID(current_user.tenant_id),
            UUID(current_user.id),
            quota_type,
            estimated_usage,
        )
    return _check
