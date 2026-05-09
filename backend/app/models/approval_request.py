"""Approval Request model for Agent Studio (Phase A.3)."""
from sqlalchemy import Column, DateTime, ForeignKey, Text, Enum as SQLEnum, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid
import enum

from app.models import Base


class ApprovalStatus(str, enum.Enum):
    """Status of an approval request."""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ApprovalRequestType(str, enum.Enum):
    """Types of operations requiring approval."""
    DEPLOY = "deploy"
    FORCE_DEPLOY = "force_deploy"
    DELETE = "delete"
    CONFIG_CHANGE = "config_change"


class ApprovalRequest(Base):
    """
    Tracks approval requests for agent operations.
    Team owners must approve high-risk or requires_approval agents.
    """
    __tablename__ = 'approval_requests'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Multi-tenancy
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey('tenants.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )

    # What is being approved
    agent_id = Column(
        UUID(as_uuid=True),
        ForeignKey('custom_agents.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )
    agent_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey('agent_versions.id', ondelete='SET NULL'),
        nullable=True
    )
    request_type = Column(
        SQLEnum(ApprovalRequestType, values_callable=lambda x: [e.value for e in x]),
        nullable=False
    )

    # Who is requesting
    requester_id = Column(
        UUID(as_uuid=True),
        ForeignKey('users.id', ondelete='CASCADE'),
        nullable=False
    )

    # Status
    status = Column(
        SQLEnum(ApprovalStatus, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=ApprovalStatus.PENDING
    )

    # Approval/rejection details
    reviewer_id = Column(
        UUID(as_uuid=True),
        ForeignKey('users.id', ondelete='SET NULL'),
        nullable=True
    )
    reason = Column(Text, nullable=True)  # Requester's reason for the action
    review_comment = Column(Text, nullable=True)  # Reviewer's comment

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), default=datetime.utcnow, onupdate=datetime.utcnow)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    tenant = relationship("Tenant")
    agent = relationship("CustomAgent")
    agent_version = relationship("AgentVersion")
    requester = relationship("User", foreign_keys=[requester_id])
    reviewer = relationship("User", foreign_keys=[reviewer_id])

    def __repr__(self):
        return f"<ApprovalRequest(id='{self.id}', agent_id='{self.agent_id}', status='{self.status}')>"

    def to_dict(self):
        """Convert to dictionary for API responses"""
        return {
            'id': str(self.id),
            'tenant_id': str(self.tenant_id),
            'agent_id': str(self.agent_id),
            'agent_version_id': str(self.agent_version_id) if self.agent_version_id else None,
            'request_type': self.request_type.value,
            'requester_id': str(self.requester_id),
            'status': self.status.value,
            'reviewer_id': str(self.reviewer_id) if self.reviewer_id else None,
            'reason': self.reason,
            'review_comment': self.review_comment,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'reviewed_at': self.reviewed_at.isoformat() if self.reviewed_at else None,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None,
        }
