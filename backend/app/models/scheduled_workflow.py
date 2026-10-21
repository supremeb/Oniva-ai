"""
Scheduled Workflow Model
Manages cron-based workflow scheduling with timezone support

Supports:
- Cron expression scheduling
- Interval scheduling (every N minutes/hours/days)
- One-time scheduled executions
- Timezone-aware scheduling
- Execution history tracking
- Enable/disable schedules
"""

from sqlalchemy import Column, String, DateTime, ForeignKey, Text, Integer, Boolean, Enum, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
import uuid
import enum

from app.models import Base


def _utcnow() -> datetime:
    """Timezone-aware UTC: a naive value would be read as server-local time by timestamptz columns."""
    return datetime.now(timezone.utc)


class ScheduleType(str, enum.Enum):
    """Type of schedule"""
    CRON = "cron"           # Cron expression (e.g., "0 9 * * 1-5" for weekdays at 9am)
    INTERVAL = "interval"    # Every N seconds/minutes/hours/days
    ONE_TIME = "one_time"    # Execute once at a specific time


class ScheduleStatus(str, enum.Enum):
    """Status of a scheduled workflow"""
    ACTIVE = "active"       # Schedule is running
    PAUSED = "paused"       # Temporarily paused
    DISABLED = "disabled"   # Permanently disabled
    COMPLETED = "completed" # One-time schedule has executed
    ERROR = "error"         # Schedule has errors


class ScheduledWorkflow(Base):
    """Scheduled workflow configuration with cron/interval support"""
    __tablename__ = 'scheduled_workflows'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_id = Column(UUID(as_uuid=True), ForeignKey('workflows.id', ondelete='CASCADE'), nullable=True, index=True)
    agent_id = Column(UUID(as_uuid=True), ForeignKey('custom_agents.id', ondelete='CASCADE'), nullable=True, index=True)
    workflow_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey('workflow_versions.id', ondelete='SET NULL'),
        nullable=True,
        index=True,
    )
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=False)

    # Multi-tenancy
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey('tenants.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )

    # Schedule configuration
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    schedule_type = Column(Enum(ScheduleType), nullable=False, default=ScheduleType.CRON)

    # Cron expression (for CRON type)
    # Format: "minute hour day month weekday"
    # Examples:
    #   "0 9 * * 1-5" - Weekdays at 9:00 AM
    #   "*/15 * * * *" - Every 15 minutes
    #   "0 0 1 * *" - First day of every month at midnight
    cron_expression = Column(String(100), nullable=True)

    # Interval settings (for INTERVAL type)
    # interval_seconds stores the interval in seconds
    interval_seconds = Column(Integer, nullable=True)

    # One-time execution (for ONE_TIME type)
    scheduled_time = Column(DateTime(timezone=True), nullable=True)

    # Timezone for schedule interpretation (e.g., "America/New_York", "UTC")
    timezone = Column(String(50), default="UTC")

    # Status and control
    status = Column(Enum(ScheduleStatus), default=ScheduleStatus.ACTIVE, index=True)
    is_enabled = Column(Boolean, default=True, index=True)

    # Workflow input variables to use for each execution
    input_variables = Column(JSONB, default=dict)

    # Execution limits and controls
    max_executions = Column(Integer, nullable=True)  # None = unlimited
    execution_count = Column(Integer, default=0)
    max_concurrent = Column(Integer, default=1)  # Max concurrent executions
    timeout_seconds = Column(Integer, default=3600)  # 1 hour default

    # Error handling
    retry_on_failure = Column(Boolean, default=False)
    max_retries = Column(Integer, default=3)
    retry_delay_seconds = Column(Integer, default=60)

    # Last execution tracking
    last_run_at = Column(DateTime(timezone=True), nullable=True)
    last_run_status = Column(String(50), nullable=True)  # success, failed, skipped
    last_error = Column(Text, nullable=True)
    next_run_at = Column(DateTime(timezone=True), nullable=True, index=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), default=_utcnow, onupdate=_utcnow)

    # Relationships
    workflow = relationship("Workflow", backref="schedules")
    workflow_version = relationship("WorkflowVersion")
    user = relationship("User")
    tenant = relationship("Tenant")
    executions = relationship("ScheduledWorkflowExecution", back_populates="schedule", cascade="all, delete-orphan")

    def to_dict(self) -> dict:
        """Convert to dictionary for API response"""
        return {
            "id": str(self.id),
            "workflow_id": str(self.workflow_id),
            "name": self.name,
            "description": self.description,
            "schedule_type": self.schedule_type.value if self.schedule_type else None,
            "cron_expression": self.cron_expression,
            "interval_seconds": self.interval_seconds,
            "scheduled_time": self.scheduled_time.isoformat() if self.scheduled_time else None,
            "timezone": self.timezone,
            "status": self.status.value if self.status else None,
            "is_enabled": self.is_enabled,
            "input_variables": self.input_variables,
            "max_executions": self.max_executions,
            "execution_count": self.execution_count,
            "max_concurrent": self.max_concurrent,
            "timeout_seconds": self.timeout_seconds,
            "retry_on_failure": self.retry_on_failure,
            "max_retries": self.max_retries,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "last_run_status": self.last_run_status,
            "last_error": self.last_error,
            "next_run_at": self.next_run_at.isoformat() if self.next_run_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def get_human_readable_schedule(self) -> str:
        """Return human-readable description of the schedule"""
        if self.schedule_type == ScheduleType.CRON:
            return f"Cron: {self.cron_expression}"
        elif self.schedule_type == ScheduleType.INTERVAL:
            if self.interval_seconds >= 86400:
                days = self.interval_seconds // 86400
                return f"Every {days} day(s)"
            elif self.interval_seconds >= 3600:
                hours = self.interval_seconds // 3600
                return f"Every {hours} hour(s)"
            elif self.interval_seconds >= 60:
                minutes = self.interval_seconds // 60
                return f"Every {minutes} minute(s)"
            else:
                return f"Every {self.interval_seconds} second(s)"
        elif self.schedule_type == ScheduleType.ONE_TIME:
            return f"Once at {self.scheduled_time.isoformat()}" if self.scheduled_time else "One-time (not scheduled)"
        return "Unknown schedule"


class ScheduledWorkflowExecution(Base):
    """Tracks individual executions of a scheduled workflow"""
    __tablename__ = 'scheduled_workflow_executions'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    schedule_id = Column(UUID(as_uuid=True), ForeignKey('scheduled_workflows.id', ondelete='CASCADE'), nullable=False, index=True)
    workflow_execution_id = Column(UUID(as_uuid=True), ForeignKey('workflow_executions.id', ondelete='SET NULL'), nullable=True)

    # Multi-tenancy
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey('tenants.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )

    # Execution details
    scheduled_for = Column(DateTime(timezone=True), nullable=False)  # When it was supposed to run
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(50), default='pending', index=True)  # pending, running, completed, failed, skipped

    # Results
    result_summary = Column(JSONB, default=dict)
    error = Column(Text, nullable=True)
    retry_count = Column(Integer, default=0)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), default=_utcnow)

    # Relationships
    schedule = relationship("ScheduledWorkflow", back_populates="executions")
    workflow_execution = relationship("WorkflowExecution")
    tenant = relationship("Tenant")

    def to_dict(self) -> dict:
        """Convert to dictionary for API response"""
        return {
            "id": str(self.id),
            "schedule_id": str(self.schedule_id),
            "workflow_execution_id": str(self.workflow_execution_id) if self.workflow_execution_id else None,
            "scheduled_for": self.scheduled_for.isoformat() if self.scheduled_for else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "status": self.status,
            "result_summary": self.result_summary,
            "error": self.error,
            "retry_count": self.retry_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
