"""Persistence for the engine interfaces (plan 003, U2).

* ``ToolInvocation``   - outcome store keyed by the engine's ``Idempotency-Key`` (tenant RLS).
* ``EngineFence``      - per-session fence high-water mark. No ``tenant_id``: it is reachable only
  through the system role (``oniva_app`` is denied by grants and an explicit deny policy).
* ``EngineConversation`` - binds a conversation to tenant, project, user and engine session (tenant RLS).
"""
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.models import Base


class ToolInvocation(Base):
    __tablename__ = "tool_invocations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    idempotency_key = Column(String(255), nullable=False, unique=True)
    session_id = Column(String(255), nullable=False)
    tool = Column(String(100), nullable=False)
    args_hash = Column(String(64), nullable=False)
    status = Column(String(20), nullable=False, server_default="in_flight")  # in_flight | completed
    outcome = Column(JSONB, nullable=True)
    fence_token = Column(BigInteger, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_tool_invocations_session_status_started", "session_id", "status", "started_at"),
    )


class EngineFence(Base):
    __tablename__ = "engine_fences"

    session_id = Column(String(255), primary_key=True)
    high_water = Column(BigInteger, nullable=False, server_default="0")
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class EngineConversation(Base):
    __tablename__ = "engine_conversations"

    conversation_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id = Column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    session_id = Column(String(255), nullable=False)
    # Chat selections (skills, connectors, knowledge bases, web search) recorded at creation (plan 004, U3).
    selections = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    # Listing metadata (chat-on-engine U10): title is set by the backend at the first completed
    # turn; updated_at orders the union list; legacy_session_id links a continued legacy session.
    title = Column(String(255), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    # Summary of the continued legacy session, kept so selection rebuilds re-include it in the prompt.
    continuation_summary = Column(Text, nullable=True)
    legacy_session_id = Column(
        UUID(as_uuid=True), ForeignKey("chat_sessions.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    # Sidebar pin, and archive ("delete" hides the row from listings; engine data is kept).
    pinned = Column(Boolean, nullable=False, server_default="false", default=False)
    archived_at = Column(DateTime(timezone=True), nullable=True)
