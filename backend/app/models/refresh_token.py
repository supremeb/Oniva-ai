"""RefreshToken model for JWT refresh token management."""
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, String, Boolean, func, Index
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import relationship
import uuid

from app.models import Base
from app.models._clock import utc_now


class RefreshToken(Base):
    """
    RefreshToken stores refresh tokens for JWT authentication.

    Implements secure refresh token rotation:
    - Tokens are hashed (never stored in plain text)
    - Tokens can be revoked individually or by user/family
    - Token families support rotation detection (reuse = compromise)
    - Tokens have configurable expiration (default: 7 days)
    """
    __tablename__ = "refresh_tokens"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # User this token belongs to
    user_id = Column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="User this refresh token belongs to"
    )

    # Tenant for RLS
    tenant_id = Column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Tenant for row-level security"
    )

    # Token (hashed for security - SHA256)
    token_hash = Column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
        comment="SHA256 hash of the refresh token"
    )

    # Token family for rotation detection
    # When a token is rotated, the new token gets the same family_id
    # If an old token in a family is reused, revoke the entire family
    family_id = Column(
        PGUUID(as_uuid=True),
        nullable=False,
        default=uuid.uuid4,
        index=True,
        comment="Token family ID for rotation tracking"
    )

    # Expiration
    expires_at = Column(
        DateTime(timezone=True),
        nullable=False,
        comment="When this token expires"
    )

    # Revocation tracking
    revoked = Column(
        Boolean,
        nullable=False,
        default=False,
        comment="Whether this token has been revoked"
    )

    revoked_at = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="When this token was revoked"
    )

    revoked_reason = Column(
        String(255),
        nullable=True,
        comment="Reason for revocation (logout, rotation, security)"
    )

    # Usage tracking
    last_used_at = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="Last time this token was used"
    )

    # Client info for security monitoring
    user_agent = Column(
        String(512),
        nullable=True,
        comment="User agent of the client that created this token"
    )

    ip_address = Column(
        String(45),
        nullable=True,
        comment="IP address of the client that created this token"
    )

    # Timestamps
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=utc_now
    )

    # Relationships
    user = relationship("User", backref="refresh_tokens")
    tenant = relationship("Tenant")

    # Indexes for common queries
    __table_args__ = (
        Index('ix_refresh_tokens_user_active', 'user_id', 'revoked'),
        Index('ix_refresh_tokens_family_active', 'family_id', 'revoked'),
        Index('ix_refresh_tokens_expires', 'expires_at'),
    )

    def __repr__(self) -> str:
        return f"<RefreshToken(id={self.id}, user_id={self.user_id}, revoked={self.revoked})>"

    @property
    def is_expired(self) -> bool:
        """Check if token has expired."""
        if self.expires_at.tzinfo is None:
            return datetime.utcnow() > self.expires_at
        return datetime.utcnow().replace(tzinfo=self.expires_at.tzinfo) > self.expires_at

    @property
    def is_valid(self) -> bool:
        """Check if token is valid (not expired and not revoked)."""
        return not self.is_expired and not self.revoked

    def revoke(self, reason: str = "manual") -> None:
        """Revoke this token."""
        self.revoked = True
        self.revoked_at = datetime.utcnow()
        self.revoked_reason = reason

    def mark_used(self) -> None:
        """Update last used timestamp."""
        self.last_used_at = datetime.utcnow()
