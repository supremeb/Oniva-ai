"""Workflow models with multi-tenancy support."""
from sqlalchemy import Column, String, DateTime, ForeignKey, Text, Integer, Boolean, Index, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
import uuid

from app.models import Base


def _utcnow() -> datetime:
    """Timezone-aware UTC: a naive value would be read as server-local time by timestamptz columns."""
    return datetime.now(timezone.utc)


class Workflow(Base):
    """Workflow definition with React Flow graph specification."""
    __tablename__ = 'workflows'
    __table_args__ = (
        Index('idx_workflows_user_tenant_updated', 'user_id', 'tenant_id', 'updated_at'),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'))

    # Multi-tenancy
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey('tenants.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )

    name = Column(String(255), nullable=False)
    description = Column(Text)
    spec = Column(JSONB, nullable=False)  # React Flow graph specification

    # Entity extraction flag (Phase 2: Workflow Entity Tracking)
    extract_entities = Column(
        Boolean,
        nullable=False,
        server_default='true',
        comment="Whether to extract entities from workflow outputs"
    )

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    user = relationship("User", back_populates="workflows")
    tenant = relationship("Tenant")
    executions = relationship("WorkflowExecution", back_populates="workflow", cascade="all, delete-orphan")
    chat_sessions = relationship("ChatSession", back_populates="workflow", cascade="all, delete-orphan")
    background_jobs = relationship("BackgroundJob", back_populates="workflow", cascade="all, delete-orphan")
    entities = relationship("WorkflowEntity", back_populates="workflow", cascade="all, delete-orphan")
    permissions = relationship("WorkflowPermission", back_populates="workflow", cascade="all, delete-orphan")


class WorkflowExecution(Base):
    """
    Tracks workflow executions with full tracing for logs and monitoring.
    DBOS manages the actual workflow state, this is for user-facing history.
    """
    __tablename__ = 'workflow_executions'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_id = Column(UUID(as_uuid=True), ForeignKey('workflows.id', ondelete='CASCADE'), index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'))

    # Multi-tenancy
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey('tenants.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )

    # DBOS workflow UUID for tracking
    dbos_workflow_uuid = Column(UUID(as_uuid=True), unique=True, nullable=True)

    # Engine conversation that runs this execution (plan 003 U1); null for legacy runs.
    engine_conversation_id = Column(UUID(as_uuid=True), nullable=True)

    # Execution details
    status = Column(String(50), default='pending', index=True)  # 'pending', 'running', 'completed', 'failed', 'cancelled'
    trigger = Column(String(50), default='manual')  # 'manual', 'api', 'scheduled'

    # Input/Output data
    input_data = Column(JSONB, default=dict)  # Input variables passed to workflow
    results = Column(JSONB, default=dict)  # Final results
    error = Column(Text, nullable=True)

    # Metrics
    nodes_executed = Column(Integer, default=0)
    total_nodes = Column(Integer, default=0)
    total_tokens = Column(Integer, default=0)
    duration_ms = Column(Integer, nullable=True)  # Execution time in milliseconds

    # Execution trace (stores per-node execution details)
    execution_trace = Column(JSONB, default=list)  # List of ExecutionTraceStep

    # Timestamps
    started_at = Column(DateTime(timezone=True), server_default=func.now(), default=_utcnow, index=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    workflow = relationship("Workflow", back_populates="executions")
    tenant = relationship("Tenant")
    user = relationship("User")
    entities = relationship("WorkflowEntity", back_populates="execution")

    def to_log_summary(self) -> dict:
        """Convert to log summary for API response."""
        return {
            "id": str(self.id),
            "workflow_id": str(self.workflow_id) if self.workflow_id else None,
            "status": self.status,
            "trigger": self.trigger or "manual",
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_ms": self.duration_ms,
            "total_tokens": self.total_tokens or 0,
            "nodes_executed": self.nodes_executed or 0,
            "total_nodes": self.total_nodes,
            "error": self.error,
            "engine_conversation_id": str(self.engine_conversation_id) if self.engine_conversation_id else None,
            "progress": 100 if self.status in ['completed', 'failed'] else (
                int((self.nodes_executed / self.total_nodes) * 100) if self.total_nodes > 0 else 0
            ),
        }

    def to_log_detail(self) -> dict:
        """Convert to detailed log for API response."""
        summary = self.to_log_summary()
        summary.update({
            # Backend bookkeeping (``__spec``, ``__run_options`` with the webhook secret) is not for API readers.
            "input_data": {k: v for k, v in self.input_data.items() if not str(k).startswith("__")}
            if isinstance(self.input_data, dict) else self.input_data,
            "result": self.results,
            "execution_trace": self.execution_trace or [],
        })
        return summary


class WorkflowVersion(Base):
    """
    Workflow Version - stores historical versions of workflow specifications.

    Vellum-inspired versioning with:
    - Automatic version numbering
    - Named releases (tags)
    - Version comparison and rollback support
    - Active version tracking for deployment
    """
    __tablename__ = 'workflow_versions'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_id = Column(
        UUID(as_uuid=True),
        ForeignKey('workflows.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )

    # Multi-tenancy
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey('tenants.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )

    # Versioning
    version_number = Column(Integer, nullable=False)  # Auto-incrementing per workflow
    version_tag = Column(String(100), nullable=True)  # e.g., "v1.0.0", "production", "staging"

    # Content
    spec = Column(JSONB, nullable=False)  # Frozen workflow specification
    spec_hash = Column(String(64), nullable=True)  # SHA-256 hash for change detection

    # Metadata
    description = Column(Text, nullable=True)  # Version notes/changelog
    created_by = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=True)

    # Deployment status
    is_active = Column(Boolean, default=False)  # Currently deployed version
    is_draft = Column(Boolean, default=True)  # Draft vs published

    # Statistics (captured at version creation)
    node_count = Column(Integer, default=0)
    edge_count = Column(Integer, default=0)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), default=_utcnow)
    published_at = Column(DateTime(timezone=True), nullable=True)  # When is_draft became False

    # Relationships
    workflow = relationship("Workflow", backref="versions")
    tenant = relationship("Tenant")
    creator = relationship("User")
    background_jobs = relationship("BackgroundJob", back_populates="workflow_version")

    def to_dict(self) -> dict:
        """Convert to dictionary for API response."""
        return {
            "id": str(self.id),
            "workflow_id": str(self.workflow_id),
            "version_number": self.version_number,
            "version_tag": self.version_tag,
            "description": self.description,
            "is_active": self.is_active,
            "is_draft": self.is_draft,
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "spec_hash": self.spec_hash,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "created_by": str(self.created_by) if self.created_by else None,
        }

    def to_full_dict(self) -> dict:
        """Convert to full dictionary including spec."""
        result = self.to_dict()
        result["spec"] = self.spec
        return result
