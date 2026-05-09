"""Workflow Batch model for bulk execution.

Enterprise feature: Execute multiple workflow instances in a single API call.
Supports parallel execution with configurable concurrency limits.

- Phase 2: API Integration
"""
from sqlalchemy import Column, String, DateTime, ForeignKey, Integer, Text, Enum, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid
import enum

from app.models import Base


class BatchStatus(str, enum.Enum):
    """Status of a batch execution."""
    QUEUED = "queued"           # Batch created, executions pending
    RUNNING = "running"         # Executions in progress
    COMPLETED = "completed"     # All executions finished successfully
    PARTIALLY_FAILED = "partially_failed"  # Some executions failed
    FAILED = "failed"           # Critical failure, batch aborted
    CANCELLED = "cancelled"     # User cancelled the batch


class WorkflowBatch(Base):
    """
    Tracks a batch of workflow executions.

    Enables enterprise users to execute 1000+ workflows in a single API call
    with parallel processing and progress tracking.

    Features:
    - Parallel execution with configurable limits
    - Progress tracking (completed/failed/pending counts)
    - Webhook callback on batch completion
    - Individual execution results stored for review
    """
    __tablename__ = 'workflow_batches'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Multi-tenancy
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey('tenants.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )

    # Workflow reference
    workflow_id = Column(UUID(as_uuid=True), ForeignKey('workflows.id', ondelete='SET NULL'), nullable=True)

    # Batch configuration
    name = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)
    parallel_limit = Column(Integer, default=10, nullable=False)  # Max concurrent executions

    # Progress tracking
    total_count = Column(Integer, default=0, nullable=False)
    completed_count = Column(Integer, default=0, nullable=False)
    failed_count = Column(Integer, default=0, nullable=False)
    pending_count = Column(Integer, default=0, nullable=False)

    # Status
    status = Column(
        Enum(BatchStatus, name='batch_status'),
        default=BatchStatus.QUEUED,
        nullable=False,
        index=True
    )
    error_message = Column(Text, nullable=True)

    # Webhook callback
    webhook_url = Column(String(2048), nullable=True)
    webhook_secret = Column(String(256), nullable=True)  # Encrypted

    # Timing
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    # Creator
    created_by = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    tenant = relationship("Tenant")
    workflow = relationship("Workflow")
    creator = relationship("User", foreign_keys=[created_by])
    items = relationship("WorkflowBatchItem", back_populates="batch", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<WorkflowBatch(id='{self.id}', status='{self.status}', {self.completed_count}/{self.total_count})>"

    @property
    def progress_percent(self) -> float:
        """Calculate batch progress percentage."""
        if self.total_count == 0:
            return 0.0
        return ((self.completed_count + self.failed_count) / self.total_count) * 100

    @property
    def is_complete(self) -> bool:
        """Check if batch has finished (success or failure)."""
        return self.status in (
            BatchStatus.COMPLETED,
            BatchStatus.PARTIALLY_FAILED,
            BatchStatus.FAILED,
            BatchStatus.CANCELLED
        )


class BatchItemStatus(str, enum.Enum):
    """Status of an individual batch item."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class WorkflowBatchItem(Base):
    """
    Individual execution within a batch.

    Stores the input for each execution and tracks its status independently.
    Results are stored in JSONB for flexible schema.
    """
    __tablename__ = 'workflow_batch_items'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Batch reference
    batch_id = Column(
        UUID(as_uuid=True),
        ForeignKey('workflow_batches.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )

    # Sequence number for ordering
    sequence = Column(Integer, nullable=False, default=0)

    # Execution input
    inputs = Column(JSONB, default=dict, nullable=False)

    # Status and results
    status = Column(
        Enum(BatchItemStatus, name='batch_item_status'),
        default=BatchItemStatus.PENDING,
        nullable=False,
        index=True
    )
    results = Column(JSONB, nullable=True)
    error_message = Column(Text, nullable=True)

    # Timing
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    execution_time_ms = Column(Integer, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    batch = relationship("WorkflowBatch", back_populates="items")

    def __repr__(self):
        return f"<WorkflowBatchItem(batch_id='{self.batch_id}', seq={self.sequence}, status='{self.status}')>"
