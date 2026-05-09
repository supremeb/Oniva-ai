"""
Internal Endpoint Whitelist model for Oniva Security Foundation.

Database-backed whitelist for internal endpoints with approval workflow.
Ensures audit trail and dual-control for security-sensitive whitelist changes.

Security Features:
- Requires approval before activation (dual-control)
- Full audit trail of who created and approved
- Soft deactivation (never hard delete for audit)

Usage:
    from app.models.internal_endpoint_whitelist import InternalEndpointWhitelist

    # Create pending whitelist entry
    entry = InternalEndpointWhitelist(
        hostname="internal-api.corp.example.com",
        description="Internal analytics API behind VPN",
        created_by=user_id,
    )

    # Approve entry (different user required for dual-control)
    entry.approve(approver_id=admin_id)
"""
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, func, Text, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from app.models import Base


class InternalEndpointWhitelist(Base):
    """
    Database-backed whitelist for internal endpoints.
    Requires approval workflow for security.

    Workflow:
    1. User creates entry (is_active=False)
    2. Different user approves (sets approved_by, approved_at, is_active=True)
    3. Entry is now active and checked by ExportControlService

    Only active AND approved entries are used for endpoint validation.
    """
    __tablename__ = "internal_endpoint_whitelist"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Hostname to whitelist (lowercase, no protocol or path)
    hostname = Column(String(500), nullable=False, unique=True, index=True)

    # Human-readable description of why this endpoint is internal
    description = Column(Text, nullable=True)

    # Multi-tenancy (optional - global whitelist if None)
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey('tenants.id', ondelete='CASCADE'),
        nullable=True,
        index=True
    )

    # Creation tracking
    created_by = Column(
        UUID(as_uuid=True),
        ForeignKey('users.id', ondelete='SET NULL'),
        nullable=False
    )

    # Approval workflow (dual-control)
    approved_by = Column(
        UUID(as_uuid=True),
        ForeignKey('users.id', ondelete='SET NULL'),
        nullable=True
    )
    approved_at = Column(DateTime(timezone=True), nullable=True)

    # Status - only active after approval
    is_active = Column(Boolean, default=False, nullable=False)

    # Soft deactivation (never hard delete for audit trail)
    deactivated_by = Column(
        UUID(as_uuid=True),
        ForeignKey('users.id', ondelete='SET NULL'),
        nullable=True
    )
    deactivated_at = Column(DateTime(timezone=True), nullable=True)
    deactivation_reason = Column(Text, nullable=True)

    # Timestamps
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False
    )

    # Relationships
    tenant = relationship("Tenant", foreign_keys=[tenant_id])
    creator = relationship("User", foreign_keys=[created_by])
    approver = relationship("User", foreign_keys=[approved_by])
    deactivator = relationship("User", foreign_keys=[deactivated_by])

    # Indexes for efficient queries
    __table_args__ = (
        Index('ix_internal_endpoint_whitelist_active', 'is_active', 'approved_at'),
        Index('ix_internal_endpoint_whitelist_tenant', 'tenant_id', 'is_active'),
    )

    def __repr__(self):
        status = "active" if self.is_active else "pending"
        return f"<InternalEndpointWhitelist(hostname='{self.hostname}', status='{status}')>"

    def approve(self, approver_id: uuid.UUID) -> None:
        """
        Approve this whitelist entry (activates it).

        Args:
            approver_id: UUID of user approving the entry

        Raises:
            ValueError: If same user tries to approve their own entry
        """
        if self.created_by == approver_id:
            raise ValueError(
                "Dual-control violation: Cannot approve your own whitelist entry"
            )

        self.approved_by = approver_id
        self.approved_at = datetime.utcnow()
        self.is_active = True

    def deactivate(self, deactivator_id: uuid.UUID, reason: str = None) -> None:
        """
        Deactivate this whitelist entry (soft delete).

        Args:
            deactivator_id: UUID of user deactivating the entry
            reason: Optional reason for deactivation
        """
        self.is_active = False
        self.deactivated_by = deactivator_id
        self.deactivated_at = datetime.utcnow()
        self.deactivation_reason = reason

    @property
    def is_pending_approval(self) -> bool:
        """Check if entry is pending approval."""
        return not self.approved_at and not self.deactivated_at

    @property
    def is_approved(self) -> bool:
        """Check if entry has been approved."""
        return self.approved_at is not None

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            'id': str(self.id),
            'hostname': self.hostname,
            'description': self.description,
            'tenant_id': str(self.tenant_id) if self.tenant_id else None,
            'created_by': str(self.created_by) if self.created_by else None,
            'approved_by': str(self.approved_by) if self.approved_by else None,
            'approved_at': self.approved_at.isoformat() if self.approved_at else None,
            'is_active': self.is_active,
            'is_pending_approval': self.is_pending_approval,
            'deactivated_by': str(self.deactivated_by) if self.deactivated_by else None,
            'deactivated_at': self.deactivated_at.isoformat() if self.deactivated_at else None,
            'deactivation_reason': self.deactivation_reason,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
