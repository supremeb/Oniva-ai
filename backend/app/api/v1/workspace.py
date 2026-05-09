"""
Workspace API endpoints for session file access.

Endpoints:
- GET /api/v1/workspace/{session_id}/files - List workspace files
- GET /api/v1/workspace/{session_id}/files/{path:path} - Read file content
- GET /api/v1/workspace/{session_id}/todo - Read todo.md (for UI polling)
"""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.auth.dependencies import get_current_tenant_id
from app.schemas.sandbox import (
    WorkspaceFileInfo,
    WorkspaceFileListResponse,
    WorkspaceFileContentResponse,
    WorkspaceTodoResponse,
)
from app.services.session_storage import get_session_storage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/workspace", tags=["workspace"])


@router.get("/{session_id}/files", response_model=WorkspaceFileListResponse)
async def list_workspace_files(
    session_id: str,
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """List files in a session workspace."""
    storage = get_session_storage()

    try:
        session_path = storage._get_session_path(str(tenant_id), session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session ID")

    workspace_path = session_path / "workspace"

    if not workspace_path.exists():
        return WorkspaceFileListResponse(
            files=[],
            total_count=0,
            session_id=session_id,
        )

    files = []
    for item in sorted(workspace_path.rglob("*")):
        if item.is_file():
            relative = item.relative_to(workspace_path)
            stat = item.stat()
            files.append(WorkspaceFileInfo(
                name=str(relative),
                type="file",
                size=stat.st_size,
                modified=str(stat.st_mtime),
            ))

    return WorkspaceFileListResponse(
        files=files,
        total_count=len(files),
        session_id=session_id,
    )


@router.get("/{session_id}/files/{path:path}", response_model=WorkspaceFileContentResponse)
async def read_workspace_file(
    session_id: str,
    path: str,
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """Read a file from the session workspace."""
    storage = get_session_storage()

    workspace_path = f"workspace/{path}"

    try:
        content = await storage.read(str(tenant_id), session_id, workspace_path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not content:
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    return WorkspaceFileContentResponse(
        path=path,
        content=content,
        size=len(content.encode("utf-8")),
        session_id=session_id,
    )


@router.get("/{session_id}/todo", response_model=WorkspaceTodoResponse)
async def read_workspace_todo(
    session_id: str,
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """Read todo.md from session (for UI polling)."""
    storage = get_session_storage()

    try:
        content = await storage.read(str(tenant_id), session_id, "todo.md")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return WorkspaceTodoResponse(
        content=content or "",
        session_id=session_id,
    )
