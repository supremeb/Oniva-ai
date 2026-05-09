"""Tests for tool retry with backoff (Phase C).

Covers:
- retryable=True retries on ConnectionError (3 attempts)
- retryable=False does NOT retry
- Non-transient errors (ValueError) are not retried
- Each attempt gets its own observability trace with attempt_number
- Workflow retry budget exhaustion
"""
import pytest
import asyncio
from unittest.mock import patch, MagicMock

from app.agents.skills.registry import skill, _registry
from app.services.observability import ObservabilityService


class TestRetryableSkill:
    """Test that retryable=True skills retry on transient errors."""

    @pytest.fixture(autouse=True)
    def cleanup_registry(self):
        """Remove test skills from registry after each test."""
        before = set(_registry.keys())
        yield
        for key in list(_registry.keys()):
            if key not in before:
                del _registry[key]

    @pytest.fixture
    def obs_service(self):
        svc = ObservabilityService()
        svc._client = None
        return svc

    @pytest.mark.asyncio
    async def test_retryable_retries_on_connection_error(self, obs_service):
        """Retryable skill retries on ConnectionError."""
        call_count = 0

        @skill("test_retry_conn", "Test retry", category="test", retryable=True)
        async def test_retry_conn(ctx, query: str) -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("connection reset")
            return "success"

        # Create a minimal mock context
        mock_ctx = MagicMock()
        mock_ctx.deps = MagicMock()
        mock_ctx.deps.tenant_id = None
        mock_ctx.deps.session_id = None

        with patch("app.services.observability.get_observability_service", return_value=obs_service):
            result = await _registry["test_retry_conn"].handler(mock_ctx, query="test")

        assert result == "success"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_non_retryable_does_not_retry(self, obs_service):
        """Non-retryable skill does NOT retry."""
        call_count = 0

        @skill("test_no_retry", "No retry", category="test", retryable=False)
        async def test_no_retry(ctx, query: str) -> str:
            nonlocal call_count
            call_count += 1
            raise ConnectionError("connection reset")

        mock_ctx = MagicMock()
        mock_ctx.deps = MagicMock()
        mock_ctx.deps.tenant_id = None
        mock_ctx.deps.session_id = None

        with patch("app.services.observability.get_observability_service", return_value=obs_service):
            with pytest.raises(ConnectionError):
                await _registry["test_no_retry"].handler(mock_ctx, query="test")

        assert call_count == 1

    @pytest.mark.asyncio
    async def test_non_transient_not_retried(self, obs_service):
        """ValueError is not a transient error — no retry even with retryable=True."""
        call_count = 0

        @skill("test_no_retry_val", "No retry val", category="test", retryable=True)
        async def test_no_retry_val(ctx, query: str) -> str:
            nonlocal call_count
            call_count += 1
            raise ValueError("bad input")

        mock_ctx = MagicMock()
        mock_ctx.deps = MagicMock()
        mock_ctx.deps.tenant_id = None
        mock_ctx.deps.session_id = None

        with patch("app.services.observability.get_observability_service", return_value=obs_service):
            with pytest.raises(ValueError, match="bad input"):
                await _registry["test_no_retry_val"].handler(mock_ctx, query="test")

        assert call_count == 1

    @pytest.mark.asyncio
    async def test_each_attempt_traced_separately(self, obs_service):
        """Each retry attempt gets its own trace with attempt_number."""
        call_count = 0

        @skill("test_trace_attempts", "Trace attempts", category="test", retryable=True)
        async def test_trace_attempts(ctx, query: str) -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("connection reset")
            return "ok"

        mock_ctx = MagicMock()
        mock_ctx.deps = MagicMock()
        mock_ctx.deps.tenant_id = None
        mock_ctx.deps.session_id = None

        with patch("app.services.observability.get_observability_service", return_value=obs_service):
            result = await _registry["test_trace_attempts"].handler(mock_ctx, query="test")

        await asyncio.sleep(0.05)  # Allow fire-and-forget tasks

        assert len(obs_service._tool_metrics) == 3
        attempts = [m.attempt_number for m in obs_service._tool_metrics]
        assert attempts == [1, 2, 3]

        # First two should be errors, third success
        statuses = [m.status for m in obs_service._tool_metrics]
        assert statuses == ["error", "error", "success"]

    def test_skill_metadata_has_retryable_flag(self):
        """Skill metadata records retryable=True."""
        @skill("test_meta_retry", "Meta retry", category="test", retryable=True)
        async def test_meta_retry(ctx) -> str:
            return "ok"

        assert _registry["test_meta_retry"].retryable is True

        @skill("test_meta_no_retry", "Meta no retry", category="test", retryable=False)
        async def test_meta_no_retry(ctx) -> str:
            return "ok"

        assert _registry["test_meta_no_retry"].retryable is False


class TestWorkflowRetryBudget:
    """Test workflow retry budget tracking."""

    def test_budget_consumption(self):
        """Consuming retries decrements budget."""
        from app.services.workflow_orchestrator import WorkflowOrchestrator

        orch = object.__new__(WorkflowOrchestrator)
        orch._retry_budget = 3
        orch._retries_used = 0

        assert orch.retries_remaining == 3
        assert orch.consume_retry() is True
        assert orch.retries_remaining == 2
        assert orch.consume_retry() is True
        assert orch.consume_retry() is True
        assert orch.retries_remaining == 0
        assert orch.consume_retry() is False

    def test_budget_exhaustion_returns_false(self):
        """Once budget is exhausted, consume_retry returns False."""
        from app.services.workflow_orchestrator import WorkflowOrchestrator

        orch = object.__new__(WorkflowOrchestrator)
        orch._retry_budget = 0
        orch._retries_used = 0

        assert orch.consume_retry() is False
