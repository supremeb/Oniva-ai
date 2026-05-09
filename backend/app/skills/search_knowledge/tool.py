"""
Search Knowledge Skill
Search the knowledge base using RAG (pgvector hybrid search).
"""

from typing import Any, Dict, List
from uuid import UUID

from app.agents.skills.context import SkillContext

import logging

logger = logging.getLogger(__name__)


async def search_knowledge(
    ctx: SkillContext,
    query: str,
    limit: int = 5,
    threshold: float = 0.01,
) -> Dict[str, Any]:
    """
    Search the knowledge base using hybrid search (semantic + keyword via RRF).

    Args:
        ctx: Skill execution context
        query: Search query
        limit: Maximum number of results
        threshold: Minimum similarity threshold (0-1)

    Returns:
        Matching documents with similarity scores
    """
    if not ctx.db or not ctx.tenant_id:
        return {
            "success": False,
            "error": "Database session or tenant not available",
            "results": [],
        }

    try:
        from app.services.rag_service import RAGService

        tenant_id = UUID(str(ctx.tenant_id)) if not isinstance(ctx.tenant_id, UUID) else ctx.tenant_id

        rag_service = RAGService(ctx.db)
        results = await rag_service.hybrid_search_v2(
            query=query,
            tenant_id=tenant_id,
            limit=limit,
        )

        # Filter by threshold and format
        formatted: List[Dict[str, Any]] = []
        for r in results:
            score = r.get("score", 0.0)
            if score < threshold:
                continue
            formatted.append({
                "content": r.get("content", ""),
                "source": r.get("document_name") or r.get("document_id", ""),
                "similarity": score,
                "chunk_id": r.get("id", ""),
                "document_id": r.get("document_id", ""),
                "knowledge_base_id": r.get("knowledge_base_id", ""),
            })

        return {
            "success": True,
            "query": query,
            "results": formatted,
            "total": len(formatted),
            "limit": limit,
            "threshold": threshold,
        }
    except Exception as e:
        logger.error(f"search_knowledge failed: {e}")
        return {
            "success": False,
            "error": str(e),
            "results": [],
        }
