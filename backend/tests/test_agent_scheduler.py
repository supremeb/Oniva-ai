"""Tests for agent scheduling in WorkflowSchedulerDaemon._poll_agents_once().

Mocks the DB session and background_dispatch to test scheduling logic
without real database connections.
"""

import asyncio
import json
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from collections import namedtuple

# Named tuple to simulate SQLAlchemy row results
AgentRow = namedtuple("AgentRow", [
    "id", "tenant_id", "user_id", "name",
    "background_config", "resource_constraints",
])

TENANT_ID = uuid4()
USER_ID = uuid4()
AGENT_ID = uuid4()


def _make_agent_row(
    agent_id=None,
    enabled=True,
    cron="0 9 * * *",
    next_run_at=None,
    max_concurrent=1,
):
    """Create a mock agent row as returned by the scheduler query."""
    if next_run_at is None:
        next_run_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()

    return AgentRow(
        id=agent_id or AGENT_ID,
        tenant_id=TENANT_ID,
        user_id=USER_ID,
        name="Test Agent",
        background_config={
            "enabled": enabled,
            "cron_expression": cron,
            "timezone": "UTC",
            "next_run_at": next_run_at,
        },
        resource_constraints={"max_concurrent": max_concurrent},
    )


@pytest.fixture
def scheduler_daemon():
    """Create a WorkflowSchedulerDaemon with mocked internals."""
    from app.services.workflow_scheduler import WorkflowSchedulerDaemon

    # Reset singleton for test isolation
    WorkflowSchedulerDaemon._instance = None

    daemon = WorkflowSchedulerDaemon()
    daemon._initialized = True
    daemon._session_factory = MagicMock()
    daemon._semaphore = asyncio.Semaphore(10)
    daemon._poll_interval = 30
    daemon._batch_size = 10
    daemon._active_tasks = set()
    daemon._is_running = True

    yield daemon

    WorkflowSchedulerDaemon._instance = None


@pytest.mark.asyncio
async def test_due_agent_triggers_dispatch(scheduler_daemon):
    """An agent with enabled background_config and past next_run_at triggers dispatch."""
    agent_row = _make_agent_row()

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_rows = MagicMock()
    mock_rows.fetchall.return_value = [agent_row]
    mock_session.execute = AsyncMock(side_effect=[mock_rows, MagicMock()])
    mock_session.commit = AsyncMock()

    scheduler_daemon._session_factory.return_value = mock_session

    mock_job = MagicMock()
    mock_job.id = uuid4()

    next_run = datetime.now(timezone.utc) + timedelta(hours=24)

    with patch("app.services.background_dispatch.check_and_create_background_job", new_callable=AsyncMock, return_value=mock_job) as mock_dispatch, \
         patch("app.services.background_agent_executor.run_background_agent", new_callable=AsyncMock) as mock_executor, \
         patch("app.services.workflow_scheduler.calculate_next_run", return_value=next_run):

        await scheduler_daemon._poll_agents_once()

        mock_dispatch.assert_called_once()
        call_kwargs = mock_dispatch.call_args.kwargs
        assert call_kwargs["agent_id"] == AGENT_ID
        assert call_kwargs["trigger_type"] == "scheduled"


@pytest.mark.asyncio
async def test_no_due_agents_skipped(scheduler_daemon):
    """When no agents are due, nothing is dispatched."""
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_rows = MagicMock()
    mock_rows.fetchall.return_value = []
    mock_session.execute = AsyncMock(return_value=mock_rows)

    scheduler_daemon._session_factory.return_value = mock_session

    with patch("app.services.background_dispatch.check_and_create_background_job", new_callable=AsyncMock) as mock_dispatch:
        await scheduler_daemon._poll_agents_once()
        mock_dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_concurrent_limit_prevents_dispatch(scheduler_daemon):
    """When agent is at max concurrent runs, dispatch is skipped gracefully."""
    agent_row = _make_agent_row(max_concurrent=1)

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_rows = MagicMock()
    mock_rows.fetchall.return_value = [agent_row]
    mock_session.execute = AsyncMock(side_effect=[mock_rows, MagicMock()])
    mock_session.commit = AsyncMock()
    mock_session.rollback = AsyncMock()

    scheduler_daemon._session_factory.return_value = mock_session

    from app.services.background_dispatch import ConcurrentLimitExceeded

    next_run = datetime.now(timezone.utc) + timedelta(hours=24)

    with patch("app.services.background_dispatch.check_and_create_background_job", new_callable=AsyncMock, side_effect=ConcurrentLimitExceeded("at max")) as mock_dispatch, \
         patch("app.services.background_agent_executor.run_background_agent", new_callable=AsyncMock) as mock_executor, \
         patch("app.services.workflow_scheduler.calculate_next_run", return_value=next_run):

        await scheduler_daemon._poll_agents_once()

        mock_dispatch.assert_called_once()
        mock_executor.assert_not_called()


@pytest.mark.asyncio
async def test_feature_gate_in_poll_loop():
    """Verify _poll_agents_once method exists and is callable."""
    from app.services.workflow_scheduler import WorkflowSchedulerDaemon

    WorkflowSchedulerDaemon._instance = None
    daemon = WorkflowSchedulerDaemon()
    daemon._initialized = True
    daemon._active_tasks = set()
    daemon._is_running = False
    daemon._poll_interval = 30
    daemon._batch_size = 10
    daemon._session_factory = MagicMock()
    daemon._semaphore = asyncio.Semaphore(10)
    daemon._last_poll_at = None
    daemon._errors_count = 0

    assert hasattr(daemon, '_poll_agents_once')
    assert callable(daemon._poll_agents_once)

    WorkflowSchedulerDaemon._instance = None
