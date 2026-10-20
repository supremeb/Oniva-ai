"""
Compliance Audit Log model for cryptographic audit trails.

Supports SEC/FINRA 7-year retention requirements with:
- RSA-2048 digital signatures
- Blockchain-style hash chaining
- Tamper-evident logging
"""
from sqlalchemy import Column, String, DateTime, ForeignKey, Text, Index, func, Integer, BigInteger
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.orm import relationship
from datetime import datetime
from enum import Enum
import uuid

from app.models import Base


class AuditAction(str, Enum):
    """Types of auditable actions."""
    AGENT_DECISION = 'agent_decision'
    STATE_TRANSITION = 'state_transition'
    TOOL_CALL = 'tool_call'
    COMPLIANCE_CHECK = 'compliance_check'
    HUMAN_REVIEW = 'human_review'
    DATA_ACCESS = 'data_access'
    REPORT_GENERATION = 'report_generation'

    # Data classification events
    CLASSIFICATION_ASSIGNED = 'classification_assigned'
    CLASSIFICATION_PROPAGATED = 'classification_propagated'
    EXPORT_BLOCKED = 'export_blocked'
    EXPORT_ALLOWED = 'export_allowed'
    CLASSIFICATION_DOWNGRADE_REQUESTED = 'classification_downgrade_requested'

    # Approvals and policy (Phase 5)
    APPROVAL_REQUESTED = 'approval_requested'
    APPROVAL_DECIDED = 'approval_decided'
    APPROVAL_EXPIRED = 'approval_expired'
    APPROVAL_CANCELLED = 'approval_cancelled'
    TOOL_POLICY_BLOCK = 'tool_policy_block'
    QUESTION_ANSWERED = 'question_answered'
    RULE_OF_TWO_CHANGED = 'rule_of_two_changed'
    APPROVAL_SETTINGS_CHANGED = 'approval_settings_changed'


class ComplianceAuditLog(Base):
    """
    Cryptographically-secured audit log for compliance.

    Features:
    - RSA-2048 digital signatures on each entry
    - Hash chain linking entries (blockchain-style)
    - 7-year retention for SEC/FINRA compliance
    - Immutable once written
    """
    __tablename__ = 'compliance_audit_logs'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Multi-tenancy
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey('tenants.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )

    # Agent/Actor identification
    agent_id = Column(String(100), nullable=False, index=True)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey('users.id', ondelete='SET NULL'),
        nullable=True
    )

    # Session context
    session_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    workflow_id = Column(UUID(as_uuid=True), nullable=True, index=True)

    # Multi-agent orchestration correlation (Phase B)
    trace_id = Column(String(32), nullable=True, index=True)

    # Action details
    action = Column(String(50), nullable=False, index=True)  # AuditAction value
    decision = Column(String(255), nullable=False)
    rationale = Column(Text, nullable=False)

    # Data lineage
    data_sources = Column(ARRAY(String), nullable=False, default=list)

    # State machine context
    workflow_state = Column(String(50), nullable=True)
    previous_state = Column(String(50), nullable=True)
    tools_called = Column(ARRAY(String), nullable=False, default=list)

    # Cryptographic integrity
    signature = Column(Text, nullable=False)  # RSA-2048 signature (base64)
    previous_hash = Column(String(64), nullable=True)  # SHA-256 of previous entry
    entry_hash = Column(String(64), nullable=False)  # SHA-256 of this entry

    # Per-tenant chain position (KTD9). NULL for rows written before the chain existed.
    seq = Column(BigInteger, nullable=True)
    signing_key_id = Column(String(64), nullable=True)

    # Actor snapshot captured at write time (survives user deletion; covered by the entry hash,
    # unlike the mutable user_id pointer).
    actor_email = Column(String(255), nullable=True)
    actor_name = Column(String(255), nullable=True)

    # Additional context
    extra_metadata = Column(JSONB, nullable=False, default=dict)

    # Retention
    retention_until = Column(
        DateTime(timezone=True),
        nullable=False,
        index=True
    )  # 7 years from creation

    # Timestamps
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True
    )

    # Relationships
    tenant = relationship("Tenant")
    user = relationship("User")

    # Indexes for efficient querying
    __table_args__ = (
        Index('ix_compliance_audit_logs_session_time', 'session_id', 'created_at'),
        Index('ix_compliance_audit_logs_workflow_time', 'workflow_id', 'created_at'),
        Index('ix_compliance_audit_logs_agent_time', 'agent_id', 'created_at'),
        Index('ix_compliance_audit_logs_retention', 'retention_until'),
        Index('ix_compliance_audit_logs_entry_hash', 'entry_hash'),
        Index('uq_compliance_audit_logs_tenant_seq', 'tenant_id', 'seq', unique=True),
    )

    def __repr__(self):
        return (
            f"<ComplianceAuditLog(id='{self.id}', "
            f"action='{self.action}', "
            f"decision='{self.decision[:50]}...')>"
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            'id': str(self.id),
            'tenant_id': str(self.tenant_id),
            'agent_id': self.agent_id,
            'user_id': str(self.user_id) if self.user_id else None,
            'session_id': str(self.session_id) if self.session_id else None,
            'workflow_id': str(self.workflow_id) if self.workflow_id else None,
            'trace_id': self.trace_id,
            'action': self.action,
            'decision': self.decision,
            'rationale': self.rationale,
            'data_sources': self.data_sources,
            'workflow_state': self.workflow_state,
            'previous_state': self.previous_state,
            'tools_called': self.tools_called,
            'signature': self.signature,
            'previous_hash': self.previous_hash,
            'entry_hash': self.entry_hash,
            'metadata': self.extra_metadata,
            'retention_until': self.retention_until.isoformat() if self.retention_until else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }

    def to_verification_dict(self) -> dict:
        """Return minimal dict for signature verification."""
        return {
            'id': str(self.id),
            'tenant_id': str(self.tenant_id),
            'agent_id': self.agent_id,
            'action': self.action,
            'decision': self.decision,
            'rationale': self.rationale,
            'data_sources': self.data_sources,
            'workflow_state': self.workflow_state,
            'tools_called': self.tools_called,
            'previous_hash': self.previous_hash,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class AuditChainHead(Base):
    """
    Per-tenant cache of the audit chain head (KTD9).

    Rebuildable from the last committed ``compliance_audit_logs`` row; the unique
    ``(tenant_id, seq)`` index on the log is what actually makes a fork impossible.
    """
    __tablename__ = 'audit_chain_heads'

    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey('tenants.id', ondelete='CASCADE'),
        primary_key=True,
    )
    seq = Column(BigInteger, nullable=False, default=0)
    head_hash = Column(Text, nullable=True)
    key_id = Column(Text, nullable=True)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self):
        return f"<AuditChainHead(tenant_id='{self.tenant_id}', seq={self.seq})>"


class WorkflowStateLog(Base):
    """
    Tracks all state transitions for workflow replay.

    Used for:
    - Audit trail of state machine progression
    - Workflow replay for compliance review
    - Debugging state transition issues
    """
    __tablename__ = 'workflow_state_logs'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Multi-tenancy
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey('tenants.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )

    # Workflow context
    workflow_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    session_id = Column(UUID(as_uuid=True), nullable=False, index=True)

    # State transition
    from_state = Column(String(50), nullable=False)
    to_state = Column(String(50), nullable=False)
    reason = Column(Text, nullable=True)

    # Context at transition
    tools_called_in_state = Column(Integer, nullable=False, default=0)
    context_snapshot = Column(JSONB, nullable=False, default=dict)

    # Timing
    transition_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True
    )

    # Indexes
    __table_args__ = (
        Index('ix_workflow_state_logs_workflow', 'workflow_id', 'transition_at'),
        Index('ix_workflow_state_logs_session', 'session_id', 'transition_at'),
    )

    def __repr__(self):
        return (
            f"<WorkflowStateLog(workflow='{self.workflow_id}', "
            f"{self.from_state} -> {self.to_state})>"
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            'id': str(self.id),
            'tenant_id': str(self.tenant_id),
            'workflow_id': str(self.workflow_id),
            'session_id': str(self.session_id),
            'from_state': self.from_state,
            'to_state': self.to_state,
            'reason': self.reason,
            'tools_called_in_state': self.tools_called_in_state,
            'context_snapshot': self.context_snapshot,
            'transition_at': self.transition_at.isoformat() if self.transition_at else None,
        }


class HumanReviewTicket(Base):
    """
    Human-in-loop review tickets for high-risk decisions.

    Created when:
    - Risk score exceeds threshold
    - Compliance check flags potential issues
    - Agent requests human verification
    """
    __tablename__ = 'human_review_tickets'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Multi-tenancy
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey('tenants.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )

    # Context
    workflow_id = Column(UUID(as_uuid=True), nullable=True)
    session_id = Column(UUID(as_uuid=True), nullable=True)
    agent_id = Column(String(100), nullable=False)

    # Requestor
    requested_by = Column(
        UUID(as_uuid=True),
        ForeignKey('users.id', ondelete='SET NULL'),
        nullable=True
    )

    # Review details
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=False)
    risk_level = Column(String(20), nullable=False, index=True)  # low, medium, high, critical
    priority = Column(String(20), nullable=False, default='normal')  # low, normal, high, urgent

    # What needs review
    review_type = Column(String(50), nullable=False)  # 'compliance', 'decision', 'output'
    review_data = Column(JSONB, nullable=False, default=dict)

    # Related audit entry
    audit_log_id = Column(
        UUID(as_uuid=True),
        ForeignKey('compliance_audit_logs.id', ondelete='SET NULL'),
        nullable=True
    )

    # Status
    status = Column(String(20), nullable=False, default='pending', index=True)
    # pending, assigned, in_review, approved, rejected, escalated

    # Assignment
    assigned_to = Column(
        UUID(as_uuid=True),
        ForeignKey('users.id', ondelete='SET NULL'),
        nullable=True
    )
    assigned_at = Column(DateTime(timezone=True), nullable=True)

    # Resolution
    reviewed_by = Column(
        UUID(as_uuid=True),
        ForeignKey('users.id', ondelete='SET NULL'),
        nullable=True
    )
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    decision = Column(String(20), nullable=True)  # approved, rejected, escalated
    review_notes = Column(Text, nullable=True)

    # SLA tracking
    due_at = Column(DateTime(timezone=True), nullable=True)
    sla_breached = Column(String(5), nullable=False, default='false')

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
    tenant = relationship("Tenant")
    requester = relationship("User", foreign_keys=[requested_by])
    assignee = relationship("User", foreign_keys=[assigned_to])
    reviewer = relationship("User", foreign_keys=[reviewed_by])
    audit_log = relationship("ComplianceAuditLog")

    # Indexes
    __table_args__ = (
        Index('ix_human_review_tickets_status_priority', 'status', 'priority'),
        Index('ix_human_review_tickets_assigned', 'assigned_to', 'status'),
        Index('ix_human_review_tickets_due', 'due_at', 'status'),
    )

    def __repr__(self):
        return (
            f"<HumanReviewTicket(id='{self.id}', "
            f"title='{self.title[:50]}...', status='{self.status}')>"
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            'id': str(self.id),
            'tenant_id': str(self.tenant_id),
            'workflow_id': str(self.workflow_id) if self.workflow_id else None,
            'session_id': str(self.session_id) if self.session_id else None,
            'agent_id': self.agent_id,
            'requested_by': str(self.requested_by) if self.requested_by else None,
            'title': self.title,
            'description': self.description,
            'risk_level': self.risk_level,
            'priority': self.priority,
            'review_type': self.review_type,
            'review_data': self.review_data,
            'audit_log_id': str(self.audit_log_id) if self.audit_log_id else None,
            'status': self.status,
            'assigned_to': str(self.assigned_to) if self.assigned_to else None,
            'assigned_at': self.assigned_at.isoformat() if self.assigned_at else None,
            'reviewed_by': str(self.reviewed_by) if self.reviewed_by else None,
            'reviewed_at': self.reviewed_at.isoformat() if self.reviewed_at else None,
            'decision': self.decision,
            'review_notes': self.review_notes,
            'due_at': self.due_at.isoformat() if self.due_at else None,
            'sla_breached': self.sla_breached == 'true',
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }

    @property
    def is_overdue(self) -> bool:
        """Check if ticket is past due date."""
        if not self.due_at:
            return False
        return datetime.utcnow() > self.due_at and self.status in ('pending', 'assigned', 'in_review')
