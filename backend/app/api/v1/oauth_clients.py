"""OAuth Client API endpoints.

Enterprise feature: Machine-to-machine authentication using OAuth 2.0
Client Credentials grant type.

Provides:
- CRUD operations for OAuth clients
- Token endpoint for client_credentials flow
- Scope validation

- Phase 2: API Integration
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import bcrypt
from fastapi import APIRouter, HTTPException, Depends, status, Form, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, get_db_no_tenant
from app.auth.dependencies import get_current_user, get_current_tenant_id
from app.auth.providers.base import AuthUser
from app.models.oauth_client import OAuthClient, generate_client_id, generate_client_secret
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter()


# --- Request/Response Models ---

class OAuthClientCreate(BaseModel):
    """Request to create an OAuth client."""
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    scopes: List[str] = Field(default_factory=list)


class OAuthClientResponse(BaseModel):
    """OAuth client info (excludes secret)."""
    id: uuid.UUID
    client_id: str
    name: str
    description: Optional[str]
    scopes: List[str]
    is_active: bool
    last_used_at: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True


class OAuthClientCreateResponse(OAuthClientResponse):
    """Response when creating client - includes secret (shown only once)."""
    client_secret: str = Field(..., description="Client secret - shown only once!")


class OAuthClientUpdate(BaseModel):
    """Request to update an OAuth client."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    scopes: Optional[List[str]] = None
    is_active: Optional[bool] = None


class TokenRequest(BaseModel):
    """OAuth token request (client_credentials grant)."""
    grant_type: str = Field(..., description="Must be 'client_credentials'")
    client_id: str
    client_secret: str
    scope: Optional[str] = Field(None, description="Space-separated scopes")


class TokenResponse(BaseModel):
    """OAuth token response."""
    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    scope: Optional[str] = None


# --- Helper Functions ---

def hash_client_secret(secret: str) -> str:
    """Hash client secret using bcrypt."""
    return bcrypt.hashpw(secret.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def verify_client_secret(secret: str, hashed: str) -> bool:
    """Verify client secret against hash."""
    try:
        return bcrypt.checkpw(secret.encode('utf-8'), hashed.encode('utf-8'))
    except Exception:
        return False


def validate_scopes(requested_scopes: List[str]) -> List[str]:
    """Validate and filter requested scopes."""
    valid = []
    for scope in requested_scopes:
        if scope in OAuthClient.VALID_SCOPES:
            valid.append(scope)
        else:
            logger.warning(f"Invalid scope requested: {scope}")
    return valid


# --- OAuth Token Endpoint ---

@router.post("/token", response_model=TokenResponse)
async def token(
    request: Request,
    grant_type: str = Form(...),
    client_id: str = Form(...),
    client_secret: str = Form(...),
    scope: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db_no_tenant, scope="function")  # No tenant context for M2M auth
):
    """
    OAuth 2.0 Token Endpoint - Client Credentials Grant.

    Exchange client credentials for an access token.
    Used for machine-to-machine (M2M) API access.

    Headers:
        Content-Type: application/x-www-form-urlencoded

    Form Data:
        grant_type: Must be 'client_credentials'
        client_id: OAuth client ID
        client_secret: OAuth client secret
        scope: (Optional) Space-separated scopes

    Returns:
        access_token: JWT token for API access
        token_type: 'Bearer'
        expires_in: Token lifetime in seconds
    """
    # Validate grant type
    if grant_type != "client_credentials":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported grant_type. Only 'client_credentials' is supported."
        )

    # Look up client
    query = select(OAuthClient).where(
        OAuthClient.client_id == client_id,
        OAuthClient.is_active == True
    )
    result = await db.execute(query)
    client = result.scalar_one_or_none()

    if not client:
        logger.warning(f"OAuth token request for unknown/inactive client: {client_id}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid client credentials",
            headers={"WWW-Authenticate": "Basic"}
        )

    # Verify secret
    if not verify_client_secret(client_secret, client.client_secret_hash):
        logger.warning(f"OAuth token request with invalid secret for client: {client_id}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid client credentials",
            headers={"WWW-Authenticate": "Basic"}
        )

    # Validate requested scopes
    requested_scopes = scope.split() if scope else []
    granted_scopes = []

    for s in requested_scopes:
        if s in (client.scopes or []):
            granted_scopes.append(s)
        else:
            logger.warning(f"Client {client_id} requested unauthorized scope: {s}")

    # If no specific scopes requested, grant all client scopes
    if not requested_scopes:
        granted_scopes = client.scopes or []

    # Update last used timestamp
    forwarded = request.headers.get("x-forwarded-for")
    ip_address = forwarded.split(",")[0].strip() if forwarded else (
        request.client.host if request.client else None
    )

    client.last_used_at = datetime.now(timezone.utc)
    client.last_used_ip = ip_address
    await db.commit()

    # Create access token with client context
    # Use client ID as subject, include tenant for RLS
    access_token = create_client_access_token(
        client_id=str(client.id),
        client_name=client.name,
        tenant_id=str(client.tenant_id),
        scopes=granted_scopes
    )

    return TokenResponse(
        access_token=access_token,
        token_type="Bearer",
        expires_in=3600,  # 1 hour for M2M tokens
        scope=" ".join(granted_scopes) if granted_scopes else None
    )


def create_client_access_token(
    client_id: str,
    client_name: str,
    tenant_id: str,
    scopes: List[str],
    expires_delta: Optional[timedelta] = None
) -> str:
    """
    Create JWT access token for OAuth client (M2M).

    Similar to user tokens but with client-specific claims.
    """
    import jwt
    from datetime import datetime, timezone

    if expires_delta is None:
        expires_delta = timedelta(hours=1)  # M2M tokens valid for 1 hour

    now = datetime.now(timezone.utc)
    expire = now + expires_delta

    payload = {
        "jti": str(uuid.uuid4()),
        "sub": client_id,  # Client ID as subject
        "client_name": client_name,
        "tenant_id": tenant_id,
        "scopes": scopes,
        "token_type": "client_credentials",
        "iat": now,
        "exp": expire,
        "type": "access"
    }

    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


# --- Client Management Endpoints ---

@router.post("", response_model=OAuthClientCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_client(
    request: OAuthClientCreate,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """
    Create a new OAuth client.

    Only admins can create OAuth clients.
    Returns the client_secret ONCE - it cannot be retrieved later.
    """
    # Check admin permission
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can create OAuth clients"
        )

    # Validate scopes
    valid_scopes = validate_scopes(request.scopes)

    # Generate credentials
    client_id = generate_client_id()
    client_secret = generate_client_secret()
    secret_hash = hash_client_secret(client_secret)

    # Create client
    client = OAuthClient(
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret_hash=secret_hash,
        name=request.name,
        description=request.description,
        scopes=valid_scopes,
        created_by=uuid.UUID(user.id)
    )

    db.add(client)
    await db.commit()
    await db.refresh(client)

    logger.info(f"Created OAuth client '{request.name}' (id={client.id}) for tenant {tenant_id}")

    return OAuthClientCreateResponse(
        id=client.id,
        client_id=client.client_id,
        client_secret=client_secret,  # Returned only once!
        name=client.name,
        description=client.description,
        scopes=client.scopes or [],
        is_active=client.is_active,
        last_used_at=client.last_used_at,
        created_at=client.created_at
    )


@router.get("", response_model=List[OAuthClientResponse])
async def list_clients(
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """
    List all OAuth clients for the current tenant.
    """
    query = select(OAuthClient).where(
        OAuthClient.tenant_id == tenant_id
    ).order_by(OAuthClient.created_at.desc())

    result = await db.execute(query)
    clients = result.scalars().all()

    return [
        OAuthClientResponse(
            id=c.id,
            client_id=c.client_id,
            name=c.name,
            description=c.description,
            scopes=c.scopes or [],
            is_active=c.is_active,
            last_used_at=c.last_used_at,
            created_at=c.created_at
        )
        for c in clients
    ]


@router.get("/{client_id}", response_model=OAuthClientResponse)
async def get_client(
    client_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """
    Get a specific OAuth client by ID.
    """
    query = select(OAuthClient).where(
        OAuthClient.id == client_id,
        OAuthClient.tenant_id == tenant_id
    )
    result = await db.execute(query)
    client = result.scalar_one_or_none()

    if not client:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="OAuth client not found"
        )

    return OAuthClientResponse(
        id=client.id,
        client_id=client.client_id,
        name=client.name,
        description=client.description,
        scopes=client.scopes or [],
        is_active=client.is_active,
        last_used_at=client.last_used_at,
        created_at=client.created_at
    )


@router.patch("/{client_id}", response_model=OAuthClientResponse)
async def update_client(
    client_id: uuid.UUID,
    request: OAuthClientUpdate,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """
    Update an OAuth client.

    Only admins can update OAuth clients.
    Note: Client secret cannot be updated - create a new client instead.
    """
    # Check admin permission
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can update OAuth clients"
        )

    query = select(OAuthClient).where(
        OAuthClient.id == client_id,
        OAuthClient.tenant_id == tenant_id
    )
    result = await db.execute(query)
    client = result.scalar_one_or_none()

    if not client:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="OAuth client not found"
        )

    # Update fields
    if request.name is not None:
        client.name = request.name
    if request.description is not None:
        client.description = request.description
    if request.scopes is not None:
        client.scopes = validate_scopes(request.scopes)
    if request.is_active is not None:
        client.is_active = request.is_active

    await db.commit()
    await db.refresh(client)

    logger.info(f"Updated OAuth client {client_id}")

    return OAuthClientResponse(
        id=client.id,
        client_id=client.client_id,
        name=client.name,
        description=client.description,
        scopes=client.scopes or [],
        is_active=client.is_active,
        last_used_at=client.last_used_at,
        created_at=client.created_at
    )


@router.delete("/{client_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_client(
    client_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """
    Delete an OAuth client.

    Only admins can delete OAuth clients.
    Consider deactivating instead for audit trails.
    """
    # Check admin permission
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can delete OAuth clients"
        )

    query = select(OAuthClient).where(
        OAuthClient.id == client_id,
        OAuthClient.tenant_id == tenant_id
    )
    result = await db.execute(query)
    client = result.scalar_one_or_none()

    if not client:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="OAuth client not found"
        )

    await db.delete(client)
    await db.commit()

    logger.info(f"Deleted OAuth client {client_id}")


@router.post("/{client_id}/regenerate-secret", response_model=OAuthClientCreateResponse)
async def regenerate_secret(
    client_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    """
    Regenerate client secret.

    Only admins can regenerate secrets.
    The old secret is immediately invalidated.
    Returns the new secret ONCE - it cannot be retrieved later.
    """
    # Check admin permission
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can regenerate client secrets"
        )

    query = select(OAuthClient).where(
        OAuthClient.id == client_id,
        OAuthClient.tenant_id == tenant_id
    )
    result = await db.execute(query)
    client = result.scalar_one_or_none()

    if not client:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="OAuth client not found"
        )

    # Generate new secret
    new_secret = generate_client_secret()
    client.client_secret_hash = hash_client_secret(new_secret)

    await db.commit()
    await db.refresh(client)

    logger.info(f"Regenerated secret for OAuth client {client_id}")

    return OAuthClientCreateResponse(
        id=client.id,
        client_id=client.client_id,
        client_secret=new_secret,
        name=client.name,
        description=client.description,
        scopes=client.scopes or [],
        is_active=client.is_active,
        last_used_at=client.last_used_at,
        created_at=client.created_at
    )
