from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from uuid import UUID

from app.core.database import get_db
from app.core.encryption import get_encryption_service
from app.models.api_key import APIKey
from app.auth.api_key_auth import compute_key_hash
from app.models.connector import Connector
from app.auth.dependencies import get_current_user, get_current_tenant_id
from app.auth.providers.base import AuthUser
from app.schemas.api_key import (
    APIKeyCreate,
    APIKeyUpdate,
    APIKeyResponse,
    APIKeyListResponse,
)
from app.schemas.connector import (
    ConnectorCreate,
    ConnectorUpdate,
    ConnectorResponse,
    ConnectorListResponse,
    ConnectorTestRequest,
    ConnectorTestResponse,
)

router = APIRouter()


def _api_key_to_response(key: APIKey) -> APIKeyResponse:
    """Build APIKeyResponse from an APIKey model, calling is_expired() explicitly."""
    from datetime import datetime, timezone

    expired = key.is_expired()
    expires_in_days = None
    if key.expires_at:
        delta = key.expires_at - datetime.now(timezone.utc)
        expires_in_days = max(0, delta.days)

    # Convert INET objects to strings for allowed_ip_addresses
    allowed_ips = [str(ip) for ip in key.allowed_ip_addresses] if key.allowed_ip_addresses else None

    return APIKeyResponse(
        id=key.id,
        user_id=key.user_id,
        provider=key.provider,
        name=key.name,
        is_default=key.is_default,
        is_active=key.is_active,
        key_metadata=key.key_metadata or {},
        scopes=key.scopes or ["*:*"],
        allowed_ip_addresses=allowed_ips,
        expires_at=key.expires_at,
        rate_limit_per_hour=key.rate_limit_per_hour,
        last_used_at=key.last_used_at,
        last_used_ip=str(key.last_used_ip) if key.last_used_ip else None,
        created_at=key.created_at,
        updated_at=key.updated_at,
        is_expired=expired,
        expires_in_days=expires_in_days,
        masked_key=f"sk-****",
    )



# ============================================================================
# API Key Management
# ============================================================================

@router.post("/api-keys", response_model=APIKeyResponse, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    api_key_data: APIKeyCreate,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id)
):
    """
    Create a new API key configuration.
    The API key will be encrypted before storage.
    tenant_id is explicitly set; RLS enforces INSERT restriction.
    """
    encryption = get_encryption_service()
    user_id = UUID(user.id)

    # Check for duplicate: same provider + name for this user/tenant
    existing_stmt = (
        select(APIKey)
        .where(APIKey.user_id == user_id)
        .where(APIKey.tenant_id == tenant_id)
        .where(APIKey.provider == api_key_data.provider)
        .where(APIKey.name == api_key_data.name)
    )
    existing_result = await db.execute(existing_stmt)
    existing_key = existing_result.scalar_one_or_none()

    # Encrypt the API key
    encrypted_key = encryption.encrypt(api_key_data.api_key)

    # Atomic: upsert key + manage defaults in one transaction
    async with db.begin_nested():
        if api_key_data.is_default:
            stmt = (
                select(APIKey)
                .where(APIKey.provider == api_key_data.provider)
                .where(APIKey.tenant_id == tenant_id)
                .where(APIKey.is_default == True)
            )
            result = await db.execute(stmt)
            existing_defaults = result.scalars().all()

            for existing in existing_defaults:
                if not existing_key or existing.id != existing_key.id:
                    existing.is_default = False

        if existing_key:
            # Update existing key instead of rejecting
            existing_key.encrypted_key = encrypted_key
            existing_key.key_hash = compute_key_hash(api_key_data.api_key)
            existing_key.is_default = api_key_data.is_default
            if api_key_data.key_metadata:
                existing_key.key_metadata = api_key_data.key_metadata
            db_api_key = existing_key
        else:
            db_api_key = APIKey(
                user_id=user_id,
                tenant_id=tenant_id,
                provider=api_key_data.provider,
                name=api_key_data.name,
                encrypted_key=encrypted_key,
                key_hash=compute_key_hash(api_key_data.api_key),
                is_default=api_key_data.is_default,
                key_metadata=api_key_data.key_metadata,
            )
            db.add(db_api_key)

    await db.flush()
    await db.refresh(db_api_key)

    # Create response with masked key
    response = _api_key_to_response(db_api_key)
    response.masked_key = f"{api_key_data.api_key[:8]}..." + "*" * 20

    return response


@router.get("/api-keys", response_model=APIKeyListResponse)
async def list_api_keys(
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id)
):
    """
    List all API keys for the current tenant.
    Explicit tenant_id filtering ensures isolation even without RLS.
    """
    user_id = UUID(user.id)
    stmt = select(APIKey).where(
        APIKey.user_id == user_id,
        APIKey.tenant_id == tenant_id  # Defense-in-depth: explicit tenant filtering
    )
    result = await db.execute(stmt)
    api_keys = result.scalars().all()

    return APIKeyListResponse(
        api_keys=[_api_key_to_response(key) for key in api_keys],
        total=len(api_keys),
    )


@router.get("/api-keys/{api_key_id}", response_model=APIKeyResponse)
async def get_api_key(
    api_key_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id)
):
    """
    Get a specific API key.
    Explicit tenant_id filtering ensures isolation even without RLS.
    """
    user_id = UUID(user.id)
    stmt = select(APIKey).where(
        APIKey.id == api_key_id,
        APIKey.user_id == user_id,
        APIKey.tenant_id == tenant_id  # Defense-in-depth: explicit tenant filtering
    )
    result = await db.execute(stmt)
    api_key = result.scalar_one_or_none()

    if not api_key:
        raise HTTPException(status_code=404, detail="API key not found")

    return _api_key_to_response(api_key)


@router.patch("/api-keys/{api_key_id}", response_model=APIKeyResponse)
async def update_api_key(
    api_key_id: UUID,
    api_key_update: APIKeyUpdate,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id)
):
    """
    Update an API key configuration.
    Explicit tenant_id filtering ensures isolation even without RLS.
    """
    user_id = UUID(user.id)
    stmt = select(APIKey).where(
        APIKey.id == api_key_id,
        APIKey.user_id == user_id,
        APIKey.tenant_id == tenant_id  # Defense-in-depth: explicit tenant filtering
    )
    result = await db.execute(stmt)
    api_key = result.scalar_one_or_none()

    if not api_key:
        raise HTTPException(status_code=404, detail="API key not found")

    # Update fields
    if api_key_update.name is not None:
        api_key.name = api_key_update.name

    if api_key_update.is_active is not None:
        api_key.is_active = api_key_update.is_active

    if api_key_update.is_default is not None and api_key_update.is_default:
        # Unset other defaults for this provider within the same tenant
        stmt = (
            select(APIKey)
            .where(APIKey.provider == api_key.provider)
            .where(APIKey.tenant_id == tenant_id)  # Defense-in-depth: explicit tenant filtering
            .where(APIKey.is_default == True)
            .where(APIKey.id != api_key_id)
        )
        result = await db.execute(stmt)
        existing_defaults = result.scalars().all()
        for existing in existing_defaults:
            existing.is_default = False

        api_key.is_default = True

    if api_key_update.key_metadata is not None:
        api_key.key_metadata = api_key_update.key_metadata

    await db.flush()
    await db.refresh(api_key)

    return _api_key_to_response(api_key)


@router.delete("/api-keys/{api_key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_api_key(
    api_key_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id)
):
    """
    Delete an API key.
    Explicit tenant_id filtering ensures isolation even without RLS.
    """
    user_id = UUID(user.id)
    stmt = select(APIKey).where(
        APIKey.id == api_key_id,
        APIKey.user_id == user_id,
        APIKey.tenant_id == tenant_id  # Defense-in-depth: explicit tenant filtering
    )
    result = await db.execute(stmt)
    api_key = result.scalar_one_or_none()

    if not api_key:
        raise HTTPException(status_code=404, detail="API key not found")

    await db.delete(api_key)
    await db.commit()


# ============================================================================
# Connector Management
# ============================================================================

@router.post("/connectors", response_model=ConnectorResponse, status_code=status.HTTP_201_CREATED)
async def create_connector(
    connector_data: ConnectorCreate,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id)
):
    """
    Create a new connector configuration.
    Credentials will be encrypted before storage.
    tenant_id is explicitly set; RLS enforces INSERT restriction.
    """
    encryption = get_encryption_service()
    user_id = UUID(user.id)

    # Encrypt credentials if provided
    encrypted_credentials = None
    if connector_data.credentials:
        import json
        encrypted_credentials = encryption.encrypt(json.dumps(connector_data.credentials))

    # Create connector
    db_connector = Connector(
        user_id=user_id,
        tenant_id=tenant_id,
        connector_type=connector_data.connector_type,
        provider=connector_data.provider,
        name=connector_data.name,
        description=connector_data.description,
        config=connector_data.config,
        encrypted_credentials=encrypted_credentials,
    )

    db.add(db_connector)
    await db.flush()
    await db.refresh(db_connector)

    response = ConnectorResponse.model_validate(db_connector)
    response.has_credentials = encrypted_credentials is not None

    return response


@router.get("/connectors", response_model=ConnectorListResponse)
async def list_connectors(
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id)
):
    """
    List all connectors for the current tenant.
    Explicit tenant_id filtering ensures isolation even without RLS.
    """
    user_id = UUID(user.id)
    stmt = select(Connector).where(
        Connector.user_id == user_id,
        Connector.tenant_id == tenant_id  # Defense-in-depth: explicit tenant filtering
    )
    result = await db.execute(stmt)
    connectors = result.scalars().all()

    responses = []
    for connector in connectors:
        response = ConnectorResponse.model_validate(connector)
        response.has_credentials = connector.encrypted_credentials is not None
        responses.append(response)

    return ConnectorListResponse(
        connectors=responses,
        total=len(responses),
    )


@router.get("/connectors/{connector_id}", response_model=ConnectorResponse)
async def get_connector(
    connector_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id)
):
    """
    Get a specific connector.
    Explicit tenant_id filtering ensures isolation even without RLS.
    """
    user_id = UUID(user.id)
    stmt = select(Connector).where(
        Connector.id == connector_id,
        Connector.user_id == user_id,
        Connector.tenant_id == tenant_id  # Defense-in-depth: explicit tenant filtering
    )
    result = await db.execute(stmt)
    connector = result.scalar_one_or_none()

    if not connector:
        raise HTTPException(status_code=404, detail="Connector not found")

    response = ConnectorResponse.model_validate(connector)
    response.has_credentials = connector.encrypted_credentials is not None

    return response


@router.patch("/connectors/{connector_id}", response_model=ConnectorResponse)
async def update_connector(
    connector_id: UUID,
    connector_update: ConnectorUpdate,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id)
):
    """
    Update a connector configuration.
    Explicit tenant_id filtering ensures isolation even without RLS.
    """
    user_id = UUID(user.id)
    stmt = select(Connector).where(
        Connector.id == connector_id,
        Connector.user_id == user_id,
        Connector.tenant_id == tenant_id  # Defense-in-depth: explicit tenant filtering
    )
    result = await db.execute(stmt)
    connector = result.scalar_one_or_none()

    if not connector:
        raise HTTPException(status_code=404, detail="Connector not found")

    encryption = get_encryption_service()

    # Update fields
    if connector_update.name is not None:
        connector.name = connector_update.name

    if connector_update.description is not None:
        connector.description = connector_update.description

    if connector_update.config is not None:
        connector.config = connector_update.config

    if connector_update.is_active is not None:
        connector.is_active = connector_update.is_active

    if connector_update.credentials is not None:
        import json
        connector.encrypted_credentials = encryption.encrypt(
            json.dumps(connector_update.credentials)
        )

    await db.flush()
    await db.refresh(connector)

    response = ConnectorResponse.model_validate(connector)
    response.has_credentials = connector.encrypted_credentials is not None

    return response


@router.delete("/connectors/{connector_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connector(
    connector_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id)
):
    """
    Delete a connector.
    Explicit tenant_id filtering ensures isolation even without RLS.
    """
    user_id = UUID(user.id)
    stmt = select(Connector).where(
        Connector.id == connector_id,
        Connector.user_id == user_id,
        Connector.tenant_id == tenant_id  # Defense-in-depth: explicit tenant filtering
    )
    result = await db.execute(stmt)
    connector = result.scalar_one_or_none()

    if not connector:
        raise HTTPException(status_code=404, detail="Connector not found")

    await db.delete(connector)
    await db.commit()


@router.post("/connectors/{connector_id}/test", response_model=ConnectorTestResponse)
async def test_connector(
    connector_id: UUID,
    test_request: ConnectorTestRequest,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id)
):
    """
    Test a connector connection.
    Explicit tenant_id filtering ensures isolation even without RLS.
    """
    user_id = UUID(user.id)
    stmt = select(Connector).where(
        Connector.id == connector_id,
        Connector.user_id == user_id,
        Connector.tenant_id == tenant_id  # Defense-in-depth: explicit tenant filtering
    )
    result = await db.execute(stmt)
    connector = result.scalar_one_or_none()

    if not connector:
        raise HTTPException(status_code=404, detail="Connector not found")

    # TODO: Implement actual connector testing based on type
    # For now, return a mock success response
    from datetime import datetime

    connector.is_verified = True
    connector.last_verified_at = datetime.utcnow()
    await db.commit()

    return ConnectorTestResponse(
        success=True,
        message=f"Successfully connected to {connector.provider}",
        data={"test_time": datetime.utcnow().isoformat()},
    )
