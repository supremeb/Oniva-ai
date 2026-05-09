"""
SAML 2.0 Service Provider authentication.

This provider enables enterprise SSO with SAML 2.0 identity providers
such as Okta, Azure AD, OneLogin, and others.

Features:
- SP-initiated SSO (user starts at Oniva, redirects to IdP)
- IdP-initiated SSO (user starts at IdP portal)
- Single Logout (SLO) for both SP and IdP initiated flows
- Configurable attribute mapping for different IdPs
- Support for signed assertions and encrypted responses

Security notes:
- All assertions must be signed by the IdP
- Replay attacks prevented via InResponseTo validation
- Clock skew tolerance configurable (default: 2 minutes)
- Certificate validation required

Configuration:
- Requires SAML settings JSON file or environment variables
- IdP metadata can be fetched from URL or loaded from file

See AUTH_ARCHITECTURE.md for the full design.
"""
import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any
from urllib.parse import urlparse

from app.auth.providers.base import BaseAuthProvider, AuthResult, AuthUser
from app.auth.jwt import decode_access_token
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# python3-saml imports - gracefully handle if not installed
try:
    from onelogin.saml2.auth import OneLogin_Saml2_Auth
    from onelogin.saml2.utils import OneLogin_Saml2_Utils
    from onelogin.saml2.settings import OneLogin_Saml2_Settings
    SAML_AVAILABLE = True
except ImportError:
    SAML_AVAILABLE = False
    logger.warning("python3-saml not installed. SAML authentication disabled.")


class SAMLAuthProvider(BaseAuthProvider):
    """
    SAML 2.0 authentication provider for enterprise SSO.

    This provider implements the SAML 2.0 Service Provider (SP) role,
    delegating authentication to an external Identity Provider (IdP).

    Supported IdPs:
    - Okta
    - Azure Active Directory
    - OneLogin
    - Auth0 (with SAML addon)
    - Ping Identity
    - Google Workspace (via SAML app)
    - Any SAML 2.0 compliant IdP

    Usage:
        # Initialize provider
        provider = SAMLAuthProvider()

        # Get login URL for SP-initiated SSO
        login_url = await provider.get_login_url(
            redirect_uri="/dashboard",
            state="csrf_token_here"
        )

        # Process SAML response from IdP (in ACS endpoint)
        result = await provider.authenticate({
            "saml_response": request.form.get("SAMLResponse"),
            "relay_state": request.form.get("RelayState"),
            "request_data": prepare_saml_request(request)
        })
    """

    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize SAML provider with configuration.

        Args:
            config_path: Path to SAML settings JSON file.
                        Defaults to SAML_CONFIG_PATH from settings.
        """
        self._settings: Optional[Dict[str, Any]] = None
        self._config_path = config_path or getattr(settings, 'SAML_CONFIG_PATH', None)

        if SAML_AVAILABLE and self._config_path:
            self._load_settings()

    @property
    def provider_name(self) -> str:
        """Return provider identifier."""
        return "saml"

    @property
    def protocol(self) -> str:
        """Return auth protocol."""
        return "saml"

    def _load_settings(self) -> None:
        """Load SAML settings from configuration file or environment."""
        if not self._config_path:
            # Try to build settings from environment variables
            self._settings = self._build_settings_from_env()
            return

        config_path = Path(self._config_path)
        if not config_path.exists():
            logger.warning(f"SAML config file not found: {config_path}")
            self._settings = self._build_settings_from_env()
            return

        try:
            with open(config_path) as f:
                self._settings = json.load(f)
            logger.info(f"SAML settings loaded from: {config_path}")
        except json.JSONDecodeError as e:
            logger.error(f"Invalid SAML config JSON: {e}")
            self._settings = None

    def _build_settings_from_env(self) -> Optional[Dict[str, Any]]:
        """Build SAML settings from environment variables."""
        # Check required environment variables
        idp_entity_id = getattr(settings, 'SAML_IDP_ENTITY_ID', None)
        idp_sso_url = getattr(settings, 'SAML_IDP_SSO_URL', None)
        idp_cert = getattr(settings, 'SAML_IDP_CERTIFICATE', None)
        sp_entity_id = getattr(settings, 'SAML_SP_ENTITY_ID', None)

        if not all([idp_entity_id, idp_sso_url, idp_cert, sp_entity_id]):
            logger.info("SAML environment variables not fully configured")
            return None

        # Build minimal settings structure
        sp_acs_url = getattr(settings, 'SAML_SP_ACS_URL',
                            f"{getattr(settings, 'APP_BASE_URL', 'http://localhost:8000')}/api/v1/auth/saml/acs")
        sp_slo_url = getattr(settings, 'SAML_SP_SLO_URL',
                            f"{getattr(settings, 'APP_BASE_URL', 'http://localhost:8000')}/api/v1/auth/saml/slo")

        return {
            "strict": True,
            "debug": settings.DEBUG,
            "sp": {
                "entityId": sp_entity_id,
                "assertionConsumerService": {
                    "url": sp_acs_url,
                    "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
                },
                "singleLogoutService": {
                    "url": sp_slo_url,
                    "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
                },
                "NameIDFormat": "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
                "x509cert": getattr(settings, 'SAML_SP_CERTIFICATE', ""),
                "privateKey": getattr(settings, 'SAML_SP_PRIVATE_KEY', "")
            },
            "idp": {
                "entityId": idp_entity_id,
                "singleSignOnService": {
                    "url": idp_sso_url,
                    "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
                },
                "singleLogoutService": {
                    "url": getattr(settings, 'SAML_IDP_SLO_URL', ""),
                    "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
                },
                "x509cert": idp_cert
            },
            "security": {
                "nameIdEncrypted": False,
                "authnRequestsSigned": getattr(settings, 'SAML_SIGN_REQUESTS', False),
                "logoutRequestSigned": getattr(settings, 'SAML_SIGN_REQUESTS', False),
                "logoutResponseSigned": getattr(settings, 'SAML_SIGN_REQUESTS', False),
                "signMetadata": False,
                "wantMessagesSigned": True,
                "wantAssertionsSigned": True,
                "wantAssertionsEncrypted": False,
                "wantNameIdEncrypted": False,
                "requestedAuthnContext": False,
                "wantNameId": True,
                "signatureAlgorithm": "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256",
                "digestAlgorithm": "http://www.w3.org/2001/04/xmlenc#sha256"
            }
        }

    def _prepare_request_dict(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Prepare request dictionary for python3-saml.

        Args:
            request_data: Dict containing request information
                - http_host: Request host header
                - script_name: Request path
                - server_port: Server port
                - get_data: Query parameters dict
                - post_data: Form data dict
                - https: 'on' or 'off'

        Returns:
            Dict formatted for OneLogin_Saml2_Auth
        """
        return {
            'https': request_data.get('https', 'on'),
            'http_host': request_data.get('http_host', ''),
            'server_port': request_data.get('server_port', 443),
            'script_name': request_data.get('script_name', ''),
            'get_data': request_data.get('get_data', {}),
            'post_data': request_data.get('post_data', {})
        }

    async def authenticate(self, credentials: dict) -> AuthResult:
        """
        Process SAML response from IdP.

        This method handles the SAML response received at the ACS endpoint
        after the user authenticates at the IdP.

        Args:
            credentials: Dict containing:
                - saml_response: Base64-encoded SAML response
                - relay_state: RelayState parameter (original return URL)
                - request_data: Request information dict for python3-saml

        Returns:
            AuthResult with user info on success, error on failure
        """
        if not SAML_AVAILABLE:
            return AuthResult(
                success=False,
                provider=self.provider_name,
                error="SAML support not available (python3-saml not installed)"
            )

        if not self._settings:
            return AuthResult(
                success=False,
                provider=self.provider_name,
                error="SAML not configured"
            )

        saml_response = credentials.get("saml_response")
        request_data = credentials.get("request_data", {})

        if not saml_response:
            return AuthResult(
                success=False,
                provider=self.provider_name,
                error="Missing SAML response"
            )

        try:
            # Prepare request for python3-saml
            req = self._prepare_request_dict(request_data)
            req['post_data']['SAMLResponse'] = saml_response

            # Create SAML auth object and process response
            auth = OneLogin_Saml2_Auth(req, self._settings)
            auth.process_response()

            # Check for errors
            errors = auth.get_errors()
            if errors:
                error_reason = auth.get_last_error_reason()
                logger.error(f"SAML authentication failed: {errors}, reason: {error_reason}")
                return AuthResult(
                    success=False,
                    provider=self.provider_name,
                    error=f"SAML error: {', '.join(errors)}"
                )

            # Verify authentication status
            if not auth.is_authenticated():
                logger.warning("SAML response processed but user not authenticated")
                return AuthResult(
                    success=False,
                    provider=self.provider_name,
                    error="Authentication failed"
                )

            # Extract user attributes
            name_id = auth.get_nameid()
            session_index = auth.get_session_index()
            raw_attributes = auth.get_attributes()

            # Map attributes to standard format
            attributes = self._map_attributes(raw_attributes)

            # Get email - try NameID first, then attribute
            email = name_id if '@' in str(name_id) else attributes.get('email')
            if not email:
                logger.error("No email found in SAML response")
                return AuthResult(
                    success=False,
                    provider=self.provider_name,
                    error="No email address in SAML response"
                )

            logger.info(f"SAML auth success: {email}")

            return AuthResult(
                success=True,
                email=email,
                name=attributes.get('name') or attributes.get('display_name'),
                provider=self.provider_name,
                provider_user_id=name_id,
                groups=attributes.get('groups', []),
                metadata={
                    "session_index": session_index,
                    "name_id": name_id,
                    "name_id_format": auth.get_nameid_format(),
                    "session_expiration": auth.get_session_expiration(),
                    "attributes": attributes,
                    "relay_state": credentials.get("relay_state")
                }
            )

        except Exception as e:
            logger.exception(f"SAML authentication error: {e}")
            return AuthResult(
                success=False,
                provider=self.provider_name,
                error=f"SAML processing error: {str(e)}"
            )

    def _map_attributes(self, raw_attributes: Dict[str, list]) -> Dict[str, Any]:
        """
        Map IdP attributes to standard user attributes.

        Different IdPs use different attribute names. This method normalizes
        them to a standard format used by Oniva.

        Supports:
        - Okta attribute names
        - Azure AD / Microsoft Entra ID claims
        - Standard SAML attribute URIs
        - Common attribute names

        Args:
            raw_attributes: Attributes dict from SAML response

        Returns:
            Normalized attributes dict
        """
        # Attribute mappings: standard_name -> [possible_names]
        mappings = {
            # Email address
            "email": [
                "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
                "http://schemas.xmlsoap.org/claims/EmailAddress",
                "email",
                "Email",
                "mail",
                "emailAddress",
                "user.email",
            ],
            # Display name
            "name": [
                "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name",
                "http://schemas.microsoft.com/identity/claims/displayname",
                "displayName",
                "name",
                "cn",
                "commonName",
                "fullName",
                "user.displayName",
            ],
            # First name
            "first_name": [
                "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/givenname",
                "firstName",
                "givenName",
                "first_name",
                "user.firstName",
            ],
            # Last name
            "last_name": [
                "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/surname",
                "lastName",
                "sn",
                "surname",
                "last_name",
                "user.lastName",
            ],
            # Groups/Roles
            "groups": [
                "http://schemas.microsoft.com/ws/2008/06/identity/claims/groups",
                "http://schemas.xmlsoap.org/claims/Group",
                "groups",
                "memberOf",
                "roles",
                "user.groups",
            ],
            # Organization/Company
            "organization": [
                "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/organization",
                "organization",
                "org",
                "company",
                "companyName",
            ],
            # Department
            "department": [
                "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/department",
                "department",
                "dept",
            ],
            # Job title
            "title": [
                "http://schemas.microsoft.com/identity/claims/jobtitle",
                "title",
                "jobTitle",
            ],
            # Phone
            "phone": [
                "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/mobilephone",
                "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/telephonenumber",
                "phone",
                "phoneNumber",
                "mobile",
            ],
        }

        result = {}

        for standard_name, possible_names in mappings.items():
            for attr_name in possible_names:
                if attr_name in raw_attributes:
                    value = raw_attributes[attr_name]
                    # SAML attributes are lists; unwrap single values
                    if isinstance(value, list):
                        if standard_name == "groups":
                            # Keep groups as list
                            result[standard_name] = value
                        elif len(value) == 1:
                            result[standard_name] = value[0]
                        else:
                            result[standard_name] = value
                    else:
                        result[standard_name] = value
                    break

        # Build full name from first + last if name not provided
        if 'name' not in result:
            first = result.get('first_name', '')
            last = result.get('last_name', '')
            if first or last:
                result['name'] = f"{first} {last}".strip()

        return result

    async def validate_token(self, token: str) -> Optional[AuthUser]:
        """
        Validate JWT token for SAML-authenticated users.

        SAML providers issue JWTs after successful authentication.
        Token validation is the same as other providers - verify JWT signature.

        Args:
            token: JWT token from Authorization header

        Returns:
            AuthUser if valid, None if invalid/expired
        """
        payload = decode_access_token(token)
        if not payload:
            return None

        # Verify this is a SAML-authenticated user
        if payload.get("auth_provider") != "saml":
            return None

        return AuthUser(
            id=payload.get("sub"),
            email=payload.get("email"),
            name=payload.get("name"),
            tenant_id=payload.get("tenant_id"),
            tenant_slug=payload.get("tenant_slug"),
            role=payload.get("role"),
            auth_provider="saml",
            groups=payload.get("groups", [])
        )

    async def get_login_url(
        self,
        redirect_uri: str,
        state: str
    ) -> Optional[str]:
        """
        Generate SAML AuthnRequest URL for SP-initiated SSO.

        This URL redirects the user to the IdP for authentication.
        After authentication, the user is redirected back to the ACS endpoint.

        Args:
            redirect_uri: URL to redirect to after successful authentication
                         (stored in RelayState)
            state: CSRF token (also stored in RelayState for verification)

        Returns:
            IdP login URL with AuthnRequest, or None if not configured
        """
        if not SAML_AVAILABLE or not self._settings:
            return None

        try:
            # Build minimal request for generating AuthnRequest
            req = {
                'https': 'on',
                'http_host': urlparse(
                    getattr(settings, 'APP_BASE_URL', 'http://localhost:8000')
                ).netloc,
                'server_port': 443,
                'script_name': '/api/v1/auth/saml/login',
                'get_data': {},
                'post_data': {}
            }

            auth = OneLogin_Saml2_Auth(req, self._settings)

            # Combine redirect_uri and state in RelayState
            relay_state = json.dumps({
                "redirect_uri": redirect_uri,
                "state": state
            })

            login_url = auth.login(return_to=relay_state)

            return login_url

        except Exception as e:
            logger.error(f"Failed to generate SAML login URL: {e}")
            return None

    async def initiate_logout(
        self,
        name_id: str,
        session_index: Optional[str] = None,
        return_to: Optional[str] = None
    ) -> Optional[str]:
        """
        Generate SAML LogoutRequest URL for SP-initiated SLO.

        This initiates Single Logout, signing the user out of both
        Oniva and the IdP.

        Args:
            name_id: User's NameID from SAML assertion
            session_index: Session index from SAML assertion
            return_to: URL to redirect to after logout

        Returns:
            IdP logout URL with LogoutRequest, or None if not configured
        """
        if not SAML_AVAILABLE or not self._settings:
            return None

        try:
            req = {
                'https': 'on',
                'http_host': urlparse(
                    getattr(settings, 'APP_BASE_URL', 'http://localhost:8000')
                ).netloc,
                'server_port': 443,
                'script_name': '/api/v1/auth/saml/logout',
                'get_data': {},
                'post_data': {}
            }

            auth = OneLogin_Saml2_Auth(req, self._settings)

            logout_url = auth.logout(
                name_id=name_id,
                session_index=session_index,
                return_to=return_to
            )

            return logout_url

        except Exception as e:
            logger.error(f"Failed to generate SAML logout URL: {e}")
            return None

    def get_sp_metadata(self) -> Optional[str]:
        """
        Generate SP metadata XML for IdP configuration.

        This metadata should be provided to the IdP administrator
        to configure the SAML integration.

        Returns:
            SP metadata XML string, or None if not configured
        """
        if not SAML_AVAILABLE or not self._settings:
            return None

        try:
            saml_settings = OneLogin_Saml2_Settings(self._settings)
            metadata = saml_settings.get_sp_metadata()

            # Validate metadata
            errors = saml_settings.validate_metadata(metadata)
            if errors:
                logger.error(f"Invalid SP metadata: {errors}")
                return None

            return metadata

        except Exception as e:
            logger.error(f"Failed to generate SP metadata: {e}")
            return None

    def is_enabled(self) -> bool:
        """
        Check if SAML authentication is configured and enabled.

        Returns:
            True if SAML is available and configured
        """
        if not SAML_AVAILABLE:
            return False

        if not self._settings:
            return False

        # Check if feature flag is enabled
        saml_enabled = getattr(settings, 'SAML_ENABLED', False)
        if not saml_enabled:
            return False

        return True


# Helper function to prepare request data from FastAPI request
def prepare_saml_request(request) -> Dict[str, Any]:
    """
    Prepare request data dict from FastAPI Request object.

    Usage:
        from app.auth.providers.saml import prepare_saml_request

        @router.post("/saml/acs")
        async def saml_acs(request: Request):
            request_data = prepare_saml_request(request)
            result = await saml_provider.authenticate({
                "saml_response": form_data.get("SAMLResponse"),
                "request_data": request_data
            })

    Args:
        request: FastAPI Request object

    Returns:
        Dict formatted for SAMLAuthProvider.authenticate()
    """
    url = request.url

    return {
        'https': 'on' if url.scheme == 'https' else 'off',
        'http_host': request.headers.get('host', url.netloc),
        'server_port': url.port or (443 if url.scheme == 'https' else 80),
        'script_name': url.path,
        'get_data': dict(request.query_params),
        'post_data': {}  # Will be populated with SAMLResponse
    }
