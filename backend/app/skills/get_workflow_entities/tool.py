"""Get Workflow Entities Skill — wraps entity extraction lookup."""

from typing import Any, Dict, Optional

from app.agents.skills.context import SkillContext


async def get_workflow_entities(
    ctx: SkillContext,
    workflow_id: str,
    entity_type: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    """Get entities extracted from a specific workflow."""
    from app.agents.skills.memory import get_workflow_entities as _impl
    return await _impl(ctx, workflow_id, entity_type, limit)
