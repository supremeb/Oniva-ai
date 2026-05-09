"""Knowledge Base model for team-owned document collections.

Part of Frontier-Inspired Platform Simplification (Phase C).
Knowledge bases allow teams to upload documents and make them searchable
via RAG for their agents.
"""
from datetime import datetime
import uuid

from sqlalchemy import (
    Column, DateTime, ForeignKey, String, Text, Integer, func, UniqueConstraint
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import relationship

from app.models import Base


class KnowledgeBase(Base):
    """A collection of documents owned by a team.

    Knowledge bases group related documents for RAG search.
    They support team-level ownership and tenant isolation via RLS.
    """
    __tablename__ = "knowledge_bases"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Tenant this KB belongs to"
    )
    team_id = Column(
        PGUUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Team that owns this KB"
    )
    source_type = Column(
        String(20),
        nullable=False,
        server_default="user",
        comment="Source type: 'user' for user-created, 'project' for project-internal KBs"
    )
    name = Column(
        String(255),
        nullable=False,
        comment="Display name for the knowledge base"
    )
    description = Column(
        Text,
        nullable=True,
        comment="Optional description of KB contents"
    )
    status = Column(
        String(20),
        nullable=False,
        server_default="empty",
        comment="KB status: empty, processing, ready, error"
    )
    document_count = Column(
        Integer,
        nullable=False,
        server_default="0",
        comment="Number of documents in this KB"
    )
    total_chunks = Column(
        Integer,
        nullable=False,
        server_default="0",
        comment="Total chunks across all documents"
    )
    created_by = Column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment="User who created this KB"
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=datetime.utcnow
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )

    # Relationships
    documents = relationship("KnowledgeDocument", back_populates="knowledge_base", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint('tenant_id', 'name', 'source_type', name='uq_knowledge_base_name'),
    )

    def __repr__(self) -> str:
        return f"<KnowledgeBase(id={self.id}, name='{self.name}', status='{self.status}')>"

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "team_id": str(self.team_id) if self.team_id else None,
            "source_type": self.source_type,
            "name": self.name,
            "description": self.description,
            "status": self.status,
            "document_count": self.document_count,
            "total_chunks": self.total_chunks,
            "created_by": str(self.created_by) if self.created_by else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
