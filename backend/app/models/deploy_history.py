"""Deploy History model for Agent Studio deployment tracking."""
from sqlalchemy import Column, String, DateTime, ForeignKey, Boolean, Text, Float, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from app.models import Base
from app.models._clock import utc_now


class DeployHistory(Base):
    """
    Tracks deployment history for agents with gate check results.
    Used for audit, rollback, and deployment analytics.
    """
    __tablename__ = 'deploy_history'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Multi-tenancy
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey('tenants.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )

    # Agent reference
    agent_id = Column(
        UUID(as_uuid=True),
        ForeignKey('custom_agents.id', ondelete='CASCADE'),
        nullable=False
    )
    agent_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey('agent_versions.id', ondelete='CASCADE'),
        nullable=False
    )

    # Deploy action
    action = Column(String(50), nullable=False)  # 'deploy', 'rollback', 'block'
    status = Column(String(50), nullable=False)  # 'success', 'blocked', 'rolled_back'

    # Gate check results
    gate_check_passed = Column(Boolean, nullable=False)
    accuracy_at_deploy = Column(Float, nullable=True)
    accuracy_threshold = Column(Float, default=0.8)  # 80% minimum
    accuracy_regression_allowed = Column(Float, default=0.05)  # 5% max regression

    # Comparison to previous version
    previous_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey('agent_versions.id', ondelete='SET NULL'),
        nullable=True
    )
    previous_accuracy = Column(Float, nullable=True)
    accuracy_delta = Column(Float, nullable=True)

    # Reason (for blocks/rollbacks)
    block_reason = Column(Text, nullable=True)

    # User action
    deployed_by = Column(
        UUID(as_uuid=True),
        ForeignKey('users.id', ondelete='SET NULL'),
        nullable=False
    )
    notes = Column(Text, nullable=True)

    # Force deploy flag (bypasses gates)
    force_deployed = Column(Boolean, default=False)
    force_reason = Column(Text, nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), default=utc_now)

    # Relationships
    tenant = relationship("Tenant")
    agent = relationship("CustomAgent")
    agent_version = relationship("AgentVersion", foreign_keys=[agent_version_id])
    previous_version = relationship("AgentVersion", foreign_keys=[previous_version_id])
    deployer = relationship("User")

    def __repr__(self):
        return f"<DeployHistory(agent_id='{self.agent_id}', action='{self.action}', status='{self.status}')>"

    def to_dict(self):
        """Convert to dictionary for API responses"""
        return {
            'id': str(self.id),
            'tenant_id': str(self.tenant_id),
            'agent_id': str(self.agent_id),
            'agent_version_id': str(self.agent_version_id),
            'action': self.action,
            'status': self.status,
            'gate_check_passed': self.gate_check_passed,
            'accuracy_at_deploy': self.accuracy_at_deploy,
            'accuracy_threshold': self.accuracy_threshold,
            'accuracy_regression_allowed': self.accuracy_regression_allowed,
            'previous_version_id': str(self.previous_version_id) if self.previous_version_id else None,
            'previous_accuracy': self.previous_accuracy,
            'accuracy_delta': self.accuracy_delta,
            'block_reason': self.block_reason,
            'deployed_by': str(self.deployed_by),
            'notes': self.notes,
            'force_deployed': self.force_deployed,
            'force_reason': self.force_reason,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }

    @staticmethod
    def create_deploy_record(
        tenant_id: uuid.UUID,
        agent_id: uuid.UUID,
        agent_version_id: uuid.UUID,
        deployed_by: uuid.UUID,
        gate_check_passed: bool,
        accuracy_at_deploy: float = None,
        previous_version_id: uuid.UUID = None,
        previous_accuracy: float = None,
        notes: str = None,
        force_deployed: bool = False,
        force_reason: str = None,
    ) -> 'DeployHistory':
        """Create a deployment success record."""
        accuracy_delta = None
        if accuracy_at_deploy is not None and previous_accuracy is not None:
            accuracy_delta = accuracy_at_deploy - previous_accuracy

        return DeployHistory(
            tenant_id=tenant_id,
            agent_id=agent_id,
            agent_version_id=agent_version_id,
            action='deploy',
            status='success',
            gate_check_passed=gate_check_passed,
            accuracy_at_deploy=accuracy_at_deploy,
            previous_version_id=previous_version_id,
            previous_accuracy=previous_accuracy,
            accuracy_delta=accuracy_delta,
            deployed_by=deployed_by,
            notes=notes,
            force_deployed=force_deployed,
            force_reason=force_reason,
        )

    @staticmethod
    def create_block_record(
        tenant_id: uuid.UUID,
        agent_id: uuid.UUID,
        agent_version_id: uuid.UUID,
        deployed_by: uuid.UUID,
        block_reason: str,
        accuracy_at_deploy: float = None,
        previous_version_id: uuid.UUID = None,
        previous_accuracy: float = None,
    ) -> 'DeployHistory':
        """Create a deployment blocked record."""
        accuracy_delta = None
        if accuracy_at_deploy is not None and previous_accuracy is not None:
            accuracy_delta = accuracy_at_deploy - previous_accuracy

        return DeployHistory(
            tenant_id=tenant_id,
            agent_id=agent_id,
            agent_version_id=agent_version_id,
            action='block',
            status='blocked',
            gate_check_passed=False,
            accuracy_at_deploy=accuracy_at_deploy,
            previous_version_id=previous_version_id,
            previous_accuracy=previous_accuracy,
            accuracy_delta=accuracy_delta,
            block_reason=block_reason,
            deployed_by=deployed_by,
        )

    @staticmethod
    def create_rollback_record(
        tenant_id: uuid.UUID,
        agent_id: uuid.UUID,
        target_version_id: uuid.UUID,
        rolled_back_from_id: uuid.UUID,
        deployed_by: uuid.UUID,
        reason: str,
    ) -> 'DeployHistory':
        """Create a rollback record."""
        return DeployHistory(
            tenant_id=tenant_id,
            agent_id=agent_id,
            agent_version_id=target_version_id,
            action='rollback',
            status='rolled_back',
            gate_check_passed=True,  # Rollbacks always allowed
            previous_version_id=rolled_back_from_id,
            notes=reason,
            deployed_by=deployed_by,
        )
