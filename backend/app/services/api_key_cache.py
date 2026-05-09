"""
API Key Cache Service - Redis-based caching for user API keys

Reduces database queries for repeated LLM calls by caching API keys
with a short TTL for security.
"""

import logging
from typing import Optional
import redis.asyncio as redis
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class APIKeyCache:
    """
    Redis-based cache for user API keys.
    
    Cache key format: "apikey:{user_id}:{provider}"
    TTL: 5 minutes (short for security, long enough for workflow execution)
    """
    
    CACHE_PREFIX = "apikey"
    DEFAULT_TTL = 300  # 5 minutes in seconds
    
    def __init__(self, redis_url: Optional[str] = None):
        self.redis_url = redis_url or settings.REDIS_URL
        self._client: Optional[redis.Redis] = None
        
    async def _get_client(self) -> redis.Redis:
        """Get or create Redis client connection"""
        if self._client is None:
            self._client = redis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True
            )
        return self._client
    
    def _cache_key(self, user_id: str, provider: str) -> str:
        """Generate cache key for user+provider combination"""
        return f"{self.CACHE_PREFIX}:{user_id}:{provider}"
    
    async def get(self, user_id: str, provider: str) -> Optional[str]:
        """
        Get cached API key for user+provider.
        
        Args:
            user_id: The user's ID
            provider: The API provider (e.g., 'openai', 'anthropic')
            
        Returns:
            The API key if cached, None otherwise
        """
        try:
            client = await self._get_client()
            key = self._cache_key(user_id, provider)
            cached = await client.get(key)
            
            if cached:
                logger.debug(f"API key cache hit: user={user_id}, provider={provider}")
                return cached
            
            logger.debug(f"API key cache miss: user={user_id}, provider={provider}")
            return None
            
        except Exception as e:
            logger.warning(f"Redis API key cache get error: {e}")
            return None
    
    async def set(
        self, 
        user_id: str,
        provider: str,
        api_key: str,
        ttl: int = DEFAULT_TTL
    ) -> bool:
        """
        Cache an API key for user+provider.
        
        Args:
            user_id: The user's ID
            provider: The API provider
            api_key: The API key to cache
            ttl: Time-to-live in seconds (default 5 min)
            
        Returns:
            True if successfully cached, False otherwise
        """
        try:
            client = await self._get_client()
            key = self._cache_key(user_id, provider)
            
            await client.setex(key, ttl, api_key)
            logger.debug(f"API key cached: user={user_id}, provider={provider}")
            return True
            
        except Exception as e:
            logger.warning(f"Redis API key cache set error: {e}")
            return False
    
    async def invalidate(self, user_id: str, provider: Optional[str] = None) -> int:
        """
        Invalidate cached API key(s) for a user.
        
        Args:
            user_id: The user's ID
            provider: Optional specific provider to invalidate. If None, invalidates all.
            
        Returns:
            Number of keys deleted
        """
        try:
            client = await self._get_client()
            
            if provider:
                key = self._cache_key(user_id, provider)
                return await client.delete(key)
            else:
                # Delete all keys for user
                pattern = f"{self.CACHE_PREFIX}:{user_id}:*"
                keys = []
                async for key in client.scan_iter(match=pattern):
                    keys.append(key)
                
                if keys:
                    return await client.delete(*keys)
                return 0
                
        except Exception as e:
            logger.warning(f"Redis API key cache invalidate error: {e}")
            return 0
    
    async def close(self):
        """Close Redis connection"""
        if self._client:
            await self._client.close()
            self._client = None


# Singleton instance
_api_key_cache: Optional[APIKeyCache] = None


def get_api_key_cache() -> APIKeyCache:
    """Get or create the singleton API key cache instance"""
    global _api_key_cache
    if _api_key_cache is None:
        _api_key_cache = APIKeyCache()
    return _api_key_cache
