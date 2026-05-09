"""Pydantic schemas for workflow logs API."""
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime


class ExecutionTraceStep(BaseModel):
    """A single step in the execution trace."""
    step_id: str
    node_id: str
    node_type: str
    node_name: Optional[str] = None
    status: str = Field(description="pending, running, completed, failed, skipped")
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_ms: Optional[int] = None
    input: Optional[Dict[str, Any]] = None
    output: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    token_usage: Optional[Dict[str, int]] = None
    reasoning: Optional[str] = None


class WorkflowLogSummary(BaseModel):
    """Summary of a workflow execution log for list views."""
    id: str
    workflow_id: str
    workflow_version_id: Optional[str] = Field(None, description="UUID of version that was executed (NULL for legacy logs)")
    version_number: Optional[int] = Field(None, description="Version number for display (denormalized)")
    status: str = Field(description="pending, running, completed, failed, cancelled")
    trigger: str = Field(description="manual, api, scheduled")
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_ms: Optional[int] = None
    total_tokens: int = 0
    nodes_executed: int = 0
    error: Optional[str] = None
    progress: int = 0


class WorkflowLogListResponse(BaseModel):
    """Response for paginated workflow log list with cursor-based pagination."""
    logs: List[WorkflowLogSummary]
    next_cursor: Optional[str] = Field(
        None,
        description="Cursor for next page (created_at:id format). None if no more pages."
    )
    limit: int
    has_more: bool


class WorkflowLogDetailResponse(BaseModel):
    """Detailed response for a single workflow execution log."""
    id: str
    workflow_id: str
    workflow_version_id: Optional[str] = Field(None, description="UUID of version that was executed")
    version_number: Optional[int] = Field(None, description="Version number for display")
    status: str
    trigger: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_ms: Optional[int] = None
    input_data: Optional[Dict[str, Any]] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    execution_trace: List[ExecutionTraceStep] = []
    total_tokens: int = 0
    nodes_executed: int = 0
    progress: int = 0


class LogStreamEvent(BaseModel):
    """Event sent via SSE for real-time log updates."""
    event_type: str = Field(description="log_update, progress, completed, failed, heartbeat")
    log_id: str
    workflow_id: str
    status: Optional[str] = None
    progress: Optional[int] = None
    step: Optional[ExecutionTraceStep] = None
    error: Optional[str] = None
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class MonitoringMetrics(BaseModel):
    """Aggregated monitoring metrics for the tenant."""
    time_range: str
    total_executions: int
    successful: int
    failed: int
    running: int
    success_rate: float
    avg_duration_ms: Optional[int] = None


class TopWorkflow(BaseModel):
    """Workflow ranked by execution count."""
    workflow_id: str
    workflow_name: Optional[str] = None
    execution_count: int
    failure_count: int
    success_rate: float = 0.0


class TopWorkflowsResponse(BaseModel):
    """Response for top workflows by execution."""
    workflows: List[TopWorkflow]


class RecentFailure(BaseModel):
    """A recent failed execution."""
    id: str
    workflow_id: str
    workflow_name: Optional[str] = None
    error: str
    failed_at: str
    trigger: str


class RecentFailuresResponse(BaseModel):
    """Response for recent failures list."""
    failures: List[RecentFailure]
