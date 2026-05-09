"""RunEvent model for unified event observability across all agent runs."""
from sqlalchemy import Column, String, DateTime, Integer, Index, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from datetime import datetime, timezone
import uuid

from app.models import Base


class RunEvent(Base):
    """
    Unified observability envelope for all events emitted during agent/workflow runs.

    Captures a stream of structured events keyed by run_id, enabling per-run
    replay, token accounting, and structured tracing without coupling to
    SSE queues or execution-specific trace formats.

    Valid event_type values:
        run_started, run_completed, run_failed,
        tool_call_started, tool_call_completed,
        content_delta, thinking_delta,
        worker_activity, phase_transition
    """
    __tablename__ = 'run_events'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Run context
    run_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    session_id = Column(UUID(as_uuid=True), nullable=True, index=True)

    # Multi-tenancy
    tenant_id = Column(UUID(as_uuid=True), nullable=False, index=True)

    # Event metadata
    event_type = Column(String(100), nullable=False)
    agent_name = Column(String(255), nullable=True)

    # Timestamp (application-set so it is authoritative for ordering)
    timestamp = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )

    # Token accounting (populated for LLM-producing events)
    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)

    # Arbitrary event payload (tool args, delta text, phase details, etc.)
    payload = Column(JSONB, nullable=True)

    __table_args__ = (
        # Common query: all events for a run in order
        Index('ix_run_events_run_id_timestamp', 'run_id', 'timestamp'),
        # Tenant-scoped queries
        Index('ix_run_events_tenant_timestamp', 'tenant_id', 'timestamp'),
    )

    def __repr__(self):
        return f"<RunEvent(id='{self.id}', run_id='{self.run_id}', event_type='{self.event_type}')>"
