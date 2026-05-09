"""
Present Plan Skill
Present a work plan to the user for approval before starting autonomous execution.
"""

from typing import Any, Dict, List

from app.agents.skills.context import SkillContext
from app.agents.skills.registry import skill


@skill(
    name="present_plan",
    description="Present a work plan to the user for approval before starting autonomous execution. "
    "Use this when a task requires more than 3 tool calls or involves multiple data sources.",
    category="planning",
    version="1.0.0",
    author="oniva",
)
async def present_plan(
    ctx: SkillContext,
    title: str,
    steps: List[str],
) -> Dict[str, Any]:
    """
    Present a structured plan for user approval.

    The chat endpoint detects `type: "plan"` in the result and serializes it
    as a structured message part in the SSE stream. The frontend renders it
    as a PlanCard with Proceed / Edit / Just Answer options.

    Args:
        ctx: Skill execution context
        title: A concise title for the plan
        steps: Ordered list of steps the agent will execute

    Returns:
        Dict with plan data that the chat endpoint emits as a plan SSE event
    """
    if not steps:
        return {
            "success": False,
            "error": "Plan must have at least one step.",
        }

    if not title.strip():
        return {
            "success": False,
            "error": "Plan title cannot be empty.",
        }

    return {
        "success": True,
        "type": "plan",
        "title": title.strip(),
        "steps": [s.strip() for s in steps if s.strip()],
    }
