"""
Embedding Cache Service - Redis-based caching for embeddings

This service provides caching for text embeddings to:
1. Reduce API costs (embeddings are expensive)
2. Improve latency (cache hits avoid network calls)
3. Enable consistent results for repeated queries

Uses content hashing for cache keys to handle variable-length text.
"""

import hashlib
import json
import logging
from typing import List, Optional
import redis.asyncio as redis
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class EmbeddingCache:
    """
    Redis-based cache for text embeddings.
    
    Cache key format: "emb:{model}:{content_hash}"
    TTL: 24 hours by default (embeddings don't change)
    Max entries: Managed by Redis LRU when memory limit is set
    """
    
    CACHE_PREFIX = "emb"
    DEFAULT_TTL = 86400  # 24 hours in seconds
    
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
    
    def _hash_content(self, text: str) -> str:
        """Create SHA256 hash of text content for cache key"""
        return hashlib.sha256(text.encode()).hexdigest()[:16]
    
    def _cache_key(self, text: str, model: str | None = None) -> str:
        """Generate cache key for text+model combination"""
        if model is None:
            model = settings.EMBEDDING_MODEL
        content_hash = self._hash_content(text)
        return f"{self.CACHE_PREFIX}:{model}:{content_hash}"
    
    async def get(self, text: str, model: str | None = None) -> Optional[List[float]]:
        """
        Get cached embedding for text.
        
        Args:
            text: The text that was embedded
            model: The embedding model used
            
        Returns:
            The embedding vector if cached, None otherwise
        """
        try:
            client = await self._get_client()
            key = self._cache_key(text, model)
            cached = await client.get(key)
            
            if cached:
                logger.debug(f"Embedding cache hit: {key[:30]}...")
                return json.loads(cached)
            
            logger.debug(f"Embedding cache miss: {key[:30]}...")
            return None
            
        except Exception as e:
            logger.warning(f"Redis cache get error: {e}")
            return None
    
    async def set(
        self, 
        text: str, 
        embedding: List[float], 
        model: str | None = None,
        ttl: int = DEFAULT_TTL
    ) -> bool:
        """
        Cache an embedding for text.
        
        Args:
            text: The text that was embedded
            embedding: The embedding vector
            model: The embedding model used
            ttl: Time-to-live in seconds
            
        Returns:
            True if successfully cached, False otherwise
        """
        try:
            client = await self._get_client()
            key = self._cache_key(text, model)
            
            await client.setex(
                key,
                ttl,
                json.dumps(embedding)
            )
            logger.debug(f"Embedding cached: {key[:30]}...")
            return True
            
        except Exception as e:
            logger.warning(f"Redis cache set error: {e}")
            return False
    
    async def clear_all(self) -> int:
        """Clear all cached embeddings. Returns count of keys deleted."""
        try:
            client = await self._get_client()
            pattern = f"{self.CACHE_PREFIX}:*"
            
            keys = []
            async for key in client.scan_iter(match=pattern):
                keys.append(key)
            
            if keys:
                deleted = await client.delete(*keys)
                logger.info(f"Cleared {deleted} cached embeddings")
                return deleted
            return 0
            
        except Exception as e:
            logger.warning(f"Redis cache clear error: {e}")
            return 0
    
    async def stats(self) -> dict:
        """Get cache statistics"""
        try:
            client = await self._get_client()
            
            # Count embedding keys
            count = 0
            async for _ in client.scan_iter(match=f"{self.CACHE_PREFIX}:*"):
                count += 1
            
            # Get Redis info
            info = await client.info("memory")
            
            return {
                "cached_embeddings": count,
                "memory_used": info.get("used_memory_human", "unknown"),
                "connected": True
            }
            
        except Exception as e:
            logger.warning(f"Redis stats error: {e}")
            return {
                "cached_embeddings": 0,
                "memory_used": "unknown",
                "connected": False,
                "error": str(e)
            }
    
    async def close(self):
        """Close Redis connection"""
        if self._client:
            await self._client.close()
            self._client = None


# Singleton instance
_embedding_cache: Optional[EmbeddingCache] = None


def get_embedding_cache() -> EmbeddingCache:
    """Get or create the singleton embedding cache instance"""
    global _embedding_cache
    if _embedding_cache is None:
        _embedding_cache = EmbeddingCache()
    return _embedding_cache
