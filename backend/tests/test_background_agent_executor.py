"""Tests for BackgroundAgentExecutor (run_background_agent).

Mocks LLMGateway and SkillRegistry to test the executor loop logic
without real LLM calls or database connections.
"""

import asyncio
import json
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, call
from uuid import uuid4

# Module paths for patching (executor imports at module level)
MOD = "app.services.background_agent_executor"

# Test UUIDs
TENANT_ID = uuid4()
USER_ID = uuid4()
AGENT_ID = uuid4()
JOB_ID = uuid4()


def _make_mock_job(job_id=None):
    """Create a mock BackgroundJob."""
    job = MagicMock()
    job.id = job_id or JOB_ID
    job.status = "pending"
    job.execution_trace = []
    job.progress = 0
    job.result = None
    job.error = None
    return job


def _make_mock_agent():
    """Create a mock CustomAgent."""
    agent = MagicMock()
    agent.id = AGENT_ID
    agent.name = "Test Agent"
    agent.system_prompt = "You are a test agent."
    agent.model = "gpt-4o"
    agent.temperature = 0.7
    agent.skills = ["web_search"]
    agent.resource_constraints = {"max_tool_calls": 5, "timeout_seconds": 60}
    agent.background_config = {"enabled": True, "webhook_url": "https://example.com/hook"}
    return agent


def _make_llm_response(content="Done.", tool_calls=None):
    """Create a mock LLMResponse."""
    resp = MagicMock()
    resp.content = content
    resp.tool_calls = tool_calls
    resp.input_tokens = 100
    resp.output_tokens = 50
    return resp


def _make_tool_call(name="web_search", args=None):
    """Create a mock ToolCallResponse."""
    tc = MagicMock()
    tc.id = f"call_{uuid4().hex[:8]}"
    tc.name = name
    tc.arguments = args or {"query": "test"}
    return tc


def _make_session_mock(job, agent):
    """Create a properly configured AsyncSession mock."""
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    # begin_nested context manager
    nested = AsyncMock()
    nested.__aenter__ = AsyncMock(return_value=nested)
    nested.__aexit__ = AsyncMock(return_value=False)
    session.begin_nested = MagicMock(return_value=nested)

    # Track execute calls: 1st=job, 2nd=agent, rest=job
    call_idx = [0]
    def execute_side_effect(*args, **kwargs):
        r = MagicMock()
        call_idx[0] += 1
        if call_idx[0] == 2:
            r.scalar_one_or_none.return_value = agent
        else:
            r.scalar_one_or_none.return_value = job
        return r

    session.execute = AsyncMock(side_effect=execute_side_effect)
    session.commit = AsyncMock()
    return session


@pytest.mark.asyncio
async def test_executor_marks_job_running_then_completed():
    """Executor marks job as running, runs to completion, and marks completed."""
    mock_job = _make_mock_job()
    mock_agent = _make_mock_agent()
    llm_response = _make_llm_response(content="Task completed successfully.")

    session = _make_session_mock(mock_job, mock_agent)
    gateway_instance = MagicMock()
    gateway_instance.complete = AsyncMock(return_value=llm_response)

    with patch(f"{MOD}.AsyncSessionLocal", return_value=session), \
         patch(f"{MOD}.set_tenant_context", new_callable=AsyncMock), \
         patch(f"{MOD}.LLMGateway", return_value=gateway_instance), \
         patch(f"{MOD}._safe_check_interventions", new_callable=AsyncMock, return_value=[]), \
         patch(f"{MOD}._safe_deliver", new_callable=AsyncMock), \
         patch(f"{MOD}._load_tools", return_value={}), \
         patch(f"{MOD}.clear_interventions", new_callable=AsyncMock):

        from app.services.background_agent_executor import run_background_agent
        await run_background_agent(
            job_id=JOB_ID,
            agent_id=AGENT_ID,
            tenant_id=TENANT_ID,
            user_id=USER_ID,
            input_data={"task": "Test task"},
        )

        mock_job.start.assert_called_once()
        mock_job.complete.assert_called_once()


@pytest.mark.asyncio
async def test_executor_updates_execution_trace():
    """Executor updates execution_trace in DB after each iteration."""
    mock_job = _make_mock_job()
    mock_agent = _make_mock_agent()
    llm_response = _make_llm_response(content="Done.")

    session = _make_session_mock(mock_job, mock_agent)
    gateway_instance = MagicMock()
    gateway_instance.complete = AsyncMock(return_value=llm_response)

    with patch(f"{MOD}.AsyncSessionLocal", return_value=session), \
         patch(f"{MOD}.set_tenant_context", new_callable=AsyncMock), \
         patch(f"{MOD}.LLMGateway", return_value=gateway_instance), \
         patch(f"{MOD}._safe_check_interventions", new_callable=AsyncMock, return_value=[]), \
         patch(f"{MOD}._safe_deliver", new_callable=AsyncMock), \
         patch(f"{MOD}._load_tools", return_value={}), \
         patch(f"{MOD}.clear_interventions", new_callable=AsyncMock):

        from app.services.background_agent_executor import run_background_agent
        await run_background_agent(
            job_id=JOB_ID,
            agent_id=AGENT_ID,
            tenant_id=TENANT_ID,
            user_id=USER_ID,
            input_data={"task": "Trace test"},
        )

        # execution_trace should have been set on the job
        assert mock_job.execution_trace is not None
        assert isinstance(mock_job.execution_trace, list)
        assert len(mock_job.execution_trace) >= 1


@pytest.mark.asyncio
async def test_executor_marks_job_failed_on_error():
    """Executor marks job as failed when an exception occurs."""
    mock_job = _make_mock_job()

    # Main session: job loads, then agent query raises
    main_session = AsyncMock()
    main_session.__aenter__ = AsyncMock(return_value=main_session)
    main_session.__aexit__ = AsyncMock(return_value=False)
    nested = AsyncMock()
    nested.__aenter__ = AsyncMock(return_value=nested)
    nested.__aexit__ = AsyncMock(return_value=False)
    main_session.begin_nested = MagicMock(return_value=nested)
    main_session.commit = AsyncMock()

    call_idx = [0]
    def main_execute(*args, **kwargs):
        call_idx[0] += 1
        if call_idx[0] == 1:
            r = MagicMock()
            r.scalar_one_or_none.return_value = mock_job
            return r
        raise ValueError("Agent not found in test")
    main_session.execute = AsyncMock(side_effect=main_execute)

    # Fail session: returns job for marking failure
    fail_session = AsyncMock()
    fail_session.__aenter__ = AsyncMock(return_value=fail_session)
    fail_session.__aexit__ = AsyncMock(return_value=False)
    fail_result = MagicMock()
    fail_result.scalar_one_or_none.return_value = mock_job
    fail_session.execute = AsyncMock(return_value=fail_result)
    fail_session.commit = AsyncMock()

    # Webhook session: no agent found
    webhook_session = AsyncMock()
    webhook_session.__aenter__ = AsyncMock(return_value=webhook_session)
    webhook_session.__aexit__ = AsyncMock(return_value=False)
    webhook_result = MagicMock()
    webhook_result.scalar_one_or_none.return_value = None
    webhook_session.execute = AsyncMock(return_value=webhook_result)

    sessions = [main_session, fail_session, webhook_session]
    idx = [0]

    class FakeSessionLocal:
        def __init__(self):
            self._idx = idx[0]
            idx[0] += 1
        async def __aenter__(self):
            return sessions[min(self._idx, len(sessions) - 1)]
        async def __aexit__(self, *args):
            return False

    with patch(f"{MOD}.AsyncSessionLocal", FakeSessionLocal), \
         patch(f"{MOD}.set_tenant_context", new_callable=AsyncMock), \
         patch(f"{MOD}.clear_interventions", new_callable=AsyncMock):

        from app.services.background_agent_executor import run_background_agent
        await run_background_agent(
            job_id=JOB_ID,
            agent_id=AGENT_ID,
            tenant_id=TENANT_ID,
            user_id=USER_ID,
        )

        mock_job.start.assert_called_once()
        mock_job.fail.assert_called_once()
        assert "Agent not found" in mock_job.fail.call_args[0][0]


@pytest.mark.asyncio
async def test_executor_respects_budget_limits():
    """Executor stops when max_tool_calls budget is reached."""
    mock_job = _make_mock_job()
    mock_agent = _make_mock_agent()
    mock_agent.resource_constraints = {"max_tool_calls": 2, "timeout_seconds": 3600}

    tool_call = _make_tool_call("web_search", {"query": "test"})
    llm_with_tool = _make_llm_response(content="", tool_calls=[tool_call])

    session = _make_session_mock(mock_job, mock_agent)
    gateway_instance = MagicMock()
    gateway_instance.complete = AsyncMock(return_value=llm_with_tool)

    mock_skill = MagicMock()
    mock_skill.handler = AsyncMock(return_value={"ok": True})
    mock_skill.description = "Search"

    with patch(f"{MOD}.AsyncSessionLocal", return_value=session), \
         patch(f"{MOD}.set_tenant_context", new_callable=AsyncMock), \
         patch(f"{MOD}.LLMGateway", return_value=gateway_instance), \
         patch(f"{MOD}._safe_check_interventions", new_callable=AsyncMock, return_value=[]), \
         patch(f"{MOD}._safe_deliver", new_callable=AsyncMock), \
         patch(f"{MOD}._load_tools", return_value={"web_search": mock_skill}), \
         patch(f"{MOD}._execute_tool", new_callable=AsyncMock, return_value={"ok": True}), \
         patch(f"{MOD}._build_openai_tools", return_value=[{"type": "function", "function": {"name": "web_search"}}]), \
         patch(f"{MOD}.clear_interventions", new_callable=AsyncMock):

        from app.services.background_agent_executor import run_background_agent
        await run_background_agent(
            job_id=JOB_ID,
            agent_id=AGENT_ID,
            tenant_id=TENANT_ID,
            user_id=USER_ID,
            input_data={"task": "Budget test"},
        )

        # Should stop after 2 tool calls (budget limit)
        assert gateway_instance.complete.call_count == 2
        mock_job.complete.assert_called_once()


@pytest.mark.asyncio
async def test_executor_calls_evaluate_and_deliver():
    """Executor calls _safe_deliver (evaluate_and_deliver) on completion."""
    mock_job = _make_mock_job()
    mock_agent = _make_mock_agent()
    llm_response = _make_llm_response(content="All done.")

    session = _make_session_mock(mock_job, mock_agent)
    gateway_instance = MagicMock()
    gateway_instance.complete = AsyncMock(return_value=llm_response)

    with patch(f"{MOD}.AsyncSessionLocal", return_value=session), \
         patch(f"{MOD}.set_tenant_context", new_callable=AsyncMock), \
         patch(f"{MOD}.LLMGateway", return_value=gateway_instance), \
         patch(f"{MOD}._safe_check_interventions", new_callable=AsyncMock, return_value=[]), \
         patch(f"{MOD}._safe_deliver", new_callable=AsyncMock) as mock_deliver, \
         patch(f"{MOD}._load_tools", return_value={}), \
         patch(f"{MOD}.clear_interventions", new_callable=AsyncMock):

        from app.services.background_agent_executor import run_background_agent
        await run_background_agent(
            job_id=JOB_ID,
            agent_id=AGENT_ID,
            tenant_id=TENANT_ID,
            user_id=USER_ID,
            input_data={"task": "Deliver test"},
        )

        mock_deliver.assert_called_once()
        call_kwargs = mock_deliver.call_args.kwargs
        assert call_kwargs["agent_name"] == "Test Agent"
        assert call_kwargs["job_id"] == JOB_ID
