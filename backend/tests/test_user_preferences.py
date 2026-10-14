"""
Unit tests for UserPreferencesService.

Tests cover:
- Getting preferences with defaults
- Setting preferences
- Updating preferences (partial update)
- Deleting preferences
- Redis caching
- Context formatting
"""

import pytest
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.user_preferences import (
    UserPreferencesService,
    get_user_preferences_service,
    reset_user_preferences_service,
    deep_merge,
)


@pytest.fixture
def preferences_service():
    """Create a fresh UserPreferencesService for testing."""
    reset_user_preferences_service()
    service = UserPreferencesService()
    service._redis = None
    service._redis_disabled = True  # Prevent Redis reconnection in tests
    return service


@pytest.fixture
def mock_db():
    """Create a mock database session."""
    mock = AsyncMock()
    mock.execute = AsyncMock()
    mock.commit = AsyncMock()
    mock.delete = AsyncMock()
    return mock


@pytest.fixture
def test_tenant_id():
    """Test tenant ID."""
    return uuid.UUID("11111111-1111-1111-1111-111111111111")


@pytest.fixture
def test_user_id():
    """Test user ID."""
    return uuid.UUID("22222222-2222-2222-2222-222222222222")


class TestDeepMerge:
    """Tests for deep_merge utility."""

    def test_deep_merge_simple(self):
        """Test merging simple dictionaries."""
        base = {"a": 1, "b": 2}
        override = {"b": 3, "c": 4}

        result = deep_merge(base, override)

        assert result == {"a": 1, "b": 3, "c": 4}

    def test_deep_merge_nested(self):
        """Test merging nested dictionaries."""
        base = {
            "level1": {
                "level2a": 1,
                "level2b": 2,
            }
        }
        override = {
            "level1": {
                "level2b": 3,
                "level2c": 4,
            }
        }

        result = deep_merge(base, override)

        assert result["level1"]["level2a"] == 1  # Kept from base
        assert result["level1"]["level2b"] == 3  # Overridden
        assert result["level1"]["level2c"] == 4  # Added from override

    def test_deep_merge_preserves_original(self):
        """Test that original dicts are not modified."""
        base = {"a": {"b": 1}}
        override = {"a": {"c": 2}}

        result = deep_merge(base, override)

        assert base == {"a": {"b": 1}}  # Unchanged
        assert result["a"]["c"] == 2


class TestGetPreferences:
    """Tests for getting preferences."""

    @pytest.mark.asyncio
    async def test_get_preferences_returns_defaults_when_none(
        self, preferences_service, mock_db, test_tenant_id, test_user_id
    ):
        """Test that defaults are returned when user has no preferences."""
        # Mock no preferences found
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        prefs = await preferences_service.get_preferences(
            mock_db, test_tenant_id, test_user_id
        )

        # Should have default values
        assert prefs["analysis"]["risk_tolerance"] == "moderate"
        assert "P/E" in prefs["analysis"]["preferred_metrics"]
        assert prefs["output"]["verbosity"] == "standard"

    @pytest.mark.asyncio
    async def test_get_preferences_merges_with_defaults(
        self, preferences_service, mock_db, test_tenant_id, test_user_id
    ):
        """Test that user preferences are merged with defaults."""
        # Mock user preferences
        mock_prefs = MagicMock()
        mock_prefs.preferences = {
            "analysis": {
                "risk_tolerance": "high",
                # Other fields should come from defaults
            }
        }

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_prefs
        mock_db.execute.return_value = mock_result

        prefs = await preferences_service.get_preferences(
            mock_db, test_tenant_id, test_user_id
        )

        # User override
        assert prefs["analysis"]["risk_tolerance"] == "high"
        # Default values
        assert "P/E" in prefs["analysis"]["preferred_metrics"]


class TestSetPreferences:
    """Tests for setting preferences."""

    @pytest.mark.asyncio
    async def test_set_preferences_validates_schema(
        self, preferences_service, mock_db, test_tenant_id, test_user_id
    ):
        """Test that preferences are validated against schema."""
        # Valid preferences should work
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = MagicMock(
            id=uuid.uuid4(),
            tenant_id=test_tenant_id,
            user_id=test_user_id,
            preferences={},
            schema_version=1,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        mock_db.execute.return_value = mock_result

        await preferences_service.set_preferences(
            mock_db, test_tenant_id, test_user_id,
            {
                "analysis": {"risk_tolerance": "high"},
                "output": {"verbosity": "detailed"},
            }
        )

        mock_db.execute.assert_called()
        mock_db.commit.assert_called()

    @pytest.mark.asyncio
    async def test_set_preferences_rejects_invalid_risk_tolerance(
        self, preferences_service, mock_db, test_tenant_id, test_user_id
    ):
        """Test that invalid risk tolerance is rejected."""
        with pytest.raises(Exception):  # Pydantic validation error
            await preferences_service.set_preferences(
                mock_db, test_tenant_id, test_user_id,
                {"analysis": {"risk_tolerance": "invalid"}}
            )


class TestUpdatePreferences:
    """Tests for updating preferences."""

    @pytest.mark.asyncio
    async def test_update_preferences_partial(
        self, preferences_service, mock_db, test_tenant_id, test_user_id
    ):
        """Test partial update of preferences."""
        # Mock existing preferences
        mock_record = MagicMock()
        mock_record.preferences = {
            "analysis": {"risk_tolerance": "moderate"},
            "output": {"verbosity": "standard"},
        }

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_record
        mock_db.execute.return_value = mock_result

        # Update only analysis.risk_tolerance
        with patch.object(preferences_service, 'set_preferences', new_callable=AsyncMock) as mock_set:
            mock_set.return_value = mock_record

            await preferences_service.update_preferences(
                mock_db, test_tenant_id, test_user_id,
                {"analysis": {"risk_tolerance": "high"}}
            )

            # Should have merged update with existing
            # set_preferences is called with positional args: (db, tenant_id, user_id, merged_prefs)
            call_args = mock_set.call_args[0]  # Get positional args
            merged_prefs = call_args[3]  # Fourth argument is the merged preferences
            assert merged_prefs["analysis"]["risk_tolerance"] == "high"


class TestDeletePreferences:
    """Tests for deleting preferences."""

    @pytest.mark.asyncio
    async def test_delete_preferences_existing(
        self, preferences_service, mock_db, test_tenant_id, test_user_id
    ):
        """Test deleting existing preferences."""
        mock_record = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_record
        mock_db.execute.return_value = mock_result

        result = await preferences_service.delete_preferences(
            mock_db, test_tenant_id, test_user_id
        )

        assert result is True
        mock_db.delete.assert_called_once_with(mock_record)
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_preferences_not_found(
        self, preferences_service, mock_db, test_tenant_id, test_user_id
    ):
        """Test deleting non-existent preferences."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        result = await preferences_service.delete_preferences(
            mock_db, test_tenant_id, test_user_id
        )

        assert result is False
        mock_db.delete.assert_not_called()


class TestAgentConfig:
    """Tests for getting agent-specific configuration."""

    @pytest.mark.asyncio
    async def test_get_agent_config_analysis(
        self, preferences_service, mock_db, test_tenant_id, test_user_id
    ):
        """Test getting config for analysis agent."""
        with patch.object(
            preferences_service, 'get_preferences',
            return_value={
                "analysis": {"risk_tolerance": "high"},
                "output": {"verbosity": "detailed"},
                "data_sources": {"include_news": True},
                "compliance": {"auto_check": False},
            }
        ):
            config = await preferences_service.get_agent_config(
                mock_db, test_tenant_id, test_user_id, "analysis"
            )

            assert "analysis" in config
            assert "output" in config
            assert "data_sources" in config
            assert config["analysis"]["risk_tolerance"] == "high"

    @pytest.mark.asyncio
    async def test_get_agent_config_compliance(
        self, preferences_service, mock_db, test_tenant_id, test_user_id
    ):
        """Test getting config for compliance agent."""
        with patch.object(
            preferences_service, 'get_preferences',
            return_value={
                "analysis": {"risk_tolerance": "high"},
                "output": {"verbosity": "detailed"},
                "compliance": {"auto_check": True, "frameworks": ["SOC2"]},
            }
        ):
            config = await preferences_service.get_agent_config(
                mock_db, test_tenant_id, test_user_id, "compliance"
            )

            assert "compliance" in config
            assert "output" in config
            assert config["compliance"]["auto_check"] is True


class TestContextFormatting:
    """Tests for formatting preferences for context."""

    @pytest.mark.xfail(strict=True, reason="KNOWN_FAILURES U13: undiagnosed")
    def test_format_for_context(self, preferences_service):
        """Test formatting preferences for agent context."""
        prefs = {
            "analysis": {
                "risk_tolerance": "high",
                "preferred_metrics": ["P/E", "ROE", "Debt/Equity"],
                "default_timeframe": "2Y",
                "benchmark_index": "QQQ",
            },
            "output": {
                "verbosity": "detailed",
            },
        }

        result = preferences_service.format_for_context(prefs)

        assert "## User Preferences" in result
        assert "Risk tolerance: high" in result
        assert "P/E" in result
        assert "Timeframe: 2Y" in result
        assert "Benchmark: QQQ" in result
        assert "Output style: detailed" in result

    def test_format_for_context_missing_fields(self, preferences_service):
        """Test formatting with missing fields uses defaults."""
        prefs = {"analysis": {}, "output": {}}

        result = preferences_service.format_for_context(prefs)

        # Should use 'moderate' as default
        assert "moderate" in result


class TestCaching:
    """Tests for Redis caching behavior."""

    @pytest.mark.asyncio
    async def test_cache_key_format(self, preferences_service, test_tenant_id, test_user_id):
        """Test cache key format."""
        key = preferences_service._cache_key(test_tenant_id, test_user_id)

        assert f"prefs:{test_tenant_id}:{test_user_id}" == key

    @pytest.mark.asyncio
    async def test_get_from_cache_returns_none_when_no_redis(
        self, preferences_service, test_tenant_id, test_user_id
    ):
        """Test cache read returns None when Redis unavailable."""
        preferences_service._redis = None

        result = await preferences_service._get_from_cache(test_tenant_id, test_user_id)

        assert result is None


class TestSingletonBehavior:
    """Tests for singleton pattern."""

    def test_get_user_preferences_service_singleton(self):
        """Test that get_user_preferences_service returns singleton."""
        reset_user_preferences_service()

        service1 = get_user_preferences_service()
        service2 = get_user_preferences_service()

        assert service1 is service2

        reset_user_preferences_service()

    def test_reset_user_preferences_service(self):
        """Test resetting the singleton."""
        reset_user_preferences_service()

        service1 = get_user_preferences_service()
        reset_user_preferences_service()
        service2 = get_user_preferences_service()

        assert service1 is not service2

        reset_user_preferences_service()


class TestDefaultPreferences:
    """Tests for default preference values."""

    def test_default_preferences_structure(self, preferences_service):
        """Test that default preferences have correct structure."""
        defaults = preferences_service.DEFAULT_PREFERENCES

        assert "analysis" in defaults
        assert "output" in defaults
        assert "data_sources" in defaults
        assert "compliance" in defaults

    def test_default_analysis_preferences(self, preferences_service):
        """Test default analysis preferences."""
        analysis = preferences_service.DEFAULT_PREFERENCES["analysis"]

        assert analysis["risk_tolerance"] == "moderate"
        assert "P/E" in analysis["preferred_metrics"]
        assert analysis["default_timeframe"] == "1Y"
        assert analysis["benchmark_index"] == "SPY"

    def test_default_output_preferences(self, preferences_service):
        """Test default output preferences."""
        output = preferences_service.DEFAULT_PREFERENCES["output"]

        assert output["verbosity"] == "standard"
        assert output["include_charts"] is True
        assert output["report_format"] == "technical"
