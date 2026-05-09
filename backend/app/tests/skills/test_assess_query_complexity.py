"""Tests for the assess_query_complexity skill."""

import pytest
from unittest.mock import MagicMock

from app.skills.assess_query_complexity.tool import assess_query_complexity


@pytest.fixture
def mock_ctx():
    return MagicMock()


@pytest.mark.asyncio
class TestAssessQueryComplexity:
    async def test_deep_work_recommended(self, mock_ctx):
        result = await assess_query_complexity(
            mock_ctx,
            needs_deep_work=True,
            reasoning="Multi-step research across SEC filings required",
        )
        assert result["recommendation"] == "deep_work"
        assert "SEC filings" in result["reasoning"]

    async def test_standard_response(self, mock_ctx):
        result = await assess_query_complexity(
            mock_ctx,
            needs_deep_work=False,
            reasoning="Simple factual question",
        )
        assert result["recommendation"] == "standard"
        assert "Simple factual" in result["reasoning"]

    async def test_empty_reasoning(self, mock_ctx):
        result = await assess_query_complexity(
            mock_ctx,
            needs_deep_work=False,
            reasoning="",
        )
        assert result["recommendation"] == "standard"
        assert result["reasoning"] == ""

    async def test_deep_work_returns_message(self, mock_ctx):
        result = await assess_query_complexity(
            mock_ctx,
            needs_deep_work=True,
            reasoning="Needs comparison of 5 companies",
        )
        assert "message" in result
        assert "recommend" in result["message"].lower()
