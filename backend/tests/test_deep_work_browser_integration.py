"""
Integration tests for deep work pipeline with browser tools.

Tests cover the full flow:
- SandboxToolRouter provides browser tools when feature flag is enabled
- Worker delegates sandbox tool calls through execute_sandbox_tool
- Browser navigate and screenshot return structured results
- Tool schemas are consistent between router and worker
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.deep_work.sandbox_tools import (
    SANDBOX_TOOL_NAMES,
    get_sandbox_tools,
    execute_sandbox_tool,
)


class TestBrowserToolPipeline:
    """End-to-end tests for browser tools through the deep work pipeline."""

    def test_sandbox_tools_include_browser_when_enabled(self):
        """get_sandbox_tools returns browser tools when flag is on."""
        with patch("app.services.deep_work.sandbox_tools.get_settings") as mock:
            mock.return_value = MagicMock(ENABLE_BROWSER_SANDBOX=True)
            tools = get_sandbox_tools()
            names = [t["function"]["name"] for t in tools]
            assert "shell_exec" in names
            assert "browser_navigate" in names
            assert "browser_screenshot" in names

    def test_sandbox_tools_exclude_browser_when_disabled(self):
        """get_sandbox_tools excludes browser tools when flag is off."""
        with patch("app.services.deep_work.sandbox_tools.get_settings") as mock:
            mock.return_value = MagicMock(ENABLE_BROWSER_SANDBOX=False)
            tools = get_sandbox_tools()
            names = [t["function"]["name"] for t in tools]
            assert "shell_exec" in names
            assert "browser_navigate" not in names
            assert "browser_screenshot" not in names

    def test_sandbox_tool_names_matches_router(self):
        """SANDBOX_TOOL_NAMES includes all routable tool names."""
        with patch("app.services.deep_work.sandbox_tools.get_settings") as mock:
            mock.return_value = MagicMock(ENABLE_BROWSER_SANDBOX=True)
            tools = get_sandbox_tools()
            schema_names = {t["function"]["name"] for t in tools}
            assert schema_names.issubset(SANDBOX_TOOL_NAMES)

    @pytest.mark.asyncio
    async def test_navigate_then_screenshot_pipeline(self):
        """Simulate a worker navigating then screenshotting a page."""
        sandbox = AsyncMock()

        # Step 1: Navigate
        nav_result = MagicMock()
        nav_result.success = True
        nav_result.stdout = json.dumps({
            "status": "success",
            "url": "https://example.com",
            "content": "Example Domain page content",
        })
        nav_result.stderr = ""
        nav_result.error = None
        sandbox.execute_code.return_value = nav_result

        with patch("app.security.url_validator.is_url_safe", return_value=None):
            result = await execute_sandbox_tool(
                "browser_navigate",
                {"url": "https://example.com", "task": "extract the main text"},
                sandbox,
            )
        assert result["status"] == "success"
        assert "content" in result
        sandbox.execute_code.assert_called_once()

        # Step 2: Screenshot
        sandbox.execute_code.reset_mock()
        ss_result = MagicMock()
        ss_result.success = True
        ss_result.stdout = json.dumps({
            "status": "success",
            "url": "https://example.com",
            "file_path": "/workspace/screenshot.png",
            "size_bytes": 45000,
        })
        ss_result.stderr = ""
        ss_result.error = None
        sandbox.execute_code.return_value = ss_result

        with patch("app.security.url_validator.is_url_safe", return_value=None):
            result = await execute_sandbox_tool(
                "browser_screenshot",
                {"url": "https://example.com", "full_page": True},
                sandbox,
            )
        assert result["status"] == "success"
        assert result["file_path"] == "/workspace/screenshot.png"
        assert result["size_bytes"] == 45000
        sandbox.execute_code.assert_called_once()

    @pytest.mark.asyncio
    async def test_shell_then_browser_mixed_workflow(self):
        """Simulate a worker using shell_exec then browser in same session."""
        sandbox = AsyncMock()

        # Step 1: Shell exec (uses run_shell)
        shell_result = MagicMock()
        shell_result.stdout = "Python 3.12.0"
        shell_result.stderr = ""
        shell_result.exit_code = 0
        sandbox.run_shell.return_value = shell_result

        result = await execute_sandbox_tool(
            "shell_exec", {"command": "python3 --version"}, sandbox
        )
        assert "Python 3.12.0" in result["result"]
        sandbox.run_shell.assert_called_once()

        # Step 2: Browser navigate (uses execute_code)
        nav_result = MagicMock()
        nav_result.success = True
        nav_result.stdout = json.dumps({
            "status": "success",
            "url": "https://sec.gov/cgi-bin/browse-edgar",
            "content": "EDGAR Company Search results...",
        })
        nav_result.stderr = ""
        nav_result.error = None
        sandbox.execute_code.return_value = nav_result

        with patch("app.security.url_validator.is_url_safe", return_value=None):
            result = await execute_sandbox_tool(
                "browser_navigate",
                {"url": "https://sec.gov/cgi-bin/browse-edgar", "task": "find recent 10-K filings"},
                sandbox,
            )
        assert result["status"] == "success"
        assert "EDGAR" in result["content"]

    @pytest.mark.asyncio
    async def test_ssrf_blocks_before_sandbox_call(self):
        """SSRF check prevents any sandbox interaction."""
        sandbox = AsyncMock()

        # Private IP
        result = await execute_sandbox_tool(
            "browser_navigate",
            {"url": "http://192.168.1.1/admin"},
            sandbox,
        )
        assert result["status"] == "blocked"
        sandbox.execute_code.assert_not_called()
        sandbox.run_shell.assert_not_called()

        # Cloud metadata
        result = await execute_sandbox_tool(
            "browser_screenshot",
            {"url": "http://169.254.169.254/latest/meta-data/"},
            sandbox,
        )
        assert result["status"] == "blocked"
        sandbox.execute_code.assert_not_called()

    @pytest.mark.asyncio
    async def test_browser_failure_does_not_break_subsequent_tools(self):
        """After a browser tool fails, other tools still work."""
        sandbox = AsyncMock()

        # Browser navigate fails
        sandbox.execute_code.side_effect = Exception("Browser crashed")
        with patch("app.security.url_validator.is_url_safe", return_value=None):
            result = await execute_sandbox_tool(
                "browser_navigate",
                {"url": "https://example.com"},
                sandbox,
            )
        assert result["status"] == "failed"

        # Shell exec still works (uses run_shell, not execute_code)
        sandbox.execute_code.side_effect = None  # Reset
        shell_result = MagicMock()
        shell_result.stdout = "ok"
        shell_result.stderr = ""
        shell_result.exit_code = 0
        sandbox.run_shell.return_value = shell_result

        result = await execute_sandbox_tool(
            "shell_exec", {"command": "echo ok"}, sandbox
        )
        assert result["result"] == "ok"

    @pytest.mark.asyncio
    async def test_execute_code_receives_python_script(self):
        """Verify browser tools pass Python code (not shell heredoc) to execute_code."""
        sandbox = AsyncMock()
        exec_result = MagicMock()
        exec_result.success = True
        exec_result.stdout = json.dumps({"status": "success", "url": "https://example.com", "content": "test"})
        exec_result.stderr = ""
        exec_result.error = None
        sandbox.execute_code.return_value = exec_result

        with patch("app.security.url_validator.is_url_safe", return_value=None):
            await execute_sandbox_tool(
                "browser_navigate",
                {"url": "https://example.com"},
                sandbox,
            )

        # Verify execute_code was called with Python code, not a shell heredoc
        call_args = sandbox.execute_code.call_args
        script = call_args[0][0]  # First positional arg
        assert "import asyncio" in script
        assert "browser_use" in script
        assert "cat >" not in script  # No heredoc
        assert "PYEOF" not in script  # No heredoc delimiter
        assert "asyncio.run" not in script  # Must use await, not asyncio.run
        assert "await main()" in script  # Jupyter kernel already has event loop
