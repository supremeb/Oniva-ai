"""
Tests for OpenAPI Connector Factory

Tests the parser, tool generator, HTTP client, and connector classes.
"""

import pytest
from unittest.mock import patch

from app.connectors.openapi.parser import OpenAPIParser, ParsedSpec
from app.connectors.openapi.tool_generator import ToolGenerator
from app.connectors.openapi.http_client import (
    SecurityError,
    is_ip_blocked,
    validate_url_safe,
)
from app.connectors.openapi.auth import (
    AuthConfig,
    BearerAuth,
    APIKeyAuth,
    BasicAuth,
    generate_pkce_pair,
    detect_auth_type,
)
from app.connectors.openapi.connector import OpenAPIConnector
from app.connectors.openapi.registry import (
    get_registry_connector,
    list_registry_connectors,
    search_registry,
    get_categories,
)


# Sample OpenAPI spec for testing
SAMPLE_SPEC = {
    "openapi": "3.0.3",
    "info": {
        "title": "Test API",
        "version": "1.0.0",
        "description": "A test API for unit tests",
    },
    "servers": [
        {"url": "https://api.test.com/v1"}
    ],
    "paths": {
        "/users": {
            "get": {
                "operationId": "listUsers",
                "summary": "List all users",
                "parameters": [
                    {
                        "name": "limit",
                        "in": "query",
                        "schema": {"type": "integer"},
                        "description": "Max results",
                    }
                ],
                "responses": {"200": {"description": "Success"}},
            },
            "post": {
                "operationId": "createUser",
                "summary": "Create a user",
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "email": {"type": "string"},
                                },
                                "required": ["name", "email"],
                            }
                        }
                    }
                },
                "responses": {"201": {"description": "Created"}},
            },
        },
        "/users/{id}": {
            "get": {
                "operationId": "getUser",
                "summary": "Get user by ID",
                "parameters": [
                    {
                        "name": "id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "responses": {"200": {"description": "Success"}},
            },
        },
    },
    "components": {
        "securitySchemes": {
            "bearerAuth": {
                "type": "http",
                "scheme": "bearer",
            }
        }
    },
}


# ========== Parser Tests ==========

class TestOpenAPIParser:
    """Tests for OpenAPI spec parsing."""

    def test_parse_valid_spec(self):
        """Should parse valid OpenAPI 3.x spec."""
        parser = OpenAPIParser(SAMPLE_SPEC)
        result = parser.parse()

        assert isinstance(result, ParsedSpec)
        assert result.openapi_version == "3.0.3"
        assert result.info.title == "Test API"
        assert result.info.version == "1.0.0"
        assert len(result.servers) == 1
        assert result.servers[0].url == "https://api.test.com/v1"

    def test_parse_operations(self):
        """Should extract all operations from paths."""
        parser = OpenAPIParser(SAMPLE_SPEC)
        result = parser.parse()

        assert len(result.operations) == 3
        op_ids = [op.operation_id for op in result.operations]
        assert "listUsers" in op_ids
        assert "createUser" in op_ids
        assert "getUser" in op_ids

    def test_parse_parameters(self):
        """Should extract parameters correctly."""
        parser = OpenAPIParser(SAMPLE_SPEC)
        result = parser.parse()

        get_user = next(op for op in result.operations if op.operation_id == "getUser")
        assert len(get_user.parameters) == 1
        assert get_user.parameters[0]["name"] == "id"
        assert get_user.parameters[0]["in"] == "path"

    def test_parse_security_schemes(self):
        """Should extract security schemes."""
        parser = OpenAPIParser(SAMPLE_SPEC)
        result = parser.parse()

        assert "bearerAuth" in result.security_schemes
        assert result.security_schemes["bearerAuth"]["type"] == "http"

    def test_reject_swagger_2(self):
        """Should reject Swagger 2.0 specs."""
        swagger_spec = {"swagger": "2.0", "info": {"title": "Test"}}

        with pytest.raises(ValueError, match="Unsupported OpenAPI version"):
            OpenAPIParser(swagger_spec)

    def test_parse_yaml_string(self):
        """Should parse YAML string input."""
        yaml_content = """
openapi: "3.0.3"
info:
  title: "YAML Test"
  version: "1.0.0"
paths: {}
"""
        parser = OpenAPIParser(yaml_content)
        result = parser.parse()
        assert result.info.title == "YAML Test"

    def test_generate_operation_id(self):
        """Should generate operation ID when missing."""
        spec_without_id = {
            "openapi": "3.0.3",
            "info": {"title": "Test", "version": "1.0"},
            "paths": {
                "/items": {
                    "get": {
                        "summary": "Get items",
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        }
        parser = OpenAPIParser(spec_without_id)
        result = parser.parse()

        assert len(result.operations) == 1
        assert result.operations[0].operation_id == "get_items"


# ========== Tool Generator Tests ==========

class TestToolGenerator:
    """Tests for tool generation from OpenAPI operations."""

    def test_generate_all_tools(self):
        """Should generate tools for all operations."""
        parser = OpenAPIParser(SAMPLE_SPEC)
        parsed = parser.parse()
        generator = ToolGenerator(parsed)

        tools = generator.generate_all()

        assert len(tools) == 3
        tool_names = [t["name"] for t in tools]
        assert "listusers" in tool_names
        assert "createuser" in tool_names
        assert "getuser" in tool_names

    def test_tool_has_required_fields(self):
        """Each tool should have name, description, input_schema, _meta."""
        parser = OpenAPIParser(SAMPLE_SPEC)
        parsed = parser.parse()
        generator = ToolGenerator(parsed)

        tools = generator.generate_all()

        for tool in tools:
            assert "name" in tool
            assert "description" in tool
            assert "input_schema" in tool
            assert "_meta" in tool
            assert "path" in tool["_meta"]
            assert "method" in tool["_meta"]

    def test_input_schema_from_parameters(self):
        """Should build input schema from parameters."""
        parser = OpenAPIParser(SAMPLE_SPEC)
        parsed = parser.parse()
        generator = ToolGenerator(parsed)

        tools = generator.generate_all()
        list_users = next(t for t in tools if t["name"] == "listusers")

        schema = list_users["input_schema"]
        assert schema["type"] == "object"
        assert "limit" in schema["properties"]
        assert schema["properties"]["limit"]["type"] == "integer"

    def test_input_schema_from_request_body(self):
        """Should include request body properties in schema."""
        parser = OpenAPIParser(SAMPLE_SPEC)
        parsed = parser.parse()
        generator = ToolGenerator(parsed)

        tools = generator.generate_all()
        create_user = next(t for t in tools if t["name"] == "createuser")

        schema = create_user["input_schema"]
        assert "name" in schema["properties"]
        assert "email" in schema["properties"]
        assert "name" in schema["required"]
        assert "email" in schema["required"]

    def test_generate_for_specific_operations(self):
        """Should generate tools for specific operations only."""
        parser = OpenAPIParser(SAMPLE_SPEC)
        parsed = parser.parse()
        generator = ToolGenerator(parsed)

        tools = generator.generate_for_operations(["listUsers"])

        assert len(tools) == 1
        assert tools[0]["name"] == "listusers"


# ========== HTTP Client Tests ==========

class TestHTTPClient:
    """Tests for safe HTTP client with SSRF protection."""

    def test_block_private_ips(self):
        """Should block private IP ranges."""
        assert is_ip_blocked("10.0.0.1") is True
        assert is_ip_blocked("172.16.0.1") is True
        assert is_ip_blocked("192.168.1.1") is True
        assert is_ip_blocked("127.0.0.1") is True
        assert is_ip_blocked("169.254.169.254") is True  # Cloud metadata

    def test_allow_public_ips(self):
        """Should allow public IPs."""
        assert is_ip_blocked("8.8.8.8") is False
        assert is_ip_blocked("1.1.1.1") is False
        assert is_ip_blocked("52.84.127.123") is False

    @pytest.mark.asyncio
    async def test_validate_url_rejects_http(self):
        """Should reject HTTP URLs (except localhost)."""
        with pytest.raises(SecurityError, match="HTTP only allowed for localhost"):
            await validate_url_safe("http://api.example.com/spec.json")

    @pytest.mark.asyncio
    async def test_validate_url_allows_https(self):
        """Should allow HTTPS URLs."""
        with patch("app.connectors.openapi.http_client.resolve_hostname") as mock_resolve:
            mock_resolve.return_value = ["93.184.216.34"]
            result = await validate_url_safe("https://api.example.com/spec.json")
            assert result == "https://api.example.com/spec.json"

    @pytest.mark.asyncio
    async def test_validate_url_blocks_internal_dns(self):
        """Should block URLs that resolve to private IPs."""
        with patch("app.connectors.openapi.http_client.resolve_hostname") as mock_resolve:
            mock_resolve.return_value = ["10.0.0.1"]

            with pytest.raises(SecurityError, match="blocked IP"):
                await validate_url_safe("https://internal.company.com/api")


# ========== Auth Handler Tests ==========

class TestAuthHandlers:
    """Tests for authentication handlers."""

    def test_bearer_auth(self):
        """Should add Bearer token to Authorization header."""
        config = AuthConfig(auth_type="bearer")
        handler = BearerAuth(config, {"token": "test-token"})

        headers: dict = {}
        params: dict = {}
        handler.apply(headers, params)

        assert headers["Authorization"] == "Bearer test-token"

    def test_api_key_header(self):
        """Should add API key to configured header."""
        config = AuthConfig(auth_type="api_key", location="header", name="X-API-Key")
        handler = APIKeyAuth(config, {"api_key": "my-api-key"})

        headers: dict = {}
        params: dict = {}
        handler.apply(headers, params)

        assert headers["X-API-Key"] == "my-api-key"

    def test_api_key_query(self):
        """Should add API key to query params."""
        config = AuthConfig(auth_type="api_key", location="query", name="api_key")
        handler = APIKeyAuth(config, {"api_key": "my-api-key"})

        headers: dict = {}
        params: dict = {}
        handler.apply(headers, params)

        assert params["api_key"] == "my-api-key"

    def test_basic_auth(self):
        """Should add Basic auth header."""
        config = AuthConfig(auth_type="basic")
        handler = BasicAuth(config, {"username": "user", "password": "pass"})

        headers: dict = {}
        params: dict = {}
        handler.apply(headers, params)

        # Base64 of "user:pass" = "dXNlcjpwYXNz"
        assert headers["Authorization"] == "Basic dXNlcjpwYXNz"

    def test_pkce_generation(self):
        """Should generate valid PKCE pair."""
        verifier, challenge = generate_pkce_pair()

        assert len(verifier) > 40
        assert len(challenge) > 40
        assert verifier != challenge

    def test_detect_auth_type_bearer(self):
        """Should detect bearer auth from security schemes."""
        schemes = {
            "bearerAuth": {"type": "http", "scheme": "bearer"}
        }
        config = detect_auth_type(schemes)
        assert config.auth_type == "bearer"

    def test_detect_auth_type_api_key(self):
        """Should detect API key auth from security schemes."""
        schemes = {
            "apiKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"}
        }
        config = detect_auth_type(schemes)
        assert config.auth_type == "api_key"
        assert config.location == "header"
        assert config.name == "X-API-Key"


# ========== Registry Tests ==========

class TestConnectorRegistry:
    """Tests for curated connector registry."""

    def test_get_existing_connector(self):
        """Should return connector entry for known ID."""
        entry = get_registry_connector("stripe")
        assert entry is not None
        assert entry["name"] == "Stripe"
        assert "spec_url" in entry

    def test_get_nonexistent_connector(self):
        """Should return None for unknown ID."""
        entry = get_registry_connector("nonexistent")
        assert entry is None

    def test_list_all_connectors(self):
        """Should list all connectors."""
        connectors = list_registry_connectors()
        assert len(connectors) >= 30
        assert all("id" in c for c in connectors)

    def test_list_by_category(self):
        """Should filter by category."""
        payments = list_registry_connectors(category="payments")
        assert len(payments) >= 1
        assert all(c.get("category") == "payments" for c in payments)

    def test_search_connectors(self):
        """Should search by name and description."""
        results = search_registry("payment")
        assert len(results) >= 1

    def test_get_categories(self):
        """Should return all categories with counts."""
        categories = get_categories()
        assert len(categories) >= 5
        assert all("id" in c and "connector_count" in c for c in categories)


# ========== Connector Tests ==========

class TestOpenAPIConnector:
    """Tests for OpenAPIConnector class."""

    def test_create_from_spec(self):
        """Should create connector from spec dict."""
        connector = OpenAPIConnector(SAMPLE_SPEC)

        assert connector.metadata.name == "Test API"
        assert connector.metadata.version == "1.0.0"
        assert connector.operation_count == 3

    def test_get_tools(self):
        """Should return generated tools."""
        connector = OpenAPIConnector(SAMPLE_SPEC)
        tools = connector.get_tools()

        assert len(tools) == 3
        assert all("name" in t for t in tools)

    def test_tools_cached(self):
        """Tools should be generated once and cached."""
        connector = OpenAPIConnector(SAMPLE_SPEC)

        tools1 = connector.get_tools()
        tools2 = connector.get_tools()

        assert tools1 is tools2  # Same object

    @pytest.mark.asyncio
    async def test_authenticate_success(self):
        """Should configure auth handler on authenticate."""
        connector = OpenAPIConnector(SAMPLE_SPEC)
        result = await connector.authenticate({"token": "test-token"})

        assert result is True
        assert connector._is_authenticated is True
        assert connector._auth_handler is not None

    @pytest.mark.asyncio
    async def test_authenticate_missing_credentials(self):
        """Should fail with missing credentials."""
        connector = OpenAPIConnector(SAMPLE_SPEC)
        result = await connector.authenticate({})

        assert result is False

    @pytest.mark.asyncio
    async def test_disconnect(self):
        """Should cleanup on disconnect."""
        connector = OpenAPIConnector(SAMPLE_SPEC)
        await connector.authenticate({"token": "test"})
        await connector.disconnect()

        assert connector._is_authenticated is False
        assert connector._http_client is None

    def test_spec_info(self):
        """Should return spec info for display."""
        connector = OpenAPIConnector(SAMPLE_SPEC)
        info = connector.spec_info

        assert info["title"] == "Test API"
        assert info["version"] == "1.0.0"
        assert info["operation_count"] == 3
        assert "https://api.test.com/v1" in info["servers"]

    @pytest.mark.asyncio
    async def test_call_tool_requires_auth(self):
        """Should raise if calling tool without auth."""
        connector = OpenAPIConnector(SAMPLE_SPEC)

        with pytest.raises(RuntimeError, match="not authenticated"):
            await connector.call_tool("listusers", {})

    @pytest.mark.asyncio
    async def test_call_tool_unknown_tool(self):
        """Should raise for unknown tool name."""
        connector = OpenAPIConnector(SAMPLE_SPEC)
        await connector.authenticate({"token": "test"})

        with pytest.raises(ValueError, match="Tool not found"):
            await connector.call_tool("nonexistent", {})
