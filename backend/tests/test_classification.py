"""
Unit tests for Phase 1: Security Foundation - Data Classification.

Tests cover:
- DataClassification enum comparison methods
- ExportControlService IP-based validation
- InternalEndpointWhitelist model
- Classification propagation logic
"""
import pytest
from uuid import uuid4

from app.schemas.classification import DataClassification


class TestDataClassification:
    """Tests for DataClassification enum."""

    def test_classification_values(self):
        """Test that classification levels have correct string values."""
        assert DataClassification.PUBLIC.value == "public"
        assert DataClassification.INTERNAL.value == "internal"
        assert DataClassification.CONFIDENTIAL.value == "confidential"

    def test_classification_priority(self):
        """Test that priority values are in correct order."""
        assert DataClassification.PUBLIC.priority == 0
        assert DataClassification.INTERNAL.priority == 100
        assert DataClassification.CONFIDENTIAL.priority == 200

    def test_classification_comparison_greater_than(self):
        """Test greater than comparison."""
        assert DataClassification.CONFIDENTIAL > DataClassification.INTERNAL
        assert DataClassification.CONFIDENTIAL > DataClassification.PUBLIC
        assert DataClassification.INTERNAL > DataClassification.PUBLIC
        assert not DataClassification.PUBLIC > DataClassification.INTERNAL

    def test_classification_comparison_less_than(self):
        """Test less than comparison."""
        assert DataClassification.PUBLIC < DataClassification.INTERNAL
        assert DataClassification.PUBLIC < DataClassification.CONFIDENTIAL
        assert DataClassification.INTERNAL < DataClassification.CONFIDENTIAL
        assert not DataClassification.CONFIDENTIAL < DataClassification.INTERNAL

    def test_classification_comparison_greater_equal(self):
        """Test greater than or equal comparison."""
        assert DataClassification.CONFIDENTIAL >= DataClassification.CONFIDENTIAL
        assert DataClassification.CONFIDENTIAL >= DataClassification.INTERNAL
        assert DataClassification.INTERNAL >= DataClassification.INTERNAL
        assert DataClassification.INTERNAL >= DataClassification.PUBLIC

    def test_classification_comparison_less_equal(self):
        """Test less than or equal comparison."""
        assert DataClassification.PUBLIC <= DataClassification.PUBLIC
        assert DataClassification.PUBLIC <= DataClassification.INTERNAL
        assert DataClassification.INTERNAL <= DataClassification.INTERNAL
        assert DataClassification.INTERNAL <= DataClassification.CONFIDENTIAL

    def test_classification_max(self):
        """Test max() works for high-water mark propagation."""
        classifications = [
            DataClassification.PUBLIC,
            DataClassification.CONFIDENTIAL,
            DataClassification.INTERNAL,
        ]
        assert max(classifications) == DataClassification.CONFIDENTIAL

    def test_classification_max_empty_with_default(self):
        """Test max() with empty list and default."""
        classifications = []
        result = max(classifications, default=DataClassification.INTERNAL)
        assert result == DataClassification.INTERNAL

    def test_can_export_to_external_public(self):
        """PUBLIC data can be exported."""
        assert DataClassification.PUBLIC.can_export_to_external() is True

    def test_can_export_to_external_internal(self):
        """INTERNAL data can be exported (with audit)."""
        assert DataClassification.INTERNAL.can_export_to_external() is True

    def test_can_export_to_external_confidential(self):
        """CONFIDENTIAL data CANNOT be exported."""
        assert DataClassification.CONFIDENTIAL.can_export_to_external() is False

    def test_from_string_lowercase(self):
        """Test parsing from lowercase string."""
        assert DataClassification.from_string("public") == DataClassification.PUBLIC
        assert DataClassification.from_string("internal") == DataClassification.INTERNAL
        assert DataClassification.from_string("confidential") == DataClassification.CONFIDENTIAL

    def test_from_string_uppercase(self):
        """Test parsing from uppercase string (case-insensitive)."""
        assert DataClassification.from_string("PUBLIC") == DataClassification.PUBLIC
        assert DataClassification.from_string("INTERNAL") == DataClassification.INTERNAL
        assert DataClassification.from_string("CONFIDENTIAL") == DataClassification.CONFIDENTIAL

    def test_from_string_mixed_case(self):
        """Test parsing from mixed case string."""
        assert DataClassification.from_string("Public") == DataClassification.PUBLIC
        assert DataClassification.from_string("Internal") == DataClassification.INTERNAL
        assert DataClassification.from_string("Confidential") == DataClassification.CONFIDENTIAL

    def test_from_string_empty_returns_default(self):
        """Empty string returns INTERNAL (safe default)."""
        assert DataClassification.from_string("") == DataClassification.INTERNAL

    def test_from_string_invalid_raises_error(self):
        """Invalid string raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            DataClassification.from_string("secret")
        assert "Invalid classification" in str(exc_info.value)

    def test_default(self):
        """Test default classification is INTERNAL."""
        assert DataClassification.default() == DataClassification.INTERNAL

    def test_display_name(self):
        """Test display names are uppercase."""
        assert DataClassification.PUBLIC.display_name == "PUBLIC"
        assert DataClassification.INTERNAL.display_name == "INTERNAL"
        assert DataClassification.CONFIDENTIAL.display_name == "CONFIDENTIAL"

    def test_description(self):
        """Test descriptions are set."""
        assert "No business impact" in DataClassification.PUBLIC.description
        assert "Proprietary" in DataClassification.INTERNAL.description
        assert "PII" in DataClassification.CONFIDENTIAL.description

    def test_str_enum_inheritance(self):
        """Test that it's a string enum (for database compatibility)."""
        # Can be used as a string directly via .value
        assert f"Level: {DataClassification.PUBLIC.value}" == "Level: public"
        # Also works for comparisons
        assert DataClassification.PUBLIC.value == "public"

    def test_comparison_with_non_classification_returns_not_implemented(self):
        """Comparison with non-DataClassification returns NotImplemented."""
        result = DataClassification.PUBLIC.__gt__("public")
        assert result is NotImplemented


class TestExportControlService:
    """Tests for ExportControlService."""

    @pytest.mark.asyncio
    async def test_is_internal_localhost(self):
        """localhost should be internal."""
        from app.services.export_control import ExportControlService

        service = ExportControlService()
        is_internal = await service.is_internal_endpoint("http://localhost:8000/api")
        assert is_internal is True

    @pytest.mark.asyncio
    async def test_is_internal_127_0_0_1(self):
        """127.0.0.1 should be internal."""
        from app.services.export_control import ExportControlService

        service = ExportControlService()
        is_internal = await service.is_internal_endpoint("http://127.0.0.1:8000/api")
        assert is_internal is True

    @pytest.mark.asyncio
    async def test_is_internal_private_network_10(self):
        """10.x.x.x should be internal (when hostname is an IP, DNS returns it directly)."""
        from app.services.export_control import ExportControlService

        service = ExportControlService()
        # When hostname is already an IP address (10.0.0.1), DNS lookup returns
        # that IP directly. Since 10.0.0.0/8 is a private range, this is internal.
        is_internal = await service.is_internal_endpoint("http://10.0.0.1:8000/api")
        assert is_internal is True

    @pytest.mark.asyncio
    async def test_is_external_openai(self):
        """api.openai.com should be external."""
        from app.services.export_control import ExportControlService

        service = ExportControlService()
        is_internal = await service.is_internal_endpoint("https://api.openai.com/v1/chat")
        assert is_internal is False

    @pytest.mark.asyncio
    async def test_is_external_anthropic(self):
        """api.anthropic.com should be external."""
        from app.services.export_control import ExportControlService

        service = ExportControlService()
        is_internal = await service.is_internal_endpoint("https://api.anthropic.com/v1/messages")
        assert is_internal is False

    @pytest.mark.asyncio
    async def test_invalid_url_returns_false(self):
        """Invalid URL returns False (fail closed)."""
        from app.services.export_control import ExportControlService

        service = ExportControlService()
        is_internal = await service.is_internal_endpoint("not-a-valid-url")
        assert is_internal is False

    @pytest.mark.asyncio
    async def test_empty_hostname_returns_false(self):
        """URL with no hostname returns False."""
        from app.services.export_control import ExportControlService

        service = ExportControlService()
        is_internal = await service.is_internal_endpoint("file:///etc/passwd")
        assert is_internal is False

    def test_is_ip_private_localhost(self):
        """127.0.0.1 is private."""
        from app.services.export_control import ExportControlService

        service = ExportControlService()
        assert service.is_ip_private("127.0.0.1") is True

    def test_is_ip_private_class_a(self):
        """10.x.x.x is private."""
        from app.services.export_control import ExportControlService

        service = ExportControlService()
        assert service.is_ip_private("10.0.0.1") is True
        assert service.is_ip_private("10.255.255.255") is True

    def test_is_ip_private_class_b(self):
        """172.16.x.x is private."""
        from app.services.export_control import ExportControlService

        service = ExportControlService()
        assert service.is_ip_private("172.16.0.1") is True
        assert service.is_ip_private("172.31.255.255") is True

    def test_is_ip_private_class_c(self):
        """192.168.x.x is private."""
        from app.services.export_control import ExportControlService

        service = ExportControlService()
        assert service.is_ip_private("192.168.0.1") is True
        assert service.is_ip_private("192.168.255.255") is True

    def test_is_ip_private_public_ip(self):
        """8.8.8.8 is public."""
        from app.services.export_control import ExportControlService

        service = ExportControlService()
        assert service.is_ip_private("8.8.8.8") is False

    def test_is_ip_private_invalid_ip(self):
        """Invalid IP returns False."""
        from app.services.export_control import ExportControlService

        service = ExportControlService()
        assert service.is_ip_private("not-an-ip") is False

    def test_validate_no_redirect_no_location(self):
        """No redirect returns True."""
        from app.services.export_control import ExportControlService

        service = ExportControlService()
        result = service.validate_no_redirect_to_external({}, "http://example.com")
        assert result is True

    def test_validate_redirect_to_localhost(self):
        """Redirect to localhost is allowed."""
        from app.services.export_control import ExportControlService

        service = ExportControlService()
        result = service.validate_no_redirect_to_external(
            {"Location": "http://localhost:8000/callback"},
            "http://example.com"
        )
        assert result is True

    def test_validate_relative_redirect(self):
        """Relative redirect is allowed (same host)."""
        from app.services.export_control import ExportControlService

        service = ExportControlService()
        result = service.validate_no_redirect_to_external(
            {"Location": "/api/callback"},
            "http://example.com"
        )
        assert result is True


class TestInternalEndpointWhitelist:
    """Tests for InternalEndpointWhitelist model."""

    def test_create_whitelist_entry(self):
        """Test creating a new whitelist entry."""
        from app.models.internal_endpoint_whitelist import InternalEndpointWhitelist

        user_id = uuid4()
        # Note: SQLAlchemy `default=False` isn't applied until flush.
        # Explicitly set is_active=False for unit tests without DB.
        entry = InternalEndpointWhitelist(
            hostname="internal-api.corp.example.com",
            description="Internal analytics API",
            created_by=user_id,
            is_active=False,  # Explicit for unit test
        )

        assert entry.hostname == "internal-api.corp.example.com"
        assert entry.description == "Internal analytics API"
        assert entry.created_by == user_id
        assert entry.is_active is False  # Not active until approved
        assert entry.approved_by is None
        assert entry.approved_at is None

    def test_approve_whitelist_entry(self):
        """Test approving a whitelist entry."""
        from app.models.internal_endpoint_whitelist import InternalEndpointWhitelist

        creator_id = uuid4()
        approver_id = uuid4()

        entry = InternalEndpointWhitelist(
            hostname="internal-api.corp.example.com",
            created_by=creator_id,
        )

        entry.approve(approver_id)

        assert entry.is_active is True
        assert entry.approved_by == approver_id
        assert entry.approved_at is not None

    def test_approve_own_entry_raises_error(self):
        """Test that approving your own entry raises an error (dual-control)."""
        from app.models.internal_endpoint_whitelist import InternalEndpointWhitelist

        user_id = uuid4()
        entry = InternalEndpointWhitelist(
            hostname="internal-api.corp.example.com",
            created_by=user_id,
        )

        with pytest.raises(ValueError) as exc_info:
            entry.approve(user_id)

        assert "Dual-control violation" in str(exc_info.value)

    def test_deactivate_whitelist_entry(self):
        """Test deactivating a whitelist entry."""
        from app.models.internal_endpoint_whitelist import InternalEndpointWhitelist

        creator_id = uuid4()
        approver_id = uuid4()
        deactivator_id = uuid4()

        entry = InternalEndpointWhitelist(
            hostname="internal-api.corp.example.com",
            created_by=creator_id,
        )
        entry.approve(approver_id)

        entry.deactivate(deactivator_id, reason="No longer needed")

        assert entry.is_active is False
        assert entry.deactivated_by == deactivator_id
        assert entry.deactivated_at is not None
        assert entry.deactivation_reason == "No longer needed"

    def test_is_pending_approval(self):
        """Test is_pending_approval property."""
        from app.models.internal_endpoint_whitelist import InternalEndpointWhitelist

        entry = InternalEndpointWhitelist(
            hostname="internal-api.corp.example.com",
            created_by=uuid4(),
        )

        assert entry.is_pending_approval is True

        entry.approve(uuid4())
        assert entry.is_pending_approval is False

    def test_to_dict(self):
        """Test to_dict method."""
        from app.models.internal_endpoint_whitelist import InternalEndpointWhitelist

        user_id = uuid4()
        # Note: SQLAlchemy `default=False` isn't applied until flush.
        # Explicitly set is_active=False for unit tests without DB.
        entry = InternalEndpointWhitelist(
            hostname="internal-api.corp.example.com",
            description="Test API",
            created_by=user_id,
            is_active=False,  # Explicit for unit test
        )

        result = entry.to_dict()

        assert result["hostname"] == "internal-api.corp.example.com"
        assert result["description"] == "Test API"
        assert result["is_active"] is False
        assert result["is_pending_approval"] is True


class TestNodeExecutionResultClassification:
    """Tests for NodeExecutionResult classification field."""

    def test_default_classification(self):
        """Default classification is INTERNAL."""
        from app.node_executors import NodeExecutionResult

        result = NodeExecutionResult(
            node_id="test-node",
            status="success",
        )

        assert result.classification == DataClassification.INTERNAL

    def test_explicit_classification(self):
        """Can set explicit classification."""
        from app.node_executors import NodeExecutionResult

        result = NodeExecutionResult(
            node_id="test-node",
            status="success",
            classification=DataClassification.CONFIDENTIAL,
        )

        assert result.classification == DataClassification.CONFIDENTIAL

    def test_classification_in_model_dump(self):
        """Classification is included in model_dump()."""
        from app.node_executors import NodeExecutionResult

        result = NodeExecutionResult(
            node_id="test-node",
            status="success",
            classification=DataClassification.PUBLIC,
        )

        dump = result.model_dump()
        assert dump["classification"] == DataClassification.PUBLIC


class TestBaseNodeExecutorExportControl:
    """Tests for BaseNodeExecutor export control hooks."""

    def test_makes_external_calls_default_false(self):
        """Default makes_external_calls is False."""
        from app.node_executors import BaseNodeExecutor

        # Check the class attribute
        assert BaseNodeExecutor.makes_external_calls is False

    def test_llm_executor_makes_external_calls(self):
        """LLMExecutor has makes_external_calls = True."""
        from app.node_executors.llm_executor import LLMExecutor

        executor = LLMExecutor()
        assert executor.makes_external_calls is True

    def test_http_executor_makes_external_calls(self):
        """HTTPExecutor has makes_external_calls = True."""
        from app.node_executors.http_executor import HTTPExecutor

        executor = HTTPExecutor()
        assert executor.makes_external_calls is True

    def test_connector_executor_makes_external_calls(self):
        """ConnectorExecutor has makes_external_calls = True."""
        from app.node_executors.connector_executor import ConnectorExecutor

        executor = ConnectorExecutor()
        assert executor.makes_external_calls is True

    def test_code_executor_internal_only(self):
        """CodeExecutor does NOT make external calls."""
        from app.node_executors.code_executor import CodeExecutor

        executor = CodeExecutor()
        assert executor.makes_external_calls is False

    def test_template_executor_internal_only(self):
        """TemplateExecutor does NOT make external calls."""
        from app.node_executors.template_executor import TemplateExecutor

        executor = TemplateExecutor()
        assert executor.makes_external_calls is False


class TestAuditActionClassificationEvents:
    """Tests for classification-related audit actions."""

    def test_classification_audit_actions_exist(self):
        """Test that classification audit actions are defined."""
        from app.models.compliance_audit_log import AuditAction

        assert hasattr(AuditAction, 'CLASSIFICATION_ASSIGNED')
        assert hasattr(AuditAction, 'CLASSIFICATION_PROPAGATED')
        assert hasattr(AuditAction, 'EXPORT_BLOCKED')
        assert hasattr(AuditAction, 'EXPORT_ALLOWED')
        assert hasattr(AuditAction, 'CLASSIFICATION_DOWNGRADE_REQUESTED')

    def test_audit_action_values(self):
        """Test audit action string values."""
        from app.models.compliance_audit_log import AuditAction

        assert AuditAction.EXPORT_BLOCKED.value == 'export_blocked'
        assert AuditAction.EXPORT_ALLOWED.value == 'export_allowed'
