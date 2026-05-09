"""User Preferences model for agent personalization.

Part of Enhanced Agent Memory System feature.
Stores per-user preferences for agent behavior customization.
"""
from datetime import datetime
from typing import TYPE_CHECKING, Any
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, func, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB
from sqlalchemy.orm import relationship

from app.models import Base

if TYPE_CHECKING:
    pass


class UserPreferences(Base):
    """
    User preferences for agent personalization.

    Stores JSONB preferences with schema versioning for migration support.
    Preferences are cached in Redis for fast retrieval.

    Security:
    - RLS enforces user-level isolation (tenant_id + user_id)
    - Preferences validated against Pydantic schema before storage
    """
    __tablename__ = "user_preferences"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Multi-tenancy with user-level isolation
    tenant_id = Column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Tenant this preferences belong to"
    )
    user_id = Column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="User these preferences belong to"
    )

    # Preferences (JSONB for flexibility)
    preferences = Column(
        JSONB,
        nullable=False,
        server_default='{}',
        comment="User preferences as JSONB"
    )

    # Schema version for migrations
    schema_version = Column(
        Integer,
        server_default='1',
        comment="Schema version for preference migration"
    )

    # Timestamps
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=datetime.utcnow
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )

    # Relationships
    tenant = relationship("Tenant", back_populates="user_preferences")
    user = relationship("User", back_populates="user_preferences")

    # Constraints
    __table_args__ = (
        UniqueConstraint('tenant_id', 'user_id', name='uq_user_preferences_tenant_user'),
    )

    def __repr__(self) -> str:
        return f"<UserPreferences(id={self.id}, user_id={self.user_id})>"

    def to_dict(self) -> dict:
        """Convert preferences to dictionary for API responses."""
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "user_id": str(self.user_id),
            "preferences": self.preferences or {},
            "schema_version": self.schema_version,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def get_preference(self, key: str, default: Any = None) -> Any:
        """Get a specific preference value by dot-notation key."""
        keys = key.split('.')
        value = self.preferences or {}
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        return value

    def set_preference(self, key: str, value: Any) -> None:
        """Set a specific preference value by dot-notation key."""
        keys = key.split('.')
        prefs = self.preferences or {}

        # Navigate to parent
        current = prefs
        for k in keys[:-1]:
            if k not in current or not isinstance(current[k], dict):
                current[k] = {}
            current = current[k]

        # Set value
        current[keys[-1]] = value
        self.preferences = prefs
