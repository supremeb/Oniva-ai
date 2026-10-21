"""Per-account login lockout.

Counts consecutive failed logins per email in Redis. After ``MAX_FAILURES`` the account is locked
for ``LOCKOUT_SECONDS``; login attempts are refused during the lock, even with the right password.

The counter is keyed on the (hashed) email whether or not the account exists, so the response
never reveals which emails are registered. Redis failures fail open (the global IP rate limiter
still applies) so an outage cannot lock everyone out.
"""
import hashlib
import logging
from typing import Optional

import redis.asyncio as redis

from app.config import get_settings

logger = logging.getLogger(__name__)

MAX_FAILURES = 10
LOCKOUT_SECONDS = 15 * 60


class LoginLockout:
    PREFIX = "auth:login_failures:"

    def __init__(self, client=None, max_failures: int = MAX_FAILURES, lockout_seconds: int = LOCKOUT_SECONDS):
        self._client = client
        self.max_failures = max_failures
        self.lockout_seconds = lockout_seconds

    async def _get_client(self):
        if self._client is None:
            self._client = redis.from_url(
                get_settings().REDIS_URL, encoding="utf-8", decode_responses=True
            )
        return self._client

    def _key(self, email: str) -> str:
        digest = hashlib.sha256(email.strip().lower().encode()).hexdigest()
        return f"{self.PREFIX}{digest}"

    async def retry_after(self, email: str) -> Optional[int]:
        """Seconds until the lock expires, or None when the account is not locked."""
        try:
            client = await self._get_client()
            key = self._key(email)
            count = await client.get(key)
            if count is not None and int(count) >= self.max_failures:
                ttl = await client.ttl(key)
                return ttl if ttl and ttl > 0 else self.lockout_seconds
        except Exception as e:  # fail open
            logger.warning("login lockout check failed: %s", e)
        return None

    async def record_failure(self, email: str) -> None:
        try:
            client = await self._get_client()
            key = self._key(email)
            count = await client.incr(key)
            # (Re)start the window on the failure that trips the lock; otherwise keep a sliding window.
            if count == 1 or count == self.max_failures:
                await client.expire(key, self.lockout_seconds)
        except Exception as e:
            logger.warning("login lockout record failed: %s", e)

    async def reset(self, email: str) -> None:
        try:
            client = await self._get_client()
            await client.delete(self._key(email))
        except Exception as e:
            logger.warning("login lockout reset failed: %s", e)


login_lockout = LoginLockout()
