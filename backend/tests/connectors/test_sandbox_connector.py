"""Tests for SandboxConnector (Code Mode pattern)."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.connectors.base import ConnectorMetadata
from app.connectors.openapi.parser import (
    ParsedSpec,
    OpenAPIInfo,
    OpenAPIServer,
    OpenAPIOperation,
)
from app.connectors.openapi.sandbox_connector import SandboxConnector


# ── Fixtures ─────────────────────────────────────────────────


def _make_metadata(connector_id: str = "stripe") -> ConnectorMetadata:
    return ConnectorMetadata(
        id=connector_id,
        name="Stripe",
        description="Stripe payment API",
        icon="💳",
        category="payments",
        auth_type="bearer",
        version="1.0.0",
    )


def _make_spec(
    operations: list[OpenAPIOperation] | None = None,
    base_url: str = "https://api.stripe.com",
) -> ParsedSpec:
    if operations is None:
        operations = [
            OpenAPIOperation(
                operation_id="list_customers",
                path="/v1/customers",
                method="GET",
                summary="List all customers",
                description="Returns a list of your customers sorted by creation date",
                parameters=[
                    {
                        "name": "email",
                        "in": "query",
                        "schema": {"type": "string"},
                        "required": False,
                        "description": "Filter by email",
                    },
                    {
                        "name": "limit",
                        "in": "query",
                        "schema": {"type": "integer"},
                        "required": False,
                        "description": "Max results",
                    },
                ],
                tags=["customers"],
            ),
            OpenAPIOperation(
                operation_id="create_customer",
                path="/v1/customers",
                method="POST",
                summary="Create a customer",
                description="Creates a new customer object",
                parameters=[],
                tags=["customers"],
            ),
            OpenAPIOperation(
                operation_id="get_customer",
                path="/v1/customers/{id}",
                method="GET",
                summary="Retrieve a customer",
                description="Retrieves the details of an existing customer",
                parameters=[
                    {
                        "name": "id",
                        "in": "path",
                        "schema": {"type": "string"},
                        "required": True,
                    }
                ],
                tags=["customers"],
            ),
            OpenAPIOperation(
                operation_id="list_charges",
                path="/v1/charges",
                method="GET",
                summary="List all charges",
                description="Returns a list of charges",
                parameters=[
                    {
                        "name": "limit",
                        "in": "query",
                        "schema": {"type": "integer"},
                        "required": False,
                    }
                ],
                tags=["charges"],
            ),
            OpenAPIOperation(
                operation_id="create_payment_intent",
                path="/v1/payment_intents",
                method="POST",
                summary="Create a PaymentIntent",
                description="Creates a PaymentIntent object",
                parameters=[],
                tags=["payment_intents"],
            ),
        ]
    raw = {
        "openapi": "3.0.3",
        "info": {"title": "Stripe API", "version": "2023-10-16"},
        "servers": [{"url": base_url}],
        "paths": {
            "/v1/customers": {
                "get": {
                    "operationId": "list_customers",
                    "summary": "List all customers",
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }
    return ParsedSpec(
        openapi_version="3.0.3",
        info=OpenAPIInfo(title="Stripe API", version="2023-10-16"),
        servers=[OpenAPIServer(url=base_url)],
        operations=operations,
        security_schemes={"bearerAuth": {"type": "http", "scheme": "bearer"}},
        raw_spec=raw,
    )


@pytest.fixture
def sample_metadata():
    return _make_metadata()


@pytest.fixture
def sample_spec():
    return _make_spec()


@pytest.fixture
def connector(sample_spec, sample_metadata):
    return SandboxConnector(sample_spec, sample_metadata)


# ── get_tools() tests ────────────────────────────────────────


class TestGetTools:
    """Test that SandboxConnector returns exactly 2 well-formed tools."""

    def test_returns_exactly_two_tools(self, connector):
        tools = connector.get_tools()
        assert len(tools) == 2

    def test_tool_names_include_connector_id(self, connector, sample_metadata):
        names = [t["name"] for t in connector.get_tools()]
        assert names[0] == f"search_{sample_metadata.id}_api"
        assert names[1] == f"execute_{sample_metadata.id}_api"

    def test_each_tool_has_input_schema(self, connector):
        for tool in connector.get_tools():
            assert "input_schema" in tool
            assert tool["input_schema"]["type"] == "object"
            assert "properties" in tool["input_schema"]
            assert "required" in tool["input_schema"]

    def test_search_tool_takes_query(self, connector):
        search_tool = connector.get_tools()[0]
        assert "query" in search_tool["input_schema"]["properties"]
        assert "query" in search_tool["input_schema"]["required"]

    def test_execute_tool_takes_code(self, connector):
        execute_tool = connector.get_tools()[1]
        assert "code" in execute_tool["input_schema"]["properties"]
        assert "code" in execute_tool["input_schema"]["required"]

    def test_execute_description_includes_base_url(self, connector):
        desc = connector.get_tools()[1]["description"]
        assert "https://api.stripe.com" in desc

    def test_execute_description_includes_env_vars(self, connector):
        desc = connector.get_tools()[1]["description"]
        assert "CONNECTOR_API_KEY" in desc
        assert "CONNECTOR_BASE_URL" in desc
        assert "CONNECTOR_NAME" in desc

    def test_execute_description_includes_auth_example(self, connector):
        desc = connector.get_tools()[1]["description"]
        assert "Bearer" in desc

    def test_execute_description_includes_code_example(self, connector):
        desc = connector.get_tools()[1]["description"]
        assert "import httpx" in desc
        assert "print(" in desc

    def test_execute_description_includes_sample_path(self, connector):
        """Should include a real endpoint path from the spec."""
        desc = connector.get_tools()[1]["description"]
        assert "/v1/customers" in desc

    def test_search_description_includes_operation_count(self, connector):
        desc = connector.get_tools()[0]["description"]
        assert "5 operations" in desc  # 5 ops in sample spec

    def test_search_description_includes_tags(self, connector):
        desc = connector.get_tools()[0]["description"]
        assert "customers" in desc.lower()

    def test_basic_auth_example(self, sample_spec, sample_metadata):
        """Basic auth should use auth= instead of Bearer header."""
        connector = SandboxConnector(
            sample_spec, sample_metadata, auth_config={"type": "basic"}
        )
        desc = connector.get_tools()[1]["description"]
        assert "CONNECTOR_USERNAME" in desc
        assert "CONNECTOR_PASSWORD" in desc


# ── _search_operations() tests ───────────────────────────────


class TestSearchOperations:
    """Test keyword + fuzzy operation search."""

    def test_search_returns_relevant_results(self, connector):
        results = connector._search_operations("list customers")
        assert len(results) > 0
        assert all("operation_id" in r and "path" in r for r in results)

    def test_search_respects_max_results(self, connector):
        results = connector._search_operations("a")
        assert len(results) <= 8

    def test_search_empty_query_returns_top_operations(self, connector):
        """Empty query returns top-N for discoverability, not empty list."""
        results = connector._search_operations("")
        assert len(results) > 0

    def test_search_no_match_returns_empty(self, connector):
        results = connector._search_operations("zzz_nonexistent_xyz_42")
        assert len(results) == 0

    def test_search_results_include_parameter_types(self, connector):
        results = connector._search_operations("list customers")
        assert len(results) > 0
        # list_customers has parameters
        customer_result = next(
            (r for r in results if r["operation_id"] == "list_customers"), None
        )
        assert customer_result is not None
        assert len(customer_result["parameters"]) > 0
        param = customer_result["parameters"][0]
        assert "type" in param
        assert "name" in param
        assert "in" in param

    def test_search_results_include_tags(self, connector):
        results = connector._search_operations("customers")
        assert len(results) > 0
        assert results[0].get("tags") is not None

    def test_search_by_path(self, connector):
        """Path segments should be searchable."""
        results = connector._search_operations("charges")
        assert any(r["operation_id"] == "list_charges" for r in results)

    def test_search_by_tag(self, connector):
        """Tags should boost relevance."""
        results = connector._search_operations("payment_intents")
        assert any(r["operation_id"] == "create_payment_intent" for r in results)

    def test_exact_phrase_match_ranked_higher(self, connector):
        """Exact phrase match in summary should rank higher."""
        results = connector._search_operations("List all customers")
        assert len(results) > 0
        assert results[0]["operation_id"] == "list_customers"

    def test_search_whitespace_only_returns_top_operations(self, connector):
        results = connector._search_operations("   ")
        assert len(results) > 0


# ── _build_env_vars() tests ──────────────────────────────────


class TestCredentialInjection:
    """Test that credentials are injected at sandbox creation, not via shell."""

    def test_bearer_token(self, connector):
        env_vars = connector._build_env_vars({"token": "sk_live_secret"})
        assert env_vars["CONNECTOR_API_KEY"] == "sk_live_secret"
        assert env_vars["CONNECTOR_BASE_URL"] == "https://api.stripe.com"
        assert env_vars["CONNECTOR_NAME"] == "stripe"

    def test_api_key_credential(self, connector):
        env_vars = connector._build_env_vars({"api_key": "key_123"})
        assert env_vars["CONNECTOR_API_KEY"] == "key_123"

    def test_access_token_credential(self, connector):
        env_vars = connector._build_env_vars({"access_token": "oauth_token"})
        assert env_vars["CONNECTOR_API_KEY"] == "oauth_token"

    def test_basic_auth_credentials(self, connector):
        env_vars = connector._build_env_vars(
            {"username": "admin", "password": "s3cret"}
        )
        assert env_vars["CONNECTOR_USERNAME"] == "admin"
        assert env_vars["CONNECTOR_PASSWORD"] == "s3cret"

    def test_no_shell_injection_possible(self, connector):
        """Token with shell metacharacters doesn't break — no shell parsing."""
        evil_token = "sk_live_'; rm -rf / #"
        env_vars = connector._build_env_vars({"token": evil_token})
        assert env_vars["CONNECTOR_API_KEY"] == evil_token

    def test_empty_credentials(self, connector):
        env_vars = connector._build_env_vars({})
        assert "CONNECTOR_API_KEY" not in env_vars
        assert "CONNECTOR_BASE_URL" in env_vars

    def test_token_priority(self, connector):
        """token > api_key > access_token in priority."""
        env_vars = connector._build_env_vars(
            {"token": "tok", "api_key": "key", "access_token": "oauth"}
        )
        assert env_vars["CONNECTOR_API_KEY"] == "tok"

    def test_no_backend_keys_injected(self, connector):
        """Must never inject backend infrastructure keys."""
        env_vars = connector._build_env_vars({"token": "x"})
        for key in env_vars:
            assert key.startswith("CONNECTOR_")


# ── Session scoping tests ────────────────────────────────────


class TestSessionScoping:
    """Test that sandbox sessions are scoped per-tenant per-user."""

    @pytest.mark.asyncio
    async def test_session_id_includes_tenant_and_user(self, connector):
        """Different tenants get different sandboxes."""
        mock_handle = AsyncMock()
        mock_handle.execute_code.return_value = MagicMock(
            success=True, stdout="ok"
        )
        mock_executor = MagicMock()
        mock_executor.get_or_create = AsyncMock(return_value=mock_handle)

        with patch(
            "app.services.sandbox_executor.get_sandbox_executor",
            return_value=mock_executor,
        ), patch(
            "app.services.sandbox_executor.resolve_e2b_key",
            new_callable=AsyncMock,
            return_value="e2b-key",
        ):
            await connector._execute_in_sandbox(
                code="print(1)",
                tenant_id="tenant-A",
                user_id="user-1",
                user_session_id="session-1",
                credentials={"token": "sk_A"},
            )
            call_args = mock_executor.get_or_create.call_args
            session_id = call_args.kwargs.get("session_id", "")
            assert "tenant-A" in session_id
            assert "session-1" in session_id
            assert "stripe" in session_id

    @pytest.mark.asyncio
    async def test_different_users_get_different_sessions(self, connector):
        """Two different user sessions must produce different session keys."""
        mock_handle = AsyncMock()
        mock_handle.execute_code.return_value = MagicMock(
            success=True, stdout="ok"
        )
        mock_executor = MagicMock()
        mock_executor.get_or_create = AsyncMock(return_value=mock_handle)

        with patch(
            "app.services.sandbox_executor.get_sandbox_executor",
            return_value=mock_executor,
        ), patch(
            "app.services.sandbox_executor.resolve_e2b_key",
            new_callable=AsyncMock,
            return_value="e2b-key",
        ):
            await connector._execute_in_sandbox(
                code="print(1)",
                tenant_id="tenant-A",
                user_id="user-1",
                user_session_id="session-1",
                credentials={"token": "sk_A"},
            )
            call1 = mock_executor.get_or_create.call_args.kwargs["session_id"]

            await connector._execute_in_sandbox(
                code="print(2)",
                tenant_id="tenant-A",
                user_id="user-2",
                user_session_id="session-2",
                credentials={"token": "sk_B"},
            )
            call2 = mock_executor.get_or_create.call_args.kwargs["session_id"]

            assert call1 != call2

    @pytest.mark.asyncio
    async def test_env_vars_passed_to_sandbox(self, connector):
        """Credentials should be passed as env_vars, not via shell."""
        mock_handle = AsyncMock()
        mock_handle.execute_code.return_value = MagicMock(
            success=True, stdout="ok"
        )
        mock_executor = MagicMock()
        mock_executor.get_or_create = AsyncMock(return_value=mock_handle)

        with patch(
            "app.services.sandbox_executor.get_sandbox_executor",
            return_value=mock_executor,
        ), patch(
            "app.services.sandbox_executor.resolve_e2b_key",
            new_callable=AsyncMock,
            return_value="e2b-key",
        ):
            await connector._execute_in_sandbox(
                code="print(1)",
                tenant_id="t",
                user_id="u",
                user_session_id="s",
                credentials={"token": "sk_live_123"},
            )
            call_kwargs = mock_executor.get_or_create.call_args.kwargs
            assert "env_vars" in call_kwargs
            assert call_kwargs["env_vars"]["CONNECTOR_API_KEY"] == "sk_live_123"


# ── Structured error tests ───────────────────────────────────


class TestStructuredErrors:
    """Test that errors are categorized for the agent."""

    def test_auth_401_error(self, connector):
        result = MagicMock(stderr="HTTP 401 Unauthorized", error="")
        msg = connector._format_error(result)
        assert msg.startswith("AUTH_ERROR:")

    def test_auth_403_error(self, connector):
        result = MagicMock(stderr="HTTP 403 Forbidden", error="")
        msg = connector._format_error(result)
        assert msg.startswith("AUTH_ERROR:")

    def test_syntax_error(self, connector):
        result = MagicMock(stderr="SyntaxError: invalid syntax", error="")
        msg = connector._format_error(result)
        assert msg.startswith("CODE_ERROR:")

    def test_indentation_error(self, connector):
        result = MagicMock(stderr="IndentationError: unexpected indent", error="")
        msg = connector._format_error(result)
        assert msg.startswith("CODE_ERROR:")

    def test_timeout_error(self, connector):
        result = MagicMock(stderr="", error="TimeoutError: request timed out")
        msg = connector._format_error(result)
        assert msg.startswith("TIMEOUT_ERROR:")

    def test_import_error(self, connector):
        result = MagicMock(
            stderr="ModuleNotFoundError: No module named 'pandas'", error=""
        )
        msg = connector._format_error(result)
        assert msg.startswith("IMPORT_ERROR:")

    def test_generic_error(self, connector):
        result = MagicMock(stderr="KeyError: 'data'", error="")
        msg = connector._format_error(result)
        assert msg.startswith("EXECUTION_ERROR:")


# ── call_tool() routing tests ────────────────────────────────


class TestCallTool:
    """Test that call_tool routes correctly."""

    @pytest.mark.asyncio
    async def test_search_tool_routing(self, connector, sample_metadata):
        """Search tool should route to _search_operations."""
        result = await connector.call_tool(
            f"search_{sample_metadata.id}_api",
            {"query": "list customers"},
        )
        assert isinstance(result, list)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_unknown_tool_raises(self, connector):
        with pytest.raises(ValueError, match="Unknown tool"):
            await connector.call_tool("nonexistent_tool", {})


# ── BaseConnector interface tests ────────────────────────────


class TestBaseConnectorInterface:
    """Test that SandboxConnector fulfills BaseConnector interface."""

    @pytest.mark.asyncio
    async def test_authenticate_always_true(self, connector):
        result = await connector.authenticate({"token": "test"})
        assert result is True

    @pytest.mark.asyncio
    async def test_disconnect_always_true(self, connector):
        result = await connector.disconnect()
        assert result is True

    @pytest.mark.asyncio
    async def test_health_check_returns_healthy(self, connector):
        result = await connector.health_check()
        assert result["status"] == "healthy"


# ── Factory tests ────────────────────────────────────────────


class TestFactory:
    """Test create_openapi_connector factory function."""

    @pytest.mark.asyncio
    async def test_sandbox_mode_returns_sandbox_connector(
        self, sample_spec, sample_metadata
    ):
        from app.connectors.registry import create_openapi_connector

        with patch("app.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                USE_SANDBOX_CONNECTORS=True, ENABLE_SANDBOX=True
            )
            connector = await create_openapi_connector(sample_spec, sample_metadata)
            assert isinstance(connector, SandboxConnector)

    @pytest.mark.asyncio
    async def test_non_sandbox_mode_returns_openapi_connector(
        self, sample_spec, sample_metadata
    ):
        from app.connectors.registry import create_openapi_connector
        from app.connectors.openapi.connector import OpenAPIConnector

        with patch("app.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                USE_SANDBOX_CONNECTORS=False, ENABLE_SANDBOX=False
            )
            connector = await create_openapi_connector(sample_spec, sample_metadata)
            assert isinstance(connector, OpenAPIConnector)


# ── from_db_connector tests ──────────────────────────────────


class TestFromDbConnector:
    """Test SandboxConnector.from_db_connector() reconstruction from DB rows."""

    def _make_db_row(self):
        """Build a mock DB Connector row with stored tool dicts."""
        row = MagicMock()
        row.provider = "stripe"
        row.name = "Stripe"
        row.description = "Stripe payment API"
        row.openapi_spec_version = "3.0.3"
        row.openapi_operations = [
            {
                "name": "list_customers",
                "description": "List all customers sorted by creation date",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "email": {"type": "string", "description": "Filter by email"},
                        "limit": {"type": "integer", "description": "Max results"},
                    },
                    "required": [],
                },
                "_meta": {
                    "path": "/v1/customers",
                    "method": "GET",
                    "base_url": "https://api.stripe.com",
                    "operation_id": "list_customers",
                    "tags": ["customers"],
                },
            },
            {
                "name": "create_charge",
                "description": "Create a new charge",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "amount": {"type": "integer", "description": "Amount in cents"},
                        "currency": {"type": "string", "description": "3-letter currency code"},
                    },
                    "required": ["amount", "currency"],
                },
                "_meta": {
                    "path": "/v1/charges",
                    "method": "POST",
                    "base_url": "https://api.stripe.com",
                    "operation_id": "create_charge",
                    "tags": ["charges"],
                },
            },
        ]
        row.config = {
            "auth_type": "bearer",
            "category": "payments",
            "icon": "💳",
        }
        return row

    def test_reconstructs_operations(self):
        row = self._make_db_row()
        connector = SandboxConnector.from_db_connector(row, row.config)

        assert len(connector._parsed_spec.operations) == 2
        op_ids = [op.operation_id for op in connector._parsed_spec.operations]
        assert "list_customers" in op_ids
        assert "create_charge" in op_ids

    def test_reconstructs_base_url(self):
        row = self._make_db_row()
        connector = SandboxConnector.from_db_connector(row, row.config)

        assert connector._base_url == "https://api.stripe.com"

    def test_reconstructs_metadata(self):
        row = self._make_db_row()
        connector = SandboxConnector.from_db_connector(row, row.config)

        assert connector.metadata.id == "stripe"
        assert connector.metadata.name == "Stripe"
        assert connector.metadata.auth_type == "bearer"

    def test_reconstructs_tags(self):
        row = self._make_db_row()
        connector = SandboxConnector.from_db_connector(row, row.config)

        assert "customers" in connector._tags
        assert "charges" in connector._tags

    def test_reconstructs_parameters(self):
        row = self._make_db_row()
        connector = SandboxConnector.from_db_connector(row, row.config)

        # Find list_customers operation
        list_op = next(
            op for op in connector._parsed_spec.operations
            if op.operation_id == "list_customers"
        )
        param_names = [p["name"] for p in list_op.parameters]
        assert "email" in param_names
        assert "limit" in param_names

    def test_required_params_reconstructed(self):
        row = self._make_db_row()
        connector = SandboxConnector.from_db_connector(row, row.config)

        # Find create_charge operation
        charge_op = next(
            op for op in connector._parsed_spec.operations
            if op.operation_id == "create_charge"
        )
        required_params = [p["name"] for p in charge_op.parameters if p.get("required")]
        assert "amount" in required_params
        assert "currency" in required_params

    def test_get_tools_works_on_reconstructed(self):
        row = self._make_db_row()
        connector = SandboxConnector.from_db_connector(row, row.config)

        tools = connector.get_tools()
        assert len(tools) == 2
        assert tools[0]["name"] == "search_stripe_api"
        assert tools[1]["name"] == "execute_stripe_api"

    def test_search_works_on_reconstructed(self):
        row = self._make_db_row()
        connector = SandboxConnector.from_db_connector(row, row.config)

        results = connector._search_operations("customers")
        assert len(results) > 0
        assert results[0]["operation_id"] == "list_customers"

    def test_empty_operations(self):
        row = self._make_db_row()
        row.openapi_operations = []
        connector = SandboxConnector.from_db_connector(row, row.config)

        assert len(connector._parsed_spec.operations) == 0
        tools = connector.get_tools()
        assert len(tools) == 2  # Still returns search + execute

    def test_missing_meta_uses_fallback(self):
        row = self._make_db_row()
        row.openapi_operations = [
            {
                "name": "some_tool",
                "description": "A tool",
                "input_schema": {"type": "object", "properties": {}},
                # No _meta field
            }
        ]
        connector = SandboxConnector.from_db_connector(row, row.config)

        op = connector._parsed_spec.operations[0]
        assert op.operation_id == "some_tool"
        assert op.method == "GET"  # default fallback
