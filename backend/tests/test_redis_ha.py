"""
Unit tests for Redis High Availability Service.

Tests cover:
- Circuit breaker state transitions
- Redis operations with graceful degradation
- Configuration from settings
- Health check behavior
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.redis_ha import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerError,
    CircuitState,
    RedisConfig,
    RedisHAService,
    _create_config_from_settings,
)


class TestCircuitBreaker:
    """Test circuit breaker behavior."""

    @pytest.fixture
    def circuit_breaker(self) -> CircuitBreaker:
        """Create circuit breaker with default config."""
        config = CircuitBreakerConfig(
            failure_threshold=3,
            success_threshold=2,
            timeout_seconds=1.0,
            half_open_max_calls=2,
        )
        return CircuitBreaker("test", config)

    @pytest.mark.asyncio
    async def test_initial_state_is_closed(self, circuit_breaker):
        """Test circuit starts in closed state."""
        assert circuit_breaker.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_closed_allows_execution(self, circuit_breaker):
        """Test closed circuit allows calls through."""
        can_execute = await circuit_breaker.can_execute()
        assert can_execute is True

    @pytest.mark.asyncio
    async def test_opens_after_failure_threshold(self, circuit_breaker):
        """Test circuit opens after reaching failure threshold."""
        # Record failures
        for _ in range(3):
            await circuit_breaker.record_failure(Exception("test error"))

        assert circuit_breaker.state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_open_blocks_execution(self, circuit_breaker):
        """Test open circuit blocks calls."""
        # Force circuit open
        for _ in range(3):
            await circuit_breaker.record_failure(Exception("test"))

        assert circuit_breaker.state == CircuitState.OPEN
        can_execute = await circuit_breaker.can_execute()
        assert can_execute is False

    @pytest.mark.asyncio
    async def test_transitions_to_half_open_after_timeout(self, circuit_breaker):
        """Test circuit transitions to half-open after timeout."""
        # Open the circuit
        for _ in range(3):
            await circuit_breaker.record_failure(Exception("test"))

        assert circuit_breaker.state == CircuitState.OPEN

        # Wait for timeout
        await asyncio.sleep(1.1)

        # Check state transition
        can_execute = await circuit_breaker.can_execute()
        assert circuit_breaker.state == CircuitState.HALF_OPEN
        assert can_execute is True

    @pytest.mark.asyncio
    async def test_half_open_closes_after_successes(self, circuit_breaker):
        """Test half-open circuit closes after success threshold."""
        # Open and transition to half-open
        for _ in range(3):
            await circuit_breaker.record_failure(Exception("test"))
        await asyncio.sleep(1.1)
        await circuit_breaker.can_execute()

        # Record successes
        await circuit_breaker.record_success()
        await circuit_breaker.record_success()

        assert circuit_breaker.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_half_open_reopens_on_failure(self, circuit_breaker):
        """Test half-open circuit reopens immediately on failure."""
        # Open and transition to half-open
        for _ in range(3):
            await circuit_breaker.record_failure(Exception("test"))
        await asyncio.sleep(1.1)
        await circuit_breaker.can_execute()

        assert circuit_breaker.state == CircuitState.HALF_OPEN

        # Single failure reopens
        await circuit_breaker.record_failure(Exception("test"))

        assert circuit_breaker.state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_success_resets_failure_count(self, circuit_breaker):
        """Test success in closed state resets failure count."""
        await circuit_breaker.record_failure(Exception("test"))
        await circuit_breaker.record_failure(Exception("test"))

        assert circuit_breaker._state.failure_count == 2

        await circuit_breaker.record_success()

        assert circuit_breaker._state.failure_count == 0

    @pytest.mark.asyncio
    async def test_execute_with_success(self, circuit_breaker):
        """Test execute method with successful call."""
        async def success_func():
            return "success"

        result = await circuit_breaker.execute(success_func)
        assert result == "success"

    @pytest.mark.asyncio
    async def test_execute_with_failure(self, circuit_breaker):
        """Test execute method propagates exceptions."""
        async def fail_func():
            raise ValueError("test error")

        with pytest.raises(ValueError, match="test error"):
            await circuit_breaker.execute(fail_func)

    @pytest.mark.asyncio
    async def test_execute_raises_when_open(self, circuit_breaker):
        """Test execute raises CircuitBreakerError when open."""
        # Open the circuit
        for _ in range(3):
            await circuit_breaker.record_failure(Exception("test"))

        async def dummy():
            return "should not run"

        with pytest.raises(CircuitBreakerError):
            await circuit_breaker.execute(dummy)

    def test_get_status(self, circuit_breaker):
        """Test status reporting."""
        status = circuit_breaker.get_status()

        assert status["name"] == "test"
        assert status["state"] == "closed"
        assert status["failure_count"] == 0


class TestRedisHAService:
    """Test Redis HA service functionality."""

    @pytest.fixture
    def redis_config(self) -> RedisConfig:
        """Create test Redis config."""
        return RedisConfig(
            redis_url="redis://localhost:6379",
            circuit_breaker_enabled=True,
            circuit_failure_threshold=3,
            circuit_timeout_seconds=1.0,
            fail_fast_on_circuit_open=False,
        )

    @pytest.fixture
    def redis_service(self, redis_config) -> RedisHAService:
        """Create Redis service with mocked client."""
        return RedisHAService(redis_config)

    @pytest.mark.asyncio
    async def test_get_returns_none_on_failure(self, redis_service):
        """Test get returns None when Redis fails (graceful degradation)."""
        with patch.object(redis_service, '_get_client') as mock_client:
            mock_redis = AsyncMock()
            mock_redis.get.side_effect = Exception("Connection refused")
            mock_client.return_value = mock_redis

            await redis_service.initialize()
            result = await redis_service.get("test_key")

            assert result is None

    @pytest.mark.asyncio
    async def test_set_returns_false_on_failure(self, redis_service):
        """Test set returns False when Redis fails."""
        with patch.object(redis_service, '_get_client') as mock_client:
            mock_redis = AsyncMock()
            mock_redis.set.side_effect = Exception("Connection refused")
            mock_client.return_value = mock_redis

            await redis_service.initialize()
            result = await redis_service.set("key", "value")

            assert result is False

    @pytest.mark.asyncio
    async def test_successful_get_set(self, redis_service):
        """Test successful get/set operations."""
        with patch.object(redis_service, '_get_client') as mock_client:
            mock_redis = AsyncMock()
            mock_redis.get.return_value = "test_value"
            mock_redis.set.return_value = True
            mock_client.return_value = mock_redis

            await redis_service.initialize()

            # Test set
            set_result = await redis_service.set("key", "value", ttl=60)
            assert set_result is True

            # Test get
            get_result = await redis_service.get("key")
            assert get_result == "test_value"

    @pytest.mark.asyncio
    async def test_circuit_breaker_opens_after_failures(self, redis_service):
        """Test circuit breaker opens after repeated failures."""
        with patch.object(redis_service, '_get_client') as mock_client:
            mock_redis = AsyncMock()
            mock_redis.get.side_effect = Exception("Connection refused")
            mock_client.return_value = mock_redis

            await redis_service.initialize()

            # Trigger failures
            for _ in range(5):
                await redis_service.get("key")

            # Check circuit is open
            status = await redis_service.get_status()
            assert status["circuit_breaker"]["state"] == "open"

    @pytest.mark.asyncio
    async def test_fail_fast_mode(self):
        """Test fail-fast mode raises error instead of returning None."""
        config = RedisConfig(
            redis_url="redis://localhost:6379",
            circuit_breaker_enabled=True,
            circuit_failure_threshold=2,
            fail_fast_on_circuit_open=True,
        )
        service = RedisHAService(config)

        with patch.object(service, '_get_client') as mock_client:
            mock_redis = AsyncMock()
            mock_redis.get.side_effect = Exception("Connection refused")
            mock_client.return_value = mock_redis

            await service.initialize()

            # Open the circuit
            for _ in range(3):
                with pytest.raises(Exception):
                    await service.get("key")

            # Verify circuit is open and raises on next call
            with pytest.raises(CircuitBreakerError):
                await service.get("key")

    @pytest.mark.asyncio
    async def test_health_check_success(self, redis_service):
        """Test health check returns True when Redis is healthy."""
        with patch.object(redis_service, '_get_client') as mock_client:
            mock_redis = AsyncMock()
            mock_redis.ping.return_value = True
            mock_client.return_value = mock_redis

            await redis_service.initialize()
            result = await redis_service.health_check()

            assert result is True

    @pytest.mark.asyncio
    async def test_health_check_failure(self, redis_service):
        """Test health check returns False when Redis is down."""
        with patch.object(redis_service, '_get_client') as mock_client:
            mock_redis = AsyncMock()
            mock_redis.ping.side_effect = Exception("Connection refused")
            mock_client.return_value = mock_redis

            await redis_service.initialize()
            result = await redis_service.health_check()

            assert result is False

    @pytest.mark.asyncio
    async def test_delete_operation(self, redis_service):
        """Test delete operation."""
        with patch.object(redis_service, '_get_client') as mock_client:
            mock_redis = AsyncMock()
            mock_redis.delete.return_value = 2
            mock_client.return_value = mock_redis

            await redis_service.initialize()
            result = await redis_service.delete("key1", "key2")

            assert result == 2

    @pytest.mark.asyncio
    async def test_incr_operation(self, redis_service):
        """Test increment operation."""
        with patch.object(redis_service, '_get_client') as mock_client:
            mock_redis = AsyncMock()
            mock_redis.incr.return_value = 5
            mock_client.return_value = mock_redis

            await redis_service.initialize()
            result = await redis_service.incr("counter")

            assert result == 5

    @pytest.mark.asyncio
    async def test_sorted_set_operations(self, redis_service):
        """Test sorted set operations."""
        with patch.object(redis_service, '_get_client') as mock_client:
            mock_redis = AsyncMock()
            mock_redis.zadd.return_value = 2
            mock_redis.zcard.return_value = 5
            mock_redis.zremrangebyscore.return_value = 3
            mock_client.return_value = mock_redis

            await redis_service.initialize()

            zadd_result = await redis_service.zadd("zset", {"a": 1.0, "b": 2.0})
            assert zadd_result == 2

            zcard_result = await redis_service.zcard("zset")
            assert zcard_result == 5

            zrem_result = await redis_service.zremrangebyscore("zset", 0, 1.5)
            assert zrem_result == 3

    @pytest.mark.asyncio
    async def test_get_status(self, redis_service):
        """Test status reporting."""
        with patch.object(redis_service, '_get_client') as mock_client:
            mock_redis = AsyncMock()
            mock_redis.info.return_value = {
                "redis_version": "7.0.0",
                "uptime_in_seconds": 3600,
                "connected_clients": 10,
            }
            mock_client.return_value = mock_redis

            await redis_service.initialize()
            status = await redis_service.get_status()

            assert "healthy" in status
            assert "circuit_breaker" in status
            assert status["initialized"] is True
            assert status["is_sentinel_mode"] is False


class TestSentinelMode:
    """Test Sentinel mode configuration."""

    @pytest.mark.asyncio
    async def test_sentinel_mode_initialization(self):
        """Test initialization with Sentinel hosts."""
        config = RedisConfig(
            sentinel_hosts=["localhost:26379", "localhost:26380", "localhost:26381"],
            sentinel_master_name="mymaster",
        )
        service = RedisHAService(config)

        with patch('app.services.redis_ha.Sentinel') as mock_sentinel_class:
            mock_sentinel = MagicMock()
            mock_sentinel_class.return_value = mock_sentinel

            await service.initialize()

            assert service._is_sentinel_mode is True
            mock_sentinel_class.assert_called_once()


class TestConfigFromSettings:
    """Test configuration creation from settings."""

    def test_create_config_from_settings_single_mode(self):
        """Test config creation in single Redis mode."""
        with patch('app.config.get_settings') as mock_get_settings:
            settings = MagicMock()
            settings.REDIS_URL = "redis://localhost:6379"
            settings.REDIS_SENTINEL_ENABLED = False
            settings.REDIS_SENTINEL_HOSTS = None
            settings.REDIS_SENTINEL_MASTER_NAME = "mymaster"
            settings.REDIS_SENTINEL_PASSWORD = None
            settings.REDIS_SOCKET_TIMEOUT = 5.0
            settings.REDIS_CONNECT_TIMEOUT = 5.0
            settings.REDIS_MAX_CONNECTIONS = 50
            settings.REDIS_CIRCUIT_BREAKER_ENABLED = True
            settings.REDIS_CIRCUIT_FAILURE_THRESHOLD = 5
            settings.REDIS_CIRCUIT_TIMEOUT_SECONDS = 30.0
            settings.REDIS_FAIL_FAST_ON_CIRCUIT_OPEN = False
            mock_get_settings.return_value = settings

            config = _create_config_from_settings()

            assert config.redis_url == "redis://localhost:6379"
            assert config.sentinel_hosts is None
            assert config.circuit_breaker_enabled is True

    def test_create_config_from_settings_sentinel_mode(self):
        """Test config creation in Sentinel mode."""
        with patch('app.config.get_settings') as mock_get_settings:
            settings = MagicMock()
            settings.REDIS_URL = "redis://localhost:6379"
            settings.REDIS_SENTINEL_ENABLED = True
            settings.REDIS_SENTINEL_HOSTS = "host1:26379,host2:26379,host3:26379"
            settings.REDIS_SENTINEL_MASTER_NAME = "mymaster"
            settings.REDIS_SENTINEL_PASSWORD = "secret"
            settings.REDIS_SOCKET_TIMEOUT = 5.0
            settings.REDIS_CONNECT_TIMEOUT = 5.0
            settings.REDIS_MAX_CONNECTIONS = 50
            settings.REDIS_CIRCUIT_BREAKER_ENABLED = True
            settings.REDIS_CIRCUIT_FAILURE_THRESHOLD = 5
            settings.REDIS_CIRCUIT_TIMEOUT_SECONDS = 30.0
            settings.REDIS_FAIL_FAST_ON_CIRCUIT_OPEN = False
            mock_get_settings.return_value = settings

            config = _create_config_from_settings()

            assert config.sentinel_hosts == ["host1:26379", "host2:26379", "host3:26379"]
            assert config.sentinel_master_name == "mymaster"
            assert config.sentinel_password == "secret"


class TestGracefulDegradation:
    """Test graceful degradation behavior."""

    @pytest.mark.asyncio
    async def test_operations_return_defaults_when_redis_down(self):
        """Test all operations return safe defaults when Redis is unavailable."""
        config = RedisConfig(
            redis_url="redis://localhost:6379",
            circuit_breaker_enabled=False,  # Disable to test direct failure handling
            fail_fast_on_circuit_open=False,
        )
        service = RedisHAService(config)

        with patch.object(service, '_get_client') as mock_client:
            mock_redis = AsyncMock()
            mock_redis.get.side_effect = Exception("Connection refused")
            mock_redis.set.side_effect = Exception("Connection refused")
            mock_redis.delete.side_effect = Exception("Connection refused")
            mock_redis.exists.side_effect = Exception("Connection refused")
            mock_redis.incr.side_effect = Exception("Connection refused")
            mock_redis.zadd.side_effect = Exception("Connection refused")
            mock_redis.zcard.side_effect = Exception("Connection refused")
            mock_redis.ttl.side_effect = Exception("Connection refused")
            mock_client.return_value = mock_redis

            await service.initialize()

            # All operations should return safe defaults
            assert await service.get("key") is None
            assert await service.set("key", "value") is False
            assert await service.delete("key") == 0
            assert await service.exists("key") == 0
            assert await service.incr("counter") == 0
            assert await service.zadd("zset", {"a": 1.0}) == 0
            assert await service.zcard("zset") == 0
            assert await service.ttl("key") == -2

    @pytest.mark.asyncio
    async def test_application_continues_without_redis(self):
        """Test that application can continue operating when Redis is completely down."""
        config = RedisConfig(
            redis_url="redis://nonexistent:6379",
            circuit_breaker_enabled=True,
            fail_fast_on_circuit_open=False,
        )
        service = RedisHAService(config)

        # Simulate complete Redis failure
        with patch('redis.asyncio.from_url') as mock_from_url:
            mock_redis = AsyncMock()
            mock_redis.get.side_effect = ConnectionError("Cannot connect")
            mock_redis.set.side_effect = ConnectionError("Cannot connect")
            mock_redis.ping.side_effect = ConnectionError("Cannot connect")
            mock_from_url.return_value = mock_redis

            await service.initialize()

            # Operations should not raise, just return defaults
            result = await service.get("any_key")
            assert result is None

            set_result = await service.set("key", "value")
            assert set_result is False

            health = await service.health_check()
            assert health is False
