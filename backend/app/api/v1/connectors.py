"""Connector API.

Every route requires authentication (AuthMiddleware denies by default) and an
RBAC permission. Connector state is per tenant: credentials are validated on a
throwaway instance and stored encrypted on the tenant's connector row, never on
a shared object.
"""
import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import require_permission
from app.auth.providers.base import AuthUser
from app.connectors.base import ConnectorMetadata
from app.connectors.registry import ConnectorRegistry, _safe_disconnect, store_tenant_credentials
from app.core.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Connectors"])


class ConnectRequest(BaseModel):
    credentials: Dict[str, Any]


def _connector_or_404(connector_id: str):
    connector = ConnectorRegistry.get_connector(connector_id)
    if connector is None:
        raise HTTPException(status_code=404, detail="Connector not found")
    return connector


@router.get("/", response_model=List[ConnectorMetadata])
async def list_connectors(user: AuthUser = Depends(require_permission("connectors:read"))):
    """List all available connectors (catalog, not tenant state)."""
    return ConnectorRegistry.list_available()


@router.get("/{connector_id}", response_model=ConnectorMetadata)
async def get_connector_details(
    connector_id: str, user: AuthUser = Depends(require_permission("connectors:read"))
):
    connector_cls = ConnectorRegistry.get_connector_class(connector_id)
    if not connector_cls:
        raise HTTPException(status_code=404, detail="Connector not found")
    return connector_cls.metadata


@router.post("/{connector_id}/connect")
async def connect_connector(
    connector_id: str,
    request: ConnectRequest,
    user: AuthUser = Depends(require_permission("connectors:manage")),
    db: AsyncSession = Depends(get_db),
):
    """Validate credentials on a throwaway instance, then store them encrypted
    on the caller's tenant row. Nothing is retained on the registry."""
    connector = _connector_or_404(connector_id)
    try:
        success = await connector.authenticate(request.credentials)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Connector %s authenticate raised %s", connector_id, type(exc).__name__)
        await _safe_disconnect(connector)
        raise HTTPException(status_code=400, detail="Authentication failed")

    try:
        if not success:
            raise HTTPException(status_code=400, detail="Authentication failed")
        await store_tenant_credentials(db, connector, user.tenant_id, user.id, request.credentials)
        await db.commit()
        return {
            "status": "connected",
            "message": f"Successfully connected to {connector.metadata.name}",
        }
    finally:
        await _safe_disconnect(connector)


@router.get("/{connector_id}/status")
async def check_status(
    connector_id: str,
    user: AuthUser = Depends(require_permission("connectors:read")),
    db: AsyncSession = Depends(get_db),
):
    """Health of THIS tenant's connection (never another tenant's)."""
    _connector_or_404(connector_id)
    connector = await ConnectorRegistry.get_authenticated_connector(connector_id, user.tenant_id, db=db)
    if connector is None:
        return {"status": "not_connected"}
    try:
        return await connector.health_check()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Connector %s health check failed: %s", connector_id, type(exc).__name__)
        return {"status": "error", "message": "Health check failed"}
    finally:
        await _safe_disconnect(connector)


@router.get("/{connector_id}/tools")
async def list_tools(
    connector_id: str, user: AuthUser = Depends(require_permission("connectors:read"))
):
    connector = _connector_or_404(connector_id)
    try:
        return connector.get_tools()
    except Exception:  # noqa: BLE001
        logger.exception("Connector %s get_tools failed", connector_id)
        raise HTTPException(status_code=500, detail="Failed to list tools")
