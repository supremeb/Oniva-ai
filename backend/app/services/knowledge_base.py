"""Knowledge Base service for CRUD operations and search.

Part of Frontier-Inspired Platform Simplification (Phase C).
"""
from typing import Optional
from uuid import UUID

from sqlalchemy import select, func, update, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.knowledge_base import KnowledgeBase
from app.models.knowledge_document import KnowledgeDocument
from app.models.knowledge_chunk import KnowledgeChunk

logger = get_logger(__name__)

# Tenant limits
MAX_KBS_PER_TENANT = 50
MAX_DOCS_PER_KB = 500


class KnowledgeBaseService:
    """Service for knowledge base CRUD and vector search."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_kb(
        self,
        name: str,
        tenant_id: UUID,
        description: Optional[str] = None,
        team_id: Optional[UUID] = None,
        created_by: Optional[UUID] = None,
    ) -> KnowledgeBase:
        """Create a new knowledge base."""
        # Check tenant limit
        count_result = await self.db.execute(
            select(func.count(KnowledgeBase.id)).where(
                KnowledgeBase.tenant_id == tenant_id
            )
        )
        current_count = count_result.scalar_one()
        if current_count >= MAX_KBS_PER_TENANT:
            raise ValueError(f"Maximum of {MAX_KBS_PER_TENANT} knowledge bases per tenant reached")

        kb = KnowledgeBase(
            name=name,
            description=description,
            tenant_id=tenant_id,
            team_id=team_id,
            created_by=created_by,
            status="empty",
        )
        self.db.add(kb)
        await self.db.flush()
        logger.info(f"Created knowledge base '{name}' (id={kb.id})")
        return kb

    async def list_kbs(
        self,
        tenant_id: UUID,
        team_id: Optional[UUID] = None,
    ) -> list[KnowledgeBase]:
        """List knowledge bases, optionally filtered by team."""
        query = select(KnowledgeBase).where(
            KnowledgeBase.tenant_id == tenant_id,
            KnowledgeBase.source_type == "user",
        ).order_by(KnowledgeBase.updated_at.desc())

        if team_id:
            query = query.where(KnowledgeBase.team_id == team_id)

        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_kb(
        self,
        kb_id: UUID,
        tenant_id: UUID,
    ) -> Optional[KnowledgeBase]:
        """Get a single KB with document counts."""
        result = await self.db.execute(
            select(KnowledgeBase).where(
                KnowledgeBase.id == kb_id,
                KnowledgeBase.tenant_id == tenant_id,
            )
        )
        return result.scalar_one_or_none()

    async def update_kb(
        self,
        kb_id: UUID,
        tenant_id: UUID,
        name: Optional[str] = None,
        description: Optional[str] = None,
        team_id: Optional[UUID] = None,
    ) -> Optional[KnowledgeBase]:
        """Update KB metadata."""
        kb = await self.get_kb(kb_id, tenant_id)
        if not kb:
            return None

        if name is not None:
            kb.name = name
        if description is not None:
            kb.description = description
        if team_id is not None:
            kb.team_id = team_id

        await self.db.flush()
        return kb

    async def delete_kb(
        self,
        kb_id: UUID,
        tenant_id: UUID,
    ) -> bool:
        """Delete a KB and cascade to documents and chunks."""
        kb = await self.get_kb(kb_id, tenant_id)
        if not kb:
            return False

        await self.db.delete(kb)
        await self.db.flush()
        logger.info(f"Deleted knowledge base '{kb.name}' (id={kb_id})")
        return True

    async def search_kb(
        self,
        kb_id: UUID,
        tenant_id: UUID,
        query_embedding: list[float],
        limit: int = 5,
    ) -> list[dict]:
        """Search a single KB by vector similarity."""

        # Set ef_search for HNSW accuracy (matches episodic memory setting)
        await self.db.execute(text("SET LOCAL hnsw.ef_search = 100"))

        result = await self.db.execute(
            select(
                KnowledgeChunk.id,
                KnowledgeChunk.content,
                KnowledgeChunk.chunk_index,
                KnowledgeChunk.chunk_metadata,
                KnowledgeChunk.document_id,
                KnowledgeChunk.embedding.cosine_distance(query_embedding).label("distance"),
            ).where(
                KnowledgeChunk.knowledge_base_id == kb_id,
                KnowledgeChunk.tenant_id == tenant_id,
                KnowledgeChunk.embedding.isnot(None),
            ).order_by(
                "distance"
            ).limit(limit)
        )

        rows = result.all()
        return [
            {
                "id": str(row.id),
                "content": row.content,
                "chunk_index": row.chunk_index,
                "metadata": row.chunk_metadata or {},
                "document_id": str(row.document_id),
                "score": 1.0 - float(row.distance),  # Convert distance to similarity
            }
            for row in rows
        ]

    async def search_multiple_kbs(
        self,
        kb_ids: list[UUID],
        tenant_id: UUID,
        query_embedding: list[float],
        limit: int = 5,
    ) -> list[dict]:
        """Search across multiple KBs by vector similarity."""
        if not kb_ids:
            return []

        await self.db.execute(text("SET LOCAL hnsw.ef_search = 100"))

        result = await self.db.execute(
            select(
                KnowledgeChunk.id,
                KnowledgeChunk.content,
                KnowledgeChunk.chunk_index,
                KnowledgeChunk.chunk_metadata,
                KnowledgeChunk.document_id,
                KnowledgeChunk.knowledge_base_id,
                KnowledgeChunk.embedding.cosine_distance(query_embedding).label("distance"),
            ).where(
                KnowledgeChunk.knowledge_base_id.in_(kb_ids),
                KnowledgeChunk.tenant_id == tenant_id,
                KnowledgeChunk.embedding.isnot(None),
            ).order_by(
                "distance"
            ).limit(limit)
        )

        rows = result.all()
        return [
            {
                "id": str(row.id),
                "content": row.content,
                "chunk_index": row.chunk_index,
                "metadata": row.chunk_metadata or {},
                "document_id": str(row.document_id),
                "knowledge_base_id": str(row.knowledge_base_id),
                "score": 1.0 - float(row.distance),
            }
            for row in rows
        ]

    async def update_kb_counts(self, kb_id: UUID, tenant_id: UUID) -> None:
        """Recalculate and update document_count and total_chunks for a KB."""
        doc_count_result = await self.db.execute(
            select(func.count(KnowledgeDocument.id)).where(
                KnowledgeDocument.knowledge_base_id == kb_id,
                KnowledgeDocument.tenant_id == tenant_id,
                KnowledgeDocument.status == "ready",
            )
        )
        doc_count = doc_count_result.scalar_one()

        chunk_count_result = await self.db.execute(
            select(func.count(KnowledgeChunk.id)).where(
                KnowledgeChunk.knowledge_base_id == kb_id,
                KnowledgeChunk.tenant_id == tenant_id,
            )
        )
        total_chunks = chunk_count_result.scalar_one()

        await self.db.execute(
            update(KnowledgeBase).where(
                KnowledgeBase.id == kb_id,
                KnowledgeBase.tenant_id == tenant_id,
            ).values(
                document_count=doc_count,
                total_chunks=total_chunks,
                status="ready" if doc_count > 0 else "empty",
            )
        )
        await self.db.flush()
