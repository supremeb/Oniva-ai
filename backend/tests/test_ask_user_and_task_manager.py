"""
Tests for ask_user skill (structured elicitation) and task_manager skills.

Tests cover:
- ask_user with valid options returns structured response
- ask_user with malformed options falls back to free-text
- ask_user with empty string returns error
- ask_user with multi_select includes flag
- create_task generates unique ID and persists
- update_task with invalid task_id returns error
- update_task on completed task returns error
- list_tasks returns correct summary counts
- list_tasks with status_filter filters correctly
- Concurrent create_task calls don't corrupt tasks.json
"""

import asyncio
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from app.agents.skills.context import SkillContext
from app.services.session_storage import SessionStorage


TENANT = "test-tenant"
STORAGE_PATCH = "app.services.session_storage.get_session_storage"


@pytest.fixture
def temp_sessions_dir():
    """Create a temporary directory for sessions."""
    temp_dir = tempfile.mkdtemp(prefix="test_ask_task_")
    yield Path(temp_dir)
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def session_storage(temp_sessions_dir):
    """Create a SessionStorage with temp directory."""
    return SessionStorage(base_path=temp_sessions_dir, checkpointing_enabled=False)


@pytest.fixture
async def setup_session(session_storage):
    """Create a session and return (session_id, session_storage)."""
    sid = "test-task-session"
    await session_storage.create_session(TENANT, sid, "user-1")
    return sid, session_storage


def _make_ctx(session_id: str) -> SkillContext:
    """Create a SkillContext for testing."""
    return SkillContext(
        user_id="user-1",
        tenant_id=TENANT,
        session_id=session_id,
    )


# =============================================================================
# ask_user tests
# =============================================================================


class TestAskUser:
    @pytest.mark.asyncio
    async def test_valid_options(self):
        """ask_user with valid options returns structured response."""
        from app.skills.ask_user.tool import ask_user

        ctx = SkillContext(user_id="u1", tenant_id="t1", session_id="s1")
        result = await ask_user.__wrapped__(
            ctx,
            question="Which format?",
            options=[
                {"label": "PDF", "description": "Portable Document Format"},
                {"label": "CSV", "id": "csv_opt"},
            ],
        )
        assert result["success"] is True
        assert result["type"] == "human_input_required"
        assert result["question"] == "Which format?"
        assert len(result["options"]) == 2
        assert result["options"][0]["id"] == "opt_0"
        assert result["options"][0]["label"] == "PDF"
        assert result["options"][1]["id"] == "csv_opt"
        assert result["multi_select"] is False

    @pytest.mark.asyncio
    async def test_malformed_options_fallback(self):
        """ask_user with malformed options falls back to free-text."""
        from app.skills.ask_user.tool import ask_user

        ctx = SkillContext(user_id="u1", tenant_id="t1", session_id="s1")
        result = await ask_user.__wrapped__(
            ctx,
            question="Pick one",
            options=[
                "not a dict",
                {"no_label_key": "value"},
            ],
        )
        assert result["success"] is True
        assert result["options"] is None  # Fell back to free-text

    @pytest.mark.asyncio
    async def test_empty_question(self):
        """ask_user with empty string returns error."""
        from app.skills.ask_user.tool import ask_user

        ctx = SkillContext(user_id="u1", tenant_id="t1", session_id="s1")
        result = await ask_user.__wrapped__(ctx, question="")
        assert result["success"] is False
        assert "empty" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_whitespace_question(self):
        """ask_user with whitespace-only question returns error."""
        from app.skills.ask_user.tool import ask_user

        ctx = SkillContext(user_id="u1", tenant_id="t1", session_id="s1")
        result = await ask_user.__wrapped__(ctx, question="   ")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_multi_select(self):
        """ask_user with multi_select=True includes flag in response."""
        from app.skills.ask_user.tool import ask_user

        ctx = SkillContext(user_id="u1", tenant_id="t1", session_id="s1")
        result = await ask_user.__wrapped__(
            ctx,
            question="Select all that apply",
            options=[{"label": "A"}, {"label": "B"}],
            multi_select=True,
        )
        assert result["success"] is True
        assert result["multi_select"] is True

    @pytest.mark.asyncio
    async def test_no_options_free_text(self):
        """ask_user without options returns free-text response."""
        from app.skills.ask_user.tool import ask_user

        ctx = SkillContext(user_id="u1", tenant_id="t1", session_id="s1")
        result = await ask_user.__wrapped__(ctx, question="What do you think?")
        assert result["success"] is True
        assert result["options"] is None


# =============================================================================
# task_manager tests
# =============================================================================


class TestTaskManager:
    @pytest.mark.asyncio
    async def test_create_task(self, setup_session, session_storage):
        """create_task generates unique ID and persists to tasks.json."""
        sid, storage = setup_session
        ctx = _make_ctx(sid)

        from app.skills.task_manager.tool import create_task

        with patch(STORAGE_PATCH, return_value=storage):
            result = await create_task.__wrapped__(ctx, title="Research AI safety")

        assert result["success"] is True
        assert result["action"] == "created"
        assert result["task"]["title"] == "Research AI safety"
        assert result["task"]["id"].startswith("task_")
        assert result["task"]["status"] == "pending"

    @pytest.mark.asyncio
    async def test_update_task_not_found(self, setup_session, session_storage):
        """update_task with invalid task_id returns error."""
        sid, storage = setup_session
        ctx = _make_ctx(sid)

        from app.skills.task_manager.tool import update_task

        with patch(STORAGE_PATCH, return_value=storage):
            result = await update_task.__wrapped__(ctx, task_id="nonexistent", status="completed")

        assert result["success"] is False
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_update_completed_task_rejected(self, setup_session, session_storage):
        """update_task on completed task returns error."""
        sid, storage = setup_session
        ctx = _make_ctx(sid)

        from app.skills.task_manager.tool import create_task, update_task

        with patch(STORAGE_PATCH, return_value=storage):
            created = await create_task.__wrapped__(ctx, title="Task 1")
            task_id = created["task"]["id"]

            await update_task.__wrapped__(ctx, task_id=task_id, status="in_progress")
            await update_task.__wrapped__(ctx, task_id=task_id, status="completed")

            # Try to modify completed task
            result = await update_task.__wrapped__(ctx, task_id=task_id, status="pending")

        assert result["success"] is False
        assert "completed" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_list_tasks_summary(self, setup_session, session_storage):
        """list_tasks returns correct summary counts."""
        sid, storage = setup_session
        ctx = _make_ctx(sid)

        from app.skills.task_manager.tool import create_task, update_task, list_tasks

        with patch(STORAGE_PATCH, return_value=storage):
            t1 = await create_task.__wrapped__(ctx, title="Task 1")
            t2 = await create_task.__wrapped__(ctx, title="Task 2")
            await create_task.__wrapped__(ctx, title="Task 3")

            await update_task.__wrapped__(ctx, task_id=t1["task"]["id"], status="in_progress")
            await update_task.__wrapped__(ctx, task_id=t2["task"]["id"], status="in_progress")
            await update_task.__wrapped__(ctx, task_id=t2["task"]["id"], status="completed")

            result = await list_tasks.__wrapped__(ctx)

        assert result["success"] is True
        assert result["summary"]["total"] == 3
        assert result["summary"]["pending"] == 1
        assert result["summary"]["in_progress"] == 1
        assert result["summary"]["completed"] == 1
        assert len(result["tasks"]) == 3

    @pytest.mark.asyncio
    async def test_list_tasks_filter(self, setup_session, session_storage):
        """list_tasks with status_filter filters correctly."""
        sid, storage = setup_session
        ctx = _make_ctx(sid)

        from app.skills.task_manager.tool import create_task, update_task, list_tasks

        with patch(STORAGE_PATCH, return_value=storage):
            t1 = await create_task.__wrapped__(ctx, title="Done task")
            await create_task.__wrapped__(ctx, title="Pending task")
            await update_task.__wrapped__(ctx, task_id=t1["task"]["id"], status="in_progress")
            await update_task.__wrapped__(ctx, task_id=t1["task"]["id"], status="completed")

            result = await list_tasks.__wrapped__(ctx, status_filter="completed")

        assert result["success"] is True
        assert len(result["tasks"]) == 1
        assert result["tasks"][0]["title"] == "Done task"

    @pytest.mark.asyncio
    async def test_concurrent_creates(self, setup_session, session_storage):
        """Concurrent create_task calls don't corrupt tasks.json."""
        sid, storage = setup_session
        ctx = _make_ctx(sid)

        from app.skills.task_manager.tool import create_task, list_tasks

        with patch(STORAGE_PATCH, return_value=storage):
            # Create 10 tasks concurrently
            coros = [
                create_task.__wrapped__(ctx, title=f"Task {i}")
                for i in range(10)
            ]
            results = await asyncio.gather(*coros)

            # All should succeed
            assert all(r["success"] for r in results)

            # All should be persisted
            listing = await list_tasks.__wrapped__(ctx)

        assert listing["summary"]["total"] == 10

    @pytest.mark.asyncio
    async def test_create_task_with_description(self, setup_session, session_storage):
        """create_task stores optional description."""
        sid, storage = setup_session
        ctx = _make_ctx(sid)

        from app.skills.task_manager.tool import create_task

        with patch(STORAGE_PATCH, return_value=storage):
            result = await create_task.__wrapped__(
                ctx, title="Complex task", description="This needs careful attention"
            )

        assert result["task"]["description"] == "This needs careful attention"

    @pytest.mark.asyncio
    async def test_update_task_title(self, setup_session, session_storage):
        """update_task can change title."""
        sid, storage = setup_session
        ctx = _make_ctx(sid)

        from app.skills.task_manager.tool import create_task, update_task

        with patch(STORAGE_PATCH, return_value=storage):
            created = await create_task.__wrapped__(ctx, title="Old title")
            task_id = created["task"]["id"]

            result = await update_task.__wrapped__(ctx, task_id=task_id, title="New title")

        assert result["success"] is True
        assert result["task"]["title"] == "New title"
