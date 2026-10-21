"""Episodic Memory model for cross-session agent learning.

Part of Enhanced Agent Memory System feature.
Stores agent decisions with vector embeddings for similarity-based retrieval.
Supports hybrid search (semantic + BM25), importance scoring, and archival.
"""
from datetime import datetime
from typing import TYPE_CHECKING
import uuid

from sqlalchemy import (
    Column, DateTime, ForeignKey, String, Text, Integer, Float, Boolean, func, CheckConstraint, ARRAY
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB, TSVECTOR
from sqlalchemy.orm import relationship
from pgvector.sqlalchemy import Vector

from app.models import Base
from app.models._clock import utc_now

if TYPE_CHECKING:
    pass


class EpisodicMemory(Base):
    """
    Episodic memory entry for agent decisions.

    Stores past decisions with context, rationale, and embeddings for
    similarity-based retrieval. Enables agents to learn from past decisions
    and provide consistent recommendations.

    Security:
    - RLS enforces user-level isolation (tenant_id + user_id)
    - Default retention: 2 years, max 7 years for compliance
    - PII should be masked before storage
    """
    __tablename__ = "episodic_memories"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Multi-tenancy with user-level isolation
    tenant_id = Column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Tenant this memory belongs to"
    )
    user_id = Column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="User who created this memory (nullable: shared memories survive user deletion)"
    )
    team_id = Column(
        PGUUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="SET NULL"),
        nullable=True,
        comment="Team scope for future team-level visibility"
    )

    # Session reference (links to filesystem session)
    session_id = Column(
        String(100),
        nullable=True,
        comment="Session ID for linking to filesystem session"
    )

    # Agent context
    agent_name = Column(
        String(100),
        nullable=False,
        comment="Name of the agent that made this decision"
    )
    action_type = Column(
        String(50),
        nullable=False,
        comment="Type of action: analysis, compliance_check, recommendation, research, chat, other"
    )

    # Combined context + decision for single embedding
    context_decision = Column(
        Text,
        nullable=False,
        comment="Combined context and decision text for embedding"
    )

    # Single embedding for similarity search (1536 dimensions for OpenAI)
    embedding = Column(
        Vector(1536),
        nullable=True,
        comment="Vector embedding for similarity search"
    )

    # Additional context
    rationale = Column(
        Text,
        nullable=True,
        comment="Reasoning behind the decision"
    )
    outcome = Column(
        JSONB,
        nullable=True,
        comment="Outcome/feedback for learning loop"
    )
    entities = Column(
        JSONB,
        server_default='[]',
        comment="Entities involved (tickers, companies, etc.)"
    )

    # Timestamps
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=utc_now
    )

    # Retention (default 2 years, max 7 years)
    valid_until = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="Memory expires after this date"
    )

    # Phase 1: Hybrid Search & Importance Scoring
    importance = Column(
        Float,
        nullable=False,
        server_default='0.5',
        comment="Memory importance score (0.0-1.0). Higher = more relevant in searches."
    )
    archived = Column(
        Boolean,
        nullable=False,
        server_default='false',
        comment="Archived memories excluded from default searches."
    )
    last_accessed = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="Last time this memory was retrieved."
    )
    access_count = Column(
        Integer,
        nullable=False,
        server_default='0',
        comment="Number of times this memory was retrieved."
    )
    search_vector = Column(
        TSVECTOR,
        nullable=True,
        comment="Full-text search vector for BM25."
    )

    # Phase 4: Memory UI Fields
    memory_type = Column(
        String(50),
        nullable=False,
        server_default='decision',
        comment="Memory type: decision, fact, preference, conversation"
    )
    feedback = Column(
        String(20),
        nullable=True,
        comment="User feedback: helpful, not_helpful, null"
    )
    feedback_at = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="When feedback was provided"
    )
    visibility = Column(
        String(20),
        nullable=False,
        server_default='private',
        comment="Visibility: private (user only), shared (tenant-wide)"
    )
    source_type = Column(
        String(50),
        nullable=True,
        comment="Source: chat, workflow, api, import"
    )
    source_id = Column(
        PGUUID(as_uuid=True),
        nullable=True,
        comment="Source reference (chat session ID, workflow execution ID)"
    )
    tags = Column(
        ARRAY(String(50)),
        nullable=False,
        server_default='{}',
        comment="User-defined tags for organization"
    )

    # Relationships
    tenant = relationship("Tenant", back_populates="episodic_memories")
    user = relationship("User", back_populates="episodic_memories")

    # Constraints
    __table_args__ = (
        CheckConstraint(
            "action_type IN ('analysis', 'compliance_check', 'recommendation', 'research', 'chat', 'other')",
            name='ck_episodic_memories_action_type'
        ),
        CheckConstraint(
            "visibility IN ('private', 'shared')",
            name='ck_episodic_memories_visibility'
        ),
    )

    def __repr__(self) -> str:
        return f"<EpisodicMemory(id={self.id}, agent={self.agent_name}, action={self.action_type})>"

    def to_dict(self) -> dict:
        """Convert memory to dictionary for API responses."""
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "user_id": str(self.user_id) if self.user_id else None,
            "session_id": self.session_id,
            "agent_name": self.agent_name,
            "action_type": self.action_type,
            "context_decision": self.context_decision,
            "rationale": self.rationale,
            "outcome": self.outcome,
            "entities": self.entities or [],
            "importance": self.importance,
            "archived": self.archived,
            "access_count": self.access_count,
            "last_accessed": self.last_accessed.isoformat() if self.last_accessed else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "valid_until": self.valid_until.isoformat() if self.valid_until else None,
            # Phase 4: UI fields
            "memory_type": self.memory_type,
            "feedback": self.feedback,
            "feedback_at": self.feedback_at.isoformat() if self.feedback_at else None,
            "visibility": self.visibility,
            "source_type": self.source_type,
            "source_id": str(self.source_id) if self.source_id else None,
            "tags": self.tags or [],
        }
