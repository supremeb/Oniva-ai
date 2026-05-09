from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from datetime import datetime
from uuid import UUID


class ConnectorBase(BaseModel):
    connector_type: str = Field(..., description="Type: mcp, rest_api, graphql, etc.")
    provider: str = Field(..., description="Provider: confluence, jira, custom, etc.")
    name: str = Field(..., description="User-friendly name")
    description: Optional[str] = None
    config: Dict[str, Any] = Field(..., description="Connector-specific configuration")


class ConnectorCreate(ConnectorBase):
    credentials: Optional[Dict[str, str]] = Field(None, description="Authentication credentials (will be encrypted)")


class ConnectorUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    credentials: Optional[Dict[str, str]] = None
    is_active: Optional[bool] = None


class ConnectorResponse(ConnectorBase):
    id: UUID
    user_id: UUID
    is_active: bool
    is_verified: bool
    last_verified_at: Optional[datetime]
    metadata: Dict[str, Any]
    created_at: datetime
    updated_at: datetime

    # Don't expose credentials
    has_credentials: bool = Field(default=False, description="Whether credentials are configured")

    class Config:
        from_attributes = True


class ConnectorListResponse(BaseModel):
    connectors: list[ConnectorResponse]
    total: int


class ConnectorTestRequest(BaseModel):
    """Request to test a connector connection"""
    test_query: Optional[str] = Field(None, description="Optional test query to run")


class ConnectorTestResponse(BaseModel):
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
