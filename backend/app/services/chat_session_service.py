"""Chat Session Service with optimistic locking for Agent Studio (Phase A.1).

Provides concurrency-safe operations for AgentChatSession by using
row versioning to detect and prevent conflicting writes.
"""
from uuid import UUID
from typing import Optional, Dict, Any
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_chat_session import AgentChatSession
from app.exceptions import OptimisticLockError, EntityNotFoundError


class ChatSessionService:
    """
    Service for managing Agent Chat Sessions with optimistic locking.

    Optimistic locking ensures that concurrent modifications to a session
    (e.g., during evaluation) don't silently overwrite each other.

    Usage:
        service = ChatSessionService(db)
        session = await service.get_session(session_id, tenant_id, user_id)

        # Make modifications
        session.add_message(...)

        # Save with version check - raises OptimisticLockError on conflict
        await service.update_session(session, expected_version=session.row_version)
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_session(
        self,
        session_id: UUID,
        tenant_id: UUID,
        user_id: UUID
    ) -> AgentChatSession:
        """
        Retrieve a chat session by ID with tenant/user isolation.

        Args:
            session_id: The session UUID
            tenant_id: Tenant UUID for isolation
            user_id: User UUID for ownership check

        Returns:
            AgentChatSession instance

        Raises:
            EntityNotFoundError: If session not found or access denied
        """
        query = select(AgentChatSession).where(
            AgentChatSession.id == session_id,
            AgentChatSession.tenant_id == tenant_id,
            AgentChatSession.user_id == user_id
        )
        result = await self.db.execute(query)
        session = result.scalar_one_or_none()

        if not session:
            raise EntityNotFoundError("AgentChatSession", session_id)

        return session

    async def update_session(
        self,
        session: AgentChatSession,
        expected_version: int
    ) -> AgentChatSession:
        """
        Update a session with optimistic locking.

        Uses WHERE row_version = expected_version to ensure no concurrent
        modifications occurred since the session was read.

        Args:
            session: The session to update
            expected_version: The row_version when the session was read

        Returns:
            Updated AgentChatSession with incremented row_version

        Raises:
            OptimisticLockError: If the session was modified by another process
        """
        # Prepare the update with version check
        stmt = (
            update(AgentChatSession)
            .where(
                AgentChatSession.id == session.id,
                AgentChatSession.row_version == expected_version
            )
            .values(
                messages=session.messages,
                total_messages=session.total_messages,
                total_tokens_used=session.total_tokens_used,
                total_latency_ms=session.total_latency_ms,
                positive_feedback_count=session.positive_feedback_count,
                negative_feedback_count=session.negative_feedback_count,
                last_message_at=session.last_message_at,
                updated_at=datetime.utcnow(),
                row_version=expected_version + 1
            )
            .returning(AgentChatSession.row_version)
        )

        result = await self.db.execute(stmt)
        row = result.first()

        if row is None:
            # The version didn't match - concurrent modification detected
            # Fetch current version for error reporting
            current = await self.db.execute(
                select(AgentChatSession.row_version).where(
                    AgentChatSession.id == session.id
                )
            )
            current_version = current.scalar_one_or_none()

            raise OptimisticLockError(
                message=f"Session {session.id} was modified by another process",
                entity_id=session.id,
                expected_version=expected_version,
                actual_version=current_version
            )

        # Update the in-memory session's version
        session.row_version = expected_version + 1
        await self.db.commit()

        return session

    async def add_message_safe(
        self,
        session_id: UUID,
        tenant_id: UUID,
        user_id: UUID,
        message_id: str,
        role: str,
        content: str,
        tokens_used: int = 0,
        latency_ms: int = 0,
        max_retries: int = 3
    ) -> Dict[str, Any]:
        """
        Add a message to a session with automatic retry on conflicts.

        This is a convenience method that handles optimistic locking
        retries automatically.

        Args:
            session_id: Session UUID
            tenant_id: Tenant UUID
            user_id: User UUID
            message_id: Unique message ID
            role: Message role (user/assistant)
            content: Message content
            tokens_used: Token count for the message
            latency_ms: Latency in milliseconds
            max_retries: Maximum retry attempts on conflict

        Returns:
            The created message dict

        Raises:
            OptimisticLockError: If all retries exhausted
        """
        for attempt in range(max_retries):
            try:
                session = await self.get_session(session_id, tenant_id, user_id)
                expected_version = session.row_version

                message = session.add_message(
                    message_id=message_id,
                    role=role,
                    content=content,
                    tokens_used=tokens_used,
                    latency_ms=latency_ms
                )

                await self.update_session(session, expected_version)
                return message

            except OptimisticLockError:
                if attempt == max_retries - 1:
                    raise
                # Refresh and retry
                await self.db.rollback()
                continue

    async def set_feedback_safe(
        self,
        session_id: UUID,
        tenant_id: UUID,
        user_id: UUID,
        message_id: str,
        feedback: str,
        test_case_id: Optional[str] = None,
        max_retries: int = 3
    ) -> bool:
        """
        Set feedback on a message with automatic retry on conflicts.

        Args:
            session_id: Session UUID
            tenant_id: Tenant UUID
            user_id: User UUID
            message_id: Message UUID to update
            feedback: 'positive' or 'negative'
            test_case_id: Optional linked test case UUID
            max_retries: Maximum retry attempts on conflict

        Returns:
            True if feedback was set

        Raises:
            OptimisticLockError: If all retries exhausted
        """
        for attempt in range(max_retries):
            try:
                session = await self.get_session(session_id, tenant_id, user_id)
                expected_version = session.row_version

                success = session.set_message_feedback(
                    message_id=message_id,
                    feedback=feedback,
                    test_case_id=test_case_id
                )

                if success:
                    await self.update_session(session, expected_version)

                return success

            except OptimisticLockError:
                if attempt == max_retries - 1:
                    raise
                await self.db.rollback()
                continue

        return False
