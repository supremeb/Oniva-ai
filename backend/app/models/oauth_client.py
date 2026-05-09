"""OAuth Client model for machine-to-machine authentication.

Enterprise feature: Enable OAuth 2.0 Client Credentials flow for
programmatic API access without user interaction.

- Phase 2: API Integration
"""
from sqlalchemy import Column, String, DateTime, ForeignKey, Boolean, Text, func
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid
import secrets

from app.models import Base


def generate_client_id() -> str:
    """Generate a secure client ID."""
    return f"fia_{secrets.token_urlsafe(24)}"


def generate_client_secret() -> str:
    """Generate a secure client secret."""
    return secrets.token_urlsafe(48)


class OAuthClient(Base):
    """
    OAuth 2.0 client for machine-to-machine authentication.

    Supports the Client Credentials grant type for programmatic API access.
    Clients are scoped to a tenant and have configurable permissions.

    Security notes:
    - client_secret_hash is stored using bcrypt, never plain text
    - Scopes restrict what the client can access
    - Clients can be deactivated without deletion for audit trails
    """
    __tablename__ = 'oauth_clients'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Multi-tenancy
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey('tenants.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )

    # Client credentials
    client_id = Column(String(64), unique=True, nullable=False, index=True)
    client_secret_hash = Column(String(256), nullable=False)

    # Client metadata
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)

    # Permissions
    scopes = Column(ARRAY(String), default=list, nullable=False)

    # Status
    is_active = Column(Boolean, default=True, nullable=False)

    # Audit trail
    created_by = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    last_used_ip = Column(String(45), nullable=True)  # IPv6 max length

    created_at = Column(DateTime(timezone=True), server_default=func.now(), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    tenant = relationship("Tenant")
    creator = relationship("User", foreign_keys=[created_by])

    # Available scopes for clients
    VALID_SCOPES = {
        "workflows:read",      # Read workflow definitions
        "workflows:write",     # Create/update workflows
        "workflows:execute",   # Execute workflows
        "workflows:delete",    # Delete workflows
        "agents:read",         # Read agent definitions
        "agents:write",        # Create/update agents
        "connectors:read",     # Read connector configs
        "connectors:write",    # Manage connectors
        "batches:write",       # Create batch executions
        "webhooks:write",      # Configure webhooks
    }

    def __repr__(self):
        return f"<OAuthClient(name='{self.name}', client_id='{self.client_id[:12]}...')>"

    def has_scope(self, scope: str) -> bool:
        """Check if client has a specific scope."""
        return scope in (self.scopes or [])

    def has_any_scope(self, scopes: list[str]) -> bool:
        """Check if client has any of the specified scopes."""
        client_scopes = set(self.scopes or [])
        return bool(client_scopes.intersection(scopes))
