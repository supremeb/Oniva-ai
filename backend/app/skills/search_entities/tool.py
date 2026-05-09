"""Search Entities Skill — wraps entity extraction search."""

from typing import Any, Dict, Optional

from app.agents.skills.context import SkillContext


async def search_entities(
    ctx: SkillContext,
    entity_name: str,
    entity_type: Optional[str] = None,
    limit: int = 20,
) -> Dict[str, Any]:
    """Search for entities across workflows."""
    from app.agents.skills.memory import search_entities as _impl
    return await _impl(ctx, entity_name, entity_type, limit)
