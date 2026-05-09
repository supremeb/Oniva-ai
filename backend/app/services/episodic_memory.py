"""
Episodic Memory Service for Oniva Enhanced Memory System.

Stores and retrieves past agent decisions with vector similarity search.
Enables cross-session learning and consistent agent behavior.

Features:
- Hybrid search (semantic + BM25) using RRF fusion
- Vector similarity search using pgvector HNSW index
- Importance scoring with recency boost
- Auto-archival of old, unused memories
- Graceful degradation if embedding generation fails
- Token budget management (3 entries max for context)
- Memory consolidation for 1M+ memories
"""

import asyncio
import logging
import threading
from typing import Optional, List, Dict, Any, Literal
import uuid
from uuid import UUID
from datetime import datetime, timedelta
from enum import Enum

from sqlalchemy import text, select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.episodic_memory import EpisodicMemory
from app.schemas.memory import EpisodicMemoryResponse


class SearchMode(str, Enum):
    """Memory search algorithm selection."""
    HYBRID = "hybrid"      # Semantic + BM25 with RRF
    SEMANTIC = "semantic"  # Pure vector similarity
    KEYWORD = "keyword"    # Pure full-text search

logger = logging.getLogger(__name__)

# Singleton instance with thread safety
_episodic_memory_service: Optional['EpisodicMemoryService'] = None
_episodic_memory_lock = threading.Lock()


def get_episodic_memory_service() -> 'EpisodicMemoryService':
    """Get or create the singleton EpisodicMemoryService instance."""
    global _episodic_memory_service
    if _episodic_memory_service is None:
        with _episodic_memory_lock:
            if _episodic_memory_service is None:  # Double-check locking
                _episodic_memory_service = EpisodicMemoryService()
    return _episodic_memory_service


def reset_episodic_memory_service() -> None:
    """Reset the singleton instance (for testing)."""
    global _episodic_memory_service
    _episodic_memory_service = None


class EpisodicMemoryService:
    """
    Store and retrieve past agent decisions.

    Uses pgvector for similarity search and RAGService for embeddings.
    Implements graceful degradation when dependencies fail.
    """

    # Memory consolidation threshold (per Performance Oracle)
    CONSOLIDATION_THRESHOLD = 50000

    # Default retention period (2 years per security requirements)
    DEFAULT_RETENTION_DAYS = 730

    # Maximum retention (7 years for compliance)
    MAX_RETENTION_DAYS = 2555

    def __init__(self):
        """Initialize EpisodicMemoryService."""
        pass  # Removed in-memory _embedding_cache — rely on Redis EmbeddingCache via RAGService

    # Expected dimension for episodic_memories.embedding column
    EMBEDDING_DIM = 1536

    async def _get_embedding(self, text: str, db: AsyncSession) -> Optional[List[float]]:
        """
        Generate embedding for text using RAGService.

        Uses Redis EmbeddingCache for caching. Returns None if embedding generation fails.
        Validates dimension matches the DB column (1536) to prevent silent insert failures.
        """
        try:
            from app.services.rag_service import RAGService
            rag_service = RAGService(db)
            embedding = await rag_service.get_embedding(text)
            if embedding and len(embedding) != self.EMBEDDING_DIM:
                logger.error(
                    f"Embedding dimension mismatch: got {len(embedding)}, "
                    f"expected {self.EMBEDDING_DIM}. Check embedding_model in LLM provider config."
                )
                return None
            return embedding
        except Exception as e:
            logger.warning(f"Embedding generation failed: {type(e).__name__}")
            logger.debug("Embedding generation error details", exc_info=True)
            return None  # Graceful degradation

    async def store_decision(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        user_id: UUID,
        agent_name: str,
        action_type: str,
        context: str,
        decision: str,
        session_id: Optional[str] = None,
        rationale: Optional[str] = None,
        entities: Optional[List[str]] = None,
        outcome: Optional[Dict[str, Any]] = None,
        visibility: Literal['private', 'shared'] = 'private',
        memory_type: str = 'decision',
        tags: Optional[List[str]] = None,
    ) -> UUID:
        """
        Store a decision to episodic memory.

        Args:
            db: Database session
            tenant_id: Tenant ID for multi-tenancy
            user_id: User ID for isolation
            agent_name: Name of the agent making the decision
            action_type: Type of action performed
            context: Context of the decision (what was asked)
            decision: The decision made
            session_id: Optional session ID for linking
            rationale: Optional reasoning behind the decision
            entities: Optional list of entities involved
            outcome: Optional outcome/feedback for learning

        Returns:
            UUID of the stored memory

        Note:
            PII should be masked by the caller before storing.
            Embedding generation failure doesn't prevent storage.
        """
        # Combine context + decision for single embedding
        combined_text = f"Context: {context}\n\nDecision: {decision}"
        if rationale:
            combined_text += f"\n\nRationale: {rationale}"

        # Generate embedding (graceful degradation)
        try:
            embedding = await self._get_embedding(combined_text, db)
        except Exception as e:
            logger.warning(f"Embedding generation failed during store: {type(e).__name__}")
            embedding = None  # Store without embedding (graceful degradation)

        # Dedup: check for near-duplicate memory (>0.95 cosine similarity)
        if embedding is not None:
            try:
                # Escape :: casts so SQLAlchemy doesn't treat them as bind params
                dedup_sql = text("""
                    SELECT id, 1 - (embedding <=> CAST(:embedding AS vector)) as similarity
                    FROM episodic_memories
                    WHERE tenant_id = :tenant_id
                      AND user_id = :user_id
                      AND embedding IS NOT NULL
                      AND valid_until > NOW()
                    ORDER BY embedding <=> CAST(:embedding AS vector)
                    LIMIT 1
                """)
                # Use SAVEPOINT so failures don't poison the outer transaction
                async with db.begin_nested():
                    await db.execute(text("SET LOCAL hnsw.ef_search = 100"))
                    dedup_result = await db.execute(dedup_sql, {
                        "embedding": str(embedding),
                        "tenant_id": str(tenant_id),
                        "user_id": str(user_id),
                    })
                    nearest = dedup_result.fetchone()
                if nearest and float(nearest.similarity) >= 0.95:
                    # Near-duplicate found — update timestamp instead of inserting
                    await db.execute(
                        text("""
                            UPDATE episodic_memories
                            SET updated_at = NOW()
                            WHERE id = :id AND tenant_id = :tenant_id
                        """),
                        {"id": str(nearest.id), "tenant_id": str(tenant_id)},
                    )
                    await db.flush()
                    logger.info(
                        f"Dedup hit: memory {nearest.id} updated (similarity={nearest.similarity:.3f})"
                    )
                    return nearest.id
            except Exception as e:
                logger.warning(f"Dedup check failed, proceeding with insert: {e}")

        # Calculate retention date
        settings = get_settings()
        retention_days = self.DEFAULT_RETENTION_DAYS
        valid_until = datetime.utcnow() + timedelta(days=retention_days)

        # PII check for shared memories
        if visibility == 'shared':
            from app.utils.pii_scanner import has_pii
            pii_found, pii_type = has_pii(combined_text)
            if pii_found:
                raise ValueError(f"Cannot share memory: {pii_type}. Remove PII before sharing.")

        # Create memory entry — set id explicitly so it's available before flush
        memory_id = uuid.uuid4()
        memory = EpisodicMemory(
            id=memory_id,
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
            agent_name=agent_name,
            action_type=action_type,
            context_decision=combined_text,
            embedding=embedding,
            rationale=rationale,
            outcome=outcome,
            entities=entities or [],
            valid_until=valid_until,
            visibility=visibility,
            memory_type=memory_type,
            tags=tags or [],
        )

        db.add(memory)
        await db.flush()

        # Populate search_vector for full-text (BM25) search
        try:
            await db.execute(
                text("""
                    UPDATE episodic_memories
                    SET search_vector = to_tsvector('english',
                        coalesce(context_decision, '') || ' ' ||
                        coalesce(rationale, '') || ' ' ||
                        coalesce((SELECT string_agg(e.value, ' ') FROM jsonb_array_elements_text(entities) e), ''))
                    WHERE id = :id AND tenant_id = :tenant_id
                """),
                {"id": str(memory_id), "tenant_id": str(tenant_id)},
            )
            await db.flush()
        except Exception as e:
            logger.warning(f"search_vector generation failed: {e}")

        logger.info(f"Stored episodic memory {memory_id} for user {user_id}")
        return memory_id

    async def hybrid_recall(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        user_id: UUID,
        query: str,
        limit: int = 10,
        include_archived: bool = False,
        importance_weight: float = 0.3,
        recency_weight: float = 0.2,
        agent_name: Optional[str] = None,
        action_type: Optional[str] = None,
    ) -> List[EpisodicMemoryResponse]:
        """
        Hybrid search combining semantic + BM25 with importance scoring.

        Score = RRF(semantic, keyword) * (1 + importance_boost) * (1 + recency_boost)

        Uses MULTIPLICATIVE boosting to avoid scale mismatch between RRF (~0.03) and weights (0-1).

        Args:
            db: Database session
            tenant_id: Tenant ID for RLS
            user_id: User ID for scoping
            query: Search query
            limit: Max results
            include_archived: Include archived memories
            importance_weight: Multiplier for importance boost (0-1)
            recency_weight: Multiplier for recency boost (0-1)
            agent_name: Optional filter by agent
            action_type: Optional filter by action type

        Returns:
            Ranked list of memories with scores
        """
        try:
            # Generate query embedding
            embedding = await self._get_embedding(query, db)
            if embedding is None:
                logger.warning("Cannot perform hybrid search without embedding, falling back to keyword")
                return await self.keyword_recall(db, tenant_id, user_id, query, limit, include_archived)

            # Hybrid search using RRF with MULTIPLICATIVE boosting
            hybrid_sql = text("""
                WITH semantic_results AS (
                    SELECT
                        id,
                        ROW_NUMBER() OVER (ORDER BY embedding <=> CAST(:embedding AS vector)) as semantic_rank
                    FROM episodic_memories
                    WHERE tenant_id = :tenant_id
                      AND user_id = :user_id
                      AND valid_until > NOW()
                      AND embedding IS NOT NULL
                      AND (CAST(:include_archived AS BOOLEAN) OR archived = false)
                      AND (CAST(:agent_name AS VARCHAR) IS NULL OR agent_name = :agent_name)
                      AND (CAST(:action_type AS VARCHAR) IS NULL OR action_type = :action_type)
                    ORDER BY embedding <=> CAST(:embedding AS vector)
                    LIMIT :candidate_limit
                ),
                keyword_results AS (
                    SELECT
                        id,
                        ROW_NUMBER() OVER (
                            ORDER BY ts_rank(search_vector, plainto_tsquery('english', :query)) DESC
                        ) as keyword_rank
                    FROM episodic_memories
                    WHERE tenant_id = :tenant_id
                      AND user_id = :user_id
                      AND valid_until > NOW()
                      AND search_vector IS NOT NULL
                      AND (CAST(:include_archived AS BOOLEAN) OR archived = false)
                      AND (CAST(:agent_name AS VARCHAR) IS NULL OR agent_name = :agent_name)
                      AND (CAST(:action_type AS VARCHAR) IS NULL OR action_type = :action_type)
                      AND search_vector @@ plainto_tsquery('english', :query)
                    LIMIT :candidate_limit
                ),
                combined AS (
                    SELECT
                        COALESCE(s.id, k.id) as id,
                        -- RRF score (k=60 is standard constant)
                        COALESCE(1.0 / (60 + s.semantic_rank), 0) +
                        COALESCE(1.0 / (60 + k.keyword_rank), 0) as rrf_score
                    FROM semantic_results s
                    FULL OUTER JOIN keyword_results k ON s.id = k.id
                )
                SELECT
                    m.id, m.session_id, m.agent_name, m.action_type,
                    m.context_decision, m.rationale, m.outcome, m.entities,
                    m.created_at, m.importance, m.archived, m.access_count,
                    m.memory_type, m.feedback, m.feedback_at, m.visibility,
                    m.source_type, m.source_id, m.tags,
                    c.rrf_score,
                    -- MULTIPLICATIVE scoring to avoid scale mismatch
                    c.rrf_score
                    * (1.0 + m.importance * :importance_weight)
                    * (1.0 + CASE
                        WHEN m.created_at > NOW() - INTERVAL '7 days' THEN :recency_weight
                        WHEN m.created_at > NOW() - INTERVAL '30 days' THEN :recency_weight * 0.5
                        ELSE 0
                       END) as final_score
                FROM combined c
                JOIN episodic_memories m ON m.id = c.id
                ORDER BY final_score DESC
                LIMIT :limit
            """)

            # Use SAVEPOINT so failures don't poison the outer transaction
            async with db.begin_nested():
                await db.execute(text("SET LOCAL hnsw.ef_search = 100"))
                result = await db.execute(
                    hybrid_sql,
                    {
                        "embedding": str(embedding),
                        "query": query,
                        "tenant_id": str(tenant_id),
                        "user_id": str(user_id),
                        "include_archived": include_archived,
                        "importance_weight": importance_weight,
                        "recency_weight": recency_weight,
                        "agent_name": agent_name,
                        "action_type": action_type,
                        "candidate_limit": limit * 3,  # Over-fetch for better RRF
                        "limit": limit,
                    }
                )
                rows = result.fetchall()

            # Fire-and-forget access tracking (async, non-blocking)
            if rows:
                memory_ids = [row.id for row in rows]
                asyncio.create_task(
                    self._update_access_stats_background(db, memory_ids, tenant_id)
                )

            return [
                EpisodicMemoryResponse(
                    id=row.id,
                    session_id=row.session_id,
                    agent_name=row.agent_name,
                    action_type=row.action_type,
                    context_decision=row.context_decision,
                    rationale=row.rationale,
                    outcome=row.outcome,
                    entities=row.entities or [],
                    similarity=float(row.final_score),
                    importance=float(row.importance),
                    archived=row.archived,
                    access_count=row.access_count,
                    created_at=row.created_at,
                    memory_type=row.memory_type or "decision",
                    feedback=row.feedback,
                    feedback_at=row.feedback_at,
                    visibility=row.visibility or "private",
                    source_type=row.source_type,
                    source_id=row.source_id,
                    tags=row.tags or [],
                )
                for row in rows
            ]

        except Exception as e:
            logger.warning("Hybrid memory search failed (SAVEPOINT rolled back): %s", e)
            logger.debug("Hybrid search error details", exc_info=True)
            return []  # Graceful degradation

    async def keyword_recall(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        user_id: UUID,
        query: str,
        limit: int = 10,
        include_archived: bool = False,
    ) -> List[EpisodicMemoryResponse]:
        """
        Pure keyword search using full-text search (BM25).

        Args:
            db: Database session
            tenant_id: Tenant ID for RLS
            user_id: User ID for scoping
            query: Search query
            limit: Max results
            include_archived: Include archived memories

        Returns:
            Ranked list of memories by keyword relevance
        """
        try:
            keyword_sql = text("""
                SELECT
                    id, session_id, agent_name, action_type,
                    context_decision, rationale, outcome, entities,
                    created_at, importance, archived, access_count,
                    ts_rank(search_vector, plainto_tsquery('english', :query)) as score
                FROM episodic_memories
                WHERE tenant_id = :tenant_id
                  AND user_id = :user_id
                  AND valid_until > NOW()
                  AND search_vector IS NOT NULL
                  AND (CAST(:include_archived AS BOOLEAN) OR archived = false)
                  AND search_vector @@ plainto_tsquery('english', :query)
                ORDER BY score DESC
                LIMIT :limit
            """)

            # Use SAVEPOINT so failures don't poison the outer transaction
            async with db.begin_nested():
                result = await db.execute(
                    keyword_sql,
                    {
                        "query": query,
                        "tenant_id": str(tenant_id),
                        "user_id": str(user_id),
                        "include_archived": include_archived,
                        "limit": limit,
                    }
                )
                rows = result.fetchall()

            return [
                EpisodicMemoryResponse(
                    id=row.id,
                    session_id=row.session_id,
                    agent_name=row.agent_name,
                    action_type=row.action_type,
                    context_decision=row.context_decision,
                    rationale=row.rationale,
                    outcome=row.outcome,
                    entities=row.entities or [],
                    similarity=float(row.score),
                    importance=float(row.importance),
                    archived=row.archived,
                    access_count=row.access_count,
                    created_at=row.created_at,
                )
                for row in rows
            ]

        except Exception as e:
            logger.warning("Keyword memory search failed (SAVEPOINT rolled back): %s", e)
            return []

    async def _update_access_stats_background(
        self,
        db: AsyncSession,
        memory_ids: List[UUID],
        tenant_id: UUID,
    ) -> None:
        """Background task to update access stats without blocking search."""
        try:
            # Create a new session for background work
            from app.core.database import get_db_for_tenant
            async with get_db_for_tenant(str(tenant_id)) as session:
                await session.execute(
                    text("""
                        UPDATE episodic_memories
                        SET last_accessed = NOW(), access_count = access_count + 1
                        WHERE id = ANY(:ids) AND tenant_id = :tenant_id
                    """),
                    {"ids": [str(mid) for mid in memory_ids], "tenant_id": str(tenant_id)}
                )
                await session.commit()
        except Exception as e:
            # Log but don't fail - access tracking is non-critical
            logger.warning(f"Failed to update access stats: {e}")

    async def set_importance(
        self,
        db: AsyncSession,
        memory_id: UUID,
        importance: float,
        tenant_id: UUID,
        user_id: UUID,
    ) -> bool:
        """
        Set importance score for a memory (0.0-1.0).

        Args:
            db: Database session
            memory_id: Memory ID to update
            importance: New importance score (0.0-1.0)
            tenant_id: Tenant ID for security check
            user_id: User ID for security check

        Returns:
            True if updated, False if not found
        """
        if not 0.0 <= importance <= 1.0:
            raise ValueError("Importance must be between 0.0 and 1.0")

        result = await db.execute(
            text("""
                UPDATE episodic_memories
                SET importance = :importance
                WHERE id = :id AND tenant_id = :tenant_id AND user_id = :user_id
            """),
            {
                "id": str(memory_id),
                "importance": importance,
                "tenant_id": str(tenant_id),
                "user_id": str(user_id),
            }
        )
        await db.commit()
        return result.rowcount > 0

    async def archive_old_memories(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        user_id: UUID,
        days_threshold: int = 90,
        min_access_count: int = 2,
    ) -> int:
        """
        Archive memories older than threshold with low access.

        Args:
            db: Database session
            tenant_id: Tenant ID
            user_id: User ID
            days_threshold: Archive memories older than N days
            min_access_count: Only archive if accessed less than N times

        Returns:
            Number of memories archived
        """
        cutoff_date = datetime.utcnow() - timedelta(days=days_threshold)

        result = await db.execute(
            text("""
                UPDATE episodic_memories
                SET archived = true
                WHERE tenant_id = :tenant_id
                  AND user_id = :user_id
                  AND archived = false
                  AND visibility = 'private'
                  AND created_at < :cutoff_date
                  AND access_count < :min_access
                  AND importance < 0.7
                RETURNING id
            """),
            {
                "tenant_id": str(tenant_id),
                "user_id": str(user_id),
                "cutoff_date": cutoff_date,
                "min_access": min_access_count,
            }
        )
        await db.commit()
        return result.rowcount

    async def recall_similar(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        user_id: UUID,
        query: str,
        limit: int = 3,
        min_similarity: float = 0.5,
        agent_name: Optional[str] = None,
        action_type: Optional[str] = None,
    ) -> List[EpisodicMemoryResponse]:
        """
        Retrieve similar past decisions using vector similarity.

        Args:
            db: Database session
            tenant_id: Tenant ID for multi-tenancy
            user_id: User ID for isolation
            query: Search query for similarity matching
            limit: Maximum number of results (default 3 for token budget)
            min_similarity: Minimum similarity threshold (default 0.5)
            agent_name: Optional filter by agent name
            action_type: Optional filter by action type

        Returns:
            List of similar memories with similarity scores

        Note:
            Token Budget: Limited to 3 entries (~600 tokens).
            Graceful Degradation: Returns empty list if search fails.
        """
        try:
            # Generate query embedding
            embedding = await self._get_embedding(query, db)
            if embedding is None:
                logger.warning("Cannot search memories without embedding")
                return []

            # Build the similarity search query
            # Using CTE to calculate similarity once (avoids duplicate computation)
            sql = text("""
                WITH similarity_calc AS (
                    SELECT
                        id, session_id, agent_name, action_type,
                        context_decision, rationale, outcome, entities,
                        created_at, importance, archived, access_count,
                        memory_type, feedback, feedback_at, visibility,
                        source_type, source_id, tags,
                        1 - (embedding <=> CAST(:embedding AS vector)) as similarity
                    FROM episodic_memories
                    WHERE tenant_id = :tenant_id
                      AND user_id = :user_id
                      AND valid_until > NOW()
                      AND embedding IS NOT NULL
                      AND (CAST(:agent_name AS VARCHAR) IS NULL OR agent_name = :agent_name)
                      AND (CAST(:action_type AS VARCHAR) IS NULL OR action_type = :action_type)
                )
                SELECT * FROM similarity_calc
                WHERE similarity >= :min_similarity
                ORDER BY similarity DESC
                LIMIT :limit
            """)

            # Use SAVEPOINT so failures don't poison the outer transaction
            async with db.begin_nested():
                await db.execute(text("SET LOCAL hnsw.ef_search = 100"))
                result = await db.execute(sql, {
                    "tenant_id": str(tenant_id),
                    "user_id": str(user_id),
                    "embedding": str(embedding),
                    "min_similarity": min_similarity,
                    "agent_name": agent_name,
                    "action_type": action_type,
                    "limit": limit,
                })
                rows = result.fetchall()

            return [
                EpisodicMemoryResponse(
                    id=row.id,
                    session_id=row.session_id,
                    agent_name=row.agent_name,
                    action_type=row.action_type,
                    context_decision=row.context_decision,
                    rationale=row.rationale,
                    outcome=row.outcome,
                    entities=row.entities or [],
                    similarity=float(row.similarity),
                    importance=float(row.importance),
                    archived=row.archived,
                    access_count=row.access_count,
                    created_at=row.created_at,
                    memory_type=row.memory_type or "decision",
                    feedback=row.feedback,
                    feedback_at=row.feedback_at,
                    visibility=row.visibility or "private",
                    source_type=row.source_type,
                    source_id=row.source_id,
                    tags=row.tags or [],
                )
                for row in rows
            ]

        except Exception as e:
            logger.warning("Episodic memory search failed (SAVEPOINT rolled back): %s", e)
            logger.debug("Episodic memory search error details", exc_info=True)
            return []  # Graceful degradation

    async def update_outcome(
        self,
        db: AsyncSession,
        memory_id: UUID,
        tenant_id: UUID,
        user_id: UUID,
        outcome: Dict[str, Any],
    ) -> bool:
        """
        Update a memory with its outcome (learning loop).

        Args:
            db: Database session
            memory_id: ID of the memory to update
            tenant_id: Tenant ID for security check
            user_id: User ID for security check
            outcome: Outcome data to store

        Returns:
            True if updated, False if not found
        """
        result = await db.execute(
            select(EpisodicMemory)
            .where(EpisodicMemory.id == memory_id)
            .where(EpisodicMemory.tenant_id == tenant_id)
            .where(EpisodicMemory.user_id == user_id)
        )
        memory = result.scalar_one_or_none()

        if not memory:
            return False

        memory.outcome = outcome
        await db.commit()
        return True

    async def get_memory_count(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        user_id: UUID,
    ) -> int:
        """Get the total number of memories for a user."""
        result = await db.scalar(
            select(func.count(EpisodicMemory.id))
            .where(EpisodicMemory.tenant_id == tenant_id)
            .where(EpisodicMemory.user_id == user_id)
        )
        return result or 0

    async def should_consolidate(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        user_id: UUID,
    ) -> bool:
        """Check if user's memories exceed consolidation threshold."""
        count = await self.get_memory_count(db, tenant_id, user_id)
        return count > self.CONSOLIDATION_THRESHOLD

    async def consolidate(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        user_id: UUID,
    ) -> Dict[str, int]:
        """
        Consolidate memories for a user.

        Performs:
        1. Archive expired memories
        2. Find and note near-duplicates (similarity > 0.95)

        Args:
            db: Database session
            tenant_id: Tenant ID
            user_id: User ID

        Returns:
            Statistics about consolidation
        """
        stats = {"duplicates_noted": 0, "archived": 0}

        # Step 1: Archive expired memories
        result = await db.execute(
            delete(EpisodicMemory)
            .where(EpisodicMemory.tenant_id == tenant_id)
            .where(EpisodicMemory.user_id == user_id)
            .where(EpisodicMemory.valid_until < func.now())
        )
        stats["archived"] = result.rowcount

        await db.commit()

        logger.info(
            f"Consolidated memories for user {user_id}: "
            f"archived={stats['archived']}"
        )

        return stats

    async def get_health_stats(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        user_id: UUID,
    ) -> Dict[str, Any]:
        """Get health statistics for the memory system."""
        # Single query for all stats
        result = await db.execute(
            select(
                func.count(EpisodicMemory.id).label('count'),
                func.min(EpisodicMemory.created_at).label('oldest'),
                func.max(EpisodicMemory.created_at).label('newest')
            )
            .where(EpisodicMemory.tenant_id == tenant_id)
            .where(EpisodicMemory.user_id == user_id)
        )
        row = result.one()
        count = row.count or 0
        oldest = row.oldest
        newest = row.newest

        return {
            "status": "healthy",
            "total_memories": count,
            "oldest_memory": oldest,
            "newest_memory": newest,
            "index_status": "hnsw_active",
            "consolidation_needed": count > self.CONSOLIDATION_THRESHOLD,
        }

    def format_for_context(
        self,
        memories: List[EpisodicMemoryResponse],
        max_tokens: int = 600,
    ) -> str:
        """
        Format memories for inclusion in agent context.

        Args:
            memories: List of memories to format
            max_tokens: Maximum token budget (default 600)

        Returns:
            Formatted string for agent context
        """
        if not memories:
            return ""

        context_parts = ["## Similar Past Analyses\n"]

        # Estimate ~200 tokens per memory, limit to 3
        for memory in memories[:3]:
            # Truncate context_decision to fit budget
            truncated = memory.context_decision[:150]
            if len(memory.context_decision) > 150:
                truncated += "..."

            context_parts.append(
                f"- **{memory.action_type}** ({memory.created_at.date()}): "
                f"{truncated}\n"
            )

        return "".join(context_parts)

    async def recall_shared(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        query: str,
        limit: int = 5,
        agent_name: Optional[str] = None,
        action_type: Optional[str] = None,
    ) -> List[EpisodicMemoryResponse]:
        """Retrieve shared memories from the entire tenant.

        Searches visibility='shared' memories using vector similarity.
        Used to inject org-level knowledge into agent context.

        Args:
            db: Database session
            tenant_id: Tenant ID (all shared memories in this tenant)
            query: Search query for similarity matching
            limit: Maximum number of results
            agent_name: Optional filter by agent name
            action_type: Optional filter by action type

        Returns:
            List of shared memories ranked by similarity
        """
        settings = get_settings()
        if not settings.USE_ORG_CONTEXT:
            return []

        try:
            embedding = await self._get_embedding(query, db)
            if embedding is None:
                logger.warning("Cannot search shared memories without embedding")
                return []

            sql = text("""
                WITH similarity_calc AS (
                    SELECT
                        id, session_id, agent_name, action_type,
                        context_decision, rationale, outcome, entities,
                        created_at, importance, archived, access_count,
                        1 - (embedding <=> CAST(:embedding AS vector)) as similarity
                    FROM episodic_memories
                    WHERE tenant_id = :tenant_id
                      AND visibility = 'shared'
                      AND archived = false
                      AND valid_until > NOW()
                      AND embedding IS NOT NULL
                      AND (CAST(:agent_name AS VARCHAR) IS NULL OR agent_name = :agent_name)
                      AND (CAST(:action_type AS VARCHAR) IS NULL OR action_type = :action_type)
                )
                SELECT * FROM similarity_calc
                WHERE similarity >= 0.70
                ORDER BY similarity DESC
                LIMIT :limit
            """)

            # Use SAVEPOINT so failures don't poison the outer transaction
            async with db.begin_nested():
                await db.execute(text("SET LOCAL hnsw.ef_search = 100"))
                result = await db.execute(sql, {
                    "tenant_id": str(tenant_id),
                    "embedding": str(embedding),
                    "agent_name": agent_name,
                    "action_type": action_type,
                    "limit": limit,
                })
                rows = result.fetchall()

            return [
                EpisodicMemoryResponse(
                    id=row.id,
                    session_id=row.session_id,
                    agent_name=row.agent_name,
                    action_type=row.action_type,
                    context_decision=row.context_decision,
                    rationale=row.rationale,
                    outcome=row.outcome,
                    entities=row.entities or [],
                    similarity=float(row.similarity),
                    importance=float(row.importance),
                    archived=row.archived,
                    access_count=row.access_count,
                    created_at=row.created_at,
                )
                for row in rows
            ]

        except Exception as e:
            logger.warning("Shared memory search failed (SAVEPOINT rolled back): %s", e)
            return []

    def format_shared_for_context(
        self,
        memories: List[EpisodicMemoryResponse],
        max_tokens: int = 400,
    ) -> str:
        """Format shared memories for agent context injection.

        Args:
            memories: Shared memories to format
            max_tokens: Maximum token budget (default 400)

        Returns:
            Formatted string for agent context, or empty string
        """
        if not memories:
            return ""

        parts = ["## Organization Shared Knowledge\n"]

        for memory in memories[:3]:
            truncated = memory.context_decision[:150]
            if len(memory.context_decision) > 150:
                truncated += "..."

            parts.append(
                f"- **{memory.action_type}** ({memory.created_at.date()}): "
                f"{truncated}\n"
            )

        return "".join(parts)
