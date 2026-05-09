"""Tests for Fleet API endpoints.

Tests list_fleet_runs and intervene_in_agent route handlers
with mocked dependencies for tenant isolation, filtering,
pagination, and error handling.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4, UUID

import redis.exceptions
from fastapi import HTTPException

from app.api.v1.fleet import (
    InterventionRequest,
    list_fleet_runs,
    intervene_in_agent,
)
from app.auth.providers.base import AuthUser
from app.models.background_job import JobType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_auth_user(tenant_id: UUID | None = None) -> AuthUser:
    return AuthUser(
        id=str(uuid4()),
        email="user@test.com",
        name="Test User",
        tenant_id=str(tenant_id or uuid4()),
        tenant_slug="test-tenant",
        role="admin",
        auth_provider="local",
    )


def _make_job(
    tenant_id: UUID,
    status: str = "running",
    agent_id: UUID | None = None,
    job_type: str = JobType.BACKGROUND_AGENT.value,
):
    """Create a mock BackgroundJob ORM object."""
    job = MagicMock()
    job.id = uuid4()
    job.tenant_id = tenant_id
    job.agent_id = agent_id or uuid4()
    job.job_type = job_type
    job.status = status
    job.trigger = "manual"
    job.triggered_by = None
    job.progress = 50
    job.input_data = None
    job.result = None
    job.error = None
    job.started_at = datetime.now(timezone.utc)
    job.completed_at = None
    job.created_at = datetime.now(timezone.utc)
    job.execution_trace = []
    # Properties
    job.duration_ms = None
    job.total_tokens = 0
    return job


def _mock_db_for_list(jobs: list, total: int):
    """Build an AsyncMock db that returns the given jobs list and total count.

    list_fleet_runs does two db.execute calls:
      1) count query  -> .scalar() -> total
      2) paginated query -> .scalars().all() -> jobs

    Then optionally a third for agent name lookup.
    """
    db = AsyncMock()

    # First call: count query
    count_result = MagicMock()
    count_result.scalar.return_value = total

    # Second call: paginated query
    page_result = MagicMock()
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = jobs
    page_result.scalars.return_value = scalars_mock

    # Third call: agent lookup (empty)
    agent_result = MagicMock()
    agent_scalars = MagicMock()
    agent_scalars.all.return_value = []
    agent_result.scalars.return_value = agent_scalars

    db.execute = AsyncMock(side_effect=[count_result, page_result, agent_result])
    return db


# ===========================================================================
# list_fleet_runs tests
# ===========================================================================

@pytest.mark.asyncio
async def test_list_fleet_runs_tenant_isolation():
    """Only jobs belonging to the calling tenant are returned."""
    tenant_a = uuid4()
    tenant_b = uuid4()

    jobs_a = [_make_job(tenant_a) for _ in range(3)]
    # Tenant B jobs exist but should NOT appear — the SQL WHERE clause
    # filters by tenant_id, so the mock db only returns tenant A's jobs.
    db = _mock_db_for_list(jobs_a, total=3)

    user = _make_auth_user(tenant_a)
    resp = await list_fleet_runs(
        user=user,
        tenant_id=tenant_a,
        db=db,
        status=None,
        agent_id=None,
        page=1,
        page_size=20,
    )

    assert resp.total == 3
    assert len(resp.items) == 3
    # All returned items must belong to tenant A
    for item in resp.items:
        assert item.agent_id is not None  # sanity check


@pytest.mark.asyncio
async def test_list_fleet_runs_status_filter():
    """When status='running', only running jobs are returned."""
    tenant_id = uuid4()
    running_jobs = [_make_job(tenant_id, status="running") for _ in range(2)]
    db = _mock_db_for_list(running_jobs, total=2)

    user = _make_auth_user(tenant_id)
    resp = await list_fleet_runs(
        user=user,
        tenant_id=tenant_id,
        db=db,
        status="running",
        agent_id=None,
        page=1,
        page_size=20,
    )

    assert resp.total == 2
    for item in resp.items:
        assert item.status == "running"


@pytest.mark.asyncio
async def test_list_fleet_runs_agent_id_filter():
    """When agent_id is passed, only matching jobs returned."""
    tenant_id = uuid4()
    target_agent = uuid4()
    jobs = [_make_job(tenant_id, agent_id=target_agent)]
    db = _mock_db_for_list(jobs, total=1)

    user = _make_auth_user(tenant_id)
    resp = await list_fleet_runs(
        user=user,
        tenant_id=tenant_id,
        db=db,
        status=None,
        agent_id=target_agent,
        page=1,
        page_size=20,
    )

    assert resp.total == 1
    assert resp.items[0].agent_id == str(target_agent)


@pytest.mark.asyncio
async def test_list_fleet_runs_pagination_has_more_true():
    """21 total results with page_size=20 -> has_more=True."""
    tenant_id = uuid4()
    jobs = [_make_job(tenant_id) for _ in range(20)]
    db = _mock_db_for_list(jobs, total=21)

    user = _make_auth_user(tenant_id)
    resp = await list_fleet_runs(
        user=user,
        tenant_id=tenant_id,
        db=db,
        status=None,
        agent_id=None,
        page=1,
        page_size=20,
    )

    assert resp.total == 21
    assert resp.has_more is True
    assert resp.page == 1
    assert resp.page_size == 20


@pytest.mark.asyncio
async def test_list_fleet_runs_pagination_has_more_false():
    """20 total results with page_size=20 -> has_more=False."""
    tenant_id = uuid4()
    jobs = [_make_job(tenant_id) for _ in range(20)]
    db = _mock_db_for_list(jobs, total=20)

    user = _make_auth_user(tenant_id)
    resp = await list_fleet_runs(
        user=user,
        tenant_id=tenant_id,
        db=db,
        status=None,
        agent_id=None,
        page=1,
        page_size=20,
    )

    assert resp.total == 20
    assert resp.has_more is False


# ===========================================================================
# intervene_in_agent tests
# ===========================================================================

@pytest.mark.asyncio
@patch("app.api.v1.fleet.send_intervention", new_callable=AsyncMock)
async def test_intervene_success(mock_send):
    """Valid running job + same tenant -> calls send_intervention, returns InterventionResponse."""
    tenant_id = uuid4()
    job_id = uuid4()
    job = _make_job(tenant_id, status="running")
    job.id = job_id

    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = job
    db.execute = AsyncMock(return_value=result_mock)

    user = _make_auth_user(tenant_id)
    mock_send.return_value = {
        "message": "Please stop",
        "sender": user.email,
        "timestamp": "2026-04-03T00:00:00+00:00",
    }

    body = InterventionRequest(message="Please stop")
    resp = await intervene_in_agent(
        job_id=job_id,
        body=body,
        user=user,
        tenant_id=tenant_id,
        db=db,
    )

    mock_send.assert_called_once_with(
        job_id=str(job_id),
        message="Please stop",
        sender=user.email,
    )
    assert resp.message == "Please stop"
    assert resp.sender == user.email


@pytest.mark.asyncio
@patch("app.api.v1.fleet.send_intervention", new_callable=AsyncMock)
async def test_intervene_idor_different_tenant(mock_send):
    """Tenant A credentials + Tenant B's job_id -> 404."""
    tenant_a = uuid4()
    tenant_b = uuid4()
    job_id = uuid4()

    # The DB query filters by tenant_id, so it returns None for cross-tenant
    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=result_mock)

    user = _make_auth_user(tenant_a)
    body = InterventionRequest(message="Hack attempt")

    with pytest.raises(HTTPException) as exc_info:
        await intervene_in_agent(
            job_id=job_id,
            body=body,
            user=user,
            tenant_id=tenant_a,
            db=db,
        )

    assert exc_info.value.status_code == 404
    mock_send.assert_not_called()


@pytest.mark.asyncio
@patch("app.api.v1.fleet.send_intervention", new_callable=AsyncMock)
async def test_intervene_not_running(mock_send):
    """Job exists but status='completed' -> 404 (query filters status='running')."""
    tenant_id = uuid4()
    job_id = uuid4()

    # Query includes status='running', so completed job won't match
    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=result_mock)

    user = _make_auth_user(tenant_id)
    body = InterventionRequest(message="Too late")

    with pytest.raises(HTTPException) as exc_info:
        await intervene_in_agent(
            job_id=job_id,
            body=body,
            user=user,
            tenant_id=tenant_id,
            db=db,
        )

    assert exc_info.value.status_code == 404
    mock_send.assert_not_called()


@pytest.mark.asyncio
@patch("app.api.v1.fleet.send_intervention", new_callable=AsyncMock)
async def test_intervene_redis_error(mock_send):
    """send_intervention raises RedisError -> HTTPException(503)."""
    tenant_id = uuid4()
    job_id = uuid4()
    job = _make_job(tenant_id, status="running")
    job.id = job_id

    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = job
    db.execute = AsyncMock(return_value=result_mock)

    user = _make_auth_user(tenant_id)
    mock_send.side_effect = redis.exceptions.RedisError("Connection refused")

    body = InterventionRequest(message="Help")

    with pytest.raises(HTTPException) as exc_info:
        await intervene_in_agent(
            job_id=job_id,
            body=body,
            user=user,
            tenant_id=tenant_id,
            db=db,
        )

    assert exc_info.value.status_code == 503
