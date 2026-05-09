"""Pydantic schemas for Projects API."""
from typing import Optional, List, Dict, Any, Literal
from pydantic import BaseModel, Field
from datetime import datetime
from uuid import UUID


# --- Project Schemas ---

class ProjectCreate(BaseModel):
    """Schema for creating a new project."""
    name: str = Field(..., max_length=255, description="Project name")
    description: Optional[str] = Field(None, description="Project description")
    instructions: Optional[str] = Field(None, max_length=10000, description="Instructions for LLM context")
    default_model: Optional[str] = Field(None, max_length=255, description="Default model for project chats")
    settings: Dict[str, Any] = Field(default_factory=dict, description="Additional settings")


class ProjectUpdate(BaseModel):
    """Schema for updating a project. All fields optional."""
    name: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    instructions: Optional[str] = Field(None, max_length=10000)
    default_model: Optional[str] = Field(None, max_length=255)
    settings: Optional[Dict[str, Any]] = None
    row_version: int = Field(..., description="Current row_version for optimistic locking")


class ProjectArchive(BaseModel):
    """Schema for archiving/unarchiving a project."""
    is_archived: bool = Field(..., description="True to archive, False to unarchive")


class ProjectResponse(BaseModel):
    """Schema for project API responses."""
    id: UUID
    tenant_id: UUID
    owner_id: UUID
    name: str
    description: Optional[str] = None
    instructions: Optional[str] = None
    default_model: Optional[str] = None
    knowledge_base_id: Optional[UUID] = None
    settings: Dict[str, Any] = Field(default_factory=dict)
    is_archived: bool = False
    row_version: int = 1
    member_count: int = 0
    file_count: int = 0
    session_count: int = 0
    user_role: str = "owner"
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ProjectListResponse(BaseModel):
    """Schema for paginated project list."""
    projects: List[ProjectResponse]
    total: int
    page: int = 1
    page_size: int = 20


# --- Project Member Schemas ---

class ProjectMemberCreate(BaseModel):
    """Schema for adding a member to a project."""
    user_id: UUID = Field(..., description="User to add")
    role: Literal["editor", "viewer"] = Field("viewer", description="Member role")


class ProjectMemberUpdate(BaseModel):
    """Schema for updating a member's role."""
    role: Literal["editor", "viewer"] = Field(..., description="New role")


class ProjectMemberResponse(BaseModel):
    """Schema for project member API responses."""
    id: UUID
    project_id: UUID
    user_id: UUID
    role: str
    invited_by: Optional[UUID] = None
    user_email: Optional[str] = None
    user_name: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


# --- Transfer Ownership ---

class ProjectTransferOwnership(BaseModel):
    """Schema for transferring project ownership."""
    new_owner_id: UUID = Field(..., description="User ID of the new owner")
