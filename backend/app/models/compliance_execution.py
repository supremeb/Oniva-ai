"""
Compliance Execution model for tracking compliance checks and review queue.
"""
from sqlalchemy import Column, String, DateTime, ForeignKey, Integer, Text, Numeric, func, CheckConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from datetime import datetime
from enum import Enum
import uuid

from app.models import Base
from app.models._clock import utc_now


class ComplianceStatus(str, Enum):
    """Compliance execution status"""
    PENDING = 'pending'
    RUNNING = 'running'
    COMPLETED = 'completed'
    FAILED = 'failed'
    MANUAL_REVIEW = 'manual_review'


class ReviewDecision(str, Enum):
    """Review decision types"""
    APPROVED = 'approved'
    REJECTED = 'rejected'
    ESCALATED = 'escalated'


class RiskLevelEnum(str, Enum):
    """Risk level classification"""
    LOW = 'low'
    MEDIUM = 'medium'
    HIGH = 'high'
    CRITICAL = 'critical'


class ComplianceExecution(Base):
    """
    Tracks compliance check executions and review queue.
    Supports human-in-loop review workflow.
    """
    __tablename__ = 'compliance_executions'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Multi-tenancy
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey('tenants.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )

    # Owner who initiated the check
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey('users.id', ondelete='CASCADE'),
        nullable=False
    )

    # Template info
    template_id = Column(String(100), nullable=False)
    template_version = Column(String(20), nullable=False)
    template_name = Column(String(255), nullable=True)

    # Subject being checked
    subject_id = Column(String(255), nullable=False)  # Customer ID, Entity ID, etc.
    subject_type = Column(String(50), nullable=False)  # 'customer', 'transaction', 'entity'
    subject_name = Column(String(255), nullable=True)  # Display name for UI

    # Execution status
    status = Column(String(50), nullable=False, default=ComplianceStatus.PENDING.value)

    # Results
    risk_score = Column(Numeric(5, 4), nullable=True)  # 0.0000 to 1.0000
    risk_level = Column(String(20), nullable=True)
    recommendation = Column(String(50), nullable=True)  # 'approve', 'review', 'escalate', 'reject'
    results = Column(JSONB, nullable=False, default=dict)
    execution_trace = Column(JSONB, nullable=False, default=list)

    # Timing
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    elapsed_ms = Column(Integer, nullable=True)

    # Review fields (Human-in-loop)
    reviewed_by = Column(
        UUID(as_uuid=True),
        ForeignKey('users.id', ondelete='SET NULL'),
        nullable=True
    )
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    review_decision = Column(String(50), nullable=True)
    review_notes = Column(Text, nullable=True)

    # Audit
    created_at = Column(DateTime(timezone=True), server_default=func.now(), default=utc_now)

    # Relationships
    tenant = relationship("Tenant")
    user = relationship("User", foreign_keys=[user_id])
    reviewer = relationship("User", foreign_keys=[reviewed_by])

    # Table constraints
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'manual_review')",
            name='ck_compliance_executions_status'
        ),
        CheckConstraint(
            "risk_level IS NULL OR risk_level IN ('low', 'medium', 'high', 'critical')",
            name='ck_compliance_executions_risk_level'
        ),
    )

    def __repr__(self):
        return f"<ComplianceExecution(id='{self.id}', status='{self.status}', subject='{self.subject_name}')>"

    def to_dict(self):
        """Convert to dictionary for API responses"""
        return {
            'id': str(self.id),
            'tenant_id': str(self.tenant_id),
            'user_id': str(self.user_id),
            'template_id': self.template_id,
            'template_version': self.template_version,
            'template_name': self.template_name,
            'subject_id': self.subject_id,
            'subject_type': self.subject_type,
            'subject_name': self.subject_name,
            'status': self.status,
            'risk_score': float(self.risk_score) if self.risk_score else None,
            'risk_level': self.risk_level,
            'recommendation': self.recommendation,
            'results': self.results,
            'execution_trace': self.execution_trace,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'elapsed_ms': self.elapsed_ms,
            'reviewed_by': str(self.reviewed_by) if self.reviewed_by else None,
            'reviewed_at': self.reviewed_at.isoformat() if self.reviewed_at else None,
            'review_decision': self.review_decision,
            'review_notes': self.review_notes,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'needs_review': self.needs_review,
            'is_reviewed': self.is_reviewed,
        }

    @property
    def needs_review(self) -> bool:
        """Check if execution requires manual review"""
        return self.status == ComplianceStatus.MANUAL_REVIEW.value and self.reviewed_at is None

    @property
    def is_reviewed(self) -> bool:
        """Check if execution has been reviewed"""
        return self.reviewed_at is not None

    def start(self):
        """Mark execution as started"""
        self.status = ComplianceStatus.RUNNING.value
        self.started_at = datetime.utcnow()

    def complete(self, results: dict = None, risk_level: str = None, risk_score: float = None):
        """Mark execution as completed"""
        self.status = ComplianceStatus.COMPLETED.value
        self.completed_at = datetime.utcnow()
        if self.started_at:
            delta = self.completed_at - self.started_at
            self.elapsed_ms = int(delta.total_seconds() * 1000)
        if results:
            self.results = results
        if risk_level:
            self.risk_level = risk_level
        if risk_score is not None:
            self.risk_score = risk_score

    def require_review(self, recommendation: str = 'review'):
        """Mark execution as requiring manual review"""
        self.status = ComplianceStatus.MANUAL_REVIEW.value
        self.recommendation = recommendation
        self.completed_at = datetime.utcnow()
        if self.started_at:
            delta = self.completed_at - self.started_at
            self.elapsed_ms = int(delta.total_seconds() * 1000)

    def fail(self, error: str):
        """Mark execution as failed"""
        self.status = ComplianceStatus.FAILED.value
        self.completed_at = datetime.utcnow()
        self.results = {'error': error}

    def submit_review(self, reviewer_id: uuid.UUID, decision: str, notes: str = None):
        """Submit a review decision"""
        self.reviewed_by = reviewer_id
        self.reviewed_at = datetime.utcnow()
        self.review_decision = decision
        self.review_notes = notes

        # Update status based on decision
        if decision == ReviewDecision.APPROVED.value:
            self.status = ComplianceStatus.COMPLETED.value
        elif decision == ReviewDecision.REJECTED.value:
            self.status = ComplianceStatus.COMPLETED.value
        elif decision == ReviewDecision.ESCALATED.value:
            # Keep in manual_review but mark as escalated
            self.recommendation = 'escalated'

    def add_trace_step(self, step: dict):
        """Add a step to the execution trace"""
        if self.execution_trace is None:
            self.execution_trace = []
        self.execution_trace = [*self.execution_trace, step]


class ComplianceScreeningResult(Base):
    """
    Individual screening results from a compliance execution.
    Stores detailed results from each screening database.
    """
    __tablename__ = 'compliance_screening_results'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    execution_id = Column(
        UUID(as_uuid=True),
        ForeignKey('compliance_executions.id', ondelete='CASCADE'),
        nullable=False
    )

    screening_type = Column(String(50), nullable=False)  # 'sanctions', 'pep', 'adverse_media'
    database_checked = Column(String(100), nullable=True)  # 'OFAC_SDN', 'UN_LIST', etc.
    query_params = Column(JSONB, nullable=True)  # What was searched
    results = Column(JSONB, nullable=False, default=dict)  # Full results
    match_count = Column(Integer, nullable=False, default=0)
    highest_match_score = Column(Numeric(5, 4), nullable=True)
    flagged = Column(String(5), nullable=False, default='false')  # Changed to match migration

    created_at = Column(DateTime(timezone=True), server_default=func.now(), default=utc_now)

    # Relationships
    execution = relationship("ComplianceExecution", backref="screening_results")

    # Table constraints
    __table_args__ = (
        CheckConstraint(
            "screening_type IN ('sanctions', 'pep', 'adverse_media', 'jurisdiction')",
            name='ck_screening_results_type'
        ),
    )

    def __repr__(self):
        return f"<ComplianceScreeningResult(id='{self.id}', type='{self.screening_type}', matches={self.match_count})>"

    def to_dict(self):
        return {
            'id': str(self.id),
            'execution_id': str(self.execution_id),
            'screening_type': self.screening_type,
            'database_checked': self.database_checked,
            'query_params': self.query_params,
            'results': self.results,
            'match_count': self.match_count,
            'highest_match_score': float(self.highest_match_score) if self.highest_match_score else None,
            'flagged': self.flagged == 'true',
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
