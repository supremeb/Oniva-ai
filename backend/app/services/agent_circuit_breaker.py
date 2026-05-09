"""Agent Circuit Breaker Service for Agent Studio (Phase A.4).

Provides production safety with binary healthy/degraded state.
Automatically trips when quality thresholds are breached.
"""
from uuid import UUID
from typing import Optional, Dict, Any
from datetime import timedelta
from enum import Enum
import logging

from fastapi import BackgroundTasks

from app.exceptions import CircuitBreakerOpenError


logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    """Circuit breaker states."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"


# Quality thresholds for tripping the circuit
DEFAULT_MIN_ACCURACY = 0.80  # 80% minimum accuracy
DEFAULT_MAX_ERROR_RATE = 0.05  # 5% maximum error rate
DEFAULT_RECOVERY_TTL_MINUTES = 15  # Auto-recover after 15 minutes


class AgentCircuitBreaker:
    """
    Circuit breaker for agent production safety.

    Binary state model:
    - HEALTHY: Agent is operating normally, all operations allowed
    - DEGRADED: Agent quality has dropped, executions blocked

    Triggers:
    - Accuracy drops below threshold (default: 80%)
    - Error rate exceeds threshold (default: 5%)

    Recovery:
    - Auto-recovery after TTL (default: 15 minutes)
    - Manual recovery via reset()
    - Re-evaluation passing quality gates

    Usage:
        breaker = AgentCircuitBreaker(redis_client)

        # Check before execution
        await breaker.check_health(agent_id)  # raises if degraded

        # Record execution result
        await breaker.record_execution(agent_id, success=True)

        # Trip on quality failure
        await breaker.trip(agent_id, "Accuracy dropped to 65%")

        # Manual recovery
        await breaker.reset(agent_id)
    """

    def __init__(
        self,
        redis_client: Any,
        min_accuracy: float = DEFAULT_MIN_ACCURACY,
        max_error_rate: float = DEFAULT_MAX_ERROR_RATE,
        recovery_ttl_minutes: int = DEFAULT_RECOVERY_TTL_MINUTES
    ):
        self.redis = redis_client
        self.min_accuracy = min_accuracy
        self.max_error_rate = max_error_rate
        self.recovery_ttl = timedelta(minutes=recovery_ttl_minutes)

    def _state_key(self, agent_id: UUID) -> str:
        """Redis key for circuit state."""
        return f"circuit:agent:{agent_id}:state"

    def _reason_key(self, agent_id: UUID) -> str:
        """Redis key for degradation reason."""
        return f"circuit:agent:{agent_id}:reason"

    def _metrics_key(self, agent_id: UUID) -> str:
        """Redis key for rolling metrics."""
        return f"circuit:agent:{agent_id}:metrics"

    async def get_state(self, agent_id: UUID) -> CircuitState:
        """
        Get the current circuit state for an agent.

        Args:
            agent_id: Agent UUID

        Returns:
            CircuitState (HEALTHY or DEGRADED)
        """
        state = await self.redis.get(self._state_key(agent_id))
        if state is None:
            return CircuitState.HEALTHY
        return CircuitState(state.decode() if isinstance(state, bytes) else state)

    async def get_degradation_reason(self, agent_id: UUID) -> Optional[str]:
        """
        Get the reason for degradation if the circuit is open.

        Args:
            agent_id: Agent UUID

        Returns:
            Reason string or None if healthy
        """
        reason = await self.redis.get(self._reason_key(agent_id))
        if reason is None:
            return None
        return reason.decode() if isinstance(reason, bytes) else reason

    async def check_health(self, agent_id: UUID) -> None:
        """
        Check if the agent is healthy. Raises if degraded.

        Args:
            agent_id: Agent UUID

        Raises:
            CircuitBreakerOpenError: If agent is in degraded state
        """
        state = await self.get_state(agent_id)
        if state == CircuitState.DEGRADED:
            reason = await self.get_degradation_reason(agent_id) or "Unknown"
            raise CircuitBreakerOpenError(agent_id, reason)

    async def is_healthy(self, agent_id: UUID) -> bool:
        """
        Check if the agent is healthy (non-throwing version).

        Args:
            agent_id: Agent UUID

        Returns:
            True if healthy, False if degraded
        """
        return await self.get_state(agent_id) == CircuitState.HEALTHY

    async def trip(
        self,
        agent_id: UUID,
        reason: str,
        ttl_minutes: Optional[int] = None
    ) -> None:
        """
        Trip the circuit breaker to degraded state.

        Args:
            agent_id: Agent UUID
            reason: Why the circuit is being tripped
            ttl_minutes: Override auto-recovery TTL
        """
        ttl = timedelta(minutes=ttl_minutes) if ttl_minutes else self.recovery_ttl
        ttl_seconds = int(ttl.total_seconds())

        # Set state with TTL for auto-recovery
        await self.redis.setex(
            self._state_key(agent_id),
            ttl_seconds,
            CircuitState.DEGRADED.value
        )
        await self.redis.setex(
            self._reason_key(agent_id),
            ttl_seconds,
            reason
        )

        logger.warning(
            f"Circuit breaker TRIPPED for agent {agent_id}: {reason} "
            f"(auto-recovery in {ttl_minutes or self.recovery_ttl.total_seconds() // 60} minutes)"
        )

    async def reset(self, agent_id: UUID) -> None:
        """
        Reset the circuit breaker to healthy state.

        Args:
            agent_id: Agent UUID
        """
        await self.redis.delete(self._state_key(agent_id))
        await self.redis.delete(self._reason_key(agent_id))

        logger.info(f"Circuit breaker RESET for agent {agent_id}")

    async def record_execution(
        self,
        agent_id: UUID,
        success: bool,
        window_size: int = 100
    ) -> None:
        """
        Record an execution result for rolling metrics.

        Uses a Redis list to maintain a sliding window of results.

        Args:
            agent_id: Agent UUID
            success: Whether the execution succeeded
            window_size: Size of the rolling window
        """
        metrics_key = self._metrics_key(agent_id)

        # Add result (1 = success, 0 = failure)
        await self.redis.lpush(metrics_key, "1" if success else "0")
        # Trim to window size
        await self.redis.ltrim(metrics_key, 0, window_size - 1)
        # Set expiry on metrics (24 hours)
        await self.redis.expire(metrics_key, 86400)

    async def get_metrics(self, agent_id: UUID) -> Dict[str, Any]:
        """
        Get rolling metrics for an agent.

        Args:
            agent_id: Agent UUID

        Returns:
            Dict with success_rate, total_executions, recent_failures
        """
        metrics_key = self._metrics_key(agent_id)
        results = await self.redis.lrange(metrics_key, 0, -1)

        if not results:
            return {
                "success_rate": 1.0,
                "total_executions": 0,
                "recent_failures": 0,
            }

        # Decode results
        decoded = [int(r.decode() if isinstance(r, bytes) else r) for r in results]
        total = len(decoded)
        successes = sum(decoded)
        failures = total - successes

        return {
            "success_rate": successes / total if total > 0 else 1.0,
            "total_executions": total,
            "recent_failures": failures,
        }

    async def check_and_trip(
        self,
        agent_id: UUID,
        accuracy: Optional[float] = None,
        error_rate: Optional[float] = None,
        background_tasks: Optional[BackgroundTasks] = None
    ) -> bool:
        """
        Check quality metrics and trip the circuit if thresholds are breached.

        Args:
            agent_id: Agent UUID
            accuracy: Current accuracy (0.0 - 1.0)
            error_rate: Current error rate (0.0 - 1.0)
            background_tasks: Optional background tasks for auto-rollback

        Returns:
            True if circuit was tripped
        """
        reasons = []

        if accuracy is not None and accuracy < self.min_accuracy:
            reasons.append(f"Accuracy {accuracy:.1%} < {self.min_accuracy:.0%}")

        if error_rate is not None and error_rate > self.max_error_rate:
            reasons.append(f"Error rate {error_rate:.1%} > {self.max_error_rate:.0%}")

        if reasons:
            reason = "; ".join(reasons)
            await self.trip(agent_id, reason)
            return True

        return False

    async def get_bulk_status(self, agent_ids: list[UUID]) -> Dict[str, Dict[str, Any]]:
        """
        Fetch circuit breaker status for multiple agents in one Redis MGET.

        Args:
            agent_ids: List of agent UUIDs

        Returns:
            Dict mapping agent_id string to {state, reason} dict
        """
        if not agent_ids:
            return {}

        state_keys = [self._state_key(aid) for aid in agent_ids]
        values = await self.redis.mget(state_keys)

        result = {}
        for aid, val in zip(agent_ids, values):
            aid_str = str(aid)
            if val:
                decoded = val.decode() if isinstance(val, bytes) else val
                result[aid_str] = {"state": decoded}
            else:
                result[aid_str] = {"state": CircuitState.HEALTHY.value}
        return result

    async def get_status(self, agent_id: UUID) -> Dict[str, Any]:
        """
        Get comprehensive circuit breaker status.

        Args:
            agent_id: Agent UUID

        Returns:
            Dict with state, reason, metrics, thresholds
        """
        state = await self.get_state(agent_id)
        reason = await self.get_degradation_reason(agent_id)
        metrics = await self.get_metrics(agent_id)

        return {
            "agent_id": str(agent_id),
            "state": state.value,
            "is_healthy": state == CircuitState.HEALTHY,
            "reason": reason,
            "metrics": metrics,
            "thresholds": {
                "min_accuracy": self.min_accuracy,
                "max_error_rate": self.max_error_rate,
                "recovery_ttl_minutes": int(self.recovery_ttl.total_seconds() // 60),
            },
        }


# Drift detection helper (inline function per plan)
async def check_drift_and_trip(
    breaker: AgentCircuitBreaker,
    agent_id: UUID,
    current_accuracy: float,
    baseline_accuracy: float,
    max_drift: float = 0.05  # 5% max drift
) -> bool:
    """
    Check for accuracy drift and trip circuit if excessive.

    Args:
        breaker: AgentCircuitBreaker instance
        agent_id: Agent UUID
        current_accuracy: Current evaluation accuracy
        baseline_accuracy: Baseline accuracy from deployed version
        max_drift: Maximum allowed accuracy drop

    Returns:
        True if drift detected and circuit tripped
    """
    drift = baseline_accuracy - current_accuracy

    if drift > max_drift:
        await breaker.trip(
            agent_id,
            f"Accuracy drift: {drift:.1%} drop from {baseline_accuracy:.1%} to {current_accuracy:.1%}"
        )
        return True

    return False
