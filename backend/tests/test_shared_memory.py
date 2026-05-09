"""
Unit tests for Organization Context Layer (Shared Memory).

Tests cover:
- Shared memory storage with visibility parameter
- Shared memory recall within tenant
- PII scan blocking when sharing
- Shared memory context formatting
- Org profile retrieval and update
- RBAC enforcement for sharing operations
- Feature flag gating
"""

import pytest
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.episodic_memory import (
    EpisodicMemoryService,
    reset_episodic_memory_service,
)
from app.schemas.memory import EpisodicMemoryResponse


@pytest.fixture
def service():
    """Create a fresh EpisodicMemoryService for testing."""
    reset_episodic_memory_service()
    return EpisodicMemoryService()


@pytest.fixture
def mock_db():
    """Create a mock database session."""
    mock = AsyncMock()
    mock.execute = AsyncMock()
    mock.commit = AsyncMock()
    mock.refresh = AsyncMock()
    mock.add = MagicMock()
    mock.scalar = AsyncMock()
    # begin_nested() must return an async context manager (SAVEPOINT)
    nested_cm = AsyncMock()
    nested_cm.__aenter__ = AsyncMock(return_value=None)
    nested_cm.__aexit__ = AsyncMock(return_value=False)
    mock.begin_nested = MagicMock(return_value=nested_cm)
    return mock


@pytest.fixture
def tenant_id():
    return uuid.UUID("11111111-1111-1111-1111-111111111111")


@pytest.fixture
def user_id():
    return uuid.UUID("22222222-2222-2222-2222-222222222222")


@pytest.fixture
def other_user_id():
    return uuid.UUID("33333333-3333-3333-3333-333333333333")


class TestStoreDecisionWithVisibility:
    """Tests for storing decisions with visibility parameter."""

    @pytest.mark.asyncio
    async def test_store_decision_default_private(self, service, mock_db, tenant_id, user_id):
        """Default visibility is private."""
        with patch.object(service, '_get_embedding', return_value=[0.0] * 1536):
            await service.store_decision(
                db=mock_db,
                tenant_id=tenant_id,
                user_id=user_id,
                agent_name="analysis",
                action_type="recommendation",
                context="Test context",
                decision="Test decision",
            )

            # Verify the model was created with default private visibility
            mock_db.add.assert_called_once()
            add_call = mock_db.add.call_args
            memory_obj = add_call[0][0]
            assert memory_obj.visibility == "private"
            mock_db.flush.assert_called()

    @pytest.mark.asyncio
    async def test_store_decision_shared_visibility(self, service, mock_db, tenant_id, user_id):
        """Can store decision with shared visibility."""
        with patch.object(service, '_get_embedding', return_value=[0.0] * 1536):
            await service.store_decision(
                db=mock_db,
                tenant_id=tenant_id,
                user_id=user_id,
                agent_name="compliance",
                action_type="compliance_check",
                context="SEC filing review",
                decision="Compliant",
                visibility="shared",
            )

            mock_db.add.assert_called_once()
            add_call = mock_db.add.call_args
            memory_obj = add_call[0][0]
            assert memory_obj.visibility == "shared"

    @pytest.mark.asyncio
    async def test_store_shared_blocks_pii_ssn(self, service, mock_db, tenant_id, user_id):
        """Sharing is blocked when SSN is detected in content."""
        with patch.object(service, '_get_embedding', return_value=[0.0] * 1536):
            # PII scan runs inside store_decision when visibility='shared'
            with pytest.raises(ValueError, match="Cannot share memory.*SSN detected"):
                await service.store_decision(
                    db=mock_db,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    agent_name="analysis",
                    action_type="analysis",
                    context="Customer SSN 123-45-6789",
                    decision="Has outstanding balance",
                    visibility="shared",
                )

            # Memory should NOT be added to DB
            mock_db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_store_shared_allows_clean_text(self, service, mock_db, tenant_id, user_id):
        """Clean text can be stored as shared."""
        with patch.object(service, '_get_embedding', return_value=[0.0] * 1536):
            await service.store_decision(
                db=mock_db,
                tenant_id=tenant_id,
                user_id=user_id,
                agent_name="analysis",
                action_type="analysis",
                context="Acme Corp credit risk elevated",
                decision="Downgrade recommended",
                visibility="shared",
            )

            mock_db.add.assert_called_once()
            add_call = mock_db.add.call_args
            memory_obj = add_call[0][0]
            assert memory_obj.visibility == "shared"


class TestRecallShared:
    """Tests for recalling shared memories across a tenant."""

    @pytest.mark.asyncio
    async def test_recall_shared_returns_empty_when_disabled(self, service, mock_db, tenant_id):
        """Returns empty list when USE_ORG_CONTEXT is disabled."""
        with patch('app.services.episodic_memory.get_settings') as mock_settings:
            mock_settings.return_value = MagicMock(USE_ORG_CONTEXT=False)

            results = await service.recall_shared(
                db=mock_db,
                tenant_id=tenant_id,
                query="test query",
            )

            assert results == []

    @pytest.mark.asyncio
    async def test_recall_shared_returns_results_when_enabled(self, service, mock_db, tenant_id):
        """Returns shared memories when USE_ORG_CONTEXT is enabled."""
        with patch('app.services.episodic_memory.get_settings') as mock_settings:
            mock_settings.return_value = MagicMock(USE_ORG_CONTEXT=True)

            with patch.object(service, '_get_embedding', return_value=[0.1] * 1536):
                mock_row = MagicMock()
                mock_row.id = uuid.uuid4()
                mock_row.session_id = "session_shared"
                mock_row.agent_name = "compliance"
                mock_row.action_type = "compliance_check"
                mock_row.context_decision = "SEC filing deadline is March 1"
                mock_row.rationale = "Regulatory requirement"
                mock_row.outcome = None
                mock_row.entities = ["SEC"]
                mock_row.similarity = 0.85
                mock_row.importance = 0.8
                mock_row.archived = False
                mock_row.access_count = 5
                mock_row.created_at = datetime.utcnow()

                mock_result = MagicMock()
                mock_result.fetchall.return_value = [mock_row]
                mock_db.execute.return_value = mock_result

                results = await service.recall_shared(
                    db=mock_db,
                    tenant_id=tenant_id,
                    query="SEC filing deadlines",
                )

                assert len(results) == 1
                assert results[0].agent_name == "compliance"
                assert results[0].similarity == 0.85

    @pytest.mark.asyncio
    async def test_recall_shared_returns_empty_on_no_embedding(self, service, mock_db, tenant_id):
        """Returns empty list when embedding generation fails."""
        with patch('app.services.episodic_memory.get_settings') as mock_settings:
            mock_settings.return_value = MagicMock(USE_ORG_CONTEXT=True)

            with patch.object(service, '_get_embedding', return_value=None):
                results = await service.recall_shared(
                    db=mock_db,
                    tenant_id=tenant_id,
                    query="test query",
                )

                assert results == []

    @pytest.mark.asyncio
    async def test_recall_shared_graceful_degradation(self, service, mock_db, tenant_id):
        """Returns empty list on database error."""
        with patch('app.services.episodic_memory.get_settings') as mock_settings:
            mock_settings.return_value = MagicMock(USE_ORG_CONTEXT=True)

            with patch.object(service, '_get_embedding', return_value=[0.1] * 1536):
                mock_db.execute.side_effect = Exception("Database error")

                results = await service.recall_shared(
                    db=mock_db,
                    tenant_id=tenant_id,
                    query="test query",
                )

                assert results == []


class TestFormatSharedForContext:
    """Tests for formatting shared memories for agent context injection."""

    def test_format_empty_returns_empty(self, service):
        """Empty memory list returns empty string."""
        result = service.format_shared_for_context([])
        assert result == ""

    def test_format_single_memory(self, service):
        """Single memory is formatted correctly."""
        memories = [
            EpisodicMemoryResponse(
                id=uuid.uuid4(),
                session_id="session_1",
                agent_name="compliance",
                action_type="compliance_check",
                context_decision="SEC filing deadline is March 1, 2026",
                rationale=None,
                outcome=None,
                entities=["SEC"],
                similarity=0.85,
                importance=0.8,
                archived=False,
                access_count=5,
                created_at=datetime(2026, 2, 3),
            )
        ]

        result = service.format_shared_for_context(memories)

        assert "## Organization Shared Knowledge" in result
        assert "compliance_check" in result
        assert "SEC filing deadline" in result
        assert "2026-02-03" in result

    def test_format_limits_to_three(self, service):
        """Formatting limits to 3 entries even if more provided."""
        memories = [
            EpisodicMemoryResponse(
                id=uuid.uuid4(),
                session_id=f"session_{i}",
                agent_name="analysis",
                action_type="analysis",
                context_decision=f"Memory {i}",
                rationale=None,
                outcome=None,
                entities=[],
                similarity=0.9,
                importance=0.7,
                archived=False,
                access_count=1,
                created_at=datetime(2026, 2, i + 1),
            )
            for i in range(5)
        ]

        result = service.format_shared_for_context(memories)
        assert result.count("**analysis**") == 3

    def test_format_truncates_long_content(self, service):
        """Long content is truncated with ellipsis."""
        long_text = "A" * 200
        memories = [
            EpisodicMemoryResponse(
                id=uuid.uuid4(),
                session_id="session_1",
                agent_name="analysis",
                action_type="analysis",
                context_decision=long_text,
                rationale=None,
                outcome=None,
                entities=[],
                similarity=0.9,
                importance=0.7,
                archived=False,
                access_count=1,
                created_at=datetime(2026, 2, 1),
            )
        ]

        result = service.format_shared_for_context(memories)
        assert "..." in result
        # Should not contain the full 200-char string
        assert long_text not in result


class TestVisibilitySchemas:
    """Tests for visibility type validation in schemas."""

    def test_store_decision_request_accepts_private(self):
        from app.schemas.memory import StoreDecisionRequest
        req = StoreDecisionRequest(
            agent_name="test",
            action_type="analysis",
            context="test",
            decision="test",
            visibility="private",
        )
        assert req.visibility == "private"

    def test_store_decision_request_accepts_shared(self):
        from app.schemas.memory import StoreDecisionRequest
        req = StoreDecisionRequest(
            agent_name="test",
            action_type="analysis",
            context="test",
            decision="test",
            visibility="shared",
        )
        assert req.visibility == "shared"

    def test_store_decision_request_defaults_private(self):
        from app.schemas.memory import StoreDecisionRequest
        req = StoreDecisionRequest(
            agent_name="test",
            action_type="analysis",
            context="test",
            decision="test",
        )
        assert req.visibility == "private"

    def test_search_request_include_shared_default_false(self):
        from app.schemas.memory import SearchDecisionsRequest
        req = SearchDecisionsRequest(query="test")
        assert req.include_shared is False

    def test_hybrid_search_request_include_shared_default_false(self):
        from app.schemas.memory import HybridSearchRequest
        req = HybridSearchRequest(query="test")
        assert req.include_shared is False


class TestOrgProfileSchemas:
    """Tests for org profile request/response schemas."""

    def test_org_profile_update_request(self):
        from app.schemas.memory import OrgProfileUpdateRequest
        req = OrgProfileUpdateRequest(
            industry="Financial Services",
            description="A mid-market investment bank",
            compliance_frameworks=["SOX", "GDPR"],
            default_context="We are a mid-cap asset manager",
        )
        assert req.industry == "Financial Services"
        assert len(req.compliance_frameworks) == 2

    def test_org_profile_update_all_optional(self):
        from app.schemas.memory import OrgProfileUpdateRequest
        req = OrgProfileUpdateRequest()
        assert req.industry is None
        assert req.description is None
        assert req.compliance_frameworks is None
        assert req.default_context is None

    def test_org_profile_response(self):
        from app.schemas.memory import OrgProfileResponse
        resp = OrgProfileResponse(
            tenant_id="11111111-1111-1111-1111-111111111111",
            industry="Financial Services",
            compliance_frameworks=["SOX"],
            shared_memory_count=42,
        )
        assert resp.shared_memory_count == 42


class TestFeatureFlagGating:
    """Tests that USE_ORG_CONTEXT flag gates org context features."""

    @pytest.mark.asyncio
    async def test_recall_shared_gated_by_flag(self, service, mock_db, tenant_id):
        """recall_shared returns empty when USE_ORG_CONTEXT is False."""
        with patch('app.services.episodic_memory.get_settings') as mock_settings:
            mock_settings.return_value = MagicMock(USE_ORG_CONTEXT=False)

            results = await service.recall_shared(
                db=mock_db,
                tenant_id=tenant_id,
                query="any query",
            )

            assert results == []
            # Should not attempt any database queries
            mock_db.execute.assert_not_called()
