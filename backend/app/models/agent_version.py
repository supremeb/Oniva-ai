"""Agent Version model for versioned agent configuration snapshots."""
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, Boolean, Float, Text, func
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from app.models import Base


class AgentVersion(Base):
    """
    Stores immutable versioned snapshots of agent configurations.
    Uses sequential integer versioning (v1, v2, v3).
    Legacy semver versions preserved in legacy_version column.
    """
    __tablename__ = 'agent_versions'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Multi-tenancy
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey('tenants.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )

    # Parent agent
    agent_id = Column(
        UUID(as_uuid=True),
        ForeignKey('custom_agents.id', ondelete='CASCADE'),
        nullable=False
    )

    # Sequential integer version (NEW: v1, v2, v3...)
    version_number = Column(Integer, nullable=True)  # nullable during migration, then NOT NULL

    # Legacy semver string (preserved from old system, e.g. '1.0.0')
    # Renamed from 'version' column
    legacy_version = Column('version', String(20), nullable=True)

    # User-provided version label (NEW)
    name = Column(Text, nullable=True)

    # Complete config snapshot (immutable)
    config = Column(JSONB, nullable=False)

    # --- Deployment status (legacy, being replaced by aliases) ---
    is_deployed = Column(Boolean, nullable=False, default=False)
    deployed_at = Column(DateTime(timezone=True), nullable=True)
    deployed_by = Column(
        UUID(as_uuid=True),
        ForeignKey('users.id', ondelete='SET NULL'),
        nullable=True
    )

    # --- Columns from migration 023 (existed in DB, missing from model) ---
    accuracy = Column(Float, nullable=True)
    accuracy_at_deploy = Column(Float, nullable=True)
    test_count = Column(Integer, server_default='0')
    deploy_blocked = Column(Boolean, server_default='false')
    block_reason = Column(Text, nullable=True)

    # --- Publishing (NEW: moved from CustomAgent) ---
    visibility = Column(String(20), default='private', nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=True)
    compliance_tags = Column(ARRAY(String(50)), nullable=True)

    # Who created this version
    created_by = Column(
        UUID(as_uuid=True),
        ForeignKey('users.id', ondelete='SET NULL'),
        nullable=True
    )

    # Optional notes about this version
    notes = Column(Text, nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), default=datetime.utcnow)

    # Relationships
    tenant = relationship("Tenant")
    agent = relationship("CustomAgent", back_populates="versions")
    deployer = relationship("User", foreign_keys=[deployed_by])
    creator = relationship("User", foreign_keys=[created_by])

    def __repr__(self):
        return f"<AgentVersion(agent_id='{self.agent_id}', v{self.version_number})>"

    def to_dict(self):
        """Convert to dictionary for API responses"""
        return {
            'id': str(self.id),
            'tenant_id': str(self.tenant_id),
            'agent_id': str(self.agent_id),
            'version_number': self.version_number,
            'legacy_version': self.legacy_version,
            'name': self.name,
            'config': self.config,
            'is_deployed': self.is_deployed,
            'deployed_at': self.deployed_at.isoformat() if self.deployed_at else None,
            'deployed_by': str(self.deployed_by) if self.deployed_by else None,
            'accuracy': self.accuracy,
            'accuracy_at_deploy': self.accuracy_at_deploy,
            'test_count': self.test_count,
            'deploy_blocked': self.deploy_blocked,
            'block_reason': self.block_reason,
            'visibility': self.visibility or 'private',
            'published_at': self.published_at.isoformat() if self.published_at else None,
            'compliance_tags': self.compliance_tags or [],
            'created_by': str(self.created_by) if self.created_by else None,
            'notes': self.notes,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
