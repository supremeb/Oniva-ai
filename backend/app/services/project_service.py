"""Project Service for managing project workspaces.

Handles CRUD operations, member management, and permission checks.
Uses flush/refresh (NOT commit) to preserve RLS tenant context.
"""
from uuid import UUID
from typing import Optional, Literal
from datetime import datetime, timezone

from sqlalchemy import select, update, func, case, literal_column, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.knowledge_base import KnowledgeBase
from app.models.chat_session import ChatSession
from app.models.engine import EngineConversation
from app.models.user import User
from app.exceptions import OptimisticLockError, EntityNotFoundError, PermissionDeniedError


ProjectRole = Literal["owner", "editor", "viewer"]


def _sort_timestamp(value: Optional[datetime]) -> float:
    """Sort key for a datetime that may be naive (SQLite) or aware."""
    if value is None:
        return 0.0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


class ProjectService:
    """Service for project workspace operations with RBAC and optimistic locking."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ──────────────────────────────────────────────
    # Permission helpers
    # ──────────────────────────────────────────────

    async def get_user_role(
        self,
        project_id: UUID,
        user_id: UUID,
        tenant_id: UUID,
    ) -> Optional[ProjectRole]:
        """Return the user's effective role on a project, or None if no access."""
        project = await self._get_project_raw(project_id, tenant_id)
        if not project:
            return None

        if project.owner_id == user_id:
            return "owner"

        query = select(ProjectMember.role).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
        )
        result = await self.db.execute(query)
        role = result.scalar_one_or_none()
        return role  # "editor", "viewer", or None

    async def check_permission(
        self,
        project_id: UUID,
        user_id: UUID,
        tenant_id: UUID,
        required_role: ProjectRole = "viewer",
    ) -> ProjectRole:
        """Check permission and return role. Raises on denied."""
        role = await self.get_user_role(project_id, user_id, tenant_id)
        if role is None:
            raise EntityNotFoundError("Project", project_id)

        hierarchy = {"owner": 3, "editor": 2, "viewer": 1}
        if hierarchy.get(role, 0) < hierarchy.get(required_role, 0):
            raise PermissionDeniedError("access project", required_role)

        return role

    # ──────────────────────────────────────────────
    # CRUD
    # ──────────────────────────────────────────────

    async def create_project(
        self,
        tenant_id: UUID,
        user_id: UUID,
        name: str,
        description: Optional[str] = None,
        instructions: Optional[str] = None,
        default_model: Optional[str] = None,
        settings: Optional[dict] = None,
    ) -> Project:
        """Create a new project with an implicit knowledge base."""
        # Create implicit KB for project files
        kb = KnowledgeBase(
            tenant_id=tenant_id,
            name=f"_project:{name}",
            source_type="project",
            description=f"Internal KB for project: {name}",
            created_by=user_id,
        )
        self.db.add(kb)
        await self.db.flush()
        await self.db.refresh(kb)

        project = Project(
            tenant_id=tenant_id,
            owner_id=user_id,
            name=name,
            description=description,
            instructions=instructions,
            default_model=default_model,
            knowledge_base_id=kb.id,
            settings=settings or {},
        )
        self.db.add(project)
        await self.db.flush()
        await self.db.refresh(project)

        # Update KB name with project ID for guaranteed uniqueness
        kb.name = f"_project:{project.id}"
        await self.db.flush()

        return project

    async def get_project(
        self,
        project_id: UUID,
        user_id: UUID,
        tenant_id: UUID,
    ) -> dict:
        """Get a project with enriched metadata. Verifies access permission."""
        role = await self.check_permission(project_id, user_id, tenant_id)
        project = await self._get_project_raw(project_id, tenant_id)

        # Gather counts
        member_count = await self._count_members(project_id)
        file_count = await self._count_files(project.knowledge_base_id) if project.knowledge_base_id else 0
        session_count = await self._count_sessions(project_id, user_id)

        data = project.to_dict()
        data["member_count"] = member_count
        data["file_count"] = file_count
        data["session_count"] = session_count
        data["user_role"] = role
        return data

    async def list_projects(
        self,
        user_id: UUID,
        tenant_id: UUID,
        page: int = 1,
        page_size: int = 20,
        include_archived: bool = False,
    ) -> dict:
        """List user's own + shared projects with pagination."""
        # Owned projects
        owned = (
            select(Project.id, literal_column("'owner'").label("user_role"))
            .where(
                Project.tenant_id == tenant_id,
                Project.owner_id == user_id,
            )
        )

        # Shared projects (user is a member)
        shared = (
            select(Project.id, ProjectMember.role.label("user_role"))
            .join(ProjectMember, ProjectMember.project_id == Project.id)
            .where(
                Project.tenant_id == tenant_id,
                ProjectMember.user_id == user_id,
            )
        )

        # The hidden automations project (plan 003 U1) never lists.
        not_automation = or_(
            Project.settings["system"].as_string().is_(None),
            Project.settings["system"].as_string() != "automations",
        )
        owned = owned.where(not_automation)
        shared = shared.where(not_automation)

        if not include_archived:
            owned = owned.where(Project.is_archived == False)
            shared = shared.where(Project.is_archived == False)

        # Union to combine — no duplicates since owner is NOT in project_members
        union_subq = owned.union(shared).subquery()

        # Count total
        count_query = select(func.count()).select_from(union_subq)
        total_result = await self.db.execute(count_query)
        total = total_result.scalar() or 0

        # Fetch project IDs + roles with pagination
        page_query = (
            select(union_subq.c.id, union_subq.c.user_role)
            .order_by(union_subq.c.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        page_result = await self.db.execute(page_query)
        rows = page_result.all()

        if not rows:
            return {"projects": [], "total": total, "page": page, "page_size": page_size}

        # Load full project objects
        project_ids = [r.id for r in rows]
        role_map = {r.id: r.user_role for r in rows}

        projects_query = (
            select(Project)
            .where(Project.id.in_(project_ids))
            .order_by(Project.updated_at.desc())
        )
        projects_result = await self.db.execute(projects_query)
        projects = projects_result.scalars().all()

        # Enrich with counts
        enriched = []
        for p in projects:
            data = p.to_dict()
            data["user_role"] = role_map.get(p.id, "viewer")
            data["member_count"] = await self._count_members(p.id)
            data["file_count"] = await self._count_files(p.knowledge_base_id) if p.knowledge_base_id else 0
            data["session_count"] = await self._count_sessions(p.id, user_id)
            enriched.append(data)

        return {"projects": enriched, "total": total, "page": page, "page_size": page_size}

    async def update_project(
        self,
        project_id: UUID,
        user_id: UUID,
        tenant_id: UUID,
        row_version: int,
        name: Optional[str] = None,
        description: Optional[str] = None,
        instructions: Optional[str] = None,
        default_model: Optional[str] = None,
        settings: Optional[dict] = None,
    ) -> Project:
        """Update a project with optimistic locking. Requires editor+ role."""
        await self.check_permission(project_id, user_id, tenant_id, required_role="editor")

        updates = {}
        if name is not None:
            updates["name"] = name
        if description is not None:
            updates["description"] = description
        if instructions is not None:
            updates["instructions"] = instructions
        if default_model is not None:
            updates["default_model"] = default_model
        if settings is not None:
            updates["settings"] = settings

        if not updates:
            project = await self._get_project_raw(project_id, tenant_id)
            return project

        updates["row_version"] = row_version + 1
        updates["updated_at"] = func.now()

        stmt = (
            update(Project)
            .where(
                Project.id == project_id,
                Project.tenant_id == tenant_id,
                Project.row_version == row_version,
            )
            .values(**updates)
            .returning(Project.id)
        )
        result = await self.db.execute(stmt)
        updated_id = result.scalar_one_or_none()

        if updated_id is None:
            raise OptimisticLockError(
                "Project was modified by another request",
                entity_id=project_id,
                expected_version=row_version,
            )

        await self.db.flush()
        project = await self._get_project_raw(project_id, tenant_id)
        return project

    async def archive_project(
        self,
        project_id: UUID,
        user_id: UUID,
        tenant_id: UUID,
        is_archived: bool,
    ) -> Project:
        """Archive or unarchive a project. Owner only."""
        await self.check_permission(project_id, user_id, tenant_id, required_role="owner")

        stmt = (
            update(Project)
            .where(Project.id == project_id, Project.tenant_id == tenant_id)
            .values(is_archived=is_archived, updated_at=func.now())
        )
        await self.db.execute(stmt)
        await self.db.flush()
        project = await self._get_project_raw(project_id, tenant_id)
        return project

    # ──────────────────────────────────────────────
    # Member management
    # ──────────────────────────────────────────────

    async def add_member(
        self,
        project_id: UUID,
        user_id: UUID,
        role: str,
        invited_by: UUID,
        tenant_id: UUID,
    ) -> ProjectMember:
        """Add a member to a project. Owner only."""
        await self.check_permission(project_id, invited_by, tenant_id, required_role="owner")

        # Prevent adding owner as a member
        project = await self._get_project_raw(project_id, tenant_id)
        if project.owner_id == user_id:
            raise PermissionDeniedError("add owner as member", "owner")

        member = ProjectMember(
            project_id=project_id,
            user_id=user_id,
            role=role,
            invited_by=invited_by,
            tenant_id=tenant_id,
        )
        self.db.add(member)
        await self.db.flush()
        await self.db.refresh(member)
        return member

    async def list_members(
        self,
        project_id: UUID,
        user_id: UUID,
        tenant_id: UUID,
    ) -> list[dict]:
        """List project members including owner. Requires viewer+ access."""
        await self.check_permission(project_id, user_id, tenant_id, required_role="viewer")
        project = await self._get_project_raw(project_id, tenant_id)

        # Build owner entry
        owner_query = select(User).where(User.id == project.owner_id)
        owner_result = await self.db.execute(owner_query)
        owner = owner_result.scalar_one_or_none()

        members_list = []
        if owner:
            members_list.append({
                "id": None,
                "project_id": str(project_id),
                "user_id": str(owner.id),
                "role": "owner",
                "invited_by": None,
                "user_email": owner.email,
                "user_name": getattr(owner, "full_name", None) or owner.email,
                "created_at": project.created_at.isoformat() if project.created_at else None,
            })

        # Non-owner members with user info
        query = (
            select(ProjectMember, User)
            .join(User, ProjectMember.user_id == User.id)
            .where(ProjectMember.project_id == project_id)
            .order_by(ProjectMember.created_at)
        )
        result = await self.db.execute(query)
        for member, user in result.all():
            members_list.append({
                "id": str(member.id),
                "project_id": str(member.project_id),
                "user_id": str(member.user_id),
                "role": member.role,
                "invited_by": str(member.invited_by) if member.invited_by else None,
                "user_email": user.email,
                "user_name": getattr(user, "full_name", None) or user.email,
                "created_at": member.created_at.isoformat() if member.created_at else None,
            })

        return members_list

    async def update_member_role(
        self,
        project_id: UUID,
        target_user_id: UUID,
        role: str,
        acting_user_id: UUID,
        tenant_id: UUID,
    ) -> ProjectMember:
        """Update a member's role. Owner only."""
        await self.check_permission(project_id, acting_user_id, tenant_id, required_role="owner")

        query = select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == target_user_id,
        )
        result = await self.db.execute(query)
        member = result.scalar_one_or_none()

        if not member:
            raise EntityNotFoundError("ProjectMember", target_user_id)

        member.role = role
        await self.db.flush()
        await self.db.refresh(member)
        return member

    async def remove_member(
        self,
        project_id: UUID,
        target_user_id: UUID,
        acting_user_id: UUID,
        tenant_id: UUID,
    ) -> None:
        """Remove a member from a project. Owner can remove anyone; members can leave."""
        project = await self._get_project_raw(project_id, tenant_id)
        if not project:
            raise EntityNotFoundError("Project", project_id)

        # Owner cannot remove themselves (must transfer ownership first)
        if project.owner_id == target_user_id:
            raise PermissionDeniedError("remove owner", "transfer ownership first")

        # Only owner or the member themselves can remove
        is_owner = project.owner_id == acting_user_id
        is_self = acting_user_id == target_user_id
        if not is_owner and not is_self:
            raise PermissionDeniedError("remove member", "owner")

        query = select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == target_user_id,
        )
        result = await self.db.execute(query)
        member = result.scalar_one_or_none()

        if not member:
            raise EntityNotFoundError("ProjectMember", target_user_id)

        await self.db.delete(member)

        # Detach the removed member's sessions from the project
        stmt = (
            update(ChatSession)
            .where(
                ChatSession.project_id == project_id,
                ChatSession.user_id == target_user_id,
            )
            .values(project_id=None)
        )
        await self.db.execute(stmt)
        await self.db.flush()

    async def transfer_ownership(
        self,
        project_id: UUID,
        new_owner_id: UUID,
        acting_user_id: UUID,
        tenant_id: UUID,
    ) -> Project:
        """Transfer project ownership. Only current owner can transfer."""
        await self.check_permission(project_id, acting_user_id, tenant_id, required_role="owner")

        # New owner must be an existing member
        query = select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == new_owner_id,
        )
        result = await self.db.execute(query)
        member = result.scalar_one_or_none()

        if not member:
            raise EntityNotFoundError("ProjectMember", new_owner_id)

        # Remove new owner from members table (they become owner via owner_id)
        await self.db.delete(member)

        # Add old owner as editor in members table
        old_owner_member = ProjectMember(
            project_id=project_id,
            user_id=acting_user_id,
            role="editor",
            invited_by=new_owner_id,
            tenant_id=tenant_id,
        )
        self.db.add(old_owner_member)

        # Update project owner_id
        stmt = (
            update(Project)
            .where(Project.id == project_id, Project.tenant_id == tenant_id)
            .values(owner_id=new_owner_id, updated_at=func.now())
        )
        await self.db.execute(stmt)
        await self.db.flush()

        project = await self._get_project_raw(project_id, tenant_id)
        return project

    # ──────────────────────────────────────────────
    # Project sessions
    # ──────────────────────────────────────────────

    async def list_project_sessions(
        self,
        project_id: UUID,
        user_id: UUID,
        tenant_id: UUID,
    ) -> list[dict]:
        """List current user's legacy sessions and engine conversations within a project (KTD2)."""
        await self.check_permission(project_id, user_id, tenant_id, required_role="viewer")

        legacy_query = select(ChatSession).where(
            ChatSession.project_id == project_id,
            ChatSession.user_id == user_id,
            ChatSession.tenant_id == tenant_id,
        )
        legacy = (await self.db.execute(legacy_query)).scalars().all()

        engine_query = select(EngineConversation).where(
            EngineConversation.project_id == project_id,
            EngineConversation.user_id == user_id,
            EngineConversation.tenant_id == tenant_id,
            EngineConversation.archived_at.is_(None),
        )
        engine = (await self.db.execute(engine_query)).scalars().all()

        rows = [
            (s.updated_at, {**s.to_dict(), "source": "legacy"}) for s in legacy
        ] + [
            (c.updated_at, {
                "id": str(c.conversation_id),
                "source": "engine",
                "project_id": str(c.project_id),
                "title": c.title,
                "pinned": bool(c.pinned),
                "legacy_session_id": str(c.legacy_session_id) if c.legacy_session_id else None,
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "updated_at": c.updated_at.isoformat() if c.updated_at else None,
            }) for c in engine
        ]
        rows.sort(key=lambda r: _sort_timestamp(r[0]), reverse=True)
        return [data for _, data in rows]

    # ──────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────

    async def _get_project_raw(self, project_id: UUID, tenant_id: UUID) -> Optional[Project]:
        """Fetch a project without permission checks."""
        query = select(Project).where(
            Project.id == project_id,
            Project.tenant_id == tenant_id,
        )
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def _count_members(self, project_id: UUID) -> int:
        query = select(func.count()).where(ProjectMember.project_id == project_id)
        result = await self.db.execute(query)
        return (result.scalar() or 0) + 1  # +1 for owner

    async def _count_files(self, kb_id: UUID) -> int:
        from app.models.knowledge_document import KnowledgeDocument
        query = select(func.count()).where(KnowledgeDocument.knowledge_base_id == kb_id)
        result = await self.db.execute(query)
        return result.scalar() or 0

    async def _count_sessions(self, project_id: UUID, user_id: UUID) -> int:
        query = select(func.count()).where(
            ChatSession.project_id == project_id,
            ChatSession.user_id == user_id,
        )
        result = await self.db.execute(query)
        return result.scalar() or 0
