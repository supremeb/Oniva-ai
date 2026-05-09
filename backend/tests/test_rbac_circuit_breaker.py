"""Tests for RBAC and Circuit Breaker wiring (Phase A).

Tests:
1. RBAC: unauthorized access returns 403
2. RBAC: team member can access team agent based on role
3. Circuit Breaker: DEGRADED agent returns 503
4. Circuit Breaker: HEALTHY agent executes normally
"""

import pytest
import uuid
from unittest.mock import AsyncMock, patch, MagicMock

from fastapi import Request
from fastapi.responses import JSONResponse

from app.api.exception_handlers import (
    permission_denied_handler,
    circuit_breaker_handler,
)
from app.exceptions import PermissionDeniedError, CircuitBreakerOpenError
from app.services.agent_rbac import AgentRBACService, AgentPermission, ROLE_HIERARCHY
from app.services.agent_circuit_breaker import AgentCircuitBreaker, CircuitState
from app.models.team import TeamRole


# ============================================================================
# Exception Handler Tests
# ============================================================================

class TestExceptionHandlers:
    """Tests for global exception handlers."""

    @pytest.mark.asyncio
    async def test_permission_denied_handler_returns_403(self):
        """Test that PermissionDeniedError maps to 403 response."""
        # Create exception
        exc = PermissionDeniedError(
            operation="edit agent 12345",
            required_role="member"
        )

        # Create mock request
        mock_request = MagicMock(spec=Request)

        # Call handler
        response = await permission_denied_handler(mock_request, exc)

        # Verify
        assert isinstance(response, JSONResponse)
        assert response.status_code == 403
        assert response.body is not None

    @pytest.mark.asyncio
    async def test_circuit_breaker_handler_returns_503(self):
        """Test that CircuitBreakerOpenError maps to 503 response."""
        # Create exception
        agent_id = uuid.uuid4()
        exc = CircuitBreakerOpenError(
            agent_id=agent_id,
            reason="Accuracy dropped to 65%"
        )

        # Create mock request
        mock_request = MagicMock(spec=Request)

        # Call handler
        response = await circuit_breaker_handler(mock_request, exc)

        # Verify
        assert isinstance(response, JSONResponse)
        assert response.status_code == 503
        assert response.headers.get("Retry-After") == "900"


# ============================================================================
# RBAC Service Tests
# ============================================================================

class TestAgentRBACService:
    """Tests for AgentRBACService."""

    def test_role_hierarchy_permissions(self):
        """Test that role hierarchy grants correct permissions."""
        # VIEWER can only VIEW
        assert AgentPermission.VIEW in ROLE_HIERARCHY[TeamRole.VIEWER]
        assert AgentPermission.EDIT not in ROLE_HIERARCHY[TeamRole.VIEWER]
        assert AgentPermission.MANAGE not in ROLE_HIERARCHY[TeamRole.VIEWER]

        # MEMBER can VIEW and EDIT
        assert AgentPermission.VIEW in ROLE_HIERARCHY[TeamRole.MEMBER]
        assert AgentPermission.EDIT in ROLE_HIERARCHY[TeamRole.MEMBER]
        assert AgentPermission.MANAGE not in ROLE_HIERARCHY[TeamRole.MEMBER]

        # ADMIN can VIEW, EDIT, and DEPLOY
        assert AgentPermission.VIEW in ROLE_HIERARCHY[TeamRole.ADMIN]
        assert AgentPermission.EDIT in ROLE_HIERARCHY[TeamRole.ADMIN]
        assert AgentPermission.DEPLOY in ROLE_HIERARCHY[TeamRole.ADMIN]
        assert AgentPermission.MANAGE not in ROLE_HIERARCHY[TeamRole.ADMIN]

        # OWNER can do everything
        assert AgentPermission.VIEW in ROLE_HIERARCHY[TeamRole.OWNER]
        assert AgentPermission.EDIT in ROLE_HIERARCHY[TeamRole.OWNER]
        assert AgentPermission.DEPLOY in ROLE_HIERARCHY[TeamRole.OWNER]
        assert AgentPermission.MANAGE in ROLE_HIERARCHY[TeamRole.OWNER]

    @pytest.mark.asyncio
    async def test_require_permission_raises_on_unauthorized(self):
        """Test that require_permission raises PermissionDeniedError."""
        # Mock database session
        mock_db = AsyncMock()
        rbac = AgentRBACService(mock_db)

        # Mock has_permission to return (False, mock_agent)
        mock_agent = MagicMock()
        with patch.object(rbac, 'has_permission', return_value=(False, mock_agent)):
            with pytest.raises(PermissionDeniedError) as exc_info:
                await rbac.require_permission(
                    user_id=uuid.uuid4(),
                    agent_id=uuid.uuid4(),
                    tenant_id=uuid.uuid4(),
                    permission=AgentPermission.EDIT
                )

            assert "edit" in str(exc_info.value).lower()


# ============================================================================
# Circuit Breaker Tests
# ============================================================================

class TestAgentCircuitBreaker:
    """Tests for AgentCircuitBreaker."""

    @pytest.mark.asyncio
    async def test_healthy_agent_passes_check(self):
        """Test that healthy agents pass the health check."""
        # Mock redis client
        mock_redis = AsyncMock()
        mock_redis.get.return_value = None  # No state = healthy

        breaker = AgentCircuitBreaker(mock_redis)
        agent_id = uuid.uuid4()

        # Should not raise
        await breaker.check_health(agent_id)

        # Verify
        is_healthy = await breaker.is_healthy(agent_id)
        assert is_healthy is True

    @pytest.mark.asyncio
    async def test_degraded_agent_raises_error(self):
        """Test that degraded agents raise CircuitBreakerOpenError."""
        # Mock redis client
        mock_redis = AsyncMock()
        mock_redis.get.side_effect = lambda key: (
            CircuitState.DEGRADED.value.encode()
            if "state" in key
            else b"Accuracy dropped to 65%"
        )

        breaker = AgentCircuitBreaker(mock_redis)
        agent_id = uuid.uuid4()

        # Should raise
        with pytest.raises(CircuitBreakerOpenError) as exc_info:
            await breaker.check_health(agent_id)

        assert exc_info.value.agent_id == agent_id
        assert "65%" in exc_info.value.reason

    @pytest.mark.asyncio
    async def test_trip_sets_degraded_state(self):
        """Test that trip() sets degraded state with TTL."""
        # Mock redis client
        mock_redis = AsyncMock()

        breaker = AgentCircuitBreaker(mock_redis)
        agent_id = uuid.uuid4()
        reason = "Error rate 10% > 5%"

        # Trip the breaker
        await breaker.trip(agent_id, reason)

        # Verify setex was called for both state and reason
        assert mock_redis.setex.call_count == 2
        calls = mock_redis.setex.call_args_list

        # First call should be state
        state_key = calls[0][0][0]
        assert f"circuit:agent:{agent_id}:state" == state_key

        # Second call should be reason
        reason_key = calls[1][0][0]
        assert f"circuit:agent:{agent_id}:reason" == reason_key

    @pytest.mark.asyncio
    async def test_reset_clears_state(self):
        """Test that reset() clears degraded state."""
        # Mock redis client
        mock_redis = AsyncMock()

        breaker = AgentCircuitBreaker(mock_redis)
        agent_id = uuid.uuid4()

        # Reset the breaker
        await breaker.reset(agent_id)

        # Verify delete was called for both keys
        assert mock_redis.delete.call_count == 2
