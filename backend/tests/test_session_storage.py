"""
Unit tests for SessionStorage (Simplified Memory Architecture).

Tests cover:
- Session creation and management
- File read/write/append operations
- Recitation context generation
- Logging and state management
- Edge cases and error handling
- Backward compatibility with InMemorySessionMemory
"""

import pytest
import tempfile
import shutil
import json
from pathlib import Path

from app.services.session_storage import (
    SessionStorage,
    FilesystemSessionMemory,
    get_session_storage,
    reset_session_storage,
)

TENANT = "test-tenant"


@pytest.fixture
def temp_sessions_dir():
    """Create a temporary directory for sessions."""
    temp_dir = tempfile.mkdtemp(prefix="test_sessions_")
    yield Path(temp_dir)
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def session_storage(temp_sessions_dir):
    """Create a SessionStorage with temp directory."""
    return SessionStorage(base_path=temp_sessions_dir, checkpointing_enabled=False)


class TestSessionCreation:
    """Tests for creating and managing sessions."""

    @pytest.mark.asyncio
    async def test_create_session(self, session_storage):
        """Test creating a new session."""
        info = await session_storage.create_session(
            tenant_id=TENANT,
            session_id="test_session_123",
            user_id="user_456",
        )

        assert info.session_id == "test_session_123"
        assert info.user_id == "user_456"
        assert info.status == "active"
        assert info.file_count >= 4  # plan.md, todo.md, state.json, logs/history.md
        assert info.created_at is not None

    @pytest.mark.asyncio
    async def test_create_session_creates_directory_structure(self, session_storage):
        """Test that session creation creates proper directory structure."""
        await session_storage.create_session(TENANT, "test_session", "user_1")

        session_path = session_storage.base_path / TENANT / "test_session"
        assert session_path.exists()
        assert (session_path / "plan.md").exists()
        assert (session_path / "todo.md").exists()
        assert (session_path / "state.json").exists()
        assert (session_path / "logs").is_dir()
        assert (session_path / "logs" / "history.md").exists()

    @pytest.mark.asyncio
    async def test_create_session_duplicate_fails(self, session_storage):
        """Test that creating duplicate session fails."""
        await session_storage.create_session(TENANT, "test_session", "user_1")

        with pytest.raises(ValueError, match="already exists"):
            await session_storage.create_session(TENANT, "test_session", "user_1")

    @pytest.mark.asyncio
    async def test_create_session_invalid_id(self, session_storage):
        """Test that invalid session IDs are rejected."""
        with pytest.raises(ValueError, match="Invalid session_id"):
            await session_storage.create_session(TENANT, "invalid/session", "user_1")

        with pytest.raises(ValueError, match="Invalid session_id"):
            await session_storage.create_session(TENANT, "../escape", "user_1")

    @pytest.mark.asyncio
    async def test_get_or_create_session_new(self, session_storage):
        """Test get_or_create_session creates new session if not exists."""
        info = await session_storage.get_or_create_session(TENANT, "new_session", "user_1")

        assert info.session_id == "new_session"
        assert info.user_id == "user_1"

    @pytest.mark.asyncio
    async def test_get_or_create_session_existing(self, session_storage):
        """Test get_or_create_session returns existing session."""
        await session_storage.create_session(TENANT, "existing_session", "user_1")

        # Should not raise, should return existing
        info = await session_storage.get_or_create_session(TENANT, "existing_session", "user_2")

        assert info.session_id == "existing_session"
        # user_id from original creation
        assert info.user_id == "user_1"


class TestSessionFileOperations:
    """Tests for reading and writing files."""

    @pytest.mark.asyncio
    async def test_write_and_read_file(self, session_storage):
        """Test writing and reading a file."""
        await session_storage.create_session(TENANT, "test_session", "user_1")

        await session_storage.write(TENANT, "test_session", "custom.txt", "Hello, World!")
        content = await session_storage.read(TENANT, "test_session", "custom.txt")

        assert content == "Hello, World!"

    @pytest.mark.asyncio
    async def test_read_nonexistent_file_returns_empty(self, session_storage):
        """Test reading non-existent file returns empty string."""
        await session_storage.create_session(TENANT, "test_session", "user_1")

        content = await session_storage.read(TENANT, "test_session", "nonexistent.txt")

        assert content == ""

    @pytest.mark.asyncio
    async def test_write_creates_parent_directories(self, session_storage):
        """Test that write creates parent directories."""
        await session_storage.create_session(TENANT, "test_session", "user_1")

        await session_storage.write(
            TENANT,
            "test_session",
            "deep/nested/path/file.txt",
            "nested content",
        )

        content = await session_storage.read(TENANT, "test_session", "deep/nested/path/file.txt")
        assert content == "nested content"

    @pytest.mark.asyncio
    async def test_append_file(self, session_storage):
        """Test appending to a file."""
        await session_storage.create_session(TENANT, "test_session", "user_1")

        await session_storage.write(TENANT, "test_session", "log.txt", "Line 1\n")
        await session_storage.append(TENANT, "test_session", "log.txt", "Line 2\n")
        await session_storage.append(TENANT, "test_session", "log.txt", "Line 3\n")

        content = await session_storage.read(TENANT, "test_session", "log.txt")

        assert content == "Line 1\nLine 2\nLine 3\n"

    @pytest.mark.asyncio
    async def test_append_creates_file_if_not_exists(self, session_storage):
        """Test that append creates file if it doesn't exist."""
        await session_storage.create_session(TENANT, "test_session", "user_1")

        await session_storage.append(TENANT, "test_session", "new_log.txt", "First line\n")

        content = await session_storage.read(TENANT, "test_session", "new_log.txt")
        assert content == "First line\n"

    @pytest.mark.asyncio
    async def test_delete_file(self, session_storage):
        """Test deleting a file."""
        await session_storage.create_session(TENANT, "test_session", "user_1")
        await session_storage.write(TENANT, "test_session", "to_delete.txt", "delete me")

        deleted = await session_storage.delete_file(TENANT, "test_session", "to_delete.txt")

        assert deleted is True
        content = await session_storage.read(TENANT, "test_session", "to_delete.txt")
        assert content == ""

    @pytest.mark.asyncio
    async def test_delete_nonexistent_file(self, session_storage):
        """Test deleting non-existent file returns False."""
        await session_storage.create_session(TENANT, "test_session", "user_1")

        deleted = await session_storage.delete_file(TENANT, "test_session", "nonexistent.txt")

        assert deleted is False

    @pytest.mark.asyncio
    async def test_list_files(self, session_storage):
        """Test listing files in a session."""
        await session_storage.create_session(TENANT, "test_session", "user_1")
        await session_storage.write(TENANT, "test_session", "custom1.txt", "content1")
        await session_storage.write(TENANT, "test_session", "custom2.txt", "content2")

        files = await session_storage.list_files(TENANT, "test_session")

        assert "plan.md" in files
        assert "todo.md" in files
        assert "state.json" in files
        assert "custom1.txt" in files
        assert "custom2.txt" in files

    @pytest.mark.asyncio
    async def test_path_traversal_prevention_read(self, session_storage):
        """Test that path traversal returns empty string (safe behavior)."""
        await session_storage.create_session(TENANT, "test_session", "user_1")

        # Path traversal attempts return empty string (file not found within session)
        content = await session_storage.read(TENANT, "test_session", "../other_session/secret.txt")
        assert content == ""

    @pytest.mark.asyncio
    async def test_path_traversal_prevention_write(self, session_storage):
        """Test that path traversal is prevented on write."""
        await session_storage.create_session(TENANT, "test_session", "user_1")

        with pytest.raises(ValueError, match="Path traversal"):
            await session_storage.write(
                TENANT,
                "test_session",
                "../../etc/passwd",
                "malicious content",
            )


class TestSessionManagement:
    """Tests for session lifecycle management."""

    @pytest.mark.asyncio
    async def test_get_session(self, session_storage):
        """Test getting session information."""
        await session_storage.create_session(TENANT, "test_session", "user_1")

        info = await session_storage.get_session(TENANT, "test_session")

        assert info.session_id == "test_session"
        assert info.user_id == "user_1"
        assert info.status == "active"

    @pytest.mark.asyncio
    async def test_get_nonexistent_session(self, session_storage):
        """Test getting non-existent session raises error."""
        with pytest.raises(ValueError, match="not found"):
            await session_storage.get_session(TENANT, "nonexistent")

    @pytest.mark.asyncio
    async def test_delete_session(self, session_storage):
        """Test deleting a session."""
        await session_storage.create_session(TENANT, "test_session", "user_1")

        deleted = await session_storage.delete_session(TENANT, "test_session")

        assert deleted is True

        with pytest.raises(ValueError, match="not found"):
            await session_storage.get_session(TENANT, "test_session")

    @pytest.mark.asyncio
    async def test_delete_nonexistent_session(self, session_storage):
        """Test deleting non-existent session returns False."""
        deleted = await session_storage.delete_session(TENANT, "nonexistent")
        assert deleted is False

    @pytest.mark.asyncio
    async def test_list_sessions(self, session_storage):
        """Test listing all sessions."""
        await session_storage.create_session(TENANT, "session_1", "user_1")
        await session_storage.create_session(TENANT, "session_2", "user_2")
        await session_storage.create_session(TENANT, "session_3", "user_1")

        sessions = await session_storage.list_sessions(TENANT)

        assert len(sessions) == 3
        session_ids = {s.session_id for s in sessions}
        assert session_ids == {"session_1", "session_2", "session_3"}


class TestRecitationContext:
    """Tests for recitation pattern support."""

    @pytest.mark.asyncio
    async def test_get_recitation_context(self, session_storage):
        """Test getting recitation context."""
        await session_storage.create_session(TENANT, "test_session", "user_1")
        await session_storage.write(TENANT, "test_session", "plan.md", "# My Plan\n\nDo something important.")
        await session_storage.write(TENANT, "test_session", "todo.md", "# Tasks\n\n- [ ] Task 1\n- [x] Task 2")

        context = await session_storage.get_recitation_context(TENANT, "test_session")

        assert "# My Plan" in context
        assert "Do something important" in context
        assert "# Tasks" in context
        assert "Task 1" in context
        assert "Task 2" in context
        assert "## Instructions" in context

    @pytest.mark.asyncio
    async def test_recitation_context_default_templates(self, session_storage):
        """Test recitation context with default templates returns empty (skipped)."""
        await session_storage.create_session(TENANT, "test_session", "user_1")

        context = await session_storage.get_recitation_context(TENANT, "test_session")

        # Default templates are now skipped (returns empty string)
        assert context == ""


class TestStateAndLogging:
    """Tests for state management and logging."""

    @pytest.mark.asyncio
    async def test_update_state(self, session_storage):
        """Test updating session state."""
        await session_storage.create_session(TENANT, "test_session", "user_1")

        updated = await session_storage.update_state(
            TENANT,
            "test_session",
            {"step": 5, "custom_field": "value"},
        )

        assert updated["step"] == 5
        assert updated["custom_field"] == "value"
        assert "updated_at" in updated

        # Verify persisted
        state_content = await session_storage.read(TENANT, "test_session", "state.json")
        state = json.loads(state_content)
        assert state["step"] == 5
        assert state["custom_field"] == "value"

    @pytest.mark.asyncio
    async def test_log_action(self, session_storage):
        """Test logging an action."""
        await session_storage.create_session(TENANT, "test_session", "user_1")

        await session_storage.log_action(
            TENANT,
            "test_session",
            "Analyzed AAPL stock",
            "Found positive momentum indicators",
        )

        history = await session_storage.read(TENANT, "test_session", "logs/history.md")

        assert "Analyzed AAPL stock" in history
        assert "Found positive momentum indicators" in history

    @pytest.mark.asyncio
    async def test_log_error(self, session_storage):
        """Test logging an error."""
        await session_storage.create_session(TENANT, "test_session", "user_1")

        await session_storage.log_error(
            TENANT,
            "test_session",
            "API rate limit exceeded",
            "Traceback: ...",
        )

        errors = await session_storage.read(TENANT, "test_session", "logs/errors.log")

        assert "ERROR" in errors
        assert "API rate limit exceeded" in errors
        assert "Traceback" in errors


class TestFilesystemSessionMemoryCompat:
    """Tests for backward compatibility with InMemorySessionMemory API."""

    @pytest.fixture
    def compat_memory(self, temp_sessions_dir):
        """Create FilesystemSessionMemory with temp directory."""
        memory = FilesystemSessionMemory()
        memory._storage = SessionStorage(
            base_path=temp_sessions_dir,
            checkpointing_enabled=False,
        )
        return memory

    @pytest.mark.asyncio
    async def test_compat_read_write(self, compat_memory):
        """Test read/write through compatibility layer."""
        await compat_memory.write(TENANT, "session_1", "plan.md", "# Updated Plan", "user_1")

        content = await compat_memory.read(TENANT, "session_1", "plan.md", "user_1")

        assert content == "# Updated Plan"

    @pytest.mark.asyncio
    async def test_compat_append(self, compat_memory):
        """Test append through compatibility layer."""
        await compat_memory.write(TENANT, "session_1", "log.txt", "Line 1\n", "user_1")
        await compat_memory.append(TENANT, "session_1", "log.txt", "Line 2\n", "user_1")

        content = await compat_memory.read(TENANT, "session_1", "log.txt", "user_1")

        assert content == "Line 1\nLine 2\n"

    @pytest.mark.asyncio
    async def test_compat_list_files(self, compat_memory):
        """Test list_files through compatibility layer."""
        await compat_memory.write(TENANT, "session_1", "file1.txt", "content", "user_1")
        await compat_memory.write(TENANT, "session_1", "file2.txt", "content", "user_1")

        files = await compat_memory.list_files(TENANT, "session_1")

        assert "file1.txt" in files
        assert "file2.txt" in files

    @pytest.mark.asyncio
    async def test_compat_delete_file(self, compat_memory):
        """Test delete_file through compatibility layer."""
        await compat_memory.write(TENANT, "session_1", "to_delete.txt", "content", "user_1")

        deleted = await compat_memory.delete_file(TENANT, "session_1", "to_delete.txt")

        assert deleted is True

    @pytest.mark.asyncio
    async def test_compat_delete_session(self, compat_memory):
        """Test delete_session through compatibility layer."""
        await compat_memory.write(TENANT, "session_1", "file.txt", "content", "user_1")

        deleted = await compat_memory.delete_session(TENANT, "session_1")

        assert deleted is True

    @pytest.mark.asyncio
    async def test_compat_get_session_info(self, compat_memory):
        """Test get_session_info through compatibility layer."""
        await compat_memory.write(TENANT, "session_1", "file.txt", "content", "user_1")

        info = await compat_memory.get_session_info(TENANT, "session_1")

        assert info is not None
        assert info["session_id"] == "session_1"
        assert "created_at" in info
        assert "files" in info


class TestSingletonBehavior:
    """Tests for singleton pattern."""

    def test_get_session_storage_singleton(self):
        """Test that get_session_storage returns singleton."""
        reset_session_storage()

        storage1 = get_session_storage()
        storage2 = get_session_storage()

        assert storage1 is storage2

        reset_session_storage()

    def test_reset_session_storage(self):
        """Test resetting the singleton."""
        reset_session_storage()

        storage1 = get_session_storage()
        reset_session_storage()
        storage2 = get_session_storage()

        assert storage1 is not storage2

        reset_session_storage()
