"""
Unit tests for LLM Gateway Service.

Tests cover:
- Provider failover with circuit breaker
- Cost calculation from the pricing table
- Direct request construction (Anthropic prompt caching)
- Connection testing
- Usage logging
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.services.llm_direct import DirectResponse
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


def _http_error(status: int) -> Exception:
    """An SDK-style error carrying an HTTP status code."""
    error = Exception(f"HTTP {status}")
    error.status_code = status
    return error


def _response(
    content="Hello there!",
    model="gpt-4o-mini",
    input_tokens=5,
    output_tokens=10,
    tool_calls=None,
    cache_read=0,
    cache_creation=0,
):
    return DirectResponse(
        content=content,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        tool_calls=tool_calls,
        cache_read_tokens=cache_read,
        cache_creation_tokens=cache_creation,
    )


@pytest.fixture(autouse=True)
def clear_circuit_breakers():
    """Clear circuit breaker cache before each test."""
    _circuit_breakers.clear()
    yield
    _circuit_breakers.clear()


@pytest.fixture(autouse=True)
def no_retry_sleep():
    """Keep retry tests fast."""
    with patch("app.services.llm_gateway.asyncio.sleep", new=AsyncMock()):
        yield


class TestLLMGateway:
    """Test LLM Gateway functionality."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock async database session."""
        db = AsyncMock()
        db.add = MagicMock()
        return db

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

    @pytest.mark.asyncio
    async def test_successful_completion(self, gateway, sample_request, mock_provider):
        """Test successful LLM completion."""
        tenant_id = uuid4()

        with patch('app.services.llm_gateway.get_tenant_providers') as mock_get_providers, \
             patch('app.services.llm_gateway.llm_direct.complete', new=AsyncMock(return_value=_response())):

            mock_get_providers.return_value = [mock_provider]

            response = await gateway.complete(sample_request, tenant_id)

            assert isinstance(response, LLMResponse)
            assert response.content == "Hello there!"
            assert response.provider_name == "test-openai"
            assert response.input_tokens == 5
            assert response.output_tokens == 10

    @pytest.mark.asyncio
    async def test_retries_5xx_then_succeeds(self, gateway, sample_request, mock_provider):
        """A 5xx is retried against the same provider before giving up."""
        tenant_id = uuid4()
        complete = AsyncMock(side_effect=[_http_error(503), _response()])

        with patch('app.services.llm_gateway.get_tenant_providers', return_value=[mock_provider]), \
             patch('app.services.llm_gateway.llm_direct.complete', new=complete):

            response = await gateway.complete(sample_request, tenant_id)

            assert response.content == "Hello there!"
            assert complete.call_count == 2

    @pytest.mark.asyncio
    async def test_no_retry_on_401(self, gateway, sample_request):
        """A 401 is not retried; the gateway falls over to the next provider."""
        tenant_id = uuid4()

        provider1 = ProviderConfig(
            id=uuid4(), name="primary-openai", provider_type="openai", api_base=None,
            api_key="sk-primary", default_model="gpt-4o", priority=0,
        )
        provider2 = ProviderConfig(
            id=uuid4(), name="backup-anthropic", provider_type="anthropic", api_base=None,
            api_key="sk-ant-backup", default_model="claude-3-haiku-20240307", priority=1,
        )

        complete = AsyncMock(side_effect=[_http_error(401), _response(content="Backup response", model="claude-3-haiku-20240307")])

        with patch('app.services.llm_gateway.get_tenant_providers', return_value=[provider1, provider2]), \
             patch('app.services.llm_gateway.llm_direct.complete', new=complete):

            response = await gateway.complete(sample_request, tenant_id)

            assert response.provider_name == "backup-anthropic"
            assert response.content == "Backup response"
            # provider1 called once (no retry), provider2 once.
            assert complete.call_count == 2

    @pytest.mark.asyncio
    async def test_all_providers_fail(self, gateway, sample_request):
        """Test error when all providers fail."""
        tenant_id = uuid4()

        provider1 = ProviderConfig(
            id=uuid4(), name="primary", provider_type="openai", api_base=None,
            api_key="sk-1", default_model="gpt-4o", priority=0,
        )
        provider2 = ProviderConfig(
            id=uuid4(), name="backup", provider_type="openai", api_base=None,
            api_key="sk-2", default_model="gpt-4o", priority=1,
        )

        with patch('app.services.llm_gateway.get_tenant_providers', return_value=[provider1, provider2]), \
             patch('app.services.llm_gateway.llm_direct.complete', new=AsyncMock(side_effect=_http_error(500))):

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

    @pytest.mark.asyncio
    async def test_breaker_ignores_4xx(self, gateway, sample_request, mock_provider):
        """Client errors do not trip the circuit breaker."""
        tenant_id = uuid4()
        circuit = get_provider_circuit_breaker(f"llm:{tenant_id}:{mock_provider.name}")

        with patch('app.services.llm_gateway.get_tenant_providers', return_value=[mock_provider]), \
             patch('app.services.llm_gateway.llm_direct.complete', new=AsyncMock(side_effect=_http_error(400))):
            for _ in range(4):
                with pytest.raises(AllProvidersFailedError):
                    await gateway.complete(sample_request, tenant_id)

        assert circuit.state.value == "closed"

    @pytest.mark.asyncio
    async def test_breaker_opens_on_repeated_5xx(self, gateway, sample_request, mock_provider):
        """Repeated server errors trip the circuit breaker."""
        tenant_id = uuid4()
        circuit = get_provider_circuit_breaker(f"llm:{tenant_id}:{mock_provider.name}")

        with patch('app.services.llm_gateway.get_tenant_providers', return_value=[mock_provider]), \
             patch('app.services.llm_gateway.llm_direct.complete', new=AsyncMock(side_effect=_http_error(503))):
            for _ in range(3):
                with pytest.raises(AllProvidersFailedError):
                    await gateway.complete(sample_request, tenant_id)

        assert circuit.state.value == "open"


class TestDirectRequestConstruction:
    """The gateway maps an LLMRequest onto the direct layer's request."""

    @pytest.fixture
    def mock_db(self):
        return AsyncMock()

    @pytest.fixture
    def gateway(self, mock_db):
        return LLMGateway(mock_db)

    @patch('app.services.llm_gateway.settings')
    def test_caching_applied_for_anthropic(self, mock_settings, gateway):
        """Anthropic caching extracts the system prompt and marks the last tool."""
        mock_settings.USE_PROVIDER_CACHING = True

        request = LLMRequest(
            messages=[
                {"role": "system", "content": "Be helpful"},
                {"role": "user", "content": "Hello"},
            ],
            model="claude-3-5-sonnet-20241022",
        )
        provider = ProviderConfig(
            id=uuid4(), name="anthropic", provider_type="anthropic", api_base=None,
            api_key="sk-ant-test", default_model="claude-3-5-sonnet-20241022", priority=0,
        )

        direct_request = gateway._build_direct_request(request, provider)

        # System prompt should be extracted and have cache_control.
        assert direct_request.system[0]["cache_control"] == {"type": "ephemeral"}
        # Messages should not include the system message.
        assert all(msg.get("role") != "system" for msg in direct_request.messages)

    @patch('app.services.llm_gateway.settings')
    def test_caching_not_applied_for_openai(self, mock_settings, gateway):
        """Non-Anthropic providers keep the system message inline."""
        mock_settings.USE_PROVIDER_CACHING = True

        request = LLMRequest(
            messages=[
                {"role": "system", "content": "Be helpful"},
                {"role": "user", "content": "Hello"},
            ],
            model="gpt-4o",
        )
        provider = ProviderConfig(
            id=uuid4(), name="openai", provider_type="openai", api_base=None,
            api_key="sk-test", default_model="gpt-4o", priority=0,
        )

        direct_request = gateway._build_direct_request(request, provider)

        assert direct_request.system is None
        assert any(msg.get("role") == "system" for msg in direct_request.messages)

    @patch('app.services.llm_gateway.settings')
    def test_caching_disabled_via_feature_flag(self, mock_settings, gateway):
        """USE_PROVIDER_CACHING=False leaves the system message inline."""
        mock_settings.USE_PROVIDER_CACHING = False

        request = LLMRequest(
            messages=[
                {"role": "system", "content": "Be helpful"},
                {"role": "user", "content": "Hello"},
            ],
            model="claude-3-5-sonnet-20241022",
        )
        provider = ProviderConfig(
            id=uuid4(), name="anthropic", provider_type="anthropic", api_base=None,
            api_key="sk-ant-test", default_model="claude-3-5-sonnet-20241022", priority=0,
        )

        direct_request = gateway._build_direct_request(request, provider)

        assert direct_request.system is None

    def test_custom_endpoint_passed_through(self, gateway):
        """A custom api_base and model survive request construction untouched."""
        request = LLMRequest(
            messages=[{"role": "user", "content": "test"}],
            model="llama-3.1-70b",
            max_tokens=128,
        )
        provider = ProviderConfig(
            id=uuid4(), name="vllm", provider_type="custom", api_base="http://localhost:8001/v1",
            api_key="token", default_model="llama-3.1-70b", priority=0,
        )

        direct_request = gateway._build_direct_request(request, provider)

        assert direct_request.model == "llama-3.1-70b"
        assert direct_request.max_tokens == 128


class TestCostCalculation:
    """Cost comes from app/config/llm_pricing.py."""

    @pytest.fixture
    def mock_db(self):
        return AsyncMock()

    @pytest.fixture
    def gateway(self, mock_db):
        return LLMGateway(mock_db)

    def test_gpt4o_cost(self, gateway):
        """GPT-4o is priced from the table."""
        cost = gateway._calculate_cost(_response(model="gpt-4o", input_tokens=1000, output_tokens=500))

        expected = (1000 / 1_000_000 * 2.5) + (500 / 1_000_000 * 10.0)
        assert abs(cost - expected) < 0.0001

    def test_unknown_model_is_free_with_warning(self, gateway, caplog):
        """An unknown model records cost 0 rather than guessing."""
        import logging
        caplog.set_level(logging.WARNING)

        cost = gateway._calculate_cost(_response(model="unknown-model-xyz"))

        assert cost == 0.0
        assert "No pricing for model" in caplog.text


class TestCircuitBreakerIntegration:
    """Test circuit breaker behavior with LLM Gateway."""

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
        mock_db.add = MagicMock()
        gateway = LLMGateway(mock_db)
        tenant_id = uuid4()

        provider1 = ProviderConfig(
            id=uuid4(), name="primary", provider_type="openai", api_base=None,
            api_key="sk-1", default_model="gpt-4o", priority=0,
        )
        provider2 = ProviderConfig(
            id=uuid4(), name="backup", provider_type="openai", api_base=None,
            api_key="sk-2", default_model="gpt-4o", priority=1,
        )

        # Open circuit for first provider
        circuit_key = f"llm:{tenant_id}:primary"
        circuit = get_provider_circuit_breaker(circuit_key)
        for _ in range(3):
            await circuit.record_failure(Exception("test"))

        request = LLMRequest(
            messages=[{"role": "user", "content": "test"}],
            model="gpt-4o",
        )

        complete = AsyncMock(return_value=_response(content="backup response", model="gpt-4o"))

        with patch('app.services.llm_gateway.get_tenant_providers', return_value=[provider1, provider2]), \
             patch('app.services.llm_gateway.llm_direct.complete', new=complete):

            response = await gateway.complete(request, tenant_id)

            # Should use backup since primary circuit is open
            assert response.provider_name == "backup"

            # The direct layer should only be called once (for backup)
            assert complete.call_count == 1


class TestProviderConnectionTest:
    """Test provider connection testing."""

    @pytest.mark.asyncio
    async def test_successful_connection(self):
        """Test successful connection test."""
        with patch('app.services.llm_gateway.llm_direct.complete', new=AsyncMock(
            return_value=_response(model="gpt-4o-mini"),
        )):
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
        with patch('app.services.llm_gateway.llm_direct.complete', new=AsyncMock(
            side_effect=Exception("Invalid API key"),
        )):
            result = await verify_provider_connection(
                provider_type="openai",
                api_base=None,
                api_key="invalid-key",
                model="gpt-4o-mini",
            )

            assert result["success"] is False
            assert "Invalid API key" in result["error"]

    @pytest.mark.asyncio
    async def test_openrouter_provider_used(self):
        """Test the OpenRouter provider is what the direct layer receives."""
        complete = AsyncMock(return_value=_response(model="openai/gpt-4o"))

        with patch('app.services.llm_gateway.llm_direct.complete', new=complete):
            await verify_provider_connection(
                provider_type="openrouter",
                api_base=None,
                api_key="sk-or-test",
                model="gpt-4o",
            )

            direct_provider = complete.call_args.args[1]
            assert direct_provider.provider_type == "openrouter"
            assert direct_provider.api_key == "sk-or-test"


class TestUsageLogging:
    """Test usage logging functionality."""

    @pytest.mark.asyncio
    async def test_usage_record_created(self):
        """Test usage record is added to database."""
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        gateway = LLMGateway(mock_db)
        tenant_id = uuid4()
        user_id = uuid4()

        provider = ProviderConfig(
            id=uuid4(), name="openai", provider_type="openai", api_base=None,
            api_key="sk-test", default_model="gpt-4o-mini", priority=0,
        )

        request = LLMRequest(
            messages=[{"role": "user", "content": "test"}],
            model="gpt-4o-mini",
            workflow_id=uuid4(),
        )

        with patch('app.services.llm_gateway.get_tenant_providers', return_value=[provider]), \
             patch('app.services.llm_gateway.llm_direct.complete', new=AsyncMock(
                 return_value=_response(input_tokens=10, output_tokens=20),
             )):

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

        gateway._log_cache_stats(_response(input_tokens=1000, cache_read=800), "anthropic")

        assert "Anthropic cache" in caplog.text
        assert "hit_rate=80.0%" in caplog.text
        assert "cached=800" in caplog.text

    def test_log_openai_cache_stats(self, gateway, caplog):
        """Verify cache stats are logged for OpenAI responses."""
        import logging
        caplog.set_level(logging.INFO)

        gateway._log_cache_stats(_response(input_tokens=1000, cache_read=500), "openai")

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
