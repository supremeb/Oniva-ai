"""Agent Permission model for per-agent access control.

Part of Frontier-Inspired Platform Simplification (Phase D).
Allows admins to control which teams can edit instructions,
view logs, or deploy agents.
"""
from datetime import datetime
import uuid

from sqlalchemy import (
    Column, DateTime, ForeignKey, String, func, UniqueConstraint
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from app.models import Base


class AgentPermission(Base):
    """Per-agent permission controls.

    Maps (agent_id, permission_type) -> team_id to control
    which teams have which access to specific agents.
    """
    __tablename__ = "agent_permissions"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_id = Column(
        PGUUID(as_uuid=True),
        ForeignKey("custom_agents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    permission_type = Column(
        String(50),
        nullable=False,
        comment="Permission type: edit_instructions, view_logs, deploy"
    )
    team_id = Column(
        PGUUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=datetime.utcnow,
    )

    __table_args__ = (
        UniqueConstraint('agent_id', 'permission_type', 'team_id', name='uq_agent_permission'),
    )

    def __repr__(self) -> str:
        return f"<AgentPermission(agent={self.agent_id}, type='{self.permission_type}', team={self.team_id})>"

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "agent_id": str(self.agent_id),
            "permission_type": self.permission_type,
            "team_id": str(self.team_id),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
