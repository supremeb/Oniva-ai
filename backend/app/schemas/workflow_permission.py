"""Pydantic schemas for workflow permissions.

Enterprise feature: Workflow sharing and collaboration.

- Phase 3: Collaboration
"""
import uuid
from datetime import datetime
from typing import Optional, List, Literal

from pydantic import BaseModel, Field, field_validator


class WorkflowPermissionCreate(BaseModel):
    """Request to create a workflow permission."""
    grantee_type: Literal["user", "role", "organization"] = Field(
        ...,
        description="Type of grantee: 'user' for specific user, 'role' for role-based, 'organization' for org-wide"
    )
    grantee_id: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="User UUID, role name, or 'organization'"
    )
    permission: Literal["view", "execute", "edit", "admin"] = Field(
        ...,
        description="Permission level: view < execute < edit < admin"
    )
    expires_at: Optional[datetime] = Field(
        None,
        description="Optional expiration time for temporary access"
    )

    @field_validator('grantee_id')
    @classmethod
    def validate_grantee_id(cls, v: str, info) -> str:
        grantee_type = info.data.get('grantee_type')
        if grantee_type == 'user':
            try:
                uuid.UUID(v)
            except ValueError:
                raise ValueError('grantee_id must be a valid UUID for user type')
        elif grantee_type == 'organization':
            if v != 'organization':
                raise ValueError("grantee_id must be 'organization' for organization type")
        return v


class WorkflowPermissionUpdate(BaseModel):
    """Request to update a workflow permission."""
    permission: Optional[Literal["view", "execute", "edit", "admin"]] = Field(
        None,
        description="New permission level"
    )
    expires_at: Optional[datetime] = Field(
        None,
        description="New expiration time (null to remove expiration)"
    )


class WorkflowPermissionResponse(BaseModel):
    """Response containing workflow permission details."""
    id: uuid.UUID
    workflow_id: uuid.UUID
    grantee_type: str
    grantee_id: str
    permission: str
    granted_by: Optional[uuid.UUID] = None
    granted_at: datetime
    expires_at: Optional[datetime] = None
    is_expired: bool = False
    created_at: datetime

    # Enriched fields (populated when available)
    grantee_name: Optional[str] = Field(
        None,
        description="Display name of the grantee (user name, role name, or 'Everyone')"
    )
    grantee_email: Optional[str] = Field(
        None,
        description="Email of the grantee (for user type only)"
    )
    granter_name: Optional[str] = Field(
        None,
        description="Name of the user who granted the permission"
    )

    class Config:
        from_attributes = True


class WorkflowPermissionBulkCreate(BaseModel):
    """Request to create multiple permissions at once."""
    permissions: List[WorkflowPermissionCreate] = Field(
        ...,
        min_length=1,
        max_length=50,
        description="List of permissions to create (max 50)"
    )


class WorkflowPermissionBulkDelete(BaseModel):
    """Request to delete multiple permissions at once."""
    permission_ids: List[uuid.UUID] = Field(
        ...,
        min_length=1,
        max_length=50,
        description="List of permission IDs to delete (max 50)"
    )


class WorkflowShareSummary(BaseModel):
    """Summary of who a workflow is shared with."""
    workflow_id: uuid.UUID
    owner_id: uuid.UUID
    owner_name: Optional[str] = None
    is_shared: bool = False
    share_count: int = 0
    org_wide: bool = False
    org_permission: Optional[str] = None
    roles: List[str] = Field(default_factory=list)
    user_count: int = 0
    permissions: List[WorkflowPermissionResponse] = Field(default_factory=list)


class CheckPermissionRequest(BaseModel):
    """Request to check if a user has permission on a workflow."""
    user_id: uuid.UUID
    user_role: str = Field(..., description="User's role (admin, analyst, viewer)")
    required_permission: Literal["view", "execute", "edit", "admin"]


class CheckPermissionResponse(BaseModel):
    """Response for permission check."""
    has_permission: bool
    reason: str = Field(
        ...,
        description="Explanation of why permission was granted or denied"
    )
    effective_permission: Optional[str] = Field(
        None,
        description="The highest permission level the user has"
    )
    grant_source: Optional[str] = Field(
        None,
        description="How the permission was granted: 'owner', 'direct', 'role', 'organization'"
    )


class SharedWorkflowResponse(BaseModel):
    """Workflow info for shared workflows list."""
    id: uuid.UUID
    name: str
    description: Optional[str] = None
    owner_id: uuid.UUID
    owner_name: Optional[str] = None
    permission: str = Field(
        ...,
        description="The user's permission level on this workflow"
    )
    grant_source: str = Field(
        ...,
        description="How access was granted: 'direct', 'role', 'organization'"
    )
    shared_at: datetime
    expires_at: Optional[datetime] = None
