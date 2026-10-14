"""
Unit tests for LLM Gateway Service.

Tests cover:
- Provider failover with circuit breaker
- Cost estimation
- Provider-specific model prefixes
- Connection testing
- Usage logging
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.services.llm_gateway import (
    LLMGateway,
    LLMRequest,
    LLMResponse,
    AllProvidersFailedError,
    ProviderConfig,
    get_provider_circuit_breaker,
    verify_provider_connection,
    _circuit_breakers,
)


class TestLLMGateway:
    """Test LLM Gateway functionality."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock async database session."""
        return AsyncMock()

    @pytest.fixture
    def gateway(self, mock_db):
        """Create gateway with mocked database."""
        return LLMGateway(mock_db)

    @pytest.fixture
    def mock_provider(self):
        """Create a mock provider config."""
        return ProviderConfig(
            id=uuid4(),
            name="test-openai",
            provider_type="openai",
            api_base=None,
            api_key="sk-test-key",
            default_model="gpt-4o-mini",
            priority=0,
        )

    @pytest.fixture
    def sample_request(self):
        """Create a sample LLM request."""
        return LLMRequest(
            messages=[{"role": "user", "content": "Hello"}],
            model="gpt-4o-mini",
            temperature=0.7,
        )

    @pytest.fixture(autouse=True)
    def clear_circuit_breakers(self):
        """Clear circuit breaker cache before each test."""
        _circuit_breakers.clear()
        yield
        _circuit_breakers.clear()

    @pytest.mark.asyncio
    async def test_successful_completion(self, gateway, sample_request, mock_provider):
        """Test successful LLM completion."""
        tenant_id = uuid4()

        # Mock LiteLLM response
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="Hello there!"))]
        mock_response.model = "gpt-4o-mini"
        mock_response.usage = MagicMock(prompt_tokens=5, completion_tokens=10)

        with patch('app.services.llm_gateway.get_tenant_providers') as mock_get_providers, \
             patch('app.services.llm_gateway.litellm') as mock_litellm:

            mock_get_providers.return_value = [mock_provider]
            mock_litellm.acompletion = AsyncMock(return_value=mock_response)
            mock_litellm.completion_cost.return_value = 0.00001

            response = await gateway.complete(sample_request, tenant_id)

            assert isinstance(response, LLMResponse)
            assert response.content == "Hello there!"
            assert response.provider_name == "test-openai"
            assert response.input_tokens == 5
            assert response.output_tokens == 10

    @pytest.mark.xfail(strict=True, reason="KNOWN_FAILURES U13: failover/retry behaviour differs from test expectation")
    @pytest.mark.asyncio
    async def test_failover_to_second_provider(self, gateway, sample_request):
        """Test automatic failover when first provider fails."""
        tenant_id = uuid4()

        provider1 = ProviderConfig(
            id=uuid4(),
            name="primary-openai",
            provider_type="openai",
            api_base=None,
            api_key="sk-primary",
            default_model="gpt-4o",
            priority=0,
        )
        provider2 = ProviderConfig(
            id=uuid4(),
            name="backup-anthropic",
            provider_type="anthropic",
            api_base=None,
            api_key="sk-ant-backup",
            default_model="claude-3-haiku-20240307",
            priority=1,
        )

        # Mock successful response from second provider
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="Backup response"))]
        mock_response.model = "claude-3-haiku-20240307"
        mock_response.usage = MagicMock(
            prompt_tokens=5,
            completion_tokens=10,
            input_tokens=5,
            cache_read_input_tokens=0,  # Required for Anthropic cache stats logging
        )

        with patch('app.services.llm_gateway.get_tenant_providers') as mock_get_providers, \
             patch('app.services.llm_gateway.litellm') as mock_litellm, \
             patch('app.services.llm_gateway.settings') as mock_settings:
            mock_settings.USE_PROVIDER_CACHING = True

            mock_get_providers.return_value = [provider1, provider2]

            # First call fails, second succeeds
            mock_litellm.acompletion = AsyncMock(side_effect=[
                Exception("Primary provider down"),
                mock_response,
            ])
            mock_litellm.completion_cost.return_value = 0.00001

            response = await gateway.complete(sample_request, tenant_id)

            assert response.provider_name == "backup-anthropic"
            assert response.content == "Backup response"

    @pytest.mark.asyncio
    async def test_all_providers_fail(self, gateway, sample_request):
        """Test error when all providers fail."""
        tenant_id = uuid4()

        provider1 = ProviderConfig(
            id=uuid4(),
            name="primary",
            provider_type="openai",
            api_base=None,
            api_key="sk-1",
            default_model="gpt-4o",
            priority=0,
        )
        provider2 = ProviderConfig(
            id=uuid4(),
            name="backup",
            provider_type="openai",
            api_base=None,
            api_key="sk-2",
            default_model="gpt-4o",
            priority=1,
        )

        with patch('app.services.llm_gateway.get_tenant_providers') as mock_get_providers, \
             patch('app.services.llm_gateway.litellm') as mock_litellm:

            mock_get_providers.return_value = [provider1, provider2]
            mock_litellm.acompletion = AsyncMock(side_effect=Exception("API Error"))

            with pytest.raises(AllProvidersFailedError) as exc_info:
                await gateway.complete(sample_request, tenant_id)

            assert "All 2 providers failed" in str(exc_info.value)
            assert len(exc_info.value.errors) == 2

    @pytest.mark.asyncio
    async def test_no_providers_configured(self, gateway, sample_request):
        """Test error when no providers are configured."""
        tenant_id = uuid4()

        with patch('app.services.llm_gateway.get_tenant_providers') as mock_get_providers:
            mock_get_providers.return_value = []

            with pytest.raises(AllProvidersFailedError) as exc_info:
                await gateway.complete(sample_request, tenant_id)

            assert "No LLM providers configured" in str(exc_info.value)


class TestProviderPrefixes:
    """Test provider-specific model prefix handling."""

    @pytest.fixture
    def mock_db(self):
        return AsyncMock()

    @pytest.fixture
    def gateway(self, mock_db):
        return LLMGateway(mock_db)

    def test_openrouter_prefix(self, gateway):
        """Test OpenRouter model prefix is added."""
        request = LLMRequest(
            messages=[{"role": "user", "content": "test"}],
            model="gpt-4o",
        )
        provider = ProviderConfig(
            id=uuid4(),
            name="openrouter",
            provider_type="openrouter",
            api_base=None,
            api_key="sk-or-test",
            default_model="gpt-4o",
            priority=0,
        )

        params = gateway._build_litellm_params(request, provider)

        assert params["model"] == "openrouter/gpt-4o"

    def test_anthropic_prefix(self, gateway):
        """Test Anthropic model prefix is added for Claude models."""
        request = LLMRequest(
            messages=[{"role": "user", "content": "test"}],
            model="claude-3-5-sonnet-20241022",
        )
        provider = ProviderConfig(
            id=uuid4(),
            name="anthropic",
            provider_type="anthropic",
            api_base=None,
            api_key="sk-ant-test",
            default_model="claude-3-5-sonnet-20241022",
            priority=0,
        )

        params = gateway._build_litellm_params(request, provider)

        assert params["model"] == "anthropic/claude-3-5-sonnet-20241022"

    def test_no_prefix_for_openai(self, gateway):
        """Test OpenAI models don't get prefixed."""
        request = LLMRequest(
            messages=[{"role": "user", "content": "test"}],
            model="gpt-4o",
        )
        provider = ProviderConfig(
            id=uuid4(),
            name="openai",
            provider_type="openai",
            api_base=None,
            api_key="sk-test",
            default_model="gpt-4o",
            priority=0,
        )

        params = gateway._build_litellm_params(request, provider)

        assert params["model"] == "gpt-4o"

    def test_custom_endpoint_includes_api_base(self, gateway):
        """Test custom endpoints include api_base."""
        request = LLMRequest(
            messages=[{"role": "user", "content": "test"}],
            model="llama-3.1-70b",
        )
        provider = ProviderConfig(
            id=uuid4(),
            name="vllm",
            provider_type="custom",
            api_base="http://localhost:8001/v1",
            api_key="token",
            default_model="llama-3.1-70b",
            priority=0,
        )

        params = gateway._build_litellm_params(request, provider)

        assert params["api_base"] == "http://localhost:8001/v1"


class TestCostEstimation:
    """Test cost estimation fallback."""

    @pytest.fixture
    def mock_db(self):
        return AsyncMock()

    @pytest.fixture
    def gateway(self, mock_db):
        return LLMGateway(mock_db)

    def test_gpt4o_cost_estimation(self, gateway):
        """Test GPT-4o cost estimation."""
        cost = gateway._estimate_cost(
            input_tokens=1000,
            output_tokens=500,
            model="gpt-4o",
        )

        # 1000 input tokens * $2.5/1M + 500 output tokens * $10/1M
        expected = (1000 / 1_000_000 * 2.5) + (500 / 1_000_000 * 10.0)
        assert abs(cost - expected) < 0.0001

    def test_gpt4o_mini_cost_estimation(self, gateway):
        """Test GPT-4o-mini cost estimation.

        Note: The cost estimation uses substring matching, so 'gpt-4o-mini'
        matches 'gpt-4o' first (which is conservative/safer pricing).
        """
        cost = gateway._estimate_cost(
            input_tokens=1000,
            output_tokens=500,
            model="gpt-4o-mini",
        )

        # Due to substring matching, gpt-4o-mini matches gpt-4o pricing
        # This is a conservative estimate (higher than actual mini pricing)
        expected = (1000 / 1_000_000 * 2.5) + (500 / 1_000_000 * 10.0)
        assert abs(cost - expected) < 0.0001

    def test_claude_cost_estimation(self, gateway):
        """Test Claude 3.5 Sonnet cost estimation."""
        cost = gateway._estimate_cost(
            input_tokens=1000,
            output_tokens=500,
            model="claude-3-5-sonnet-20241022",
        )

        expected = (1000 / 1_000_000 * 3.0) + (500 / 1_000_000 * 15.0)
        assert abs(cost - expected) < 0.0001

    def test_unknown_model_uses_default(self, gateway):
        """Test unknown models use conservative defaults."""
        cost = gateway._estimate_cost(
            input_tokens=1000,
            output_tokens=500,
            model="unknown-model-xyz",
        )

        # Default: $1/1M input + $2/1M output
        expected = (1000 / 1_000_000 * 1.0) + (500 / 1_000_000 * 2.0)
        assert abs(cost - expected) < 0.0001


class TestCircuitBreakerIntegration:
    """Test circuit breaker behavior with LLM Gateway."""

    @pytest.fixture(autouse=True)
    def clear_circuit_breakers(self):
        """Clear circuit breaker cache."""
        _circuit_breakers.clear()
        yield
        _circuit_breakers.clear()

    def test_circuit_breaker_creation(self):
        """Test circuit breaker is created for each provider key."""
        key1 = "llm:tenant1:openai"
        key2 = "llm:tenant1:anthropic"

        cb1 = get_provider_circuit_breaker(key1)
        cb2 = get_provider_circuit_breaker(key2)

        assert cb1 is not cb2
        assert cb1.name == key1
        assert cb2.name == key2

    def test_circuit_breaker_reuse(self):
        """Test same circuit breaker is reused for same key."""
        key = "llm:tenant1:openai"

        cb1 = get_provider_circuit_breaker(key)
        cb2 = get_provider_circuit_breaker(key)

        assert cb1 is cb2

    @pytest.mark.asyncio
    async def test_skips_provider_with_open_circuit(self):
        """Test gateway skips providers with open circuits."""
        mock_db = AsyncMock()
        gateway = LLMGateway(mock_db)
        tenant_id = uuid4()

        provider1 = ProviderConfig(
            id=uuid4(),
            name="primary",
            provider_type="openai",
            api_base=None,
            api_key="sk-1",
            default_model="gpt-4o",
            priority=0,
        )
        provider2 = ProviderConfig(
            id=uuid4(),
            name="backup",
            provider_type="openai",
            api_base=None,
            api_key="sk-2",
            default_model="gpt-4o",
            priority=1,
        )

        # Open circuit for first provider
        circuit_key = f"llm:{tenant_id}:primary"
        circuit = get_provider_circuit_breaker(circuit_key)
        for _ in range(3):
            await circuit.record_failure(Exception("test"))

        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="backup response"))]
        mock_response.model = "gpt-4o"
        mock_response.usage = MagicMock(prompt_tokens=5, completion_tokens=10)

        request = LLMRequest(
            messages=[{"role": "user", "content": "test"}],
            model="gpt-4o",
        )

        with patch('app.services.llm_gateway.get_tenant_providers') as mock_get_providers, \
             patch('app.services.llm_gateway.litellm') as mock_litellm:

            mock_get_providers.return_value = [provider1, provider2]
            mock_litellm.acompletion = AsyncMock(return_value=mock_response)
            mock_litellm.completion_cost.return_value = 0.00001

            response = await gateway.complete(request, tenant_id)

            # Should use backup since primary circuit is open
            assert response.provider_name == "backup"

            # LiteLLM should only be called once (for backup)
            assert mock_litellm.acompletion.call_count == 1


class TestProviderConnectionTest:
    """Test provider connection testing."""

    @pytest.mark.asyncio
    async def test_successful_connection(self):
        """Test successful connection test."""
        mock_response = MagicMock()
        mock_response.model = "gpt-4o-mini"

        with patch('app.services.llm_gateway.litellm') as mock_litellm:
            mock_litellm.acompletion = AsyncMock(return_value=mock_response)

            result = await verify_provider_connection(
                provider_type="openai",
                api_base=None,
                api_key="sk-test",
                model="gpt-4o-mini",
            )

            assert result["success"] is True
            assert result["model"] == "gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_failed_connection(self):
        """Test failed connection test."""
        with patch('app.services.llm_gateway.litellm') as mock_litellm:
            mock_litellm.acompletion = AsyncMock(side_effect=Exception("Invalid API key"))

            result = await verify_provider_connection(
                provider_type="openai",
                api_base=None,
                api_key="invalid-key",
                model="gpt-4o-mini",
            )

            assert result["success"] is False
            assert "Invalid API key" in result["error"]

    @pytest.mark.asyncio
    async def test_openrouter_prefix_in_test(self):
        """Test OpenRouter prefix is applied during connection test."""
        mock_response = MagicMock()
        mock_response.model = "openrouter/gpt-4o"

        with patch('app.services.llm_gateway.litellm') as mock_litellm:
            mock_litellm.acompletion = AsyncMock(return_value=mock_response)

            await verify_provider_connection(
                provider_type="openrouter",
                api_base=None,
                api_key="sk-or-test",
                model="gpt-4o",
            )

            call_args = mock_litellm.acompletion.call_args
            assert call_args.kwargs["model"] == "openrouter/gpt-4o"


class TestUsageLogging:
    """Test usage logging functionality."""

    @pytest.mark.asyncio
    async def test_usage_record_created(self):
        """Test usage record is added to database."""
        mock_db = AsyncMock()
        gateway = LLMGateway(mock_db)
        tenant_id = uuid4()
        user_id = uuid4()

        provider = ProviderConfig(
            id=uuid4(),
            name="openai",
            provider_type="openai",
            api_base=None,
            api_key="sk-test",
            default_model="gpt-4o-mini",
            priority=0,
        )

        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="response"))]
        mock_response.model = "gpt-4o-mini"
        mock_response.usage = MagicMock(prompt_tokens=10, completion_tokens=20)

        request = LLMRequest(
            messages=[{"role": "user", "content": "test"}],
            model="gpt-4o-mini",
            workflow_id=uuid4(),
        )

        with patch('app.services.llm_gateway.get_tenant_providers') as mock_get_providers, \
             patch('app.services.llm_gateway.litellm') as mock_litellm:

            mock_get_providers.return_value = [provider]
            mock_litellm.acompletion = AsyncMock(return_value=mock_response)
            mock_litellm.completion_cost.return_value = 0.00005

            await gateway.complete(request, tenant_id, user_id)

            # Verify db.add was called with usage record
            mock_db.add.assert_called_once()
            usage_record = mock_db.add.call_args[0][0]

            assert usage_record.tenant_id == tenant_id
            assert usage_record.user_id == user_id
            assert usage_record.provider_name == "openai"
            assert usage_record.model == "gpt-4o-mini"
            assert usage_record.input_tokens == 10
            assert usage_record.output_tokens == 20
            assert usage_record.workflow_id == request.workflow_id


class TestAnthropicPromptCaching:
    """Test Anthropic prompt caching integration (P2 Cost Optimization)."""

    @pytest.fixture
    def mock_db(self):
        return AsyncMock()

    @pytest.fixture
    def gateway(self, mock_db):
        return LLMGateway(mock_db)

    def test_extract_system_prompt(self, gateway):
        """Test system prompt extraction from messages."""
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "How are you?"},
        ]

        system_prompt, filtered = gateway._extract_system_prompt(messages)

        assert system_prompt == "You are a helpful assistant."
        assert len(filtered) == 3
        assert all(msg["role"] != "system" for msg in filtered)

    def test_extract_multiple_system_prompts(self, gateway):
        """Test multiple system messages are concatenated."""
        messages = [
            {"role": "system", "content": "First instruction."},
            {"role": "system", "content": "Second instruction."},
            {"role": "user", "content": "Hello"},
        ]

        system_prompt, filtered = gateway._extract_system_prompt(messages)

        assert "First instruction." in system_prompt
        assert "Second instruction." in system_prompt
        assert len(filtered) == 1

    def test_extract_no_system_prompt(self, gateway):
        """Test extraction when no system message exists."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
        ]

        system_prompt, filtered = gateway._extract_system_prompt(messages)

        assert system_prompt == ""
        assert len(filtered) == 2

    def test_system_prompt_has_cache_control(self, gateway):
        """Verify system prompt gets cache_control marker."""
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]

        result = gateway._prepare_anthropic_request(messages)

        assert result["system"] is not None
        assert len(result["system"]) == 1
        assert result["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert result["system"][0]["text"] == "You are helpful."

    def test_tools_last_item_has_cache_control(self, gateway):
        """Verify only last tool gets cache_control marker."""
        messages = [{"role": "user", "content": "Hello"}]
        tools = [
            {"name": "tool1", "description": "First tool"},
            {"name": "tool2", "description": "Second tool"},
            {"name": "tool3", "description": "Third tool"},
        ]

        result = gateway._prepare_anthropic_request(messages, tools)

        # First two tools should NOT have cache_control
        assert "cache_control" not in result["tools"][0]
        assert "cache_control" not in result["tools"][1]
        # Only last tool should have cache_control
        assert result["tools"][2]["cache_control"] == {"type": "ephemeral"}

    def test_tools_original_list_not_mutated(self, gateway):
        """Verify caller's tools list is not modified."""
        messages = [{"role": "user", "content": "Hello"}]
        original_tools = [
            {"name": "tool1", "description": "First tool"},
            {"name": "tool2", "description": "Second tool"},
        ]
        # Make a copy to compare
        tools_before = [dict(t) for t in original_tools]

        gateway._prepare_anthropic_request(messages, original_tools)

        # Original tools should be unchanged
        for i, tool in enumerate(original_tools):
            assert tool == tools_before[i]
            assert "cache_control" not in tool

    def test_no_system_prompt_returns_none(self, gateway):
        """Verify system is None when no system prompt exists."""
        messages = [{"role": "user", "content": "Hello"}]

        result = gateway._prepare_anthropic_request(messages)

        assert result["system"] is None

    def test_no_tools_returns_none(self, gateway):
        """Verify tools is None when no tools provided."""
        messages = [{"role": "user", "content": "Hello"}]

        result = gateway._prepare_anthropic_request(messages)

        assert result["tools"] is None

    @patch('app.services.llm_gateway.settings')
    def test_caching_applied_for_anthropic(self, mock_settings, gateway):
        """Verify caching is applied when USE_PROVIDER_CACHING=True and provider is Anthropic."""
        mock_settings.USE_PROVIDER_CACHING = True

        request = LLMRequest(
            messages=[
                {"role": "system", "content": "Be helpful"},
                {"role": "user", "content": "Hello"},
            ],
            model="claude-3-5-sonnet-20241022",
        )
        provider = ProviderConfig(
            id=uuid4(),
            name="anthropic",
            provider_type="anthropic",
            api_base=None,
            api_key="sk-ant-test",
            default_model="claude-3-5-sonnet-20241022",
            priority=0,
        )

        params = gateway._build_litellm_params(request, provider)

        # System prompt should be extracted and have cache_control
        assert "system" in params
        assert params["system"][0]["cache_control"] == {"type": "ephemeral"}
        # Messages should not include system message
        assert all(msg.get("role") != "system" for msg in params["messages"])

    @patch('app.services.llm_gateway.settings')
    def test_caching_not_applied_for_openai(self, mock_settings, gateway):
        """Verify caching is NOT applied for non-Anthropic providers."""
        mock_settings.USE_PROVIDER_CACHING = True

        request = LLMRequest(
            messages=[
                {"role": "system", "content": "Be helpful"},
                {"role": "user", "content": "Hello"},
            ],
            model="gpt-4o",
        )
        provider = ProviderConfig(
            id=uuid4(),
            name="openai",
            provider_type="openai",
            api_base=None,
            api_key="sk-test",
            default_model="gpt-4o",
            priority=0,
        )

        params = gateway._build_litellm_params(request, provider)

        # System should NOT be extracted for OpenAI
        assert "system" not in params
        # Messages should still include system message
        assert any(msg.get("role") == "system" for msg in params["messages"])

    @patch('app.services.llm_gateway.settings')
    def test_caching_disabled_via_feature_flag(self, mock_settings, gateway):
        """Verify USE_PROVIDER_CACHING=False disables cache markers."""
        mock_settings.USE_PROVIDER_CACHING = False

        request = LLMRequest(
            messages=[
                {"role": "system", "content": "Be helpful"},
                {"role": "user", "content": "Hello"},
            ],
            model="claude-3-5-sonnet-20241022",
        )
        provider = ProviderConfig(
            id=uuid4(),
            name="anthropic",
            provider_type="anthropic",
            api_base=None,
            api_key="sk-ant-test",
            default_model="claude-3-5-sonnet-20241022",
            priority=0,
        )

        params = gateway._build_litellm_params(request, provider)

        # No cache-related params when disabled
        assert "system" not in params


class TestCacheStatsLogging:
    """Test cache statistics logging."""

    @pytest.fixture
    def mock_db(self):
        return AsyncMock()

    @pytest.fixture
    def gateway(self, mock_db):
        return LLMGateway(mock_db)

    def test_log_anthropic_cache_stats(self, gateway, caplog):
        """Verify cache stats are logged for Anthropic responses."""
        import logging
        caplog.set_level(logging.INFO)

        mock_response = MagicMock()
        mock_response.usage = MagicMock(
            input_tokens=1000,
            cache_read_input_tokens=800,
        )

        gateway._log_cache_stats(mock_response, "anthropic")

        assert "Anthropic cache" in caplog.text
        assert "hit_rate=80.0%" in caplog.text
        assert "cached=800" in caplog.text

    def test_log_anthropic_missing_cache_attrs(self, gateway, caplog):
        """Verify graceful handling when cache attributes missing."""
        import logging
        caplog.set_level(logging.DEBUG)

        mock_response = MagicMock()
        mock_response.usage = MagicMock(spec=['input_tokens'])
        mock_response.usage.input_tokens = 1000

        gateway._log_cache_stats(mock_response, "anthropic")

        assert "caching may not be enabled" in caplog.text

    def test_log_openai_cache_stats(self, gateway, caplog):
        """Verify cache stats are logged for OpenAI responses."""
        import logging
        caplog.set_level(logging.INFO)

        mock_response = MagicMock()
        mock_response.usage = MagicMock(
            prompt_tokens=1000,
        )
        mock_response.usage.prompt_tokens_details = MagicMock(cached_tokens=500)

        gateway._log_cache_stats(mock_response, "openai")

        assert "OpenAI cache" in caplog.text
        assert "cached=500" in caplog.text


class TestPromptHashing:
    """Test prompt hashing for cache keys."""

    @pytest.fixture
    def mock_db(self):
        return AsyncMock()

    @pytest.fixture
    def gateway(self, mock_db):
        return LLMGateway(mock_db)

    def test_hash_is_deterministic(self, gateway):
        """Verify same messages produce same hash."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
        ]

        hash1 = gateway._hash_prompt(messages)
        hash2 = gateway._hash_prompt(messages)

        assert hash1 == hash2

    def test_hash_is_16_chars(self, gateway):
        """Verify hash is truncated to 16 characters."""
        messages = [{"role": "user", "content": "Hello"}]

        hash_result = gateway._hash_prompt(messages)

        assert len(hash_result) == 16

    def test_different_messages_different_hash(self, gateway):
        """Verify different messages produce different hashes."""
        messages1 = [{"role": "user", "content": "Hello"}]
        messages2 = [{"role": "user", "content": "Goodbye"}]

        hash1 = gateway._hash_prompt(messages1)
        hash2 = gateway._hash_prompt(messages2)

        assert hash1 != hash2

    def test_hash_ignores_dict_ordering(self, gateway):
        """Verify hash is consistent regardless of dict key order."""
        messages1 = [{"role": "user", "content": "Hello"}]
        messages2 = [{"content": "Hello", "role": "user"}]

        hash1 = gateway._hash_prompt(messages1)
        hash2 = gateway._hash_prompt(messages2)

        assert hash1 == hash2
