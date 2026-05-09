"""
Tenant Skill Service
Provides tenant-isolated access to skills, combining built-in skills
with tenant-specific custom skills from the database.
"""

import logging
from typing import Any, Dict, List, Optional, Union
from uuid import UUID

from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.skills.registry import SkillRegistry
from app.models.skills import Skill as SkillModel

logger = logging.getLogger(__name__)


class TenantSkillService:
    """
    Service for tenant-isolated skill access.

    Combines:
    - Built-in system skills (available to all tenants)
    - Tenant-specific custom skills (from database)

    Usage:
        service = TenantSkillService(db, tenant_id)
        skills = await service.list_skills()
        skill = await service.get_skill("web_search")
    """

    def __init__(self, db: AsyncSession, tenant_id: Union[str, UUID]):
        self.db = db
        self.tenant_id = UUID(tenant_id) if isinstance(tenant_id, str) else tenant_id

    async def list_skills(
        self,
        *,
        category: Optional[str] = None,
        include_disabled: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        List all skills available to this tenant.

        Args:
            category: Filter by category
            include_disabled: Include disabled skills

        Returns:
            List of skill dictionaries
        """
        skills: List[Dict[str, Any]] = []

        # 1. Add built-in system skills from registry
        for skill_info in SkillRegistry.list_skills():
            if category and skill_info.get("category") != category:
                continue
            if not include_disabled and not skill_info.get("enabled", True):
                continue

            skill_info["source"] = "system"
            skill_info["tenant_id"] = None
            skills.append(skill_info)

        # 2. Add tenant-specific custom skills from database
        conditions = [
            or_(
                SkillModel.tenant_id == self.tenant_id,  # Tenant's custom skills
                SkillModel.tenant_id.is_(None),  # System skills in DB
            ),
            SkillModel.is_custom == True,  # Only custom skills from DB
        ]

        if category:
            conditions.append(SkillModel.category == category)
        if not include_disabled:
            conditions.append(SkillModel.enabled == True)

        query = select(SkillModel).where(and_(*conditions))
        result = await self.db.execute(query)
        db_skills = result.scalars().all()

        for skill in db_skills:
            skills.append({
                "name": skill.name,
                "description": skill.description,
                "category": skill.category,
                "enabled": skill.enabled,
                "version": skill.version,
                "source": "custom",
                "tenant_id": str(skill.tenant_id) if skill.tenant_id else None,
                "id": str(skill.id),
            })

        return skills

    async def get_skill(self, name: str) -> Optional[Dict[str, Any]]:
        """
        Get a specific skill by name.

        Checks built-in registry first, then tenant's custom skills.

        Args:
            name: Skill name

        Returns:
            Skill dictionary if found, None otherwise
        """
        # 1. Check built-in registry first
        manifest = SkillRegistry.get_manifest(name)
        if manifest:
            return {
                "name": manifest.name,
                "description": manifest.description,
                "category": manifest.category,
                "enabled": manifest.enabled,
                "source": "system",
                "tenant_id": None,
            }

        # 2. Check tenant's custom skills in database
        query = select(SkillModel).where(
            and_(
                SkillModel.name == name,
                or_(
                    SkillModel.tenant_id == self.tenant_id,
                    SkillModel.tenant_id.is_(None),
                ),
                SkillModel.is_custom == True,
            )
        )
        result = await self.db.execute(query)
        skill = result.scalar_one_or_none()

        if skill:
            return {
                "name": skill.name,
                "description": skill.description,
                "category": skill.category,
                "enabled": skill.enabled,
                "version": skill.version,
                "source": "custom",
                "tenant_id": str(skill.tenant_id) if skill.tenant_id else None,
                "id": str(skill.id),
                "code": skill.code,
                "configuration": skill.configuration,
            }

        return None

    async def skill_available(self, name: str) -> bool:
        """
        Check if a skill is available and enabled for this tenant.

        Args:
            name: Skill name

        Returns:
            True if skill exists and is enabled
        """
        skill = await self.get_skill(name)
        return skill is not None and skill.get("enabled", True)

    async def get_categories(self) -> List[str]:
        """
        Get all unique skill categories available to this tenant.

        Returns:
            List of category names
        """
        categories = set()

        # From registry
        for manifest in SkillRegistry.list_manifests().values():
            categories.add(manifest.category)

        # From database
        query = select(SkillModel.category).where(
            and_(
                or_(
                    SkillModel.tenant_id == self.tenant_id,
                    SkillModel.tenant_id.is_(None),
                ),
                SkillModel.enabled == True,
            )
        ).distinct()

        result = await self.db.execute(query)
        for row in result.scalars().all():
            if row:
                categories.add(row)

        return sorted(list(categories))

    async def get_skill_stats(self) -> Dict[str, Any]:
        """
        Get skill statistics for this tenant.

        Returns:
            Dict with skill counts by type and category
        """
        # Count system skills
        system_skills = SkillRegistry.list_skills()
        system_count = len(system_skills)
        system_enabled = len([s for s in system_skills if s.get("enabled", True)])

        # Count custom skills
        custom_query = select(SkillModel).where(
            and_(
                or_(
                    SkillModel.tenant_id == self.tenant_id,
                    SkillModel.tenant_id.is_(None),
                ),
                SkillModel.is_custom == True,
            )
        )
        result = await self.db.execute(custom_query)
        custom_skills = result.scalars().all()
        custom_count = len(custom_skills)
        custom_enabled = len([s for s in custom_skills if s.enabled])

        # Categories
        categories = await self.get_categories()

        return {
            "tenant_id": str(self.tenant_id),
            "system_skills": {
                "total": system_count,
                "enabled": system_enabled,
            },
            "custom_skills": {
                "total": custom_count,
                "enabled": custom_enabled,
            },
            "total_skills": system_count + custom_count,
            "categories": categories,
        }
