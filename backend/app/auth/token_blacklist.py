"""JWT Token Blacklist Service.

SECURITY: Provides Redis-based token revocation for immediate logout support.
Tokens are blacklisted until their natural expiration time.

Usage:
    from app.auth.token_blacklist import token_blacklist

    # On logout
    await token_blacklist.blacklist(jti, exp_timestamp)

    # During authentication
    if await token_blacklist.is_blacklisted(jti):
        raise HTTPException(401, "Token has been revoked")
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import redis.asyncio as redis

from app.config import get_settings

logger = logging.getLogger(__name__)


class TokenBlacklist:
    """Redis-based JWT token blacklist for immediate revocation."""

    BLACKLIST_PREFIX = "token:blacklist:"

    def __init__(self):
        self.settings = get_settings()
        self._client: Optional[redis.Redis] = None

    async def _get_client(self) -> redis.Redis:
        """Get or create Redis client."""
        if self._client is None:
            self._client = redis.from_url(
                self.settings.REDIS_URL,
                encoding="utf-8",
                decode_responses=True
            )
        return self._client

    async def blacklist(self, jti: str, exp: int) -> bool:
        """
        Add token to blacklist until its natural expiration.

        Args:
            jti: JWT token ID (unique identifier)
            exp: Token expiration timestamp (Unix timestamp)

        Returns:
            True if successfully blacklisted, False on error
        """
        try:
            client = await self._get_client()

            # Calculate TTL (time until token expires naturally)
            now = int(datetime.now(timezone.utc).timestamp())
            ttl = exp - now

            if ttl <= 0:
                # Token already expired, no need to blacklist
                return True

            # Store in Redis with expiration
            key = f"{self.BLACKLIST_PREFIX}{jti}"
            await client.setex(key, ttl, "1")

            logger.info(f"Token {jti[:8]}... blacklisted for {ttl}s")
            return True

        except Exception as e:
            logger.error(f"Failed to blacklist token: {e}")
            return False

    async def is_blacklisted(self, jti: str) -> bool:
        """
        Check if token is blacklisted.

        Args:
            jti: JWT token ID to check

        Returns:
            True if blacklisted, False otherwise
        """
        if not jti:
            return False

        try:
            client = await self._get_client()
            key = f"{self.BLACKLIST_PREFIX}{jti}"
            exists = await client.exists(key)
            return exists > 0

        except Exception as e:
            logger.error(f"Failed to check blacklist: {e}")
            # SECURITY: Fail open only if Redis is completely unavailable
            # In production, consider failing closed instead
            return False

    async def invalidate_all_user_tokens(self, user_id: str) -> bool:
        """
        Invalidate all access tokens for a user by setting a timestamp.

        Any token with iat < this timestamp will be rejected by the auth middleware.
        Uses O(1) Redis lookup per request — no user→JTI set mappings needed.

        Used for:
        - Account disable
        - "Logout everywhere"
        - Admin-initiated revocation

        Args:
            user_id: User ID to invalidate all tokens for

        Returns:
            True if successfully set, False on error
        """
        try:
            client = await self._get_client()
            now = int(datetime.now(timezone.utc).timestamp())
            key = f"user:{user_id}:auth_invalidated_at"
            # TTL matches access token lifetime — after that, all old tokens
            # have expired naturally and this entry is no longer needed
            ttl = self.settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
            await client.setex(key, ttl, str(now))
            logger.info(f"All tokens invalidated for user {str(user_id)[:8]}...")
            return True
        except Exception as e:
            logger.error(f"Failed to invalidate user tokens: {e}")
            return False

    async def is_user_invalidated(self, user_id: str, token_iat: int) -> bool:
        """
        Check if a token was issued before the user's invalidation timestamp.

        Args:
            user_id: User ID to check
            token_iat: Token's issued-at timestamp (Unix)

        Returns:
            True if the token should be rejected, False otherwise
        """
        try:
            client = await self._get_client()
            key = f"user:{user_id}:auth_invalidated_at"
            invalidated_at = await client.get(key)
            if invalidated_at is None:
                return False
            return token_iat < int(invalidated_at)
        except Exception as e:
            logger.error(f"Failed to check user invalidation: {e}")
            # Fail open (pre-existing behavior — separate hardening task)
            return False

    async def close(self):
        """Close Redis connection."""
        if self._client:
            await self._client.close()
            self._client = None


# Singleton instance
token_blacklist = TokenBlacklist()
