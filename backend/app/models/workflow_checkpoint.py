"""
WorkflowCheckpoint SQLAlchemy model for durable workflow execution.

Part of Phase 1: Production Reliability.
Enables zero data loss on crash via DBOS-style automatic checkpointing.

Stores intermediate state after each workflow node execution to enable:
- Crash recovery from last completed step
- Regulatory audit replay capability
- Session teleportation between servers
"""

from sqlalchemy import (
    Column,
    String,
    Integer,
    DateTime,
    Text,
    ForeignKey,
    Index,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from app.models import Base


class WorkflowCheckpoint(Base):
    """
    Checkpoint for durable workflow execution.

    Stores intermediate state after each workflow node execution to enable
    crash recovery. If workflow fails or pod restarts, execution can resume
    from last completed checkpoint.

    Retention: Deleted 24 hours after workflow completion (configurable).
    Encryption: Sensitive data in input/output_data can be encrypted at
                application level using same Fernet key as API keys.

    Attributes:
        id: Unique checkpoint identifier
        tenant_id: Multi-tenant isolation
        workflow_uuid: DBOS workflow UUID for tracking
        workflow_execution_id: Optional link to workflow_executions table
        step_number: Sequential step number in workflow
        step_name: Human-readable step name (e.g., "llm:node-123")
        node_id: React Flow node ID
        input_data: Input state for this step (JSONB)
        output_data: Output state after completion (JSONB)
        status: "pending", "running", "completed", "failed"
        error_message: Error details if status is "failed"
        started_at: When step execution started
        completed_at: When step execution completed
        elapsed_ms: Execution time in milliseconds
        created_at: Record creation timestamp
    """

    __tablename__ = "workflow_checkpoints"

    # Primary key
    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # Multi-tenancy
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Workflow tracking
    workflow_uuid = Column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    workflow_execution_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workflow_executions.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Checkpoint identification
    step_number = Column(Integer, nullable=False)
    step_name = Column(String(255), nullable=False)
    node_id = Column(String(255), nullable=False)

    # Input/Output state (JSONB for efficient querying)
    input_data = Column(JSONB, nullable=False, default=dict)
    output_data = Column(JSONB, nullable=True)

    # Status tracking
    status = Column(
        String(50),
        nullable=False,
        default="pending",
    )
    error_message = Column(Text, nullable=True)

    # Timing
    started_at = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
    )
    completed_at = Column(DateTime(timezone=True), nullable=True)
    elapsed_ms = Column(Integer, nullable=True)

    # Metadata
    created_at = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
    )

    # Relationships
    tenant = relationship("Tenant", backref="workflow_checkpoints")

    # Table constraints
    __table_args__ = (
        UniqueConstraint(
            "workflow_uuid",
            "step_number",
            name="uq_workflow_step",
        ),
        Index("idx_checkpoints_workflow_uuid", "workflow_uuid"),
        Index("idx_checkpoints_status", "status"),
        Index("idx_checkpoints_tenant_workflow", "tenant_id", "workflow_uuid"),
    )

    def __repr__(self) -> str:
        return (
            f"<WorkflowCheckpoint("
            f"workflow={self.workflow_uuid}, "
            f"step={self.step_number}, "
            f"status={self.status}"
            f")>"
        )

    def to_dict(self) -> dict:
        """Convert checkpoint to dictionary for API responses."""
        return {
            "id": str(self.id),
            "workflow_uuid": str(self.workflow_uuid),
            "step_number": self.step_number,
            "step_name": self.step_name,
            "node_id": self.node_id,
            "status": self.status,
            "has_output": self.output_data is not None,
            "error": self.error_message,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "elapsed_ms": self.elapsed_ms,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def mark_running(self) -> None:
        """Mark checkpoint as running."""
        self.status = "running"
        self.started_at = datetime.utcnow()

    def mark_completed(self, output_data: dict = None) -> None:
        """Mark checkpoint as completed with optional output data."""
        self.status = "completed"
        self.completed_at = datetime.utcnow()
        if output_data:
            self.output_data = output_data
        if self.started_at:
            delta = self.completed_at - self.started_at
            self.elapsed_ms = int(delta.total_seconds() * 1000)

    def mark_failed(self, error_message: str) -> None:
        """Mark checkpoint as failed with error message."""
        self.status = "failed"
        self.completed_at = datetime.utcnow()
        self.error_message = error_message
        if self.started_at:
            delta = self.completed_at - self.started_at
            self.elapsed_ms = int(delta.total_seconds() * 1000)
