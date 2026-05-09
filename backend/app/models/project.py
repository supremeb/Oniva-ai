"""Project model for grouping chat sessions, files, and settings into a workspace."""
from datetime import datetime
import uuid

from sqlalchemy import (
    Column, DateTime, ForeignKey, String, Text, Integer, Boolean, func, CheckConstraint
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB
from sqlalchemy.orm import relationship

from app.models import Base


class Project(Base):
    """A workspace that groups related chat sessions, files, instructions, and settings.

    Projects support team sharing via ProjectMember with RBAC.
    File storage is backed by an implicit KnowledgeBase (source_type='project').
    Ownership is tracked exclusively via owner_id (NOT in project_members).
    """
    __tablename__ = "projects"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    owner_id = Column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    instructions = Column(Text, nullable=True)
    default_model = Column(String(255), nullable=True)
    knowledge_base_id = Column(
        PGUUID(as_uuid=True),
        ForeignKey("knowledge_bases.id", ondelete="RESTRICT"),
        nullable=True,
    )
    settings = Column(JSONB, nullable=False, server_default="{}")
    is_archived = Column(Boolean, nullable=False, server_default="false")
    row_version = Column(Integer, nullable=False, server_default="1")
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=datetime.utcnow,
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    # Relationships
    owner = relationship("User", foreign_keys=[owner_id])
    knowledge_base = relationship("KnowledgeBase")
    members = relationship("ProjectMember", back_populates="project", cascade="all, delete-orphan")
    chat_sessions = relationship("ChatSession", back_populates="project")

    __table_args__ = (
        CheckConstraint("length(instructions) <= 10000", name="ck_projects_instructions_length"),
    )

    def __repr__(self) -> str:
        return f"<Project(id={self.id}, name='{self.name}')>"

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "owner_id": str(self.owner_id),
            "name": self.name,
            "description": self.description,
            "instructions": self.instructions,
            "default_model": self.default_model,
            "knowledge_base_id": str(self.knowledge_base_id) if self.knowledge_base_id else None,
            "settings": self.settings,
            "is_archived": self.is_archived,
            "row_version": self.row_version,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
