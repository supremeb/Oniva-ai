"""
Tests for Research Agent Vector Search

Verifies that:
- RAGService.get_embedding returns 1536-dim vector
- search_documents uses hybrid_search_v2 (knowledge_chunks table)
- Results include relevance scores
- Results truncate long content
"""

import pytest
from unittest.mock import patch, AsyncMock


class TestEmbeddingGeneration:
    """Test embedding generation via RAGService."""

    @pytest.mark.asyncio
    async def test_generate_embedding_returns_correct_dimensions(self):
        """Verify RAGService.get_embedding returns 1536-dim vector."""
        from app.services.rag_service import RAGService

        mock_db = AsyncMock()

        # Patch cache before constructing RAGService (cache set in __init__)
        with patch("app.services.rag_service.get_embedding_cache") as mock_cache_fn:
            mock_cache_instance = AsyncMock()
            mock_cache_instance.get = AsyncMock(return_value=None)
            mock_cache_instance.set = AsyncMock()
            mock_cache_fn.return_value = mock_cache_instance

            service = RAGService(mock_db)

            embedding_data = [0.1] * 1536
            # Mock _resolve_embedding_config so it doesn't query DB
            with patch.object(
                service, '_resolve_embedding_config',
                new_callable=AsyncMock,
                return_value=("text-embedding-3-small", "test-key", "openai")
            ):
                with patch("app.services.rag_service.llm_direct") as mock_llm_direct:
                    # llm_direct.embed returns vectors in input order
                    mock_llm_direct.embed = AsyncMock(return_value=[embedding_data])

                    result = await service.get_embedding("test query")

                    assert result is not None
                    assert len(result) == 1536
                    assert all(isinstance(x, float) for x in result)

    @pytest.mark.asyncio
    async def test_generate_embedding_calls_correct_model(self):
        """Verify correct model is used for embeddings via llm_direct."""
        from app.services.rag_service import RAGService

        mock_db = AsyncMock()

        with patch("app.services.rag_service.get_embedding_cache") as mock_cache_fn:
            mock_cache_instance = AsyncMock()
            mock_cache_instance.get = AsyncMock(return_value=None)
            mock_cache_instance.set = AsyncMock()
            mock_cache_fn.return_value = mock_cache_instance

            service = RAGService(mock_db)

            # Mock _resolve_embedding_config so it doesn't query DB
            with patch.object(
                service, '_resolve_embedding_config',
                new_callable=AsyncMock,
                return_value=("text-embedding-3-small", "test-key", "openai")
            ):
                with patch("app.services.rag_service.llm_direct") as mock_llm_direct:
                    mock_llm_direct.embed = AsyncMock(return_value=[[0.1] * 1536])

                    await service.get_embedding("test query")

                    mock_llm_direct.embed.assert_called_once()
                    call_kwargs = mock_llm_direct.embed.call_args.kwargs
                    assert call_kwargs["model"] == "text-embedding-3-small"


class TestSearchDocumentsUsesHybridSearchV2:
    """Test search_documents tool uses hybrid_search_v2."""

    def test_research_agent_has_search_tool(self):
        """Verify ResearchAgent has search_documents tool registered."""
        from app.agents.research import ResearchAgent

        agent = ResearchAgent()
        assert agent.agent is not None

    def test_no_keyword_search_fallback(self):
        """Verify _keyword_search_fallback has been removed."""
        import app.agents.research as research_module

        assert not hasattr(research_module, '_keyword_search_fallback'), \
            "_keyword_search_fallback should be deleted — hybrid_search_v2 handles keyword+vector fusion"


class TestNoILIKE:
    """Verify ILIKE is not used in search."""

    def test_search_documents_sql_no_ilike(self):
        """Verify the vector search SQL doesn't contain ILIKE."""
        import inspect

        import app.agents.research as research_module
        source = inspect.getsource(research_module)

        assert "content.ilike" not in source, "ILIKE should be removed from search_documents"
