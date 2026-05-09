"""Agent Registry Service for cross-team agent discovery and sharing.

Provides browse, publish, clone, and unpublish operations for the
organization-wide agent registry (Phase B: Registry-First Architecture).
"""
import logging
from uuid import UUID
from typing import Optional, List
from datetime import datetime

from sqlalchemy import select, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.custom_agent import CustomAgent

logger = logging.getLogger(__name__)


class AgentRegistryService:
    """Service for agent discovery, publish, and clone operations."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def browse_organization(
        self,
        tenant_id: UUID,
        query: Optional[str] = None,
        category: Optional[str] = None,
        compliance_tag: Optional[str] = None,
        sort_by: str = "clone_count",
        limit: int = 20,
        offset: int = 0,
    ) -> dict:
        """Browse agents published to organization (visibility=tenant).

        Returns:
            dict with 'agents' list and 'total' count.
        """
        # Base filter: same tenant, visibility=tenant, active
        base_filter = and_(
            CustomAgent.tenant_id == tenant_id,
            CustomAgent.visibility == 'tenant',
            CustomAgent.is_active == True,
        )

        # Optional search filter
        filters = [base_filter]
        if query:
            search = f"%{query}%"
            filters.append(
                or_(
                    CustomAgent.name.ilike(search),
                    CustomAgent.description.ilike(search),
                )
            )
        if category:
            filters.append(CustomAgent.category == category)
        if compliance_tag:
            # GIN index: compliance_tags @> ARRAY['HIPAA']
            filters.append(CustomAgent.compliance_tags.contains([compliance_tag]))

        combined = and_(*filters)

        # Count total
        count_query = select(func.count()).select_from(CustomAgent).where(combined)
        total_result = await self.db.execute(count_query)
        total = total_result.scalar() or 0

        # Fetch agents with sorting
        agents_query = select(CustomAgent).where(combined)

        if sort_by == "clone_count":
            agents_query = agents_query.order_by(CustomAgent.clone_count.desc())
        elif sort_by == "recent":
            agents_query = agents_query.order_by(CustomAgent.published_at.desc().nullslast())
        elif sort_by == "name":
            agents_query = agents_query.order_by(CustomAgent.name.asc())
        else:
            agents_query = agents_query.order_by(CustomAgent.clone_count.desc())

        agents_query = agents_query.offset(offset).limit(limit)
        result = await self.db.execute(agents_query)
        agents = result.scalars().all()

        return {
            "agents": agents,
            "total": total,
        }

    async def browse_team(
        self,
        tenant_id: UUID,
        team_id: UUID,
        query: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict:
        """Browse agents published to a specific team (visibility=team)."""
        base_filter = and_(
            CustomAgent.tenant_id == tenant_id,
            CustomAgent.team_id == team_id,
            CustomAgent.visibility == 'team',
            CustomAgent.is_active == True,
        )

        filters = [base_filter]
        if query:
            search = f"%{query}%"
            filters.append(
                or_(
                    CustomAgent.name.ilike(search),
                    CustomAgent.description.ilike(search),
                )
            )

        combined = and_(*filters)

        count_query = select(func.count()).select_from(CustomAgent).where(combined)
        total_result = await self.db.execute(count_query)
        total = total_result.scalar() or 0

        agents_query = (
            select(CustomAgent)
            .where(combined)
            .order_by(CustomAgent.clone_count.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self.db.execute(agents_query)
        agents = result.scalars().all()

        return {"agents": agents, "total": total}

    async def get_categories(self, tenant_id: UUID) -> List[dict]:
        """Get categories with counts for organization agents."""
        query = (
            select(
                CustomAgent.category,
                func.count(CustomAgent.id).label('count')
            )
            .where(
                CustomAgent.tenant_id == tenant_id,
                CustomAgent.visibility == 'tenant',
                CustomAgent.is_active == True,
                CustomAgent.category.isnot(None),
            )
            .group_by(CustomAgent.category)
            .order_by(func.count(CustomAgent.id).desc())
        )
        result = await self.db.execute(query)
        return [{"name": row.category, "count": row.count} for row in result.all()]

    async def publish_agent(
        self,
        agent_id: UUID,
        user_id: UUID,
        tenant_id: UUID,
        visibility: str,
        category: Optional[str] = None,
        compliance_tags: Optional[list[str]] = None,
    ) -> CustomAgent:
        """Publish agent with specified visibility.

        - team: Immediate publish (no approval needed)
        - tenant: Requires approval for org-wide visibility

        Returns the updated agent. For tenant visibility, the caller should
        create an approval request separately.
        """
        result = await self.db.execute(
            select(CustomAgent).where(
                CustomAgent.id == agent_id,
                CustomAgent.tenant_id == tenant_id,
            )
        )
        agent = result.scalar_one_or_none()
        if not agent:
            raise ValueError(f"Agent {agent_id} not found")

        agent.visibility = visibility
        agent.category = category or agent.category
        if compliance_tags is not None:
            agent.compliance_tags = compliance_tags
        agent.published_at = datetime.utcnow()

        await self.db.flush()
        await self.db.refresh(agent)
        return agent

    async def unpublish_agent(
        self,
        agent_id: UUID,
        user_id: UUID,
        tenant_id: UUID,
    ) -> CustomAgent:
        """Remove agent from registry (reset to private)."""
        result = await self.db.execute(
            select(CustomAgent).where(
                CustomAgent.id == agent_id,
                CustomAgent.tenant_id == tenant_id,
            )
        )
        agent = result.scalar_one_or_none()
        if not agent:
            raise ValueError(f"Agent {agent_id} not found")

        agent.visibility = 'private'
        agent.published_at = None

        await self.db.flush()
        await self.db.refresh(agent)
        return agent

    async def clone_agent(
        self,
        source_agent_id: UUID,
        user_id: UUID,
        tenant_id: UUID,
        new_name: Optional[str] = None,
    ) -> CustomAgent:
        """Clone a published agent to user's workspace.

        Creates a private copy owned by the user. Increments clone_count on source.
        """
        result = await self.db.execute(
            select(CustomAgent).where(
                CustomAgent.id == source_agent_id,
                CustomAgent.tenant_id == tenant_id,
            )
        )
        source = result.scalar_one_or_none()
        if not source:
            raise ValueError(f"Agent {source_agent_id} not found")

        # Create cloned agent (private, user-owned)
        cloned = CustomAgent(
            user_id=user_id,
            tenant_id=tenant_id,
            team_id=None,
            name=new_name or f"{source.name} (Copy)",
            description=source.description,
            icon=source.icon,
            agent_type=source.agent_type,
            model=source.model,
            system_prompt=source.system_prompt,
            temperature=source.temperature,
            max_tokens=source.max_tokens,
            skills=source.skills.copy() if source.skills else [],
            connector_ids=source.connector_ids.copy() if source.connector_ids else [],
            config=source.config.copy() if source.config else {},
            risk_tier=source.risk_tier,
            visibility='private',
            category=source.category,
            cloned_from_id=source.id,
            compliance_tags=source.compliance_tags.copy() if source.compliance_tags else [],
        )

        self.db.add(cloned)

        # Increment clone count on source
        source.clone_count = (source.clone_count or 0) + 1

        await self.db.flush()
        await self.db.refresh(cloned)
        return cloned
