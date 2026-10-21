"""Agent Accuracy History model for Agent Studio metrics tracking."""
from sqlalchemy import Column, DateTime, ForeignKey, Integer, Float, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from app.models import Base
from app.models._clock import utc_now


class AgentAccuracyHistory(Base):
    """
    Stores accuracy metrics over time for agents.
    Used for trend analysis, deploy gates, and regression detection.
    """
    __tablename__ = 'agent_accuracy_history'

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
        ForeignKey('agent_versions.id', ondelete='SET NULL'),
        nullable=True
    )

    # Accuracy metrics (0.0 to 1.0)
    accuracy = Column(Float, nullable=False)
    total_tests = Column(Integer, nullable=False)
    passed_tests = Column(Integer, nullable=False)
    failed_tests = Column(Integer, nullable=False)

    # Breakdown by category
    # Format: {category: {accuracy: float, passed: int, failed: int}}
    category_breakdown = Column(JSONB, nullable=True)

    # Execution details
    evaluation_run_id = Column(UUID(as_uuid=True), nullable=True)
    duration_ms = Column(Integer, nullable=True)

    # Comparison to previous evaluation
    previous_accuracy = Column(Float, nullable=True)
    accuracy_delta = Column(Float, nullable=True)  # Current - Previous

    # Timestamps
    evaluated_at = Column(DateTime(timezone=True), server_default=func.now(), default=utc_now)

    # Relationships
    tenant = relationship("Tenant")
    agent = relationship("CustomAgent")
    agent_version = relationship("AgentVersion")

    def __repr__(self):
        return f"<AgentAccuracyHistory(agent_id='{self.agent_id}', accuracy={self.accuracy:.2%})>"

    def to_dict(self):
        """Convert to dictionary for API responses"""
        return {
            'id': str(self.id),
            'tenant_id': str(self.tenant_id),
            'agent_id': str(self.agent_id),
            'agent_version_id': str(self.agent_version_id) if self.agent_version_id else None,
            'accuracy': self.accuracy,
            'total_tests': self.total_tests,
            'passed_tests': self.passed_tests,
            'failed_tests': self.failed_tests,
            'category_breakdown': self.category_breakdown,
            'evaluation_run_id': str(self.evaluation_run_id) if self.evaluation_run_id else None,
            'duration_ms': self.duration_ms,
            'previous_accuracy': self.previous_accuracy,
            'accuracy_delta': self.accuracy_delta,
            'evaluated_at': self.evaluated_at.isoformat() if self.evaluated_at else None,
        }

    @staticmethod
    def create_from_results(
        tenant_id: uuid.UUID,
        agent_id: uuid.UUID,
        test_results: list[dict],
        agent_version_id: uuid.UUID = None,
        previous_accuracy: float = None,
        duration_ms: int = None,
        evaluation_run_id: uuid.UUID = None
    ) -> 'AgentAccuracyHistory':
        """
        Create an accuracy history entry from test results.

        Args:
            tenant_id: Tenant UUID
            agent_id: Agent UUID
            test_results: List of {passed: bool, category: str} dicts
            agent_version_id: Optional version UUID
            previous_accuracy: Optional previous accuracy for delta calc
            duration_ms: Optional evaluation duration
            evaluation_run_id: Optional run ID for grouping

        Returns:
            New AgentAccuracyHistory instance
        """
        total = len(test_results)
        passed = sum(1 for r in test_results if r.get('passed'))
        failed = total - passed
        accuracy = passed / total if total > 0 else 0.0

        # Calculate category breakdown
        category_breakdown = {}
        for result in test_results:
            cat = result.get('category') or 'uncategorized'
            if cat not in category_breakdown:
                category_breakdown[cat] = {'passed': 0, 'failed': 0, 'total': 0}
            category_breakdown[cat]['total'] += 1
            if result.get('passed'):
                category_breakdown[cat]['passed'] += 1
            else:
                category_breakdown[cat]['failed'] += 1

        # Calculate accuracy per category
        for cat in category_breakdown:
            cat_total = category_breakdown[cat]['total']
            cat_passed = category_breakdown[cat]['passed']
            category_breakdown[cat]['accuracy'] = cat_passed / cat_total if cat_total > 0 else 0.0

        # Calculate delta
        accuracy_delta = None
        if previous_accuracy is not None:
            accuracy_delta = accuracy - previous_accuracy

        return AgentAccuracyHistory(
            tenant_id=tenant_id,
            agent_id=agent_id,
            agent_version_id=agent_version_id,
            accuracy=accuracy,
            total_tests=total,
            passed_tests=passed,
            failed_tests=failed,
            category_breakdown=category_breakdown,
            evaluation_run_id=evaluation_run_id,
            duration_ms=duration_ms,
            previous_accuracy=previous_accuracy,
            accuracy_delta=accuracy_delta,
        )
