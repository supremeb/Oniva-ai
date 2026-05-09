"""Pydantic schemas for Agent Learning System API."""
import re
from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AgentLearningResponse(BaseModel):
    """Response schema for a single agent learning."""
    id: UUID
    tenant_id: UUID
    agent_id: Optional[UUID] = None
    title: str
    content: str
    pattern_type: Optional[str] = None
    source: str
    source_session_id: Optional[UUID] = None
    source_error: Optional[str] = None
    status: str
    content_hash: str
    created_at: datetime
    last_used_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class AgentLearningListResponse(BaseModel):
    """Paginated list of agent learnings."""
    items: list[AgentLearningResponse]
    total: int
    limit: int
    offset: int


class AgentLearningUpdate(BaseModel):
    """Request schema for updating a learning's status."""
    status: Literal["active", "archived"] = Field(
        ..., description="New status for the learning"
    )


class SaveLearningRequest(BaseModel):
    """Request schema for manually saving a learning."""
    title: str = Field(..., min_length=1, max_length=255, description="Short title")
    content: str = Field(..., min_length=1, max_length=5000, description="Full explanation")
    agent_id: Optional[UUID] = Field(None, description="Agent scope (null = tenant-wide)")
    pattern_type: Literal[
        "data_quality", "api_error", "calculation", "domain_insight", "general"
    ] = Field("general", description="Category of the learning")

    @field_validator('title', 'content')
    @classmethod
    def sanitize_xss(cls, v: str) -> str:
        """Prevent XSS in user-provided content."""
        v = re.sub(r'<script[^>]*>.*?</script>', '', v, flags=re.IGNORECASE | re.DOTALL)
        v = re.sub(r'<[^>]+>', '', v)
        return v.strip()


class SaveLearningResponse(BaseModel):
    """Response after saving a learning."""
    id: Optional[UUID] = Field(None, description="Learning ID (null if duplicate)")
    status: str = Field(..., description="'stored' or 'duplicate'")
