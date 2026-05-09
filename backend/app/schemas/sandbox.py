"""Pydantic schemas for sandbox execution API."""
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from uuid import UUID


class SandboxExecuteRequest(BaseModel):
    """Request to execute Python code in E2B sandbox."""
    code: str = Field(..., min_length=1, max_length=50000)
    session_id: UUID
    input_files: list[str] = Field(default_factory=list, max_length=20)


class SandboxExecutionResponse(BaseModel):
    """Response model for a sandbox execution."""
    id: str
    session_id: str
    tenant_id: str
    sandbox_id: str
    code: str
    status: str
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    duration_seconds: Optional[float] = None
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class WorkspaceFileInfo(BaseModel):
    """Metadata for a workspace file."""
    name: str
    type: str  # "file" or "directory"
    size: Optional[int] = None
    modified: Optional[str] = None


class WorkspaceFileListResponse(BaseModel):
    """Response for listing workspace files."""
    files: list[WorkspaceFileInfo]
    total_count: int
    session_id: str


class WorkspaceFileContentResponse(BaseModel):
    """Response for reading a workspace file."""
    path: str
    content: str
    size: int
    session_id: str


class WorkspaceTodoResponse(BaseModel):
    """Response for reading todo.md."""
    content: str
    session_id: str
