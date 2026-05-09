"""
Assess Query Complexity Skill
Binary classification: does this query need deep work or not?
"""

from typing import Any, Dict

from app.agents.skills.context import SkillContext


async def assess_query_complexity(
    ctx: SkillContext,
    needs_deep_work: bool,
    reasoning: str,
) -> Dict[str, Any]:
    """
    Assess whether this query requires deep work.

    Call this FIRST before any other tools. Deep work is needed when the query requires:
    - Multi-step research across multiple sources
    - Comparison or analysis of 3+ entities
    - Producing deliverables (reports, presentations, spreadsheets)
    - Tasks that would take a human analyst 30+ minutes

    Most queries do NOT need deep work. Default to needs_deep_work=False.

    Args:
        ctx: Skill execution context
        needs_deep_work: Whether the query requires deep work
        reasoning: Brief explanation of why or why not

    Returns:
        Dict with recommendation and reasoning
    """
    if needs_deep_work:
        return {
            "recommendation": "deep_work",
            "reasoning": reasoning,
            "message": "Deep work recommended. The system will suggest this to the user.",
        }
    return {
        "recommendation": "standard",
        "reasoning": reasoning,
        "message": "Standard response. Proceed with available tools.",
    }
