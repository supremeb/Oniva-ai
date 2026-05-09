"""Sandbox Execution model for tracking E2B code execution."""
from sqlalchemy import Column, String, DateTime, Float, Text, Index, func, text
from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime
from enum import Enum
import uuid

from app.models import Base


class SandboxExecutionStatus(str, Enum):
    """Sandbox execution status."""
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"


class SandboxExecution(Base):
    """
    Tracks sandbox code executions.

    One table only. sandbox_quotas deferred until tenants hit cost limits.
    Cost is derivable from duration_seconds * config rate.
    """
    __tablename__ = "sandbox_executions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=False)
    sandbox_id = Column(String(255), nullable=False)
    code = Column(Text, nullable=False)
    status = Column(String(50), nullable=False, default=SandboxExecutionStatus.PENDING_REVIEW.value)
    stdout = Column(Text, nullable=True)
    stderr = Column(Text, nullable=True)
    duration_seconds = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), default=datetime.utcnow)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_sandbox_executions_tenant_created", "tenant_id", "created_at"),
        Index(
            "idx_sandbox_executions_status_active",
            "status",
            postgresql_where=text("status NOT IN ('completed', 'failed')"),
        ),
    )

    def __repr__(self):
        return f"<SandboxExecution(id='{self.id}', status='{self.status}')>"

    def to_dict(self):
        return {
            "id": str(self.id),
            "session_id": str(self.session_id),
            "tenant_id": str(self.tenant_id),
            "sandbox_id": self.sandbox_id,
            "code": self.code,
            "status": self.status,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_seconds": self.duration_seconds,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }
