"""The approval ledger (Approvals & Policy plan, U2 / KTD3).

One row per approval-like item (tool call, review, question). The engine's ``agent.approvals``
document stays the source of truth for whether a call runs; this table indexes it so the backend
can authorise, list, audit and bind approvals without asking the engine.
"""
import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.models import Base

KINDS = ("tool_call", "review", "question")
STATUSES = ("pending", "deciding", "approved", "denied", "answered", "expired", "cancelled")
OPEN_STATUSES = ("pending", "deciding")


class ToolApproval(Base):
    __tablename__ = "tool_approvals"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind = Column(String(20), nullable=False, server_default="tool_call")
    status = Column(String(20), nullable=False, server_default="pending", default="pending")
    project_id = Column(UUID(as_uuid=True), nullable=True)
    # ROOT conversation (the key); the conversation that made the call may be a child.
    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("engine_conversations.conversation_id", ondelete="CASCADE"),
        nullable=True,
    )
    engine_conversation_id = Column(String(255), nullable=True)
    run_id = Column(String(255), nullable=True)
    tool_call_id = Column(String(255), nullable=True)
    tool_task_id = Column(String(255), nullable=True)
    tool_name = Column(String(255), nullable=True)
    arguments_preview = Column(JSONB, nullable=True)  # scrubbed
    arguments_hash = Column(String(64), nullable=True)
    reason = Column(Text, nullable=True)
    rule = Column(String(30), nullable=False, server_default="requires_approval")
    approver_role = Column(String(20), nullable=False, server_default="member")
    requested_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decision = Column(String(10), nullable=True)  # approve | deny
    note = Column(Text, nullable=True)
    self_approved = Column(Boolean, nullable=False, server_default=text("false"), default=False)
    taint_sources = Column(JSONB, nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    decided_at = Column(DateTime(timezone=True), nullable=True)
    consumed_at = Column(DateTime(timezone=True), nullable=True)
    legacy_ticket_id = Column(UUID(as_uuid=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("kind IN ('tool_call','review','question')", name="ck_tool_approvals_kind"),
        CheckConstraint(
            "status IN ('pending','deciding','approved','denied','answered','expired','cancelled')",
            name="ck_tool_approvals_status",
        ),
        Index(
            "uq_tool_approvals_root_task", "tenant_id", "conversation_id", "tool_task_id",
            unique=True,
            postgresql_where=text("tool_task_id IS NOT NULL"),
            sqlite_where=text("tool_task_id IS NOT NULL"),
        ),
        Index(
            "uq_tool_approvals_run_call", "tenant_id", "run_id", "tool_call_id",
            unique=True,
            postgresql_where=text("run_id IS NOT NULL AND tool_call_id IS NOT NULL"),
            sqlite_where=text("run_id IS NOT NULL AND tool_call_id IS NOT NULL"),
        ),
        Index("ix_tool_approvals_tenant_status", "tenant_id", "status"),
    )
