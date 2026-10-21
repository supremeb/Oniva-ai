"""Authentication API endpoints.

This module provides the REST API for authentication, including:
- Login with email/password (stub or local provider)
- List available auth providers
- Get current user info
- OAuth/SAML login initiation (when providers are configured)
- OAuth/SAML callback handling

Tokens are set as httpOnly cookies for browser clients and also
returned in the response body for API/mobile clients.
"""
from typing import Optional
import secrets

from fastapi import APIRouter, HTTPException, status, Depends, Request, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from app.auth.service import get_stub_auth_service, get_auth_service
from app.auth.providers.stub import get_stub_users
from app.auth.dependencies import get_current_user
from app.auth.providers.base import AuthUser
from app.auth.token_blacklist import token_blacklist
from app.auth.login_lockout import login_lockout
from app.config import get_settings
from app.core.database import get_db, get_db_no_tenant
from sqlalchemy import select
from uuid import UUID
from app.middleware.csrf import generate_csrf_token, CSRF_COOKIE_NAME
from app.services.user_management import UserManagementService
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()
settings = get_settings()


# Cookie configuration
def _cookie_config() -> dict:
    """Base cookie configuration for access tokens."""
    return {
        "httponly": True,
        "secure": not settings.DEBUG,
        "samesite": "lax",
        "path": "/",
        "max_age": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    }


def _refresh_cookie_config() -> dict:
    """Cookie configuration for refresh tokens (restricted path)."""
    return {
        "httponly": True,
        "secure": not settings.DEBUG,
        "samesite": "strict",
        "path": "/api/v1/auth/refresh",
        "max_age": settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
    }


def _set_auth_cookies(
    response: JSONResponse,
    access_token: str,
    refresh_token: Optional[str] = None,
) -> None:
    """Set httpOnly auth cookies and CSRF token on a response."""
    response.set_cookie(key="access_token", value=access_token, **_cookie_config())

    if refresh_token:
        response.set_cookie(key="refresh_token", value=refresh_token, **_refresh_cookie_config())

    # Non-httpOnly cookie for client-side routing guards (is user logged in?)
    # TTL matches refresh token (7 days), not access token (15 min).
    # This is just a hint — actual auth uses httpOnly access/refresh tokens.
    response.set_cookie(
        key="is_authenticated",
        value="1",
        httponly=False,
        secure=not settings.DEBUG,
        samesite="lax",
        path="/",
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
    )

    # CSRF token (non-httpOnly so JS can read and send as header)
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=generate_csrf_token(),
        httponly=False,
        secure=not settings.DEBUG,
        samesite="lax",
        path="/",
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


def _clear_auth_cookies(response: JSONResponse) -> None:
    """Clear all auth cookies on logout."""
    for name in ("access_token", "refresh_token", "is_authenticated", CSRF_COOKIE_NAME):
        response.delete_cookie(key=name, path="/")
    # refresh token has a restricted path
    response.delete_cookie(key="refresh_token", path="/api/v1/auth/refresh")


# Request/Response Models

class LoginRequest(BaseModel):
    """Login request body."""
    email: str  # Allow .local domains for stub auth
    password: str
    provider: Optional[str] = None  # Auto-detect: local for DB users, stub for .local domains

    @field_validator('email')
    @classmethod
    def validate_email_format(cls, v: str) -> str:
        """Basic email format validation that allows .local domains."""
        if '@' not in v or '.' not in v.split('@')[-1]:
            raise ValueError('Invalid email format')
        return v.lower()


class LoginResponse(BaseModel):
    """Login response with JWT tokens."""
    access_token: str
    refresh_token: Optional[str] = None  # Only returned for DB-backed auth
    token_type: str = "bearer"
    expires_in: int  # seconds (for access token)
    refresh_token_expires_in: Optional[int] = None  # seconds (for refresh token)
    user: dict


class ProviderInfo(BaseModel):
    """Auth provider information."""
    name: str
    protocol: str
    is_sso: bool


class ProvidersResponse(BaseModel):
    """List of enabled auth providers."""
    providers: list[ProviderInfo]
    default: str


class StubUser(BaseModel):
    """Stub user for login UI."""
    email: str
    name: str
    role: str
    tenant_slug: str


class OAuthLoginResponse(BaseModel):
    """OAuth/SAML login initiation response."""
    login_url: str
    state: str


class RefreshRequest(BaseModel):
    """Token refresh request."""
    refresh_token: Optional[str] = None  # browsers send it in the httpOnly cookie instead


class SignupRequest(BaseModel):
    """User signup request."""
    email: str
    password: str
    name: str

    @field_validator('email')
    @classmethod
    def validate_email_format(cls, v: str) -> str:
        """Basic email format validation."""
        if '@' not in v or '.' not in v.split('@')[-1]:
            raise ValueError('Invalid email format')
        return v.lower()


class SignupResponse(BaseModel):
    """Signup response."""
    message: str
    user: dict


class CheckEmailResponse(BaseModel):
    """Check email response."""
    can_signup: bool
    message: str


class ChangePasswordRequest(BaseModel):
    """Change password request."""
    current_password: str
    new_password: str


# Endpoints

@router.get("/providers", response_model=ProvidersResponse)
async def list_providers():
    """
    List enabled authentication providers.

    Returns the available auth methods for the frontend login UI.
    Used to determine which login options to show (email/password,
    Google SSO, Microsoft SSO, SAML, etc.)
    """
    auth_service = get_stub_auth_service()
    providers = auth_service.get_enabled_providers()

    # Determine default provider
    provider_names = [p["name"] for p in providers]
    default = "stub" if "stub" in provider_names else (
        "local" if "local" in provider_names else (
            provider_names[0] if provider_names else None
        )
    )

    return ProvidersResponse(
        providers=[ProviderInfo(**p) for p in providers],
        default=default
    )


@router.post("/login", response_model=LoginResponse)
async def login(
    request: LoginRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db_no_tenant)
):
    """
    Authenticate user and return JWT tokens (access + refresh).

    Supports multiple providers:
    - stub: Development provider with preset users (auto-detected for .local domains)
    - local: Database-backed email/password (default for all other domains)

    For stub auth, use preset credentials:
    - admin@demo.local / demo123
    - analyst@demo.local / demo123
    - admin@acme.local / acme123
    - analyst@acme.local / acme123
    - viewer@test.local / test123

    Returns:
    - access_token: Short-lived JWT (15 min)
    - refresh_token: Long-lived opaque token (7 days) - only for DB-backed auth
    """
    # Auto-detect provider if not specified
    provider = request.provider
    if provider is None:
        # Use stub for .local domains (development users)
        # Use local for all other domains (real users)
        if request.email.endswith('.local'):
            provider = "stub"
        else:
            provider = "local"

    # Per-account lockout (keyed on email whether or not it exists: no user enumeration)
    retry_after = await login_lockout.retry_after(request.email)
    if retry_after is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed sign-in attempts. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )

    # Use DB-backed auth service to support local provider
    auth_service = await get_auth_service(db)

    user, tokens = await auth_service.authenticate(
        provider,
        {
            "email": request.email,
            "password": request.password
        }
    )

    if not user or not tokens:
        await login_lockout.record_failure(request.email)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    await login_lockout.reset(request.email)

    # Create refresh token for all auth providers (including stub)
    # If stub user UUIDs don't exist in users table, FK will fail —
    # the try/except handles this gracefully (login succeeds, no refresh token)
    refresh_token = None
    refresh_token_expires_in = None
    try:
        user_agent = http_request.headers.get("user-agent")
        # Get client IP (handle proxies)
        forwarded = http_request.headers.get("x-forwarded-for")
        ip_address = forwarded.split(",")[0].strip() if forwarded else http_request.client.host if http_request.client else None

        refresh_token, _ = await auth_service.create_refresh_token_for_user(
            user=user,
            user_agent=user_agent,
            ip_address=ip_address,
        )
        refresh_token_expires_in = settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60
    except Exception as e:
        # Log but don't fail login if refresh token creation fails
        # (e.g., stub user UUIDs not in users table → FK violation)
        import logging
        logging.getLogger(__name__).warning(f"Failed to create refresh token: {e}")

    login_data = LoginResponse(
        access_token=tokens["access_token"],
        refresh_token=refresh_token,
        token_type=tokens["token_type"],
        expires_in=tokens["expires_in"],
        refresh_token_expires_in=refresh_token_expires_in,
        user={
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "role": user.role,
            "tenant_id": user.tenant_id,
            "tenant_slug": user.tenant_slug,
            "groups": user.groups
        }
    )

    # Set httpOnly cookies for browser clients
    response = JSONResponse(content=login_data.model_dump(), status_code=200)
    _set_auth_cookies(response, tokens["access_token"], refresh_token)
    return response


@router.get("/login/{provider}")
async def oauth_login(
    provider: str,
    redirect_uri: str = Query(..., description="URL to redirect after auth")
):
    """
    Initiate OAuth/SAML login flow.

    This endpoint generates the authorization URL for external identity
    providers (Google, Microsoft, SAML IdP). The frontend should redirect
    the user to the returned login_url.

    Args:
        provider: Provider name (e.g., 'google_oidc', 'microsoft_oidc', 'saml')
        redirect_uri: URL to redirect back after successful authentication

    Returns:
        OAuthLoginResponse with login_url and state token
    """
    auth_service = get_stub_auth_service()
    state = secrets.token_urlsafe(32)

    login_url = await auth_service.get_login_url(provider, redirect_uri, state)
    if not login_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Provider '{provider}' not enabled or does not support SSO"
        )

    return OAuthLoginResponse(login_url=login_url, state=state)


@router.post("/callback/{provider}", response_model=LoginResponse)
async def oauth_callback(
    provider: str,
    request: Request
):
    """
    Handle OAuth/SAML callback from external identity provider.

    This endpoint receives the callback from the IdP after user authentication.
    It exchanges the auth code/SAML response for user info and issues JWT tokens.

    For OIDC providers: expects 'code' and 'state' query parameters
    For SAML providers: expects 'SAMLResponse' in POST body
    """
    auth_service = get_stub_auth_service()

    # Build credentials based on provider type
    auth_provider = auth_service.get_provider(provider)
    if not auth_provider:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Provider '{provider}' not found"
        )

    if auth_provider.protocol == "saml":
        # SAML callback comes as POST with form data
        form = await request.form()
        credentials = {
            "saml_response": form.get("SAMLResponse"),
            "request_data": {
                "https": request.url.scheme == "https",
                "http_host": request.url.netloc,
                "script_name": request.url.path,
                "post_data": dict(form)
            }
        }
    else:
        # OIDC callback comes as GET with query params
        params = dict(request.query_params)
        credentials = {
            "code": params.get("code"),
            "redirect_uri": str(request.url).split("?")[0]  # Remove query params
        }

    user, tokens = await auth_service.authenticate(provider, credentials)

    if not user or not tokens:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication failed"
        )

    callback_data = LoginResponse(
        access_token=tokens["access_token"],
        token_type=tokens["token_type"],
        expires_in=tokens["expires_in"],
        user={
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "role": user.role,
            "tenant_id": user.tenant_id,
            "tenant_slug": user.tenant_slug,
            "groups": user.groups
        }
    )

    response = JSONResponse(content=callback_data.model_dump(), status_code=200)
    _set_auth_cookies(response, tokens["access_token"])
    return response


@router.post("/refresh", response_model=LoginResponse)
async def refresh_token(
    request: Optional[RefreshRequest] = None,
    http_request: Request = None,
    db: AsyncSession = Depends(get_db_no_tenant)
):
    """
    Refresh access token using refresh token.

    The refresh token can be provided in:
    1. Request body (API clients)
    2. httpOnly cookie (browser clients)

    Implements secure token rotation:
    - Validates the refresh token against the database
    - Revokes the old refresh token
    - Issues a new access token + new refresh token
    - Returns fresh user data from database

    Security features:
    - Token rotation (each refresh token can only be used once)
    - Token family tracking (reuse detection)
    - Client metadata logging (user agent, IP)
    """
    # Get refresh token from body or cookie
    refresh_token_value = None
    if request and request.refresh_token:
        refresh_token_value = request.refresh_token
    elif http_request:
        refresh_token_value = http_request.cookies.get("refresh_token")

    if not refresh_token_value:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token required",
        )

    user_agent = http_request.headers.get("user-agent") if http_request else None
    forwarded = http_request.headers.get("x-forwarded-for") if http_request else None
    ip_address = forwarded.split(",")[0].strip() if forwarded else (http_request.client.host if http_request and http_request.client else None)

    auth_service = await get_auth_service(db)
    tokens = await auth_service.refresh_access_token(
        refresh_token=refresh_token_value,
        user_agent=user_agent,
        ip_address=ip_address,
    )

    if not tokens:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Get user info from the new access token
    from app.auth.jwt import decode_access_token
    payload = decode_access_token(tokens["access_token"])

    refresh_data = LoginResponse(
        access_token=tokens["access_token"],
        refresh_token=tokens.get("refresh_token"),
        token_type=tokens["token_type"],
        expires_in=tokens["expires_in"],
        refresh_token_expires_in=tokens.get("refresh_token_expires_in"),
        user={
            "id": payload.get("sub"),
            "email": payload.get("email"),
            "name": payload.get("name"),
            "role": payload.get("role"),
            "tenant_id": payload.get("tenant_id"),
            "tenant_slug": payload.get("tenant_slug"),
            "groups": payload.get("groups", [])
        } if payload else {}
    )

    # Set updated cookies for browser clients
    response = JSONResponse(content=refresh_data.model_dump(), status_code=200)
    _set_auth_cookies(response, tokens["access_token"], tokens.get("refresh_token"))
    return response


@router.get("/stub-users", response_model=list[StubUser])
async def list_stub_users():
    """
    List available stub users for development login UI.

    This endpoint is only enabled in development mode (DEBUG=True).
    Returns preset test users with their roles and tenants.
    """
    if not settings.DEBUG:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found"
        )

    return get_stub_users()


@router.get("/me")
async def get_current_user_info(
    user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_no_tenant),
):
    """
    Get current authenticated user information including resolved feature flags.

    Returns the user info extracted from the JWT token plus the tenant's
    resolved feature flags for frontend feature gating.
    """
    # Resolve tenant features for frontend
    features = {}
    try:
        from app.services.feature_flags import get_tenant_features
        from app.models.tenant import Tenant
        result = await db.execute(
            select(Tenant).where(Tenant.id == UUID(user.tenant_id))
        )
        tenant = result.scalar_one_or_none()
        if tenant:
            features = get_tenant_features(tenant)
    except Exception:
        pass  # Graceful degradation — features empty if resolution fails

    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "tenant_id": user.tenant_id,
        "tenant_slug": user.tenant_slug,
        "auth_provider": user.auth_provider,
        "groups": user.groups,
        "is_platform_operator": user.is_platform_operator,
        "features": features,
    }


class LogoutRequest(BaseModel):
    """Logout request body."""
    refresh_token: Optional[str] = None  # If provided, revoke this specific token
    logout_all: bool = False  # If true, revoke all user's refresh tokens


@router.post("/logout")
async def logout(
    request_body: Optional[LogoutRequest] = None,
    http_request: Request = None,
    user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_no_tenant)
):
    """
    Logout current user, blacklist access token, and optionally revoke refresh tokens.

    Options:
    - Default: Revoke the specific refresh_token if provided
    - logout_all=true: Revoke ALL refresh tokens for this user (logout everywhere)

    Security: The current access token JTI is blacklisted immediately via Redis,
    so it cannot be reused even before its natural expiration.
    """
    from uuid import UUID

    auth_service = await get_auth_service(db)

    # Blacklist the current access token JTI immediately
    if http_request and hasattr(http_request.state, 'token_payload') and http_request.state.token_payload:
        payload = http_request.state.token_payload
        jti = payload.get("jti")
        exp = payload.get("exp")
        if jti and exp:
            await token_blacklist.blacklist(jti, exp)

    tokens_revoked = 0

    if not (request_body and (request_body.logout_all or request_body.refresh_token)):
        # No refresh token named (e.g. empty body): revoke them all, so a bare logout
        # cannot leave a live 7-day refresh token behind.
        tokens_revoked = await auth_service.revoke_all_user_tokens(
            user_id=UUID(user.id),
            reason="logout"
        )
    elif request_body:
        if request_body.logout_all:
            # Revoke all tokens for this user
            tokens_revoked = await auth_service.revoke_all_user_tokens(
                user_id=UUID(user.id),
                reason="logout_all"
            )
        elif request_body.refresh_token:
            # Revoke specific token
            from app.auth.jwt import hash_refresh_token
            from app.models.refresh_token import RefreshToken
            from sqlalchemy import select

            token_hash = hash_refresh_token(request_body.refresh_token)
            query = select(RefreshToken).where(
                RefreshToken.token_hash == token_hash,
                RefreshToken.user_id == UUID(user.id),
            )
            result = await db.execute(query)
            token = result.scalar_one_or_none()

            if token and not token.revoked:
                token.revoke(reason="logout")
                await db.commit()
                tokens_revoked = 1

    response = JSONResponse(content={
        "message": "Logged out successfully",
        "tokens_revoked": tokens_revoked
    })
    _clear_auth_cookies(response)
    return response


# =========================================
# Signup & Password Endpoints
# =========================================

@router.get("/check-email/{email}", response_model=CheckEmailResponse)
async def check_email(
    email: str,
    db: AsyncSession = Depends(get_db_no_tenant)
):
    """
    Check if an email can sign up.

    Returns whether the email is in the pending users list
    and can complete signup.
    """
    service = UserManagementService(db)
    can_signup, pending_user = await service.check_can_signup(email.lower())

    if can_signup:
        return CheckEmailResponse(
            can_signup=True,
            message="Email found. You can sign up."
        )
    elif pending_user is None:
        return CheckEmailResponse(
            can_signup=False,
            message="Email not found. Contact your admin to be added to the organization."
        )
    else:
        return CheckEmailResponse(
            can_signup=False,
            message="An account with this email already exists. Please log in."
        )


@router.post("/signup", response_model=SignupResponse)
async def signup(
    request: SignupRequest,
    db: AsyncSession = Depends(get_db_no_tenant)
):
    """
    Complete user signup.

    The email must be in the pending users list (added by an admin).
    Creates the user account with the pre-assigned role.
    """
    service = UserManagementService(db)

    try:
        user = await service.signup_user(
            email=request.email,
            password=request.password,
            name=request.name
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

    return SignupResponse(
        message="Account created successfully. You can now log in.",
        user={
            "id": str(user.id),
            "email": user.email,
            "name": user.name,
            "role": user.role,
            "tenant_id": str(user.tenant_id)
        }
    )


@router.post("/change-password")
async def change_password(
    request_body: ChangePasswordRequest,
    http_request: Request = None,
    user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_no_tenant)
):
    """
    Change current user's password.

    Requires the current password for verification.
    Security: Revokes all refresh tokens and blacklists the current access token
    to force re-authentication on all sessions.
    """
    service = UserManagementService(db)

    try:
        await service.change_password(
            user_id=user.id,
            current_password=request_body.current_password,
            new_password=request_body.new_password,
            tenant_id=user.tenant_id
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

    # Revoke all refresh tokens for this user
    auth_service = await get_auth_service(db)
    await auth_service.revoke_all_user_tokens(
        user_id=UUID(user.id),
        reason="password_change"
    )

    # Blacklist the current access token JTI
    if http_request and hasattr(http_request.state, 'token_payload') and http_request.state.token_payload:
        payload = http_request.state.token_payload
        jti = payload.get("jti")
        exp = payload.get("exp")
        if jti and exp:
            await token_blacklist.blacklist(jti, exp)

    return {"message": "Password changed successfully"}
