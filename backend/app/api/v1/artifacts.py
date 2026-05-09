"""
Artifact API endpoints for Oniva Simplified Memory Architecture.

Provides REST API for managing artifacts - persistent outputs with optional
7-year retention for regulatory compliance.

Endpoints:
- GET /api/v1/artifacts/sessions/{session_id} - List artifacts for session
- POST /api/v1/artifacts/sessions/{session_id} - Upload artifact
- GET /api/v1/artifacts/sessions/{session_id}/download/{file_path:path} - Get download URL
- GET /api/v1/artifacts/sessions/{session_id}/content/{file_path:path} - Read artifact content
- DELETE /api/v1/artifacts/sessions/{session_id}/{file_path:path} - Delete artifact
- DELETE /api/v1/artifacts/sessions/{session_id} - Delete all session artifacts
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import Response
from pydantic import BaseModel

from app.auth.dependencies import get_current_tenant_id
from app.services.artifacts import (
    ArtifactStorage,
    ArtifactInfo,
    ArtifactMetadata,
    get_artifact_storage,
)


router = APIRouter(prefix="/artifacts", tags=["artifacts"])


# ============================================================================
# Request/Response Models
# ============================================================================


class ArtifactResponse(BaseModel):
    """Response model for artifact information."""
    file_path: str
    size_bytes: int
    last_modified: datetime
    content_type: str
    s3_uri: Optional[str] = None

    @classmethod
    def from_artifact_info(cls, info: ArtifactInfo) -> "ArtifactResponse":
        return cls(
            file_path=info.file_path,
            size_bytes=info.size_bytes,
            last_modified=info.last_modified,
            content_type=info.content_type,
            s3_uri=info.s3_uri,
        )


class ArtifactMetadataResponse(BaseModel):
    """Response model for artifact metadata."""
    session_id: str
    file_path: str
    size_bytes: int
    content_type: str
    created_at: datetime
    checksum: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    retention_until: Optional[datetime] = None
    s3_uri: Optional[str] = None
    download_url: Optional[str] = None

    @classmethod
    def from_metadata(cls, meta: ArtifactMetadata) -> "ArtifactMetadataResponse":
        return cls(
            session_id=meta.session_id,
            file_path=meta.file_path,
            size_bytes=meta.size_bytes,
            content_type=meta.content_type,
            created_at=meta.created_at,
            checksum=meta.checksum,
            user_id=meta.user_id,
            agent_id=meta.agent_id,
            retention_until=meta.retention_until,
            s3_uri=meta.s3_uri,
            download_url=meta.download_url,
        )


class ArtifactUploadResponse(BaseModel):
    """Response model for artifact upload."""
    file_path: str
    size_bytes: int
    content_type: str
    storage_uri: str
    checksum: str


class ArtifactListResponse(BaseModel):
    """Response for listing artifacts."""
    session_id: str
    artifacts: list[ArtifactResponse]
    count: int
    total_size_bytes: int


class DownloadUrlResponse(BaseModel):
    """Response for download URL."""
    session_id: str
    file_path: str
    download_url: str
    expires_in: int


class DeleteResponse(BaseModel):
    """Response for delete operations."""
    deleted: bool
    count: int = 1


# ============================================================================
# Endpoints
# ============================================================================


@router.get("/sessions/{session_id}", response_model=ArtifactListResponse)
async def list_session_artifacts(
    session_id: str,
    tenant_id: UUID = Depends(get_current_tenant_id),
    artifact_storage: ArtifactStorage = Depends(get_artifact_storage),
) -> ArtifactListResponse:
    """
    List all artifacts for a session.

    Returns artifact metadata including file path, size, and content type.
    """
    artifacts = await artifact_storage.list_artifacts(str(tenant_id), session_id)
    total_size = sum(a.size_bytes for a in artifacts)

    return ArtifactListResponse(
        session_id=session_id,
        artifacts=[ArtifactResponse.from_artifact_info(a) for a in artifacts],
        count=len(artifacts),
        total_size_bytes=total_size,
    )


@router.post("/sessions/{session_id}", response_model=ArtifactUploadResponse, status_code=201)
async def upload_artifact(
    session_id: str,
    file: UploadFile = File(...),
    user_id: Optional[str] = Query(default=None, description="User ID for metadata"),
    agent_id: Optional[str] = Query(default=None, description="Agent ID for metadata"),
    tenant_id: UUID = Depends(get_current_tenant_id),
    artifact_storage: ArtifactStorage = Depends(get_artifact_storage),
) -> ArtifactUploadResponse:
    """
    Upload an artifact to storage.

    The artifact is stored with:
    - 7-year retention (in S3 production mode)
    - Server-side encryption
    - Metadata (user_id, agent_id, checksum)

    Use multipart/form-data to upload files.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename required")

    content = await file.read()

    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Empty file not allowed")

    # Build metadata
    metadata = {}
    if user_id:
        metadata["user_id"] = user_id
    if agent_id:
        metadata["agent_id"] = agent_id

    try:
        storage_uri = await artifact_storage.write(
            tenant_id=str(tenant_id),
            session_id=session_id,
            file_path=file.filename,
            content=content,
            metadata=metadata,
            content_type=file.content_type,
        )

        # Get checksum from metadata
        artifact_meta = await artifact_storage.get_metadata(str(tenant_id), session_id, file.filename)
        checksum = artifact_meta.checksum if artifact_meta else ""

        return ArtifactUploadResponse(
            file_path=file.filename,
            size_bytes=len(content),
            content_type=file.content_type or "application/octet-stream",
            storage_uri=storage_uri,
            checksum=checksum,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to upload artifact: {str(e)}",
        )


@router.post(
    "/sessions/{session_id}/write",
    response_model=ArtifactUploadResponse,
    status_code=201,
)
async def write_artifact(
    session_id: str,
    file_path: str = Query(..., description="File path within session"),
    content_type: Optional[str] = Query(default=None, description="MIME type"),
    user_id: Optional[str] = Query(default=None, description="User ID for metadata"),
    agent_id: Optional[str] = Query(default=None, description="Agent ID for metadata"),
    file: UploadFile = File(...),
    tenant_id: UUID = Depends(get_current_tenant_id),
    artifact_storage: ArtifactStorage = Depends(get_artifact_storage),
) -> ArtifactUploadResponse:
    """
    Write an artifact to a specific path.

    Similar to upload, but allows specifying the exact file path
    (including subdirectories).
    """
    content = await file.read()

    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Empty file not allowed")

    metadata = {}
    if user_id:
        metadata["user_id"] = user_id
    if agent_id:
        metadata["agent_id"] = agent_id

    try:
        storage_uri = await artifact_storage.write(
            tenant_id=str(tenant_id),
            session_id=session_id,
            file_path=file_path,
            content=content,
            metadata=metadata,
            content_type=content_type or file.content_type,
        )

        artifact_meta = await artifact_storage.get_metadata(str(tenant_id), session_id, file_path)
        checksum = artifact_meta.checksum if artifact_meta else ""

        return ArtifactUploadResponse(
            file_path=file_path,
            size_bytes=len(content),
            content_type=content_type or file.content_type or "application/octet-stream",
            storage_uri=storage_uri,
            checksum=checksum,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to write artifact: {str(e)}",
        )


@router.get(
    "/sessions/{session_id}/download/{file_path:path}",
    response_model=DownloadUrlResponse,
)
async def get_artifact_download_url(
    session_id: str,
    file_path: str,
    expires_in: int = Query(
        default=3600,
        ge=60,
        le=86400,
        description="URL expiration time in seconds (1 min to 24 hours)",
    ),
    tenant_id: UUID = Depends(get_current_tenant_id),
    artifact_storage: ArtifactStorage = Depends(get_artifact_storage),
) -> DownloadUrlResponse:
    """
    Generate a download URL for an artifact.

    In S3 mode: Returns a presigned URL that expires after the specified time.
    In local mode: Returns a file:// URL.
    """
    try:
        url = await artifact_storage.get_download_url(str(tenant_id), session_id, file_path, expires_in)

        return DownloadUrlResponse(
            session_id=session_id,
            file_path=file_path,
            download_url=url,
            expires_in=expires_in,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/sessions/{session_id}/content/{file_path:path}")
async def read_artifact_content(
    session_id: str,
    file_path: str,
    tenant_id: UUID = Depends(get_current_tenant_id),
    artifact_storage: ArtifactStorage = Depends(get_artifact_storage),
):
    """
    Read artifact content directly.

    Returns the file content with appropriate content-type header.
    For large files, prefer using the download URL endpoint.
    """
    try:
        content = await artifact_storage.read(str(tenant_id), session_id, file_path)

        # Get metadata for content type
        metadata = await artifact_storage.get_metadata(str(tenant_id), session_id, file_path)
        content_type = metadata.content_type if metadata else "application/octet-stream"

        return Response(
            content=content,
            media_type=content_type,
            headers={
                "Content-Disposition": f'inline; filename="{file_path.split("/")[-1]}"',
            },
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get(
    "/sessions/{session_id}/metadata/{file_path:path}",
    response_model=ArtifactMetadataResponse,
)
async def get_artifact_metadata(
    session_id: str,
    file_path: str,
    tenant_id: UUID = Depends(get_current_tenant_id),
    artifact_storage: ArtifactStorage = Depends(get_artifact_storage),
) -> ArtifactMetadataResponse:
    """
    Get metadata for an artifact.

    Returns detailed metadata including checksum, user_id, agent_id, and retention info.
    """
    metadata = await artifact_storage.get_metadata(str(tenant_id), session_id, file_path)

    if not metadata:
        raise HTTPException(
            status_code=404,
            detail=f"Artifact not found: {file_path}",
        )

    return ArtifactMetadataResponse.from_metadata(metadata)


@router.delete("/sessions/{session_id}/{file_path:path}", response_model=DeleteResponse)
async def delete_artifact(
    session_id: str,
    file_path: str,
    tenant_id: UUID = Depends(get_current_tenant_id),
    artifact_storage: ArtifactStorage = Depends(get_artifact_storage),
) -> DeleteResponse:
    """
    Delete an artifact.

    Note: In S3 with Object Lock, deletion may fail before retention expires.
    """
    deleted = await artifact_storage.delete(str(tenant_id), session_id, file_path)

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail=f"Artifact not found: {file_path}",
        )

    return DeleteResponse(deleted=True, count=1)


@router.delete("/sessions/{session_id}", response_model=DeleteResponse)
async def delete_all_session_artifacts(
    session_id: str,
    tenant_id: UUID = Depends(get_current_tenant_id),
    artifact_storage: ArtifactStorage = Depends(get_artifact_storage),
) -> DeleteResponse:
    """
    Delete all artifacts for a session.

    Use with caution - this deletes all artifacts permanently.
    In S3 with Object Lock, deletion may fail before retention expires.
    """
    count = await artifact_storage.delete_session_artifacts(str(tenant_id), session_id)

    return DeleteResponse(deleted=count > 0, count=count)
