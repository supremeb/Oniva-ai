"""
Tests for workspace file skills (write_file, read_file, delete_file, list_workspace).

Tests cover:
- Basic write/read/delete/list operations
- Path traversal prevention
- Absolute path rejection
- Session ID requirement
- File size limit enforcement
- Unicode content round-trip
- list_workspace truncation (>50 files)
- Subdirectory support
"""

import pytest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch

from app.agents.skills.context import SkillContext
from app.services.session_storage import SessionStorage


STORAGE_PATCH = "app.services.session_storage.get_session_storage"
TENANT = "test-tenant"


@pytest.fixture
def temp_sessions_dir():
    """Create a temporary directory for sessions."""
    temp_dir = tempfile.mkdtemp(prefix="test_workspace_")
    yield Path(temp_dir)
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def session_storage(temp_sessions_dir):
    """Create a SessionStorage with temp directory."""
    return SessionStorage(base_path=temp_sessions_dir, checkpointing_enabled=False)


@pytest.fixture
async def setup_session(session_storage):
    """Create a session and return (session_id, session_storage)."""
    sid = "test-workspace-session"
    await session_storage.create_session(TENANT, sid, "user-1")
    return sid, session_storage


@pytest.fixture
def ctx_with_session(setup_session):
    """Create a SkillContext with a session_id."""
    sid, _ = setup_session
    return SkillContext(user_id="user-1", tenant_id=TENANT, session_id=sid)


@pytest.fixture
def ctx_without_session():
    """Create a SkillContext without a session_id."""
    return SkillContext(user_id="user-1", session_id=None)


class TestWriteFile:
    """Tests for write_file skill."""

    @pytest.mark.asyncio
    async def test_write_file_creates_file(self, setup_session):
        """Test write_file creates file in workspace/ subdirectory."""
        sid, storage = setup_session
        ctx = SkillContext(user_id="user-1", tenant_id=TENANT, session_id=sid)

        with patch(STORAGE_PATCH, return_value=storage):
            from app.skills.write_file.tool import write_file
            result = await write_file(ctx, "output.txt", "Hello, World!")

        assert result["success"] is True
        assert result["path"] == "output.txt"
        assert result["size_bytes"] == len("Hello, World!".encode("utf-8"))

        # Verify file actually exists in workspace/
        content = await storage.read(TENANT, sid, "workspace/output.txt")
        assert content == "Hello, World!"

    @pytest.mark.asyncio
    async def test_write_file_subdirectory(self, setup_session):
        """Test write_file creates subdirectories automatically."""
        sid, storage = setup_session
        ctx = SkillContext(user_id="user-1", tenant_id=TENANT, session_id=sid)

        with patch(STORAGE_PATCH, return_value=storage):
            from app.skills.write_file.tool import write_file
            result = await write_file(ctx, "analysis/deep/results.json", '{"score": 0.85}')

        assert result["success"] is True
        content = await storage.read(TENANT, sid, "workspace/analysis/deep/results.json")
        assert content == '{"score": 0.85}'

    @pytest.mark.asyncio
    async def test_write_file_no_session(self, ctx_without_session):
        """Test write_file returns error when session_id is None."""
        from app.skills.write_file.tool import write_file
        result = await write_file(ctx_without_session, "test.txt", "content")

        assert result["success"] is False
        assert "session" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_write_file_path_traversal_blocked(self, ctx_with_session):
        """Test write_file rejects path traversal attempts."""
        from app.skills.write_file.tool import write_file
        result = await write_file(ctx_with_session, "../plan.md", "malicious")

        assert result["success"] is False
        assert "traversal" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_write_file_absolute_path_blocked(self, ctx_with_session):
        """Test write_file rejects absolute paths."""
        from app.skills.write_file.tool import write_file
        result = await write_file(ctx_with_session, "/etc/passwd", "malicious")

        assert result["success"] is False
        assert "absolute" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_write_file_size_limit(self, setup_session):
        """Test write_file enforces 10MB limit."""
        sid, storage = setup_session
        ctx = SkillContext(user_id="user-1", tenant_id=TENANT, session_id=sid)

        with patch(STORAGE_PATCH, return_value=storage):
            from app.skills.write_file.tool import write_file, MAX_FILE_SIZE
            big_content = "x" * (MAX_FILE_SIZE + 1)
            result = await write_file(ctx, "big.txt", big_content)

        assert result["success"] is False
        assert "too large" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_write_file_unicode(self, setup_session):
        """Test write_file handles unicode content correctly."""
        sid, storage = setup_session
        ctx = SkillContext(user_id="user-1", tenant_id=TENANT, session_id=sid)

        unicode_content = "Hello 世界! Cafe\u0301"
        with patch(STORAGE_PATCH, return_value=storage):
            from app.skills.write_file.tool import write_file
            result = await write_file(ctx, "unicode.txt", unicode_content)

        assert result["success"] is True
        content = await storage.read(TENANT, sid, "workspace/unicode.txt")
        assert content == unicode_content


class TestReadFile:
    """Tests for read_file skill."""

    @pytest.mark.asyncio
    async def test_read_file_returns_content(self, setup_session):
        """Test read_file returns content from workspace/."""
        sid, storage = setup_session
        ctx = SkillContext(user_id="user-1", tenant_id=TENANT, session_id=sid)
        await storage.write(TENANT, sid, "workspace/data.txt", "test content")

        with patch(STORAGE_PATCH, return_value=storage):
            from app.skills.read_file.tool import read_file
            result = await read_file(ctx, "data.txt")

        assert result["success"] is True
        assert result["content"] == "test content"
        assert result["size_bytes"] == len("test content".encode("utf-8"))

    @pytest.mark.asyncio
    async def test_read_file_not_found(self, setup_session):
        """Test read_file returns error for missing file."""
        sid, storage = setup_session
        ctx = SkillContext(user_id="user-1", tenant_id=TENANT, session_id=sid)

        with patch(STORAGE_PATCH, return_value=storage):
            from app.skills.read_file.tool import read_file
            result = await read_file(ctx, "nonexistent.txt")

        assert result["success"] is False
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_read_file_no_session(self, ctx_without_session):
        """Test read_file returns error when session_id is None."""
        from app.skills.read_file.tool import read_file
        result = await read_file(ctx_without_session, "test.txt")

        assert result["success"] is False
        assert "session" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_read_file_path_traversal_blocked(self, ctx_with_session):
        """Test read_file rejects path traversal."""
        from app.skills.read_file.tool import read_file
        result = await read_file(ctx_with_session, "../plan.md")

        assert result["success"] is False
        assert "traversal" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_read_file_absolute_path_blocked(self, ctx_with_session):
        """Test read_file rejects absolute paths."""
        from app.skills.read_file.tool import read_file
        result = await read_file(ctx_with_session, "/etc/passwd")

        assert result["success"] is False
        assert "absolute" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_read_file_unicode(self, setup_session):
        """Test read_file handles unicode content correctly."""
        sid, storage = setup_session
        ctx = SkillContext(user_id="user-1", tenant_id=TENANT, session_id=sid)
        unicode_content = "Hello 世界! Cafe\u0301"
        await storage.write(TENANT, sid, "workspace/unicode.txt", unicode_content)

        with patch(STORAGE_PATCH, return_value=storage):
            from app.skills.read_file.tool import read_file
            result = await read_file(ctx, "unicode.txt")

        assert result["success"] is True
        assert result["content"] == unicode_content


class TestDeleteFile:
    """Tests for delete_file skill."""

    @pytest.mark.asyncio
    async def test_delete_file_removes_file(self, setup_session):
        """Test delete_file removes file from workspace/."""
        sid, storage = setup_session
        ctx = SkillContext(user_id="user-1", tenant_id=TENANT, session_id=sid)
        await storage.write(TENANT, sid, "workspace/to_delete.txt", "delete me")

        with patch(STORAGE_PATCH, return_value=storage):
            from app.skills.delete_file.tool import delete_file
            result = await delete_file(ctx, "to_delete.txt")

        assert result["success"] is True
        assert result["deleted"] is True

        # Verify file is gone
        content = await storage.read(TENANT, sid, "workspace/to_delete.txt")
        assert content == ""

    @pytest.mark.asyncio
    async def test_delete_file_nonexistent(self, setup_session):
        """Test delete_file for non-existent file returns deleted=False."""
        sid, storage = setup_session
        ctx = SkillContext(user_id="user-1", tenant_id=TENANT, session_id=sid)

        with patch(STORAGE_PATCH, return_value=storage):
            from app.skills.delete_file.tool import delete_file
            result = await delete_file(ctx, "nonexistent.txt")

        assert result["success"] is True
        assert result["deleted"] is False

    @pytest.mark.asyncio
    async def test_delete_file_no_session(self, ctx_without_session):
        """Test delete_file returns error when session_id is None."""
        from app.skills.delete_file.tool import delete_file
        result = await delete_file(ctx_without_session, "test.txt")

        assert result["success"] is False
        assert "session" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_delete_file_path_traversal_blocked(self, ctx_with_session):
        """Test delete_file rejects path traversal."""
        from app.skills.delete_file.tool import delete_file
        result = await delete_file(ctx_with_session, "../plan.md")

        assert result["success"] is False
        assert "traversal" in result["error"].lower()


class TestListWorkspace:
    """Tests for list_workspace skill."""

    @pytest.mark.asyncio
    async def test_list_workspace_returns_metadata(self, setup_session):
        """Test list_workspace returns metadata (name, type, size, modified)."""
        sid, storage = setup_session
        ctx = SkillContext(user_id="user-1", tenant_id=TENANT, session_id=sid)
        await storage.write(TENANT, sid, "workspace/file1.txt", "hello")
        await storage.write(TENANT, sid, "workspace/file2.json", '{"a":1}')

        with patch(STORAGE_PATCH, return_value=storage):
            from app.skills.list_workspace.tool import list_workspace
            result = await list_workspace(ctx)

        assert result["success"] is True
        assert result["total_count"] == 2
        assert result["truncated"] is False

        names = {f["name"] for f in result["files"]}
        assert "file1.txt" in names
        assert "file2.json" in names

        for f in result["files"]:
            assert "name" in f
            assert "type" in f
            assert "modified" in f
            if f["type"] == "file":
                assert "size" in f

    @pytest.mark.asyncio
    async def test_list_workspace_subdirectory(self, setup_session):
        """Test list_workspace with subdirectory path parameter."""
        sid, storage = setup_session
        ctx = SkillContext(user_id="user-1", tenant_id=TENANT, session_id=sid)
        await storage.write(TENANT, sid, "workspace/sub/a.txt", "a")
        await storage.write(TENANT, sid, "workspace/sub/b.txt", "b")
        await storage.write(TENANT, sid, "workspace/root.txt", "root")

        with patch(STORAGE_PATCH, return_value=storage):
            from app.skills.list_workspace.tool import list_workspace
            result = await list_workspace(ctx, path="sub")

        assert result["success"] is True
        assert result["total_count"] == 2
        names = {f["name"] for f in result["files"]}
        assert names == {"a.txt", "b.txt"}

    @pytest.mark.asyncio
    async def test_list_workspace_empty(self, setup_session):
        """Test list_workspace on empty/missing workspace returns empty list."""
        sid, storage = setup_session
        ctx = SkillContext(user_id="user-1", tenant_id=TENANT, session_id=sid)

        with patch(STORAGE_PATCH, return_value=storage):
            from app.skills.list_workspace.tool import list_workspace
            result = await list_workspace(ctx)

        assert result["success"] is True
        assert result["files"] == []
        assert result["total_count"] == 0

    @pytest.mark.asyncio
    async def test_list_workspace_shows_directories(self, setup_session):
        """Test list_workspace shows directory entries."""
        sid, storage = setup_session
        ctx = SkillContext(user_id="user-1", tenant_id=TENANT, session_id=sid)
        await storage.write(TENANT, sid, "workspace/subdir/file.txt", "content")

        with patch(STORAGE_PATCH, return_value=storage):
            from app.skills.list_workspace.tool import list_workspace
            result = await list_workspace(ctx)

        assert result["success"] is True
        dir_entries = [f for f in result["files"] if f["type"] == "directory"]
        assert len(dir_entries) == 1
        assert dir_entries[0]["name"] == "subdir"

    @pytest.mark.asyncio
    async def test_list_workspace_truncation(self, setup_session):
        """Test list_workspace truncation (>50 files)."""
        sid, storage = setup_session
        ctx = SkillContext(user_id="user-1", tenant_id=TENANT, session_id=sid)

        # Create 55 files
        for i in range(55):
            await storage.write(TENANT, sid, f"workspace/file_{i:03d}.txt", f"content {i}")

        with patch(STORAGE_PATCH, return_value=storage):
            from app.skills.list_workspace.tool import list_workspace
            result = await list_workspace(ctx)

        assert result["success"] is True
        assert len(result["files"]) == 50
        assert result["total_count"] == 55
        assert result["truncated"] is True

    @pytest.mark.asyncio
    async def test_list_workspace_no_session(self, ctx_without_session):
        """Test list_workspace returns error when session_id is None."""
        from app.skills.list_workspace.tool import list_workspace
        result = await list_workspace(ctx_without_session)

        assert result["success"] is False
        assert "session" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_list_workspace_path_traversal_blocked(self, ctx_with_session):
        """Test list_workspace rejects path traversal."""
        from app.skills.list_workspace.tool import list_workspace
        result = await list_workspace(ctx_with_session, path="../")

        assert result["success"] is False
        assert "traversal" in result["error"].lower()
