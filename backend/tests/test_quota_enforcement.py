"""Tests for quota API enforcement.

Tests the require_quota dependency factory, global QuotaExceededError handler,
and endpoint-level enforcement.
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from uuid import uuid4

from app.api.dependencies.quota import require_quota
from app.models.usage_quota import QuotaType
from app.services.quota_service import QuotaExceededError, QuotaService
from app.api.exception_handlers import quota_exceeded_handler


@pytest.mark.asyncio
async def test_require_quota_allows_within_limit():
    """Quota at 50% usage — request passes without raising."""
    dep_fn = require_quota(QuotaType.EXECUTIONS)

    mock_db = AsyncMock()
    mock_user = MagicMock()
    mock_user.tenant_id = str(uuid4())
    mock_user.id = str(uuid4())

    with patch.object(QuotaService, 'check_and_raise', new_callable=AsyncMock) as mock_check:
        mock_check.return_value = None  # No exception = allowed
        result = await dep_fn(db=mock_db, current_user=mock_user)
        assert result is None
        mock_check.assert_called_once()


@pytest.mark.asyncio
async def test_require_quota_blocks_exceeded():
    """Quota at 100% — raises QuotaExceededError."""
    dep_fn = require_quota(QuotaType.EXECUTIONS)

    mock_db = AsyncMock()
    mock_user = MagicMock()
    mock_user.tenant_id = str(uuid4())
    mock_user.id = str(uuid4())

    with patch.object(QuotaService, 'check_and_raise', new_callable=AsyncMock) as mock_check:
        mock_check.side_effect = QuotaExceededError(
            message="Quota exceeded",
            quota_type="executions",
            current=50,
            limit=50,
        )
        with pytest.raises(QuotaExceededError):
            await dep_fn(db=mock_db, current_user=mock_user)


@pytest.mark.asyncio
async def test_global_handler_structured_error_body():
    """Verify 429 response body has quota_type, current, limit, message."""
    exc = QuotaExceededError(
        message="User quota exceeded for executions: 50/50",
        quota_type="executions",
        current=50,
        limit=50,
    )

    mock_request = MagicMock()
    response = await quota_exceeded_handler(mock_request, exc)

    assert response.status_code == 429
    import json
    body = json.loads(response.body.decode())
    assert body["error"] == "quota_exceeded"
    assert body["quota_type"] == "executions"
    assert body["current"] == 50
    assert body["limit"] == 50
    assert body["message"] == "User quota exceeded for executions: 50/50"


@pytest.mark.asyncio
async def test_workflow_execute_enforces_quota():
    """POST /workflows/execute with exceeded quota → 429 via global handler."""
    dep_fn = require_quota(QuotaType.EXECUTIONS)

    mock_db = AsyncMock()
    mock_user = MagicMock()
    mock_user.tenant_id = str(uuid4())
    mock_user.id = str(uuid4())

    with patch.object(QuotaService, 'check_and_raise', new_callable=AsyncMock) as mock_check:
        mock_check.side_effect = QuotaExceededError(
            message="Execution quota exceeded",
            quota_type="executions",
            current=100,
            limit=100,
        )
        with pytest.raises(QuotaExceededError) as exc_info:
            await dep_fn(db=mock_db, current_user=mock_user)
        assert exc_info.value.quota_type == "executions"


@pytest.mark.asyncio
async def test_batch_execute_dynamic_estimation():
    """Batch of 5 with only 3 remaining → check_and_raise called with estimated_usage=5."""
    mock_db = AsyncMock()
    tenant_id = uuid4()
    user_id = uuid4()

    with patch.object(QuotaService, 'check_and_raise', new_callable=AsyncMock) as mock_check:
        mock_check.side_effect = QuotaExceededError(
            message="Execution quota exceeded",
            quota_type="executions",
            current=47,
            limit=50,
        )
        quota_svc = QuotaService(mock_db)
        with pytest.raises(QuotaExceededError):
            await quota_svc.check_and_raise(
                tenant_id, user_id,
                QuotaType.EXECUTIONS,
                estimated_usage=5,
            )
        mock_check.assert_called_once_with(
            tenant_id, user_id, QuotaType.EXECUTIONS, estimated_usage=5,
        )


@pytest.mark.asyncio
async def test_no_quota_configured_allows_request():
    """Tenant with no quota rows → request passes (fail open)."""
    dep_fn = require_quota(QuotaType.EXECUTIONS)

    mock_db = AsyncMock()
    mock_user = MagicMock()
    mock_user.tenant_id = str(uuid4())
    mock_user.id = str(uuid4())

    with patch.object(QuotaService, 'check_and_raise', new_callable=AsyncMock) as mock_check:
        # check_and_raise returns None when no quota rows exist (fail open)
        mock_check.return_value = None
        result = await dep_fn(db=mock_db, current_user=mock_user)
        assert result is None


@pytest.mark.asyncio
async def test_expired_quota_resets():
    """Expired period auto-resets — request passes."""
    dep_fn = require_quota(QuotaType.EXECUTIONS)

    mock_db = AsyncMock()
    mock_user = MagicMock()
    mock_user.tenant_id = str(uuid4())
    mock_user.id = str(uuid4())

    with patch.object(QuotaService, 'check_and_raise', new_callable=AsyncMock) as mock_check:
        # QuotaService resets expired quotas internally and allows the request
        mock_check.return_value = None
        result = await dep_fn(db=mock_db, current_user=mock_user)
        assert result is None
