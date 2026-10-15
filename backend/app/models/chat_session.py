"""Chat Session model for storing chat history."""
from __future__ import annotations

from sqlalchemy import Column, String, Text, DateTime, ForeignKey, Boolean, Integer, UniqueConstraint, func, update, cast
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid
from typing import Optional

from app.models import Base


class ChatSession(Base):
    """
    Stores chat sessions for both agent testing and workflow debugging.
    Messages are stored as JSONB for flexibility with AI SDK message format.
    """
    __tablename__ = 'chat_sessions'

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

    # Context - either agent or workflow (one should be set)
    agent_id = Column(
        UUID(as_uuid=True),
        ForeignKey('custom_agents.id', ondelete='SET NULL'),
        nullable=True
    )
    workflow_id = Column(
        UUID(as_uuid=True),
        ForeignKey('workflows.id', ondelete='SET NULL'),
        nullable=True
    )
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey('projects.id', ondelete='SET NULL'),
        nullable=True,
        index=True
    )

    # Context type: 'agent' or 'workflow'
    context_type = Column(String(20), nullable=False, default='agent')

    # Session info
    title = Column(String(255), nullable=True)

    # Messages in AI SDK UIMessage format
    # [{id, role, content, parts, createdAt, ...}]
    messages = Column(JSONB, nullable=False, default=list)

    # Additional metadata (draft config snapshot, execution context, etc.)
    session_metadata = Column(JSONB, nullable=False, default=dict)

    # Draft mode - testing unsaved agent config
    is_draft = Column(Boolean, nullable=False, default=False)

    # Pinned sessions appear at top
    is_pinned = Column(Boolean, nullable=False, default=False)

    # Message tracking
    message_count = Column(Integer, nullable=False, default=0)
    last_message_at = Column(DateTime(timezone=True), nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    tenant = relationship("Tenant")
    user = relationship("User")
    agent = relationship("CustomAgent", back_populates="chat_sessions")
    workflow = relationship("Workflow", back_populates="chat_sessions")
    project = relationship("Project", back_populates="chat_sessions")

    def __repr__(self):
        return f"<ChatSession(id='{self.id}', title='{self.title}')>"

    def to_dict(self):
        """Convert to dictionary for API responses"""
        return {
            'id': str(self.id),
            'tenant_id': str(self.tenant_id),
            'user_id': str(self.user_id),
            'agent_id': str(self.agent_id) if self.agent_id else None,
            'workflow_id': str(self.workflow_id) if self.workflow_id else None,
            'project_id': str(self.project_id) if self.project_id else None,
            'context_type': self.context_type,
            'title': self.title,
            'messages': self.messages,
            'metadata': self.session_metadata,
            'is_draft': self.is_draft,
            'is_pinned': self.is_pinned,
            'message_count': self.message_count or 0,
            'last_message_at': self.last_message_at.isoformat() if self.last_message_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }

    @staticmethod
    def _strip_null_bytes(obj):
        """Recursively strip \\x00 from strings in dicts/lists.

        PostgreSQL JSONB cannot store the Unicode null character (U+0000).
        Some LLM providers (e.g. DeepSeek) occasionally emit it.
        """
        if isinstance(obj, str):
            return obj.replace("\x00", "")
        if isinstance(obj, dict):
            return {k: ChatSession._strip_null_bytes(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [ChatSession._strip_null_bytes(v) for v in obj]
        return obj

    async def add_message(
        self,
        role: str,
        content: str,
        metadata: dict = None,
        db: Optional[AsyncSession] = None,
    ) -> dict:
        """Add a message to the session and return the created message.

        When *db* is provided the message is appended server-side via a
        JSONB ``concat`` + ``jsonb_build_array`` expression, avoiding a
        full-array rebuild in Python memory.  After the UPDATE the ORM
        attributes are expired so the next access fetches fresh state.

        When *db* is ``None`` the legacy in-memory path is used (kept for
        backward compatibility with unit tests and callers that flush
        separately).
        """
        message = {
            "role": role,
            "content": self._strip_null_bytes(content),
            "timestamp": datetime.utcnow().isoformat(),
            "metadata": self._strip_null_bytes(metadata or {}),
        }

        if db is not None:
            # Server-side JSONB append — avoids pulling the entire array
            # into Python just to add one element.
            # ``jsonb_build_array`` wraps the dict so ``||`` (concat)
            # appends it as a single element rather than merging keys.
            stmt = (
                update(ChatSession)
                .where(ChatSession.id == self.id)
                .values(
                    messages=ChatSession.messages.concat(
                        func.jsonb_build_array(cast(message, JSONB))
                    ),
                    message_count=ChatSession.message_count + 1,
                    last_message_at=func.now(),
                )
            )
            await db.execute(stmt)
            # Expire cached ORM attributes so subsequent reads hit the DB.
            db.expire(self, ["messages", "message_count", "last_message_at"])
        else:
            # Legacy in-memory path (no db session available).
            if self.messages is None:
                self.messages = []
            self.messages = [*self.messages, message]
            self.message_count = len(self.messages)
            self.last_message_at = datetime.utcnow()

        return message

    def get_last_message(self) -> dict | None:
        """Get the last message in the session"""
        if self.messages and len(self.messages) > 0:
            return self.messages[-1]
        return None

    def generate_title(self) -> str:
        """Generate a title from the first user message"""
        if not self.messages:
            return "New Chat"

        for msg in self.messages:
            if msg.get('role') == 'user':
                content = msg.get('content', '')
                if isinstance(content, str):
                    # Truncate to first 50 chars
                    return content[:50] + ('...' if len(content) > 50 else '')
                # Handle parts format
                parts = msg.get('parts', [])
                for part in parts:
                    if part.get('type') == 'text':
                        text = part.get('text', '')
                        return text[:50] + ('...' if len(text) > 50 else '')

        return "New Chat"


class ChatSessionMessage(Base):
    """Read-model row: one legacy chat message with its renderable parts (chat-on-engine U10).

    Projected from ``chat_sessions.messages`` by ``app.services.chat_backfill`` and dual-written
    by ``persist_session``. The JSONB column stays the legacy source of truth; ``parts`` are
    copied as-is (``metadata.parts``) or synthesized as a single text part from ``content``.
    """
    __tablename__ = 'chat_session_messages'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey('tenants.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey('chat_sessions.id', ondelete='CASCADE'),
        nullable=False,
    )
    seq = Column(Integer, nullable=False)
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False, server_default='', default='')
    parts = Column(JSONB, nullable=False, default=list)
    # False when ``parts`` failed the schema check; the row is kept and flagged, never dropped.
    parts_valid = Column(Boolean, nullable=False, server_default='true', default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint('session_id', 'seq', name='uq_chat_session_messages_session_seq'),
    )
