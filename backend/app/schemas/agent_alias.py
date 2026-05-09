"""Pydantic schemas for AgentAlias API."""
from typing import Optional
from pydantic import BaseModel, Field
from datetime import datetime
from uuid import UUID


class AliasCreateOrUpdate(BaseModel):
    """Request to create or update an alias."""
    version_id: UUID = Field(..., description="Version ID to point the alias to")


class AliasResponse(BaseModel):
    """Response for an agent alias."""
    id: UUID
    tenant_id: UUID
    agent_id: UUID
    alias_name: str
    version_id: UUID
    version_number: Optional[int] = None
    version_name: Optional[str] = None
    updated_by: Optional[UUID] = None
    updated_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class AliasListResponse(BaseModel):
    """Response for listing aliases."""
    aliases: list[AliasResponse]
    total: int
