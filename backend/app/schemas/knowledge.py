"""Schemas for Knowledge Management API (Phase C)."""
from typing import Optional

from pydantic import BaseModel, Field


# ============================================================================
# Knowledge Base Schemas
# ============================================================================

class KnowledgeBaseCreate(BaseModel):
    """Request to create a knowledge base."""
    name: str = Field(..., min_length=1, max_length=255, description="KB display name")
    description: Optional[str] = Field(None, max_length=2000, description="Optional description")
    team_id: Optional[str] = Field(None, description="Team UUID that owns this KB")


class KnowledgeBaseUpdate(BaseModel):
    """Request to update a knowledge base."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=2000)
    team_id: Optional[str] = Field(None, description="Transfer ownership to team")


class KnowledgeBaseResponse(BaseModel):
    """Knowledge base in API responses."""
    id: str
    tenant_id: str
    team_id: Optional[str] = None
    name: str
    description: Optional[str] = None
    status: str
    document_count: int
    total_chunks: int
    created_by: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class KnowledgeBaseListResponse(BaseModel):
    """List of knowledge bases."""
    knowledge_bases: list[KnowledgeBaseResponse]
    count: int


# ============================================================================
# Knowledge Document Schemas
# ============================================================================

class KnowledgeDocumentResponse(BaseModel):
    """Document in API responses."""
    id: str
    knowledge_base_id: str
    filename: str
    file_type: str
    file_size_bytes: int
    version: int
    status: str
    error_message: Optional[str] = None
    chunk_count: int
    uploaded_by: Optional[str] = None
    created_at: Optional[str] = None


class KnowledgeDocumentListResponse(BaseModel):
    """List of documents."""
    documents: list[KnowledgeDocumentResponse]
    count: int


# ============================================================================
# Knowledge Search Schemas
# ============================================================================

class KnowledgeSearchRequest(BaseModel):
    """Search query against a knowledge base."""
    query: str = Field(..., min_length=1, max_length=2000, description="Search query text")
    limit: int = Field(default=5, ge=1, le=20, description="Max results to return")


class KnowledgeSearchResult(BaseModel):
    """A single search result chunk."""
    id: str
    content: str
    chunk_index: int
    metadata: dict = Field(default_factory=dict)
    document_id: str
    knowledge_base_id: Optional[str] = None
    score: float = Field(description="Similarity score (0-1)")


class KnowledgeSearchResponse(BaseModel):
    """Search results."""
    results: list[KnowledgeSearchResult]
    count: int
    query: str
