"""
Unit tests for WideResearchOrchestrator.

Tests cover:
- Parallel execution with semaphore concurrency control
- Early termination on high failure rate (>50%)
- Timeout per item enforcement
- Results aggregation and status computation
- return_exceptions=True handles individual failures
- Sub-agent has no sandbox/workspace tools (no session_id)
- Expert review synthesis
- Items > max rejected before execution
- Abort signal during execution
- Results saved to workspace JSON
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.wide_research_orchestrator import (
    WideResearchOrchestrator,
    WideResearchResult,
    WideResearchItemResult,
    _MAX_ITEMS,
    _MAX_PARALLEL,
    _APPROVAL_THRESHOLD,
    _EARLY_TERMINATION_THRESHOLD,
)


@pytest.fixture
def mock_db():
    """Mock async DB session."""
    return AsyncMock()


@pytest.fixture
def orchestrator(mock_db):
    """Create orchestrator with mock DB."""
    return WideResearchOrchestrator(mock_db)


@pytest.fixture
def sample_items():
    """Sample items for testing."""
    return [
        {"ticker": "AAPL", "period": "Q4"},
        {"ticker": "GOOGL", "period": "Q4"},
        {"ticker": "MSFT", "period": "Q4"},
    ]


@pytest.fixture
def mock_storage():
    """Mock session storage for workspace writes."""
    with patch("app.services.session_storage.get_session_storage") as mock:
        storage = AsyncMock()
        storage.write = AsyncMock()
        mock.return_value = storage
        yield storage


@pytest.fixture
def mock_audit():
    """Mock audit logger."""
    with patch("app.services.audit_logger.log_decision", new_callable=AsyncMock) as mock:
        yield mock


class TestExecution:
    """Tests for parallel execution."""

    @pytest.mark.asyncio
    async def test_all_items_succeed(self, orchestrator, sample_items, mock_storage, mock_audit):
        """All items processed successfully."""
        with patch.object(
            orchestrator, "_run_sub_agent",
            new_callable=AsyncMock,
            return_value={"findings": "Analysis complete", "confidence": "high"},
        ):
            result = await orchestrator.execute(
                task_template="Analyze {item}",
                items=sample_items,
                agent_id="analysis-agent",
                tenant_id="t1",
                user_id="u1",
                session_id="s1",
            )

        assert result.status == "completed"
        assert result.total_items == 3
        assert result.completed == 3
        assert result.failed == 0
        assert result.skipped == 0
        assert len(result.items) == 3

    @pytest.mark.asyncio
    async def test_partial_failures(self, orchestrator, mock_storage, mock_audit):
        """Some items fail, result is partial."""
        call_count = 0

        async def mock_run(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise ValueError("LLM rate limit exceeded")
            return {"findings": "ok"}

        items = [{"id": i} for i in range(5)]

        with patch.object(orchestrator, "_run_sub_agent", side_effect=mock_run):
            result = await orchestrator.execute(
                task_template="Process {item}",
                items=items,
                agent_id="agent",
                tenant_id="t1",
                user_id="u1",
                session_id="s1",
            )

        assert result.status == "partial"
        assert result.completed == 4
        assert result.failed == 1

    @pytest.mark.asyncio
    async def test_all_fail(self, orchestrator, mock_storage, mock_audit):
        """All items fail, result status is 'failed' or 'aborted'."""
        with patch.object(
            orchestrator, "_run_sub_agent",
            new_callable=AsyncMock,
            side_effect=Exception("API down"),
        ):
            result = await orchestrator.execute(
                task_template="Analyze {item}",
                items=[{"id": 1}, {"id": 2}],
                agent_id="agent",
                tenant_id="t1",
                user_id="u1",
                session_id="s1",
            )

        # Either "failed" or "aborted" (early termination triggers)
        assert result.status in ("failed", "aborted")
        assert result.completed == 0


class TestEarlyTermination:
    """Tests for early termination on high failure rate."""

    @pytest.mark.asyncio
    async def test_aborts_on_high_failure_rate(self, orchestrator, mock_storage, mock_audit):
        """Aborts when >50% of first batch fails."""
        call_count = 0

        async def mock_run(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            # First 6 out of 10 fail
            if call_count <= 6:
                raise Exception("Failed")
            return {"findings": "ok"}

        items = [{"id": i} for i in range(10)]

        with patch.object(orchestrator, "_run_sub_agent", side_effect=mock_run):
            result = await orchestrator.execute(
                task_template="Process {item}",
                items=items,
                agent_id="agent",
                tenant_id="t1",
                user_id="u1",
                session_id="s1",
                max_parallel=10,
            )

        assert result.status == "aborted"
        assert result.skipped >= 0  # Some items may have been skipped


class TestTimeout:
    """Tests for timeout enforcement."""

    @pytest.mark.asyncio
    async def test_item_timeout(self, orchestrator, mock_storage, mock_audit):
        """Items that exceed timeout are marked as timeout."""
        async def slow_agent(*args, **kwargs):
            await asyncio.sleep(10)
            return {"findings": "late"}

        with patch.object(orchestrator, "_run_sub_agent", side_effect=slow_agent):
            result = await orchestrator.execute(
                task_template="Process {item}",
                items=[{"id": 1}],
                agent_id="agent",
                tenant_id="t1",
                user_id="u1",
                session_id="s1",
                timeout_per_item=1,  # 1 second timeout
            )

        assert result.items[0].status == "timeout"
        assert "Timed out" in result.items[0].error


class TestItemLimits:
    """Tests for item count limits."""

    @pytest.mark.asyncio
    async def test_exceeds_max_items(self, orchestrator):
        """Rejects batches exceeding _MAX_ITEMS."""
        items = [{"id": i} for i in range(_MAX_ITEMS + 1)]

        with pytest.raises(ValueError, match="Too many items"):
            await orchestrator.execute(
                task_template="Process {item}",
                items=items,
                agent_id="agent",
                tenant_id="t1",
                user_id="u1",
                session_id="s1",
            )


class TestAbort:
    """Tests for abort functionality."""

    @pytest.mark.asyncio
    async def test_abort_signal(self, orchestrator, mock_storage, mock_audit):
        """Abort signal causes remaining items to be skipped."""
        call_count = 0

        async def mock_run(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                orchestrator.abort()
            await asyncio.sleep(0.01)  # Small delay to allow abort propagation
            return {"findings": "ok"}

        items = [{"id": i} for i in range(10)]

        with patch.object(orchestrator, "_run_sub_agent", side_effect=mock_run):
            result = await orchestrator.execute(
                task_template="Process {item}",
                items=items,
                agent_id="agent",
                tenant_id="t1",
                user_id="u1",
                session_id="s1",
                max_parallel=2,
            )

        assert result.skipped > 0


class TestConcurrency:
    """Tests for semaphore-controlled concurrency."""

    @pytest.mark.asyncio
    async def test_respects_max_parallel(self, orchestrator, mock_storage, mock_audit):
        """Concurrency never exceeds max_parallel."""
        concurrent = 0
        max_concurrent = 0

        async def mock_run(*args, **kwargs):
            nonlocal concurrent, max_concurrent
            concurrent += 1
            max_concurrent = max(max_concurrent, concurrent)
            await asyncio.sleep(0.05)
            concurrent -= 1
            return {"findings": "ok"}

        items = [{"id": i} for i in range(20)]

        with patch.object(orchestrator, "_run_sub_agent", side_effect=mock_run):
            await orchestrator.execute(
                task_template="Process {item}",
                items=items,
                agent_id="agent",
                tenant_id="t1",
                user_id="u1",
                session_id="s1",
                max_parallel=3,
            )

        assert max_concurrent <= 3


class TestWorkspaceSave:
    """Tests for workspace result storage."""

    @pytest.mark.asyncio
    async def test_saves_results_to_workspace(self, orchestrator, mock_storage, mock_audit):
        """Results are saved to workspace JSON."""
        with patch.object(
            orchestrator, "_run_sub_agent",
            new_callable=AsyncMock,
            return_value={"findings": "ok"},
        ):
            result = await orchestrator.execute(
                task_template="Process {item}",
                items=[{"id": 1}],
                agent_id="agent",
                tenant_id="t1",
                user_id="u1",
                session_id="s1",
            )

        mock_storage.write.assert_called_once()
        call_args = mock_storage.write.call_args
        assert call_args[0][0] == "t1"  # tenant_id
        assert call_args[0][1] == "s1"  # session_id
        assert f"wide_research/{result.execution_id}" in call_args[0][2]
        assert "results.json" in call_args[0][2]


class TestAuditLogging:
    """Tests for audit log integration."""

    @pytest.mark.asyncio
    async def test_logs_execution_metadata(self, orchestrator, mock_storage, mock_audit):
        """Execution metadata is logged via AuditLogger."""
        with patch.object(
            orchestrator, "_run_sub_agent",
            new_callable=AsyncMock,
            return_value={"findings": "ok"},
        ):
            await orchestrator.execute(
                task_template="Process {item}",
                items=[{"id": 1}],
                agent_id="agent",
                tenant_id="t1",
                user_id="u1",
                session_id="s1",
            )

        mock_audit.assert_called_once()
        call_kwargs = mock_audit.call_args[1]
        assert call_kwargs["agent_id"] == "wide_research_orchestrator"
        assert "wide_research_completed" in call_kwargs["decision"]


class TestSynthesis:
    """Tests for expert review synthesis."""

    @pytest.mark.asyncio
    async def test_synthesis_produces_summary(self, orchestrator):
        """Synthesis returns summary text with confidence."""
        mock_response = MagicMock()
        mock_response.content = "Summary: All items analyzed successfully."

        with patch("app.services.llm_gateway.LLMGateway") as MockGateway:
            mock_gw = AsyncMock()
            mock_gw.complete.return_value = mock_response
            MockGateway.return_value = mock_gw

            result = WideResearchResult(
                execution_id="test",
                status="completed",
                total_items=5,
                completed=5,
                failed=0,
                skipped=0,
                items=[
                    WideResearchItemResult(
                        item_index=i,
                        item={"id": i},
                        status="completed",
                        result={"findings": f"Result {i}"},
                    )
                    for i in range(5)
                ],
            )

            synthesis = await orchestrator.synthesize(
                result=result,
                tenant_id="t1",
                user_id="u1",
            )

        assert "synthesis" in synthesis
        assert synthesis["total_completed"] == 5
        assert synthesis["confidence"] == "high"

    @pytest.mark.asyncio
    async def test_synthesis_low_completion_medium_confidence(self, orchestrator):
        """Synthesis has medium confidence when completion rate < 80%."""
        mock_response = MagicMock()
        mock_response.content = "Partial analysis."

        with patch("app.services.llm_gateway.LLMGateway") as MockGateway:
            mock_gw = AsyncMock()
            mock_gw.complete.return_value = mock_response
            MockGateway.return_value = mock_gw

            result = WideResearchResult(
                execution_id="test",
                status="partial",
                total_items=10,
                completed=3,
                failed=7,
                skipped=0,
                items=[
                    WideResearchItemResult(
                        item_index=i,
                        item={"id": i},
                        status="completed" if i < 3 else "failed",
                        result={"findings": f"Result {i}"} if i < 3 else None,
                        error="Failed" if i >= 3 else None,
                    )
                    for i in range(10)
                ],
            )

            synthesis = await orchestrator.synthesize(
                result=result,
                tenant_id="t1",
                user_id="u1",
            )

        assert synthesis["confidence"] == "medium"


class TestSubAgentRestrictions:
    """Tests for sub-agent tool restrictions."""

    @pytest.mark.asyncio
    async def test_sub_agent_no_session_id(self, orchestrator, mock_storage, mock_audit):
        """Sub-agents are called without session_id (no workspace access)."""
        with patch.object(orchestrator, "_run_sub_agent", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = {"findings": "ok"}

            await orchestrator.execute(
                task_template="Process {item}",
                items=[{"id": 1}],
                agent_id="agent",
                tenant_id="t1",
                user_id="u1",
                session_id="s1",
            )

            # _run_sub_agent receives prompt, agent_id, tenant_id, user_id, api_keys
            # but NOT session_id
            call_kwargs = mock_run.call_args[1]
            assert "session_id" not in call_kwargs


class TestConstants:
    """Tests for module constants."""

    def test_approval_threshold(self):
        assert _APPROVAL_THRESHOLD == 10

    def test_max_items(self):
        assert _MAX_ITEMS == 500

    def test_max_parallel(self):
        assert _MAX_PARALLEL == 50

    def test_early_termination_threshold(self):
        assert _EARLY_TERMINATION_THRESHOLD == 0.5
