"""Project memory (evals/context/memory plan, U25 / KTD16).

``memory_files`` holds path-addressed text (``core.md``, topic files); ``memory_bullets`` holds the
structured playbook items with helpful/harmful counters. Both are tenant tables (RLS ENABLE + FORCE)
scoped to a project; deleting the project deletes the rows (erasure follows the project). ``status`` is
``active`` or ``pending``: pending rows (tainted or proposed) are never shown to a model.
"""
import uuid

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.models import Base
from app.models._clock import utc_now

STATUSES = ("active", "pending")
SOURCE_KINDS = ("model", "user", "curator", "system")


def _tenant_fk():
    return Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)


def _project_fk():
    return Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)


class MemoryFile(Base):
    __tablename__ = "memory_files"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = _tenant_fk()
    project_id = _project_fk()
    # Null = project-level memory. Reserved for per-user memory (not shipped yet).
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    path = Column(String(128), nullable=False)
    content = Column(Text, nullable=False, server_default="", default="")
    content_hash = Column(String(64), nullable=False)
    version = Column(Integer, nullable=False, server_default="1", default=1)
    status = Column(String(10), nullable=False, server_default="active", default="active")
    source_kind = Column(String(20), nullable=False, server_default="user", default="user")
    source_conversation_id = Column(UUID(as_uuid=True), nullable=True)
    flags = Column(JSONB, nullable=True)  # instruction-shaped text flags (U27), shown in the UI
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), default=utc_now)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        CheckConstraint("status IN ('active','pending')", name="ck_memory_files_status"),
        CheckConstraint("version >= 1", name="ck_memory_files_version"),
        # One path per (tenant, project, scope). Two partial indexes instead of coalesce(user_id, nil)
        # so the same definition works on SQLite and Postgres.
        Index(
            "uq_memory_files_project_path", "tenant_id", "project_id", "path",
            unique=True,
            postgresql_where=text("user_id IS NULL"),
            sqlite_where=text("user_id IS NULL"),
        ),
        Index(
            "uq_memory_files_user_path", "tenant_id", "project_id", "user_id", "path",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL"),
            sqlite_where=text("user_id IS NOT NULL"),
        ),
        Index("ix_memory_files_tenant_project", "tenant_id", "project_id", "status"),
    )


class MemoryBullet(Base):
    __tablename__ = "memory_bullets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = _tenant_fk()
    project_id = _project_fk()
    bullet_key = Column(String(64), nullable=False)
    section = Column(String(64), nullable=False)
    text = Column(Text, nullable=False)
    helpful = Column(Integer, nullable=False, server_default="0", default=0)
    harmful = Column(Integer, nullable=False, server_default="0", default=0)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(10), nullable=False, server_default="active", default="active")
    source_kind = Column(String(20), nullable=False, server_default="curator", default="curator")
    source_conversation_id = Column(UUID(as_uuid=True), nullable=True)
    source_run_id = Column(String(255), nullable=True)
    flags = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), default=utc_now)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        CheckConstraint("status IN ('active','pending')", name="ck_memory_bullets_status"),
        CheckConstraint("helpful >= 0 AND harmful >= 0", name="ck_memory_bullets_counters"),
        Index("uq_memory_bullets_key", "tenant_id", "project_id", "bullet_key", unique=True),
        Index("ix_memory_bullets_tenant_project", "tenant_id", "project_id", "status"),
    )
