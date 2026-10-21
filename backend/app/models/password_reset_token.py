"""PasswordResetToken model for password reset functionality."""
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import relationship
import uuid

from app.models import Base
from app.models._clock import utc_now


class PasswordResetToken(Base):
    """
    PasswordResetToken stores tokens for password reset functionality.

    Currently stubbed - will be used when Resend email integration is added.
    For now, admins can reset passwords directly.
    """
    __tablename__ = "password_reset_tokens"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # User this token belongs to
    user_id = Column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="User this reset token is for"
    )

    # Token (hashed for security)
    token_hash = Column(
        String(255),
        nullable=False,
        comment="Hashed reset token"
    )

    # Expiration
    expires_at = Column(
        DateTime(timezone=True),
        nullable=False,
        comment="When this token expires"
    )

    # Usage tracking
    used_at = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="When this token was used (null if not used)"
    )

    # Timestamps
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=utc_now
    )

    # Relationships
    user = relationship("User", backref="password_reset_tokens")

    def __repr__(self) -> str:
        return f"<PasswordResetToken(id={self.id}, user_id={self.user_id}, expires_at={self.expires_at})>"

    @property
    def is_expired(self) -> bool:
        """Check if token has expired."""
        return datetime.utcnow() > self.expires_at.replace(tzinfo=None)

    @property
    def is_used(self) -> bool:
        """Check if token has been used."""
        return self.used_at is not None

    @property
    def is_valid(self) -> bool:
        """Check if token is valid (not expired and not used)."""
        return not self.is_expired and not self.is_used
