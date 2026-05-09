"""Authentication providers."""
from app.auth.providers.base import AuthUser, AuthResult, BaseAuthProvider
from app.auth.providers.stub import StubAuthProvider, get_stub_users
from app.auth.providers.local import (
    LocalAuthProvider,
    create_local_user,
    change_password,
    reset_password,
)

__all__ = [
    # Base classes
    'AuthUser',
    'AuthResult',
    'BaseAuthProvider',
    # Providers
    'StubAuthProvider',
    'LocalAuthProvider',
    # Utilities
    'get_stub_users',
    'create_local_user',
    'change_password',
    'reset_password',
]
