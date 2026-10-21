"""
SAML 2.0 Authentication Endpoints.

This module provides dedicated SAML endpoints:
- /saml/metadata - SP metadata XML for IdP configuration
- /saml/login - SP-initiated SSO (redirect to IdP)
- /saml/acs - Assertion Consumer Service (receive SAML response)
- /saml/slo - Single Logout endpoint
- /saml/sls - Single Logout Service (receive logout response)

These endpoints follow the SAML 2.0 Web SSO Profile specification.
"""
import json
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, status, Request, Depends, Query, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.providers.saml import SAMLAuthProvider, prepare_saml_request
from app.auth.service import AuthService
from app.auth.jwt import create_access_token
from app.config import get_settings
from app.core.database import get_db_no_tenant

router = APIRouter(prefix="/saml", tags=["saml"])
settings = get_settings()
logger = logging.getLogger(__name__)

# Lazy-initialize SAML provider
_saml_provider: Optional[SAMLAuthProvider] = None


def get_saml_provider() -> SAMLAuthProvider:
    """Get or create SAML provider instance."""
    global _saml_provider
    if _saml_provider is None:
        _saml_provider = SAMLAuthProvider()
    return _saml_provider


def require_saml_enabled() -> SAMLAuthProvider:
    """Dependency to ensure SAML is enabled."""
    provider = get_saml_provider()
    if not provider.is_enabled():
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="SAML authentication is not enabled. Configure SAML_ENABLED=true and provide IdP settings."
        )
    return provider


# Response Models

class SAMLLoginInitiated(BaseModel):
    """Response when SAML login is initiated."""
    message: str
    redirect_url: str


class SAMLStatusResponse(BaseModel):
    """SAML configuration status."""
    enabled: bool
    configured: bool
    sp_entity_id: Optional[str] = None
    idp_entity_id: Optional[str] = None


# Endpoints

@router.get("/status", response_model=SAMLStatusResponse)
async def saml_status():
    """
    Check SAML configuration status.

    Returns whether SAML is enabled and properly configured.
    Useful for frontend to determine if SAML login should be shown.
    """
    provider = get_saml_provider()

    # Check if SAML library is available and configured
    try:
        from app.auth.providers.saml import SAML_AVAILABLE
        saml_available = SAML_AVAILABLE
    except ImportError:
        saml_available = False

    return SAMLStatusResponse(
        enabled=settings.SAML_ENABLED,
        configured=provider.is_enabled() if saml_available else False,
        sp_entity_id=settings.SAML_SP_ENTITY_ID,
        idp_entity_id=settings.SAML_IDP_ENTITY_ID
    )


@router.get("/metadata")
async def sp_metadata(provider: SAMLAuthProvider = Depends(require_saml_enabled)):
    """
    Return SP metadata XML for IdP configuration.

    This endpoint provides the SAML Service Provider metadata that
    should be given to the Identity Provider administrator for setup.

    The metadata includes:
    - SP Entity ID
    - Assertion Consumer Service (ACS) URL
    - Single Logout Service URL
    - NameID format requirements
    - Signing certificate (if configured)

    Usage:
        1. Access this endpoint: GET /api/v1/auth/saml/metadata
        2. Copy the XML output
        3. Import into your IdP (Okta, Azure AD, etc.)
    """
    metadata = provider.get_sp_metadata()

    if not metadata:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate SP metadata. Check SAML configuration."
        )

    return Response(
        content=metadata,
        media_type="application/xml",
        headers={
            "Content-Disposition": "inline; filename=sp-metadata.xml"
        }
    )


@router.get("/login")
async def saml_login(
    request: Request,
    return_to: str = Query(
        default="/dashboard",
        description="URL to redirect to after successful authentication"
    ),
    provider: SAMLAuthProvider = Depends(require_saml_enabled)
):
    """
    Initiate SAML SP-initiated SSO.

    This endpoint starts the SAML authentication flow by redirecting
    the user to the Identity Provider for authentication.

    Flow:
    1. User clicks "Login with SSO" on the frontend
    2. Frontend calls this endpoint with return_to URL
    3. This endpoint generates a SAML AuthnRequest
    4. User is redirected to IdP with the AuthnRequest
    5. User authenticates at IdP
    6. IdP redirects user back to /saml/acs with SAML Response
    7. User is redirected to the original return_to URL

    Args:
        return_to: URL to redirect to after successful authentication

    Returns:
        Redirect to Identity Provider SSO URL
    """
    import secrets
    state = secrets.token_urlsafe(32)

    login_url = await provider.get_login_url(
        redirect_uri=return_to,
        state=state
    )

    if not login_url:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate SAML login URL"
        )

    logger.info(f"Initiating SAML login, redirecting to IdP")
    return RedirectResponse(url=login_url, status_code=302)


@router.post("/acs")
async def assertion_consumer_service(
    request: Request,
    db: AsyncSession = Depends(get_db_no_tenant, scope="function"),
    provider: SAMLAuthProvider = Depends(require_saml_enabled)
):
    """
    Assertion Consumer Service (ACS) endpoint.

    This endpoint receives the SAML Response from the IdP after
    successful authentication. It:
    1. Validates the SAML Response signature
    2. Extracts user attributes from the assertion
    3. Creates or updates the user in the database
    4. Issues a JWT token
    5. Redirects to the original requested URL

    Security checks:
    - Assertion signature verification
    - InResponseTo validation (replay prevention)
    - Timestamp validation (NotBefore/NotOnOrAfter)
    - Audience restriction validation

    Request:
        POST with form data containing:
        - SAMLResponse: Base64-encoded SAML response
        - RelayState: Optional original URL (JSON with redirect_uri and state)
    """
    # Get form data
    form = await request.form()
    saml_response = form.get("SAMLResponse")
    relay_state = form.get("RelayState", "{}")

    if not saml_response:
        logger.error("ACS endpoint called without SAMLResponse")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing SAML Response"
        )

    # Prepare request data for python3-saml
    request_data = prepare_saml_request(request)

    # Authenticate with SAML provider
    result = await provider.authenticate({
        "saml_response": saml_response,
        "relay_state": relay_state,
        "request_data": request_data
    })

    if not result.success:
        logger.error(f"SAML authentication failed: {result.error}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"SAML authentication failed: {result.error}"
        )

    # Get or create user from auth result
    auth_service = AuthService(db)
    user = await auth_service._get_or_create_user(result)

    if not user:
        # If user doesn't exist, we might want to auto-provision
        # For now, return an error
        logger.warning(f"No user found for SAML email: {result.email}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User not provisioned. Contact your administrator."
        )

    # Generate JWT token
    access_token = create_access_token(
        user_id=user.id,
        email=user.email,
        tenant_id=user.tenant_id,
        tenant_slug=user.tenant_slug,
        role=user.role,
        name=user.name,
        auth_provider="saml"
    )

    # Parse relay state to get redirect URL
    try:
        relay_data = json.loads(relay_state) if relay_state else {}
        redirect_uri = relay_data.get("redirect_uri", "/dashboard")
    except (json.JSONDecodeError, TypeError):
        redirect_uri = str(relay_state) if relay_state else "/dashboard"

    # Build redirect URL with token
    # Option 1: Token in URL hash (for SPA)
    # Option 2: Set HTTP-only cookie (more secure)
    # We'll use cookie for security
    response = RedirectResponse(
        url=f"{settings.APP_BASE_URL}{redirect_uri}",
        status_code=302
    )

    # Set JWT as HTTP-only cookie
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=not settings.DEBUG,  # Secure in production
        samesite="lax",
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/"
    )

    # Also store SAML session info for SLO
    session_metadata = result.metadata or {}
    if session_metadata.get("session_index"):
        response.set_cookie(
            key="saml_session_index",
            value=session_metadata.get("session_index"),
            httponly=True,
            secure=not settings.DEBUG,
            samesite="lax",
            max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            path="/"
        )

    logger.info(f"SAML authentication successful for: {result.email}")
    return response


@router.get("/logout")
async def saml_logout(
    request: Request,
    return_to: str = Query(
        default="/login",
        description="URL to redirect to after logout"
    ),
    provider: SAMLAuthProvider = Depends(require_saml_enabled)
):
    """
    Initiate SAML Single Logout (SLO).

    This endpoint starts the logout process by:
    1. Getting the user's SAML session info from cookies
    2. Generating a SAML LogoutRequest
    3. Redirecting to the IdP's SLO endpoint
    4. Clearing local session cookies

    The IdP will:
    1. Terminate the user's IdP session
    2. Optionally notify other SPs of the logout
    3. Redirect back to our /saml/sls endpoint

    For IdP-initiated logout, the IdP will send a LogoutRequest
    to our /saml/sls endpoint directly.
    """
    # Get SAML session info from cookies
    session_index = request.cookies.get("saml_session_index")
    access_token = request.cookies.get("access_token")

    # Try to get name_id from JWT
    name_id = None
    if access_token:
        from app.auth.jwt import decode_access_token
        payload = decode_access_token(access_token)
        if payload:
            name_id = payload.get("email")

    if not name_id:
        # Can't do SLO without user identity - just clear local session
        logger.warning("SAML logout requested but no name_id available, clearing local session only")
        response = RedirectResponse(url=return_to, status_code=302)
        response.delete_cookie("access_token", path="/")
        response.delete_cookie("saml_session_index", path="/")
        return response

    # Generate logout URL
    logout_url = await provider.initiate_logout(
        name_id=name_id,
        session_index=session_index,
        return_to=f"{settings.APP_BASE_URL}{return_to}"
    )

    if not logout_url:
        # IdP doesn't support SLO - just clear local session
        logger.info("SAML SLO not available, clearing local session only")
        response = RedirectResponse(url=return_to, status_code=302)
        response.delete_cookie("access_token", path="/")
        response.delete_cookie("saml_session_index", path="/")
        return response

    # Clear local cookies and redirect to IdP for SLO
    response = RedirectResponse(url=logout_url, status_code=302)
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("saml_session_index", path="/")

    logger.info(f"Initiating SAML SLO for: {name_id}")
    return response


@router.get("/sls")
@router.post("/sls")
async def single_logout_service(
    request: Request,
    provider: SAMLAuthProvider = Depends(require_saml_enabled)
):
    """
    Single Logout Service (SLS) endpoint.

    This endpoint handles:
    1. Logout responses from IdP (after SP-initiated logout)
    2. Logout requests from IdP (IdP-initiated logout)

    For logout responses:
    - Validates the response signature
    - Clears local session
    - Redirects to post-logout URL

    For logout requests:
    - Terminates the local session
    - Generates and sends a LogoutResponse
    - Can notify other applications (if applicable)
    """
    # Get SAML message from request
    params = dict(request.query_params)
    saml_response = params.get("SAMLResponse")
    saml_request = params.get("SAMLRequest")

    # Clear local session regardless of message type
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("saml_session_index", path="/")

    if saml_response:
        # SLO signature validation is not yet implemented; refusing to
        # silently accept an unverified logout response.
        logger.info("Received SAML logout response from IdP")
        raise HTTPException(
            status_code=501,
            detail="SAML SLO signature validation is not implemented in this build.",
        )

    elif saml_request:
        # IdP-initiated SLO is not yet implemented.
        logger.info("Received SAML logout request from IdP")
        raise HTTPException(
            status_code=501,
            detail="IdP-initiated SLO is not implemented in this build.",
        )

    else:
        # Direct access - just clear session
        logger.info("SLS accessed without SAML message, clearing session")
        return response
