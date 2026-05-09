"""Schemas for Org Admin Dashboard API (Phase D)."""
from typing import Optional
from pydantic import BaseModel, Field


# ============================================================================
# Overview
# ============================================================================

class OrgOverviewResponse(BaseModel):
    """High-level org dashboard metrics."""
    total_agents: int
    active_agents: int
    total_users: int
    total_teams: int
    executions_30d: int
    total_feedback_30d: int
    satisfaction_rate: Optional[float] = Field(None, description="% positive feedback (0-100)")


# ============================================================================
# Agent Fleet
# ============================================================================

class AgentFleetItem(BaseModel):
    """Single agent in the fleet list."""
    id: str
    name: str
    description: Optional[str] = None
    is_active: bool
    model: Optional[str] = None
    feedback_total: int = 0
    feedback_positive: int = 0
    satisfaction_rate: Optional[float] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class AgentFleetResponse(BaseModel):
    """Paginated agent fleet list."""
    agents: list[AgentFleetItem]
    total: int
    limit: int
    offset: int


# ============================================================================
# Agent Detail
# ============================================================================

class FeedbackSummary(BaseModel):
    """Feedback stats for an agent."""
    total: int
    positive: int
    negative: int
    satisfaction_rate: Optional[float] = None


class AgentDetailResponse(BaseModel):
    """Detailed metrics for a single agent."""
    agent: dict
    feedback_summary: FeedbackSummary
    recent_feedback: list[dict]
    permissions: list[dict]


# ============================================================================
# Permissions
# ============================================================================

class PermissionGrantRequest(BaseModel):
    """Request to grant a permission."""
    agent_id: str = Field(..., description="Agent UUID")
    permission_type: str = Field(..., description="edit_instructions | view_logs | deploy")
    team_id: str = Field(..., description="Team UUID")


class PermissionResponse(BaseModel):
    """Permission in API responses."""
    id: str
    tenant_id: str
    agent_id: str
    permission_type: str
    team_id: str
    created_at: Optional[str] = None
