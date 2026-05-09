"""Agent Test Case model for Agent Studio evaluation."""
from sqlalchemy import Column, String, DateTime, ForeignKey, Boolean, Text, Integer, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from app.models import Base


class AgentTestCase(Base):
    """
    Stores test cases for agent evaluation.
    Can be created from chat feedback or manually.
    Used for accuracy measurement and deploy gates.
    """
    __tablename__ = 'agent_test_cases'

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

    # Test case content
    name = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)
    category = Column(String(100), nullable=True)

    # Input/Output
    input = Column(Text, nullable=False)
    expected_output = Column(Text, nullable=True)
    context = Column(JSONB, nullable=True)

    # Source tracking
    source = Column(String(50), nullable=False)  # 'chat_feedback', 'manual', 'imported'
    source_session_id = Column(
        UUID(as_uuid=True),
        ForeignKey('agent_chat_sessions.id', ondelete='SET NULL'),
        nullable=True
    )
    source_message_id = Column(UUID(as_uuid=True), nullable=True)

    # Feedback type - is this a positive example or negative?
    is_positive = Column(Boolean, nullable=False, default=True)

    # Evaluation config
    evaluation_type = Column(String(50), default='llm_judge')  # 'exact', 'contains', 'llm_judge'
    evaluation_criteria = Column(JSONB, nullable=True)

    # Execution history - stores last evaluation result
    # Format: {passed: bool, actual_output: str, score: float, evaluated_at: datetime}
    last_result = Column(JSONB, nullable=True)
    pass_count = Column(Integer, default=0)
    fail_count = Column(Integer, default=0)

    # Status
    is_active = Column(Boolean, default=True)
    is_baseline = Column(Boolean, default=False)  # Include in baseline evaluation

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), default=datetime.utcnow, onupdate=datetime.utcnow)
    last_evaluated_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    tenant = relationship("Tenant")
    agent = relationship("CustomAgent")
    source_session = relationship("AgentChatSession", back_populates="test_cases")

    def __repr__(self):
        return f"<AgentTestCase(id='{self.id}', name='{self.name}')>"

    def to_dict(self):
        """Convert to dictionary for API responses"""
        return {
            'id': str(self.id),
            'tenant_id': str(self.tenant_id),
            'agent_id': str(self.agent_id),
            'name': self.name,
            'description': self.description,
            'category': self.category,
            'input': self.input,
            'expected_output': self.expected_output,
            'context': self.context,
            'source': self.source,
            'source_session_id': str(self.source_session_id) if self.source_session_id else None,
            'source_message_id': str(self.source_message_id) if self.source_message_id else None,
            'is_positive': self.is_positive,
            'evaluation_type': self.evaluation_type,
            'evaluation_criteria': self.evaluation_criteria,
            'last_result': self.last_result,
            'pass_count': self.pass_count or 0,
            'fail_count': self.fail_count or 0,
            'is_active': self.is_active,
            'is_baseline': self.is_baseline,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'last_evaluated_at': self.last_evaluated_at.isoformat() if self.last_evaluated_at else None,
        }

    def record_result(self, passed: bool, actual_output: str, score: float = None) -> None:
        """Record the result of running this test case."""
        self.last_result = {
            'passed': passed,
            'actual_output': actual_output,
            'score': score,
            'evaluated_at': datetime.utcnow().isoformat(),
        }
        self.last_evaluated_at = datetime.utcnow()

        if passed:
            self.pass_count = (self.pass_count or 0) + 1
        else:
            self.fail_count = (self.fail_count or 0) + 1

    @property
    def accuracy(self) -> float | None:
        """Calculate accuracy from pass/fail counts."""
        total = (self.pass_count or 0) + (self.fail_count or 0)
        if total == 0:
            return None
        return (self.pass_count or 0) / total

    @staticmethod
    def generate_name_from_input(input_text: str) -> str:
        """Generate a test case name from input text."""
        if not input_text:
            return "Unnamed test case"
        # Truncate to first 50 chars
        truncated = input_text[:50].strip()
        if len(input_text) > 50:
            truncated += "..."
        return truncated
