"""Agent Learning model for error-driven knowledge capture."""
import logging
import uuid

from sqlalchemy import (
    Column, DateTime, ForeignKey, String, Text, func
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import relationship
from pgvector.sqlalchemy import Vector

from app.models import Base

logger = logging.getLogger(__name__)


class AgentLearning(Base):
    """
    Stores learnings captured from agent error-recovery cycles.

    Learnings are auto-captured when agents recover from errors
    and retrieved via semantic similarity before future executions.

    Security:
    - RLS enforces tenant-level isolation
    - PII scrubbed before storage
    - Content hash prevents duplicates
    """
    __tablename__ = "agent_learnings"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Multi-tenancy
    tenant_id = Column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Agent scope: NULL = tenant-wide shared learning
    agent_id = Column(
        PGUUID(as_uuid=True),
        ForeignKey("custom_agents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Learning content
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)
    pattern_type = Column(
        String(100), nullable=True,
        comment="data_quality, api_error, calculation, domain_insight, general"
    )

    # Source tracking
    source = Column(
        String(50), nullable=False,
        comment="error_capture, user_feedback, manual"
    )
    source_session_id = Column(PGUUID(as_uuid=True), nullable=True)
    source_error = Column(Text, nullable=True, comment="Sanitized original error")

    # Semantic search
    embedding = Column(Vector(1536), nullable=True)

    # Status: active or archived
    status = Column(String(20), nullable=False, server_default="active")

    # Deduplication
    content_hash = Column(
        String(64), unique=True, nullable=False,
        comment="SHA256 of normalized error signature for dedup"
    )

    # Timestamps
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_used_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    tenant = relationship("Tenant")
    agent = relationship("CustomAgent", foreign_keys=[agent_id])
