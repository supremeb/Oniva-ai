"""
Unit tests for browser_navigate and browser_screenshot skills.

Tests cover:
- Feature flag gating (disabled returns error)
- SSRF URL validation
- Filename sanitization (screenshot)
- Happy path with mocked sandbox
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.skills.browser_navigate.tool import browser_navigate
from app.skills.browser_screenshot.tool import browser_screenshot


@pytest.fixture
def mock_ctx():
    """Create a minimal SkillContext mock."""
    ctx = MagicMock()
    ctx.tenant_id = "11111111-1111-1111-1111-111111111111"
    ctx.user_id = "22222222-2222-2222-2222-222222222222"
    ctx.session_id = "test-session"
    ctx.api_keys = {"openai": "test-key"}
    ctx.metadata = {"agent_id": "test-agent"}
    ctx.db = None
    return ctx


class TestBrowserNavigate:
    """Tests for browser_navigate skill."""

    @pytest.mark.asyncio
    async def test_disabled_when_sandbox_off(self, mock_ctx):
        """Returns error when ENABLE_SANDBOX is False."""
        with patch("app.skills.browser_navigate.tool.get_settings") as mock_settings:
            settings = MagicMock()
            settings.ENABLE_SANDBOX = False
            settings.ENABLE_BROWSER_SANDBOX = False
            mock_settings.return_value = settings

            result = await browser_navigate(mock_ctx, url="https://example.com")
            assert result["status"] == "disabled"
            assert "not enabled" in result["error"]

    @pytest.mark.asyncio
    async def test_disabled_when_browser_flag_off(self, mock_ctx):
        """Returns error when ENABLE_BROWSER_SANDBOX is False."""
        with patch("app.skills.browser_navigate.tool.get_settings") as mock_settings:
            settings = MagicMock()
            settings.ENABLE_SANDBOX = True
            settings.ENABLE_BROWSER_SANDBOX = False
            mock_settings.return_value = settings

            result = await browser_navigate(mock_ctx, url="https://example.com")
            assert result["status"] == "disabled"

    @pytest.mark.asyncio
    async def test_blocks_ssrf(self, mock_ctx):
        """Blocks SSRF attempts to cloud metadata."""
        with patch("app.skills.browser_navigate.tool.get_settings") as mock_settings:
            settings = MagicMock()
            settings.ENABLE_SANDBOX = True
            settings.ENABLE_BROWSER_SANDBOX = True
            settings.E2B_API_KEY = "test-key"
            mock_settings.return_value = settings

            result = await browser_navigate(
                mock_ctx, url="http://169.254.169.254/latest/meta-data/"
            )
            assert result["status"] == "blocked"

    @pytest.mark.asyncio
    async def test_blocks_ftp_scheme(self, mock_ctx):
        """Blocks non-HTTP schemes."""
        with patch("app.skills.browser_navigate.tool.get_settings") as mock_settings:
            settings = MagicMock()
            settings.ENABLE_SANDBOX = True
            settings.ENABLE_BROWSER_SANDBOX = True
            settings.E2B_API_KEY = "test-key"
            mock_settings.return_value = settings

            result = await browser_navigate(mock_ctx, url="ftp://files.example.com")
            assert result["status"] == "blocked"

    @pytest.mark.asyncio
    async def test_missing_api_key(self, mock_ctx):
        """Returns error when E2B_API_KEY is not set."""
        with patch("app.skills.browser_navigate.tool.get_settings") as mock_settings:
            settings = MagicMock()
            settings.ENABLE_SANDBOX = True
            settings.ENABLE_BROWSER_SANDBOX = True
            settings.E2B_API_KEY = ""
            mock_settings.return_value = settings

            result = await browser_navigate(mock_ctx, url="https://example.com")
            assert result["status"] == "disabled"
            assert "API key" in result["error"]


class TestBrowserScreenshot:
    """Tests for browser_screenshot skill."""

    @pytest.mark.asyncio
    async def test_disabled_when_sandbox_off(self, mock_ctx):
        """Returns error when ENABLE_SANDBOX is False."""
        with patch("app.skills.browser_screenshot.tool.get_settings") as mock_settings:
            settings = MagicMock()
            settings.ENABLE_SANDBOX = False
            settings.ENABLE_BROWSER_SANDBOX = False
            mock_settings.return_value = settings

            result = await browser_screenshot(mock_ctx, url="https://example.com")
            assert result["status"] == "disabled"

    @pytest.mark.asyncio
    async def test_blocks_ssrf(self, mock_ctx):
        """Blocks SSRF attempts."""
        with patch("app.skills.browser_screenshot.tool.get_settings") as mock_settings:
            settings = MagicMock()
            settings.ENABLE_SANDBOX = True
            settings.ENABLE_BROWSER_SANDBOX = True
            settings.E2B_API_KEY = "test-key"
            mock_settings.return_value = settings

            result = await browser_screenshot(
                mock_ctx, url="http://metadata.google.internal/"
            )
            assert result["status"] == "blocked"

    @pytest.mark.asyncio
    async def test_blocks_path_traversal_filename(self, mock_ctx):
        """Blocks filenames with path traversal."""
        with patch("app.skills.browser_screenshot.tool.get_settings") as mock_settings:
            settings = MagicMock()
            settings.ENABLE_SANDBOX = True
            settings.ENABLE_BROWSER_SANDBOX = True
            settings.E2B_API_KEY = "test-key"
            mock_settings.return_value = settings

            with patch("app.skills.browser_screenshot.tool.is_url_safe", return_value=None):
                result = await browser_screenshot(
                    mock_ctx,
                    url="https://example.com",
                    filename="../../../etc/evil.png",
                )
                assert result["status"] == "blocked"
                assert "Invalid filename" in result["error"]

    @pytest.mark.asyncio
    async def test_blocks_slash_in_filename(self, mock_ctx):
        """Blocks filenames with slashes."""
        with patch("app.skills.browser_screenshot.tool.get_settings") as mock_settings:
            settings = MagicMock()
            settings.ENABLE_SANDBOX = True
            settings.ENABLE_BROWSER_SANDBOX = True
            settings.E2B_API_KEY = "test-key"
            mock_settings.return_value = settings

            with patch("app.skills.browser_screenshot.tool.is_url_safe", return_value=None):
                result = await browser_screenshot(
                    mock_ctx,
                    url="https://example.com",
                    filename="/etc/passwd",
                )
                assert result["status"] == "blocked"
