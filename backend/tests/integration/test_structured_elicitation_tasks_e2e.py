"""
E2E Integration Tests: Structured Elicitation (ask_user v2) + Task System

Tests exercise the full code path through AutonomousExecutor:
- ask_user with structured options → SSE human_input_required event with options/multiSelect
- create_task/update_task/list_tasks → tasks.json persisted → recitation in _build_messages
- Combined: task + elicitation in one session

All LLM calls are mocked. Tests verify the SSE events, session state, and message history.
"""

import asyncio
import json
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.autonomous_executor import AutonomousExecutor
from app.services.session_storage import SessionStorage


# Valid UUIDs required by AutonomousExecutor._iterate() for llm_gateway.complete()
TENANT_UUID = "6d4200f2-49a8-4c18-b05c-2505e7588087"
USER_UUID = "4998feaf-4a91-4a42-b993-5ab482bf6eae"
STORAGE_PATCH = "app.services.session_storage.get_session_storage"


# ===========================================================================
# Fixtures (self-contained, no dependency on conftest.test_session)
# ===========================================================================

@pytest.fixture
def storage(tmp_path):
    """Real SessionStorage backed by tmp_path filesystem."""
    return SessionStorage(base_path=tmp_path / "sessions", checkpointing_enabled=False)


@pytest.fixture
def session_id():
    """Unique session ID for each test."""
    return f"e2e-{uuid.uuid4().hex[:8]}"


# ===========================================================================
# Helpers
# ===========================================================================

def _make_executor(storage, sse_queue=None):
    """Build an AutonomousExecutor with a real SessionStorage and mocked LLM."""
    skill_registry = MagicMock()
    llm_gateway = AsyncMock()
    queue = sse_queue or asyncio.Queue()

    executor = AutonomousExecutor(
        session_storage=storage,
        skill_registry=skill_registry,
        llm_gateway=llm_gateway,
        sse_queue=queue,
    )
    return executor, queue, llm_gateway, skill_registry


def _register_skills(skill_registry, skill_map):
    """
    Mock skill_registry.get_skill() to return handlers by name.
    skill_map: dict[str, callable] of skill_name -> handler
    """
    def get_skill(name):
        handler = skill_map.get(name)
        if handler is None:
            return None
        mock = MagicMock()
        mock.handler = handler
        return mock

    skill_registry.get_skill.side_effect = get_skill


def _planning_skill_stubs():
    """Mock stubs for skills that aren't under test."""
    return {
        "present_plan": AsyncMock(return_value={"success": True}),
        "complete_task": AsyncMock(return_value={
            "success": True, "type": "complete",
            "summary": "Done", "key_findings": [],
        }),
    }


async def _start_session(executor, storage, session_id, plan_steps=None, message="test"):
    """Helper to start a session on the executor."""
    steps = plan_steps or ["Step 1"]
    await executor.start_session(
        agent_config=None,
        plan={"title": "Test Plan", "steps": steps},
        todo_items=steps,
        message=message,
        user_id=USER_UUID,
        tenant_id=TENANT_UUID,
        session_id=session_id,
    )


async def _run_with_input(executor, queue, session_id, respond_fn):
    """
    Run executor.execute() and a responder coroutine concurrently.
    respond_fn: async callable that reads from queue and sends input.
    """
    await asyncio.gather(
        executor.execute(session_id),
        respond_fn(queue, executor, session_id),
    )


# ===========================================================================
# Scenario 1: ask_user with structured options emits correct SSE event
# ===========================================================================

class TestStructuredElicitationE2E:
    """Verify ask_user → AutonomousExecutor → SSE event chain."""

    @pytest.mark.asyncio
    async def test_ask_user_options_flow(self, storage, session_id):
        """
        When the LLM calls ask_user with options, the executor should:
        1. Stream human_input_required SSE with options + multiSelect
        2. Wait for user input
        3. Append user response to message history
        """
        executor, queue, llm_gateway, skill_registry = _make_executor(storage)

        from app.skills.ask_user.tool import ask_user
        from app.skills.task_manager.tool import create_task, update_task, list_tasks

        _register_skills(skill_registry, {
            "ask_user": ask_user,
            "create_task": create_task,
            "update_task": update_task,
            "list_tasks": list_tasks,
            **_planning_skill_stubs(),
        })

        # LLM response 1: call ask_user with structured options
        ask_response = MagicMock()
        ask_response.content = json.dumps({
            "tool": "ask_user",
            "args": {
                "question": "Which analysis format would you prefer?",
                "options": [
                    {"label": "PDF Report", "description": "Formatted PDF document"},
                    {"label": "CSV Data", "id": "csv"},
                    {"label": "JSON API", "description": "Machine-readable format"},
                ],
                "multi_select": False,
            },
        })

        # LLM response 2: call complete_task after user responds
        complete_response = MagicMock()
        complete_response.content = json.dumps({
            "tool": "complete_task",
            "args": {"summary": "User chose PDF Report", "key_findings": ["Preference: PDF"]},
        })

        llm_gateway.complete.side_effect = [ask_response, complete_response]

        with patch(STORAGE_PATCH, return_value=storage):
            await _start_session(executor, storage, session_id,
                                 plan_steps=["Ask user preference", "Complete"],
                                 message="Help me create a report")

            async def respond(q, ex, sid):
                while True:
                    try:
                        event = await asyncio.wait_for(q.get(), timeout=5)
                        if event.get("type") == "human_input_required":
                            data = event.get("data", {})
                            # Verify SSE event has options
                            assert data["question"] == "Which analysis format would you prefer?"
                            assert len(data["options"]) == 3
                            assert data["options"][0]["label"] == "PDF Report"
                            assert data["options"][1]["id"] == "csv"
                            assert data["multiSelect"] is False

                            await asyncio.sleep(0.05)
                            await ex.send_human_input(sid, "PDF Report")
                            return
                    except asyncio.TimeoutError:
                        pytest.fail("Never received human_input_required SSE event")

            await _run_with_input(executor, queue, session_id, respond)

        # Verify user response was added to message history
        user_msgs = [m for m in executor._message_history if m["role"] == "user"]
        response_msgs = [m for m in user_msgs if "PDF Report" in m.get("content", "")]
        assert len(response_msgs) >= 1, "User's option selection should be in message history"

    @pytest.mark.asyncio
    async def test_ask_user_multi_select_flow(self, storage, session_id):
        """Multi-select ask_user sets multiSelect=True in SSE event."""
        executor, queue, llm_gateway, skill_registry = _make_executor(storage)

        from app.skills.ask_user.tool import ask_user
        from app.skills.task_manager.tool import create_task, update_task, list_tasks

        _register_skills(skill_registry, {
            "ask_user": ask_user,
            "create_task": create_task,
            "update_task": update_task,
            "list_tasks": list_tasks,
            **_planning_skill_stubs(),
        })

        ask_response = MagicMock()
        ask_response.content = json.dumps({
            "tool": "ask_user",
            "args": {
                "question": "Select all data sources",
                "options": [
                    {"label": "Bloomberg"},
                    {"label": "Reuters"},
                    {"label": "Yahoo Finance"},
                ],
                "multi_select": True,
            },
        })

        complete_response = MagicMock()
        complete_response.content = json.dumps({
            "tool": "complete_task",
            "args": {"summary": "Selected sources", "key_findings": []},
        })

        llm_gateway.complete.side_effect = [ask_response, complete_response]

        captured_event = {}

        with patch(STORAGE_PATCH, return_value=storage):
            await _start_session(executor, storage, session_id,
                                 plan_steps=["Select sources"])

            async def respond(q, ex, sid):
                while True:
                    try:
                        event = await asyncio.wait_for(q.get(), timeout=5)
                        if event.get("type") == "human_input_required":
                            captured_event.update(event.get("data", {}))
                            await asyncio.sleep(0.05)
                            await ex.send_human_input(sid, "Bloomberg, Reuters")
                            return
                    except asyncio.TimeoutError:
                        pytest.fail("Never received human_input_required event")

            await _run_with_input(executor, queue, session_id, respond)

        assert captured_event["multiSelect"] is True
        assert captured_event["question"] == "Select all data sources"
        assert len(captured_event["options"]) == 3

    @pytest.mark.asyncio
    async def test_ask_user_free_text_no_options_in_event(self, storage, session_id):
        """ask_user without options does NOT include options in SSE event."""
        executor, queue, llm_gateway, skill_registry = _make_executor(storage)

        from app.skills.ask_user.tool import ask_user
        from app.skills.task_manager.tool import create_task, update_task, list_tasks

        _register_skills(skill_registry, {
            "ask_user": ask_user,
            "create_task": create_task,
            "update_task": update_task,
            "list_tasks": list_tasks,
            **_planning_skill_stubs(),
        })

        ask_response = MagicMock()
        ask_response.content = json.dumps({
            "tool": "ask_user",
            "args": {"question": "What is your risk tolerance?"},
        })

        complete_response = MagicMock()
        complete_response.content = json.dumps({
            "tool": "complete_task",
            "args": {"summary": "Got risk tolerance", "key_findings": []},
        })

        llm_gateway.complete.side_effect = [ask_response, complete_response]

        captured_event = {}

        with patch(STORAGE_PATCH, return_value=storage):
            await _start_session(executor, storage, session_id,
                                 plan_steps=["Ask risk"])

            async def respond(q, ex, sid):
                while True:
                    try:
                        event = await asyncio.wait_for(q.get(), timeout=5)
                        if event.get("type") == "human_input_required":
                            captured_event.update(event.get("data", {}))
                            await asyncio.sleep(0.05)
                            await ex.send_human_input(sid, "Moderate risk")
                            return
                    except asyncio.TimeoutError:
                        pytest.fail("Never received human_input_required event")

            await _run_with_input(executor, queue, session_id, respond)

        # Free-text mode: no options in event
        assert "options" not in captured_event
        assert captured_event["question"] == "What is your risk tolerance?"


# ===========================================================================
# Scenario 2: Task System through AutonomousExecutor
# ===========================================================================

class TestTaskSystemE2E:
    """Verify create_task/update_task/list_tasks → session storage → recitation."""

    @pytest.mark.asyncio
    async def test_task_crud_and_persistence(self, storage, session_id):
        """
        Create + update + list tasks through executor tool execution,
        verify tasks.json is persisted and recited in messages.
        """
        executor, queue, llm_gateway, skill_registry = _make_executor(storage)

        from app.skills.task_manager.tool import create_task, update_task, list_tasks

        _register_skills(skill_registry, {
            "create_task": create_task,
            "update_task": update_task,
            "list_tasks": list_tasks,
            "ask_user": AsyncMock(return_value={"success": True}),
            **_planning_skill_stubs(),
        })

        with patch(STORAGE_PATCH, return_value=storage):
            await _start_session(executor, storage, session_id,
                                 plan_steps=["Create tasks", "Update tasks"])

            # Create tasks directly via _execute_tool
            result1 = await executor._execute_tool("create_task", {"title": "Gather market data"})
            assert result1["success"] is True
            task1_id = result1["task"]["id"]

            result2 = await executor._execute_tool("create_task", {
                "title": "Analyze trends",
                "description": "Use AAPL + MSFT data",
            })
            assert result2["success"] is True

            # Verify tasks.json was written
            content = await storage.read(TENANT_UUID, session_id, "tasks.json")
            assert content is not None
            tasks_data = json.loads(content)
            assert len(tasks_data) == 2
            assert tasks_data[0]["title"] == "Gather market data"
            assert tasks_data[1]["description"] == "Use AAPL + MSFT data"

            # Update task 1 to in_progress
            update_result = await executor._execute_tool("update_task", {
                "task_id": task1_id,
                "status": "in_progress",
            })
            assert update_result["success"] is True
            assert update_result["task"]["status"] == "in_progress"

            # List tasks
            list_result = await executor._execute_tool("list_tasks", {})
            assert list_result["success"] is True
            assert list_result["summary"]["total"] == 2
            assert list_result["summary"]["in_progress"] == 1
            assert list_result["summary"]["pending"] == 1

    @pytest.mark.asyncio
    async def test_task_recitation_in_messages(self, storage, session_id):
        """
        Verify that tasks.json content is recited in _build_messages
        instead of todo.md when tasks exist.
        """
        executor, queue, llm_gateway, skill_registry = _make_executor(storage)

        from app.skills.task_manager.tool import create_task, update_task, list_tasks

        _register_skills(skill_registry, {
            "create_task": create_task,
            "update_task": update_task,
            "list_tasks": list_tasks,
            "ask_user": AsyncMock(return_value={"success": True}),
            **_planning_skill_stubs(),
        })

        with patch(STORAGE_PATCH, return_value=storage):
            await _start_session(executor, storage, session_id,
                                 plan_steps=["Step A", "Step B"])

            # Before task creation → todo.md fallback
            plan = await storage.read(TENANT_UUID, session_id, "plan.md") or ""
            todo = await storage.read(TENANT_UUID, session_id, "todo.md") or ""

            task_section = ""
            tasks_content = await storage.read(TENANT_UUID, session_id, "tasks.json")
            if tasks_content:
                tasks_data = json.loads(tasks_content)
                task_lines = [f"- [{t['status']}] {t['title']}" for t in tasks_data]
                task_section = "## Tasks\n" + "\n".join(task_lines)
            if not task_section:
                task_section = f"## Todo (Progress)\n{todo}"

            messages_before = executor._build_messages(plan, task_section, 1)
            last_content = messages_before[-1]["content"]
            assert "## Todo (Progress)" in last_content

            # Now create a task
            await executor._execute_tool("create_task", {"title": "Gather data"})
            listing = await executor._execute_tool("list_tasks", {})
            tid = listing["tasks"][0]["id"]
            await executor._execute_tool("update_task", {"task_id": tid, "status": "in_progress"})

            # Rebuild recitation — should now use tasks.json
            task_section2 = ""
            tasks_content2 = await storage.read(TENANT_UUID, session_id, "tasks.json")
            if tasks_content2:
                tasks_data2 = json.loads(tasks_content2)
                task_lines2 = [f"- [{t['status']}] {t['title']}" for t in tasks_data2]
                task_section2 = "## Tasks\n" + "\n".join(task_lines2)

            messages_after = executor._build_messages(plan, task_section2, 2)
            last_content_after = messages_after[-1]["content"]
            assert "## Tasks" in last_content_after
            assert "- [in_progress] Gather data" in last_content_after
            assert "## Todo (Progress)" not in last_content_after

    @pytest.mark.asyncio
    async def test_task_update_rejected_on_completed(self, storage, session_id):
        """update_task on a completed task returns error through executor."""
        executor, queue, llm_gateway, skill_registry = _make_executor(storage)

        from app.skills.task_manager.tool import create_task, update_task, list_tasks

        _register_skills(skill_registry, {
            "create_task": create_task,
            "update_task": update_task,
            "list_tasks": list_tasks,
            "ask_user": AsyncMock(return_value={"success": True}),
            **_planning_skill_stubs(),
        })

        with patch(STORAGE_PATCH, return_value=storage):
            await _start_session(executor, storage, session_id,
                                 plan_steps=["Test"])

            # Create → in_progress → completed
            r = await executor._execute_tool("create_task", {"title": "Quick task"})
            tid = r["task"]["id"]
            await executor._execute_tool("update_task", {"task_id": tid, "status": "in_progress"})
            await executor._execute_tool("update_task", {"task_id": tid, "status": "completed"})

            # Try to modify completed task
            result = await executor._execute_tool("update_task", {"task_id": tid, "status": "pending"})
            assert result["success"] is False
            assert "completed" in result["error"].lower()


# ===========================================================================
# Scenario 3: Combined flow — tasks + elicitation in one session
# ===========================================================================

class TestCombinedFlowE2E:
    """Verify both task tools and ask_user work together in the same session."""

    @pytest.mark.asyncio
    async def test_create_task_then_ask_user(self, storage, session_id):
        """
        LLM creates a task, then asks user a structured question.
        Both should work in sequence without interference.
        """
        executor, queue, llm_gateway, skill_registry = _make_executor(storage)

        from app.skills.ask_user.tool import ask_user
        from app.skills.task_manager.tool import create_task, update_task, list_tasks

        _register_skills(skill_registry, {
            "ask_user": ask_user,
            "create_task": create_task,
            "update_task": update_task,
            "list_tasks": list_tasks,
            **_planning_skill_stubs(),
        })

        # LLM response sequence: create_task → ask_user → complete
        r1 = MagicMock()
        r1.content = json.dumps({
            "tool": "create_task",
            "args": {"title": "Analyze portfolio risk"},
        })

        r2 = MagicMock()
        r2.content = json.dumps({
            "tool": "ask_user",
            "args": {
                "question": "Which risk model?",
                "options": [
                    {"label": "VaR", "description": "Value at Risk"},
                    {"label": "CVaR", "description": "Conditional VaR"},
                ],
            },
        })

        r3 = MagicMock()
        r3.content = json.dumps({
            "tool": "complete_task",
            "args": {"summary": "Analysis complete", "key_findings": ["Used VaR model"]},
        })

        llm_gateway.complete.side_effect = [r1, r2, r3]

        captured_events = []

        with patch(STORAGE_PATCH, return_value=storage):
            await _start_session(executor, storage, session_id,
                                 plan_steps=["Create task", "Ask preference", "Complete"],
                                 message="Run combined flow")

            async def respond(q, ex, sid):
                while True:
                    try:
                        event = await asyncio.wait_for(q.get(), timeout=5)
                        captured_events.append(event)
                        if event.get("type") == "human_input_required":
                            await asyncio.sleep(0.05)
                            await ex.send_human_input(sid, "VaR")
                            return
                    except asyncio.TimeoutError:
                        return

            await _run_with_input(executor, queue, session_id, respond)

        # Verify task was created (persisted in tasks.json)
        content = await storage.read(TENANT_UUID, session_id, "tasks.json")
        assert content is not None
        tasks = json.loads(content)
        assert len(tasks) == 1
        assert tasks[0]["title"] == "Analyze portfolio risk"

        # Verify human_input_required event was emitted
        input_events = [e for e in captured_events if e.get("type") == "human_input_required"]
        assert len(input_events) == 1
        assert input_events[0]["data"]["question"] == "Which risk model?"
        assert len(input_events[0]["data"]["options"]) == 2
