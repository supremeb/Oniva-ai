"""Connector model with multi-tenancy support."""
from sqlalchemy import Column, String, DateTime, ForeignKey, Boolean, Text, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from app.models import Base


class Connector(Base):
    """
    Stores connector configurations for external data sources.
    Supports MCP connectors (Confluence, Jira, etc.) and custom APIs.
    """
    __tablename__ = 'connectors'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=False)

    # Multi-tenancy
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey('tenants.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )

    # Connector info
    connector_type = Column(String(50), nullable=False)  # 'mcp', 'rest_api', 'graphql', etc.
    provider = Column(String(100), nullable=False)  # 'confluence', 'jira', 'custom', etc.
    name = Column(String(255), nullable=False)  # User-friendly name
    description = Column(Text, nullable=True)

    # Configuration
    config = Column(JSONB, nullable=False)  # Connector-specific config
    # For MCP: {mcp_server: str, transport: str, args: dict}
    # For REST: {base_url: str, auth_type: str, headers: dict}

    # Credentials (encrypted)
    encrypted_credentials = Column(Text, nullable=True)  # Encrypted auth tokens, API keys

    # Status
    is_active = Column(Boolean, default=True)
    is_verified = Column(Boolean, default=False)  # Has connection been tested?
    last_verified_at = Column(DateTime(timezone=True), nullable=True)

    # Metadata
    connector_metadata = Column(JSONB, default=dict)  # Additional metadata

    # OpenAPI Connector Factory fields
    source_type = Column(String(20), nullable=False, default="native")  # "native", "curated", "custom"
    registry_id = Column(String(100), nullable=True)  # ID in curated registry (e.g., "stripe")
    openapi_spec_url = Column(String(500), nullable=True)  # URL to fetch OpenAPI spec
    openapi_spec_version = Column(String(20), nullable=True)  # OpenAPI version (e.g., "3.0.3")
    openapi_spec_hash = Column(String(64), nullable=True)  # SHA256 hash for change detection
    openapi_operations = Column(JSONB, default=list)  # Cached tool definitions from spec

    created_at = Column(DateTime(timezone=True), server_default=func.now(), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="connectors")
    tenant = relationship("Tenant")

    def __repr__(self):
        return f"<Connector(provider='{self.provider}', name='{self.name}')>"
