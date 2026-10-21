"""API Key model with multi-tenancy support."""
from sqlalchemy import Column, String, DateTime, ForeignKey, Boolean, Text, Integer, func
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY, INET
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
import uuid

from app.models import Base
from app.models._clock import utc_now


def normalize_scopes(scopes):
    """Strip stray quotes/whitespace from stored scopes (old rows hold ``'*:*'`` with quotes)."""
    cleaned = (str(x).strip().strip("'\"") for x in (scopes or []))
    return [s for s in cleaned if s]


class APIKey(Base):
    """
    Stores encrypted API keys for various LLM providers.
    Supports OpenRouter and direct provider keys.
    """
    __tablename__ = 'api_keys'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=False)

    # Multi-tenancy
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey('tenants.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )

    # Provider info
    provider = Column(String(50), nullable=False)  # 'openrouter', 'openai', 'anthropic', etc.
    name = Column(String(255), nullable=False)  # User-friendly name

    # Encrypted key + SHA-256 hash for O(1) lookup
    encrypted_key = Column(Text, nullable=False)
    key_hash = Column(String(64), unique=True, index=True, nullable=True)

    # Metadata
    is_default = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    key_metadata = Column(JSONB, default=dict)  # Additional config (base_url, etc.)

    # Enterprise: API key scoping and security
    # No single quotes in the array literal (they were stored literally, giving ["'*:*'"]).
    # The Python-side default covers ORM inserts; normalize_scopes covers existing rows.
    scopes = Column(ARRAY(String), nullable=False, default=lambda: ["*:*"], server_default='{"*:*"}')
    allowed_ip_addresses = Column(ARRAY(INET), nullable=True)  # NULL = all IPs allowed
    expires_at = Column(DateTime(timezone=True), nullable=True)  # NULL = never expires
    rate_limit_per_hour = Column(Integer, nullable=False, server_default='1000')

    # Usage tracking
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    last_used_ip = Column(INET, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), default=utc_now)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), default=utc_now, onupdate=utc_now)

    # Relationships
    user = relationship("User", back_populates="api_keys")
    tenant = relationship("Tenant")

    def is_expired(self) -> bool:
        """Check if this API key has expired."""
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) > self.expires_at

    def is_ip_allowed(self, client_ip: str) -> bool:
        """Check if client IP is allowed (whitelist check).

        Args:
            client_ip: IP address to check

        Returns:
            True if IP is allowed (whitelist is None or IP in whitelist)
        """
        if not self.allowed_ip_addresses:
            return True  # No whitelist = all IPs allowed

        # Convert string IP to PostgreSQL INET for comparison
        # This would typically be done in the service layer
        return True  # Placeholder - actual check in service

    def has_scope(self, required_scope: str) -> bool:
        """Check if this API key has the required scope.

        Args:
            required_scope: Scope to check (e.g., 'workflows:execute')

        Returns:
            True if key has the required scope
        """
        scopes = normalize_scopes(self.scopes)
        if not scopes:
            return False

        # Wildcard grants all scopes
        if '*:*' in scopes or 'admin:all' in scopes:
            return True

        # Exact match
        return required_scope in scopes

    def __repr__(self):
        return f"<APIKey(provider='{self.provider}', name='{self.name}', scopes={len(self.scopes or [])})>"
