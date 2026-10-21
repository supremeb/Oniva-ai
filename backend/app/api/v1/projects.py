"""Projects API router — CRUD, members, files, sessions."""
import asyncio
import logging
from uuid import UUID
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db, get_db_for_tenant
from app.auth.dependencies import get_current_user, get_current_tenant_id
from app.auth.providers.base import AuthUser
from app.services.project_service import ProjectService
from app.schemas.project import (
    ProjectCreate,
    ProjectUpdate,
    ProjectArchive,
    ProjectResponse,
    ProjectListResponse,
    ProjectMemberCreate,
    ProjectMemberUpdate,
    ProjectMemberResponse,
    ProjectTransferOwnership,
)
from app.exceptions import OptimisticLockError, EntityNotFoundError, EntityConflictError, PermissionDeniedError

logger = logging.getLogger(__name__)

router = APIRouter()


def _to_project_response(data: dict) -> ProjectResponse:
    """Convert project dict to response schema."""
    return ProjectResponse(
        id=UUID(data["id"]),
        tenant_id=UUID(data["tenant_id"]),
        owner_id=UUID(data["owner_id"]),
        name=data["name"],
        description=data.get("description"),
        instructions=data.get("instructions"),
        default_model=data.get("default_model"),
        knowledge_base_id=UUID(data["knowledge_base_id"]) if data.get("knowledge_base_id") else None,
        settings=data.get("settings", {}),
        is_archived=data.get("is_archived", False),
        row_version=data.get("row_version", 1),
        member_count=data.get("member_count", 0),
        file_count=data.get("file_count", 0),
        session_count=data.get("session_count", 0),
        user_role=data.get("user_role", "owner"),
        created_at=data["created_at"],
        updated_at=data["updated_at"],
    )


# ──────────────────────────────────────────────
# Project CRUD
# ──────────────────────────────────────────────

@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectCreate,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """Create a new project workspace."""
    service = ProjectService(db)
    project = await service.create_project(
        tenant_id=tenant_id,
        user_id=UUID(user.id),
        name=body.name,
        description=body.description,
        instructions=body.instructions,
        default_model=body.default_model,
        settings=body.settings,
    )
    data = await service.get_project(project.id, UUID(user.id), tenant_id)
    return _to_project_response(data)


@router.get("", response_model=ProjectListResponse)
async def list_projects(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    include_archived: bool = Query(False),
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """List projects the current user owns or is a member of."""
    service = ProjectService(db)
    result = await service.list_projects(
        user_id=UUID(user.id),
        tenant_id=tenant_id,
        page=page,
        page_size=page_size,
        include_archived=include_archived,
    )
    return ProjectListResponse(
        projects=[_to_project_response(p) for p in result["projects"]],
        total=result["total"],
        page=result["page"],
        page_size=result["page_size"],
    )


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """Get project details."""
    service = ProjectService(db)
    try:
        data = await service.get_project(project_id, UUID(user.id), tenant_id)
    except EntityNotFoundError:
        raise HTTPException(status_code=404, detail="Project not found")
    except PermissionDeniedError:
        raise HTTPException(status_code=403, detail="Access denied")
    return _to_project_response(data)


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: UUID,
    body: ProjectUpdate,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """Update project settings. Requires editor+ role."""
    service = ProjectService(db)
    try:
        project = await service.update_project(
            project_id=project_id,
            user_id=UUID(user.id),
            tenant_id=tenant_id,
            row_version=body.row_version,
            name=body.name,
            description=body.description,
            instructions=body.instructions,
            default_model=body.default_model,
            settings=body.settings,
        )
    except EntityNotFoundError:
        raise HTTPException(status_code=404, detail="Project not found")
    except PermissionDeniedError:
        raise HTTPException(status_code=403, detail="Access denied")
    except OptimisticLockError:
        raise HTTPException(status_code=409, detail="Project was modified by another request. Refresh and retry.")

    data = await service.get_project(project_id, UUID(user.id), tenant_id)
    return _to_project_response(data)


@router.patch("/{project_id}/archive", response_model=ProjectResponse)
async def archive_project(
    project_id: UUID,
    body: ProjectArchive,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """Archive or unarchive a project. Owner only."""
    service = ProjectService(db)
    try:
        await service.archive_project(
            project_id=project_id,
            user_id=UUID(user.id),
            tenant_id=tenant_id,
            is_archived=body.is_archived,
        )
    except EntityNotFoundError:
        raise HTTPException(status_code=404, detail="Project not found")
    except PermissionDeniedError:
        raise HTTPException(status_code=403, detail="Only the project owner can archive")

    data = await service.get_project(project_id, UUID(user.id), tenant_id)
    return _to_project_response(data)


# ──────────────────────────────────────────────
# Members
# ──────────────────────────────────────────────

@router.get("/{project_id}/members")
async def list_members(
    project_id: UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """List project members including owner."""
    service = ProjectService(db)
    try:
        members = await service.list_members(project_id, UUID(user.id), tenant_id)
    except EntityNotFoundError:
        raise HTTPException(status_code=404, detail="Project not found")
    except PermissionDeniedError:
        raise HTTPException(status_code=403, detail="Access denied")
    return {"members": members}


@router.post("/{project_id}/members", status_code=status.HTTP_201_CREATED)
async def add_member(
    project_id: UUID,
    body: ProjectMemberCreate,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """Add a member to a project. Owner only."""
    service = ProjectService(db)
    try:
        member = await service.add_member(
            project_id=project_id,
            user_id=body.user_id,
            role=body.role,
            invited_by=UUID(user.id),
            tenant_id=tenant_id,
        )
    except EntityNotFoundError as e:
        what = "User" if getattr(e, "entity_type", "") == "User" else "Project"
        raise HTTPException(status_code=404, detail=f"{what} not found")
    except EntityConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return member.to_dict()


@router.patch("/{project_id}/members/{member_user_id}")
async def update_member_role(
    project_id: UUID,
    member_user_id: UUID,
    body: ProjectMemberUpdate,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """Update a member's role. Owner only."""
    service = ProjectService(db)
    try:
        member = await service.update_member_role(
            project_id=project_id,
            target_user_id=member_user_id,
            role=body.role,
            acting_user_id=UUID(user.id),
            tenant_id=tenant_id,
        )
    except EntityNotFoundError:
        raise HTTPException(status_code=404, detail="Member not found")
    except PermissionDeniedError:
        raise HTTPException(status_code=403, detail="Only the project owner can change roles")
    return member.to_dict()


@router.delete("/{project_id}/members/{member_user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    project_id: UUID,
    member_user_id: UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """Remove a member. Owner can remove anyone; members can leave."""
    service = ProjectService(db)
    try:
        await service.remove_member(
            project_id=project_id,
            target_user_id=member_user_id,
            acting_user_id=UUID(user.id),
            tenant_id=tenant_id,
        )
    except EntityNotFoundError:
        raise HTTPException(status_code=404, detail="Member not found")
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.post("/{project_id}/transfer-ownership", response_model=ProjectResponse)
async def transfer_ownership(
    project_id: UUID,
    body: ProjectTransferOwnership,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """Transfer project ownership to an existing member. Owner only."""
    service = ProjectService(db)
    try:
        await service.transfer_ownership(
            project_id=project_id,
            new_owner_id=body.new_owner_id,
            acting_user_id=UUID(user.id),
            tenant_id=tenant_id,
        )
    except EntityNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionDeniedError:
        raise HTTPException(status_code=403, detail="Only the project owner can transfer ownership")

    data = await service.get_project(project_id, UUID(user.id), tenant_id)
    return _to_project_response(data)


# ──────────────────────────────────────────────
# Sessions
# ──────────────────────────────────────────────

@router.get("/{project_id}/sessions")
async def list_project_sessions(
    project_id: UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """List current user's chat sessions within a project."""
    service = ProjectService(db)
    try:
        sessions = await service.list_project_sessions(project_id, UUID(user.id), tenant_id)
    except EntityNotFoundError:
        raise HTTPException(status_code=404, detail="Project not found")
    except PermissionDeniedError:
        raise HTTPException(status_code=403, detail="Access denied")
    return {"sessions": sessions}


# ──────────────────────────────────────────────
# Files
# ──────────────────────────────────────────────

@router.post("/{project_id}/files", status_code=status.HTTP_201_CREATED)
async def upload_project_file(
    project_id: UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """Upload a file to a project's knowledge base. Requires editor+ role."""
    service = ProjectService(db)
    try:
        role = await service.check_permission(project_id, UUID(user.id), tenant_id, required_role="editor")
    except (EntityNotFoundError, PermissionDeniedError):
        raise HTTPException(status_code=403, detail="Access denied")

    project = await service._get_project_raw(project_id, tenant_id)
    if not project or not project.knowledge_base_id:
        raise HTTPException(status_code=404, detail="Project knowledge base not found")

    # Check file size (50MB limit)
    contents = await file.read()
    if len(contents) > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large. Maximum size is 50MB.")
    await file.seek(0)

    # Check file count (100 file limit)
    current_count = await service._count_files(project.knowledge_base_id)
    if current_count >= 100:
        raise HTTPException(status_code=400, detail="Maximum 100 files per project.")

    # Delegate to document processor
    from app.services.document_processor import DocumentProcessorService
    doc_service = DocumentProcessorService(db)
    from app.services.document_processor import ALLOWED_FILE_TYPES, _sanitize_filename
    safe_name = _sanitize_filename(file.filename or "untitled")
    ext = safe_name.rsplit(".", 1)[-1].lower() if "." in safe_name else ""
    if ext not in ALLOWED_FILE_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type '.{ext}'. Allowed: {', '.join(sorted(ALLOWED_FILE_TYPES))}",
        )
    try:
        document = await doc_service.upload_document(
            kb_id=project.knowledge_base_id,
            tenant_id=tenant_id,
            filename=file.filename or "untitled",
            file_content=contents,
            file_size=len(contents),
            user_id=UUID(user.id),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # Build the response BEFORE committing: the tenant GUC is transaction-local (SET LOCAL), so a
    # refresh after the commit runs without tenant context and RLS hides the new row (500).
    # Embedding/processing failures happen in the background and surface as status="error".
    await db.refresh(document)
    response = document.to_dict()
    # Commit so the background task (new DB session) can see the document
    await db.commit()

    # Fire background processing (same pattern as knowledge_management.py)
    doc_id = document.id
    tid = str(tenant_id)
    uid = UUID(user.id)
    asyncio.create_task(_process_project_doc_in_background(doc_id, tid, uid))

    return response


@router.get("/{project_id}/files")
async def list_project_files(
    project_id: UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """List files in a project's knowledge base."""
    service = ProjectService(db)
    try:
        await service.check_permission(project_id, UUID(user.id), tenant_id, required_role="viewer")
    except (EntityNotFoundError, PermissionDeniedError):
        raise HTTPException(status_code=403, detail="Access denied")

    project = await service._get_project_raw(project_id, tenant_id)
    if not project or not project.knowledge_base_id:
        return {"files": []}

    from app.models.knowledge_document import KnowledgeDocument
    from sqlalchemy import select as sa_select
    query = sa_select(KnowledgeDocument).where(
        KnowledgeDocument.knowledge_base_id == project.knowledge_base_id
    ).order_by(KnowledgeDocument.created_at.desc())
    result = await db.execute(query)
    docs = result.scalars().all()
    return {"files": [d.to_dict() for d in docs]}


@router.delete("/{project_id}/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project_file(
    project_id: UUID,
    file_id: UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """Delete a file from a project's knowledge base. Requires editor+ role."""
    service = ProjectService(db)
    try:
        await service.check_permission(project_id, UUID(user.id), tenant_id, required_role="editor")
    except (EntityNotFoundError, PermissionDeniedError):
        raise HTTPException(status_code=403, detail="Access denied")

    project = await service._get_project_raw(project_id, tenant_id)
    if not project or not project.knowledge_base_id:
        raise HTTPException(status_code=404, detail="Project not found")

    from app.models.knowledge_document import KnowledgeDocument
    from app.services.knowledge_base import KnowledgeBaseService
    from sqlalchemy import select as sa_select

    result = await db.execute(
        sa_select(KnowledgeDocument).where(
            KnowledgeDocument.id == file_id,
            KnowledgeDocument.knowledge_base_id == project.knowledge_base_id,
            KnowledgeDocument.tenant_id == tenant_id,
        )
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="File not found")

    await db.delete(doc)
    await db.flush()

    kb_service = KnowledgeBaseService(db)
    await kb_service.update_kb_counts(project.knowledge_base_id, tenant_id)


# ──────────────────────────────────────────────
# Background helpers
# ──────────────────────────────────────────────

async def _process_project_doc_in_background(document_id, tenant_id: str, user_id: UUID) -> None:
    """Process an uploaded project document in a background task with its own DB session."""
    try:
        async with get_db_for_tenant(tenant_id) as bg_db:
            from app.models.background_job import BackgroundJob, JobType, JobStatus
            from app.services.document_processor import DocumentProcessorService

            job = BackgroundJob(
                tenant_id=UUID(tenant_id),
                user_id=user_id,
                job_type=JobType.DOCUMENT_PROCESSING.value,
                status=JobStatus.RUNNING.value,
                input_data={"document_id": str(document_id)},
            )
            job.start()
            bg_db.add(job)
            await bg_db.flush()

            processor = DocumentProcessorService(bg_db)
            await processor.process_document(document_id, UUID(tenant_id))

            job.complete({"chunk_count": "see document record"})
    except Exception as e:
        logger.error(f"Background project document processing failed: {e}")
        try:
            from app.models.knowledge_document import KnowledgeDocument
            async with get_db_for_tenant(tenant_id) as err_db:
                result = await err_db.execute(
                    select(KnowledgeDocument).where(
                        KnowledgeDocument.id == document_id,
                    )
                )
                failed_doc = result.scalar_one_or_none()
                if failed_doc and failed_doc.status != "error":
                    failed_doc.status = "error"
                    failed_doc.error_message = str(e)[:500]
        except Exception as inner_e:
            logger.error(f"Failed to mark document {document_id} as error: {inner_e}")
