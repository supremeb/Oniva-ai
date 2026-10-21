"""Agent Alias model for named deployment pointers (PRODUCTION, STAGING)."""
from sqlalchemy import Column, String, DateTime, ForeignKey, func, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from app.models import Base
from app.models._clock import utc_now


class AgentAlias(Base):
    """
    Named deployment pointer that references a specific AgentVersion.
    E.g., PRODUCTION -> v3, STAGING -> v4.
    Alias pointer swaps enable instant rollback without config copying.
    """
    __tablename__ = 'agent_aliases'
    __table_args__ = (
        UniqueConstraint('agent_id', 'alias_name', name='uq_agent_alias_name'),
    )

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
        nullable=False,
        index=True
    )

    # Alias name (restricted to PRODUCTION for now; STAGING deferred)
    alias_name = Column(String(50), nullable=False)

    # Points to a specific version
    version_id = Column(
        UUID(as_uuid=True),
        ForeignKey('agent_versions.id', ondelete='CASCADE'),
        nullable=False
    )

    # Audit
    updated_by = Column(
        UUID(as_uuid=True),
        ForeignKey('users.id', ondelete='SET NULL'),
        nullable=True
    )
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), default=utc_now, onupdate=utc_now)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), default=utc_now)

    # Relationships
    tenant = relationship("Tenant")
    agent = relationship("CustomAgent")
    version = relationship("AgentVersion")
    updater = relationship("User")

    def __repr__(self):
        return f"<AgentAlias(agent_id='{self.agent_id}', alias='{self.alias_name}', version_id='{self.version_id}')>"

    def to_dict(self):
        return {
            'id': str(self.id),
            'tenant_id': str(self.tenant_id),
            'agent_id': str(self.agent_id),
            'alias_name': self.alias_name,
            'version_id': str(self.version_id),
            'updated_by': str(self.updated_by) if self.updated_by else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }

    def point_to(self, version_id: uuid.UUID, user_id: uuid.UUID):
        """Update alias to point to a different version."""
        self.version_id = version_id
        self.updated_by = user_id
        self.updated_at = datetime.utcnow()
