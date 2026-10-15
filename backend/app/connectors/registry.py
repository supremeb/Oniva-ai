
from typing import Dict, Type, List, Optional, Any
from .base import BaseConnector, ConnectorMetadata
import pkgutil
import importlib
import inspect
import json
import logging
import uuid
import app.connectors

logger = logging.getLogger(__name__)


class ConnectorRegistry:
    """
    Auto-discovers and manages connectors.
    Follows plugin pattern for easy extensibility.
    """

    # Class registry only. NEVER cache instances: connector instances hold
    # credentials after authenticate(), and a process-global instance would
    # share one tenant's credentials with every other tenant (R2).
    _connectors: Dict[str, Type[BaseConnector]] = {}

    @classmethod
    def auto_discover(cls):
        """Scan connectors directory and register all connectors"""
        package = app.connectors
        path = package.__path__
        prefix = package.__name__ + "."

        for _, name, is_pkg in pkgutil.iter_modules(path, prefix):
            # Only look into packages (subdirectories)
            if is_pkg:
                try:
                    # Import the connector module (convention: connector.py inside the package)
                    # Eg: app.connectors.servicenow.connector
                    # We try importing the specific module if it exists
                    connector_module_name = f"{name}.connector"
                    try:
                        module = importlib.import_module(connector_module_name)
                    except ImportError:
                        # Fallback: maybe the package's __init__ defines it?
                        module = importlib.import_module(name)

                    # Find BaseConnector subclasses
                    for _, obj in inspect.getmembers(module):
                        if (inspect.isclass(obj) and
                            issubclass(obj, BaseConnector) and
                            obj is not BaseConnector):
                            cls.register(obj)

                except Exception as e:
                    print(f"Error loading connector from {name}: {e}")

    @classmethod
    def register(cls, connector_cls: Type[BaseConnector]):
        """Register a connector class"""
        if not hasattr(connector_cls, 'metadata'):
            print(f"Skipping {connector_cls.__name__}: missing metadata")
            return

        metadata = connector_cls.metadata
        if not metadata or not metadata.id:
            return

        cls._connectors[metadata.id] = connector_cls
        print(f"Registered connector: {metadata.name} ({metadata.id})")

    @classmethod
    def get_connector_class(cls, connector_id: str) -> Optional[Type[BaseConnector]]:
        return cls._connectors.get(connector_id)

    @classmethod
    def get_connector(cls, connector_id: str) -> Optional[BaseConnector]:
        """Return a FRESH, unauthenticated instance (metadata / tool schemas only).

        Each call returns a new object. To act on a tenant's behalf use
        get_authenticated_connector(), which binds that tenant's stored
        credentials to a per-call instance.
        """
        connector_cls = cls._connectors.get(connector_id)
        return connector_cls() if connector_cls else None

    @classmethod
    async def get_authenticated_connector(
        cls,
        connector_id: str,
        tenant_id: Any,
        db: Optional[Any] = None,
    ) -> Optional[BaseConnector]:
        """Build a per-call connector authenticated with THIS tenant's stored credentials.

        Returns None if the connector is unknown, the tenant has not connected it
        (connectors with auth_type "none" need no stored credentials), or
        authentication fails. The caller owns the instance and should disconnect() it.
        """
        connector = cls.get_connector(connector_id)
        if connector is None or not tenant_id:
            return None

        if connector.metadata.auth_type == "none":
            stored: Dict[str, Any] = {}  # public/built-in data: no tenant secret exists
        else:
            try:
                loaded = await load_tenant_credentials(connector_id, tenant_id, db=db)
            except ValueError:  # malformed tenant id
                return None
            if loaded is None:
                return None
            stored = loaded
        try:
            ok = await connector.authenticate(stored)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Connector %s authentication failed: %s", connector_id, type(exc).__name__)
            ok = False
        if not ok:
            await _safe_disconnect(connector)
            return None
        return connector

    @classmethod
    def list_available(cls) -> List[ConnectorMetadata]:
        """List all available connectors for UI"""
        return [c.metadata for c in cls._connectors.values()]

    @classmethod
    async def get_tools_for_agent(
        cls,
        connector_ids: List[str],
        tenant_id: str = "",
        user_id: str = "",
        user_session_id: str = "",
    ) -> list:
        """
        Get Pydantic AI Tool objects for the given connectors.

        For SandboxConnector instances, returns 2 tools per connector
        (search + execute) with credentials resolved per-call.
        For standard connectors, returns N tools (one per endpoint).
        """
        from app.connectors.openapi.sandbox_connector import SandboxConnector

        tools: list = []
        for cid in connector_ids:
            instance = cls.get_connector(cid)
            if instance is None:
                continue

            if isinstance(instance, SandboxConnector):
                from app.connectors.openapi.pydantic_ai_bridge import build_pydantic_tools

                credentials = await _resolve_connector_credentials(cid, tenant_id, user_id)
                tools.extend(build_pydantic_tools(
                    connector=instance,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    user_session_id=user_session_id,
                    credentials=credentials,
                ))
            else:
                # Standard connector — return MCP-style tool dicts
                tools.extend(instance.get_tools())
        return tools


async def create_openapi_connector(
    parsed_spec: Any,
    metadata: ConnectorMetadata,
    auth_config: dict[str, Any] | None = None,
) -> BaseConnector:
    """
    Factory: returns SandboxConnector when sandbox mode is enabled,
    otherwise returns standard OpenAPIConnector.
    """
    from app.config import get_settings

    settings = get_settings()
    if settings.USE_SANDBOX_CONNECTORS and settings.ENABLE_SANDBOX:
        from .openapi.sandbox_connector import SandboxConnector
        return SandboxConnector(parsed_spec, metadata, auth_config)
    from .openapi.connector import OpenAPIConnector
    return OpenAPIConnector(parsed_spec.raw_spec, metadata)


async def _resolve_connector_credentials(
    connector_id: str,
    tenant_id: str,
    user_id: str,
) -> dict[str, Any]:
    """Resolve connector credentials from api_keys table."""
    from uuid import UUID
    from sqlalchemy import select
    from app.core.database import AsyncSessionLocal, set_tenant_context
    from app.models.api_key import APIKey
    from app.core.encryption import get_encryption_service

    if not tenant_id or not user_id:
        return {}

    tid = UUID(str(tenant_id)) if not isinstance(tenant_id, UUID) else tenant_id
    uid = UUID(str(user_id)) if not isinstance(user_id, UUID) else user_id

    try:
        async with AsyncSessionLocal() as db:
            await set_tenant_context(db, str(tid))
            result = await db.execute(
                select(APIKey)
                .where(APIKey.tenant_id == tid)
                .where(APIKey.user_id == uid)
                .where(APIKey.provider == connector_id)
                .where(APIKey.is_active.is_(True))
                .limit(1)
            )
            key_row = result.scalar_one_or_none()
            if key_row and key_row.encrypted_key:
                encryption = get_encryption_service()
                decrypted = encryption.decrypt(key_row.encrypted_key)
                return {"token": decrypted}
    except Exception as exc:
        logger.warning("Failed to resolve credentials for connector %s: %s", connector_id, exc)
    return {}


# ---------------------------------------------------------------------------
# Per-tenant credential storage (connectors table, encrypted_credentials)
# ---------------------------------------------------------------------------

def encrypt_credentials(credentials: Dict[str, Any]) -> str:
    from app.core.encryption import get_encryption_service

    return get_encryption_service().encrypt(json.dumps(credentials))


def decrypt_credentials(blob: str) -> Dict[str, Any]:
    from app.core.encryption import get_encryption_service

    raw = get_encryption_service().decrypt(blob)
    return json.loads(raw) if raw else {}


def _as_uuid(value: Any) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


async def _safe_disconnect(connector: BaseConnector) -> None:
    try:
        await connector.disconnect()
    except Exception:  # noqa: BLE001
        pass


async def _native_row(db: Any, connector_id: str, tenant_id: uuid.UUID):
    from sqlalchemy import select
    from app.models.connector import Connector

    result = await db.execute(
        select(Connector)
        .where(Connector.tenant_id == tenant_id)
        .where(Connector.provider == connector_id)
        .where(Connector.connector_type == "native")
        .limit(1)
    )
    return result.scalar_one_or_none()


async def load_tenant_credentials(
    connector_id: str, tenant_id: Any, db: Optional[Any] = None
) -> Optional[Dict[str, Any]]:
    """Decrypted credentials the given tenant stored for a native connector, or None.

    Always filtered by tenant_id in the query (defence in depth on top of RLS).
    With db=None a short-lived tenant-scoped session is opened.
    """
    tid = _as_uuid(tenant_id)

    async def _load(session: Any) -> Optional[Dict[str, Any]]:
        row = await _native_row(session, connector_id, tid)
        if row is None or not row.is_active or not row.encrypted_credentials:
            return None
        return decrypt_credentials(row.encrypted_credentials)

    if db is not None:
        return await _load(db)

    from app.core.database import AsyncSessionLocal, set_tenant_context

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, str(tid))
        return await _load(session)


async def store_tenant_credentials(
    db: Any,
    connector: BaseConnector,
    tenant_id: Any,
    user_id: Any,
    credentials: Dict[str, Any],
) -> None:
    """Upsert the tenant's native connector row with encrypted credentials."""
    from app.models.connector import Connector

    tid, uid = _as_uuid(tenant_id), _as_uuid(user_id)
    meta = connector.metadata
    row = await _native_row(db, meta.id, tid)
    blob = encrypt_credentials(credentials)
    if row is None:
        db.add(Connector(
            tenant_id=tid,
            user_id=uid,
            connector_type="native",
            provider=meta.id,
            name=meta.name,
            description=meta.description,
            config={},
            encrypted_credentials=blob,
            is_active=True,
            is_verified=True,
            source_type="native",
        ))
    else:
        row.encrypted_credentials = blob
        row.is_active = True
        row.is_verified = True
    await db.flush()
