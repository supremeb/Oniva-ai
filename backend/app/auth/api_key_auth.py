"""API Key authentication and validation.

Enterprise feature: API key scoping with granular permissions, IP whitelisting,
and expiration management.
"""
import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import Header, HTTPException, Request, status, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_no_tenant
from app.models.api_key import APIKey
from app.auth.providers.base import AuthUser
from app.auth.dependencies import get_client_ip, is_ip_in_whitelist
from app.core.encryption import get_encryption_service

logger = logging.getLogger(__name__)


def compute_key_hash(api_key_value: str) -> str:
    """Compute SHA-256 hash of an API key for indexed lookup."""
    return hashlib.sha256(api_key_value.encode()).hexdigest()


async def validate_api_key(
    api_key_value: str,
    db: AsyncSession
) -> tuple[APIKey, str]:
    """Validate API key using O(1) SHA-256 hash lookup.

    Falls back to O(n) decrypt-all if key_hash is not yet backfilled.

    Args:
        api_key_value: The raw API key value
        db: Database session

    Returns:
        Tuple of (APIKey model, decrypted_key_value)

    Raises:
        HTTPException: If key is invalid, expired, or inactive
    """
    # O(1) path: lookup by SHA-256 hash
    key_hash = compute_key_hash(api_key_value)
    query = select(APIKey).where(
        APIKey.key_hash == key_hash,
        APIKey.is_active == True
    )
    result = await db.execute(query)
    key_obj = result.scalar_one_or_none()

    if key_obj:
        return key_obj, api_key_value

    # Fallback O(n) path for keys without hashes (pre-backfill).
    # Once backfill is complete this path is never hit.
    encryption_service = get_encryption_service()
    query = select(APIKey).where(
        APIKey.is_active == True,
        APIKey.key_hash.is_(None)
    )
    result = await db.execute(query)
    api_keys = result.scalars().all()

    for key_obj in api_keys:
        try:
            decrypted = encryption_service.decrypt(key_obj.encrypted_key)
            if decrypted == api_key_value:
                # Backfill hash on this key opportunistically
                key_obj.key_hash = key_hash
                await db.commit()
                return key_obj, decrypted
        except Exception:
            continue

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid API key"
    )


async def get_current_user_from_api_key(
    request: Request,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    db: AsyncSession = Depends(get_db_no_tenant)
) -> AuthUser:
    """Validate API key and return AuthUser.

    Checks:
    - API key exists and is active
    - API key hasn't expired
    - Client IP is in whitelist (if configured)

    Sets request.state.api_key_scopes for scope validation.

    Args:
        request: FastAPI request
        x_api_key: API key from X-API-Key header
        db: Database session

    Returns:
        AuthUser populated from API key

    Raises:
        HTTPException 401: Invalid, expired, or inactive key
        HTTPException 403: IP not in whitelist
    """
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key required",
            headers={"WWW-Authenticate": "ApiKey"}
        )

    # Validate and decrypt API key
    key_obj, _ = await validate_api_key(x_api_key, db)

    # Check expiration
    if key_obj.is_expired():
        logger.warning(f"Expired API key used: {key_obj.id}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key has expired"
        )

    # Check IP whitelist
    if key_obj.allowed_ip_addresses:
        client_ip = get_client_ip(request)

        # Convert PostgreSQL INET objects to strings for comparison
        allowed_ips = [str(ip) for ip in key_obj.allowed_ip_addresses]

        if not is_ip_in_whitelist(client_ip, allowed_ips):
            logger.warning(
                f"API key {key_obj.id} used from unauthorized IP: {client_ip}. "
                f"Allowed: {allowed_ips}"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"IP address {client_ip} not in whitelist"
            )

    # Update last used tracking
    key_obj.last_used_at = datetime.now(timezone.utc)
    key_obj.last_used_ip = get_client_ip(request)
    await db.commit()

    # Set API key scopes on request state for scope validation
    request.state.api_key_scopes = key_obj.scopes or []

    # Return AuthUser
    return AuthUser(
        id=str(key_obj.user_id),
        email=f"api_key_{key_obj.id}",  # Placeholder email
        name=key_obj.name,
        role="member",  # API keys get member role by default
        tenant_id=str(key_obj.tenant_id),
        tenant_slug="",  # Not needed for API keys
        auth_provider="api_key"
    )


async def get_current_user_flexible(
    request: Request,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    db: AsyncSession = Depends(get_db_no_tenant)
) -> AuthUser:
    """Get current user from either JWT token or API key.

    Supports two authentication methods:
    1. JWT Bearer token (via Authorization header or query param)
    2. API key (via X-API-Key header)

    Args:
        request: FastAPI request
        x_api_key: Optional API key header
        db: Database session

    Returns:
        AuthUser from either JWT or API key

    Raises:
        HTTPException 401: If no valid authentication
    """
    # Try JWT first (from middleware)
    user = getattr(request.state, 'user', None)
    if user:
        # JWT auth successful
        # Set scopes to None (JWT tokens have all scopes)
        request.state.api_key_scopes = None
        return user

    # Try API key
    if x_api_key:
        return await get_current_user_from_api_key(request, x_api_key, db)

    # No authentication provided
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required (Bearer token or X-API-Key)",
        headers={"WWW-Authenticate": "Bearer, ApiKey"}
    )
