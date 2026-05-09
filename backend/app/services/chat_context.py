"""
ChatContext — shared database session + identity for pre-stream chat operations.

Part of the S5 audit fix: consolidate multiple AsyncSessionLocal() calls
per chat request into a single pre-stream session.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class ChatContext:
    """Shared database session + identity for pre-stream chat operations."""
    db: AsyncSession
    tenant_id: UUID
    user_id: UUID
