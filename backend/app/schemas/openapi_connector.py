"""
Pydantic Schemas for OpenAPI Connector Marketplace

Request/response models for the marketplace API endpoints.
"""

from pydantic import BaseModel, Field, HttpUrl
from typing import Any
from datetime import datetime


# ========== Marketplace Schemas ==========

class ConnectorCategory(BaseModel):
    """Category in the connector marketplace."""
    id: str
    name: str
    icon: str
    description: str
    connector_count: int = 0


class MarketplaceConnector(BaseModel):
    """Connector listing in the marketplace."""
    id: str
    name: str
    description: str
    category: str
    auth_type: str
    icon: str = "🔌"
    docs_url: str | None = None
    operation_count: int | None = None
    is_enabled: bool = False


class MarketplaceConnectorDetail(MarketplaceConnector):
    """Detailed connector info with sample tools."""
    spec_url: str | None = None
    sample_tools: list[dict[str, Any]] = Field(default_factory=list)
    required_credentials: list[str] = Field(default_factory=list)


# ========== Enable/Configure Schemas ==========

class EnableConnectorRequest(BaseModel):
    """Request to enable a marketplace connector."""
    credentials: dict[str, str] = Field(
        ...,
        description="Credentials based on auth_type: api_key, token, username/password, etc.",
        examples=[{"api_key": "sk_test_..."}],
    )
    selected_operations: list[str] | None = Field(
        default=None,
        description="Specific operation IDs to enable. None = all operations.",
    )
    name: str | None = Field(
        default=None,
        description="Custom display name for the connector.",
    )


class EnableConnectorResponse(BaseModel):
    """Response after enabling a connector."""
    id: str = Field(..., description="UUID of the enabled connector instance")
    connector_id: str = Field(..., description="Registry connector ID (e.g., 'stripe')")
    name: str
    status: str = "enabled"
    operation_count: int
    message: str = "Connector enabled successfully"


# ========== Import Custom Spec Schemas ==========

class ImportOpenAPIRequest(BaseModel):
    """Request to import a custom OpenAPI spec."""
    name: str = Field(..., min_length=1, max_length=255)
    spec_url: HttpUrl | None = Field(
        default=None,
        description="URL to fetch OpenAPI spec from",
    )
    spec_content: dict[str, Any] | None = Field(
        default=None,
        description="Inline OpenAPI spec (alternative to spec_url)",
    )
    auth_type: str = Field(
        default="bearer",
        description="Authentication type: bearer, api_key, basic, oauth2",
    )
    auth_location: str = Field(
        default="header",
        description="Where to send auth: header, query, cookie",
    )
    auth_header_name: str = Field(
        default="Authorization",
        description="Header/param name for auth",
    )
    category: str = Field(default="custom")

    def model_post_init(self, __context: Any) -> None:
        """Validate that either spec_url or spec_content is provided."""
        if not self.spec_url and not self.spec_content:
            raise ValueError("Either spec_url or spec_content must be provided")


class ImportOpenAPIResponse(BaseModel):
    """Response after importing a custom spec."""
    id: str
    name: str
    description: str | None
    operation_count: int
    status: str = "imported"
    message: str = "Custom connector imported successfully"


# ========== Operations Schemas ==========

class ConnectorOperation(BaseModel):
    """Single operation/tool from a connector."""
    name: str
    description: str
    method: str
    path: str
    parameters: list[dict[str, Any]] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class ConnectorOperationsResponse(BaseModel):
    """Response listing connector operations."""
    connector_id: str
    connector_name: str
    operations: list[ConnectorOperation]
    total_count: int


# ========== Test Connection Schemas ==========

class TestConnectionRequest(BaseModel):
    """Request to test connector connection."""
    credentials: dict[str, str] | None = Field(
        default=None,
        description="Test with these credentials (uses stored if not provided)",
    )


class TestConnectionResponse(BaseModel):
    """Response from connection test."""
    status: str = Field(..., description="healthy, unhealthy, or unauthenticated")
    message: str
    latency_ms: float | None = None
    tested_at: datetime = Field(default_factory=datetime.utcnow)


# ========== Refresh Spec Schemas ==========

class RefreshSpecRequest(BaseModel):
    """Request to refresh connector spec from URL."""
    force: bool = Field(
        default=False,
        description="Force refresh even if spec unchanged",
    )


class RefreshSpecResponse(BaseModel):
    """Response from spec refresh."""
    status: str = Field(..., description="refreshed, unchanged, or error")
    message: str
    new_operation_count: int | None = None
    previous_operation_count: int | None = None


# ========== Enabled Connectors Schemas ==========

class EnabledConnector(BaseModel):
    """An enabled connector instance."""
    id: str  # UUID
    connector_id: str  # Registry ID (e.g., "stripe")
    name: str
    description: str | None
    category: str
    icon: str
    auth_type: str
    source_type: str = "curated"  # native, curated, custom
    operation_count: int
    is_active: bool
    is_verified: bool
    last_verified_at: datetime | None
    created_at: datetime
    updated_at: datetime


class EnabledConnectorsList(BaseModel):
    """List of enabled connectors."""
    connectors: list[EnabledConnector]
    total_count: int
