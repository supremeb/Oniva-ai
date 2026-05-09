"""
Pydantic schemas for WorkflowSession API.

Part of Phase 2: Filesystem Sessions.
"""
from typing import Optional, Dict, Any, Literal
from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime
from uuid import UUID


# Valid session status values
SessionStatusType = Literal["active", "paused", "completed", "archived", "error"]


class WorkflowSessionBase(BaseModel):
    """Base schema with fields shared across create/update/response."""
    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Session name",
        examples=["Q4 Financial Analysis Session"]
    )
    description: Optional[str] = Field(
        None,
        max_length=2000,
        description="Optional session description"
    )
    workflow_id: Optional[UUID] = Field(
        None,
        description="Associated workflow definition ID"
    )


class WorkflowSessionCreate(WorkflowSessionBase):
    """Schema for creating a new workflow session."""
    session_metadata: Optional[Dict[str, Any]] = Field(
        default_factory=dict,
        description="Session metadata (agent config, model settings, etc.)"
    )


class WorkflowSessionUpdate(BaseModel):
    """
    Schema for updating a workflow session.

    All fields are optional - only provided fields will be updated.
    """
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=2000)
    status: Optional[SessionStatusType] = None
    current_state: Optional[str] = Field(
        None,
        max_length=50,
        description="Current workflow state"
    )
    session_metadata: Optional[Dict[str, Any]] = None


class WorkflowSessionResponse(WorkflowSessionBase):
    """Schema for workflow session API responses."""
    id: UUID
    tenant_id: UUID
    user_id: UUID
    status: SessionStatusType
    filesystem_path: str = Field(..., description="Relative path to session files")

    # State tracking
    current_state: Optional[str] = None
    last_checkpoint_id: Optional[UUID] = None

    # Resource usage
    total_size_bytes: int = 0
    file_count: int = 0

    # Context tracking
    context_window_used: int = 0
    context_window_max: int = 128000

    # Cost tracking
    total_tokens_used: int = 0
    total_cost_usd: int = Field(0, description="Cost in cents (avoid float precision)")

    # Metadata
    session_metadata: Dict[str, Any] = Field(default_factory=dict)

    # Timestamps
    created_at: datetime
    updated_at: datetime
    last_activity_at: datetime
    archived_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class WorkflowSessionListResponse(BaseModel):
    """Paginated list of workflow sessions."""
    items: list[WorkflowSessionResponse]
    total: int = Field(..., description="Total number of sessions")
    page: int = Field(..., description="Current page number")
    page_size: int = Field(..., description="Items per page")


class WorkflowSessionDetailResponse(WorkflowSessionResponse):
    """Detailed session response with workflow info."""
    workflow_name: Optional[str] = None
    workflow_description: Optional[str] = None


class SessionFileInfo(BaseModel):
    """Information about a file in a session."""
    name: str
    path: str
    size_bytes: int
    mime_type: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class SessionFilesResponse(BaseModel):
    """List of files in a session."""
    session_id: UUID
    files: list[SessionFileInfo]
    total_size_bytes: int


class SessionContextResponse(BaseModel):
    """Current context state for a session."""
    session_id: UUID
    working_memory: Dict[str, Any] = Field(
        default_factory=dict,
        description="Active working memory items"
    )
    context_window_used: int
    context_window_max: int
    context_utilization: float = Field(
        ...,
        ge=0,
        le=1,
        description="Utilization ratio (used/max)"
    )


class SessionCheckpointResponse(BaseModel):
    """Information about a session checkpoint."""
    checkpoint_id: UUID
    session_id: UUID
    step_number: int
    step_name: str
    status: str
    created_at: datetime
    can_resume: bool = True


class SessionCheckpointListResponse(BaseModel):
    """List of checkpoints for a session."""
    session_id: UUID
    checkpoints: list[SessionCheckpointResponse]
    latest_checkpoint_id: Optional[UUID] = None
