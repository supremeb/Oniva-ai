"""Custom exceptions for the Oniva application."""
from uuid import UUID
from typing import Optional


class FIAgentsException(Exception):
    """Base exception for all Oniva errors."""
    pass


class OptimisticLockError(FIAgentsException):
    """Raised when optimistic locking detects a concurrent modification.

    This exception indicates that another process has modified the entity
    since it was read, and the current operation should be retried.
    """
    def __init__(
        self,
        message: str,
        entity_id: Optional[UUID] = None,
        expected_version: Optional[int] = None,
        actual_version: Optional[int] = None
    ):
        super().__init__(message)
        self.entity_id = entity_id
        self.expected_version = expected_version
        self.actual_version = actual_version


class EntityNotFoundError(FIAgentsException):
    """Raised when a requested entity does not exist."""
    def __init__(self, entity_type: str, entity_id: UUID):
        super().__init__(f"{entity_type} with id {entity_id} not found")
        self.entity_type = entity_type
        self.entity_id = entity_id


class EntityConflictError(FIAgentsException):
    """Raised when an operation conflicts with existing state (maps to HTTP 409)."""
    def __init__(self, message: str):
        super().__init__(message)


class PermissionDeniedError(FIAgentsException):
    """Raised when a user lacks permission to perform an operation."""
    def __init__(self, operation: str, required_role: str):
        super().__init__(f"Permission denied: {operation} requires {required_role} role")
        self.operation = operation
        self.required_role = required_role


class InvalidStateTransitionError(FIAgentsException):
    """Raised when an invalid state transition is attempted."""
    def __init__(self, current_state: str, target_state: str):
        super().__init__(f"Invalid transition from {current_state} to {target_state}")
        self.current_state = current_state
        self.target_state = target_state


class CircuitBreakerOpenError(FIAgentsException):
    """Raised when an operation is blocked by an open circuit breaker."""
    def __init__(self, agent_id: UUID, reason: str):
        super().__init__(f"Agent {agent_id} is degraded: {reason}")
        self.agent_id = agent_id
        self.reason = reason


class ApprovalRequiredError(FIAgentsException):
    """Raised when an operation requires approval before proceeding."""
    def __init__(self, operation: str, agent_id: UUID, approval_request_id: Optional[UUID] = None):
        super().__init__(f"Operation {operation} on agent {agent_id} requires approval")
        self.operation = operation
        self.agent_id = agent_id
        self.approval_request_id = approval_request_id
