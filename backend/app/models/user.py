"""User model with multi-tenancy support."""
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, func, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import relationship
import uuid

from app.models import Base

if TYPE_CHECKING:
    pass


class User(Base):
    """
    User model with multi-tenancy support.

    Each user belongs to exactly one tenant. Email must be unique within tenant.
    Auth provider can be 'stub' (development), 'saml', 'oidc', or 'api_key'.
    """
    __tablename__ = "users"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Multi-tenancy
    tenant_id = Column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Tenant this user belongs to"
    )

    # Identity
    email = Column(String(255), nullable=False, index=True, comment="User email (unique within tenant)")
    name = Column(String(255), nullable=True, comment="Display name")
    hashed_password = Column(String(255), nullable=True, comment="Hashed password (if using local auth)")

    # Role-based access (4-role system: owner, admin, member, viewer)
    role = Column(
        String(50),
        nullable=False,
        server_default="member",
        comment="User role: owner, admin, member, viewer"
    )

    # Authentication
    auth_provider = Column(
        String(50),
        nullable=False,
        server_default="stub",
        comment="Auth provider: stub, saml, oidc, api_key"
    )
    external_id = Column(
        String(255),
        nullable=True,
        comment="ID from external auth provider (SSO)"
    )

    # Status
    is_active = Column(Boolean, nullable=False, server_default="true", default=True)
    is_superuser = Column(Boolean, nullable=False, server_default="false", default=False)

    # User Management - who added this user
    added_by = Column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment="Admin who added this user"
    )
    added_at = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="When this user was added by admin"
    )

    # Password tracking
    password_changed_at = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="When password was last changed"
    )

    # Timestamps
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(),
                        default=datetime.utcnow, onupdate=datetime.utcnow)
    deleted_at = Column(DateTime(timezone=True), nullable=True, comment="Soft delete timestamp")

    # Relationships
    tenant = relationship("Tenant", back_populates="users")
    api_keys = relationship("APIKey", back_populates="user", cascade="all, delete-orphan")
    connectors = relationship("Connector", back_populates="user", cascade="all, delete-orphan")
    custom_agents = relationship("CustomAgent", back_populates="user", cascade="all, delete-orphan")
    workflows = relationship("Workflow", back_populates="user", cascade="all, delete-orphan")
    documents = relationship("Document", back_populates="user", cascade="all, delete-orphan")
    episodic_memories = relationship("EpisodicMemory", back_populates="user", cascade="all, delete-orphan")
    user_preferences = relationship("UserPreferences", back_populates="user", cascade="all, delete-orphan")

    # Unique constraint: email unique within tenant
    __table_args__ = (
        UniqueConstraint('tenant_id', 'email', name='uq_users_tenant_email'),
    )

    def __repr__(self) -> str:
        return f"<User(id={self.id}, email='{self.email}', tenant_id={self.tenant_id})>"

    def to_dict(self) -> dict:
        """Convert user to dictionary for API responses."""
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "email": self.email,
            "name": self.name,
            "role": self.role,
            "auth_provider": self.auth_provider,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    @property
    def is_owner(self) -> bool:
        """Check if user is the organization owner."""
        return self.role == "owner"

    @property
    def is_admin(self) -> bool:
        """Check if user has admin or owner role."""
        return self.role in ("owner", "admin")

    @property
    def is_member(self) -> bool:
        """Check if user has member role or higher."""
        return self.role in ("owner", "admin", "member")

    @property
    def is_viewer(self) -> bool:
        """Check if user has viewer role."""
        return self.role == "viewer"

    @property
    def can_edit(self) -> bool:
        """Check if user can edit resources (owner, admin, or member)."""
        return self.role in ("owner", "admin", "member")

    @property
    def can_manage_users(self) -> bool:
        """Check if user can manage other users (owner or admin)."""
        return self.role in ("owner", "admin")
