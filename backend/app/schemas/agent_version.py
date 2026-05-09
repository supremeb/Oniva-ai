"""Pydantic schemas for AgentVersion API."""
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field
from datetime import datetime
from uuid import UUID


class AgentVersionCreate(BaseModel):
    """Schema for creating a new agent version (sequential integer)."""
    agent_id: UUID = Field(..., description="Parent agent ID")
    name: str = Field(..., description="User-provided version label (e.g., 'Added OFAC cross-reference')")
    notes: Optional[str] = Field(None, description="Release notes")


class AgentVersionUpdate(BaseModel):
    """Schema for updating an agent version."""
    notes: Optional[str] = None
    name: Optional[str] = None


class AgentVersionResponse(BaseModel):
    """Schema for agent version API responses."""
    id: UUID
    tenant_id: UUID
    agent_id: UUID
    version_number: Optional[int] = None
    legacy_version: Optional[str] = Field(None, alias="version")
    name: Optional[str] = None
    config: Dict[str, Any] = Field(default_factory=dict)
    is_deployed: bool = False
    deployed_at: Optional[datetime] = None
    deployed_by: Optional[UUID] = None
    accuracy: Optional[float] = None
    accuracy_at_deploy: Optional[float] = None
    deploy_blocked: Optional[bool] = False
    block_reason: Optional[str] = None
    visibility: Optional[str] = "private"
    published_at: Optional[datetime] = None
    compliance_tags: Optional[List[str]] = None
    created_by: Optional[UUID] = None
    notes: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True
        populate_by_name = True


class AgentVersionListResponse(BaseModel):
    """Schema for paginated agent version list."""
    versions: list[AgentVersionResponse]
    total: int
    current_deployed: Optional[str] = Field(None, description="Currently deployed version string")
    production_version_number: Optional[int] = Field(None, description="Version number pointed to by PRODUCTION alias")


class AgentVersionDetailResponse(AgentVersionResponse):
    """Schema for detailed agent version with deployer info."""
    deployer_name: Optional[str] = None
    deployer_email: Optional[str] = None
    creator_name: Optional[str] = None
    creator_email: Optional[str] = None


class DraftStatusResponse(BaseModel):
    """Response for draft change detection."""
    has_changes: bool
    latest_version_number: Optional[int] = None
    changed_fields: List[str] = Field(default_factory=list)
    production_version_number: Optional[int] = None


class DeployVersionRequest(BaseModel):
    """Request to deploy a specific version."""
    version_id: UUID = Field(..., description="Version ID to deploy")


class DeployVersionResponse(BaseModel):
    """Response after deploying a version."""
    success: bool
    version_number: Optional[int] = None
    legacy_version: Optional[str] = None
    deployed_at: datetime
    message: str


class CompareVersionsRequest(BaseModel):
    """Request to compare two versions."""
    version_a: UUID = Field(..., description="First version ID")
    version_b: UUID = Field(..., description="Second version ID")


class ConfigDiff(BaseModel):
    """Schema for config differences between versions."""
    field: str
    old_value: Any
    new_value: Any


class CompareVersionsResponse(BaseModel):
    """Response with version comparison."""
    version_a: Optional[int] = None
    version_b: Optional[int] = None
    differences: list[ConfigDiff]
    summary: str


class RollbackRequest(BaseModel):
    """Request to rollback to a previous version."""
    version_id: UUID = Field(..., description="Version ID to rollback to")
    reason: Optional[str] = Field(None, description="Reason for rollback")


class RollbackResponse(BaseModel):
    """Response after rollback."""
    success: bool
    from_version: Optional[int] = None
    to_version: Optional[int] = None
    message: str
