"""
Unit tests for SandboxToolRouter.

Tests cover:
- Tool schema generation with/without browser flag
- Tool routing to correct handlers
- SSRF validation in browser tools
- Shell exec routing
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.deep_work.sandbox_tools import (
    SHELL_EXEC_TOOL,
    BROWSER_NAV_TOOL,
    BROWSER_SCREENSHOT_TOOL,
    SANDBOX_TOOL_NAMES,
    get_sandbox_tools,
    execute_sandbox_tool,
)


class TestGetSandboxTools:
    """Tests for get_sandbox_tools schema generation."""

    def test_returns_shell_exec_by_default(self):
        """Always includes shell_exec."""
        with patch("app.services.deep_work.sandbox_tools.get_settings") as mock:
            mock.return_value = MagicMock(ENABLE_BROWSER_SANDBOX=False)
            tools = get_sandbox_tools()
            names = [t["function"]["name"] for t in tools]
            assert "shell_exec" in names
            assert len(tools) == 1

    def test_includes_browser_when_flag_enabled(self):
        """Includes browser tools when ENABLE_BROWSER_SANDBOX is True."""
        with patch("app.services.deep_work.sandbox_tools.get_settings") as mock:
            mock.return_value = MagicMock(ENABLE_BROWSER_SANDBOX=True)
            tools = get_sandbox_tools()
            names = [t["function"]["name"] for t in tools]
            assert "shell_exec" in names
            assert "browser_navigate" in names
            assert "browser_screenshot" in names
            assert len(tools) == 3

    def test_include_browser_override(self):
        """include_browser=True overrides feature flag."""
        with patch("app.services.deep_work.sandbox_tools.get_settings") as mock:
            mock.return_value = MagicMock(ENABLE_BROWSER_SANDBOX=False)
            tools = get_sandbox_tools(include_browser=True)
            names = [t["function"]["name"] for t in tools]
            assert "browser_navigate" in names
            assert len(tools) == 3


class TestSandboxToolNames:
    """Tests for SANDBOX_TOOL_NAMES constant."""

    def test_contains_all_tools(self):
        assert "shell_exec" in SANDBOX_TOOL_NAMES
        assert "browser_navigate" in SANDBOX_TOOL_NAMES
        assert "browser_screenshot" in SANDBOX_TOOL_NAMES

    def test_does_not_contain_non_sandbox_tools(self):
        assert "web_search" not in SANDBOX_TOOL_NAMES
        assert "read_file" not in SANDBOX_TOOL_NAMES


class TestToolSchemas:
    """Tests for tool schema structure."""

    def test_shell_exec_schema(self):
        assert SHELL_EXEC_TOOL["type"] == "function"
        assert SHELL_EXEC_TOOL["function"]["name"] == "shell_exec"
        params = SHELL_EXEC_TOOL["function"]["parameters"]
        assert "command" in params["properties"]
        assert "command" in params["required"]

    def test_browser_nav_schema(self):
        assert BROWSER_NAV_TOOL["function"]["name"] == "browser_navigate"
        params = BROWSER_NAV_TOOL["function"]["parameters"]
        assert "url" in params["properties"]
        assert "url" in params["required"]

    def test_browser_screenshot_schema(self):
        assert BROWSER_SCREENSHOT_TOOL["function"]["name"] == "browser_screenshot"
        params = BROWSER_SCREENSHOT_TOOL["function"]["parameters"]
        assert "url" in params["properties"]
        assert "url" in params["required"]


class TestExecuteSandboxTool:
    """Tests for execute_sandbox_tool routing."""

    @pytest.mark.asyncio
    async def test_routes_shell_exec(self):
        """shell_exec routes to sandbox.run_shell()."""
        sandbox = AsyncMock()
        result_mock = MagicMock()
        result_mock.stdout = "hello world"
        result_mock.stderr = ""
        result_mock.exit_code = 0
        sandbox.run_shell.return_value = result_mock

        result = await execute_sandbox_tool(
            "shell_exec", {"command": "echo hello"}, sandbox
        )
        assert result["result"] == "hello world"
        sandbox.run_shell.assert_called_once_with("echo hello")

    @pytest.mark.asyncio
    async def test_shell_exec_with_stderr(self):
        """shell_exec includes stderr in output."""
        sandbox = AsyncMock()
        result_mock = MagicMock()
        result_mock.stdout = "output"
        result_mock.stderr = "warning"
        result_mock.exit_code = 0
        sandbox.run_shell.return_value = result_mock

        result = await execute_sandbox_tool(
            "shell_exec", {"command": "cmd"}, sandbox
        )
        assert "STDERR: warning" in result["result"]

    @pytest.mark.asyncio
    async def test_shell_exec_failure(self):
        """shell_exec returns error on exception."""
        sandbox = AsyncMock()
        sandbox.run_shell.side_effect = Exception("Sandbox crashed")

        result = await execute_sandbox_tool(
            "shell_exec", {"command": "fail"}, sandbox
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_browser_navigate_blocks_ssrf(self):
        """browser_navigate blocks SSRF URLs."""
        sandbox = AsyncMock()
        result = await execute_sandbox_tool(
            "browser_navigate",
            {"url": "http://169.254.169.254/"},
            sandbox,
        )
        assert result["status"] == "blocked"
        sandbox.execute_code.assert_not_called()

    @pytest.mark.asyncio
    async def test_browser_screenshot_blocks_ssrf(self):
        """browser_screenshot blocks SSRF URLs."""
        sandbox = AsyncMock()
        result = await execute_sandbox_tool(
            "browser_screenshot",
            {"url": "http://metadata.google.internal/"},
            sandbox,
        )
        assert result["status"] == "blocked"
        sandbox.execute_code.assert_not_called()

    @pytest.mark.asyncio
    async def test_browser_screenshot_blocks_bad_filename(self):
        """browser_screenshot rejects path traversal in filename."""
        sandbox = AsyncMock()
        with patch("app.security.url_validator.is_url_safe", return_value=None):
            result = await execute_sandbox_tool(
                "browser_screenshot",
                {"url": "https://example.com", "filename": "../../evil.png"},
                sandbox,
            )
            assert result["status"] == "blocked"
            assert "Invalid filename" in result["error"]

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        """Unknown tool names return error."""
        sandbox = AsyncMock()
        result = await execute_sandbox_tool("nonexistent", {}, sandbox)
        assert "error" in result
        assert "Unknown" in result["error"]


class TestGracefulDegradation:
    """Tests for graceful degradation when sandbox/browser operations fail."""

    @pytest.mark.asyncio
    async def test_browser_navigate_sandbox_exception(self):
        """browser_navigate returns error dict on sandbox exception, not crash."""
        sandbox = AsyncMock()
        sandbox.execute_code.side_effect = Exception("Sandbox connection lost")
        with patch("app.security.url_validator.is_url_safe", return_value=None):
            result = await execute_sandbox_tool(
                "browser_navigate",
                {"url": "https://example.com"},
                sandbox,
            )
        assert result["status"] == "failed"
        assert "error" in result

    @pytest.mark.asyncio
    async def test_browser_navigate_timeout(self):
        """browser_navigate returns error on timeout."""
        sandbox = AsyncMock()
        sandbox.execute_code.side_effect = TimeoutError("Sandbox operation timed out")
        with patch("app.security.url_validator.is_url_safe", return_value=None):
            result = await execute_sandbox_tool(
                "browser_navigate",
                {"url": "https://slow-page.example.com"},
                sandbox,
            )
        assert result["status"] == "failed"
        assert "error" in result

    @pytest.mark.asyncio
    async def test_browser_navigate_page_load_failure(self):
        """browser_navigate returns error on execution failure."""
        sandbox = AsyncMock()
        result_mock = MagicMock()
        result_mock.success = False
        result_mock.stdout = ""
        result_mock.stderr = "net::ERR_CONNECTION_REFUSED"
        result_mock.error = None
        sandbox.execute_code.return_value = result_mock
        with patch("app.security.url_validator.is_url_safe", return_value=None):
            result = await execute_sandbox_tool(
                "browser_navigate",
                {"url": "https://unreachable.example.com"},
                sandbox,
            )
        assert result["status"] == "failed"
        assert "error" in result

    @pytest.mark.asyncio
    async def test_browser_screenshot_sandbox_exception(self):
        """browser_screenshot returns error dict on sandbox exception."""
        sandbox = AsyncMock()
        sandbox.execute_code.side_effect = Exception("E2B API error")
        with patch("app.security.url_validator.is_url_safe", return_value=None):
            result = await execute_sandbox_tool(
                "browser_screenshot",
                {"url": "https://example.com"},
                sandbox,
            )
        assert result["status"] == "failed"
        assert "error" in result

    @pytest.mark.asyncio
    async def test_browser_screenshot_timeout(self):
        """browser_screenshot returns error on timeout."""
        sandbox = AsyncMock()
        sandbox.execute_code.side_effect = TimeoutError("Screenshot timed out")
        with patch("app.security.url_validator.is_url_safe", return_value=None):
            result = await execute_sandbox_tool(
                "browser_screenshot",
                {"url": "https://example.com"},
                sandbox,
            )
        assert result["status"] == "failed"
        assert "error" in result

    @pytest.mark.asyncio
    async def test_shell_exec_returns_error_not_exception(self):
        """shell_exec returns error dict on crash, worker continues."""
        sandbox = AsyncMock()
        sandbox.run_shell.side_effect = ConnectionError("Sandbox terminated")
        result = await execute_sandbox_tool(
            "shell_exec", {"command": "echo test"}, sandbox
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_browser_navigate_non_json_stdout(self):
        """browser_navigate handles non-JSON stdout gracefully."""
        sandbox = AsyncMock()
        result_mock = MagicMock()
        result_mock.success = True
        result_mock.stdout = "Some warning text\nNot valid JSON"
        result_mock.stderr = ""
        result_mock.error = None
        sandbox.execute_code.return_value = result_mock
        with patch("app.security.url_validator.is_url_safe", return_value=None):
            result = await execute_sandbox_tool(
                "browser_navigate",
                {"url": "https://example.com"},
                sandbox,
            )
        # Should return partial content, not crash
        assert result["status"] == "success"
        assert "content" in result
