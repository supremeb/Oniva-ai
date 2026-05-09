"""Pydantic schemas for BackgroundJob API."""
from typing import Optional, List, Dict, Any, Literal
from pydantic import BaseModel, Field
from datetime import datetime
from uuid import UUID
from enum import Enum


class JobStatus(str, Enum):
    """Background job status enum."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobType(str, Enum):
    """Background job type enum."""
    WORKFLOW_EXECUTION = "workflow_execution"
    AGENT_CHAT = "agent_chat"
    BATCH_PROCESSING = "batch_processing"
    DEEP_WORK = "deep_work"
    BACKGROUND_AGENT = "background_agent"


class ExecutionTraceStep(BaseModel):
    """Schema for a single step in execution trace."""
    step_id: str = Field(..., description="Unique step identifier")
    node_id: Optional[str] = Field(None, description="Workflow node ID")
    node_type: Optional[str] = Field(None, description="Type of node (agent, tool, etc.)")
    status: Literal["pending", "running", "completed", "failed", "skipped"] = "pending"
    input: Optional[Dict[str, Any]] = None
    output: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    reasoning: Optional[str] = Field(None, description="AI reasoning for this step")


class BackgroundJobBase(BaseModel):
    """Base schema for background jobs."""
    job_type: JobType = Field(JobType.WORKFLOW_EXECUTION, description="Type of background job")
    input_data: Optional[Dict[str, Any]] = Field(None, description="Input parameters for the job")


class BackgroundJobCreate(BackgroundJobBase):
    """Schema for creating a new background job."""
    workflow_id: Optional[UUID] = Field(None, description="Associated workflow ID")
    agent_id: Optional[UUID] = Field(None, description="Associated agent ID")


class BackgroundJobUpdate(BaseModel):
    """Schema for updating a background job (internal use)."""
    status: Optional[JobStatus] = None
    progress: Optional[int] = Field(None, ge=0, le=100)
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class BackgroundJobResponse(BaseModel):
    """Schema for background job API responses."""
    id: UUID
    tenant_id: UUID
    user_id: UUID
    workflow_id: Optional[UUID] = None
    workflow_version_id: Optional[UUID] = None
    version_number: Optional[int] = Field(None, description="Denormalized version number for convenience")
    agent_id: Optional[UUID] = None
    job_type: str
    status: str
    progress: int = 0
    input_data: Optional[Dict[str, Any]] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    execution_trace: List[Dict[str, Any]] = Field(default_factory=list)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime
    duration_ms: Optional[int] = None

    class Config:
        from_attributes = True


class BackgroundJobListResponse(BaseModel):
    """Schema for paginated background job list."""
    jobs: List[BackgroundJobResponse]
    total: int
    page: int = 1
    page_size: int = 20


class BackgroundJobDetailResponse(BackgroundJobResponse):
    """Schema for detailed background job with context info."""
    workflow_name: Optional[str] = None
    agent_name: Optional[str] = None


class CancelJobRequest(BaseModel):
    """Request to cancel a running job."""
    reason: Optional[str] = Field(None, description="Reason for cancellation")


class CancelJobResponse(BaseModel):
    """Response after cancelling a job."""
    success: bool
    job_id: UUID
    status: str
    message: str


class JobProgressUpdate(BaseModel):
    """Schema for real-time job progress updates (SSE)."""
    job_id: UUID
    status: str
    progress: int
    current_step: Optional[ExecutionTraceStep] = None
    message: Optional[str] = None
    timestamp: datetime


class JobCompletedEvent(BaseModel):
    """Schema for job completion event (SSE)."""
    job_id: UUID
    status: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    duration_ms: int
    execution_trace: List[Dict[str, Any]]
    completed_at: datetime


class JobsFilterParams(BaseModel):
    """Query parameters for filtering jobs."""
    status: Optional[JobStatus] = None
    job_type: Optional[JobType] = None
    workflow_id: Optional[UUID] = None
    agent_id: Optional[UUID] = None
    since: Optional[datetime] = Field(None, description="Jobs created after this time")
    until: Optional[datetime] = Field(None, description="Jobs created before this time")
