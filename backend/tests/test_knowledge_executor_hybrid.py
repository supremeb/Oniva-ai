"""
Tests for KnowledgeExecutor Hybrid Search integration.
"""

import pytest
from uuid import UUID
from unittest.mock import AsyncMock, patch
from app.node_executors.knowledge_executor import KnowledgeExecutor
from app.node_executors import NodeExecutionContext

TEST_TENANT_ID = "11111111-1111-1111-1111-111111111111"
TEST_USER_ID = "00000000-0000-0000-0000-000000000000"


@pytest.mark.asyncio
async def test_knowledge_executor_hybrid_call():
    """
    Verify that KnowledgeExecutor calls RAGService.hybrid_search_v2 when configured.
    """
    executor = KnowledgeExecutor()
    mock_db = AsyncMock()

    context = NodeExecutionContext(
        node_id="knowledge_1",
        node_type="knowledge",
        node_data={
            "query": "market trends 2024",
            "searchMode": "hybrid",
            "topK": 3
        },
        inputs={},
        variables={},
        user_id=TEST_USER_ID,
        tenant_id=TEST_TENANT_ID,
    )

    # Mock RAGService
    with patch("app.node_executors.knowledge_executor.RAGService") as MockRAGService:
        mock_instance = MockRAGService.return_value
        mock_instance.hybrid_search_v2 = AsyncMock(return_value=[
            {"id": "doc1", "content": "Content 1", "score": 0.9, "metadata": {}},
            {"id": "doc2", "content": "Content 2", "score": 0.8, "metadata": {}}
        ])

        result = await executor.execute(context, mock_db)

        assert result.status == "success"
        assert result.output["count"] == 2
        mock_instance.hybrid_search_v2.assert_called_once_with(
            query="market trends 2024",
            tenant_id=UUID(TEST_TENANT_ID),
            limit=3,
            knowledge_base_ids=None,
        )


@pytest.mark.asyncio
async def test_knowledge_executor_vector_call():
    """
    Verify that KnowledgeExecutor calls RAGService.vector_search when configured.
    """
    executor = KnowledgeExecutor()
    mock_db = AsyncMock()

    context = NodeExecutionContext(
        node_id="knowledge_2",
        node_type="knowledge",
        node_data={
            "query": "fixed income",
            "searchMode": "vector",
            "topK": 5
        },
        inputs={},
        variables={},
        user_id=TEST_USER_ID,
        tenant_id=TEST_TENANT_ID,
    )

    # Mock RAGService
    with patch("app.node_executors.knowledge_executor.RAGService") as MockRAGService:
        mock_instance = MockRAGService.return_value
        mock_instance.vector_search = AsyncMock(return_value=[
            {"id": "doc3", "content": "Content 3", "score": 0.95, "metadata": {}}
        ])

        result = await executor.execute(context, mock_db)

        assert result.status == "success"
        assert result.output["count"] == 1
        mock_instance.vector_search.assert_called_once_with(
            query="fixed income",
            tenant_id=UUID(TEST_TENANT_ID),
            limit=5,
        )
