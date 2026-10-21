"""Agent Feedback model for inline user feedback on agent responses.

Part of Frontier-Inspired Platform Simplification (Phase E).
Stores thumbs up/down ratings with optional text feedback.
"""
from datetime import datetime
import uuid

from sqlalchemy import (
    Column, DateTime, ForeignKey, String, Text, Integer, func, Index
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from app.models import Base
from app.models._clock import utc_now


class AgentFeedback(Base):
    """Feedback on an agent response.

    Captures user sentiment (positive/negative) and optional text
    feedback for agent responses in both Agent Studio and main chat.
    """
    __tablename__ = "agent_feedback"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_id = Column(
        PGUUID(as_uuid=True),
        ForeignKey("custom_agents.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    user_id = Column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    session_id = Column(
        String(255),
        nullable=True,
        comment="Chat session reference"
    )
    message_index = Column(
        Integer,
        nullable=True,
        comment="Position in conversation"
    )
    rating = Column(
        String(10),
        nullable=False,
        comment="positive or negative"
    )
    feedback_text = Column(
        Text,
        nullable=True,
        comment="Optional text for negative feedback"
    )
    agent_response_summary = Column(
        Text,
        nullable=True,
        comment="Truncated response for context"
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=utc_now,
    )

    __table_args__ = (
        Index('ix_agent_feedback_agent_created', 'agent_id', 'created_at'),
    )

    def __repr__(self) -> str:
        return f"<AgentFeedback(id={self.id}, rating='{self.rating}')>"

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "agent_id": str(self.agent_id) if self.agent_id else None,
            "user_id": str(self.user_id) if self.user_id else None,
            "session_id": self.session_id,
            "message_index": self.message_index,
            "rating": self.rating,
            "feedback_text": self.feedback_text,
            "agent_response_summary": self.agent_response_summary,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
