"""Sandbox session tokens for LLM proxy authentication.

Generates opaque, short-lived tokens stored in Redis hashes.
Sandboxes receive a token instead of real LLM API keys.
All LLM calls from sandboxes route through the proxy endpoint
which validates the token and forwards via LLMGateway.

Convention: module-level async functions (matches _redis_get_sandbox
pattern in sandbox_executor.py).
"""

import secrets
import time
from dataclasses import dataclass

from app.services.redis_ha import get_redis_service

_TOKEN_PREFIX = "sbx_tok_"
_REDIS_KEY_PREFIX = "sandbox_token:"
_MICRO = 1_000_000  # cost is stored as integer micro-USD (atomic HINCRBY)


@dataclass(frozen=True, slots=True)
class SandboxTokenMetadata:
    """Typed metadata stored in Redis hash for each sandbox token."""

    tenant_id: str
    user_id: str
    session_id: str
    worker_id: str
    created_at: float
    tokens_used: int
    requests_count: int
    cost_usd: float = 0.0  # real, usage-based spend reported by the gateway

    @classmethod
    def from_redis(cls, data: dict[str, str]) -> "SandboxTokenMetadata":
        return cls(
            tenant_id=data["tenant_id"],
            user_id=data["user_id"],
            session_id=data["session_id"],
            worker_id=data["worker_id"],
            created_at=float(data.get("created_at", 0)),
            tokens_used=int(data.get("tokens_used", 0)),
            requests_count=int(data.get("requests_count", 0)),
            cost_usd=int(data.get("cost_micro_usd", 0)) / _MICRO,
        )


async def create_sandbox_token(
    tenant_id: str,
    user_id: str,
    session_id: str,
    worker_id: str,
    ttl_seconds: int = 3600,
) -> str:
    """Generate opaque token, store in Redis HSET with TTL."""
    redis = await get_redis_service()
    token = _TOKEN_PREFIX + secrets.token_urlsafe(32)
    redis_key = f"{_REDIS_KEY_PREFIX}{token}"

    async with redis.pipeline() as pipe:
        pipe.hset(redis_key, mapping={
            "tenant_id": tenant_id,
            "user_id": user_id,
            "session_id": session_id,
            "worker_id": worker_id,
            "created_at": str(time.time()),
            "tokens_used": "0",
            "requests_count": "0",
            "cost_micro_usd": "0",
        })
        pipe.expire(redis_key, ttl_seconds)
        await redis.execute_pipeline(pipe)

    return token


async def validate_sandbox_token(token: str) -> SandboxTokenMetadata | None:
    """Validate token, return typed metadata or None."""
    redis = await get_redis_service()
    data = await redis.hgetall(f"{_REDIS_KEY_PREFIX}{token}")
    if not data:
        return None
    return SandboxTokenMetadata.from_redis(data)


async def extend_sandbox_token_ttl(token: str, ttl_seconds: int) -> bool:
    """Extend token TTL (called alongside sandbox extend_timeout)."""
    redis = await get_redis_service()
    return await redis.expire(f"{_REDIS_KEY_PREFIX}{token}", ttl_seconds)


async def revoke_sandbox_token(token: str) -> None:
    """Immediately revoke a single token."""
    redis = await get_redis_service()
    await redis.delete(f"{_REDIS_KEY_PREFIX}{token}")


async def increment_sandbox_token_usage(
    token: str, tokens_used: int, cost_usd: float = 0.0,
) -> int:
    """Atomically increment token + real-cost counters. Returns new token total."""
    redis = await get_redis_service()
    redis_key = f"{_REDIS_KEY_PREFIX}{token}"
    # Also increment request count
    await redis.hincrby(redis_key, "requests_count", 1)
    if cost_usd > 0:
        await redis.hincrby(redis_key, "cost_micro_usd", max(1, round(cost_usd * _MICRO)))
    return await redis.hincrby(redis_key, "tokens_used", tokens_used)
