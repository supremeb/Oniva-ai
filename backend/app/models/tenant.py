"""Tenant model for multi-tenancy support."""
from sqlalchemy import Boolean, Column, DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import relationship

from app.models import Base


class Tenant(Base):
    """
    Tenant represents an organization/customer in the multi-tenant system.

    All user data is scoped to a tenant via tenant_id foreign keys.
    PostgreSQL RLS policies enforce isolation at the database level.
    """
    __tablename__ = "tenants"

    id = Column(PGUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    name = Column(String(255), nullable=False, comment="Display name for the tenant")
    slug = Column(String(100), nullable=False, unique=True, index=True,
                  comment="URL-safe identifier (e.g., 'acme-corp')")
    is_active = Column(Boolean, nullable=False, server_default="true",
                       comment="Whether tenant can access the platform")
    settings = Column(JSONB, nullable=False, server_default="{}",
                      comment="Tenant-specific configuration (plan, features, limits)")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(),
                        onupdate=func.now())

    # Relationships (will be added as tenant_id is added to other tables)
    users = relationship("User", back_populates="tenant", cascade="all, delete-orphan")
    episodic_memories = relationship("EpisodicMemory", back_populates="tenant", cascade="all, delete-orphan")
    user_preferences = relationship("UserPreferences", back_populates="tenant", cascade="all, delete-orphan")
    workflow_entities = relationship("WorkflowEntity", back_populates="tenant", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Tenant(id={self.id}, slug='{self.slug}', name='{self.name}')>"

    # Settings accessors for common configuration fields
    @property
    def signup_mode(self) -> str:
        """
        Get signup mode from settings.

        Returns:
            'open' - Anyone can self-register and create an org
            'invite_only' - Users must be invited via pending_users
        """
        settings = self.settings or {}
        return settings.get("signup_mode", "open")

    @signup_mode.setter
    def signup_mode(self, value: str):
        """Set signup mode in settings."""
        if value not in ("open", "invite_only"):
            raise ValueError("signup_mode must be 'open' or 'invite_only'")
        if self.settings is None:
            self.settings = {}
        self.settings["signup_mode"] = value

    @property
    def allowed_email_domains(self) -> list:
        """Get allowed email domains for self-signup (empty = all allowed)."""
        settings = self.settings or {}
        return settings.get("allowed_email_domains", [])

    @allowed_email_domains.setter
    def allowed_email_domains(self, value: list):
        """Set allowed email domains for self-signup."""
        if not isinstance(value, list):
            raise ValueError("allowed_email_domains must be a list")
        if self.settings is None:
            self.settings = {}
        self.settings["allowed_email_domains"] = value

    @property
    def max_users(self) -> int | None:
        """Get maximum number of users allowed in this tenant (None = unlimited)."""
        settings = self.settings or {}
        return settings.get("max_users")

    def to_dict(self) -> dict:
        """Convert tenant to dictionary for API responses."""
        return {
            "id": str(self.id),
            "name": self.name,
            "slug": self.slug,
            "is_active": self.is_active,
            "settings": self.settings,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
