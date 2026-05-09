"""Authentication module for Oniva.

This module provides a pluggable authentication system supporting:
- Local (email/password) authentication
- SAML 2.0 for enterprise SSO
- OIDC for Google/Microsoft auth
- API keys for machine-to-machine access

See AUTH_ARCHITECTURE.md for the full design.
"""
from app.auth.providers.base import AuthUser, AuthResult, BaseAuthProvider
from app.auth.providers.stub import StubAuthProvider, get_stub_users
from app.auth.providers.local import (
    LocalAuthProvider,
    create_local_user,
    change_password,
    reset_password,
)
from app.auth.jwt import create_access_token, decode_access_token
from app.auth.service import AuthService, get_auth_service, get_stub_auth_service
from app.auth.dependencies import (
    get_current_user,
    get_current_user_id,
    get_current_tenant_id,
    get_optional_user,
    require_role,
)

__all__ = [
    # Core models
    'AuthUser',
    'AuthResult',
    'BaseAuthProvider',
    # Providers
    'StubAuthProvider',
    'LocalAuthProvider',
    'get_stub_users',
    # User management utilities
    'create_local_user',
    'change_password',
    'reset_password',
    # JWT utilities
    'create_access_token',
    'decode_access_token',
    # Auth service
    'AuthService',
    'get_auth_service',
    'get_stub_auth_service',
    # Dependencies
    'get_current_user',
    'get_current_user_id',
    'get_current_tenant_id',
    'get_optional_user',
    'require_role',
]
