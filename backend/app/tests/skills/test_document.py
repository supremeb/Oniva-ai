
import pytest
from unittest.mock import MagicMock, patch

from app.agents.skills.document import (
    parse_pdf,
    extract_entities,
    summarize_document
)
from pydantic_ai import RunContext
from app.agents.base import AgentDependencies


@pytest.fixture
def ctx():
    """Create a mock RunContext with AgentDependencies."""
    mock_ctx = MagicMock(spec=RunContext)
    mock_ctx.deps = MagicMock(spec=AgentDependencies)
    return mock_ctx


class TestDocumentSkills:

    @pytest.mark.asyncio
    @patch("app.agents.skills.document.PdfReader")
    @patch("app.agents.skills.document.os.path.exists")
    @patch("app.agents.skills.document.is_path_allowed")
    async def test_parse_pdf(self, mock_allowed, mock_exists, mock_reader, ctx):
        mock_allowed.return_value = True
        mock_exists.return_value = True

        # Mock PDF Reader
        mock_pdf = MagicMock()
        mock_pdf.pages = [MagicMock(), MagicMock()]
        mock_pdf.pages[0].extract_text.return_value = "Page 1 Content."
        mock_pdf.pages[1].extract_text.return_value = "Page 2 Content."
        mock_reader.return_value = mock_pdf

        result = await parse_pdf(ctx, path="/tmp/test.pdf")

        assert result["success"]
        assert "Page 1 Content" in result["text"]
        assert result["pages_extracted"] == 2

    @pytest.mark.asyncio
    async def test_extract_entities(self, ctx):
        text = "Invoice for $1,500.00 dated 2023-10-25 paid to Acme Corp."
        result = await extract_entities(ctx, text=text)

        assert result["success"]
        assert "2023-10-25" in result["entities"]["dates"]
        assert "$1,500.00" in result["entities"]["money"]

    @pytest.mark.asyncio
    @patch("app.config.get_settings")
    async def test_summarize_document(self, mock_get_settings, ctx):
        mock_settings = MagicMock()
        mock_settings.USE_CONTEXT_SUMMARIZATION = False
        mock_get_settings.return_value = mock_settings

        text = "Sentence one. Sentence two. Sentence three. Sentence four."
        result = await summarize_document(ctx, text=text)

        assert result["success"]
        assert result["summary"].endswith(".")
        assert len(result["summary"]) < len(text) + 5  # +5 for "..." logic margin
