"""
Unit tests for SAML Authentication Provider.

Tests cover:
- SAMLAuthProvider initialization and configuration
- Attribute mapping from different IdPs
- SAML response processing
- Login URL generation
- Error handling

Note: These tests use mocking since actual SAML flows require an IdP.
"""
import pytest
from unittest.mock import patch, MagicMock

from app.auth.providers.saml import SAMLAuthProvider, prepare_saml_request, SAML_AVAILABLE

# Skip tests that require mocking onelogin if it's not installed
requires_saml_library = pytest.mark.skipif(
    not SAML_AVAILABLE,
    reason="python3-saml not installed"
)


class TestSAMLProviderInitialization:
    """Test SAML provider initialization and configuration."""

    def test_provider_name(self):
        """Test provider name is 'saml'."""
        provider = SAMLAuthProvider()
        assert provider.provider_name == "saml"

    def test_protocol_is_saml(self):
        """Test protocol is 'saml'."""
        provider = SAMLAuthProvider()
        assert provider.protocol == "saml"

    def test_disabled_when_not_configured(self):
        """Test provider is disabled when SAML is not configured."""
        with patch('app.auth.providers.saml.settings') as mock_settings:
            mock_settings.SAML_ENABLED = False
            provider = SAMLAuthProvider()
            assert provider.is_enabled() is False

    def test_disabled_when_settings_missing(self):
        """Test provider is disabled when settings can't be loaded."""
        with patch('app.auth.providers.saml.settings') as mock_settings:
            mock_settings.SAML_ENABLED = True
            mock_settings.SAML_CONFIG_PATH = "/nonexistent/path"
            mock_settings.SAML_IDP_ENTITY_ID = None
            provider = SAMLAuthProvider(config_path="/nonexistent/path")
            assert provider.is_enabled() is False


class TestSAMLAttributeMapping:
    """Test SAML attribute mapping from different IdPs."""

    @pytest.fixture
    def provider(self):
        """Create SAML provider for testing."""
        return SAMLAuthProvider()

    def test_map_okta_attributes(self, provider):
        """Test mapping Okta-style attributes."""
        raw_attrs = {
            "email": ["user@example.com"],
            "firstName": ["John"],
            "lastName": ["Doe"],
            "displayName": ["John Doe"],
            "groups": ["Admins", "Developers"]
        }

        result = provider._map_attributes(raw_attrs)

        assert result["email"] == "user@example.com"
        assert result["first_name"] == "John"
        assert result["last_name"] == "Doe"
        assert result["name"] == "John Doe"
        assert result["groups"] == ["Admins", "Developers"]

    def test_map_azure_ad_attributes(self, provider):
        """Test mapping Azure AD SAML claims."""
        raw_attrs = {
            "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress": ["user@example.com"],
            "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/givenname": ["Jane"],
            "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/surname": ["Smith"],
            "http://schemas.microsoft.com/ws/2008/06/identity/claims/groups": ["group-id-1", "group-id-2"]
        }

        result = provider._map_attributes(raw_attrs)

        assert result["email"] == "user@example.com"
        assert result["first_name"] == "Jane"
        assert result["last_name"] == "Smith"
        assert result["name"] == "Jane Smith"  # Constructed from first + last
        assert result["groups"] == ["group-id-1", "group-id-2"]

    def test_map_constructs_name_from_parts(self, provider):
        """Test name is constructed from first and last name."""
        raw_attrs = {
            "firstName": ["John"],
            "lastName": ["Doe"]
        }

        result = provider._map_attributes(raw_attrs)

        assert result["name"] == "John Doe"

    def test_map_empty_attributes(self, provider):
        """Test mapping empty attributes."""
        result = provider._map_attributes({})
        assert result == {}

    def test_map_single_value_unwrapped(self, provider):
        """Test single values are unwrapped from lists."""
        raw_attrs = {
            "email": ["single@example.com"]
        }

        result = provider._map_attributes(raw_attrs)

        assert result["email"] == "single@example.com"
        assert not isinstance(result["email"], list)

    def test_map_groups_remain_as_list(self, provider):
        """Test groups remain as a list."""
        raw_attrs = {
            "groups": ["Admin", "User"]
        }

        result = provider._map_attributes(raw_attrs)

        assert isinstance(result["groups"], list)
        assert result["groups"] == ["Admin", "User"]


class TestSAMLAuthentication:
    """Test SAML authentication flow."""

    @pytest.fixture
    def provider_with_settings(self):
        """Create SAML provider with mock settings."""
        provider = SAMLAuthProvider()
        provider._settings = {
            "strict": True,
            "debug": False,
            "sp": {
                "entityId": "https://app.example.com/metadata",
                "assertionConsumerService": {
                    "url": "https://app.example.com/acs",
                    "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
                }
            },
            "idp": {
                "entityId": "https://idp.example.com",
                "singleSignOnService": {
                    "url": "https://idp.example.com/sso",
                    "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
                },
                "x509cert": "MIIC..."
            }
        }
        return provider

    @pytest.mark.asyncio
    @requires_saml_library
    async def test_authenticate_missing_saml_response(self):
        """Test authentication fails without SAML response."""
        provider = SAMLAuthProvider()
        provider._settings = {}

        result = await provider.authenticate({
            "saml_response": None,
            "request_data": {}
        })

        assert result.success is False
        assert "Missing SAML response" in result.error

    @pytest.mark.asyncio
    @requires_saml_library
    async def test_authenticate_not_configured(self):
        """Test authentication fails when not configured."""
        provider = SAMLAuthProvider()
        provider._settings = None

        result = await provider.authenticate({
            "saml_response": "base64data",
            "request_data": {}
        })

        assert result.success is False
        assert "not configured" in result.error.lower()

    @pytest.mark.asyncio
    @requires_saml_library
    async def test_authenticate_success(self, provider_with_settings):
        """Test successful SAML authentication."""
        from onelogin.saml2.auth import OneLogin_Saml2_Auth

        with patch.object(OneLogin_Saml2_Auth, '__init__', return_value=None), \
             patch.object(OneLogin_Saml2_Auth, 'process_response'), \
             patch.object(OneLogin_Saml2_Auth, 'get_errors', return_value=[]), \
             patch.object(OneLogin_Saml2_Auth, 'is_authenticated', return_value=True), \
             patch.object(OneLogin_Saml2_Auth, 'get_nameid', return_value="user@example.com"), \
             patch.object(OneLogin_Saml2_Auth, 'get_nameid_format', return_value="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"), \
             patch.object(OneLogin_Saml2_Auth, 'get_session_index', return_value="session123"), \
             patch.object(OneLogin_Saml2_Auth, 'get_session_expiration', return_value=None), \
             patch.object(OneLogin_Saml2_Auth, 'get_attributes', return_value={"email": ["user@example.com"], "displayName": ["Test User"]}):

            result = await provider_with_settings.authenticate({
                "saml_response": "base64encodedresponse",
                "request_data": {
                    "https": "on",
                    "http_host": "app.example.com",
                    "script_name": "/acs"
                }
            })

            assert result.success is True
            assert result.email == "user@example.com"
            assert result.provider == "saml"
            assert result.metadata["session_index"] == "session123"

    @pytest.mark.asyncio
    @requires_saml_library
    async def test_authenticate_saml_error(self, provider_with_settings):
        """Test authentication fails with SAML errors."""
        from onelogin.saml2.auth import OneLogin_Saml2_Auth

        with patch.object(OneLogin_Saml2_Auth, '__init__', return_value=None), \
             patch.object(OneLogin_Saml2_Auth, 'process_response'), \
             patch.object(OneLogin_Saml2_Auth, 'get_errors', return_value=["invalid_response"]), \
             patch.object(OneLogin_Saml2_Auth, 'get_last_error_reason', return_value="Signature validation failed"):

            result = await provider_with_settings.authenticate({
                "saml_response": "invalidresponse",
                "request_data": {}
            })

            assert result.success is False
            assert "SAML error" in result.error


class TestSAMLLoginURL:
    """Test SAML login URL generation."""

    @pytest.mark.asyncio
    async def test_login_url_not_configured(self):
        """Test login URL returns None when not configured."""
        provider = SAMLAuthProvider()
        provider._settings = None

        url = await provider.get_login_url("/dashboard", "state123")

        assert url is None

    @pytest.mark.asyncio
    @requires_saml_library
    async def test_login_url_generation(self):
        """Test login URL is generated correctly."""
        from onelogin.saml2.auth import OneLogin_Saml2_Auth

        provider = SAMLAuthProvider()
        provider._settings = {
            "sp": {"entityId": "https://app.example.com"},
            "idp": {"singleSignOnService": {"url": "https://idp.example.com/sso"}}
        }

        with patch.object(OneLogin_Saml2_Auth, '__init__', return_value=None), \
             patch.object(OneLogin_Saml2_Auth, 'login', return_value="https://idp.example.com/sso?SAMLRequest=..."), \
             patch('app.auth.providers.saml.settings') as mock_settings:
            mock_settings.APP_BASE_URL = "https://app.example.com"

            url = await provider.get_login_url("/dashboard", "csrf_token")

        assert url is not None
        assert "idp.example.com" in url


class TestSAMLLogout:
    """Test SAML Single Logout."""

    @pytest.mark.asyncio
    async def test_logout_not_configured(self):
        """Test logout returns None when not configured."""
        provider = SAMLAuthProvider()
        provider._settings = None

        url = await provider.initiate_logout("user@example.com", "session123")

        assert url is None

    @pytest.mark.asyncio
    @requires_saml_library
    async def test_logout_url_generation(self):
        """Test logout URL is generated correctly."""
        from onelogin.saml2.auth import OneLogin_Saml2_Auth

        provider = SAMLAuthProvider()
        provider._settings = {
            "sp": {"entityId": "https://app.example.com"},
            "idp": {"singleLogoutService": {"url": "https://idp.example.com/slo"}}
        }

        with patch.object(OneLogin_Saml2_Auth, '__init__', return_value=None), \
             patch.object(OneLogin_Saml2_Auth, 'logout', return_value="https://idp.example.com/slo?SAMLRequest=..."), \
             patch('app.auth.providers.saml.settings') as mock_settings:
            mock_settings.APP_BASE_URL = "https://app.example.com"

            url = await provider.initiate_logout(
                name_id="user@example.com",
                session_index="session123",
                return_to="/login"
            )

        assert url is not None


class TestSAMLMetadata:
    """Test SP metadata generation."""

    def test_metadata_not_configured(self):
        """Test metadata returns None when not configured."""
        provider = SAMLAuthProvider()
        provider._settings = None

        metadata = provider.get_sp_metadata()

        assert metadata is None

    @requires_saml_library
    def test_metadata_generation(self):
        """Test metadata is generated correctly."""
        from onelogin.saml2.settings import OneLogin_Saml2_Settings

        with patch.object(OneLogin_Saml2_Settings, '__init__', return_value=None), \
             patch.object(OneLogin_Saml2_Settings, 'get_sp_metadata', return_value="<?xml version='1.0'?><EntityDescriptor>...</EntityDescriptor>"), \
             patch.object(OneLogin_Saml2_Settings, 'validate_metadata', return_value=[]):

            provider = SAMLAuthProvider()
            provider._settings = {"sp": {}, "idp": {}}

            metadata = provider.get_sp_metadata()

        assert metadata is not None
        assert "<?xml" in metadata


class TestPrepareSAMLRequest:
    """Test prepare_saml_request helper function."""

    def test_prepare_request_https(self):
        """Test request preparation with HTTPS."""
        mock_request = MagicMock()
        mock_request.url.scheme = "https"
        mock_request.url.netloc = "app.example.com"
        mock_request.url.port = 443
        mock_request.url.path = "/api/v1/auth/saml/acs"
        mock_request.headers = {"host": "app.example.com"}
        mock_request.query_params = {}

        result = prepare_saml_request(mock_request)

        assert result["https"] == "on"
        assert result["http_host"] == "app.example.com"
        assert result["server_port"] == 443
        assert result["script_name"] == "/api/v1/auth/saml/acs"

    def test_prepare_request_http(self):
        """Test request preparation with HTTP."""
        mock_request = MagicMock()
        mock_request.url.scheme = "http"
        mock_request.url.netloc = "localhost:8000"
        mock_request.url.port = 8000
        mock_request.url.path = "/api/v1/auth/saml/acs"
        mock_request.headers = {"host": "localhost:8000"}
        mock_request.query_params = {"param": "value"}

        result = prepare_saml_request(mock_request)

        assert result["https"] == "off"
        assert result["http_host"] == "localhost:8000"
        assert result["server_port"] == 8000
        assert result["get_data"] == {"param": "value"}


class TestSAMLLibraryUnavailable:
    """Test behavior when python3-saml is not installed."""

    @pytest.mark.asyncio
    @patch('app.auth.providers.saml.SAML_AVAILABLE', False)
    async def test_authenticate_saml_unavailable(self):
        """Test authentication fails gracefully when SAML library unavailable."""
        provider = SAMLAuthProvider()

        result = await provider.authenticate({
            "saml_response": "data",
            "request_data": {}
        })

        assert result.success is False
        assert "not available" in result.error.lower() or "not installed" in result.error.lower()

    @patch('app.auth.providers.saml.SAML_AVAILABLE', False)
    def test_is_enabled_saml_unavailable(self):
        """Test provider is disabled when SAML library unavailable."""
        provider = SAMLAuthProvider()

        assert provider.is_enabled() is False
