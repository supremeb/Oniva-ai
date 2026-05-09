"""Agent RBAC Service for Agent Studio (Phase A.2).

Provides role-based access control for agent operations with a simplified
4-role hierarchy: viewer < member < admin < owner.
"""
from uuid import UUID
from typing import Optional, List, Tuple
from enum import Enum

from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.team import TeamMembership, TeamRole
from app.models.custom_agent import CustomAgent
from app.exceptions import PermissionDeniedError, EntityNotFoundError


class AgentPermission(str, Enum):
    """Permissions for agent operations."""
    VIEW = "view"           # viewer+: Read agent configs, view executions
    EDIT = "edit"           # member+: Create/edit agents, run tests
    PUBLISH = "publish"     # member+: Publish agents to team/organization
    DEPLOY = "deploy"       # admin+: Deploy to production, rollback
    MANAGE = "manage"       # owner: Delete agent, approve deployments, manage team


# Role hierarchy: each role inherits permissions from lower roles
ROLE_HIERARCHY = {
    TeamRole.VIEWER: [AgentPermission.VIEW],
    TeamRole.MEMBER: [AgentPermission.VIEW, AgentPermission.EDIT, AgentPermission.PUBLISH],
    TeamRole.ADMIN: [AgentPermission.VIEW, AgentPermission.EDIT, AgentPermission.PUBLISH, AgentPermission.DEPLOY],
    TeamRole.OWNER: [AgentPermission.VIEW, AgentPermission.EDIT, AgentPermission.PUBLISH, AgentPermission.DEPLOY, AgentPermission.MANAGE],
}


class AgentRBACService:
    """
    Role-Based Access Control for Agent Studio operations.

    Access rules:
    1. If agent has team_id: Check user's role in that team
    2. If agent has no team_id: Only the user_id owner has access

    Usage:
        rbac = AgentRBACService(db)

        # Check permission
        if await rbac.can_edit_agent(user_id, agent_id, tenant_id):
            # proceed with edit

        # Require permission (raises PermissionDeniedError)
        await rbac.require_permission(user_id, agent_id, tenant_id, AgentPermission.DEPLOY)
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_user_role_for_agent(
        self,
        user_id: UUID,
        agent_id: UUID,
        tenant_id: UUID
    ) -> Tuple[Optional[TeamRole], CustomAgent]:
        """
        Get the user's effective role for an agent.

        Returns:
            Tuple of (TeamRole if user has access else None, loaded CustomAgent)
        """
        # First, get the agent
        agent_query = select(CustomAgent).where(
            CustomAgent.id == agent_id,
            CustomAgent.tenant_id == tenant_id
        )
        result = await self.db.execute(agent_query)
        agent = result.scalar_one_or_none()

        if not agent:
            raise EntityNotFoundError("CustomAgent", agent_id)

        # Case 1: No team - only user_id owner has access (as owner)
        if agent.team_id is None:
            if agent.user_id == user_id:
                return TeamRole.OWNER, agent
            return None, agent

        # Case 2: Has team - check membership
        membership_query = select(TeamMembership).where(
            TeamMembership.team_id == agent.team_id,
            TeamMembership.user_id == user_id
        )
        result = await self.db.execute(membership_query)
        membership = result.scalar_one_or_none()

        if membership:
            return membership.role, agent

        return None, agent

    async def has_permission(
        self,
        user_id: UUID,
        agent_id: UUID,
        tenant_id: UUID,
        permission: AgentPermission
    ) -> Tuple[bool, CustomAgent]:
        """
        Check if a user has a specific permission for an agent.

        Args:
            user_id: User UUID
            agent_id: Agent UUID
            tenant_id: Tenant UUID
            permission: Required permission

        Returns:
            Tuple of (True if user has permission, loaded CustomAgent)
        """
        role, agent = await self.get_user_role_for_agent(user_id, agent_id, tenant_id)

        if role is None:
            return False, agent

        return permission in ROLE_HIERARCHY.get(role, []), agent

    async def require_permission(
        self,
        user_id: UUID,
        agent_id: UUID,
        tenant_id: UUID,
        permission: AgentPermission
    ) -> CustomAgent:
        """
        Require a permission, raising PermissionDeniedError if not granted.

        Args:
            user_id: User UUID
            agent_id: Agent UUID
            tenant_id: Tenant UUID
            permission: Required permission

        Returns:
            The loaded CustomAgent ORM instance

        Raises:
            PermissionDeniedError: If user lacks permission
        """
        has_perm, agent = await self.has_permission(user_id, agent_id, tenant_id, permission)
        if not has_perm:
            # Determine the minimum required role for this permission
            required_role = "unknown"
            for role, perms in ROLE_HIERARCHY.items():
                if permission in perms:
                    required_role = role.value
                    break

            raise PermissionDeniedError(
                operation=f"{permission.value} agent {agent_id}",
                required_role=required_role
            )

        return agent

    # Convenience methods
    async def can_view_agent(self, user_id: UUID, agent_id: UUID, tenant_id: UUID) -> bool:
        """Check if user can view agent."""
        has_perm, _agent = await self.has_permission(user_id, agent_id, tenant_id, AgentPermission.VIEW)
        return has_perm

    async def can_edit_agent(self, user_id: UUID, agent_id: UUID, tenant_id: UUID) -> bool:
        """Check if user can edit agent."""
        has_perm, _agent = await self.has_permission(user_id, agent_id, tenant_id, AgentPermission.EDIT)
        return has_perm

    async def can_deploy_agent(self, user_id: UUID, agent_id: UUID, tenant_id: UUID) -> bool:
        """Check if user can deploy agent."""
        has_perm, _agent = await self.has_permission(user_id, agent_id, tenant_id, AgentPermission.DEPLOY)
        return has_perm

    async def can_manage_agent(self, user_id: UUID, agent_id: UUID, tenant_id: UUID) -> bool:
        """Check if user can manage agent (delete, approve)."""
        has_perm, _agent = await self.has_permission(user_id, agent_id, tenant_id, AgentPermission.MANAGE)
        return has_perm

    async def list_agents_for_user(
        self,
        user_id: UUID,
        tenant_id: UUID,
        min_permission: AgentPermission = AgentPermission.VIEW,
        include_inactive: bool = False
    ) -> List[CustomAgent]:
        """
        List all agents the user has access to with minimum permission.

        Uses a single LEFT OUTER JOIN query to fetch agents that are either:
        - Personal agents (team_id IS NULL) owned by the user, OR
        - Team agents where the user has a membership with sufficient role

        Args:
            user_id: User UUID
            tenant_id: Tenant UUID
            min_permission: Minimum required permission level
            include_inactive: If True, include inactive agents

        Returns:
            List of CustomAgent instances
        """
        # Find roles that have the minimum permission
        allowed_roles = [
            role.value for role, perms in ROLE_HIERARCHY.items()
            if min_permission in perms
        ]

        # Single query with LEFT OUTER JOIN:
        # - Personal agents (team_id IS NULL, user_id matches) have no join match — included via LEFT join
        # - Team agents match via TeamMembership with role check
        query = (
            select(CustomAgent)
            .outerjoin(
                TeamMembership,
                and_(
                    TeamMembership.team_id == CustomAgent.team_id,
                    TeamMembership.user_id == user_id,
                    TeamMembership.role.in_(allowed_roles)
                )
            )
            .where(
                CustomAgent.tenant_id == tenant_id,
                or_(
                    # Personal agents: no team, owned by user
                    and_(CustomAgent.team_id.is_(None), CustomAgent.user_id == user_id),
                    # Team agents: user has membership with sufficient role
                    TeamMembership.id.isnot(None)
                )
            )
        )

        if not include_inactive:
            query = query.where(CustomAgent.is_active == True)  # noqa: E712

        query = query.order_by(CustomAgent.created_at.desc())

        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def is_team_owner(
        self,
        user_id: UUID,
        team_id: UUID
    ) -> bool:
        """Check if user is an owner of the specified team."""
        query = select(TeamMembership).where(
            TeamMembership.team_id == team_id,
            TeamMembership.user_id == user_id,
            TeamMembership.role == TeamRole.OWNER.value
        )
        result = await self.db.execute(query)
        return result.scalar_one_or_none() is not None
