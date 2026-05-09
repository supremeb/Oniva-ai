"""Knowledge Document model for documents within a knowledge base.

Part of Frontier-Inspired Platform Simplification (Phase C).
Tracks uploaded documents with versioning (re-upload same filename = new version).
"""
from datetime import datetime
import uuid

from sqlalchemy import (
    Column, DateTime, ForeignKey, String, Text, Integer, BigInteger, func, Index
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import relationship

from app.models import Base


class KnowledgeDocument(Base):
    """A document within a knowledge base, with versioning.

    Documents are parsed, chunked, and embedded for vector search.
    Versioning is per-filename within a KB — re-uploading the same
    filename creates a new version.
    """
    __tablename__ = "knowledge_documents"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Tenant this document belongs to"
    )
    knowledge_base_id = Column(
        PGUUID(as_uuid=True),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="KB this document belongs to"
    )
    filename = Column(
        String(500),
        nullable=False,
        comment="Original filename"
    )
    file_type = Column(
        String(20),
        nullable=False,
        comment="File type: pdf, docx, txt, csv, md"
    )
    file_size_bytes = Column(
        BigInteger,
        nullable=False,
        comment="File size in bytes"
    )
    version = Column(
        Integer,
        nullable=False,
        server_default="1",
        comment="Document version (incremented on re-upload of same filename)"
    )
    status = Column(
        String(20),
        nullable=False,
        server_default="uploading",
        comment="Status: uploading, processing, ready, error"
    )
    error_message = Column(
        Text,
        nullable=True,
        comment="Error details if processing failed"
    )
    chunk_count = Column(
        Integer,
        nullable=False,
        server_default="0",
        comment="Number of chunks generated from this document"
    )
    storage_path = Column(
        String(1000),
        nullable=False,
        comment="S3 key or local filesystem path"
    )
    uploaded_by = Column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment="User who uploaded this document"
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=datetime.utcnow
    )

    # Relationships
    knowledge_base = relationship("KnowledgeBase", back_populates="documents")
    chunks = relationship("KnowledgeChunk", back_populates="document", cascade="all, delete-orphan")

    __table_args__ = (
        Index('ix_knowledge_doc_kb_filename', 'knowledge_base_id', 'filename'),
    )

    def __repr__(self) -> str:
        return f"<KnowledgeDocument(id={self.id}, filename='{self.filename}', v{self.version})>"

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "knowledge_base_id": str(self.knowledge_base_id),
            "filename": self.filename,
            "file_type": self.file_type,
            "file_size_bytes": self.file_size_bytes,
            "version": self.version,
            "status": self.status,
            "error_message": self.error_message,
            "chunk_count": self.chunk_count,
            "storage_path": self.storage_path,
            "uploaded_by": str(self.uploaded_by) if self.uploaded_by else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
