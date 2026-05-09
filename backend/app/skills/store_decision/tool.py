"""Store Decision Skill — wraps episodic memory store_decision."""

from typing import Any, Dict, List, Literal, Optional

from app.agents.skills.context import SkillContext


async def store_decision(
    ctx: SkillContext,
    agent_name: str,
    action_type: Literal["analysis", "compliance_check", "recommendation", "research", "chat", "other"],
    context: str,
    decision: str,
    rationale: Optional[str] = None,
    entities: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Store a decision to episodic memory for future recall."""
    from app.agents.skills.memory import store_decision as _impl
    return await _impl(ctx, agent_name, action_type, context, decision, rationale, entities)
