"""Agent Learning Service for error-driven knowledge capture and retrieval.

Captures learnings from agent error-recovery cycles and retrieves relevant
learnings via semantic similarity before agent execution.
"""
import asyncio
import functools
import hashlib
import logging
import re
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# In-memory dedup cache to avoid embedding API calls for known duplicates.
# Thread-safe via asyncio lock. FIFO eviction, max 100 entries.
_recent_hashes: dict[str, bool] = {}
_HASH_CACHE_MAX = 100
_hash_lock = asyncio.Lock()


@functools.lru_cache(maxsize=1)
def get_agent_learning_service() -> "AgentLearningService":
    """Singleton access to AgentLearningService."""
    return AgentLearningService()


class AgentLearningService:
    """
    Captures learnings from agent error-recovery cycles.
    Retrieves relevant learnings via semantic similarity before execution.
    """

    async def capture_from_error(
        self,
        tenant_id: UUID,
        error: Exception,
        context: dict[str, Any],
        fix_applied: str,
        agent_id: UUID | None = None,
        session_id: UUID | None = None,
    ) -> UUID | None:
        """
        Auto-capture a learning from an error + recovery.
        Uses its own DB session (not the caller's, which may be failed).
        Returns learning ID, or None if duplicate or disabled.
        """
        from app.config import get_settings
        settings = get_settings()
        if not getattr(settings, 'USE_AGENT_LEARNING', False):
            return None

        # 1. Normalize and hash for dedup
        error_type = type(error).__name__
        error_msg = str(error)[:100]
        tool_name = context.get("tool_name", "unknown")
        content_hash = self._compute_hash(error_type, error_msg, tool_name)

        # 2. Check in-memory cache first (avoid embedding API call)
        async with _hash_lock:
            if content_hash in _recent_hashes:
                return None

        # 3. Generate content with PII scrubbing
        title, content = self._generate_learning_content(error, context, fix_applied)
        sanitized_error = self._sanitize_pii(str(error)[:500])

        # 4. Acquire independent DB session (caller's may be failed/rolled-back)
        from app.core.database import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            # Set RLS tenant context
            await db.execute(
                text("SET LOCAL app.current_tenant = :tenant_id"),
                {"tenant_id": str(tenant_id)},
            )

            # Check DB dedup
            existing = await self._find_by_hash(db, content_hash)
            if existing:
                async with _hash_lock:
                    self._cache_hash(content_hash)
                return None

            # 5. Generate embedding
            try:
                from app.services.rag_service import RAGService
                rag = RAGService(db)
                embedding = await rag.get_embedding(f"{title} {content}")
            except Exception as e:
                logger.warning(f"Embedding generation failed for learning: {e}")
                embedding = None

            # 6. Store
            from app.models.agent_learning import AgentLearning
            learning = AgentLearning(
                tenant_id=tenant_id,
                agent_id=agent_id,
                title=title[:255],
                content=content,
                pattern_type=self._classify_error(error),
                source="error_capture",
                source_session_id=session_id,
                source_error=sanitized_error,
                embedding=embedding,
                content_hash=content_hash,
                status="active",
            )
            db.add(learning)
            await db.commit()

            async with _hash_lock:
                self._cache_hash(content_hash)
            logger.info(f"Captured learning: {title[:80]} (agent={agent_id})")
            return learning.id

    async def search_relevant_learnings(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        query: str,
        agent_id: UUID | None = None,
        limit: int = 3,
        min_similarity: float = 0.6,
    ) -> list:
        """
        Semantic search for relevant learnings. Pure cosine similarity,
        filtered by tenant + agent affinity. No magic-number boosts.
        """
        from app.config import get_settings
        settings = get_settings()
        if not getattr(settings, 'USE_AGENT_LEARNING', False):
            return []

        try:
            from app.services.rag_service import RAGService
            rag = RAGService(db)
            query_embedding = await rag.get_embedding(query)
        except Exception as e:
            logger.warning(f"Embedding generation failed for learning search: {e}")
            return []

        sql = text("""
            SELECT id, title, content, pattern_type, source,
                   (1 - (embedding <=> :query_embedding)) AS similarity
            FROM agent_learnings
            WHERE tenant_id = :tenant_id
              AND (agent_id = :agent_id OR agent_id IS NULL)
              AND status = 'active'
              AND embedding IS NOT NULL
              AND (1 - (embedding <=> :query_embedding)) >= :min_similarity
            ORDER BY similarity DESC
            LIMIT :limit
        """)

        result = await db.execute(sql, {
            "query_embedding": str(query_embedding),
            "tenant_id": str(tenant_id),
            "agent_id": str(agent_id) if agent_id else None,
            "min_similarity": min_similarity,
            "limit": limit,
        })

        rows = result.fetchall()

        # Update last_used_at for retrieved learnings
        if rows:
            ids = [row.id for row in rows]
            from app.models.agent_learning import AgentLearning
            await db.execute(
                update(AgentLearning)
                .where(AgentLearning.id.in_(ids))
                .values(last_used_at=func.now())
            )

        return rows

    def format_for_context(self, learnings: list) -> str:
        """Format learnings for injection into agent context (~400 tokens max)."""
        if not learnings:
            return ""

        lines = ["[Relevant Learnings from Past Experience]"]
        for learning in learnings[:3]:
            lines.append(f"- {learning.title}: {learning.content[:200]}")
        return "\n".join(lines)

    async def save_learning(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        title: str,
        content: str,
        agent_id: UUID | None = None,
        pattern_type: str = "general",
    ) -> UUID | None:
        """Manually save a learning (for skills). Returns ID or None if duplicate."""
        content_hash = self._compute_hash("manual", title[:100], pattern_type)
        existing = await self._find_by_hash(db, content_hash)
        if existing:
            return None

        try:
            from app.services.rag_service import RAGService
            rag = RAGService(db)
            embedding = await rag.get_embedding(f"{title} {content}")
        except Exception as e:
            logger.warning(f"Embedding generation failed for manual learning: {e}")
            embedding = None

        from app.models.agent_learning import AgentLearning
        learning = AgentLearning(
            tenant_id=tenant_id,
            agent_id=agent_id,
            title=title[:255],
            content=content,
            pattern_type=pattern_type,
            source="manual",
            embedding=embedding,
            content_hash=content_hash,
            status="active",
        )
        db.add(learning)
        await db.commit()
        return learning.id

    async def list_learnings(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        agent_id: UUID | None = None,
        status: str | None = None,
        pattern_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list, int]:
        """List learnings with optional filters. Returns (items, total_count)."""
        from app.models.agent_learning import AgentLearning

        query = select(AgentLearning).where(AgentLearning.tenant_id == tenant_id)

        if agent_id is not None:
            query = query.where(AgentLearning.agent_id == agent_id)
        if status is not None:
            query = query.where(AgentLearning.status == status)
        if pattern_type is not None:
            query = query.where(AgentLearning.pattern_type == pattern_type)

        # Count
        from sqlalchemy import func as sa_func
        count_query = select(sa_func.count()).select_from(query.subquery())
        total = (await db.execute(count_query)).scalar() or 0

        # Fetch page
        query = query.order_by(AgentLearning.created_at.desc()).limit(limit).offset(offset)
        result = await db.execute(query)
        items = list(result.scalars().all())

        return items, total

    async def update_learning_status(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        learning_id: UUID,
        status: str,
    ) -> bool:
        """Update learning status. Returns True if found and updated."""
        from app.models.agent_learning import AgentLearning
        result = await db.execute(
            select(AgentLearning).where(
                AgentLearning.id == learning_id,
                AgentLearning.tenant_id == tenant_id,
            )
        )
        learning = result.scalars().first()
        if not learning:
            return False

        learning.status = status
        await db.commit()
        return True

    async def delete_learning(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        learning_id: UUID,
    ) -> bool:
        """Delete a learning. Returns True if found and deleted."""
        from app.models.agent_learning import AgentLearning
        result = await db.execute(
            select(AgentLearning).where(
                AgentLearning.id == learning_id,
                AgentLearning.tenant_id == tenant_id,
            )
        )
        learning = result.scalars().first()
        if not learning:
            return False

        await db.delete(learning)
        await db.commit()
        return True

    # --- Private helpers ---

    def _compute_hash(self, error_type: str, error_msg: str, tool_name: str) -> str:
        """Normalized hash: error_type + first 100 chars + tool_name."""
        normalized = f"{error_type}:{error_msg[:100]}:{tool_name}".lower().strip()
        return hashlib.sha256(normalized.encode()).hexdigest()

    def _cache_hash(self, content_hash: str) -> None:
        """Add to in-memory dedup cache, evicting oldest if full.
        MUST be called while holding _hash_lock.
        """
        if len(_recent_hashes) >= _HASH_CACHE_MAX:
            first_key = next(iter(_recent_hashes))
            del _recent_hashes[first_key]
        _recent_hashes[content_hash] = True

    def _generate_learning_content(
        self, error: Exception, context: dict[str, Any], fix: str
    ) -> tuple[str, str]:
        """Template-based content generation. No LLM call."""
        error_type = type(error).__name__
        error_msg = str(error)[:200]

        title = f"{error_type}: {error_msg}"
        content = (
            f"Error: {error_type} - {self._sanitize_pii(str(error)[:500])}\n"
            f"Context: {self._sanitize_pii(str(context)[:500])}\n"
            f"Resolution: {fix[:500]}\n"
            f"Pattern: When encountering '{error_msg[:100]}', "
            f"the fix is: {fix[:200]}"
        )
        return title, content

    def _sanitize_pii(self, text_input: str) -> str:
        """Remove PII from text before storage. Best-effort regex filter."""
        # Emails
        text_input = re.sub(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', '[EMAIL]', text_input)
        # API keys / tokens
        text_input = re.sub(r'(sk-|pk-|key-|token-|bearer\s+)[a-zA-Z0-9]{10,}', '[REDACTED_KEY]', text_input, flags=re.IGNORECASE)
        # SSN
        text_input = re.sub(r'\b\d{3}-\d{2}-\d{4}\b', '[SSN]', text_input)
        # Phone numbers
        text_input = re.sub(r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b', '[PHONE]', text_input)
        # Credit card numbers
        text_input = re.sub(r'\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{1,7}\b', '[CREDIT_CARD]', text_input)
        # IPv4
        text_input = re.sub(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', '[IP_ADDR]', text_input)
        return text_input[:1000]

    def _classify_error(self, error: Exception) -> str:
        """Classify error type for pattern_type field."""
        error_name = type(error).__name__.lower()
        if "timeout" in error_name or "connect" in error_name:
            return "api_error"
        if "type" in error_name or "value" in error_name or "key" in error_name:
            return "data_quality"
        if "permission" in error_name or "auth" in error_name:
            return "auth_error"
        return "general"

    async def _find_by_hash(self, db: AsyncSession, content_hash: str):
        from app.models.agent_learning import AgentLearning
        result = await db.execute(
            select(AgentLearning).where(AgentLearning.content_hash == content_hash)
        )
        return result.scalars().first()
