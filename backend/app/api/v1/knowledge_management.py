"""Knowledge Management API endpoints (Phase C).

Provides CRUD for knowledge bases, document upload/management,
and search preview functionality.
"""
import asyncio
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db, get_db_for_tenant
from app.core.logging import get_logger
from app.models.knowledge_base import KnowledgeBase
from app.models.knowledge_document import KnowledgeDocument
from app.services.knowledge_base import KnowledgeBaseService
from app.services.document_processor import ALLOWED_FILE_TYPES, DocumentProcessorService, _sanitize_filename
from app.schemas.knowledge import (
    KnowledgeBaseCreate,
    KnowledgeBaseUpdate,
    KnowledgeBaseResponse,
    KnowledgeBaseListResponse,
    KnowledgeDocumentResponse,
    KnowledgeDocumentListResponse,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
    KnowledgeSearchResult,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/knowledge/bases", tags=["knowledge-management"])


def _kb_to_response(kb: KnowledgeBase) -> KnowledgeBaseResponse:
    d = kb.to_dict()
    return KnowledgeBaseResponse(**d)


def _doc_to_response(doc: KnowledgeDocument) -> KnowledgeDocumentResponse:
    d = doc.to_dict()
    return KnowledgeDocumentResponse(**d)


# ============================================================================
# Knowledge Base CRUD
# ============================================================================

@router.post("", response_model=KnowledgeBaseResponse, status_code=201)
async def create_knowledge_base(
    data: KnowledgeBaseCreate,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Create a new knowledge base."""
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Tenant context required")

    user_id = getattr(request.state, "audit_context", {}).get("user_id")

    service = KnowledgeBaseService(db)
    try:
        kb = await service.create_kb(
            name=data.name,
            tenant_id=UUID(str(tenant_id)),
            description=data.description,
            team_id=UUID(data.team_id) if data.team_id else None,
            created_by=UUID(str(user_id)) if user_id else None,
        )
        return _kb_to_response(kb)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("", response_model=KnowledgeBaseListResponse)
async def list_knowledge_bases(
    request: Request,
    team_id: Optional[str] = Query(None, description="Filter by team"),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """List all knowledge bases for the tenant."""
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Tenant context required")

    service = KnowledgeBaseService(db)
    kbs = await service.list_kbs(
        tenant_id=UUID(str(tenant_id)),
        team_id=UUID(team_id) if team_id else None,
    )
    return KnowledgeBaseListResponse(
        knowledge_bases=[_kb_to_response(kb) for kb in kbs],
        count=len(kbs),
    )


@router.get("/{kb_id}", response_model=KnowledgeBaseResponse)
async def get_knowledge_base(
    kb_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Get a knowledge base by ID."""
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Tenant context required")

    service = KnowledgeBaseService(db)
    kb = await service.get_kb(kb_id, UUID(str(tenant_id)))
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    return _kb_to_response(kb)


@router.patch("/{kb_id}", response_model=KnowledgeBaseResponse)
async def update_knowledge_base(
    kb_id: UUID,
    data: KnowledgeBaseUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Update a knowledge base."""
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Tenant context required")

    service = KnowledgeBaseService(db)
    kb = await service.update_kb(
        kb_id=kb_id,
        tenant_id=UUID(str(tenant_id)),
        name=data.name,
        description=data.description,
        team_id=UUID(data.team_id) if data.team_id else None,
    )
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    return _kb_to_response(kb)


@router.delete("/{kb_id}", status_code=204)
async def delete_knowledge_base(
    kb_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Delete a knowledge base and all its documents/chunks."""
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Tenant context required")

    service = KnowledgeBaseService(db)
    deleted = await service.delete_kb(kb_id, UUID(str(tenant_id)))
    if not deleted:
        raise HTTPException(status_code=404, detail="Knowledge base not found")


# ============================================================================
# Document Management
# ============================================================================

@router.post("/{kb_id}/documents", response_model=KnowledgeDocumentResponse, status_code=201)
async def upload_document(
    kb_id: UUID,
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Upload a document to a knowledge base.

    Supports PDF, DOCX, TXT, CSV, and Markdown files.
    Re-uploading the same filename creates a new version.
    """
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Tenant context required")

    user_id = getattr(request.state, "audit_context", {}).get("user_id")

    # Verify KB exists
    kb_service = KnowledgeBaseService(db)
    kb = await kb_service.get_kb(kb_id, UUID(str(tenant_id)))
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    # Unsupported type is a 415 (same as project file upload), before reading the body
    safe_name = _sanitize_filename(file.filename or "untitled")
    ext = safe_name.rsplit(".", 1)[-1].lower() if "." in safe_name else ""
    if ext not in ALLOWED_FILE_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type '.{ext}'. Allowed: {', '.join(sorted(ALLOWED_FILE_TYPES))}",
        )

    # Read file content
    content = await file.read()

    processor = DocumentProcessorService(db)
    try:
        doc = await processor.upload_document(
            kb_id=kb_id,
            tenant_id=UUID(str(tenant_id)),
            filename=file.filename or "unknown",
            file_content=content,
            file_size=len(content),
            user_id=UUID(str(user_id)) if user_id else None,
        )
        # Build the response BEFORE committing: the tenant GUC is transaction-local (SET LOCAL),
        # so a refresh after commit runs without tenant context and RLS hides the new row.
        await db.refresh(doc)
        response = _doc_to_response(doc)
        await db.commit()

        # Dispatch processing as a background task with its own DB session
        doc_id = doc.id
        tid = UUID(str(tenant_id))
        uid = UUID(str(user_id)) if user_id else None

        async def _process_in_background() -> None:
            try:
                async with get_db_for_tenant(str(tid)) as bg_db:
                    from app.models.background_job import BackgroundJob, JobType, JobStatus
                    job = BackgroundJob(
                        tenant_id=tid,
                        user_id=uid or tid,
                        job_type=JobType.DOCUMENT_PROCESSING.value,
                        status=JobStatus.RUNNING.value,
                        input_data={"document_id": str(doc_id), "kb_id": str(kb_id)},
                    )
                    job.start()
                    bg_db.add(job)
                    await bg_db.flush()

                    bg_processor = DocumentProcessorService(bg_db)
                    await bg_processor.process_document(doc_id, tid)

                    job.complete({"chunk_count": "see document record"})
            except Exception as e:
                logger.error(f"Background document processing failed: {e}")
                # Use a SEPARATE session to mark document as error.
                # The original session was rolled back by get_db_for_tenant,
                # so we need a fresh session to persist the error status.
                try:
                    async with get_db_for_tenant(str(tid)) as err_db:
                        result = await err_db.execute(
                            select(KnowledgeDocument).where(
                                KnowledgeDocument.id == doc_id,
                            )
                        )
                        failed_doc = result.scalar_one_or_none()
                        if failed_doc and failed_doc.status != "error":
                            failed_doc.status = "error"
                            failed_doc.error_message = str(e)[:500]
                        # get_db_for_tenant auto-commits on clean exit
                except Exception as inner_e:
                    logger.error(f"Failed to mark document {doc_id} as error: {inner_e}")

        asyncio.create_task(_process_in_background())

        # Return doc immediately with status="uploading" — client polls for "ready"
        return response

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{kb_id}/documents", response_model=KnowledgeDocumentListResponse)
async def list_documents(
    kb_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """List all documents in a knowledge base."""
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Tenant context required")

    result = await db.execute(
        select(KnowledgeDocument).where(
            KnowledgeDocument.knowledge_base_id == kb_id,
            KnowledgeDocument.tenant_id == UUID(str(tenant_id)),
        ).order_by(KnowledgeDocument.created_at.desc())
    )
    docs = list(result.scalars().all())
    return KnowledgeDocumentListResponse(
        documents=[_doc_to_response(doc) for doc in docs],
        count=len(docs),
    )


@router.get("/{kb_id}/documents/{doc_id}", response_model=KnowledgeDocumentResponse)
async def get_document(
    kb_id: UUID,
    doc_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Get a single document's details."""
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Tenant context required")

    result = await db.execute(
        select(KnowledgeDocument).where(
            KnowledgeDocument.id == UUID(doc_id),
            KnowledgeDocument.knowledge_base_id == kb_id,
            KnowledgeDocument.tenant_id == UUID(str(tenant_id)),
        )
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return _doc_to_response(doc)


@router.delete("/{kb_id}/documents/{doc_id}", status_code=204)
async def delete_document(
    kb_id: UUID,
    doc_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Delete a document and its chunks."""
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Tenant context required")

    result = await db.execute(
        select(KnowledgeDocument).where(
            KnowledgeDocument.id == UUID(doc_id),
            KnowledgeDocument.knowledge_base_id == kb_id,
            KnowledgeDocument.tenant_id == UUID(str(tenant_id)),
        )
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    await db.delete(doc)
    await db.flush()

    # Update KB counts
    kb_service = KnowledgeBaseService(db)
    await kb_service.update_kb_counts(kb_id, UUID(str(tenant_id)))


@router.get("/{kb_id}/documents/{doc_id}/versions", response_model=KnowledgeDocumentListResponse)
async def list_document_versions(
    kb_id: UUID,
    doc_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """List all versions of a document (by filename match)."""
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Tenant context required")

    # First get the document to find its filename
    result = await db.execute(
        select(KnowledgeDocument).where(
            KnowledgeDocument.id == UUID(doc_id),
            KnowledgeDocument.knowledge_base_id == kb_id,
            KnowledgeDocument.tenant_id == UUID(str(tenant_id)),
        )
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Find all versions with same filename
    versions_result = await db.execute(
        select(KnowledgeDocument).where(
            KnowledgeDocument.knowledge_base_id == kb_id,
            KnowledgeDocument.tenant_id == UUID(str(tenant_id)),
            KnowledgeDocument.filename == doc.filename,
        ).order_by(KnowledgeDocument.version.desc())
    )
    versions = list(versions_result.scalars().all())
    return KnowledgeDocumentListResponse(
        documents=[_doc_to_response(v) for v in versions],
        count=len(versions),
    )


# ============================================================================
# Search Preview
# ============================================================================

@router.post("/{kb_id}/search", response_model=KnowledgeSearchResponse)
async def search_knowledge_base(
    kb_id: UUID,
    data: KnowledgeSearchRequest,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Search a knowledge base with a query.

    Uses vector similarity search over embedded document chunks.
    """
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Tenant context required")

    # Verify KB exists
    kb_service = KnowledgeBaseService(db)
    kb = await kb_service.get_kb(kb_id, UUID(str(tenant_id)))
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    # Generate query embedding via RAGService (uses Redis cache + configurable model)
    from app.services.rag_service import RAGService
    rag_service = RAGService(db)
    try:
        query_embedding = await rag_service.get_embedding(data.query)
        if query_embedding is None:
            raise HTTPException(status_code=500, detail="Embedding generation failed")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Embedding generation failed: {str(e)}")

    # Search
    results = await kb_service.search_kb(
        kb_id=kb_id,
        tenant_id=UUID(str(tenant_id)),
        query_embedding=query_embedding,
        limit=data.limit,
    )

    return KnowledgeSearchResponse(
        results=[KnowledgeSearchResult(**r) for r in results],
        count=len(results),
        query=data.query,
    )
