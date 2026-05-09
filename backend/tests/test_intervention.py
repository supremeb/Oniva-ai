"""Tests for the intervention service (Redis-backed).

Tests send_intervention, check_interventions, and clear_interventions
with a mocked Redis HA service.
"""

import json
import pytest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.intervention import (
    send_intervention,
    check_interventions,
    clear_interventions,
    _KEY_PREFIX,
    _TTL_SECONDS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_redis_mock():
    """Build a mock RedisHAService with lpush, lrange, delete, expire, pipeline, execute_pipeline."""
    redis_mock = AsyncMock()

    # pipeline() is an async context manager that yields a pipe mock
    pipe_mock = MagicMock()
    pipe_mock.lrange = MagicMock()
    pipe_mock.delete = MagicMock()

    @asynccontextmanager
    async def _pipeline(transaction=True):
        yield pipe_mock

    redis_mock.pipeline = _pipeline
    redis_mock.execute_pipeline = AsyncMock(return_value=[[], 0])
    redis_mock._pipe_mock = pipe_mock  # expose for assertions

    return redis_mock


def _expected_key(job_id: str) -> str:
    return f"{_KEY_PREFIX}:{job_id}"


# ===========================================================================
# send_intervention tests
# ===========================================================================

@pytest.mark.asyncio
@patch("app.services.intervention.get_redis_service", new_callable=AsyncMock)
async def test_send_intervention_lpush_and_expire(mock_get_redis):
    """LPUSH called with correct key + JSON payload, EXPIRE called with 3600."""
    redis_mock = AsyncMock()
    mock_get_redis.return_value = redis_mock

    job_id = "test-job-123"
    message = "Please adjust parameters"
    sender = "operator@test.com"

    result = await send_intervention(job_id, message, sender)

    expected_key = _expected_key(job_id)

    # Verify LPUSH was called
    redis_mock.lpush.assert_called_once()
    call_args = redis_mock.lpush.call_args
    assert call_args[0][0] == expected_key
    payload = json.loads(call_args[0][1])
    assert payload["message"] == message
    assert payload["sender"] == sender
    assert "timestamp" in payload

    # Verify EXPIRE was called with TTL
    redis_mock.expire.assert_called_once_with(expected_key, _TTL_SECONDS)

    # Verify returned dict
    assert result["message"] == message
    assert result["sender"] == sender
    assert "timestamp" in result


@pytest.mark.asyncio
@patch("app.services.intervention.get_redis_service", new_callable=AsyncMock)
async def test_send_intervention_expire_ttl_is_3600(mock_get_redis):
    """Verify EXPIRE TTL is exactly 3600 seconds."""
    redis_mock = AsyncMock()
    mock_get_redis.return_value = redis_mock

    await send_intervention("job-ttl", "msg", "user@test.com")

    expire_call = redis_mock.expire.call_args
    assert expire_call[0][1] == 3600
    assert _TTL_SECONDS == 3600


# ===========================================================================
# check_interventions tests
# ===========================================================================

@pytest.mark.asyncio
@patch("app.services.intervention.get_redis_service", new_callable=AsyncMock)
async def test_check_interventions_returns_messages_in_send_order(mock_get_redis):
    """Pipeline LRANGE returns messages, reversed from LPUSH LIFO to send order."""
    redis_mock = _make_redis_mock()
    mock_get_redis.return_value = redis_mock

    msg1 = json.dumps({"message": "first", "sender": "a@test.com", "timestamp": "t1"})
    msg2 = json.dumps({"message": "second", "sender": "b@test.com", "timestamp": "t2"})

    # LPUSH is LIFO, so LRANGE returns [second, first] — service reverses it
    redis_mock.execute_pipeline.return_value = [[msg2, msg1], 1]

    job_id = "job-check-1"
    result = await check_interventions(job_id)

    # Pipeline should have lrange and delete queued
    pipe = redis_mock._pipe_mock
    expected_key = _expected_key(job_id)
    pipe.lrange.assert_called_once_with(expected_key, 0, -1)
    pipe.delete.assert_called_once_with(expected_key)

    # Results should be in send order (reversed from LRANGE)
    assert len(result) == 2
    assert result[0]["message"] == "first"
    assert result[1]["message"] == "second"


@pytest.mark.asyncio
@patch("app.services.intervention.get_redis_service", new_callable=AsyncMock)
async def test_check_interventions_missing_key_returns_empty(mock_get_redis):
    """Missing key returns [], no error."""
    redis_mock = _make_redis_mock()
    mock_get_redis.return_value = redis_mock

    # Empty LRANGE result
    redis_mock.execute_pipeline.return_value = [[], 0]

    result = await check_interventions("nonexistent-job")

    assert result == []


@pytest.mark.asyncio
@patch("app.services.intervention.get_redis_service", new_callable=AsyncMock)
async def test_check_interventions_pipeline_used(mock_get_redis):
    """Verify pipeline context manager is used for atomicity."""
    redis_mock = _make_redis_mock()
    mock_get_redis.return_value = redis_mock
    redis_mock.execute_pipeline.return_value = [[], 0]

    await check_interventions("job-pipeline")

    # execute_pipeline must be called with the pipe mock
    redis_mock.execute_pipeline.assert_called_once()


# ===========================================================================
# clear_interventions tests
# ===========================================================================

@pytest.mark.asyncio
@patch("app.services.intervention.get_redis_service", new_callable=AsyncMock)
async def test_clear_interventions_deletes_key(mock_get_redis):
    """DELETE called on correct key."""
    redis_mock = AsyncMock()
    mock_get_redis.return_value = redis_mock

    job_id = "job-clear-1"
    await clear_interventions(job_id)

    expected_key = _expected_key(job_id)
    redis_mock.delete.assert_called_once_with(expected_key)


# ===========================================================================
# Integration-style sequence tests
# ===========================================================================

@pytest.mark.asyncio
@patch("app.services.intervention.get_redis_service", new_callable=AsyncMock)
async def test_send_clear_check_returns_empty(mock_get_redis):
    """send + clear + check sequence: check returns []."""
    redis_mock = _make_redis_mock()
    # For send_intervention, get_redis_service returns a plain AsyncMock
    # For check_interventions, it returns our pipeline-capable mock
    # We use a single mock that supports both interfaces
    redis_mock.lpush = AsyncMock()
    redis_mock.expire = AsyncMock()
    redis_mock.delete = AsyncMock()
    mock_get_redis.return_value = redis_mock

    job_id = "job-sequence"

    # Step 1: Send
    await send_intervention(job_id, "hello", "user@test.com")
    redis_mock.lpush.assert_called_once()

    # Step 2: Clear
    await clear_interventions(job_id)
    redis_mock.delete.assert_called_once_with(_expected_key(job_id))

    # Step 3: Check — after clear, the key is gone so LRANGE returns empty
    redis_mock.execute_pipeline.return_value = [[], 0]
    result = await check_interventions(job_id)

    assert result == []
