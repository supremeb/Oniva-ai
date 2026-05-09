"""Tests for cache metrics endpoint and cache token extraction in _log_usage()."""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.models.llm_usage import LLMUsageRecord
from app.services.llm_gateway import LLMGateway, LLMRequest, ProviderConfig


class TestCacheTokenExtraction:
    """Test that _log_usage() correctly extracts cache tokens per provider."""

    @pytest.fixture
    def mock_db(self):
        return AsyncMock()

    @pytest.fixture
    def gateway(self, mock_db):
        return LLMGateway(mock_db)

    @pytest.fixture
    def base_request(self):
        return LLMRequest(
            messages=[{"role": "user", "content": "Hello"}],
            model="gpt-4o-mini",
        )

    def _make_provider(self, provider_type: str) -> ProviderConfig:
        return ProviderConfig(
            id=uuid4(),
            name=f"test-{provider_type}",
            provider_type=provider_type,
            api_base=None,
            api_key="sk-test",
            default_model="test-model",
            priority=0,
        )

    @pytest.mark.asyncio
    async def test_anthropic_cache_extraction(self, gateway, mock_db, base_request):
        """Anthropic responses populate both cache_read and cache_creation."""
        provider = self._make_provider("anthropic")
        response = MagicMock()
        response.model = "claude-3.5-sonnet"
        response.usage.prompt_tokens = 1000
        response.usage.completion_tokens = 200
        response.usage.cache_read_input_tokens = 800
        response.usage.cache_creation_input_tokens = 100

        await gateway._log_usage(
            tenant_id=uuid4(),
            user_id=uuid4(),
            provider=provider,
            request=base_request,
            response=response,
            cost=0.01,
            latency_ms=150,
        )

        mock_db.add.assert_called_once()
        record = mock_db.add.call_args[0][0]
        assert isinstance(record, LLMUsageRecord)
        assert record.cache_read_tokens == 800
        assert record.cache_creation_tokens == 100

    @pytest.mark.asyncio
    async def test_openai_cache_extraction(self, gateway, mock_db, base_request):
        """OpenAI responses populate cache_read from prompt_tokens_details."""
        provider = self._make_provider("openai")
        response = MagicMock()
        response.model = "gpt-4o"
        response.usage.prompt_tokens = 500
        response.usage.completion_tokens = 100
        response.usage.prompt_tokens_details = MagicMock(cached_tokens=300)
        # OpenAI does not report cache_creation
        del response.usage.cache_read_input_tokens
        del response.usage.cache_creation_input_tokens

        await gateway._log_usage(
            tenant_id=uuid4(),
            user_id=uuid4(),
            provider=provider,
            request=base_request,
            response=response,
            cost=0.005,
            latency_ms=200,
        )

        record = mock_db.add.call_args[0][0]
        assert record.cache_read_tokens == 300
        assert record.cache_creation_tokens == 0

    @pytest.mark.asyncio
    async def test_no_cache_data(self, gateway, mock_db, base_request):
        """Provider with no cache support logs zeros."""
        provider = self._make_provider("openrouter")
        response = MagicMock()
        response.model = "meta-llama/llama-3"
        response.usage.prompt_tokens = 400
        response.usage.completion_tokens = 50
        # No cache attributes
        del response.usage.cache_read_input_tokens
        del response.usage.cache_creation_input_tokens
        del response.usage.prompt_tokens_details

        await gateway._log_usage(
            tenant_id=uuid4(),
            user_id=uuid4(),
            provider=provider,
            request=base_request,
            response=response,
            cost=0.002,
            latency_ms=100,
        )

        record = mock_db.add.call_args[0][0]
        assert record.cache_read_tokens == 0
        assert record.cache_creation_tokens == 0

    @pytest.mark.asyncio
    async def test_anthropic_none_cache_values(self, gateway, mock_db, base_request):
        """Anthropic with None cache values treated as 0."""
        provider = self._make_provider("anthropic")
        response = MagicMock()
        response.model = "claude-3.5-sonnet"
        response.usage.prompt_tokens = 500
        response.usage.completion_tokens = 100
        response.usage.cache_read_input_tokens = None
        response.usage.cache_creation_input_tokens = None

        await gateway._log_usage(
            tenant_id=uuid4(),
            user_id=uuid4(),
            provider=provider,
            request=base_request,
            response=response,
            cost=0.01,
            latency_ms=150,
        )

        record = mock_db.add.call_args[0][0]
        assert record.cache_read_tokens == 0
        assert record.cache_creation_tokens == 0

    @pytest.mark.asyncio
    async def test_openai_no_details(self, gateway, mock_db, base_request):
        """OpenAI without prompt_tokens_details logs 0."""
        provider = self._make_provider("openai")
        response = MagicMock()
        response.model = "gpt-4o-mini"
        response.usage.prompt_tokens = 200
        response.usage.completion_tokens = 50
        response.usage.prompt_tokens_details = None
        del response.usage.cache_read_input_tokens
        del response.usage.cache_creation_input_tokens

        await gateway._log_usage(
            tenant_id=uuid4(),
            user_id=uuid4(),
            provider=provider,
            request=base_request,
            response=response,
            cost=0.001,
            latency_ms=80,
        )

        record = mock_db.add.call_args[0][0]
        assert record.cache_read_tokens == 0
        assert record.cache_creation_tokens == 0


class TestCacheMetricsEndpoint:
    """Test GET /cache-metrics endpoint logic.

    Uses direct function calls with mocked DB to avoid full app setup.
    """

    @pytest.mark.asyncio
    async def test_cache_metrics_with_data(self):
        """Endpoint returns correct aggregated cache stats."""
        from app.api.v1.analytics import get_cache_metrics

        # Mock DB result row
        mock_row = MagicMock()
        mock_row.total_requests = 100
        mock_row.cached_requests = 60
        mock_row.total_cache_read = 50000
        mock_row.total_cache_creation = 10000
        mock_row.total_input = 100000

        mock_result = MagicMock()
        mock_result.one.return_value = mock_row

        mock_db = AsyncMock()
        mock_db.execute.return_value = mock_result

        mock_user = MagicMock()
        tenant_id = uuid4()
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = datetime(2026, 2, 1, tzinfo=timezone.utc)

        response = await get_cache_metrics(
            start_date=start,
            end_date=end,
            db=mock_db,
            user=mock_user,
            tenant_id=tenant_id,
        )

        assert response.total_requests == 100
        assert response.cached_requests == 60
        assert response.hit_rate == pytest.approx(0.5)  # 50000/100000
        assert response.total_cache_read_tokens == 50000
        assert response.total_cache_creation_tokens == 10000

    @pytest.mark.asyncio
    async def test_cache_metrics_no_data(self):
        """Endpoint returns zeros when no data in range."""
        from app.api.v1.analytics import get_cache_metrics

        mock_row = MagicMock()
        mock_row.total_requests = 0
        mock_row.cached_requests = 0
        mock_row.total_cache_read = 0
        mock_row.total_cache_creation = 0
        mock_row.total_input = 0

        mock_result = MagicMock()
        mock_result.one.return_value = mock_row

        mock_db = AsyncMock()
        mock_db.execute.return_value = mock_result

        response = await get_cache_metrics(
            start_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2026, 2, 1, tzinfo=timezone.utc),
            db=mock_db,
            user=MagicMock(),
            tenant_id=uuid4(),
        )

        assert response.total_requests == 0
        assert response.hit_rate == 0.0
        assert response.total_cache_read_tokens == 0
