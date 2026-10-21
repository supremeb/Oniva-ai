"""
RAG Service - Handles document retrieval using Hybrid Search (Vector + BM25)
Supports query augmentation with HyDE and multi-query for improved retrieval.
"""

import logging
from typing import List, Dict, Any, Optional
from uuid import UUID

import numpy as np
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.encryption import get_encryption_service
from app.models.llm_provider import LLMProvider
from app.services import llm_direct
from app.services.embedding_cache import get_embedding_cache
from app.services.llm_direct import DirectProvider
from app.services.llm_gateway import get_provider_from_model, normalize_model_id
from app.services.query_augmentation import get_query_augmentation_service

settings = get_settings()
logger = logging.getLogger(__name__)


class RAGService:
    """
    Service for Hybrid Retrieval Augmented Generation.
    Combines pgvector (semantic) and pg_textsearch (BM25) results.
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.cache = get_embedding_cache()

    async def _resolve_embedding_config(self) -> tuple[str, str | None, str | None]:
        """Get embedding model + API key + provider_type from tenant's LLM provider, or fall back to config.

        Returns (model, api_key, provider_type).
        """
        try:
            stmt = (
                select(LLMProvider)
                .where(
                    LLMProvider.is_enabled == True,
                    LLMProvider.embedding_model.isnot(None),
                    LLMProvider.embedding_model != "",
                )
                .order_by(LLMProvider.priority)
                .limit(1)
            )
            result = await self.db.execute(stmt)
            provider = result.scalar_one_or_none()
            if provider:
                model = provider.embedding_model
                api_key = None
                if provider.api_key_encrypted:
                    encryption = get_encryption_service()
                    api_key = encryption.decrypt(provider.api_key_encrypted)
                return model, api_key, provider.provider_type
        except Exception as e:
            logger.warning(f"Failed to resolve embedding config from providers: {e}")

        # Fallback to env config — infer provider from the configured embedding model
        model = normalize_model_id(settings.EMBEDDING_MODEL)
        provider_type = get_provider_from_model(model)
        env_keys = {
            "openai": settings.OPENAI_API_KEY,
            "anthropic": settings.ANTHROPIC_API_KEY,
            "openrouter": settings.OPENROUTER_API_KEY,
        }
        api_key = env_keys.get(provider_type) or settings.OPENAI_API_KEY
        return model, api_key, provider_type

    async def get_embedding(self, text_input: str, model: str | None = None) -> Optional[List[float]]:
        """Generate embedding for query text with Redis caching.

        Uses OpenRouter's embeddings API directly for openrouter providers and the
        native provider SDK (``llm_direct.embed``) for OpenAI and the others.

        Returns None on failure (callers must handle gracefully).
        """
        api_key: str | None = None
        provider_type: str | None = None
        if model is None:
            model, api_key, provider_type = await self._resolve_embedding_config()

        # Check cache first
        cached = await self.cache.get(text_input, model)
        if cached is not None:
            logger.debug(f"Embedding cache hit for text: {text_input[:50]}...")
            return cached

        try:
            # Resolve API key if not already set by _resolve_embedding_config
            if api_key is None:
                provider_type = get_provider_from_model(normalize_model_id(model))
                env_keys = {
                    "openai": settings.OPENAI_API_KEY,
                    "anthropic": settings.ANTHROPIC_API_KEY,
                    "openrouter": settings.OPENROUTER_API_KEY,
                }
                api_key = env_keys.get(provider_type) or settings.OPENAI_API_KEY

            if provider_type == "openrouter":
                embedding = await self._openrouter_embedding(model, text_input, api_key)
            else:
                provider = DirectProvider(provider_type=provider_type, api_key=api_key)
                vectors = await llm_direct.embed([text_input], provider, model=model)
                embedding = vectors[0]

            # Truncate to 1536 dims if model returns larger vectors
            # (pgvector column is Vector(1536))
            if embedding and len(embedding) > 1536:
                embedding = embedding[:1536]

            # Cache the result
            await self.cache.set(text_input, embedding, model)
            logger.debug(f"Cached new embedding for text: {text_input[:50]}...")

            return embedding
        except Exception as e:
            logger.error(f"Error generating embedding: {e}")
            return None

    async def _openrouter_embedding(
        self, model: str, text_input: str, api_key: str
    ) -> List[float]:
        """Call OpenRouter's OpenAI-compatible embeddings API directly."""
        import httpx

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/embeddings",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={"model": model, "input": text_input},
            )
            resp.raise_for_status()
            data = resp.json()
            return data["data"][0]["embedding"]

    # ------------------------------------------------------------------
    # hybrid_search_v2: queries knowledge_chunks with tenant isolation
    # ------------------------------------------------------------------

    async def hybrid_search_v2(
        self,
        query: str,
        tenant_id: UUID,
        limit: int = 5,
        k: int = 60,
        knowledge_base_ids: Optional[List[UUID]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Hybrid search over knowledge_chunks table with tenant isolation.

        Uses RRF 4:1 (vector:BM25) fusion, ef_search=100.

        Args:
            query: User query
            tenant_id: Tenant ID (REQUIRED, parameterized)
            limit: Number of results to return
            k: RRF fusion parameter
            knowledge_base_ids: Optional list of KB IDs to scope search

        Returns:
            List of chunk dicts with relevance scores
        """
        query_vector = await self.get_embedding(query)
        if query_vector is None:
            logger.warning("Embedding generation failed, skipping search")
            return []

        kb_filter = ""
        if knowledge_base_ids:
            kb_filter = "AND knowledge_base_id = ANY(:kb_ids)"

        sql = text(f"""
            WITH keyword_results AS (
                SELECT id, content, document_id, knowledge_base_id, chunk_metadata,
                       ROW_NUMBER() OVER (
                           ORDER BY ts_rank(to_tsvector('english', content), plainto_tsquery('english', :query)) DESC
                       ) as rank
                FROM knowledge_chunks
                WHERE to_tsvector('english', content) @@ plainto_tsquery('english', :query)
                  AND tenant_id = :tenant_id
                  AND embedding IS NOT NULL
                  {kb_filter}
                LIMIT 50
            ),
            vector_results AS (
                SELECT id, content, document_id, knowledge_base_id, chunk_metadata,
                       ROW_NUMBER() OVER (ORDER BY embedding <=> :vector) as rank
                FROM knowledge_chunks
                WHERE embedding IS NOT NULL
                  AND tenant_id = :tenant_id
                  {kb_filter}
                LIMIT 50
            ),
            merged AS (
                SELECT
                    COALESCE(k.id, v.id) as id,
                    COALESCE(k.content, v.content) as content,
                    COALESCE(k.document_id, v.document_id) as document_id,
                    COALESCE(k.knowledge_base_id, v.knowledge_base_id) as knowledge_base_id,
                    COALESCE(k.chunk_metadata, v.chunk_metadata) as chunk_metadata,
                    (4.0 * COALESCE(1.0 / (:k + v.rank), 0.0) + COALESCE(1.0 / (:k + k.rank), 0.0)) as rrf_score
                FROM keyword_results k
                FULL OUTER JOIN vector_results v ON k.id = v.id
                ORDER BY rrf_score DESC
                LIMIT :limit
            )
            SELECT m.*, d.filename as document_name
            FROM merged m
            LEFT JOIN knowledge_documents d ON d.id = m.document_id
        """)

        params: Dict[str, Any] = {
            "query": query,
            "vector": str(query_vector),
            "k": k,
            "limit": limit,
            "tenant_id": str(tenant_id),
        }

        if knowledge_base_ids:
            params["kb_ids"] = [str(kb_id) for kb_id in knowledge_base_ids]

        # Use a SAVEPOINT so that SQL failures here don't poison the
        # outer transaction (which is shared with quota checks, etc.).
        try:
            async with self.db.begin_nested():
                await self.db.execute(text("SET LOCAL hnsw.ef_search = 100"))
                result = await self.db.execute(sql, params)
                rows = result.all()

            return [
                {
                    "id": str(row.id),
                    "content": row.content,
                    "document_id": str(row.document_id),
                    "knowledge_base_id": str(row.knowledge_base_id),
                    "metadata": row.chunk_metadata or {},
                    "score": float(row.rrf_score),
                    "document_name": getattr(row, "document_name", None),
                }
                for row in rows
            ]
        except Exception as e:
            logger.error(f"Error executing hybrid_search_v2: {e}")
            return []

    async def _rerank_results(
        self,
        original_query: str,
        results: List[Dict[str, Any]],
        limit: int,
    ) -> List[Dict[str, Any]]:
        """Re-rank merged results by relevance to original query."""
        if not results:
            return results

        query_vector = await self.get_embedding(original_query)
        if query_vector is None:
            return results[:limit]

        query_np = np.array(query_vector)

        for result in results:
            if "embedding" in result and result["embedding"]:
                doc_np = np.array(result["embedding"])
                similarity = np.dot(query_np, doc_np) / (
                    np.linalg.norm(query_np) * np.linalg.norm(doc_np) + 1e-8
                )
                result["_rerank_score"] = float(similarity)
            else:
                result["_rerank_score"] = result.get("score", 0)

        results.sort(key=lambda x: x.get("_rerank_score", 0), reverse=True)

        for result in results:
            result.pop("_rerank_score", None)

        return results[:limit]

    async def vector_search(self, query: str, tenant_id: Optional[UUID] = None, limit: int = 5) -> List[Dict[str, Any]]:
        """Pure semantic search using pgvector on legacy documents table."""
        query_vector = await self.get_embedding(query)
        if query_vector is None:
            logger.warning("Embedding generation failed, skipping vector search")
            return []

        tenant_filter = "AND tenant_id = :tenant_id" if tenant_id else ""
        sql = text(f"""
            SELECT id, content, source, metadata, (1 - (embedding <=> :vector)) as score
            FROM documents
            WHERE embedding IS NOT NULL {tenant_filter}
            ORDER BY embedding <=> :vector
            LIMIT :limit
        """)

        params: Dict[str, Any] = {"vector": str(query_vector), "limit": limit}
        if tenant_id:
            params["tenant_id"] = str(tenant_id)

        result = await self.db.execute(sql, params)
        rows = result.all()

        return [
            {
                "id": str(row.id),
                "content": row.content,
                "score": float(row.score),
                "metadata": row.metadata,
            }
            for row in rows
        ]
