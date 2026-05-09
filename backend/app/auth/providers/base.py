"""Base authentication provider interface.

This module defines the core interfaces for the pluggable authentication system.
All auth providers (Local, SAML, OIDC, API Key) implement these interfaces.
"""
from abc import ABC, abstractmethod
from typing import Optional
from pydantic import BaseModel


class AuthResult(BaseModel):
    """
    Standardized result from any auth provider.

    This richer result supports:
    - External identity linking (provider_user_id)
    - Group-based RBAC (groups)
    - Provider-specific metadata (tokens, profile info)
    """
    success: bool
    user_id: Optional[str] = None  # Internal user ID (may be None for new users)
    email: Optional[str] = None
    name: Optional[str] = None
    provider: str  # 'local', 'saml', 'oidc', 'api_key', 'stub'
    provider_user_id: Optional[str] = None  # External ID from identity provider
    groups: list[str] = []  # For RBAC mapping from IdP groups
    error: Optional[str] = None
    metadata: dict = {}  # Provider-specific data (tokens, profile, etc.)


class AuthUser(BaseModel):
    """Authenticated user data for request context."""
    id: str
    email: str
    name: Optional[str] = None
    tenant_id: str
    tenant_slug: str
    role: str  # 'admin', 'analyst', 'viewer'
    auth_provider: str
    groups: list[str] = []  # Groups from identity provider
    is_platform_operator: bool = False  # Platform operators can manage any tenant's features


class BaseAuthProvider(ABC):
    """
    Abstract base class for authentication providers.

    Implement this interface to add new auth providers (SAML, OIDC, etc.).

    The pluggable auth system uses the Strategy Pattern:
    - Each provider handles its own authentication flow
    - AuthService orchestrates provider selection
    - Providers can be enabled/disabled via configuration
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the provider name (e.g., 'stub', 'saml', 'oidc')."""
        pass

    @property
    @abstractmethod
    def protocol(self) -> str:
        """
        Return the auth protocol.

        Standard values: 'local', 'saml', 'oidc', 'api_key', 'ldap'
        """
        pass

    @abstractmethod
    async def authenticate(self, credentials: dict) -> AuthResult:
        """
        Authenticate user with provider-specific credentials.

        Args:
            credentials: Provider-specific credentials dict
                - local/stub: {"email": str, "password": str}
                - saml: {"saml_response": str}
                - oidc: {"code": str, "redirect_uri": str}
                - api_key: {"api_key": str}

        Returns:
            AuthResult with success=True and user info, or success=False with error
        """
        pass

    @abstractmethod
    async def validate_token(self, token: str) -> Optional[AuthUser]:
        """
        Validate a session token and return user data.

        Args:
            token: JWT token from Authorization header

        Returns:
            AuthUser if valid, None if invalid/expired
        """
        pass

    async def get_login_url(self, redirect_uri: str, state: str) -> Optional[str]:
        """
        Get external login URL for redirect-based auth flows.

        Used by SAML and OIDC providers to initiate SSO.
        Returns None for providers without redirect flows (local, api_key).

        Args:
            redirect_uri: URL to redirect back to after authentication
            state: CSRF token for security

        Returns:
            Authorization URL for external IdP, or None if not applicable
        """
        return None

    async def refresh_token(self, refresh_data: dict) -> Optional[AuthResult]:
        """
        Refresh authentication tokens.

        Used by OIDC providers to refresh access tokens.
        Returns None if refresh is not supported.

        Args:
            refresh_data: Provider-specific refresh data (e.g., refresh_token)

        Returns:
            AuthResult with new tokens, or None if refresh not supported
        """
        return None

    def is_enabled(self) -> bool:
        """
        Check if provider is configured and enabled.

        Override to add provider-specific enablement checks
        (e.g., required environment variables present).
        """
        return True
