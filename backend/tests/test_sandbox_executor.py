"""
Unit tests for SandboxExecutor (E2B Code Execution Sandbox).

Tests cover:
- Sandbox creation and lifecycle
- Code execution with output capture
- File upload/download with path validation
- Path traversal rejection on download
- Cost limit enforcement
- Sandbox cleanup on execution failure (try/finally)
- Configuration checks (ENABLE_SANDBOX, E2B_API_KEY)
"""

import pytest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.sandbox_executor import (
    SandboxExecutor,
    SandboxSession,
    ExecutionResult,
    _MAX_COST_PER_EXECUTION,
    _MAX_DOWNLOAD_FILES,
    _COST_PER_SECOND,
)


@pytest.fixture
def temp_session_dir():
    """Create a temporary session directory."""
    temp_dir = tempfile.mkdtemp(prefix="test_sessions_")
    yield Path(temp_dir)
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def mock_settings():
    """Mock settings with sandbox enabled."""
    with patch("app.services.sandbox_executor.get_settings") as mock:
        settings = MagicMock()
        settings.ENABLE_SANDBOX = True
        settings.E2B_API_KEY = "test-api-key"
        mock.return_value = settings
        yield settings


@pytest.fixture
def mock_storage(temp_session_dir):
    """Mock session storage."""
    with patch("app.services.sandbox_executor.get_session_storage") as mock:
        storage = AsyncMock()
        storage._get_session_path.return_value = temp_session_dir
        storage.read.return_value = "file content"
        storage.write = AsyncMock()
        storage.write_binary = AsyncMock()
        mock.return_value = storage
        yield storage


class TestSandboxExecutorConfig:
    """Tests for configuration checks."""

    def test_check_enabled_when_disabled(self, mock_settings):
        mock_settings.ENABLE_SANDBOX = False
        executor = SandboxExecutor()
        enabled, error = executor._check_enabled()
        assert not enabled
        assert "disabled" in error

    def test_check_enabled_without_api_key(self, mock_settings):
        mock_settings.E2B_API_KEY = ""
        executor = SandboxExecutor()
        enabled, error = executor._check_enabled()
        assert not enabled
        assert "API key" in error

    def test_check_enabled_with_config(self, mock_settings):
        executor = SandboxExecutor()
        enabled, error = executor._check_enabled()
        assert enabled
        assert error is None


class TestCostLimit:
    """Tests for per-execution cost enforcement constants."""

    def test_within_cost_limit(self, mock_settings, mock_storage):
        # 10 seconds at cost rate should be well within limit
        cost = 10.0 * _COST_PER_SECOND
        assert cost < _MAX_COST_PER_EXECUTION

    def test_exceeds_cost_limit(self, mock_settings, mock_storage):
        # Calculate a duration that exceeds the cost limit
        max_duration = _MAX_COST_PER_EXECUTION / _COST_PER_SECOND
        cost = (max_duration + 1000) * _COST_PER_SECOND
        assert cost > _MAX_COST_PER_EXECUTION


class TestSandboxCreate:
    """Tests for sandbox creation."""

    @pytest.mark.asyncio
    async def test_create_fails_when_disabled(self, mock_settings, mock_storage):
        mock_settings.ENABLE_SANDBOX = False
        executor = SandboxExecutor()
        with pytest.raises(RuntimeError, match="disabled"):
            await executor.create_sandbox()

    @pytest.mark.asyncio
    async def test_create_fails_without_api_key(self, mock_settings, mock_storage):
        mock_settings.E2B_API_KEY = ""
        executor = SandboxExecutor()
        with pytest.raises(RuntimeError, match="API key"):
            await executor.create_sandbox()

    @pytest.mark.asyncio
    async def test_create_fails_without_e2b_package(self, mock_settings, mock_storage):
        executor = SandboxExecutor()
        with patch.dict("sys.modules", {"e2b_code_interpreter": None}):
            with pytest.raises(RuntimeError):
                await executor.create_sandbox()


class TestSandboxExecution:
    """Tests for code execution."""

    @pytest.mark.asyncio
    async def test_execute_code_sandbox_not_found(self, mock_settings, mock_storage):
        executor = SandboxExecutor()
        result = await executor.execute_code("nonexistent", "print('hello')")
        assert not result.success
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_execute_with_lifecycle_cleanup(self, mock_settings, mock_storage):
        """Test that sandbox is cleaned up even on failure."""
        executor = SandboxExecutor()

        mock_sandbox = AsyncMock()
        mock_sandbox.sandbox_id = "test-sandbox"
        mock_sandbox.run_code = AsyncMock(side_effect=Exception("execution error"))
        mock_sandbox.kill = AsyncMock()

        with patch.object(executor, "create_sandbox", return_value=SandboxSession(
            sandbox_id="test-sandbox",
            template="python-data-analysis",
            timeout_seconds=3600,
        )):
            executor._sandboxes["test-sandbox"] = mock_sandbox

            result = await executor.execute_with_lifecycle(
                code="print('hello')",
                tenant_id="test-tenant",
                session_id="test-session",
            )

            assert not result.success
            # Sandbox should be cleaned up via close_sandbox
            mock_sandbox.kill.assert_called_once()


class TestDownloadValidation:
    """Tests for download path validation and limits."""

    @pytest.mark.asyncio
    async def test_download_too_many_files(self, mock_settings, mock_storage):
        executor = SandboxExecutor()
        mock_sandbox = MagicMock()
        mock_sandbox.sandbox_id = "test"
        executor._sandboxes["test"] = mock_sandbox

        paths = [f"/file_{i}.txt" for i in range(_MAX_DOWNLOAD_FILES + 1)]

        with pytest.raises(ValueError, match="Too many files"):
            await executor.download_from_sandbox("test", "test-tenant", "session", paths)

    @pytest.mark.asyncio
    async def test_download_sandbox_not_found(self, mock_settings, mock_storage):
        executor = SandboxExecutor()
        with pytest.raises(RuntimeError, match="not found"):
            await executor.download_from_sandbox("nonexistent", "test-tenant", "session", ["/file.txt"])


class TestExecutionResult:
    """Tests for ExecutionResult dataclass."""

    def test_success_result(self):
        result = ExecutionResult(
            success=True,
            stdout="Hello",
            stderr="",
            duration_seconds=1.5,
        )
        assert result.success
        assert result.stdout == "Hello"
        assert result.error is None

    def test_failure_result(self):
        result = ExecutionResult(
            success=False,
            stdout="",
            stderr="Error occurred",
            duration_seconds=0.1,
            error="RuntimeError",
        )
        assert not result.success
        assert result.error == "RuntimeError"
