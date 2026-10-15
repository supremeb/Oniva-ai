"""Deep Work on the new engine: tenant gate and handler seam (plan 2026-10-15-002, U1).

The routes under /api/v1/deep-work/ keep their URLs. When the tenant flag is on, the legacy
handlers in deep_work.py delegate here; engine-only routes use `require_engine_deep_work`.
The handlers are filled in by U7; until then they answer 501 so a flipped flag never falls
into the legacy executor by accident.
"""
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_tenant_id
from app.core.database import get_db
from app.models.tenant import Tenant
from app.services.feature_flags import engine_deep_work_enabled


async def tenant_uses_engine_deep_work(db: AsyncSession, tenant_id: UUID, request: Request | None = None) -> bool:
    """True when this tenant's Deep Work runs on the engine (evaluated per request, server-side)."""
    tenant = await db.get(Tenant, UUID(str(tenant_id)))
    if tenant is None:
        return False
    settings = getattr(getattr(request, "app", None), "state", None)
    settings = getattr(settings, "engine_deep_work_settings", None)
    return engine_deep_work_enabled(tenant, settings)


async def require_engine_deep_work_for(db: AsyncSession, tenant_id: UUID, request: Request | None = None) -> None:
    """Gate for engine-only routes: 404 unless the tenant has engine Deep Work on."""
    if not await tenant_uses_engine_deep_work(db, tenant_id, request):
        raise HTTPException(status_code=404, detail="Not found")


async def require_engine_deep_work(
    request: Request,
    db: AsyncSession = Depends(get_db),
    tenant_id: UUID = Depends(get_current_tenant_id),
) -> None:
    """FastAPI dependency form of the gate, for engine-only routes (e.g. steer)."""
    await require_engine_deep_work_for(db, tenant_id, request)


def not_implemented() -> HTTPException:
    return HTTPException(status_code=501, detail="Engine Deep Work is not available yet")


async def clarify(*_a, **_k):
    raise not_implemented()


async def plan(*_a, **_k):
    raise not_implemented()


async def start(*_a, **_k):
    raise not_implemented()
