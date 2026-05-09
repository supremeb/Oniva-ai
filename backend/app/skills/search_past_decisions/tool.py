"""Search Past Decisions Skill — wraps episodic memory search."""

from typing import Any, Dict, Literal, Optional

from app.agents.skills.context import SkillContext


async def search_past_decisions(
    ctx: SkillContext,
    query: str,
    limit: int = 3,
    search_mode: Literal["hybrid", "semantic", "keyword"] = "hybrid",
    agent_name: Optional[str] = None,
    action_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Search for similar past decisions using hybrid search."""
    from app.agents.skills.memory import search_past_decisions as _impl
    return await _impl(ctx, query, limit, search_mode, agent_name, action_type)
