"""Document model with multi-tenancy support for RAG."""
from sqlalchemy import Column, String, DateTime, ForeignKey, Text, Integer, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid
from pgvector.sqlalchemy import Vector

from app.models import Base


class Document(Base):
    """
    Stores documents for RAG (Retrieval-Augmented Generation).
    Uses pgvector for semantic search.
    """
    __tablename__ = 'documents'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'))

    # Multi-tenancy
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey('tenants.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )

    # Document info
    title = Column(String(255))
    content = Column(Text, nullable=False)
    source = Column(String(255), nullable=True)  # URL, file name, etc.

    # Vector embedding for semantic search
    embedding = Column(Vector(1536))  # OpenAI text-embedding-3-small dimension

    # Metadata
    doc_metadata = Column(JSONB, default=dict)  # Tags, categories, etc.
    chunk_index = Column(Integer, nullable=True)  # For chunked documents
    parent_document_id = Column(UUID(as_uuid=True), nullable=True)  # Link to original doc

    created_at = Column(DateTime(timezone=True), server_default=func.now(), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="documents")
    tenant = relationship("Tenant")
