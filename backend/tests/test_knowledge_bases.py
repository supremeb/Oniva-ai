"""
Unit tests for Knowledge Base API.

Tests cover:
- Listing knowledge bases from document metadata
- Empty database handling
- Response structure validation
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from collections import namedtuple
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.knowledge_bases import list_knowledge_bases


# Create a named tuple to simulate database row
KBRow = namedtuple("KBRow", ["id", "name", "document_count"])


def _make_mock_request(tenant_id="test-tenant-id"):
    """Create a mock Request with tenant_id on request.state."""
    mock_request = MagicMock()
    mock_request.state.tenant_id = tenant_id
    return mock_request


class TestKnowledgeBasesAPI:
    """Tests for the knowledge bases API endpoint."""

    @pytest.mark.asyncio
    async def test_list_knowledge_bases_returns_aggregated_data(self):
        """Test that knowledge bases are aggregated from document metadata."""
        # Mock database result using namedtuple to simulate fetchall() rows
        mock_rows = [
            KBRow(id="kb-finance", name="Finance Documents", document_count=15),
            KBRow(id="kb-legal", name="Legal Contracts", document_count=8),
        ]

        mock_result = MagicMock()
        mock_result.fetchall.return_value = mock_rows

        mock_db = AsyncMock(spec=AsyncSession)
        mock_db.execute.return_value = mock_result

        response = await list_knowledge_bases(request=_make_mock_request(), db=mock_db)

        assert response.count == 2
        assert len(response.knowledge_bases) == 2
        assert response.knowledge_bases[0].id == "kb-finance"
        assert response.knowledge_bases[0].name == "Finance Documents"
        assert response.knowledge_bases[0].document_count == 15
        assert response.knowledge_bases[1].id == "kb-legal"

    @pytest.mark.asyncio
    async def test_list_knowledge_bases_empty_database(self):
        """Test that empty database returns empty list."""
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []

        mock_db = AsyncMock(spec=AsyncSession)
        mock_db.execute.return_value = mock_result

        response = await list_knowledge_bases(request=_make_mock_request(), db=mock_db)

        assert response.count == 0
        assert response.knowledge_bases == []

    @pytest.mark.asyncio
    async def test_list_knowledge_bases_uses_kb_name_fallback(self):
        """Test that kb_id is used when kb_name is missing."""
        # When kb_name is NULL, the SQL COALESCE returns kb_id
        mock_rows = [
            KBRow(id="kb-unnamed", name="kb-unnamed", document_count=5),
        ]

        mock_result = MagicMock()
        mock_result.fetchall.return_value = mock_rows

        mock_db = AsyncMock(spec=AsyncSession)
        mock_db.execute.return_value = mock_result

        response = await list_knowledge_bases(request=_make_mock_request(), db=mock_db)

        assert response.knowledge_bases[0].name == "kb-unnamed"

    @pytest.mark.asyncio
    async def test_list_knowledge_bases_sorted_by_name(self):
        """Test that results are sorted by name."""
        mock_rows = [
            KBRow(id="kb-a", name="Alpha KB", document_count=1),
            KBRow(id="kb-b", name="Beta KB", document_count=2),
            KBRow(id="kb-c", name="Charlie KB", document_count=3),
        ]

        mock_result = MagicMock()
        mock_result.fetchall.return_value = mock_rows

        mock_db = AsyncMock(spec=AsyncSession)
        mock_db.execute.return_value = mock_result

        response = await list_knowledge_bases(request=_make_mock_request(), db=mock_db)

        names = [kb.name for kb in response.knowledge_bases]
        assert names == ["Alpha KB", "Beta KB", "Charlie KB"]

    @pytest.mark.asyncio
    async def test_list_knowledge_bases_executes_correct_query(self):
        """Test that the correct SQL query is executed."""
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []

        mock_db = AsyncMock(spec=AsyncSession)
        mock_db.execute.return_value = mock_result

        await list_knowledge_bases(request=_make_mock_request(), db=mock_db)

        # Verify execute was called
        mock_db.execute.assert_called_once()

        # Get the SQL text that was executed
        call_args = mock_db.execute.call_args
        sql_text = str(call_args[0][0])

        # Verify key parts of the query
        assert "doc_metadata->>'kb_id'" in sql_text
        assert "doc_metadata->>'kb_name'" in sql_text
        assert "GROUP BY" in sql_text
        assert "ORDER BY" in sql_text
