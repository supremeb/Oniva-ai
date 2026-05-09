"""Tests for SandboxExecutor horizontal scaling via Redis.

Tests Redis registry for cross-instance sandbox discovery, ownership-aware
teardown, graceful degradation when Redis is unavailable, and cleanup_orphans
checking Redis before killing.
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.sandbox_executor import (
    SandboxExecutor,
    SandboxHandle,
    _redis_get_sandbox,
    _redis_set_sandbox,
    _redis_delete_sandbox,
    _INSTANCE_ID,
)


def _make_mock_sandbox(sandbox_id: str = "sb-123") -> MagicMock:
    """Create a mock E2B sandbox."""
    mock = MagicMock()
    mock.sandbox_id = sandbox_id
    mock.kill = AsyncMock()
    mock.set_timeout = AsyncMock()
    return mock


def _make_executor() -> SandboxExecutor:
    """Create a SandboxExecutor with mocked settings."""
    with patch("app.services.sandbox_executor.get_settings") as mock_settings, \
         patch("app.services.sandbox_executor.get_session_storage"):
        settings = MagicMock()
        settings.ENABLE_SANDBOX = True
        settings.E2B_API_KEY = "test-key"
        settings.E2B_TEMPLATE_ID = None
        settings.SESSION_BASE_PATH = "/tmp/sessions"
        mock_settings.return_value = settings
        return SandboxExecutor()


@pytest.mark.asyncio
async def test_get_or_create_local_cache_hit():
    """Second call returns cached handle without hitting Redis."""
    executor = _make_executor()
    mock_sandbox = _make_mock_sandbox()
    executor._sandboxes["session-1"] = mock_sandbox

    handle = await executor.get_or_create("session-1", api_key="test-key")
    assert handle.sandbox_id == "sb-123"
    # Should not have hit Redis since it was in local cache


@pytest.mark.asyncio
async def test_get_or_create_redis_reconnect():
    """Pre-populate Redis, verify AsyncSandbox.connect() called."""
    executor = _make_executor()
    redis_data = {
        "sandbox_id": "sb-remote-456",
        "instance_id": "other-instance",
        "created_at": 1234567890,
        "status": "active",
    }

    with patch("app.services.sandbox_executor._redis_get_sandbox", new_callable=AsyncMock) as mock_redis_get, \
         patch("app.services.sandbox_executor._redis_set_sandbox", new_callable=AsyncMock), \
         patch("e2b_code_interpreter.AsyncSandbox") as MockSandbox:
        mock_redis_get.return_value = redis_data
        mock_connected = _make_mock_sandbox("sb-remote-456")
        MockSandbox.connect = AsyncMock(return_value=mock_connected)

        handle = await executor.get_or_create("session-2", api_key="test-key")
        assert handle.sandbox_id == "sb-remote-456"
        MockSandbox.connect.assert_called_once_with("sb-remote-456", api_key="test-key")


@pytest.mark.asyncio
async def test_get_or_create_redis_stale_entry():
    """Redis has entry but reconnect fails → creates new sandbox, cleans stale entry."""
    executor = _make_executor()
    redis_data = {
        "sandbox_id": "sb-stale",
        "instance_id": "dead-instance",
        "created_at": 1234567890,
        "status": "active",
    }

    with patch("app.services.sandbox_executor._redis_get_sandbox", new_callable=AsyncMock) as mock_redis_get, \
         patch("app.services.sandbox_executor._redis_set_sandbox", new_callable=AsyncMock) as mock_redis_set, \
         patch("app.services.sandbox_executor._redis_delete_sandbox", new_callable=AsyncMock) as mock_redis_del, \
         patch("e2b_code_interpreter.AsyncSandbox") as MockSandbox:
        mock_redis_get.return_value = redis_data
        MockSandbox.connect = AsyncMock(side_effect=Exception("sandbox not found"))
        mock_new = _make_mock_sandbox("sb-new")
        MockSandbox.create = AsyncMock(return_value=mock_new)
        mock_redis_set.return_value = True

        handle = await executor.get_or_create("session-stale", api_key="test-key")
        assert handle.sandbox_id == "sb-new"
        # Stale entry should be cleaned
        mock_redis_del.assert_called_once_with("session-stale", owner_only=False)
        # New sandbox should be registered
        mock_redis_set.assert_called_once()


@pytest.mark.asyncio
async def test_teardown_only_deletes_owned_redis_entry():
    """Instance A's sandbox in Redis, Instance B calls teardown → Redis entry preserved."""
    executor = _make_executor()
    mock_sandbox = _make_mock_sandbox("sb-A")
    executor._sandboxes["session-owned"] = mock_sandbox

    with patch("app.services.sandbox_executor._redis_delete_sandbox", new_callable=AsyncMock) as mock_redis_del:
        await executor.teardown("session-owned")
        mock_redis_del.assert_called_once_with("session-owned", owner_only=True)


@pytest.mark.asyncio
async def test_teardown_deletes_own_redis_entry():
    """Instance owns sandbox, teardown removes Redis entry."""
    executor = _make_executor()
    mock_sandbox = _make_mock_sandbox("sb-mine")
    executor._sandboxes["session-mine"] = mock_sandbox

    mock_redis = AsyncMock()
    # Return metadata showing this instance owns it
    mock_redis.get.return_value = json.dumps({
        "sandbox_id": "sb-mine",
        "instance_id": _INSTANCE_ID,
    })
    mock_redis.delete = AsyncMock()

    with patch("app.services.sandbox_executor.get_redis_service", new_callable=AsyncMock) as mock_get_redis:
        mock_get_redis.return_value = mock_redis
        await executor.teardown("session-mine")
        mock_redis.delete.assert_called_once_with("sandbox:session-mine")


@pytest.mark.asyncio
async def test_redis_down_graceful_degradation():
    """Redis failure → sandbox still created, no error."""
    executor = _make_executor()

    with patch("app.services.sandbox_executor._redis_get_sandbox", new_callable=AsyncMock) as mock_redis_get, \
         patch("app.services.sandbox_executor._redis_set_sandbox", new_callable=AsyncMock) as mock_redis_set, \
         patch("e2b_code_interpreter.AsyncSandbox") as MockSandbox:
        mock_redis_get.return_value = None  # Redis miss
        mock_redis_set.return_value = None  # Redis unavailable
        mock_sandbox = _make_mock_sandbox("sb-local-only")
        MockSandbox.create = AsyncMock(return_value=mock_sandbox)

        handle = await executor.get_or_create("session-no-redis", api_key="test-key")
        assert handle.sandbox_id == "sb-local-only"


@pytest.mark.asyncio
async def test_nx_race_lost_vs_redis_down():
    """Verify _redis_set_sandbox returns False (NX lost) vs None (Redis down)."""
    mock_redis = AsyncMock()

    # NX race lost — key already exists
    mock_redis.set = AsyncMock(return_value=False)
    with patch("app.services.sandbox_executor.get_redis_service", new_callable=AsyncMock) as mock_get_redis:
        mock_get_redis.return_value = mock_redis
        result = await _redis_set_sandbox("key-1", "sb-1")
        assert result is False

    # Redis down — exception
    with patch("app.services.sandbox_executor.get_redis_service", new_callable=AsyncMock) as mock_get_redis:
        mock_get_redis.side_effect = Exception("Redis connection refused")
        result = await _redis_set_sandbox("key-2", "sb-2")
        assert result is None


@pytest.mark.asyncio
async def test_cleanup_orphans_skips_redis_registered():
    """Sandbox in Redis but not local → NOT killed."""
    executor = _make_executor()

    mock_active_sb = MagicMock()
    mock_active_sb.sandbox_id = "sb-other-instance"
    mock_active_sb.kill = AsyncMock()

    with patch("app.services.sandbox_executor._redis_get_sandbox", new_callable=AsyncMock) as mock_redis_get, \
         patch("e2b_code_interpreter.AsyncSandbox") as MockSandbox:
        MockSandbox.list = AsyncMock(return_value=[mock_active_sb])
        # Redis knows about this sandbox (registered by another instance)
        mock_redis_get.return_value = {
            "sandbox_id": "sb-other-instance",
            "instance_id": "other-instance",
        }

        killed = await executor.cleanup_orphans()
        assert killed == 0
        mock_active_sb.kill.assert_not_called()


@pytest.mark.asyncio
async def test_cleanup_orphans_kills_true_orphans():
    """Sandbox not in local AND not in Redis → killed."""
    executor = _make_executor()

    mock_orphan = MagicMock()
    mock_orphan.sandbox_id = "sb-orphan"
    mock_orphan.kill = AsyncMock()

    with patch("app.services.sandbox_executor._redis_get_sandbox", new_callable=AsyncMock) as mock_redis_get, \
         patch("e2b_code_interpreter.AsyncSandbox") as MockSandbox:
        MockSandbox.list = AsyncMock(return_value=[mock_orphan])
        mock_redis_get.return_value = None  # Not in Redis either

        killed = await executor.cleanup_orphans()
        assert killed == 1
        mock_orphan.kill.assert_called_once()
