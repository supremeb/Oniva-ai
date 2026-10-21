"""Agent Chat Session model for Agent Studio testing."""
from sqlalchemy import Column, String, DateTime, ForeignKey, Integer, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from app.models import Base
from app.models._clock import utc_now


class AgentChatSession(Base):
    """
    Stores chat sessions for Agent Studio testing.
    Different from ChatSession - focused on testing with feedback collection.
    Messages include feedback and test case linkage.
    """
    __tablename__ = 'agent_chat_sessions'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Multi-tenancy
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey('tenants.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )

    # Owner
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey('users.id', ondelete='CASCADE'),
        nullable=False
    )

    # Agent reference
    # Note: No FK constraint - supports both custom agents (UUIDs) and built-in agents
    # (deterministic UUIDs generated from string IDs like 'analysis', 'data-retrieval')
    agent_id = Column(
        UUID(as_uuid=True),
        nullable=False,
        index=True
    )
    agent_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey('agent_versions.id', ondelete='SET NULL'),
        nullable=True
    )

    # Session state
    name = Column(String(255), nullable=True)
    status = Column(String(50), default='active')  # 'active', 'archived'

    # Messages stored as JSONB array
    # Format: [{
    #   id: uuid,
    #   role: 'user' | 'assistant',
    #   content: string,
    #   timestamp: iso_datetime,
    #   feedback: 'positive' | 'negative' | null,
    #   test_case_id: uuid | null,
    #   tokens_used: int,
    #   latency_ms: int
    # }]
    messages = Column(JSONB, nullable=False, default=list)

    # Session metrics
    total_messages = Column(Integer, default=0)
    total_tokens_used = Column(Integer, default=0)
    total_latency_ms = Column(Integer, default=0)
    positive_feedback_count = Column(Integer, default=0)
    negative_feedback_count = Column(Integer, default=0)

    # Optimistic locking for concurrent modification detection
    row_version = Column(Integer, default=1, nullable=False)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), default=utc_now)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), default=utc_now, onupdate=utc_now)
    last_message_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    tenant = relationship("Tenant")
    user = relationship("User")
    # Note: No agent relationship - agent_id can be custom agent UUID or built-in agent UUID
    agent_version = relationship("AgentVersion")
    test_cases = relationship("AgentTestCase", back_populates="source_session")

    def __repr__(self):
        return f"<AgentChatSession(id='{self.id}', agent_id='{self.agent_id}')>"

    def to_dict(self):
        """Convert to dictionary for API responses"""
        return {
            'id': str(self.id),
            'tenant_id': str(self.tenant_id),
            'user_id': str(self.user_id),
            'agent_id': str(self.agent_id),
            'agent_version_id': str(self.agent_version_id) if self.agent_version_id else None,
            'name': self.name,
            'status': self.status,
            'messages': self.messages or [],
            'total_messages': self.total_messages or 0,
            'total_tokens_used': self.total_tokens_used or 0,
            'total_latency_ms': self.total_latency_ms or 0,
            'positive_feedback_count': self.positive_feedback_count or 0,
            'negative_feedback_count': self.negative_feedback_count or 0,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'last_message_at': self.last_message_at.isoformat() if self.last_message_at else None,
            'row_version': self.row_version or 1,
        }

    @staticmethod
    def _strip_null_bytes(obj):
        """Recursively strip \\x00 from strings — PostgreSQL JSONB rejects U+0000."""
        if isinstance(obj, str):
            return obj.replace("\x00", "")
        if isinstance(obj, dict):
            return {k: AgentChatSession._strip_null_bytes(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [AgentChatSession._strip_null_bytes(v) for v in obj]
        return obj

    def add_message(
        self,
        message_id: str,
        role: str,
        content: str,
        tokens_used: int = 0,
        latency_ms: int = 0
    ) -> dict:
        """Add a message to the session and return the created message."""
        if self.messages is None:
            self.messages = []

        message = {
            "id": message_id,
            "role": role,
            "content": self._strip_null_bytes(content),
            "timestamp": datetime.utcnow().isoformat(),
            "feedback": None,
            "test_case_id": None,
            "tokens_used": tokens_used,
            "latency_ms": latency_ms,
        }

        self.messages = [*self.messages, message]
        self.total_messages = len(self.messages)
        self.total_tokens_used = (self.total_tokens_used or 0) + tokens_used
        self.total_latency_ms = (self.total_latency_ms or 0) + latency_ms
        self.last_message_at = datetime.utcnow()

        return message

    def set_message_feedback(
        self,
        message_id: str,
        feedback: str,
        test_case_id: str = None
    ) -> bool:
        """Set feedback on a message and optionally link to test case."""
        if not self.messages:
            return False

        updated = False
        new_messages = []
        for msg in self.messages:
            if msg.get('id') == message_id:
                msg['feedback'] = feedback
                msg['test_case_id'] = test_case_id
                updated = True
            new_messages.append(msg)

        if updated:
            self.messages = new_messages
            if feedback == 'positive':
                self.positive_feedback_count = (self.positive_feedback_count or 0) + 1
            elif feedback == 'negative':
                self.negative_feedback_count = (self.negative_feedback_count or 0) + 1

        return updated

    def get_message_by_id(self, message_id: str) -> dict | None:
        """Get a specific message by ID"""
        if not self.messages:
            return None

        for msg in self.messages:
            if msg.get('id') == message_id:
                return msg
        return None

    def get_previous_message(self, message_id: str) -> dict | None:
        """Get the message before the specified message (for getting user input)"""
        if not self.messages:
            return None

        for i, msg in enumerate(self.messages):
            if msg.get('id') == message_id and i > 0:
                return self.messages[i - 1]
        return None
