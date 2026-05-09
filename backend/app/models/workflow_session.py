"""
Workflow Session model for filesystem-based context management.

Different from ChatSession - this tracks long-running workflow state
with filesystem-backed session storage for multi-hour executions.

Part of Phase 2: Filesystem Sessions.
"""
from sqlalchemy import Column, String, DateTime, ForeignKey, Text, Integer, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from enum import Enum
import uuid

from app.models import Base


class SessionStatus(str, Enum):
    """Valid session status values."""
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ARCHIVED = "archived"
    ERROR = "error"


class WorkflowSession(Base):
    """
    Filesystem-backed session for multi-hour workflow execution.

    Stores metadata in PostgreSQL; actual files on disk at SESSION_BASE_PATH.
    Supports sessions lasting 8+ hours with automatic checkpointing via DBOS.

    File structure (per session):
    {SESSION_BASE_PATH}/{tenant_id}/{session_id}/
        ├── context.json      # Working memory state
        ├── artifacts/        # Generated files
        ├── checkpoints/      # DBOS checkpoint data
        └── logs/             # Execution logs
    """
    __tablename__ = 'workflow_sessions'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Multi-tenancy (required for enterprise deployment)
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey('tenants.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey('users.id', ondelete='CASCADE'),
        nullable=False
    )

    workflow_id = Column(
        UUID(as_uuid=True),
        ForeignKey('workflows.id', ondelete='SET NULL'),
        nullable=True
    )

    # Session metadata
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(50), default=SessionStatus.ACTIVE.value)

    # Filesystem path (relative to SESSION_BASE_PATH)
    filesystem_path = Column(String(500), nullable=False)

    # State tracking
    current_state = Column(String(50), nullable=True)  # WorkflowState enum from state machine
    last_checkpoint_id = Column(UUID(as_uuid=True), nullable=True)  # Latest DBOS checkpoint

    # Resource tracking
    total_size_bytes = Column(Integer, default=0)
    file_count = Column(Integer, default=0)

    # Context management
    context_window_used = Column(Integer, default=0)  # Tokens in current context
    context_window_max = Column(Integer, default=128000)  # Max tokens allowed

    # Cost tracking
    total_tokens_used = Column(Integer, default=0)
    total_cost_usd = Column(Integer, default=0)  # Stored as cents to avoid float precision

    # Session metadata (agent config, model settings, etc.)
    # Note: Using 'session_metadata' as 'metadata' is reserved by SQLAlchemy
    session_metadata = Column(JSONB, default=dict)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    last_activity_at = Column(DateTime(timezone=True), server_default=func.now())
    archived_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    user = relationship("User")
    tenant = relationship("Tenant")
    workflow = relationship("Workflow")

    def __repr__(self) -> str:
        return f"<WorkflowSession {self.id} name={self.name} status={self.status}>"
