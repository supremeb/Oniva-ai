"""Pydantic schemas for routing policy CRUD."""
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from uuid import UUID


class RoutingPolicyCreate(BaseModel):
    name: str = Field(..., max_length=255)
    task_type: str = Field(..., max_length=50, description="research | analysis | code_gen | extraction | synthesis | browser")
    output_type: Optional[str] = Field(None, max_length=50, description="pdf_report | presentation | spreadsheet | dashboard | chart | document | data_export")
    model_id: str = Field(..., max_length=255, description="e.g. anthropic/claude-sonnet-4-5")
    provider: str = Field(..., max_length=50, description="openai | anthropic | openrouter")
    priority: int = Field(0)
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    is_active: bool = True


class RoutingPolicyUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=255)
    task_type: Optional[str] = Field(None, max_length=50)
    output_type: Optional[str] = Field(None, max_length=50)
    model_id: Optional[str] = Field(None, max_length=255)
    provider: Optional[str] = Field(None, max_length=50)
    priority: Optional[int] = None
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    is_active: Optional[bool] = None


class RoutingPolicyResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    name: str
    task_type: str
    output_type: Optional[str]
    model_id: str
    provider: str
    priority: int
    max_tokens: Optional[int]
    temperature: Optional[float]
    is_active: bool
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

    model_config = {"from_attributes": True}
