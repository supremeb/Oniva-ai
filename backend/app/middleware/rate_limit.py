"""
Rate Limiting Middleware - Redis-based rate limiting for API protection

Uses sliding window algorithm for accurate rate limiting.
Supports per-IP and per-user limits with 2-tier rate limiting
and concurrent SSE stream guards.

Tiers:
  - Expensive (Tier 1): 20 req/min — /chat, /sandbox, /agents/custom POST
  - Standard  (Tier 2): 100 req/min — everything else
"""

import time
import logging
from typing import Optional, Callable
from fastapi import Request, Response, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import StreamingResponse
import redis.asyncio as redis

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# ---------------------------------------------------------------------------
# Tier configuration
# ---------------------------------------------------------------------------
EXPENSIVE_RATE_LIMIT = 20   # requests per minute
STANDARD_RATE_LIMIT = 100   # requests per minute
MAX_CONCURRENT_SSE_STREAMS = 5

# Path fragments that qualify a request as "expensive" (Tier 1)
EXPENSIVE_PATH_FRAGMENTS = ("/chat", "/sandbox")
# Exact path prefix + method check for agent generation
AGENT_CUSTOM_PATH_PREFIX = "/api/v1/agents/custom"


def _is_expensive_request(method: str, path: str) -> bool:
    """Determine whether a request falls into the expensive tier."""
    # Any request whose path contains /chat or /sandbox
    for fragment in EXPENSIVE_PATH_FRAGMENTS:
        if fragment in path:
            return True
    # POST to /agents/custom (agent generation)
    if method == "POST" and path.startswith(AGENT_CUSTOM_PATH_PREFIX):
        return True
    return False


def _is_sse_stream_request(method: str, path: str) -> bool:
    """Return True for requests that open a long-lived SSE stream."""
    # POST to any path containing /chat that also contains /messages
    # (the main chat message endpoints that stream responses)
    # Also match the workflow AI chat endpoint
    if method != "POST":
        return False
    if "/chat" in path:
        return True
    return False


class RateLimiter:
    """
    Redis-based rate limiter using sliding window algorithm.
    Supports tiered rate limits and concurrent SSE stream tracking.
    """

    RATE_LIMIT_PREFIX = "ratelimit"
    SSE_STREAM_PREFIX = "sse_streams"

    def __init__(
        self,
        redis_url: Optional[str] = None,
        requests_per_minute: int = 100,
        burst_size: int = 20,
        fail_open: bool = False
    ):
        self.redis_url = redis_url or settings.REDIS_URL
        self.requests_per_minute = requests_per_minute
        self.burst_size = burst_size
        self.window_size = 60  # 1 minute in seconds
        self.fail_open = fail_open  # Allow requests if Redis fails
        self._client: Optional[redis.Redis] = None

    async def _get_client(self) -> redis.Redis:
        """Get or create Redis client"""
        if self._client is None:
            self._client = redis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True
            )
        return self._client

    def _get_key(self, identifier: str) -> str:
        """Generate rate limit key for identifier"""
        return f"{self.RATE_LIMIT_PREFIX}:{identifier}"

    async def is_allowed(
        self,
        identifier: str,
        limit_override: Optional[int] = None,
    ) -> tuple[bool, dict]:
        """
        Check if request is allowed under rate limit.

        Args:
            identifier: Unique key (includes tier prefix).
            limit_override: Per-request override of requests_per_minute.

        Returns:
            Tuple of (is_allowed, rate_limit_info)
        """
        effective_limit = limit_override if limit_override is not None else self.requests_per_minute

        try:
            client = await self._get_client()
            key = self._get_key(identifier)
            now = time.time()
            window_start = now - self.window_size

            # Use pipeline for atomic operations
            pipe = client.pipeline()

            # Remove old entries outside window
            pipe.zremrangebyscore(key, 0, window_start)

            # Count requests in current window
            pipe.zcard(key)

            # Add current request
            pipe.zadd(key, {str(now): now})

            # Set TTL
            pipe.expire(key, self.window_size + 1)

            results = await pipe.execute()
            request_count = results[1]

            is_allowed = request_count < effective_limit

            rate_info = {
                "limit": effective_limit,
                "remaining": max(0, effective_limit - request_count - 1),
                "reset": int(now + self.window_size),
            }

            if not is_allowed:
                logger.warning(f"Rate limit exceeded for {identifier}: {request_count} requests (limit={effective_limit})")

            return is_allowed, rate_info

        except Exception as e:
            logger.error(f"Rate limiter error: {e}")
            # SECURITY: Only fail open if explicitly configured (dev mode)
            if self.fail_open:
                logger.warning("Rate limiter failing open due to Redis error")
                return True, {"limit": effective_limit, "remaining": -1, "reset": 0}
            else:
                # Fail closed in production - reject request if Redis unavailable
                logger.error("Rate limiter failing closed - rejecting request")
                return False, {"limit": effective_limit, "remaining": 0, "reset": 0}

    # ------------------------------------------------------------------
    # Concurrent SSE stream tracking
    # ------------------------------------------------------------------

    async def acquire_sse_slot(self, user_id: str) -> bool:
        """
        Try to acquire an SSE stream slot for the user.
        Returns True if the slot was acquired (under limit), False otherwise.
        """
        try:
            client = await self._get_client()
            key = f"{self.SSE_STREAM_PREFIX}:{user_id}"

            # INCR is atomic; check if we exceeded the limit *after* incrementing
            count = await client.incr(key)

            # Set a safety TTL so keys don't leak if decrement is missed
            # (e.g., process crash). 30 minutes is generous for any stream.
            if count == 1:
                await client.expire(key, 1800)

            if count > MAX_CONCURRENT_SSE_STREAMS:
                # Over limit — roll back the increment
                await client.decr(key)
                logger.warning(
                    f"SSE stream limit exceeded for user {user_id}: "
                    f"{count} > {MAX_CONCURRENT_SSE_STREAMS}"
                )
                return False

            logger.debug(f"SSE stream acquired for user {user_id}: {count}/{MAX_CONCURRENT_SSE_STREAMS}")
            return True

        except Exception as e:
            logger.error(f"SSE stream acquire error: {e}")
            if self.fail_open:
                return True
            return False

    async def release_sse_slot(self, user_id: str) -> None:
        """Release an SSE stream slot for the user."""
        try:
            client = await self._get_client()
            key = f"{self.SSE_STREAM_PREFIX}:{user_id}"

            count = await client.decr(key)
            # Guard against going negative (e.g., stale keys after restart)
            if count < 0:
                await client.set(key, 0)

            logger.debug(f"SSE stream released for user {user_id}: now {max(count, 0)}/{MAX_CONCURRENT_SSE_STREAMS}")

        except Exception as e:
            logger.error(f"SSE stream release error: {e}")

    async def close(self):
        """Close Redis connection"""
        if self._client:
            await self._client.close()
            self._client = None


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    FastAPI middleware for 2-tier rate limiting with SSE stream guards.

    Tier 1 (expensive): 20 req/min — /chat, /sandbox, /agents/custom POST
    Tier 2 (standard):  100 req/min — everything else

    SSE guard: max 5 concurrent SSE streams per user, tracked in Redis.

    SECURITY: Rate limiting is always enabled. Configure fail_open=True
    only in development to allow requests when Redis is unavailable.
    In production, fail_open=False ensures requests are rejected if
    Redis fails (fail-closed for security).
    """

    # Paths to exclude from rate limiting
    EXCLUDED_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}

    def __init__(
        self,
        app,
        requests_per_minute: int = 100,
        enabled: bool = True,
        fail_open: bool = False
    ):
        super().__init__(app)
        self.limiter = RateLimiter(
            requests_per_minute=requests_per_minute,
            fail_open=fail_open
        )
        self.enabled = enabled

    def _get_identifier(self, request: Request) -> str:
        """Get unique identifier for rate limiting (IP or user ID)"""
        # Check for user ID in state (set by auth middleware)
        user_id = getattr(request.state, "user_id", None)
        if user_id:
            return f"user:{user_id}"

        # Fall back to IP address
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            ip = forwarded.split(",")[0].strip()
        else:
            ip = request.client.host if request.client else "unknown"

        return f"ip:{ip}"

    def _get_raw_user_id(self, request: Request) -> Optional[str]:
        """Get the raw user ID (without prefix) for SSE tracking."""
        return getattr(request.state, "user_id", None)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process request with 2-tier rate limiting and SSE stream guard"""

        # Skip if disabled or excluded path
        if not self.enabled or request.url.path in self.EXCLUDED_PATHS:
            return await call_next(request)

        method = request.method
        path = request.url.path
        identifier = self._get_identifier(request)
        is_expensive = _is_expensive_request(method, path)
        is_sse = _is_sse_stream_request(method, path)

        # --- Tier-based rate limiting ---
        if is_expensive:
            tier_key = f"expensive:{identifier}"
            tier_limit = EXPENSIVE_RATE_LIMIT
        else:
            tier_key = f"standard:{identifier}"
            tier_limit = STANDARD_RATE_LIMIT

        is_allowed, rate_info = await self.limiter.is_allowed(
            tier_key, limit_override=tier_limit
        )

        if not is_allowed:
            retry_after = max(1, rate_info["reset"] - int(time.time()))
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded. Please slow down.",
                headers={
                    "X-RateLimit-Limit": str(rate_info["limit"]),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(rate_info["reset"]),
                    "Retry-After": str(retry_after),
                }
            )

        # --- Concurrent SSE stream guard ---
        sse_acquired = False
        raw_user_id = self._get_raw_user_id(request)

        if is_sse and raw_user_id:
            sse_acquired = await self.limiter.acquire_sse_slot(raw_user_id)
            if not sse_acquired:
                raise HTTPException(
                    status_code=429,
                    detail="Too many active streams. Please close an existing stream before opening a new one.",
                    headers={
                        "X-RateLimit-Limit": str(MAX_CONCURRENT_SSE_STREAMS),
                        "X-RateLimit-Remaining": "0",
                        "Retry-After": "5",
                    }
                )

        try:
            response = await call_next(request)
        except Exception:
            # If the downstream handler raised, release the SSE slot
            if sse_acquired and raw_user_id:
                await self.limiter.release_sse_slot(raw_user_id)
            raise

        # Add rate limit headers to response
        response.headers["X-RateLimit-Limit"] = str(rate_info["limit"])
        response.headers["X-RateLimit-Remaining"] = str(rate_info["remaining"])
        response.headers["X-RateLimit-Reset"] = str(rate_info["reset"])

        # Wrap streaming responses so we release the SSE slot when done
        if sse_acquired and raw_user_id and isinstance(response, StreamingResponse):
            original_body_iterator = response.body_iterator

            async def _tracked_stream():
                try:
                    async for chunk in original_body_iterator:
                        yield chunk
                finally:
                    await self.limiter.release_sse_slot(raw_user_id)

            response.body_iterator = _tracked_stream()
        elif sse_acquired and raw_user_id:
            # Non-streaming response on an SSE path (unlikely but safe)
            await self.limiter.release_sse_slot(raw_user_id)

        return response


# Singleton rate limiter for direct use
_rate_limiter: Optional[RateLimiter] = None


def get_rate_limiter() -> RateLimiter:
    """Get or create singleton rate limiter"""
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter()
    return _rate_limiter
