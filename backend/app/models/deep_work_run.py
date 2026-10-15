"""Deep Work run state on the engine path (deep-work-on-engine plan, U3).

One row per engine Deep Work session, keyed by the ROOT engine conversation. Workers run in child
conversations but save through the gateway, which binds them to the root, so all state lands here.
"""
import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.models import Base


class DeepWorkRun(Base):
    __tablename__ = "deep_work_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id = Column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("engine_conversations.conversation_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    # The originating chat (legacy chat session id, or the engine chat conversation id). No FK: it can be
    # either table. Lets a refreshed chat find its run (by-conversation full-state).
    chat_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    query = Column(Text, nullable=False)
    enriched_query = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, server_default="running")  # running | completed | failed | cancelled
    phase = Column(String(20), nullable=False, server_default="clarify")  # forward-only, see deep_work_state.PHASES
    round = Column(Integer, nullable=False, server_default="0", default=0)  # monotonic
    plan = Column(JSONB, nullable=True)
    rounds = Column(JSONB, nullable=False, server_default=text("'[]'"), default=list)
    reflections = Column(JSONB, nullable=False, server_default=text("'[]'"), default=list)
    artifacts = Column(JSONB, nullable=False, server_default=text("'[]'"), default=list)
    job_id = Column(UUID(as_uuid=True), ForeignKey("background_jobs.id", ondelete="SET NULL"), nullable=True)
    is_engine = Column(Boolean, nullable=False, server_default="true", default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
