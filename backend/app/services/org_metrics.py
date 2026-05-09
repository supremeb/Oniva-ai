"""Org Admin Metrics service for dashboard aggregations.

Part of Frontier-Inspired Platform Simplification (Phase D).
Provides tenant-level overview, agent fleet stats, and user activity.
"""
from typing import Optional
from uuid import UUID
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func, case, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.custom_agent import CustomAgent
from app.models.user import User
from app.models.team import Team
from app.models.workflow import WorkflowExecution
from app.models.agent_feedback import AgentFeedback
from app.models.agent_permission import AgentPermission

logger = get_logger(__name__)


class OrgMetricsService:
    """Service for org-level admin dashboard metrics."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_overview(self, tenant_id: UUID) -> dict:
        """Get high-level org overview: agent count, user count, execution stats."""
        now = datetime.now(timezone.utc)
        thirty_days_ago = now - timedelta(days=30)

        # Total agents
        agent_count_result = await self.db.execute(
            select(func.count(CustomAgent.id)).where(
                CustomAgent.tenant_id == tenant_id
            )
        )
        total_agents = agent_count_result.scalar_one()

        # Active agents
        active_agents_result = await self.db.execute(
            select(func.count(CustomAgent.id)).where(
                and_(
                    CustomAgent.tenant_id == tenant_id,
                    CustomAgent.is_active == True,
                )
            )
        )
        active_agents = active_agents_result.scalar_one()

        # Total users
        user_count_result = await self.db.execute(
            select(func.count(User.id)).where(
                User.tenant_id == tenant_id
            )
        )
        total_users = user_count_result.scalar_one()

        # Total teams
        team_count_result = await self.db.execute(
            select(func.count(Team.id)).where(
                Team.tenant_id == tenant_id
            )
        )
        total_teams = team_count_result.scalar_one()

        # Workflow executions in last 30 days
        exec_count_result = await self.db.execute(
            select(func.count(WorkflowExecution.id)).where(
                and_(
                    WorkflowExecution.tenant_id == tenant_id,
                    WorkflowExecution.started_at >= thirty_days_ago,
                )
            )
        )
        executions_30d = exec_count_result.scalar_one()

        # Feedback stats (last 30 days)
        feedback_result = await self.db.execute(
            select(
                func.count(AgentFeedback.id),
                func.count(case((AgentFeedback.rating == "positive", 1))),
            ).where(
                and_(
                    AgentFeedback.tenant_id == tenant_id,
                    AgentFeedback.created_at >= thirty_days_ago,
                )
            )
        )
        fb_row = feedback_result.one()
        total_feedback = fb_row[0]
        positive_feedback = fb_row[1]
        satisfaction_rate = (positive_feedback / total_feedback * 100) if total_feedback > 0 else None

        return {
            "total_agents": total_agents,
            "active_agents": active_agents,
            "total_users": total_users,
            "total_teams": total_teams,
            "executions_30d": executions_30d,
            "total_feedback_30d": total_feedback,
            "satisfaction_rate": satisfaction_rate,
        }

    async def get_agent_fleet(
        self,
        tenant_id: UUID,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """Get agent fleet list with status, feedback counts, and usage."""
        # Base query for agents
        query = (
            select(
                CustomAgent.id,
                CustomAgent.name,
                CustomAgent.description,
                CustomAgent.is_active,
                CustomAgent.model,
                CustomAgent.created_at,
                CustomAgent.updated_at,
            )
            .where(CustomAgent.tenant_id == tenant_id)
        )

        # Sort
        sort_col = getattr(CustomAgent, sort_by, CustomAgent.created_at)
        if sort_order == "asc":
            query = query.order_by(sort_col.asc())
        else:
            query = query.order_by(sort_col.desc())

        # Total count
        count_result = await self.db.execute(
            select(func.count(CustomAgent.id)).where(
                CustomAgent.tenant_id == tenant_id
            )
        )
        total = count_result.scalar_one()

        # Paginate
        query = query.limit(limit).offset(offset)
        result = await self.db.execute(query)
        agents = result.all()

        # Get feedback counts per agent
        agent_ids = [a.id for a in agents]
        feedback_counts = {}
        if agent_ids:
            fb_result = await self.db.execute(
                select(
                    AgentFeedback.agent_id,
                    func.count(AgentFeedback.id).label("total"),
                    func.count(case((AgentFeedback.rating == "positive", 1))).label("positive"),
                ).where(
                    AgentFeedback.agent_id.in_(agent_ids)
                ).group_by(AgentFeedback.agent_id)
            )
            for row in fb_result.all():
                feedback_counts[row.agent_id] = {
                    "total": row.total,
                    "positive": row.positive,
                }

        fleet = []
        for a in agents:
            fb = feedback_counts.get(a.id, {"total": 0, "positive": 0})
            fleet.append({
                "id": str(a.id),
                "name": a.name,
                "description": a.description,
                "is_active": a.is_active,
                "model": a.model,
                "feedback_total": fb["total"],
                "feedback_positive": fb["positive"],
                "satisfaction_rate": (
                    round(fb["positive"] / fb["total"] * 100, 1) if fb["total"] > 0 else None
                ),
                "created_at": a.created_at.isoformat() if a.created_at else None,
                "updated_at": a.updated_at.isoformat() if a.updated_at else None,
            })

        return {
            "agents": fleet,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    async def get_agent_detail(self, agent_id: UUID, tenant_id: UUID) -> Optional[dict]:
        """Get detailed metrics for a single agent."""
        # Get agent
        result = await self.db.execute(
            select(CustomAgent).where(
                and_(
                    CustomAgent.id == agent_id,
                    CustomAgent.tenant_id == tenant_id,
                )
            )
        )
        agent = result.scalar_one_or_none()
        if not agent:
            return None

        now = datetime.now(timezone.utc)
        thirty_days_ago = now - timedelta(days=30)

        # Recent feedback
        fb_result = await self.db.execute(
            select(AgentFeedback)
            .where(
                and_(
                    AgentFeedback.agent_id == agent_id,
                    AgentFeedback.tenant_id == tenant_id,
                )
            )
            .order_by(desc(AgentFeedback.created_at))
            .limit(20)
        )
        recent_feedback = [f.to_dict() for f in fb_result.scalars().all()]

        # Feedback summary
        fb_summary_result = await self.db.execute(
            select(
                func.count(AgentFeedback.id),
                func.count(case((AgentFeedback.rating == "positive", 1))),
                func.count(case((AgentFeedback.rating == "negative", 1))),
            ).where(
                and_(
                    AgentFeedback.agent_id == agent_id,
                    AgentFeedback.tenant_id == tenant_id,
                    AgentFeedback.created_at >= thirty_days_ago,
                )
            )
        )
        fb_summary = fb_summary_result.one()

        # Permissions
        perm_result = await self.db.execute(
            select(AgentPermission).where(
                and_(
                    AgentPermission.agent_id == agent_id,
                    AgentPermission.tenant_id == tenant_id,
                )
            )
        )
        permissions = [p.to_dict() for p in perm_result.scalars().all()]

        return {
            "agent": agent.to_dict(),
            "feedback_summary": {
                "total": fb_summary[0],
                "positive": fb_summary[1],
                "negative": fb_summary[2],
                "satisfaction_rate": (
                    round(fb_summary[1] / fb_summary[0] * 100, 1) if fb_summary[0] > 0 else None
                ),
            },
            "recent_feedback": recent_feedback,
            "permissions": permissions,
        }

    async def get_agent_permissions(self, agent_id: UUID, tenant_id: UUID) -> list[dict]:
        """Get all permissions for an agent."""
        result = await self.db.execute(
            select(AgentPermission).where(
                and_(
                    AgentPermission.agent_id == agent_id,
                    AgentPermission.tenant_id == tenant_id,
                )
            )
        )
        return [p.to_dict() for p in result.scalars().all()]

    async def set_agent_permission(
        self,
        agent_id: UUID,
        permission_type: str,
        team_id: UUID,
        tenant_id: UUID,
    ) -> dict:
        """Grant a permission to a team for an agent."""
        valid_types = {"edit_instructions", "view_logs", "deploy"}
        if permission_type not in valid_types:
            raise ValueError(f"Invalid permission_type. Must be one of: {valid_types}")

        # Check agent exists
        agent_result = await self.db.execute(
            select(CustomAgent.id).where(
                and_(
                    CustomAgent.id == agent_id,
                    CustomAgent.tenant_id == tenant_id,
                )
            )
        )
        if not agent_result.scalar_one_or_none():
            raise ValueError("Agent not found")

        # Check team exists
        team_result = await self.db.execute(
            select(Team.id).where(
                and_(
                    Team.id == team_id,
                    Team.tenant_id == tenant_id,
                )
            )
        )
        if not team_result.scalar_one_or_none():
            raise ValueError("Team not found")

        # Upsert: check if already exists
        existing = await self.db.execute(
            select(AgentPermission).where(
                and_(
                    AgentPermission.agent_id == agent_id,
                    AgentPermission.permission_type == permission_type,
                    AgentPermission.team_id == team_id,
                )
            )
        )
        perm = existing.scalar_one_or_none()
        if perm:
            return perm.to_dict()

        perm = AgentPermission(
            agent_id=agent_id,
            permission_type=permission_type,
            team_id=team_id,
            tenant_id=tenant_id,
        )
        self.db.add(perm)
        await self.db.flush()
        logger.info(f"Granted {permission_type} on agent {agent_id} to team {team_id}")
        return perm.to_dict()

    async def revoke_agent_permission(
        self,
        permission_id: UUID,
        tenant_id: UUID,
    ) -> bool:
        """Revoke a specific permission."""
        result = await self.db.execute(
            select(AgentPermission).where(
                and_(
                    AgentPermission.id == permission_id,
                    AgentPermission.tenant_id == tenant_id,
                )
            )
        )
        perm = result.scalar_one_or_none()
        if not perm:
            return False

        await self.db.delete(perm)
        await self.db.flush()
        logger.info(f"Revoked permission {permission_id}")
        return True
