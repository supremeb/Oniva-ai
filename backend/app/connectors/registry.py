
from typing import Dict, Type, List, Optional, Any
from .base import BaseConnector, ConnectorMetadata
import pkgutil
import importlib
import inspect
import logging
import app.connectors

logger = logging.getLogger(__name__)


class ConnectorRegistry:
    """
    Auto-discovers and manages connectors.
    Follows plugin pattern for easy extensibility.
    """

    _connectors: Dict[str, Type[BaseConnector]] = {}
    _instances: Dict[str, BaseConnector] = {} # In-memory cache of authenticated instances? Or just instances.

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
        """Get or create connector instance. Note: Does not handle auth here, just instantiation."""
        if connector_id in cls._instances:
            return cls._instances[connector_id]

        connector_cls = cls._connectors.get(connector_id)
        if connector_cls:
            instance = connector_cls()
            cls._instances[connector_id] = instance
            return instance
        return None

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
            instance = cls._instances.get(cid)
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
