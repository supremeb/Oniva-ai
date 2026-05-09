"""
AuthService: Central authentication orchestrator.

This service manages multiple auth providers (Local, SAML, OIDC, API Key, Stub)
using the Strategy Pattern. It handles:
- Provider registration and discovery
- Authentication routing to appropriate providers
- User creation/linking from external identity providers
- JWT token generation and validation
- Provider-specific login URL generation for SSO flows

See AUTH_ARCHITECTURE.md for the full design.
"""
from typing import Optional, Tuple
from uuid import UUID, uuid4
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.providers.base import BaseAuthProvider, AuthResult, AuthUser
from app.auth.providers.stub import StubAuthProvider
from app.auth.jwt import (
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_refresh_token,
    get_refresh_token_expiry,
)
from app.models.user import User
from app.models.tenant import Tenant
from app.models.refresh_token import RefreshToken
from app.config import get_settings
from app.core.logging import get_logger

settings = get_settings()
logger = get_logger(__name__)


class AuthService:
    """
    Central authentication service that orchestrates multiple providers.

    The AuthService:
    1. Registers all enabled auth providers at initialization
    2. Routes authentication requests to the appropriate provider
    3. Creates or links users in the local database
    4. Issues JWT tokens for authenticated sessions

    Usage:
        # In an API endpoint
        auth_service = AuthService(db)

        # Authenticate with a specific provider
        user, tokens = await auth_service.authenticate("stub", {
            "email": "admin@demo.local",
            "password": "demo123"
        })

        # List enabled providers
        providers = auth_service.get_enabled_providers()

        # Get login URL for OAuth/SAML
        url = await auth_service.get_login_url("google_oidc", redirect_uri, state)
    """

    def __init__(self, db: Optional[AsyncSession] = None):
        """
        Initialize AuthService with optional database session.

        Args:
            db: Async database session. If None, only stub provider is available.
        """
        self.db = db
        self.providers: dict[str, BaseAuthProvider] = {}
        self._register_providers()

    def _register_providers(self):
        """Register all enabled auth providers."""
        # Stub provider always available for development
        stub = StubAuthProvider()
        if stub.is_enabled():
            self.providers["stub"] = stub
            logger.debug("Registered auth provider: stub")

        # Local auth provider (DB-backed email/password) - requires db session
        if self.db:
            from app.auth.providers.local import LocalAuthProvider
            local = LocalAuthProvider(self.db)
            if local.is_enabled():
                self.providers["local"] = local
                logger.debug("Registered auth provider: local")

        # API Key provider (for machine-to-machine) - requires db session
        # Uncomment when APIKeyAuthProvider is implemented:
        # if self.db:
        #     from app.auth.providers.api_key import APIKeyAuthProvider
        #     api_key = APIKeyAuthProvider(self.db)
        #     if api_key.is_enabled():
        #         self.providers["api_key"] = api_key
        #         logger.debug("Registered auth provider: api_key")

        # SAML provider (for enterprise SSO)
        if settings.SAML_ENABLED:
            from app.auth.providers.saml import SAMLAuthProvider
            saml = SAMLAuthProvider()
            if saml.is_enabled():
                self.providers[saml.provider_name] = saml
                logger.debug(f"Registered auth provider: {saml.provider_name}")

        # OIDC providers (Google, Microsoft, custom)
        # Uncomment when OIDCAuthProvider is implemented:
        # for provider_name in ["google", "microsoft"]:
        #     client_id_var = f"{provider_name.upper()}_CLIENT_ID"
        #     if os.getenv(client_id_var):
        #         from app.auth.providers.oidc import OIDCAuthProvider
        #         oidc = OIDCAuthProvider(provider_name)
        #         if oidc.is_enabled():
        #             self.providers[oidc.provider_name] = oidc
        #             logger.debug(f"Registered auth provider: {oidc.provider_name}")

        logger.info(f"Auth providers registered: {list(self.providers.keys())}")

    def get_provider(self, provider_name: str) -> Optional[BaseAuthProvider]:
        """
        Get a specific auth provider by name.

        Args:
            provider_name: Provider identifier (e.g., 'stub', 'local', 'google_oidc')

        Returns:
            AuthProvider instance or None if not found/disabled
        """
        return self.providers.get(provider_name)

    def get_enabled_providers(self) -> list[dict]:
        """
        Return list of enabled provider info for frontend display.

        Returns:
            List of dicts with provider name, protocol, and display info
        """
        return [
            {
                "name": name,
                "protocol": provider.protocol,
                "is_sso": provider.protocol in ("saml", "oidc"),
            }
            for name, provider in self.providers.items()
            if provider.is_enabled()
        ]

    async def authenticate(
        self,
        provider_name: str,
        credentials: dict
    ) -> tuple[Optional[AuthUser], Optional[dict]]:
        """
        Authenticate user with specified provider.

        This method:
        1. Routes to the appropriate provider
        2. Validates credentials with the provider
        3. Creates or links user in local database (for SSO providers)
        4. Generates JWT tokens

        Args:
            provider_name: Provider to authenticate with
            credentials: Provider-specific credentials dict
                - stub/local: {"email": str, "password": str}
                - saml: {"saml_response": str, "request_data": dict}
                - oidc: {"code": str, "redirect_uri": str}
                - api_key: {"api_key": str}

        Returns:
            Tuple of (AuthUser, tokens_dict) on success
            Tuple of (None, None) on failure

        Example:
            user, tokens = await auth_service.authenticate("stub", {
                "email": "admin@demo.local",
                "password": "demo123"
            })
            if user:
                # Success - user is authenticated
                print(f"Welcome {user.name}")
            else:
                # Failure - invalid credentials
                print("Authentication failed")
        """
        provider = self.providers.get(provider_name)
        if not provider:
            logger.warning(f"Auth provider not found: {provider_name}")
            return None, None

        if not provider.is_enabled():
            logger.warning(f"Auth provider disabled: {provider_name}")
            return None, None

        # Authenticate with provider
        result = await provider.authenticate(credentials)

        if not result.success:
            logger.info(f"Auth failed via {provider_name}: {result.error}")
            return None, None

        # For stub provider, construct AuthUser directly from result
        # For DB-backed providers (local, SAML, OIDC), we'd query/create the user
        if provider_name == "stub":
            # Stub provider returns all user info in metadata
            auth_user = AuthUser(
                id=result.user_id,
                email=result.email,
                name=result.name,
                tenant_id=result.metadata.get("tenant_id"),
                tenant_slug=result.metadata.get("tenant_slug"),
                role=result.metadata.get("role"),
                auth_provider=result.provider,
                groups=result.groups,
                is_platform_operator=result.metadata.get("is_platform_operator", False),
            )

            # Generate JWT tokens
            tokens = self._generate_tokens(auth_user)

            logger.info(f"Auth success via {provider_name}: {result.email}")
            return auth_user, tokens

        # For other providers, we need to get/create user in database
        if self.db:
            auth_user = await self._get_or_create_user(result)
            if auth_user:
                tokens = self._generate_tokens(auth_user)
                logger.info(f"Auth success via {provider_name}: {result.email}")
                return auth_user, tokens

        logger.warning(f"Could not create/find user for auth result: {result.email}")
        return None, None

    async def _get_or_create_user(self, auth_result: AuthResult) -> Optional[AuthUser]:
        """
        Find existing user or create new one based on auth result.

        For external providers (SAML, OIDC), this links the external identity
        to a local user record. Users are matched by email first, then by
        provider_user_id.

        Args:
            auth_result: Result from auth provider

        Returns:
            AuthUser if found/created, None on error
        """
        if not self.db:
            return None

        # First try to find by provider_user_id (for returning OAuth users)
        if auth_result.provider_user_id and auth_result.provider not in ("local", "stub"):
            query = (
                select(User)
                .options(selectinload(User.tenant))  # Eager load to avoid lazy-load in sync context
                .where(
                    User.external_id == auth_result.provider_user_id,
                    User.auth_provider == auth_result.provider,
                    User.is_active == True
                )
            )
            result = await self.db.execute(query)
            user = result.scalar_one_or_none()
            if user:
                return self._user_to_auth_user(user)

        # Try to find by email (for linking or local auth)
        if auth_result.email:
            query = (
                select(User)
                .options(selectinload(User.tenant))  # Eager load to avoid lazy-load in sync context
                .where(
                    User.email == auth_result.email,
                    User.is_active == True
                )
            )
            result = await self.db.execute(query)
            user = result.scalar_one_or_none()

            if user:
                # Update external_id if this is first SSO login
                if auth_result.provider not in ("local", "stub") and not user.external_id:
                    user.external_id = auth_result.provider_user_id
                    user.auth_provider = auth_result.provider
                    await self.db.commit()

                return self._user_to_auth_user(user)

        # User doesn't exist - for SSO providers, we could auto-create
        # For now, require users to be pre-provisioned
        logger.warning(f"No user found for email: {auth_result.email}")
        return None

    def _user_to_auth_user(self, user: User) -> AuthUser:
        """Convert database User model to AuthUser for request context."""
        return AuthUser(
            id=str(user.id),
            email=user.email,
            name=user.name,
            tenant_id=str(user.tenant_id),
            tenant_slug=user.tenant.slug if user.tenant else "unknown",
            role=user.role,
            auth_provider=user.auth_provider,
            groups=[],  # Groups would come from IdP or role mapping
            is_platform_operator=getattr(user, 'is_superuser', False) or False,
        )

    async def get_or_create_saml_user(
        self,
        email: str,
        name: Optional[str] = None,
        groups: list[str] = None,
        idp_user_id: Optional[str] = None,
        tenant_id: Optional[UUID] = None,
    ) -> Optional[User]:
        """
        Get existing user or create new one for SAML authentication.

        This method handles the user provisioning flow for SAML SSO:
        1. First tries to find user by email (most common case)
        2. If not found and auto-provisioning is enabled, creates the user
        3. Maps IdP groups to roles if configured

        Args:
            email: User's email from SAML assertion
            name: Display name from SAML assertion
            groups: Group memberships from IdP
            idp_user_id: NameID from SAML assertion
            tenant_id: Optional tenant to assign (for multi-tenant)

        Returns:
            User object if found/created, None if provisioning disabled
        """
        if not self.db:
            return None

        groups = groups or []

        # Try to find existing user by email
        query = select(User).where(
            User.email == email.lower(),
            User.is_active == True,
            User.deleted_at.is_(None)
        )
        result = await self.db.execute(query)
        user = result.scalar_one_or_none()

        if user:
            # Update user info from IdP if changed
            updated = False

            if name and user.name != name:
                user.name = name
                updated = True

            if idp_user_id and user.external_id != idp_user_id:
                user.external_id = idp_user_id
                updated = True

            if user.auth_provider != "saml":
                user.auth_provider = "saml"
                updated = True

            # Map groups to role if configured
            new_role = self._map_groups_to_role(groups)
            if new_role and user.role != new_role:
                user.role = new_role
                updated = True

            if updated:
                from datetime import datetime
                user.updated_at = datetime.utcnow()
                await self.db.commit()
                logger.info(f"Updated SAML user: {email}")

            return user

        # User doesn't exist - check if auto-provisioning is enabled
        saml_auto_provision = getattr(settings, 'SAML_AUTO_PROVISION', False)
        if not saml_auto_provision:
            logger.warning(
                f"SAML user not found and auto-provisioning disabled: {email}. "
                f"Enable SAML_AUTO_PROVISION=true or pre-provision the user."
            )
            return None

        # Auto-provision new user
        # Need to determine tenant - use default or first available
        if not tenant_id:
            # Get default tenant for SAML users
            default_tenant_slug = getattr(settings, 'SAML_DEFAULT_TENANT', None)
            if default_tenant_slug:
                tenant_query = select(Tenant).where(
                    Tenant.slug == default_tenant_slug,
                    Tenant.is_active == True
                )
                tenant_result = await self.db.execute(tenant_query)
                tenant = tenant_result.scalar_one_or_none()
                if tenant:
                    tenant_id = tenant.id
                else:
                    logger.error(f"Default SAML tenant not found: {default_tenant_slug}")
                    return None
            else:
                # Use first active tenant (for single-tenant deployments)
                tenant_query = select(Tenant).where(Tenant.is_active == True).limit(1)
                tenant_result = await self.db.execute(tenant_query)
                tenant = tenant_result.scalar_one_or_none()
                if tenant:
                    tenant_id = tenant.id
                else:
                    logger.error("No active tenant available for SAML user provisioning")
                    return None

        # Determine role from groups or use default
        role = self._map_groups_to_role(groups) or getattr(settings, 'SAML_DEFAULT_ROLE', 'analyst')

        # Create new user
        new_user = User(
            email=email.lower(),
            name=name or email.split('@')[0],  # Use email prefix if no name
            tenant_id=tenant_id,
            role=role,
            auth_provider="saml",
            external_id=idp_user_id,
            is_active=True,
            hashed_password=None  # SAML users don't have local passwords
        )

        self.db.add(new_user)
        await self.db.commit()
        await self.db.refresh(new_user)

        logger.info(f"Auto-provisioned SAML user: {email} with role {role}")
        return new_user

    def _map_groups_to_role(self, groups: list[str]) -> Optional[str]:
        """
        Map IdP groups to Oniva roles.

        Uses configuration to map group names to roles.
        Example config:
            SAML_GROUP_MAPPING = '{"Admins": "admin", "Analysts": "analyst", "Viewers": "viewer"}'

        Args:
            groups: List of group names from IdP

        Returns:
            Mapped role name or None if no mapping found
        """
        if not groups:
            return None

        # Get group mapping from settings
        group_mapping_str = getattr(settings, 'SAML_GROUP_MAPPING', '{}')
        try:
            import json
            group_mapping = json.loads(group_mapping_str)
        except json.JSONDecodeError:
            logger.warning(f"Invalid SAML_GROUP_MAPPING JSON: {group_mapping_str}")
            return None

        # Check each group against mapping (priority: admin > analyst > viewer)
        for role in ['admin', 'analyst', 'viewer']:
            for group in groups:
                mapped_role = group_mapping.get(group)
                if mapped_role == role:
                    return role

        return None

    def _generate_tokens(self, user: AuthUser) -> dict:
        """
        Generate JWT access token for authenticated user.

        Note: Refresh tokens are created separately via create_refresh_token_for_user()
        because they require database storage.

        Args:
            user: Authenticated user

        Returns:
            Dict with access_token, token_type, expires_in
        """
        access_token = create_access_token(
            user_id=user.id,
            email=user.email,
            tenant_id=user.tenant_id,
            tenant_slug=user.tenant_slug,
            role=user.role,
            name=user.name,
            auth_provider=user.auth_provider,
            is_platform_operator=getattr(user, 'is_platform_operator', False),
        )

        return {
            "access_token": access_token,
            "token_type": "bearer",
            "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
        }

    async def create_refresh_token_for_user(
        self,
        user: AuthUser,
        user_agent: Optional[str] = None,
        ip_address: Optional[str] = None,
        family_id: Optional[UUID] = None,
    ) -> Tuple[str, RefreshToken]:
        """
        Create a refresh token for an authenticated user.

        This creates a cryptographically secure refresh token, stores the hash
        in the database, and returns the raw token to send to the client.

        Args:
            user: Authenticated user
            user_agent: Client user agent string
            ip_address: Client IP address
            family_id: Token family ID (for rotation). If None, creates new family.

        Returns:
            Tuple of (raw_token, RefreshToken model)

        Raises:
            ValueError: If no database session available
        """
        if not self.db:
            raise ValueError("Database session required for refresh tokens")

        # Generate secure random token
        raw_token, token_hash = generate_refresh_token()

        # Create database record
        refresh_token = RefreshToken(
            user_id=UUID(user.id),
            tenant_id=UUID(user.tenant_id),
            token_hash=token_hash,
            family_id=family_id or uuid4(),
            expires_at=get_refresh_token_expiry(),
            user_agent=user_agent[:512] if user_agent else None,
            ip_address=ip_address,
        )

        self.db.add(refresh_token)
        await self.db.commit()
        await self.db.refresh(refresh_token)

        logger.info(f"Created refresh token for user {user.email}")
        return raw_token, refresh_token

    async def validate_refresh_token(self, raw_token: str) -> Optional[RefreshToken]:
        """
        Validate a refresh token and return the database record.

        Args:
            raw_token: The raw refresh token from the client

        Returns:
            RefreshToken model if valid, None if invalid/expired/revoked
        """
        if not self.db:
            return None

        token_hash = hash_refresh_token(raw_token)

        query = select(RefreshToken).where(
            RefreshToken.token_hash == token_hash,
            RefreshToken.revoked == False,
        )
        result = await self.db.execute(query)
        refresh_token = result.scalar_one_or_none()

        if not refresh_token:
            logger.warning("Refresh token not found or revoked")
            return None

        if not refresh_token.is_valid:
            logger.warning(f"Refresh token expired: {refresh_token.id}")
            return None

        # Update last used timestamp
        refresh_token.mark_used()
        await self.db.commit()

        return refresh_token

    async def rotate_refresh_token(
        self,
        old_token: RefreshToken,
        user: AuthUser,
        user_agent: Optional[str] = None,
        ip_address: Optional[str] = None,
    ) -> Tuple[str, RefreshToken]:
        """
        Rotate a refresh token - revoke old, issue new in same family.

        Token rotation improves security by ensuring each refresh token
        can only be used once. If an old token is reused, we know the
        family has been compromised.

        Args:
            old_token: The refresh token being rotated
            user: Authenticated user
            user_agent: Client user agent string
            ip_address: Client IP address

        Returns:
            Tuple of (new_raw_token, new RefreshToken model)
        """
        # Revoke the old token
        old_token.revoke(reason="rotation")
        await self.db.commit()

        # Create new token in same family
        return await self.create_refresh_token_for_user(
            user=user,
            user_agent=user_agent,
            ip_address=ip_address,
            family_id=old_token.family_id,
        )

    async def revoke_token_family(self, family_id: UUID, reason: str = "security") -> int:
        """
        Revoke all tokens in a family (security breach response).

        Called when token reuse is detected, indicating a potential
        compromise of the token family.

        Args:
            family_id: Token family to revoke
            reason: Reason for revocation

        Returns:
            Number of tokens revoked
        """
        if not self.db:
            return 0

        from sqlalchemy import update

        stmt = (
            update(RefreshToken)
            .where(
                RefreshToken.family_id == family_id,
                RefreshToken.revoked == False,
            )
            .values(
                revoked=True,
                revoked_at=datetime.utcnow(),
                revoked_reason=reason,
            )
        )
        result = await self.db.execute(stmt)
        await self.db.commit()

        count = result.rowcount
        logger.warning(f"Revoked {count} tokens in family {family_id}: {reason}")
        return count

    async def revoke_all_user_tokens(self, user_id: UUID, reason: str = "logout_all") -> int:
        """
        Revoke all refresh tokens for a user.

        Used for:
        - "Log out everywhere" functionality
        - Account compromise response
        - Password change

        Args:
            user_id: User whose tokens to revoke
            reason: Reason for revocation

        Returns:
            Number of tokens revoked
        """
        if not self.db:
            return 0

        from sqlalchemy import update

        stmt = (
            update(RefreshToken)
            .where(
                RefreshToken.user_id == user_id,
                RefreshToken.revoked == False,
            )
            .values(
                revoked=True,
                revoked_at=datetime.utcnow(),
                revoked_reason=reason,
            )
        )
        result = await self.db.execute(stmt)
        await self.db.commit()

        count = result.rowcount
        logger.info(f"Revoked {count} tokens for user {user_id}: {reason}")
        return count

    async def get_user_by_id(self, user_id: UUID) -> Optional[AuthUser]:
        """
        Get user by ID for token refresh.

        Args:
            user_id: User UUID

        Returns:
            AuthUser if found and active, None otherwise
        """
        if not self.db:
            return None

        query = (
            select(User)
            .options(selectinload(User.tenant))
            .where(
                User.id == user_id,
                User.is_active == True,
            )
        )
        result = await self.db.execute(query)
        user = result.scalar_one_or_none()

        if user:
            return self._user_to_auth_user(user)
        return None

    async def validate_token(self, token: str) -> Optional[AuthUser]:
        """
        Validate JWT token and return user data.

        This method validates the token signature and expiration,
        then returns the embedded user claims as an AuthUser.

        For production, you might want to:
        - Check token against a blacklist
        - Verify user still exists and is active in DB
        - Check tenant is still active

        Args:
            token: JWT token from Authorization header

        Returns:
            AuthUser if valid, None if invalid/expired
        """
        payload = decode_access_token(token)
        if not payload:
            return None

        # For stub auth, trust the token claims
        return AuthUser(
            id=payload.get("sub"),
            email=payload.get("email"),
            name=payload.get("name"),
            tenant_id=payload.get("tenant_id"),
            tenant_slug=payload.get("tenant_slug"),
            role=payload.get("role"),
            auth_provider=payload.get("auth_provider", "unknown"),
            groups=payload.get("groups", []),
            is_platform_operator=payload.get("is_platform_operator", False),
        )

    async def get_login_url(
        self,
        provider_name: str,
        redirect_uri: str,
        state: str
    ) -> Optional[str]:
        """
        Get external login URL for OAuth/SAML providers.

        This initiates the SSO flow by generating the authorization URL
        for the external identity provider.

        Args:
            provider_name: Provider to initiate login with
            redirect_uri: URL to redirect back after authentication
            state: CSRF token for security

        Returns:
            Authorization URL for external IdP, or None if not supported
        """
        provider = self.providers.get(provider_name)
        if not provider:
            return None

        return await provider.get_login_url(redirect_uri, state)

    async def refresh_access_token(
        self,
        refresh_token: str,
        user_agent: Optional[str] = None,
        ip_address: Optional[str] = None,
    ) -> Optional[dict]:
        """
        Refresh access token using refresh token.

        Implements secure token refresh with rotation:
        1. Validates refresh token against database
        2. Verifies token is not expired or revoked
        3. Gets fresh user data from database
        4. Rotates refresh token (old token revoked, new token issued)
        5. Returns new access token + new refresh token

        Args:
            refresh_token: Refresh token from client
            user_agent: Client user agent for new token
            ip_address: Client IP for new token

        Returns:
            Dict with access_token, refresh_token, token_type, expires_in
            or None if invalid
        """
        if not self.db:
            logger.warning("Cannot refresh token: no database session")
            return None

        # Validate the refresh token
        token_record = await self.validate_refresh_token(refresh_token)
        if not token_record:
            return None

        # Get fresh user data from database
        user = await self.get_user_by_id(token_record.user_id)
        if not user:
            logger.warning(f"User not found for refresh token: {token_record.user_id}")
            # Revoke the token family if user doesn't exist
            await self.revoke_token_family(token_record.family_id, "user_not_found")
            return None

        # Rotate the refresh token (revoke old, issue new)
        new_raw_token, new_token_record = await self.rotate_refresh_token(
            old_token=token_record,
            user=user,
            user_agent=user_agent,
            ip_address=ip_address,
        )

        # Generate new access token
        tokens = self._generate_tokens(user)

        # Add the new refresh token to response
        tokens["refresh_token"] = new_raw_token
        tokens["refresh_token_expires_in"] = settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60

        logger.info(f"Token refresh successful for user {user.email}")
        return tokens


# Factory function for creating AuthService with database
async def get_auth_service(db: AsyncSession) -> AuthService:
    """
    Create AuthService instance with database session.

    Usage in FastAPI endpoint:
        @router.post("/login")
        async def login(
            request: LoginRequest,
            db: AsyncSession = Depends(get_db_no_tenant)
        ):
            auth_service = await get_auth_service(db)
            user, tokens = await auth_service.authenticate("local", {...})
    """
    return AuthService(db)


# Singleton for stub-only auth (no database needed)
_stub_auth_service: Optional[AuthService] = None


def get_stub_auth_service() -> AuthService:
    """
    Get AuthService for stub authentication only.

    This is useful for development when you don't need database users.
    """
    global _stub_auth_service
    if _stub_auth_service is None:
        _stub_auth_service = AuthService(db=None)
    return _stub_auth_service
