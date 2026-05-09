"""JWT token creation and validation utilities.

SECURITY: Tokens include a unique jti (JWT ID) claim for revocation support.
Use TokenBlacklist service to revoke tokens on logout or security events.

Implements secure refresh token flow:
- Access tokens: Short-lived (15 min), stored in memory
- Refresh tokens: Long-lived (7 days), stored in httpOnly cookie
- Token rotation: New refresh token issued on each refresh
- Family tracking: Detect token reuse (potential compromise)
"""
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
import uuid
import secrets
import hashlib
import jwt

from app.config import get_settings

settings = get_settings()


def generate_refresh_token() -> Tuple[str, str]:
    """
    Generate a cryptographically secure refresh token.

    Returns:
        Tuple of (raw_token, token_hash):
        - raw_token: The token to send to the client (32 bytes, base64url encoded)
        - token_hash: SHA256 hash to store in database
    """
    # Generate 32 bytes of random data (256 bits of entropy)
    raw_token = secrets.token_urlsafe(32)
    # Hash for storage (never store raw token)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    return raw_token, token_hash


def hash_refresh_token(raw_token: str) -> str:
    """
    Hash a raw refresh token for database lookup.

    Args:
        raw_token: The raw token received from client

    Returns:
        SHA256 hash of the token
    """
    return hashlib.sha256(raw_token.encode()).hexdigest()


def get_refresh_token_expiry() -> datetime:
    """Get expiration datetime for a new refresh token."""
    return datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)


def create_access_token(
    user_id: str,
    email: str,
    tenant_id: str,
    tenant_slug: str,
    role: str,
    name: Optional[str] = None,
    auth_provider: str = "stub",
    expires_delta: Optional[timedelta] = None,
    is_platform_operator: bool = False,
) -> str:
    """
    Create JWT access token with user and tenant claims.

    Args:
        user_id: User UUID
        email: User email
        tenant_id: Tenant UUID
        tenant_slug: Tenant slug for display
        role: User role (admin, analyst, viewer)
        name: Optional display name
        auth_provider: Auth provider name
        expires_delta: Optional custom expiry (default: 15 min)

    Returns:
        Encoded JWT token string
    """
    if expires_delta is None:
        expires_delta = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    now = datetime.now(timezone.utc)
    expire = now + expires_delta

    payload = {
        "jti": str(uuid.uuid4()),  # SECURITY: Unique token ID for revocation
        "sub": user_id,
        "email": email,
        "name": name,
        "tenant_id": tenant_id,
        "tenant_slug": tenant_slug,
        "role": role,
        "auth_provider": auth_provider,
        "is_platform_operator": is_platform_operator,
        "iat": now,
        "exp": expire,
        "type": "access"
    }

    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_access_token(token: str) -> Optional[dict]:
    """
    Decode and validate JWT access token.

    Args:
        token: JWT token string

    Returns:
        Token payload dict if valid, None if invalid/expired
    """
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM]
        )

        # Verify required claims
        required_claims = ["sub", "email", "tenant_id", "role"]
        if not all(claim in payload for claim in required_claims):
            return None

        return payload

    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def decode_expired_access_token(token: str) -> Optional[dict]:
    """
    Decode an access token without validating expiration.

    Used during token refresh to extract user info from expired access token.
    Still validates signature and other claims.

    Args:
        token: JWT token string (may be expired)

    Returns:
        Token payload dict if signature valid, None if invalid
    """
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
            options={"verify_exp": False}  # Allow expired tokens
        )

        # Verify required claims
        required_claims = ["sub", "email", "tenant_id", "role"]
        if not all(claim in payload for claim in required_claims):
            return None

        # Verify it's an access token
        if payload.get("type") != "access":
            return None

        return payload

    except jwt.InvalidTokenError:
        return None
