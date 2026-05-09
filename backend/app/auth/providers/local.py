"""
Local (email/password) authentication provider.

This provider authenticates users against the database using bcrypt-hashed
passwords. It's the primary auth method for small teams or when SSO is not
required.

Security features:
- Passwords hashed with bcrypt (via passlib)
- Constant-time comparison to prevent timing attacks
- User must be active to authenticate
- Tenant context preserved from user record

See AUTH_ARCHITECTURE.md for the full design.
"""
from typing import Optional
from datetime import datetime

from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.providers.base import BaseAuthProvider, AuthResult, AuthUser
from app.auth.jwt import decode_access_token
from app.models.user import User
from app.core.logging import get_logger

logger = get_logger(__name__)

# Password hashing context using bcrypt
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class LocalAuthProvider(BaseAuthProvider):
    """
    Email/password authentication against the database.

    This provider queries the users table to validate credentials.
    Passwords are stored as bcrypt hashes in the hashed_password column.

    Usage:
        provider = LocalAuthProvider(db_session)
        result = await provider.authenticate({
            "email": "user@example.com",
            "password": "secret123"
        })
        if result.success:
            print(f"Welcome {result.name}")

    Note: This provider requires a database session. For stateless validation
    (e.g., in middleware), use validate_token() which only requires the JWT.
    """

    def __init__(self, db: AsyncSession):
        """
        Initialize with database session.

        Args:
            db: Async SQLAlchemy session for user queries
        """
        self.db = db

    @property
    def provider_name(self) -> str:
        """Return provider identifier."""
        return "local"

    @property
    def protocol(self) -> str:
        """Return auth protocol."""
        return "local"

    async def authenticate(self, credentials: dict) -> AuthResult:
        """
        Authenticate with email and password.

        Args:
            credentials: Dict with 'email' and 'password' keys

        Returns:
            AuthResult with user info on success, error on failure
        """
        email = credentials.get("email", "").lower().strip()
        password = credentials.get("password", "")

        if not email or not password:
            return AuthResult(
                success=False,
                provider=self.provider_name,
                error="Email and password required"
            )

        # Query user with tenant loaded
        query = (
            select(User)
            .options(selectinload(User.tenant))
            .where(
                User.email == email,
                User.is_active == True,
                User.deleted_at.is_(None)  # Soft delete check
            )
        )

        result = await self.db.execute(query)
        user = result.scalar_one_or_none()

        if not user:
            logger.info(f"Local auth failed: user not found: {email}")
            return AuthResult(
                success=False,
                provider=self.provider_name,
                error="Invalid email or password"
            )

        # Check password hash
        if not user.hashed_password:
            logger.warning(f"Local auth failed: no password set for user: {email}")
            return AuthResult(
                success=False,
                provider=self.provider_name,
                error="Password login not enabled for this account"
            )

        if not self.verify_password(password, user.hashed_password):
            logger.info(f"Local auth failed: incorrect password for: {email}")
            return AuthResult(
                success=False,
                provider=self.provider_name,
                error="Invalid email or password"
            )

        # Check if tenant is active
        if user.tenant and not user.tenant.is_active:
            logger.warning(f"Local auth failed: tenant inactive for user: {email}")
            return AuthResult(
                success=False,
                provider=self.provider_name,
                error="Your organization's account is inactive"
            )

        logger.info(f"Local auth success: {email}")

        return AuthResult(
            success=True,
            user_id=str(user.id),
            email=user.email,
            name=user.name,
            provider=self.provider_name,
            provider_user_id=str(user.id),  # For local auth, same as user_id
            groups=[],  # Local auth doesn't have IdP groups
            metadata={
                "tenant_id": str(user.tenant_id),
                "tenant_slug": user.tenant.slug if user.tenant else "unknown",
                "role": user.role
            }
        )

    async def validate_token(self, token: str) -> Optional[AuthUser]:
        """
        Validate JWT token and return user data.

        This method validates the token and optionally verifies the user
        still exists and is active in the database.

        Args:
            token: JWT token from Authorization header

        Returns:
            AuthUser if valid, None if invalid/expired
        """
        payload = decode_access_token(token)
        if not payload:
            return None

        # For strict validation, verify user still exists and is active
        # This adds a database query but ensures revoked users can't access
        user_id = payload.get("sub")
        if user_id:
            query = (
                select(User)
                .options(selectinload(User.tenant))
                .where(
                    User.id == user_id,
                    User.is_active == True,
                    User.deleted_at.is_(None)
                )
            )
            result = await self.db.execute(query)
            user = result.scalar_one_or_none()

            if not user:
                logger.warning(f"Token validation failed: user not found or inactive: {user_id}")
                return None

            # Return fresh data from database
            return AuthUser(
                id=str(user.id),
                email=user.email,
                name=user.name,
                tenant_id=str(user.tenant_id),
                tenant_slug=user.tenant.slug if user.tenant else payload.get("tenant_slug", "unknown"),
                role=user.role,
                auth_provider="local",
                groups=[],
                is_platform_operator=getattr(user, 'is_superuser', False) or False,
            )

        # Fallback to token claims if sub not present
        return AuthUser(
            id=payload.get("sub"),
            email=payload.get("email"),
            name=payload.get("name"),
            tenant_id=payload.get("tenant_id"),
            tenant_slug=payload.get("tenant_slug"),
            role=payload.get("role"),
            auth_provider=payload.get("auth_provider", "local"),
            groups=[],
            is_platform_operator=payload.get("is_platform_operator", False),
        )

    async def get_login_url(self, redirect_uri: str, state: str) -> Optional[str]:
        """
        Local auth doesn't use external login URLs.

        Returns:
            None - local auth uses direct email/password login
        """
        return None

    async def refresh_token(self, refresh_data: dict) -> Optional[AuthResult]:
        """
        Refresh is handled by AuthService, not provider.

        Returns:
            None - refresh is JWT-based, not provider-specific
        """
        return None

    def is_enabled(self) -> bool:
        """
        Local auth is always enabled when a database session is available.

        Returns:
            True if database connection is available
        """
        return self.db is not None

    # Password utilities

    @staticmethod
    def hash_password(password: str) -> str:
        """
        Hash a password using bcrypt.

        Note: bcrypt has a 72-byte limit, so we truncate longer passwords.
        This is safe because 72 bytes provides sufficient entropy.

        Args:
            password: Plain text password

        Returns:
            Bcrypt hash string
        """
        # Truncate to 72 bytes (bcrypt limit)
        password_bytes = password.encode('utf-8')[:72]
        return pwd_context.hash(password_bytes.decode('utf-8', errors='ignore'))

    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        """
        Verify a password against its hash.

        Uses constant-time comparison to prevent timing attacks.

        Args:
            plain_password: Plain text password to check
            hashed_password: Stored bcrypt hash

        Returns:
            True if password matches, False otherwise
        """
        try:
            # Truncate to 72 bytes to match hashing behavior
            password_bytes = plain_password.encode('utf-8')[:72]
            return pwd_context.verify(password_bytes.decode('utf-8', errors='ignore'), hashed_password)
        except Exception:
            # Handle invalid hash format gracefully
            return False

    @staticmethod
    def needs_rehash(hashed_password: str) -> bool:
        """
        Check if password needs to be rehashed.

        This can happen when bcrypt cost factor is increased.

        Args:
            hashed_password: Current password hash

        Returns:
            True if rehash is recommended
        """
        return pwd_context.needs_update(hashed_password)


# Utility functions for user management

async def create_local_user(
    db: AsyncSession,
    email: str,
    password: str,
    name: str,
    tenant_id: str,
    role: str = "analyst"
) -> User:
    """
    Create a new user with local authentication.

    This is a convenience function for creating users programmatically.
    For admin operations, use the user management API.

    Args:
        db: Database session
        email: User's email address
        password: Plain text password (will be hashed)
        name: Display name
        tenant_id: UUID of user's tenant
        role: User role ('admin', 'analyst', 'viewer')

    Returns:
        Created User model instance

    Raises:
        IntegrityError: If email already exists in tenant
    """
    user = User(
        email=email.lower().strip(),
        name=name,
        hashed_password=LocalAuthProvider.hash_password(password),
        tenant_id=tenant_id,
        role=role,
        auth_provider="local",
        is_active=True
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def change_password(
    db: AsyncSession,
    user_id: str,
    old_password: str,
    new_password: str
) -> bool:
    """
    Change a user's password.

    Verifies the old password before updating.

    Args:
        db: Database session
        user_id: UUID of user
        old_password: Current password for verification
        new_password: New password to set

    Returns:
        True if password changed successfully, False otherwise
    """
    query = select(User).where(User.id == user_id, User.is_active == True)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user or not user.hashed_password:
        return False

    if not LocalAuthProvider.verify_password(old_password, user.hashed_password):
        return False

    user.hashed_password = LocalAuthProvider.hash_password(new_password)
    user.updated_at = datetime.utcnow()
    await db.commit()

    logger.info(f"Password changed for user: {user.email}")
    return True


async def reset_password(
    db: AsyncSession,
    user_id: str,
    new_password: str
) -> bool:
    """
    Reset a user's password (admin operation).

    Does not require old password - for use with password reset tokens.

    Args:
        db: Database session
        user_id: UUID of user
        new_password: New password to set

    Returns:
        True if password reset successfully, False otherwise
    """
    query = select(User).where(User.id == user_id)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        return False

    user.hashed_password = LocalAuthProvider.hash_password(new_password)
    user.updated_at = datetime.utcnow()
    await db.commit()

    logger.info(f"Password reset for user: {user.email}")
    return True
