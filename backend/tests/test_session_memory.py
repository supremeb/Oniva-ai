"""Unit tests for session memory and plan/todo recitation.

Tests for:
- InMemorySessionMemory CRUD operations
- Plan/todo recitation context building
- Default templates
- Session isolation
"""

import pytest

from app.services.session_memory import (
    InMemorySessionMemory,
    get_session_memory,
    read_session_file,
    write_session_file,
    get_recitation_context,
)
from app.services.session_storage import FilesystemSessionMemory

TENANT = "test-tenant"


# ============================================================================
# InMemorySessionMemory Tests
# ============================================================================

class TestInMemorySessionMemory:
    """Tests for InMemorySessionMemory class."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """Reset singleton before each test."""
        InMemorySessionMemory.reset()
        yield
        InMemorySessionMemory.reset()

    def test_singleton_instance(self):
        """Test that get_instance returns singleton."""
        instance1 = InMemorySessionMemory.get_instance()
        instance2 = InMemorySessionMemory.get_instance()

        assert instance1 is instance2

    @pytest.mark.asyncio
    async def test_reset_clears_state(self):
        """Test that reset clears all sessions."""
        memory = InMemorySessionMemory.get_instance()
        # Create a session by reading (triggers auto-create)
        await memory.read(TENANT, "session-1", "plan.md")

        InMemorySessionMemory.reset()
        memory2 = InMemorySessionMemory.get_instance()

        # Should be empty after reset
        files = await memory2.list_files(TENANT, "session-1")
        assert len(files) == 0

    @pytest.mark.asyncio
    async def test_read_creates_default_session(self):
        """Test that reading creates session with defaults."""
        memory = InMemorySessionMemory.get_instance()

        plan = await memory.read(TENANT, "new-session", "plan.md")

        assert "Session Plan" in plan
        assert "No plan defined" in plan

    @pytest.mark.asyncio
    async def test_read_returns_written_content(self):
        """Test that reading returns previously written content."""
        memory = InMemorySessionMemory.get_instance()

        await memory.write(TENANT, "session-1", "plan.md", "# My Plan\n\nDo something")
        content = await memory.read(TENANT, "session-1", "plan.md")

        assert content == "# My Plan\n\nDo something"

    @pytest.mark.asyncio
    async def test_write_creates_new_files(self):
        """Test writing creates new files."""
        memory = InMemorySessionMemory.get_instance()

        await memory.write(TENANT, "session-1", "custom.md", "Custom content")
        content = await memory.read(TENANT, "session-1", "custom.md")

        assert content == "Custom content"

    @pytest.mark.asyncio
    async def test_append_adds_to_existing(self):
        """Test appending adds to existing content."""
        memory = InMemorySessionMemory.get_instance()

        await memory.write(TENANT, "session-1", "log.txt", "Line 1\n")
        await memory.append(TENANT, "session-1", "log.txt", "Line 2\n")
        content = await memory.read(TENANT, "session-1", "log.txt")

        assert content == "Line 1\nLine 2\n"

    @pytest.mark.asyncio
    async def test_list_files(self):
        """Test listing files in a session."""
        memory = InMemorySessionMemory.get_instance()

        # Trigger session creation
        await memory.read(TENANT, "session-1", "plan.md")
        files = await memory.list_files(TENANT, "session-1")

        assert "plan.md" in files
        assert "todo.md" in files
        assert "state.json" in files

    @pytest.mark.asyncio
    async def test_delete_file(self):
        """Test deleting a file."""
        memory = InMemorySessionMemory.get_instance()

        await memory.write(TENANT, "session-1", "temp.txt", "Temporary")
        result = await memory.delete_file(TENANT, "session-1", "temp.txt")
        content = await memory.read(TENANT, "session-1", "temp.txt")

        assert result is True
        assert content == ""  # Returns empty for non-existent file

    @pytest.mark.asyncio
    async def test_delete_session(self):
        """Test deleting entire session."""
        memory = InMemorySessionMemory.get_instance()

        await memory.read(TENANT, "session-1", "plan.md")  # Create session
        result = await memory.delete_session(TENANT, "session-1")
        files = await memory.list_files(TENANT, "session-1")

        assert result is True
        assert len(files) == 0

    @pytest.mark.asyncio
    async def test_session_isolation(self):
        """Test that sessions are isolated from each other."""
        memory = InMemorySessionMemory.get_instance()

        await memory.write(TENANT, "session-1", "plan.md", "Plan for session 1")
        await memory.write(TENANT, "session-2", "plan.md", "Plan for session 2")

        content1 = await memory.read(TENANT, "session-1", "plan.md")
        content2 = await memory.read(TENANT, "session-2", "plan.md")

        assert content1 == "Plan for session 1"
        assert content2 == "Plan for session 2"

    @pytest.mark.asyncio
    async def test_get_session_info(self):
        """Test getting session metadata."""
        memory = InMemorySessionMemory.get_instance()

        await memory.read(TENANT, "session-1", "plan.md", user_id="user-123")
        info = await memory.get_session_info(TENANT, "session-1")

        assert info is not None
        assert info["session_id"] == "session-1"
        assert info["user_id"] == "user-123"
        assert "created_at" in info
        assert "file_count" in info

    @pytest.mark.asyncio
    async def test_get_session_info_nonexistent(self):
        """Test getting info for non-existent session."""
        memory = InMemorySessionMemory.get_instance()

        info = await memory.get_session_info(TENANT, "nonexistent")

        assert info is None


# ============================================================================
# Convenience Function Tests
# ============================================================================

class TestConvenienceFunctions:
    """Tests for module-level convenience functions."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """Reset singleton before each test."""
        InMemorySessionMemory.reset()
        FilesystemSessionMemory.reset()
        yield
        InMemorySessionMemory.reset()
        FilesystemSessionMemory.reset()

    @pytest.mark.asyncio
    async def test_read_session_file(self):
        """Test read_session_file convenience function."""
        await write_session_file(TENANT, "session-1", "test.txt", "Hello")
        content = await read_session_file(TENANT, "session-1", "test.txt")

        assert content == "Hello"

    @pytest.mark.asyncio
    async def test_write_session_file(self):
        """Test write_session_file convenience function."""
        await write_session_file(TENANT, "session-1", "test.txt", "World")
        content = await read_session_file(TENANT, "session-1", "test.txt")

        assert content == "World"


# ============================================================================
# Recitation Context Tests
# ============================================================================

class TestRecitationContext:
    """Tests for plan/todo recitation context building."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """Reset singleton before each test."""
        InMemorySessionMemory.reset()
        FilesystemSessionMemory.reset()
        yield
        InMemorySessionMemory.reset()
        FilesystemSessionMemory.reset()

    @pytest.mark.asyncio
    async def test_recitation_includes_plan(self):
        """Test that recitation includes plan.md content."""
        await write_session_file(TENANT, "session-1", "plan.md", "# My Analysis Plan\n\nAnalyze FAANG stocks")

        context = await get_recitation_context(TENANT, "session-1")

        assert "Current Plan" in context
        assert "My Analysis Plan" in context
        assert "FAANG stocks" in context

    @pytest.mark.asyncio
    async def test_recitation_includes_todo(self):
        """Test that recitation includes todo.md content."""
        await write_session_file(TENANT, "session-1", "todo.md", "# Tasks\n\n1. Fetch data\n2. Analyze")

        context = await get_recitation_context(TENANT, "session-1")

        assert "Current Todo" in context
        assert "Fetch data" in context
        assert "Analyze" in context

    @pytest.mark.asyncio
    async def test_recitation_includes_instructions(self):
        """Test that recitation includes standard instructions."""
        context = await get_recitation_context(TENANT, "session-1")

        assert "Instructions" in context
        assert "Review the plan" in context
        assert "Update todo.md" in context
        assert "bash_tool" not in context

    @pytest.mark.asyncio
    async def test_recitation_with_default_templates(self):
        """Test recitation returns empty when both files are default templates."""
        context = await get_recitation_context(TENANT, "new-session")

        # When both plan and todo are defaults, recitation is skipped (returns empty)
        assert context == ""

    @pytest.mark.asyncio
    async def test_recitation_user_id_propagation(self):
        """Test that user_id is properly propagated."""
        context = await get_recitation_context(TENANT, "session-uid-test", user_id="user-456")

        # Check session was created with correct user via the active memory backend
        memory = get_session_memory()
        info = await memory.get_session_info(TENANT, "session-uid-test")

        assert info is not None
        assert info["user_id"] == "user-456"
