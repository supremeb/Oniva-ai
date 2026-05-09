"""
User Preferences Service for Oniva Enhanced Memory System.

Manages per-user preferences for agent personalization.
Includes Redis caching for fast retrieval.

Features:
- JSONB-backed flexible preferences
- Redis caching with 1hr TTL
- Schema versioning for migrations
- Default value merging
"""

import json
import logging
import threading
from typing import Optional, Dict, Any
from uuid import UUID
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert

from app.config import get_settings
from app.models.user_preferences import UserPreferences
from app.schemas.memory import UserPreferencesSchema

logger = logging.getLogger(__name__)

# Singleton instance with thread safety
_user_preferences_service: Optional['UserPreferencesService'] = None
_user_preferences_lock = threading.Lock()


def get_user_preferences_service() -> 'UserPreferencesService':
    """Get or create the singleton UserPreferencesService instance."""
    global _user_preferences_service
    if _user_preferences_service is None:
        with _user_preferences_lock:
            if _user_preferences_service is None:  # Double-check locking
                _user_preferences_service = UserPreferencesService()
    return _user_preferences_service


def reset_user_preferences_service() -> None:
    """Reset the singleton instance (for testing)."""
    global _user_preferences_service
    _user_preferences_service = None


def deep_merge(base: Dict, override: Dict) -> Dict:
    """
    Deep merge two dictionaries.

    Override values take precedence over base values.
    """
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class UserPreferencesService:
    """
    Manage user preferences for agent personalization.

    Uses Redis caching for fast retrieval and JSONB for flexible storage.
    """

    # Cache TTL in seconds (1 hour per performance recommendation)
    CACHE_TTL = 3600

    # Current schema version
    SCHEMA_VERSION = 1

    # Default preferences (applied when user has no preferences set)
    DEFAULT_PREFERENCES = {
        "analysis": {
            "risk_tolerance": "moderate",
            "preferred_metrics": ["P/E", "ROE", "Debt/Equity", "Revenue Growth"],
            "default_timeframe": "1Y",
            "include_competitors": True,
            "benchmark_index": "SPY",
        },
        "output": {
            "verbosity": "standard",
            "include_charts": True,
            "report_format": "technical",
            "language": "en",
            "currency": "USD",
        },
        "data_sources": {
            "preferred_providers": ["yahoo_finance", "sec_edgar"],
            "include_news": True,
            "news_recency_days": 7,
        },
        "compliance": {
            "frameworks": [],
            "auto_check": False,
            "risk_flags": ["high_leverage", "insider_trading"],
        },
    }

    def __init__(self):
        """Initialize UserPreferencesService."""
        self._redis = None
        self._redis_disabled = False  # Set to True in tests to prevent Redis reconnection

    async def _get_redis(self):
        """Get Redis connection lazily with connection pooling."""
        if self._redis_disabled:
            return None
        if self._redis is None:
            try:
                import redis.asyncio as redis
                settings = get_settings()
                self._redis = redis.from_url(
                    settings.REDIS_URL,
                    encoding="utf-8",
                    decode_responses=True,
                    max_connections=20,  # Connection pool size
                    socket_connect_timeout=5,  # Connection timeout
                    socket_timeout=5,  # Read/write timeout
                    retry_on_timeout=True,
                )
            except Exception as e:
                logger.warning(f"Redis connection failed: {type(e).__name__}")
                logger.debug("Redis connection error details", exc_info=True)
                self._redis = None
        return self._redis

    def _cache_key(self, tenant_id: UUID, user_id: UUID) -> str:
        """Generate cache key for user preferences."""
        return f"prefs:{tenant_id}:{user_id}"

    async def _get_from_cache(
        self,
        tenant_id: UUID,
        user_id: UUID,
    ) -> Optional[Dict[str, Any]]:
        """Get preferences from Redis cache."""
        try:
            redis = await self._get_redis()
            if redis is None:
                return None

            cached = await redis.get(self._cache_key(tenant_id, user_id))
            if cached:
                return json.loads(cached)
        except Exception as e:
            logger.warning(f"Cache read failed: {type(e).__name__}")
        return None

    async def _set_cache(
        self,
        tenant_id: UUID,
        user_id: UUID,
        preferences: Dict[str, Any],
    ) -> None:
        """Set preferences in Redis cache."""
        try:
            redis = await self._get_redis()
            if redis is None:
                return

            await redis.setex(
                self._cache_key(tenant_id, user_id),
                self.CACHE_TTL,
                json.dumps(preferences)
            )
        except Exception as e:
            logger.warning(f"Cache write failed: {type(e).__name__}")

    async def _invalidate_cache(
        self,
        tenant_id: UUID,
        user_id: UUID,
    ) -> None:
        """Invalidate preferences in Redis cache."""
        try:
            redis = await self._get_redis()
            if redis is None:
                return

            await redis.delete(self._cache_key(tenant_id, user_id))
        except Exception as e:
            logger.warning(f"Cache invalidation failed: {type(e).__name__}")

    async def get_preferences(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        user_id: UUID,
    ) -> Dict[str, Any]:
        """
        Get user preferences with caching.

        Returns merged preferences (user overrides + defaults).

        Args:
            db: Database session
            tenant_id: Tenant ID for multi-tenancy
            user_id: User ID

        Returns:
            Dictionary of preferences (user + defaults merged)
        """
        # Check cache first
        cached = await self._get_from_cache(tenant_id, user_id)
        if cached is not None:
            return cached

        # Load from database
        result = await db.execute(
            select(UserPreferences)
            .where(UserPreferences.tenant_id == tenant_id)
            .where(UserPreferences.user_id == user_id)
        )
        prefs = result.scalar_one_or_none()

        # Merge with defaults
        user_prefs = prefs.preferences if prefs else {}
        merged = deep_merge(self.DEFAULT_PREFERENCES, user_prefs)

        # Cache result
        await self._set_cache(tenant_id, user_id, merged)

        return merged

    async def get_preferences_record(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        user_id: UUID,
    ) -> Optional[UserPreferences]:
        """
        Get the raw preferences record (without defaults merged).

        Args:
            db: Database session
            tenant_id: Tenant ID
            user_id: User ID

        Returns:
            UserPreferences record or None
        """
        result = await db.execute(
            select(UserPreferences)
            .where(UserPreferences.tenant_id == tenant_id)
            .where(UserPreferences.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def set_preferences(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        user_id: UUID,
        preferences: Dict[str, Any],
    ) -> UserPreferences:
        """
        Set user preferences (full replace).

        Args:
            db: Database session
            tenant_id: Tenant ID
            user_id: User ID
            preferences: New preferences dictionary

        Returns:
            Updated UserPreferences record
        """
        # Validate against schema
        validated = UserPreferencesSchema(**preferences)

        # Upsert using PostgreSQL INSERT ... ON CONFLICT
        stmt = insert(UserPreferences).values(
            tenant_id=tenant_id,
            user_id=user_id,
            preferences=validated.model_dump(),
            schema_version=self.SCHEMA_VERSION,
            updated_at=datetime.utcnow(),
        ).on_conflict_do_update(
            constraint='uq_user_preferences_tenant_user',
            set_={
                'preferences': validated.model_dump(),
                'schema_version': self.SCHEMA_VERSION,
                'updated_at': datetime.utcnow(),
            }
        )
        await db.execute(stmt)
        await db.commit()

        # Invalidate cache
        await self._invalidate_cache(tenant_id, user_id)

        # Fetch and return the updated record
        return await self.get_preferences_record(db, tenant_id, user_id)

    async def update_preferences(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        user_id: UUID,
        updates: Dict[str, Any],
    ) -> UserPreferences:
        """
        Update user preferences (partial update).

        Args:
            db: Database session
            tenant_id: Tenant ID
            user_id: User ID
            updates: Partial preferences to merge

        Returns:
            Updated UserPreferences record
        """
        # Get current preferences (without defaults)
        record = await self.get_preferences_record(db, tenant_id, user_id)
        current = record.preferences if record else {}

        # Merge updates
        merged = deep_merge(current, updates)

        # Save
        return await self.set_preferences(db, tenant_id, user_id, merged)

    async def delete_preferences(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        user_id: UUID,
    ) -> bool:
        """
        Delete user preferences (reset to defaults).

        Args:
            db: Database session
            tenant_id: Tenant ID
            user_id: User ID

        Returns:
            True if deleted, False if not found
        """
        record = await self.get_preferences_record(db, tenant_id, user_id)
        if not record:
            return False

        await db.delete(record)
        await db.commit()

        # Invalidate cache
        await self._invalidate_cache(tenant_id, user_id)

        return True

    async def get_agent_config(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        user_id: UUID,
        agent_name: str,
    ) -> Dict[str, Any]:
        """
        Get agent-specific configuration from preferences.

        Extracts relevant preferences for a specific agent type.

        Args:
            db: Database session
            tenant_id: Tenant ID
            user_id: User ID
            agent_name: Name of the agent (e.g., 'analysis', 'compliance')

        Returns:
            Agent-specific configuration dictionary
        """
        prefs = await self.get_preferences(db, tenant_id, user_id)

        # Extract agent-relevant preferences
        config = {
            "output": prefs.get("output", {}),
        }

        # Add agent-specific preferences
        if agent_name in ["analysis", "data_retrieval", "research"]:
            config["analysis"] = prefs.get("analysis", {})
            config["data_sources"] = prefs.get("data_sources", {})
        elif agent_name == "compliance":
            config["compliance"] = prefs.get("compliance", {})

        return config

    def format_for_context(
        self,
        preferences: Dict[str, Any],
        max_tokens: int = 300,
    ) -> str:
        """
        Format preferences for inclusion in agent context.

        Args:
            preferences: User preferences dictionary
            max_tokens: Maximum token budget (default 300)

        Returns:
            Formatted string for agent context
        """
        analysis = preferences.get("analysis", {})
        output = preferences.get("output", {})

        parts = [
            "## User Preferences\n",
            f"- Risk tolerance: {analysis.get('risk_tolerance', 'moderate')}\n",
            f"- Preferred metrics (include when relevant): {', '.join(analysis.get('preferred_metrics', [])[:5])}\n",
            f"- Timeframe: {analysis.get('default_timeframe', '1Y')}\n",
            f"- Output style: {output.get('verbosity', 'standard')}\n",
        ]

        if analysis.get("benchmark_index"):
            parts.append(f"- Benchmark (only when user asks for comparison): {analysis['benchmark_index']}\n")

        return "".join(parts)
