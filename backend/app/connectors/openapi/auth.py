"""
Authentication Handlers for OpenAPI Connectors

Supports multiple authentication methods:
- Bearer Token (OAuth2, JWT)
- API Key (header, query, cookie)
- Basic Auth
- OAuth2 with PKCE (for interactive flows)
"""

from abc import ABC, abstractmethod
from typing import Any
from dataclasses import dataclass
import secrets
import hashlib
import base64


@dataclass
class AuthConfig:
    """Authentication configuration from OpenAPI security scheme."""
    auth_type: str  # "bearer", "api_key", "basic", "oauth2"
    location: str = "header"  # "header", "query", "cookie"
    name: str = "Authorization"  # Header/query param name
    scheme: str = "Bearer"  # For Authorization header
    oauth2_config: dict[str, Any] | None = None


class AuthHandler(ABC):
    """Base class for authentication handlers."""

    @abstractmethod
    def apply(self, headers: dict[str, str], params: dict[str, Any]) -> None:
        """Apply authentication to request headers/params."""
        pass

    @abstractmethod
    def validate_credentials(self, credentials: dict[str, Any]) -> bool:
        """Validate that required credentials are present."""
        pass

    @classmethod
    def from_config(cls, config: AuthConfig, credentials: dict[str, Any]) -> "AuthHandler":
        """Create appropriate auth handler from config."""
        handlers = {
            "bearer": BearerAuth,
            "api_key": APIKeyAuth,
            "basic": BasicAuth,
            "oauth2": OAuth2Auth,
        }

        handler_cls = handlers.get(config.auth_type)
        if not handler_cls:
            raise ValueError(f"Unsupported auth type: {config.auth_type}")

        return handler_cls(config, credentials)


class BearerAuth(AuthHandler):
    """Bearer token authentication (OAuth2, JWT, API tokens)."""

    def __init__(self, config: AuthConfig, credentials: dict[str, Any]):
        self.config = config
        self.token = credentials.get("token") or credentials.get("api_key") or ""

    def apply(self, headers: dict[str, str], params: dict[str, Any]) -> None:
        """Add Bearer token to Authorization header."""
        if self.token:
            scheme = self.config.scheme or "Bearer"
            headers["Authorization"] = f"{scheme} {self.token}"

    def validate_credentials(self, credentials: dict[str, Any]) -> bool:
        """Check token is present."""
        return bool(credentials.get("token") or credentials.get("api_key"))


class APIKeyAuth(AuthHandler):
    """API Key authentication (header, query param, or cookie)."""

    def __init__(self, config: AuthConfig, credentials: dict[str, Any]):
        self.config = config
        self.api_key = credentials.get("api_key") or ""

    def apply(self, headers: dict[str, str], params: dict[str, Any]) -> None:
        """Add API key to configured location."""
        if not self.api_key:
            return

        location = self.config.location
        name = self.config.name

        if location == "header":
            headers[name] = self.api_key
        elif location == "query":
            params[name] = self.api_key
        elif location == "cookie":
            # Cookies are typically handled via headers
            existing = headers.get("Cookie", "")
            headers["Cookie"] = f"{existing}; {name}={self.api_key}" if existing else f"{name}={self.api_key}"

    def validate_credentials(self, credentials: dict[str, Any]) -> bool:
        """Check API key is present."""
        return bool(credentials.get("api_key"))


class BasicAuth(AuthHandler):
    """HTTP Basic authentication."""

    def __init__(self, config: AuthConfig, credentials: dict[str, Any]):
        self.config = config
        self.username = credentials.get("username") or ""
        self.password = credentials.get("password") or ""

    def apply(self, headers: dict[str, str], params: dict[str, Any]) -> None:
        """Add Basic auth header."""
        if self.username:
            credentials_str = f"{self.username}:{self.password}"
            encoded = base64.b64encode(credentials_str.encode()).decode()
            headers["Authorization"] = f"Basic {encoded}"

    def validate_credentials(self, credentials: dict[str, Any]) -> bool:
        """Check username is present."""
        return bool(credentials.get("username"))


class OAuth2Auth(AuthHandler):
    """
    OAuth2 authentication with PKCE support.

    For connectors requiring OAuth2 authorization code flow.
    Stores access_token after authorization completes.
    """

    def __init__(self, config: AuthConfig, credentials: dict[str, Any]):
        self.config = config
        self.access_token = credentials.get("access_token") or ""
        self.refresh_token = credentials.get("refresh_token")
        self.token_type = credentials.get("token_type", "Bearer")

    def apply(self, headers: dict[str, str], params: dict[str, Any]) -> None:
        """Add OAuth2 access token to Authorization header."""
        if self.access_token:
            headers["Authorization"] = f"{self.token_type} {self.access_token}"

    def validate_credentials(self, credentials: dict[str, Any]) -> bool:
        """Check access token is present."""
        return bool(credentials.get("access_token"))


# PKCE helpers for OAuth2 flows
def generate_pkce_pair() -> tuple[str, str]:
    """
    Generate PKCE code_verifier and code_challenge.

    Returns (code_verifier, code_challenge) tuple.
    """
    # Generate cryptographically random code_verifier (43-128 chars)
    code_verifier = secrets.token_urlsafe(64)

    # Create code_challenge using S256 method
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).decode().rstrip("=")

    return code_verifier, code_challenge


def generate_state_token() -> str:
    """Generate cryptographically random state parameter for CSRF protection."""
    return secrets.token_urlsafe(32)


def detect_auth_type(security_schemes: dict[str, dict]) -> AuthConfig:
    """
    Detect authentication type from OpenAPI security schemes.

    Returns AuthConfig for the first supported scheme.
    """
    for name, scheme in security_schemes.items():
        scheme_type = scheme.get("type", "").lower()

        if scheme_type == "http":
            http_scheme = scheme.get("scheme", "").lower()
            if http_scheme == "bearer":
                return AuthConfig(
                    auth_type="bearer",
                    scheme=scheme.get("bearerFormat", "Bearer"),
                )
            elif http_scheme == "basic":
                return AuthConfig(auth_type="basic")

        elif scheme_type == "apikey":
            return AuthConfig(
                auth_type="api_key",
                location=scheme.get("in", "header"),
                name=scheme.get("name", "X-API-Key"),
            )

        elif scheme_type == "oauth2":
            return AuthConfig(
                auth_type="oauth2",
                oauth2_config=scheme.get("flows", {}),
            )

    # Default to bearer if no schemes found
    return AuthConfig(auth_type="bearer")
