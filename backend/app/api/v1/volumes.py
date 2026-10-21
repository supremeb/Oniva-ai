"""
Volume API endpoints for Oniva Simplified Memory Architecture.

Provides REST API for managing volumes - pre-loaded, read-only datasets
that can be mounted to agent sessions.

Endpoints:
- GET /api/v1/volumes - List all available volumes
- POST /api/v1/volumes - Create a new volume
- GET /api/v1/volumes/{volume_name} - Get volume details
- DELETE /api/v1/volumes/{volume_name} - Delete a volume
- POST /api/v1/volumes/{volume_name}/mount/{session_id} - Mount volume to session
- DELETE /api/v1/volumes/{volume_name}/mount/{session_id} - Unmount volume from session
- GET /api/v1/volumes/{volume_name}/files - List files in volume
- GET /api/v1/volumes/{volume_name}/files/{file_path:path} - Read file from volume
"""

from datetime import datetime
from typing import Optional
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.auth.dependencies import get_current_tenant_id
from app.services.volumes import (
    VolumeManager,
    VolumeInfo,
    get_volume_manager,
)


router = APIRouter(prefix="/volumes", tags=["volumes"])


# ============================================================================
# Request/Response Models
# ============================================================================


class VolumeCreateRequest(BaseModel):
    """Request body for creating a volume."""
    name: str = Field(
        ...,
        description="Unique volume name (alphanumeric + underscores)",
        min_length=1,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_]+$",
    )
    source: str = Field(
        ...,
        description="Data source: S3 URI (s3://bucket/prefix) or local path",
    )
    overwrite: bool = Field(
        default=False,
        description="Overwrite existing volume if it exists",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "name": "market_data_2026",
                    "source": "/data/market_data",
                    "overwrite": False,
                },
                {
                    "name": "sec_filings",
                    "source": "s3://oniva-data/sec_filings",
                    "overwrite": True,
                },
            ]
        }
    }


class VolumeResponse(BaseModel):
    """Response model for volume information."""
    name: str
    path: str
    size_bytes: int
    file_count: int
    created_at: Optional[datetime] = None
    source: Optional[str] = None
    is_mounted: bool = False

    @classmethod
    def from_volume_info(cls, info: VolumeInfo) -> "VolumeResponse":
        return cls(
            name=info.name,
            path=info.path,
            size_bytes=info.size_bytes,
            file_count=info.file_count,
            created_at=info.created_at,
            source=info.source,
            is_mounted=info.is_mounted,
        )


class MountResponse(BaseModel):
    """Response model for mount information."""
    session_id: str
    volume_name: str
    mount_path: str
    mounted_at: datetime


class FileInfo(BaseModel):
    """Information about a file in a volume."""
    name: str
    path: str
    size: int
    is_dir: bool


class VolumeListResponse(BaseModel):
    """Response for listing volumes."""
    volumes: list[VolumeResponse]
    count: int


class FileListResponse(BaseModel):
    """Response for listing files."""
    files: list[FileInfo]
    count: int
    volume_name: str
    path: str


# ============================================================================
# Endpoints
# ============================================================================


@router.get("", response_model=VolumeListResponse)
async def list_volumes(
    volume_mgr: VolumeManager = Depends(get_volume_manager),
    tenant_id: UUID = Depends(get_current_tenant_id),
) -> VolumeListResponse:
    """
    List all available volumes.

    Returns volume metadata including name, size, and file count.
    """
    volumes = await volume_mgr.list_volumes(str(tenant_id))
    return VolumeListResponse(
        volumes=[VolumeResponse.from_volume_info(v) for v in volumes],
        count=len(volumes),
    )


@router.post("", response_model=VolumeResponse, status_code=201)
async def create_volume(
    request: VolumeCreateRequest,
    volume_mgr: VolumeManager = Depends(get_volume_manager),
    tenant_id: UUID = Depends(get_current_tenant_id),
) -> VolumeResponse:
    """
    Create a new volume from S3 or local filesystem.

    The source can be:
    - A local filesystem path (e.g., "/data/market_data")
    - An S3 URI (e.g., "s3://bucket/prefix")

    Volume data is copied to the volumes directory and can be mounted
    to multiple sessions as read-only data.
    """
    try:
        info = await volume_mgr.create_volume(str(tenant_id), 
            volume_name=request.name,
            source=request.source,
            overwrite=request.overwrite,
        )
        response = VolumeResponse.from_volume_info(info)
        response.source = request.source
        return response
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create volume: {str(e)}",
        )


@router.get("/{volume_name}", response_model=VolumeResponse)
async def get_volume(
    volume_name: str,
    volume_mgr: VolumeManager = Depends(get_volume_manager),
    tenant_id: UUID = Depends(get_current_tenant_id),
) -> VolumeResponse:
    """
    Get detailed information about a specific volume.
    """
    try:
        info = await volume_mgr.get_volume(str(tenant_id), volume_name)
        return VolumeResponse.from_volume_info(info)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/{volume_name}", status_code=204)
async def delete_volume(
    volume_name: str,
    force: bool = Query(
        default=False,
        description="Delete even if mounted to sessions",
    ),
    volume_mgr: VolumeManager = Depends(get_volume_manager),
    tenant_id: UUID = Depends(get_current_tenant_id),
) -> None:
    """
    Delete a volume.

    By default, fails if the volume is mounted to any sessions.
    Use force=true to delete anyway.
    """
    try:
        deleted = await volume_mgr.delete_volume(str(tenant_id), volume_name, force=force)
        if not deleted:
            raise HTTPException(
                status_code=404,
                detail=f"Volume '{volume_name}' not found",
            )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post(
    "/{volume_name}/mount/{session_id}",
    response_model=MountResponse,
    status_code=201,
)
async def mount_volume_to_session(
    volume_name: str,
    session_id: str,
    volume_mgr: VolumeManager = Depends(get_volume_manager),
    tenant_id: UUID = Depends(get_current_tenant_id),
) -> MountResponse:
    """
    Mount a volume to a session.

    Creates a symlink from the session's volumes directory to the volume.
    The volume is accessible read-only at:
    /sessions/{session_id}/volumes/{volume_name}/
    """
    try:
        mount_path = await volume_mgr.mount_volume(str(tenant_id), volume_name, session_id)
        mounts = await volume_mgr.list_session_mounts(str(tenant_id), session_id)
        # Find the mount we just created
        mount = next((m for m in mounts if m.volume_name == volume_name), None)
        if mount:
            return MountResponse(
                session_id=mount.session_id,
                volume_name=mount.volume_name,
                mount_path=mount.mount_path,
                mounted_at=mount.mounted_at,
            )
        # Fallback if not found in tracking
        return MountResponse(
            session_id=session_id,
            volume_name=volume_name,
            mount_path=str(mount_path),
            mounted_at=datetime.utcnow(),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/{volume_name}/mount/{session_id}", status_code=204)
async def unmount_volume_from_session(
    volume_name: str,
    session_id: str,
    volume_mgr: VolumeManager = Depends(get_volume_manager),
    tenant_id: UUID = Depends(get_current_tenant_id),
) -> None:
    """
    Unmount a volume from a session.

    Removes the symlink. Does not affect the volume data.
    """
    unmounted = await volume_mgr.unmount_volume(str(tenant_id), volume_name, session_id)
    if not unmounted:
        raise HTTPException(
            status_code=404,
            detail=f"Volume '{volume_name}' not mounted to session '{session_id}'",
        )


@router.get("/{volume_name}/files", response_model=FileListResponse)
async def list_volume_files(
    volume_name: str,
    path: str = Query(default="", description="Relative path within volume"),
    recursive: bool = Query(default=False, description="List files recursively"),
    volume_mgr: VolumeManager = Depends(get_volume_manager),
    tenant_id: UUID = Depends(get_current_tenant_id),
) -> FileListResponse:
    """
    List files in a volume directory.

    By default, lists files in the root of the volume.
    Use path parameter to list a subdirectory.
    Use recursive=true to list all files recursively.
    """
    try:
        files = await volume_mgr.list_files(str(tenant_id), volume_name, path, recursive)
        return FileListResponse(
            files=[FileInfo(**f) for f in files],
            count=len(files),
            volume_name=volume_name,
            path=path,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{volume_name}/files/{file_path:path}")
async def read_volume_file(
    volume_name: str,
    file_path: str,
    volume_mgr: VolumeManager = Depends(get_volume_manager),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """
    Read a file from a volume.

    Returns the file content with appropriate content-type header.
    For large files, consider using presigned URLs instead.
    """
    from fastapi.responses import Response
    import mimetypes

    try:
        content = await volume_mgr.read_file(str(tenant_id), volume_name, file_path)

        # Guess content type
        mime_type, _ = mimetypes.guess_type(file_path)
        if mime_type is None:
            mime_type = "application/octet-stream"

        return Response(
            content=content,
            media_type=mime_type,
            headers={
                "Content-Disposition": f"inline; filename=\"{Path(file_path).name}\"",
            },
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/sessions/{session_id}/mounts", response_model=list[MountResponse])
async def list_session_mounts(
    session_id: str,
    volume_mgr: VolumeManager = Depends(get_volume_manager),
    tenant_id: UUID = Depends(get_current_tenant_id),
) -> list[MountResponse]:
    """
    List all volumes mounted to a session.
    """
    mounts = await volume_mgr.list_session_mounts(str(tenant_id), session_id)
    return [
        MountResponse(
            session_id=m.session_id,
            volume_name=m.volume_name,
            mount_path=m.mount_path,
            mounted_at=m.mounted_at,
        )
        for m in mounts
    ]
