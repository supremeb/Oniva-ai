"""
Knowledge Base API endpoints for Oniva.

Lists distinct knowledge bases derived from document metadata (doc_metadata->>'kb_id').
No dedicated knowledge_bases table - KB info is aggregated from documents.

Endpoints:
- GET /api/v1/knowledge-bases - List all knowledge bases
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.logging import get_logger

logger = get_logger(__name__)


router = APIRouter(prefix="/knowledge-bases", tags=["knowledge-bases"])


# ============================================================================
# Request/Response Models
# ============================================================================


class KnowledgeBase(BaseModel):
    """Knowledge base metadata derived from document metadata."""

    id: str = Field(..., description="Unique knowledge base identifier")
    name: str = Field(..., description="Display name for the knowledge base")
    document_count: int = Field(
        ..., alias="documentCount", description="Number of documents in this KB"
    )

    model_config = {"populate_by_name": True}


class KnowledgeBaseListResponse(BaseModel):
    """Response for listing knowledge bases."""

    knowledge_bases: list[KnowledgeBase]
    count: int


# ============================================================================
# Endpoints
# ============================================================================


@router.get("", response_model=KnowledgeBaseListResponse)
async def list_knowledge_bases(
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
) -> KnowledgeBaseListResponse:
    """
    List all distinct knowledge bases from document metadata.

    Knowledge bases are derived from document metadata (doc_metadata->>'kb_id').
    No dedicated table is required - this aggregates from existing documents.

    Returns:
        List of knowledge bases with their document counts.
    """
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Tenant context required")

    try:
        # NOTE: Uses doc_metadata (not metadata) - verified against Document model
        # This query benefits from idx_documents_kb_id index for performance
        query = text("""
            SELECT
                doc_metadata->>'kb_id' as id,
                COALESCE(doc_metadata->>'kb_name', doc_metadata->>'kb_id') as name,
                COUNT(*) as document_count
            FROM documents
            WHERE doc_metadata->>'kb_id' IS NOT NULL
            GROUP BY doc_metadata->>'kb_id', doc_metadata->>'kb_name'
            ORDER BY name
        """)
        result = await db.execute(query)
        rows = result.fetchall()

        knowledge_bases = [
            KnowledgeBase(
                id=row.id,
                name=row.name or row.id,
                documentCount=row.document_count,
            )
            for row in rows
        ]

        return KnowledgeBaseListResponse(
            knowledge_bases=knowledge_bases,
            count=len(knowledge_bases),
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to list knowledge bases: {str(e)}",
        )
