"""Background Job model for tracking long-running workflow executions."""
from sqlalchemy import Column, String, DateTime, ForeignKey, Index, Integer, Text, func, CheckConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from enum import Enum
import uuid

from app.models import Base


class JobStatus(str, Enum):
    """Background job status enum"""
    PENDING = 'pending'
    RUNNING = 'running'
    COMPLETED = 'completed'
    FAILED = 'failed'
    CANCELLED = 'cancelled'


class JobType(str, Enum):
    """Background job type enum"""
    WORKFLOW_EXECUTION = 'workflow_execution'
    AGENT_CHAT = 'agent_chat'
    BATCH_PROCESSING = 'batch_processing'
    DEEP_WORK = 'deep_work'
    DOCUMENT_PROCESSING = 'document_processing'
    SESSION_CLEANUP = 'session_cleanup'
    BACKGROUND_AGENT = 'background_agent'  # Phase 3: Autonomous agent execution


class JobTrigger(str, Enum):
    """How the job was triggered"""
    MANUAL = 'manual'      # User clicked "Run" in UI
    API = 'api'            # Triggered via API call
    SCHEDULED = 'scheduled'  # Triggered by scheduler/cron
    WEBHOOK = 'webhook'    # Phase 3: Triggered by inbound webhook


class BackgroundJob(Base):
    """
    Tracks background job executions for workflows and agents.
    Supports real-time progress updates via SSE streaming.
    """
    __tablename__ = 'background_jobs'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Multi-tenancy
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey('tenants.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )

    # Owner
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey('users.id', ondelete='CASCADE'),
        nullable=False
    )

    # Context - workflow or agent
    workflow_id = Column(
        UUID(as_uuid=True),
        ForeignKey('workflows.id', ondelete='SET NULL'),
        nullable=True
    )
    agent_id = Column(
        UUID(as_uuid=True),
        ForeignKey('custom_agents.id', ondelete='SET NULL'),
        nullable=True
    )

    # Version tracking - which version of workflow was executed
    # NULL means: legacy execution before versioning, or version was deleted
    workflow_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey('workflow_versions.id', ondelete='SET NULL'),
        nullable=True,
        index=True
    )

    # Session context (for deep research and other session-bound jobs)
    session_id = Column(String(255), nullable=True, index=True)

    # Conversation that spawned this job (Deep Work lookup by chat)
    # No FK to chat_sessions — conversations may be deleted independently
    conversation_id = Column(UUID(as_uuid=True), nullable=True)

    # Job metadata
    job_type = Column(String(50), nullable=False, default=JobType.WORKFLOW_EXECUTION.value)
    status = Column(String(20), nullable=False, default=JobStatus.PENDING.value)
    trigger = Column(String(20), nullable=False, default=JobTrigger.MANUAL.value)  # manual, api, scheduled, webhook
    progress = Column(Integer, nullable=False, default=0)  # 0-100

    # How this job was triggered (Phase 3: Autonomous Execution)
    # {"type": "scheduler", "schedule_id": "..."} or {"type": "webhook", "webhook_id": "..."}
    triggered_by = Column(JSONB, nullable=True, comment="Trigger source details for audit")

    # Input and output
    input_data = Column(JSONB, nullable=True)  # Input parameters
    result = Column(JSONB, nullable=True)  # Final result
    error = Column(Text, nullable=True)  # Error message if failed

    # Execution trace for debugging
    # [{step_id, node_id, node_type, status, input, output, started_at, completed_at, duration_ms, reasoning}]
    execution_trace = Column(JSONB, nullable=False, default=list)

    # Timestamps
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), default=lambda: datetime.now(timezone.utc))

    # Relationships
    tenant = relationship("Tenant")
    user = relationship("User")
    workflow = relationship("Workflow", back_populates="background_jobs")
    agent = relationship("CustomAgent", back_populates="background_jobs")
    workflow_version = relationship("WorkflowVersion", back_populates="background_jobs")

    # Table constraints
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'cancelled')",
            name='ck_background_jobs_status'
        ),
        Index('ix_bgj_conversation_created', 'conversation_id', 'created_at'),
    )

    def __repr__(self):
        return f"<BackgroundJob(id='{self.id}', status='{self.status}')>"

    def to_dict(self):
        """Convert to dictionary for API responses"""
        return {
            'id': str(self.id),
            'tenant_id': str(self.tenant_id),
            'user_id': str(self.user_id),
            'workflow_id': str(self.workflow_id) if self.workflow_id else None,
            'workflow_version_id': str(self.workflow_version_id) if self.workflow_version_id else None,
            'agent_id': str(self.agent_id) if self.agent_id else None,
            'session_id': self.session_id,
            'conversation_id': str(self.conversation_id) if self.conversation_id else None,
            'job_type': self.job_type,
            'status': self.status,
            'trigger': self.trigger,
            'progress': self.progress,
            'input_data': self.input_data,
            'result': self.result,
            'error': self.error,
            'execution_trace': self.execution_trace,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'duration_ms': self.duration_ms,
            'total_tokens': self.total_tokens,
            'nodes_executed': self.nodes_executed,
        }

    @property
    def duration_ms(self) -> int | None:
        """Calculate job duration in milliseconds"""
        if self.started_at and self.completed_at:
            delta = self.completed_at - self.started_at
            return int(delta.total_seconds() * 1000)
        return None

    @property
    def total_tokens(self) -> int:
        """Sum token usage across all nodes in execution trace."""
        if not self.execution_trace:
            return 0
        return sum(
            step.get('token_usage', {}).get('total', 0)
            for step in self.execution_trace
            if isinstance(step, dict)
        )

    @property
    def nodes_executed(self) -> int:
        """Count the number of executed nodes in the trace."""
        if not self.execution_trace:
            return 0
        return len(self.execution_trace)

    @property
    def is_running(self) -> bool:
        """Check if job is currently running"""
        return self.status == JobStatus.RUNNING.value

    @property
    def is_completed(self) -> bool:
        """Check if job has finished (success or failure)"""
        return self.status in [JobStatus.COMPLETED.value, JobStatus.FAILED.value, JobStatus.CANCELLED.value]

    def start(self):
        """Mark job as started"""
        self.status = JobStatus.RUNNING.value
        self.started_at = datetime.now(timezone.utc)

    def complete(self, result: dict = None):
        """Mark job as completed"""
        self.status = JobStatus.COMPLETED.value
        self.completed_at = datetime.now(timezone.utc)
        self.progress = 100
        if result:
            self.result = result

    def fail(self, error: str):
        """Mark job as failed"""
        self.status = JobStatus.FAILED.value
        self.completed_at = datetime.now(timezone.utc)
        self.error = error

    def cancel(self):
        """Mark job as cancelled"""
        self.status = JobStatus.CANCELLED.value
        self.completed_at = datetime.now(timezone.utc)

    def update_progress(self, progress: int):
        """Update job progress (0-100)"""
        self.progress = max(0, min(100, progress))

    def add_trace_step(self, step: dict):
        """Add a step to the execution trace"""
        if self.execution_trace is None:
            self.execution_trace = []
        self.execution_trace = [*self.execution_trace, step]

    def get_trace_step(self, step_id: str) -> dict | None:
        """Get a specific trace step by ID"""
        if not self.execution_trace:
            return None
        for step in self.execution_trace:
            if step.get('step_id') == step_id:
                return step
        return None
