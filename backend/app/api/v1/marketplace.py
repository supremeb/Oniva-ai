"""
Connector Marketplace API

REST endpoints for browsing, enabling, and managing OpenAPI connectors.
"""

import hashlib
import json
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.auth.dependencies import get_current_user, get_current_tenant_id
from app.models.connector import Connector
from app.models.user import User
from app.core.encryption import EncryptionService
from app.schemas.openapi_connector import (
    ConnectorCategory,
    MarketplaceConnector,
    MarketplaceConnectorDetail,
    EnableConnectorRequest,
    EnableConnectorResponse,
    ImportOpenAPIRequest,
    ImportOpenAPIResponse,
    ConnectorOperationsResponse,
    ConnectorOperation,
    TestConnectionRequest,
    TestConnectionResponse,
    EnabledConnector,
    EnabledConnectorsList,
)
from app.connectors.openapi import (
    OpenAPIConnector,
    get_registry_connector,
)
from app.connectors.openapi.registry import (
    list_registry_connectors,
    search_registry,
    get_categories,
)
from app.connectors.openapi.http_client import SafeHTTPClient, SecurityError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/marketplace", tags=["marketplace"])


# ========== Categories ==========

@router.get("/categories", response_model=list[ConnectorCategory])
async def list_categories() -> list[ConnectorCategory]:
    """List all connector categories with counts."""
    categories = get_categories()
    return [ConnectorCategory(**cat) for cat in categories]


# ========== Browse Connectors ==========

@router.get("/connectors", response_model=list[MarketplaceConnector])
async def list_connectors(
    category: str | None = None,
    search: str | None = None,
    db: AsyncSession = Depends(get_db),
    tenant_id: UUID = Depends(get_current_tenant_id),
) -> list[MarketplaceConnector]:
    """
    List connectors from the curated registry.

    Query params:
    - category: Filter by category (e.g., "payments", "crm")
    - search: Search by name or description
    """
    # Get registry connectors
    if search:
        connectors = search_registry(search)
    else:
        connectors = list_registry_connectors(category)

    # Check which are already enabled for this tenant
    enabled_query = select(Connector.registry_id).where(
        Connector.tenant_id == tenant_id,
        Connector.registry_id.isnot(None),
        Connector.is_active == True,
    )
    result = await db.execute(enabled_query)
    enabled_ids = set(row[0] for row in result.fetchall())

    return [
        MarketplaceConnector(
            id=c["id"],
            name=c["name"],
            description=c["description"],
            category=c.get("category", "other"),
            auth_type=c.get("auth_type", "bearer"),
            icon=c.get("icon", "🔌"),
            docs_url=c.get("docs_url"),
            is_enabled=c["id"] in enabled_ids,
        )
        for c in connectors
    ]


@router.get("/connectors/{connector_id}", response_model=MarketplaceConnectorDetail)
async def get_connector_detail(
    connector_id: str,
    db: AsyncSession = Depends(get_db),
    tenant_id: UUID = Depends(get_current_tenant_id),
) -> MarketplaceConnectorDetail:
    """
    Get detailed info about a connector including sample tools.

    Fetches the OpenAPI spec to show available operations.
    """
    registry_entry = get_registry_connector(connector_id)
    if not registry_entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Connector not found: {connector_id}",
        )

    # Check if enabled
    enabled_query = select(Connector).where(
        Connector.tenant_id == tenant_id,
        Connector.registry_id == connector_id,
        Connector.is_active == True,
    )
    result = await db.execute(enabled_query)
    enabled_connector = result.scalar_one_or_none()

    # Get sample tools (first 5)
    sample_tools: list[dict] = []
    operation_count = 0

    if enabled_connector and enabled_connector.openapi_operations:
        # Use cached operations
        operations = enabled_connector.openapi_operations
        operation_count = len(operations)
        sample_tools = operations[:5]
    else:
        # Try to fetch spec for preview (non-blocking failure)
        try:
            client = SafeHTTPClient(timeout=10.0)
            spec = await client.fetch_spec(registry_entry["spec_url"])
            await client.close()

            # Create temporary connector to get tools
            connector = OpenAPIConnector(spec)
            tools = connector.get_tools()
            operation_count = len(tools)
            sample_tools = tools[:5]
        except Exception as e:
            logger.warning(f"Failed to fetch spec for preview: {e}")

    # Determine required credentials based on auth type
    auth_type = registry_entry.get("auth_type", "bearer")
    required_credentials = _get_required_credentials(auth_type)

    return MarketplaceConnectorDetail(
        id=connector_id,
        name=registry_entry["name"],
        description=registry_entry["description"],
        category=registry_entry.get("category", "other"),
        auth_type=auth_type,
        icon=registry_entry.get("icon", "🔌"),
        docs_url=registry_entry.get("docs_url"),
        spec_url=registry_entry.get("spec_url"),
        is_enabled=enabled_connector is not None,
        operation_count=operation_count,
        sample_tools=sample_tools,
        required_credentials=required_credentials,
    )


def _get_required_credentials(auth_type: str) -> list[str]:
    """Get list of required credential fields for auth type."""
    if auth_type == "bearer":
        return ["api_key"]
    elif auth_type == "api_key":
        return ["api_key"]
    elif auth_type == "basic":
        return ["username", "password"]
    elif auth_type == "oauth2":
        return ["access_token"]
    return ["api_key"]


# ========== Enable Connector ==========

@router.post("/connectors/{connector_id}/enable", response_model=EnableConnectorResponse)
async def enable_connector(
    connector_id: str,
    request: EnableConnectorRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
) -> EnableConnectorResponse:
    """
    Enable a connector from the marketplace.

    Fetches the OpenAPI spec, validates credentials, and creates
    a connector instance for the tenant.
    """
    registry_entry = get_registry_connector(connector_id)
    if not registry_entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Connector not found: {connector_id}",
        )

    # Check if already enabled
    existing_query = select(Connector).where(
        Connector.tenant_id == tenant_id,
        Connector.registry_id == connector_id,
        Connector.is_active == True,
    )
    result = await db.execute(existing_query)
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Connector already enabled: {connector_id}",
        )

    # Fetch and parse OpenAPI spec
    try:
        connector = await OpenAPIConnector.from_registry(
            connector_id,
            credentials=request.credentials,
        )
    except SecurityError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Security error: {e}",
        )
    except Exception as e:
        logger.error(f"Failed to create connector: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to fetch connector spec: {e}",
        )

    # Encrypt credentials
    encryption = EncryptionService()
    encrypted_creds = encryption.encrypt(str(request.credentials))

    # Get tools (optionally filtered)
    if request.selected_operations:
        tools = connector.get_tools_for_operations(request.selected_operations)
    else:
        tools = connector.get_tools()

    # Calculate spec hash
    spec_hash = hashlib.sha256(
        str(connector._parsed_spec.raw_spec).encode()
    ).hexdigest()

    # Create connector record
    db_connector = Connector(
        user_id=current_user.id,
        tenant_id=tenant_id,
        connector_type="openapi",
        provider=connector_id,
        name=request.name or registry_entry["name"],
        description=registry_entry["description"],
        config={
            "spec_url": registry_entry["spec_url"],
            "auth_type": registry_entry.get("auth_type", "bearer"),
            "category": registry_entry.get("category", "other"),
            "icon": registry_entry.get("icon", "🔌"),
        },
        encrypted_credentials=encrypted_creds,
        is_active=True,
        is_verified=True,
        source_type="curated",
        registry_id=connector_id,
        openapi_spec_url=registry_entry["spec_url"],
        openapi_spec_version=connector._parsed_spec.openapi_version,
        openapi_spec_hash=spec_hash,
        openapi_operations=tools,
    )

    db.add(db_connector)
    await db.commit()
    await db.refresh(db_connector)

    # Cleanup
    await connector.disconnect()

    logger.info(f"Enabled connector {connector_id} for tenant {tenant_id}")

    return EnableConnectorResponse(
        id=str(db_connector.id),
        connector_id=connector_id,
        name=db_connector.name,
        status="enabled",
        operation_count=len(tools),
        message=f"Connector '{registry_entry['name']}' enabled with {len(tools)} operations",
    )


# ========== Import Custom Spec ==========

@router.post("/import", response_model=ImportOpenAPIResponse)
async def import_custom_spec(
    request: ImportOpenAPIRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
) -> ImportOpenAPIResponse:
    """
    Import a custom OpenAPI spec.

    Can import from URL or inline spec content.
    """
    try:
        if request.spec_url:
            connector = await OpenAPIConnector.from_url(str(request.spec_url))
            spec_url = str(request.spec_url)
        elif request.spec_content:
            connector = OpenAPIConnector(request.spec_content)
            spec_url = None
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Either spec_url or spec_content required",
            )
    except SecurityError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Security error: {e}",
        )
    except Exception as e:
        logger.error(f"Failed to parse spec: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to parse OpenAPI spec: {e}",
        )

    # Get tools
    tools = connector.get_tools()

    # Calculate spec hash
    spec_hash = hashlib.sha256(
        str(connector._parsed_spec.raw_spec).encode()
    ).hexdigest()

    # Create connector record
    db_connector = Connector(
        user_id=current_user.id,
        tenant_id=tenant_id,
        connector_type="openapi",
        provider="custom",
        name=request.name,
        description=connector._parsed_spec.info.description,
        config={
            "auth_type": request.auth_type,
            "auth_location": request.auth_location,
            "auth_header_name": request.auth_header_name,
            "category": request.category,
        },
        is_active=True,
        is_verified=False,
        source_type="custom",
        openapi_spec_url=spec_url,
        openapi_spec_version=connector._parsed_spec.openapi_version,
        openapi_spec_hash=spec_hash,
        openapi_operations=tools,
    )

    db.add(db_connector)
    await db.commit()
    await db.refresh(db_connector)

    logger.info(f"Imported custom connector '{request.name}' for tenant {tenant_id}")

    return ImportOpenAPIResponse(
        id=str(db_connector.id),
        name=request.name,
        description=connector._parsed_spec.info.description,
        operation_count=len(tools),
        status="imported",
        message=f"Custom connector imported with {len(tools)} operations",
    )


# ========== Operations ==========

@router.get("/connectors/{connector_id}/operations", response_model=ConnectorOperationsResponse)
async def get_connector_operations(
    connector_id: str,
    db: AsyncSession = Depends(get_db),
    tenant_id: UUID = Depends(get_current_tenant_id),
) -> ConnectorOperationsResponse:
    """
    List all operations available for a connector.

    Returns cached operations if connector is enabled,
    otherwise fetches from spec.
    """
    # Check if enabled
    query = select(Connector).where(
        Connector.tenant_id == tenant_id,
        Connector.registry_id == connector_id,
        Connector.is_active == True,
    )
    result = await db.execute(query)
    db_connector = result.scalar_one_or_none()

    if db_connector and db_connector.openapi_operations:
        # Use cached operations
        tools = db_connector.openapi_operations
        connector_name = db_connector.name
    else:
        # Fetch from registry
        registry_entry = get_registry_connector(connector_id)
        if not registry_entry:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Connector not found: {connector_id}",
            )

        try:
            client = SafeHTTPClient(timeout=30.0)
            spec = await client.fetch_spec(registry_entry["spec_url"])
            await client.close()

            connector = OpenAPIConnector(spec)
            tools = connector.get_tools()
            connector_name = registry_entry["name"]
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to fetch spec: {e}",
            )

    operations = [
        ConnectorOperation(
            name=tool["name"],
            description=tool.get("description", ""),
            method=tool.get("_meta", {}).get("method", "GET"),
            path=tool.get("_meta", {}).get("path", "/"),
            parameters=list(tool.get("input_schema", {}).get("properties", {}).keys()),
            tags=tool.get("_meta", {}).get("tags", []),
        )
        for tool in tools
    ]

    return ConnectorOperationsResponse(
        connector_id=connector_id,
        connector_name=connector_name,
        operations=operations,
        total_count=len(operations),
    )


# ========== Test Connection ==========

@router.post("/{connector_uuid}/test", response_model=TestConnectionResponse)
async def test_connection(
    connector_uuid: UUID,
    request: TestConnectionRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: UUID = Depends(get_current_tenant_id),
) -> TestConnectionResponse:
    """Test connectivity for an enabled connector."""
    import time

    # Get connector
    query = select(Connector).where(
        Connector.id == connector_uuid,
        Connector.tenant_id == tenant_id,
    )
    result = await db.execute(query)
    db_connector = result.scalar_one_or_none()

    if not db_connector:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Connector not found",
        )

    # Get credentials
    if request.credentials:
        credentials = request.credentials
    elif db_connector.encrypted_credentials:
        encryption = EncryptionService()
        credentials = json.loads(encryption.decrypt(db_connector.encrypted_credentials))
    else:
        return TestConnectionResponse(
            status="unauthenticated",
            message="No credentials configured",
        )

    # Test connection
    start = time.time()
    try:
        if db_connector.registry_id:
            connector = await OpenAPIConnector.from_registry(
                db_connector.registry_id,
                credentials=credentials,
            )
        elif db_connector.openapi_spec_url:
            connector = await OpenAPIConnector.from_url(db_connector.openapi_spec_url)
            await connector.authenticate(credentials)
        else:
            return TestConnectionResponse(
                status="error",
                message="No spec URL configured",
            )

        health = await connector.health_check()
        await connector.disconnect()

        latency = (time.time() - start) * 1000

        # Update verification status
        from datetime import datetime
        db_connector.is_verified = health["status"] == "healthy"
        db_connector.last_verified_at = datetime.utcnow()
        await db.commit()

        return TestConnectionResponse(
            status=health["status"],
            message=health.get("message", ""),
            latency_ms=latency,
        )

    except Exception as e:
        return TestConnectionResponse(
            status="error",
            message=str(e),
        )


# ========== Enabled Connectors ==========

@router.get("/enabled", response_model=EnabledConnectorsList)
async def list_enabled_connectors(
    db: AsyncSession = Depends(get_db),
    tenant_id: UUID = Depends(get_current_tenant_id),
) -> EnabledConnectorsList:
    """List all connectors enabled for the current tenant."""
    query = select(Connector).where(
        Connector.tenant_id == tenant_id,
        Connector.source_type.in_(["curated", "custom"]),
    )
    result = await db.execute(query)
    connectors = result.scalars().all()

    return EnabledConnectorsList(
        connectors=[
            EnabledConnector(
                id=str(c.id),
                connector_id=c.registry_id or c.provider,
                name=c.name,
                description=c.description,
                category=c.config.get("category", "other") if c.config else "other",
                icon=c.config.get("icon", "🔌") if c.config else "🔌",
                auth_type=c.config.get("auth_type", "bearer") if c.config else "bearer",
                source_type=c.source_type,
                operation_count=len(c.openapi_operations) if c.openapi_operations else 0,
                is_active=c.is_active,
                is_verified=c.is_verified,
                last_verified_at=c.last_verified_at,
                created_at=c.created_at,
                updated_at=c.updated_at,
            )
            for c in connectors
        ],
        total_count=len(connectors),
    )


@router.delete("/{connector_uuid}")
async def disable_connector(
    connector_uuid: UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: UUID = Depends(get_current_tenant_id),
) -> dict[str, str]:
    """Disable (soft-delete) a connector."""
    query = select(Connector).where(
        Connector.id == connector_uuid,
        Connector.tenant_id == tenant_id,
    )
    result = await db.execute(query)
    db_connector = result.scalar_one_or_none()

    if not db_connector:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Connector not found",
        )

    db_connector.is_active = False
    await db.commit()

    logger.info(f"Disabled connector {connector_uuid} for tenant {tenant_id}")

    return {"status": "disabled", "message": f"Connector '{db_connector.name}' disabled"}
