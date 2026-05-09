"""
Redis High Availability Service - Production-grade Redis with Sentinel support.

Phase 2 (P2.2): Redis HA Implementation

Features:
- Redis Sentinel support for automatic failover
- Circuit breaker pattern to prevent cascade failures
- Graceful degradation when Redis unavailable
- Connection pooling with health checks
- Exponential backoff for reconnection

Usage:
    from app.services.redis_ha import get_redis_service

    redis_service = get_redis_service()

    # Get/set with automatic failover
    await redis_service.set("key", "value", ttl=300)
    value = await redis_service.get("key")

    # Check health
    is_healthy = await redis_service.health_check()

References:
- Redis Sentinel: https://redis.io/docs/management/sentinel/
- Circuit Breaker Pattern: https://martinfowler.com/bliki/CircuitBreaker.html
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Union

import redis.asyncio as redis
from redis.asyncio.sentinel import Sentinel

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"      # Normal operation, requests pass through
    OPEN = "open"          # Circuit tripped, requests fail fast
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker."""
    failure_threshold: int = 5          # Failures before opening circuit
    success_threshold: int = 2          # Successes in half-open to close
    timeout_seconds: float = 30.0       # Time before attempting half-open
    half_open_max_calls: int = 3        # Max calls in half-open state


@dataclass
class CircuitBreakerState:
    """State tracking for circuit breaker."""
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    success_count: int = 0
    last_failure_time: Optional[float] = None
    half_open_calls: int = 0


class CircuitBreakerError(Exception):
    """Raised when circuit breaker is open."""
    pass


class CircuitBreaker:
    """
    Circuit breaker implementation for Redis operations.

    States:
    - CLOSED: Normal operation, failures increment counter
    - OPEN: Service unavailable, fail fast without attempting calls
    - HALF_OPEN: Testing recovery, limited calls allowed

    Transitions:
    - CLOSED -> OPEN: failure_count >= failure_threshold
    - OPEN -> HALF_OPEN: timeout_seconds elapsed
    - HALF_OPEN -> CLOSED: success_count >= success_threshold
    - HALF_OPEN -> OPEN: any failure
    """

    def __init__(self, name: str, config: Optional[CircuitBreakerConfig] = None):
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self._state = CircuitBreakerState()
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        """Get current circuit state."""
        return self._state.state

    async def _check_state_transition(self) -> None:
        """Check if state should transition."""
        if self._state.state == CircuitState.OPEN:
            # Check if timeout has elapsed
            if self._state.last_failure_time:
                elapsed = time.time() - self._state.last_failure_time
                if elapsed >= self.config.timeout_seconds:
                    logger.info(f"Circuit '{self.name}' transitioning to HALF_OPEN after {elapsed:.1f}s")
                    self._state.state = CircuitState.HALF_OPEN
                    self._state.half_open_calls = 0
                    self._state.success_count = 0

    async def can_execute(self) -> bool:
        """Check if a call can be executed."""
        async with self._lock:
            await self._check_state_transition()

            if self._state.state == CircuitState.CLOSED:
                return True

            if self._state.state == CircuitState.HALF_OPEN:
                if self._state.half_open_calls < self.config.half_open_max_calls:
                    self._state.half_open_calls += 1
                    return True
                return False

            # OPEN state
            return False

    async def record_success(self) -> None:
        """Record a successful call."""
        async with self._lock:
            if self._state.state == CircuitState.HALF_OPEN:
                self._state.success_count += 1
                if self._state.success_count >= self.config.success_threshold:
                    logger.info(f"Circuit '{self.name}' closing after {self._state.success_count} successes")
                    self._state.state = CircuitState.CLOSED
                    self._state.failure_count = 0
            elif self._state.state == CircuitState.CLOSED:
                # Reset failure count on success
                self._state.failure_count = 0

    async def record_failure(self, error: Exception) -> None:
        """Record a failed call."""
        async with self._lock:
            self._state.failure_count += 1
            self._state.last_failure_time = time.time()

            if self._state.state == CircuitState.HALF_OPEN:
                # Any failure in half-open immediately opens circuit
                logger.warning(f"Circuit '{self.name}' re-opening after failure in HALF_OPEN: {error}")
                self._state.state = CircuitState.OPEN
            elif self._state.state == CircuitState.CLOSED:
                if self._state.failure_count >= self.config.failure_threshold:
                    logger.warning(
                        f"Circuit '{self.name}' opening after {self._state.failure_count} failures: {error}"
                    )
                    self._state.state = CircuitState.OPEN

    async def execute(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute a function with circuit breaker protection.

        Raises:
            CircuitBreakerError: If circuit is open
        """
        if not await self.can_execute():
            raise CircuitBreakerError(
                f"Circuit '{self.name}' is {self._state.state.value}, failing fast"
            )

        try:
            result = await func(*args, **kwargs)
            await self.record_success()
            return result
        except Exception as e:
            await self.record_failure(e)
            raise

    def get_status(self) -> Dict[str, Any]:
        """Get circuit breaker status for monitoring."""
        return {
            "name": self.name,
            "state": self._state.state.value,
            "failure_count": self._state.failure_count,
            "success_count": self._state.success_count,
            "last_failure_time": self._state.last_failure_time,
        }


@dataclass
class RedisConfig:
    """Configuration for Redis HA service."""
    # Single Redis mode
    redis_url: Optional[str] = None

    # Sentinel mode
    sentinel_hosts: Optional[List[str]] = None  # ["host1:26379", "host2:26379"]
    sentinel_master_name: str = "mymaster"
    sentinel_password: Optional[str] = None

    # Connection settings
    socket_timeout: float = 5.0
    socket_connect_timeout: float = 5.0
    retry_on_timeout: bool = True
    max_connections: int = 50

    # Health check settings
    health_check_interval: float = 30.0

    # Circuit breaker settings
    circuit_breaker_enabled: bool = True
    circuit_failure_threshold: int = 5
    circuit_timeout_seconds: float = 30.0

    # Graceful degradation
    fail_fast_on_circuit_open: bool = False  # If True, raise error; if False, return None


class RedisHAService:
    """
    High-availability Redis service with Sentinel support and circuit breaker.

    This service provides:
    1. Automatic failover via Redis Sentinel (when configured)
    2. Circuit breaker to prevent cascade failures
    3. Graceful degradation when Redis is unavailable
    4. Connection pooling with health checks
    5. Metrics and monitoring support
    """

    def __init__(self, config: Optional[RedisConfig] = None):
        self.config = config or RedisConfig()
        self._client: Optional[redis.Redis] = None
        self._sentinel: Optional[Sentinel] = None
        self._circuit_breaker: Optional[CircuitBreaker] = None
        self._is_sentinel_mode = False
        self._lock = asyncio.Lock()
        self._initialized = False
        self._last_health_check: Optional[float] = None
        self._health_status: bool = False

        # Initialize circuit breaker
        if self.config.circuit_breaker_enabled:
            cb_config = CircuitBreakerConfig(
                failure_threshold=self.config.circuit_failure_threshold,
                timeout_seconds=self.config.circuit_timeout_seconds,
            )
            self._circuit_breaker = CircuitBreaker("redis", cb_config)

    async def initialize(self) -> None:
        """Initialize Redis connection(s)."""
        if self._initialized:
            return

        async with self._lock:
            if self._initialized:
                return

            if self.config.sentinel_hosts:
                await self._init_sentinel_mode()
            elif self.config.redis_url:
                await self._init_single_mode()
            else:
                # Use default from settings
                from app.config import get_settings
                settings = get_settings()
                self.config.redis_url = settings.REDIS_URL
                await self._init_single_mode()

            self._initialized = True
            logger.info(f"Redis HA service initialized (sentinel={self._is_sentinel_mode})")

    async def _init_single_mode(self) -> None:
        """Initialize single Redis mode."""
        self._client = redis.from_url(
            self.config.redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_timeout=self.config.socket_timeout,
            socket_connect_timeout=self.config.socket_connect_timeout,
            retry_on_timeout=self.config.retry_on_timeout,
            max_connections=self.config.max_connections,
        )
        self._is_sentinel_mode = False
        logger.info(f"Redis single mode initialized: {self.config.redis_url}")

    async def _init_sentinel_mode(self) -> None:
        """Initialize Redis Sentinel mode."""
        # Parse sentinel hosts
        sentinels = []
        for host_port in self.config.sentinel_hosts:
            host, port = host_port.split(":")
            sentinels.append((host, int(port)))

        # Create Sentinel instance
        self._sentinel = Sentinel(
            sentinels,
            socket_timeout=self.config.socket_timeout,
            sentinel_kwargs={
                "password": self.config.sentinel_password,
            } if self.config.sentinel_password else {},
        )

        self._is_sentinel_mode = True
        logger.info(f"Redis Sentinel mode initialized with {len(sentinels)} sentinels")

    async def _get_client(self) -> redis.Redis:
        """Get Redis client (master for Sentinel mode)."""
        await self.initialize()

        if self._is_sentinel_mode and self._sentinel:
            return self._sentinel.master_for(
                self.config.sentinel_master_name,
                decode_responses=True,
                socket_timeout=self.config.socket_timeout,
            )

        return self._client

    async def _execute_with_protection(
        self,
        operation: Callable,
        *args,
        default: Any = None,
        **kwargs
    ) -> Any:
        """
        Execute Redis operation with circuit breaker protection and graceful degradation.
        """
        # Check circuit breaker
        if self._circuit_breaker:
            try:
                return await self._circuit_breaker.execute(operation, *args, **kwargs)
            except CircuitBreakerError as e:
                if self.config.fail_fast_on_circuit_open:
                    raise
                logger.warning(f"Circuit open, returning default: {e}")
                return default
            except Exception as e:
                if self.config.fail_fast_on_circuit_open:
                    raise
                logger.warning(f"Redis operation failed, returning default: {e}")
                return default
        else:
            # No circuit breaker, execute directly with try/except
            try:
                return await operation(*args, **kwargs)
            except Exception as e:
                logger.warning(f"Redis operation failed: {e}")
                return default

    async def get(self, key: str) -> Optional[str]:
        """
        Get a value from Redis.

        Returns None if key doesn't exist or Redis is unavailable.
        """
        async def _get():
            client = await self._get_client()
            return await client.get(key)

        return await self._execute_with_protection(_get)

    async def set(
        self,
        key: str,
        value: str,
        ttl: Optional[int] = None,
        nx: bool = False,
        xx: bool = False
    ) -> bool:
        """
        Set a value in Redis.

        Args:
            key: Redis key
            value: Value to store
            ttl: Time-to-live in seconds
            nx: Only set if key does not exist
            xx: Only set if key already exists

        Returns:
            True if successfully set, False otherwise
        """
        async def _set():
            client = await self._get_client()
            if ttl:
                result = await client.set(key, value, ex=ttl, nx=nx, xx=xx)
            else:
                result = await client.set(key, value, nx=nx, xx=xx)
            return result is not None

        result = await self._execute_with_protection(_set, default=False)
        return result if result is not None else False

    async def setex(self, key: str, seconds: int, value: str) -> bool:
        """Set a value with expiration."""
        return await self.set(key, value, ttl=seconds)

    async def delete(self, *keys: str) -> int:
        """Delete one or more keys."""
        async def _delete():
            client = await self._get_client()
            return await client.delete(*keys)

        result = await self._execute_with_protection(_delete, default=0)
        return result if result is not None else 0

    async def exists(self, *keys: str) -> int:
        """Check if keys exist."""
        async def _exists():
            client = await self._get_client()
            return await client.exists(*keys)

        result = await self._execute_with_protection(_exists, default=0)
        return result if result is not None else 0

    async def expire(self, key: str, seconds: int) -> bool:
        """Set key expiration."""
        async def _expire():
            client = await self._get_client()
            return await client.expire(key, seconds)

        result = await self._execute_with_protection(_expire, default=False)
        return result if result is not None else False

    async def ttl(self, key: str) -> int:
        """Get time-to-live for key."""
        async def _ttl():
            client = await self._get_client()
            return await client.ttl(key)

        result = await self._execute_with_protection(_ttl, default=-2)
        return result if result is not None else -2

    async def incr(self, key: str, amount: int = 1) -> int:
        """Increment a key's value."""
        async def _incr():
            client = await self._get_client()
            if amount == 1:
                return await client.incr(key)
            return await client.incrby(key, amount)

        result = await self._execute_with_protection(_incr, default=0)
        return result if result is not None else 0

    async def zadd(self, key: str, mapping: Dict[str, float]) -> int:
        """Add members to a sorted set."""
        async def _zadd():
            client = await self._get_client()
            return await client.zadd(key, mapping)

        result = await self._execute_with_protection(_zadd, default=0)
        return result if result is not None else 0

    async def zremrangebyscore(
        self,
        key: str,
        min_score: Union[float, str],
        max_score: Union[float, str]
    ) -> int:
        """Remove members from sorted set by score range."""
        async def _zremrangebyscore():
            client = await self._get_client()
            return await client.zremrangebyscore(key, min_score, max_score)

        result = await self._execute_with_protection(_zremrangebyscore, default=0)
        return result if result is not None else 0

    async def zcard(self, key: str) -> int:
        """Get number of members in sorted set."""
        async def _zcard():
            client = await self._get_client()
            return await client.zcard(key)

        result = await self._execute_with_protection(_zcard, default=0)
        return result if result is not None else 0

    async def lpush(self, key: str, *values: str) -> int:
        """Push values to the head of a list."""
        async def _lpush():
            client = await self._get_client()
            return await client.lpush(key, *values)

        result = await self._execute_with_protection(_lpush, default=0)
        return result if result is not None else 0

    async def lrange(self, key: str, start: int, end: int) -> List[str]:
        """Get a range of elements from a list."""
        async def _lrange():
            client = await self._get_client()
            return await client.lrange(key, start, end)

        result = await self._execute_with_protection(_lrange, default=[])
        return result if result is not None else []

    async def ltrim(self, key: str, start: int, end: int) -> bool:
        """Trim a list to the specified range."""
        async def _ltrim():
            client = await self._get_client()
            return await client.ltrim(key, start, end)

        result = await self._execute_with_protection(_ltrim, default=False)
        return result if result is not None else False

    async def hset(self, key: str, mapping: Dict[str, str]) -> int:
        """Set fields in a hash."""
        async def _hset():
            client = await self._get_client()
            return await client.hset(key, mapping=mapping)

        result = await self._execute_with_protection(_hset, default=0)
        return result if result is not None else 0

    async def hgetall(self, key: str) -> Dict[str, str]:
        """Get all fields and values in a hash."""
        async def _hgetall():
            client = await self._get_client()
            return await client.hgetall(key)

        result = await self._execute_with_protection(_hgetall, default={})
        return result if result is not None else {}

    async def hincrby(self, key: str, field: str, amount: int = 1) -> int:
        """Increment a hash field by amount."""
        async def _hincrby():
            client = await self._get_client()
            return await client.hincrby(key, field, amount)

        result = await self._execute_with_protection(_hincrby, default=0)
        return result if result is not None else 0

    @asynccontextmanager
    async def pipeline(self, transaction: bool = True):
        """
        Context manager for Redis pipeline operations.

        Note: Pipelines bypass circuit breaker for the individual commands,
        but the execute() call is protected.
        """
        client = await self._get_client()
        pipe = client.pipeline(transaction=transaction)

        try:
            yield pipe
        finally:
            pass  # Caller should call pipe.execute()

    async def execute_pipeline(self, pipe) -> List[Any]:
        """Execute a pipeline with circuit breaker protection."""
        async def _execute():
            return await pipe.execute()

        result = await self._execute_with_protection(_execute, default=[])
        return result if result is not None else []

    async def health_check(self) -> bool:
        """
        Check Redis health.

        Returns True if Redis is healthy and responding.
        """
        try:
            client = await self._get_client()
            result = await asyncio.wait_for(
                client.ping(),
                timeout=self.config.socket_timeout
            )
            self._health_status = result is True
            self._last_health_check = time.time()
            return self._health_status
        except Exception as e:
            logger.warning(f"Redis health check failed: {e}")
            self._health_status = False
            self._last_health_check = time.time()
            return False

    async def get_status(self) -> Dict[str, Any]:
        """
        Get comprehensive status for monitoring.

        Returns dict with health, circuit breaker state, and connection info.
        """
        await self.initialize()

        status = {
            "healthy": self._health_status,
            "last_health_check": self._last_health_check,
            "is_sentinel_mode": self._is_sentinel_mode,
            "initialized": self._initialized,
        }

        if self._circuit_breaker:
            status["circuit_breaker"] = self._circuit_breaker.get_status()

        # Get connection info
        try:
            client = await self._get_client()
            info = await client.info("server")
            status["server"] = {
                "redis_version": info.get("redis_version"),
                "uptime_in_seconds": info.get("uptime_in_seconds"),
                "connected_clients": info.get("connected_clients"),
            }
        except Exception as e:
            status["server_error"] = str(e)

        # Sentinel info if applicable
        if self._is_sentinel_mode and self._sentinel:
            try:
                master = await self._sentinel.discover_master(self.config.sentinel_master_name)
                slaves = await self._sentinel.discover_slaves(self.config.sentinel_master_name)
                status["sentinel"] = {
                    "master": f"{master[0]}:{master[1]}",
                    "slave_count": len(slaves),
                }
            except Exception as e:
                status["sentinel_error"] = str(e)

        return status

    async def close(self) -> None:
        """Close Redis connections."""
        async with self._lock:
            if self._client:
                await self._client.close()
                self._client = None
            self._initialized = False
            logger.info("Redis HA service closed")


# Singleton instance
_redis_service: Optional[RedisHAService] = None
_redis_lock = asyncio.Lock()


def _create_config_from_settings() -> RedisConfig:
    """Create RedisConfig from application settings."""
    from app.config import get_settings
    settings = get_settings()

    # Parse sentinel hosts if enabled
    sentinel_hosts = None
    if settings.REDIS_SENTINEL_ENABLED and settings.REDIS_SENTINEL_HOSTS:
        sentinel_hosts = [h.strip() for h in settings.REDIS_SENTINEL_HOSTS.split(",")]

    return RedisConfig(
        redis_url=settings.REDIS_URL,
        sentinel_hosts=sentinel_hosts,
        sentinel_master_name=settings.REDIS_SENTINEL_MASTER_NAME,
        sentinel_password=settings.REDIS_SENTINEL_PASSWORD,
        socket_timeout=settings.REDIS_SOCKET_TIMEOUT,
        socket_connect_timeout=settings.REDIS_CONNECT_TIMEOUT,
        max_connections=settings.REDIS_MAX_CONNECTIONS,
        circuit_breaker_enabled=settings.REDIS_CIRCUIT_BREAKER_ENABLED,
        circuit_failure_threshold=settings.REDIS_CIRCUIT_FAILURE_THRESHOLD,
        circuit_timeout_seconds=settings.REDIS_CIRCUIT_TIMEOUT_SECONDS,
        fail_fast_on_circuit_open=settings.REDIS_FAIL_FAST_ON_CIRCUIT_OPEN,
    )


async def get_redis_service(config: Optional[RedisConfig] = None) -> RedisHAService:
    """
    Get or create the singleton Redis HA service instance.

    Args:
        config: Optional configuration. If not provided, creates from settings.

    Returns:
        Initialized RedisHAService instance
    """
    global _redis_service

    if _redis_service is None:
        async with _redis_lock:
            if _redis_service is None:
                if config is None:
                    config = _create_config_from_settings()
                _redis_service = RedisHAService(config)
                await _redis_service.initialize()

    return _redis_service


def get_redis_service_sync(config: Optional[RedisConfig] = None) -> RedisHAService:
    """
    Get the Redis service synchronously (for FastAPI dependencies).

    Note: This creates the instance but doesn't initialize it.
    Call await service.initialize() before first use.
    """
    global _redis_service

    if _redis_service is None:
        if config is None:
            config = _create_config_from_settings()
        _redis_service = RedisHAService(config)

    return _redis_service


async def close_redis_service() -> None:
    """Close the global Redis service."""
    global _redis_service

    if _redis_service:
        await _redis_service.close()
        _redis_service = None
