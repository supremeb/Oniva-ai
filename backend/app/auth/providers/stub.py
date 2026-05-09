"""
Stub authentication provider for development and testing.

This provider uses preset test users for quick development iteration
and POC demonstrations. Replace with SAML/OIDC for production.

WARNING: This provider is disabled in production environments.
"""
import os
from typing import Optional

from app.auth.providers.base import AuthUser, AuthResult, BaseAuthProvider
from app.auth.jwt import decode_access_token


# Environments where stub auth is explicitly allowed
# Fail-closed: only these environments can use stub auth
ALLOWED_ENVIRONMENTS = frozenset({"development", "test", "testing", "local"})


# Preset test users (one per demo tenant)
# Roles: owner, admin, member, viewer
STUB_USERS = {
    "owner@demo.local": {
        "id": "00000000-0000-0000-0000-000000000001",
        "password": "demo123",
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "tenant_slug": "demo",
        "role": "owner",
        "name": "Demo Owner",
        "groups": ["owners", "admins"]
    },
    "admin@demo.local": {
        "id": "00000000-0000-0000-0000-000000000006",
        "password": "demo123",
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "tenant_slug": "demo",
        "role": "admin",
        "name": "Demo Admin",
        "groups": ["admins"]
    },
    "member@demo.local": {
        "id": "00000000-0000-0000-0000-000000000002",
        "password": "demo123",
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "tenant_slug": "demo",
        "role": "member",
        "name": "Demo Member",
        "groups": ["members"]
    },
    "owner@acme.local": {
        "id": "00000000-0000-0000-0000-000000000003",
        "password": "acme123",
        "tenant_id": "22222222-2222-2222-2222-222222222222",
        "tenant_slug": "acme",
        "role": "owner",
        "name": "Acme Owner",
        "groups": ["owners", "admins"]
    },
    "member@acme.local": {
        "id": "00000000-0000-0000-0000-000000000004",
        "password": "acme123",
        "tenant_id": "22222222-2222-2222-2222-222222222222",
        "tenant_slug": "acme",
        "role": "member",
        "name": "Acme Member",
        "groups": ["members"]
    },
    "viewer@test.local": {
        "id": "00000000-0000-0000-0000-000000000005",
        "password": "test123",
        "tenant_id": "33333333-3333-3333-3333-333333333333",
        "tenant_slug": "test",
        "role": "viewer",
        "name": "Test Viewer",
        "groups": ["viewers"]
    },
    # Oniva test users
    "owner@oniva.ai": {
        "id": "00000000-0000-0000-0000-000000000010",
        "password": "oniva123",
        "tenant_id": "44444444-4444-4444-4444-444444444444",
        "tenant_slug": "oniva",
        "role": "owner",
        "name": "Oniva Owner",
        "groups": ["owners", "admins"]
    },
    "admin@oniva.ai": {
        "id": "00000000-0000-0000-0000-000000000011",
        "password": "oniva123",
        "tenant_id": "44444444-4444-4444-4444-444444444444",
        "tenant_slug": "oniva",
        "role": "admin",
        "name": "Oniva Admin",
        "groups": ["admins"]
    },
    "member@oniva.ai": {
        "id": "00000000-0000-0000-0000-000000000012",
        "password": "oniva123",
        "tenant_id": "44444444-4444-4444-4444-444444444444",
        "tenant_slug": "oniva",
        "role": "member",
        "name": "Oniva Member",
        "groups": ["members"]
    },
    "viewer@oniva.ai": {
        "id": "00000000-0000-0000-0000-000000000013",
        "password": "oniva123",
        "tenant_id": "44444444-4444-4444-4444-444444444444",
        "tenant_slug": "oniva",
        "role": "viewer",
        "name": "Oniva Viewer",
        "groups": ["viewers"]
    }
}


class StubAuthProvider(BaseAuthProvider):
    """
    Stub authentication for development and POC.

    Usage:
        provider = StubAuthProvider()
        result = await provider.authenticate({
            "email": "admin@demo.local",
            "password": "demo123"
        })
        if result.success:
            user = AuthUser(...)

    Note: This provider is disabled in production. See is_enabled().
    """

    @property
    def provider_name(self) -> str:
        return "stub"

    @property
    def protocol(self) -> str:
        return "local"

    def is_enabled(self) -> bool:
        """
        Check if stub auth is enabled for the current environment.

        Uses fail-closed pattern: only explicitly allowed environments
        can use stub authentication. Production and any unknown
        environments will return False.

        Returns:
            True only if ENVIRONMENT is in ALLOWED_ENVIRONMENTS
        """
        env = os.getenv("ENVIRONMENT", "").lower()
        return env in ALLOWED_ENVIRONMENTS

    async def authenticate(self, credentials: dict) -> AuthResult:
        """Authenticate with email/password against preset users."""
        # Fail-closed: reject all authentication in non-allowed environments
        if not self.is_enabled():
            return AuthResult(
                success=False,
                provider=self.provider_name,
                error="Stub authentication is disabled in this environment"
            )

        email = credentials.get("email", "").lower()
        password = credentials.get("password", "")

        user_data = STUB_USERS.get(email)
        if not user_data:
            return AuthResult(
                success=False,
                provider=self.provider_name,
                error="Invalid email or password"
            )

        if user_data["password"] != password:
            return AuthResult(
                success=False,
                provider=self.provider_name,
                error="Invalid email or password"
            )

        return AuthResult(
            success=True,
            user_id=user_data["id"],
            email=email,
            name=user_data["name"],
            provider=self.provider_name,
            provider_user_id=user_data["id"],  # For stub, same as user_id
            groups=user_data.get("groups", []),
            metadata={
                "tenant_id": user_data["tenant_id"],
                "tenant_slug": user_data["tenant_slug"],
                "role": user_data["role"]
            }
        )

    async def validate_token(self, token: str) -> Optional[AuthUser]:
        """Validate JWT token and return user data."""
        user, _ = await self.validate_token_with_payload(token)
        return user

    async def validate_token_with_payload(self, token: str) -> tuple[Optional[AuthUser], Optional[dict]]:
        """
        Validate JWT token and return both user and raw payload.

        This method is used for blacklist checking (requires jti from payload).

        Returns:
            Tuple of (AuthUser or None, payload dict or None)
        """
        payload = decode_access_token(token)
        if not payload:
            return None, None

        # For stub auth, we trust the token claims
        user = AuthUser(
            id=payload.get("sub"),
            email=payload.get("email"),
            name=payload.get("name"),
            tenant_id=payload.get("tenant_id"),
            tenant_slug=payload.get("tenant_slug"),
            role=payload.get("role"),
            auth_provider=payload.get("auth_provider", "stub"),
            groups=payload.get("groups", []),
            is_platform_operator=payload.get("is_platform_operator", False),
        )
        return user, payload


def get_stub_users() -> list[dict]:
    """Return list of available stub users (for login UI)."""
    return [
        {
            "email": email,
            "name": data["name"],
            "role": data["role"],
            "tenant_slug": data["tenant_slug"]
        }
        for email, data in STUB_USERS.items()
    ]
