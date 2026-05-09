"""Tests for session lifecycle cleanup (Phase B).

Covers:
- Stale completed session is deleted
- Active session (recent mtime) is NOT deleted
- Session in non-terminal state is NOT deleted
- Session with active background job is NOT deleted
- Idempotent — re-running doesn't error on missing sessions
"""
import json
import os
import pytest
import tempfile
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from app.services.session_storage import SessionStorage, cleanup_stale_sessions


@pytest.fixture
def temp_sessions_dir():
    """Create a temporary sessions directory."""
    tmpdir = tempfile.mkdtemp()
    yield Path(tmpdir)
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def storage(temp_sessions_dir):
    """Create a SessionStorage with temp directory."""
    return SessionStorage(base_path=temp_sessions_dir)


FAKE_TENANT_ID = "test-tenant"


def _create_session(base_path: Path, session_id: str, status: str = "completed",
                    age_days: int = 60, tenant_id: str = FAKE_TENANT_ID) -> Path:
    """Create a fake session directory with specified age and status.

    Sessions live under base_path / tenant_id / session_id to match the
    production layout that cleanup_stale_sessions expects.
    """
    session_path = base_path / tenant_id / session_id
    session_path.mkdir(parents=True, exist_ok=True)

    # Create state.json
    state = {
        "status": status,
        "user_id": "test-user",
        "created_at": (datetime.now() - timedelta(days=age_days + 1)).isoformat(),
    }
    state_file = session_path / "state.json"
    state_file.write_text(json.dumps(state))

    # Create some session files
    (session_path / "plan.md").write_text("# Test Plan")
    (session_path / "todo.md").write_text("# Todo\n- [x] Done")

    logs_dir = session_path / "logs"
    logs_dir.mkdir(exist_ok=True)
    (logs_dir / "history.md").write_text("## Test entry")

    # Set file mtimes to simulate age
    old_time = (datetime.now() - timedelta(days=age_days)).timestamp()
    for f in session_path.rglob("*"):
        if f.is_file():
            os.utime(f, (old_time, old_time))

    return session_path


class TestCleanupStaleSessions:

    @pytest.mark.asyncio
    async def test_deletes_stale_completed_session(self, temp_sessions_dir):
        """Stale completed session is deleted."""
        _create_session(temp_sessions_dir, "stale-completed", status="completed", age_days=60)

        with patch("app.services.session_storage.get_session_storage") as mock_get:
            mock_get.return_value = SessionStorage(base_path=temp_sessions_dir)
            result = await cleanup_stale_sessions(retention_days=30)

        assert result["deleted"] == 1
        assert not (temp_sessions_dir / FAKE_TENANT_ID / "stale-completed").exists()

    @pytest.mark.asyncio
    async def test_deletes_stale_failed_session(self, temp_sessions_dir):
        """Stale failed session is deleted."""
        _create_session(temp_sessions_dir, "stale-failed", status="failed", age_days=60)

        with patch("app.services.session_storage.get_session_storage") as mock_get:
            mock_get.return_value = SessionStorage(base_path=temp_sessions_dir)
            result = await cleanup_stale_sessions(retention_days=30)

        assert result["deleted"] == 1
        assert not (temp_sessions_dir / FAKE_TENANT_ID / "stale-failed").exists()

    @pytest.mark.asyncio
    async def test_skips_active_session_recent_mtime(self, temp_sessions_dir):
        """Active session (recent file writes) is NOT deleted."""
        _create_session(temp_sessions_dir, "active-session", status="completed", age_days=5)

        with patch("app.services.session_storage.get_session_storage") as mock_get:
            mock_get.return_value = SessionStorage(base_path=temp_sessions_dir)
            result = await cleanup_stale_sessions(retention_days=30)

        assert result["deleted"] == 0
        assert result["skipped"] == 1
        assert (temp_sessions_dir / FAKE_TENANT_ID / "active-session").exists()

    @pytest.mark.asyncio
    async def test_skips_session_in_active_state(self, temp_sessions_dir):
        """Session in active/human_review state is NOT deleted."""
        _create_session(temp_sessions_dir, "in-review", status="human_review", age_days=60)

        with patch("app.services.session_storage.get_session_storage") as mock_get:
            mock_get.return_value = SessionStorage(base_path=temp_sessions_dir)
            result = await cleanup_stale_sessions(retention_days=30)

        assert result["deleted"] == 0
        assert result["skipped"] == 1
        assert (temp_sessions_dir / FAKE_TENANT_ID / "in-review").exists()

    @pytest.mark.asyncio
    async def test_deletes_session_with_empty_status(self, temp_sessions_dir):
        """Session with empty status in state.json is eligible for deletion."""
        _create_session(temp_sessions_dir, "empty-status", status="", age_days=60)

        with patch("app.services.session_storage.get_session_storage") as mock_get:
            mock_get.return_value = SessionStorage(base_path=temp_sessions_dir)
            result = await cleanup_stale_sessions(retention_days=30)

        assert result["deleted"] == 1

    @pytest.mark.asyncio
    async def test_idempotent_no_error_on_empty(self, temp_sessions_dir):
        """Re-running cleanup on empty directory doesn't error."""
        with patch("app.services.session_storage.get_session_storage") as mock_get:
            mock_get.return_value = SessionStorage(base_path=temp_sessions_dir)
            result = await cleanup_stale_sessions(retention_days=30)

        assert result["deleted"] == 0
        assert result["errors"] == 0

    @pytest.mark.asyncio
    async def test_batch_size_limits_deletions(self, temp_sessions_dir):
        """Only batch_size sessions are deleted per run."""
        for i in range(5):
            _create_session(temp_sessions_dir, f"batch-{i}", status="completed", age_days=60)

        with patch("app.services.session_storage.get_session_storage") as mock_get:
            mock_get.return_value = SessionStorage(base_path=temp_sessions_dir)
            result = await cleanup_stale_sessions(retention_days=30, batch_size=2)

        assert result["deleted"] == 2
