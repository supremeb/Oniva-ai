"""
Unit tests for EpisodicMemoryService.

Tests cover:
- Storing decisions to episodic memory
- Searching similar decisions
- Memory consolidation
- Health statistics
- Graceful degradation
- Context formatting
"""

import pytest
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.episodic_memory import (
    EpisodicMemoryService,
    get_episodic_memory_service,
    reset_episodic_memory_service,
)
from app.schemas.memory import EpisodicMemoryResponse


@pytest.fixture
def episodic_service():
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
def test_tenant_id():
    """Test tenant ID."""
    return uuid.UUID("11111111-1111-1111-1111-111111111111")


@pytest.fixture
def test_user_id():
    """Test user ID."""
    return uuid.UUID("22222222-2222-2222-2222-222222222222")


class TestEpisodicMemoryStorage:
    """Tests for storing decisions."""

    @pytest.mark.asyncio
    async def test_store_decision_creates_memory(
        self, episodic_service, mock_db, test_tenant_id, test_user_id
    ):
        """Test that store_decision creates a memory entry."""
        # Mock the RAG service to return a dummy embedding
        with patch.object(
            episodic_service, '_get_embedding',
            return_value=[0.0] * 1536
        ):
            # Mock refresh to set the id
            async def mock_refresh(obj):
                obj.id = uuid.uuid4()
            mock_db.refresh = mock_refresh

            memory_id = await episodic_service.store_decision(
                db=mock_db,
                tenant_id=test_tenant_id,
                user_id=test_user_id,
                agent_name="analysis",
                action_type="recommendation",
                context="User asked about AAPL",
                decision="Recommended buy",
                rationale="Strong fundamentals",
                entities=["AAPL"],
            )

            assert memory_id is not None
            mock_db.add.assert_called_once()
            mock_db.flush.assert_called()

    @pytest.mark.asyncio
    async def test_store_decision_combines_context_and_decision(
        self, episodic_service, mock_db, test_tenant_id, test_user_id
    ):
        """Test that context and decision are combined for embedding."""
        combined_texts = []

        async def capture_embedding(text, db):
            combined_texts.append(text)
            return [0.0] * 1536

        with patch.object(episodic_service, '_get_embedding', capture_embedding):
            async def mock_refresh(obj):
                obj.id = uuid.uuid4()
            mock_db.refresh = mock_refresh

            await episodic_service.store_decision(
                db=mock_db,
                tenant_id=test_tenant_id,
                user_id=test_user_id,
                agent_name="analysis",
                action_type="analysis",
                context="What is AAPL risk?",
                decision="Moderate risk",
                rationale="Volatility analysis",
            )

            assert len(combined_texts) == 1
            assert "What is AAPL risk?" in combined_texts[0]
            assert "Moderate risk" in combined_texts[0]
            assert "Volatility analysis" in combined_texts[0]

    @pytest.mark.asyncio
    async def test_store_decision_graceful_degradation(
        self, episodic_service, mock_db, test_tenant_id, test_user_id
    ):
        """Test that storage continues even if embedding fails."""
        # Make embedding generation fail
        async def failing_embedding(text, db):
            raise Exception("Embedding service unavailable")

        with patch.object(episodic_service, '_get_embedding', failing_embedding):
            async def mock_refresh(obj):
                obj.id = uuid.uuid4()
            mock_db.refresh = mock_refresh

            # Should not raise, should store with null embedding
            memory_id = await episodic_service.store_decision(
                db=mock_db,
                tenant_id=test_tenant_id,
                user_id=test_user_id,
                agent_name="analysis",
                action_type="analysis",
                context="Test context",
                decision="Test decision",
            )

            assert memory_id is not None
            mock_db.add.assert_called_once()


class TestEpisodicMemorySearch:
    """Tests for searching similar decisions."""

    @pytest.mark.xfail(strict=True, reason="KNOWN_FAILURES U13: MagicMock rows fail pydantic validation")
    @pytest.mark.asyncio
    async def test_recall_similar_returns_results(
        self, episodic_service, mock_db, test_tenant_id, test_user_id
    ):
        """Test that recall_similar returns matching results."""
        # Mock the embedding
        with patch.object(
            episodic_service, '_get_embedding',
            return_value=[0.1] * 1536
        ):
            # Mock database results
            mock_row = MagicMock()
            mock_row.id = uuid.uuid4()
            mock_row.session_id = "session_123"
            mock_row.agent_name = "analysis"
            mock_row.action_type = "recommendation"
            mock_row.context_decision = "Context: AAPL\nDecision: Buy"
            mock_row.rationale = "Strong fundamentals"
            mock_row.outcome = None
            mock_row.entities = ["AAPL"]
            mock_row.similarity = 0.92
            mock_row.created_at = datetime.utcnow()

            mock_result = MagicMock()
            mock_result.fetchall.return_value = [mock_row]
            mock_db.execute.return_value = mock_result

            results = await episodic_service.recall_similar(
                db=mock_db,
                tenant_id=test_tenant_id,
                user_id=test_user_id,
                query="Tell me about AAPL",
                limit=3,
            )

            assert len(results) == 1
            assert results[0].agent_name == "analysis"
            assert results[0].similarity == 0.92

    @pytest.mark.asyncio
    async def test_recall_similar_returns_empty_on_no_embedding(
        self, episodic_service, mock_db, test_tenant_id, test_user_id
    ):
        """Test that search returns empty list when embedding fails."""
        with patch.object(episodic_service, '_get_embedding', return_value=None):
            results = await episodic_service.recall_similar(
                db=mock_db,
                tenant_id=test_tenant_id,
                user_id=test_user_id,
                query="Test query",
            )

            assert results == []

    @pytest.mark.asyncio
    async def test_recall_similar_graceful_degradation(
        self, episodic_service, mock_db, test_tenant_id, test_user_id
    ):
        """Test graceful degradation on database error."""
        with patch.object(
            episodic_service, '_get_embedding',
            return_value=[0.1] * 1536
        ):
            mock_db.execute.side_effect = Exception("Database error")

            results = await episodic_service.recall_similar(
                db=mock_db,
                tenant_id=test_tenant_id,
                user_id=test_user_id,
                query="Test query",
            )

            assert results == []


class TestEpisodicMemoryConsolidation:
    """Tests for memory consolidation."""

    @pytest.mark.asyncio
    async def test_should_consolidate_below_threshold(
        self, episodic_service, mock_db, test_tenant_id, test_user_id
    ):
        """Test should_consolidate returns False below threshold."""
        mock_db.scalar.return_value = 1000  # Below 50000 threshold

        result = await episodic_service.should_consolidate(
            mock_db, test_tenant_id, test_user_id
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_should_consolidate_above_threshold(
        self, episodic_service, mock_db, test_tenant_id, test_user_id
    ):
        """Test should_consolidate returns True above threshold."""
        mock_db.scalar.return_value = 60000  # Above 50000 threshold

        result = await episodic_service.should_consolidate(
            mock_db, test_tenant_id, test_user_id
        )

        assert result is True

    @pytest.mark.asyncio
    async def test_consolidate_archives_expired(
        self, episodic_service, mock_db, test_tenant_id, test_user_id
    ):
        """Test that consolidation archives expired memories."""
        mock_result = MagicMock()
        mock_result.rowcount = 5
        mock_db.execute.return_value = mock_result

        stats = await episodic_service.consolidate(
            mock_db, test_tenant_id, test_user_id
        )

        assert stats["archived"] == 5
        mock_db.commit.assert_called_once()


class TestEpisodicMemoryHealth:
    """Tests for health statistics."""

    @pytest.mark.asyncio
    async def test_get_health_stats(
        self, episodic_service, mock_db, test_tenant_id, test_user_id
    ):
        """Test getting health statistics."""
        # Mock single query result with all stats (count, oldest, newest)
        mock_row = MagicMock()
        mock_row.count = 1000
        mock_row.oldest = datetime(2025, 1, 1)
        mock_row.newest = datetime(2026, 1, 17)

        mock_result = MagicMock()
        mock_result.one.return_value = mock_row
        mock_db.execute.return_value = mock_result

        stats = await episodic_service.get_health_stats(
            mock_db, test_tenant_id, test_user_id
        )

        assert stats["status"] == "healthy"
        assert stats["total_memories"] == 1000
        assert stats["oldest_memory"] == datetime(2025, 1, 1)
        assert stats["newest_memory"] == datetime(2026, 1, 17)
        assert stats["index_status"] == "hnsw_active"
        assert stats["consolidation_needed"] is False  # 1000 < 50000 threshold


class TestContextFormatting:
    """Tests for formatting memories for context."""

    def test_format_for_context_empty(self, episodic_service):
        """Test formatting empty memory list."""
        result = episodic_service.format_for_context([])
        assert result == ""

    def test_format_for_context_single_memory(self, episodic_service):
        """Test formatting single memory."""
        memories = [
            EpisodicMemoryResponse(
                id=uuid.uuid4(),
                session_id="session_1",
                agent_name="analysis",
                action_type="recommendation",
                context_decision="Context: AAPL analysis\nDecision: Buy recommendation",
                rationale="Strong fundamentals",
                outcome=None,
                entities=["AAPL"],
                similarity=0.9,
                created_at=datetime.utcnow(),
            )
        ]

        result = episodic_service.format_for_context(memories)

        assert "## Similar Past Analyses" in result
        assert "recommendation" in result

    def test_format_for_context_limits_to_three(self, episodic_service):
        """Test that formatting limits to 3 memories."""
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
                created_at=datetime.utcnow(),
            )
            for i in range(5)
        ]

        result = episodic_service.format_for_context(memories)

        # Should only have 3 entries
        assert result.count("**analysis**") == 3


class TestSingletonBehavior:
    """Tests for singleton pattern."""

    def test_get_episodic_memory_service_singleton(self):
        """Test that get_episodic_memory_service returns singleton."""
        reset_episodic_memory_service()

        service1 = get_episodic_memory_service()
        service2 = get_episodic_memory_service()

        assert service1 is service2

        reset_episodic_memory_service()

    def test_reset_episodic_memory_service(self):
        """Test resetting the singleton."""
        reset_episodic_memory_service()

        service1 = get_episodic_memory_service()
        reset_episodic_memory_service()
        service2 = get_episodic_memory_service()

        assert service1 is not service2

        reset_episodic_memory_service()
