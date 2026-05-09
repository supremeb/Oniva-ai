"""Schemas for Inline Feedback API (Phase E)."""
from typing import Optional
from pydantic import BaseModel, Field


class FeedbackCreate(BaseModel):
    """Submit feedback on an agent response."""
    agent_id: Optional[str] = Field(None, description="Agent UUID (null for general chat)")
    session_id: Optional[str] = Field(None, description="Chat session reference")
    message_index: Optional[int] = Field(None, ge=0, description="Position in conversation")
    rating: str = Field(..., pattern="^(positive|negative)$", description="positive or negative")
    feedback_text: Optional[str] = Field(None, max_length=2000, description="Optional text")
    agent_response_summary: Optional[str] = Field(None, max_length=500, description="Truncated response")


class FeedbackResponse(BaseModel):
    """Feedback in API responses."""
    id: str
    tenant_id: str
    agent_id: Optional[str] = None
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    message_index: Optional[int] = None
    rating: str
    feedback_text: Optional[str] = None
    agent_response_summary: Optional[str] = None
    created_at: Optional[str] = None


class FeedbackListResponse(BaseModel):
    """Paginated feedback list."""
    feedback: list[FeedbackResponse]
    count: int


class FeedbackSummaryResponse(BaseModel):
    """Aggregated feedback summary for an agent."""
    agent_id: Optional[str] = None
    total: int
    positive: int
    negative: int
    satisfaction_rate: Optional[float] = None
    recent_negative_texts: list[str] = Field(default_factory=list, description="Last 5 negative feedback texts")
