"""PendingUser model for users added by admin but not yet signed up."""
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, String, func, UniqueConstraint, CheckConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import relationship
import uuid

from app.models import Base


class PendingUser(Base):
    """
    PendingUser represents a user who has been invited to the organization
    but hasn't completed their signup yet.

    Admin adds email → User goes to /signup → User sets password → User is in!
    """
    __tablename__ = "pending_users"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Multi-tenancy
    tenant_id = Column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Tenant this pending user belongs to"
    )

    # Identity
    email = Column(
        String(255),
        nullable=False,
        index=True,
        comment="Email address of the pending user"
    )

    # Role (pre-assigned by admin)
    role = Column(
        String(50),
        nullable=False,
        server_default="member",
        comment="Pre-assigned role: admin, member, viewer"
    )

    # Who added this user
    added_by = Column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment="Admin who added this user"
    )

    # Timestamps
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=datetime.utcnow
    )

    # Relationships
    tenant = relationship("Tenant", backref="pending_users")
    added_by_user = relationship("User", foreign_keys=[added_by])

    # Constraints
    __table_args__ = (
        UniqueConstraint('tenant_id', 'email', name='uq_pending_users_tenant_email'),
        CheckConstraint(
            "role IN ('admin', 'member', 'viewer')",
            name='ck_pending_users_valid_role'
        ),
    )

    def __repr__(self) -> str:
        return f"<PendingUser(id={self.id}, email='{self.email}', tenant_id={self.tenant_id})>"

    def to_dict(self) -> dict:
        """Convert pending user to dictionary for API responses."""
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "email": self.email,
            "role": self.role,
            "added_by": str(self.added_by) if self.added_by else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
