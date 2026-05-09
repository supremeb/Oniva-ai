"""
Ask User Skill
Pause the autonomous agent loop to ask the user a question.
Supports structured multiple-choice options via AgentQuestion UI.
"""

import logging
from typing import Any

from app.agents.skills.context import SkillContext
from app.agents.skills.registry import skill

logger = logging.getLogger(__name__)


@skill(
    name="ask_user",
    description="Pause execution to ask the user a clarifying question. "
    "Provide options for structured multiple-choice when possible.",
    category="planning",
    version="2.0.0",
    author="oniva",
)
async def ask_user(
    ctx: SkillContext,
    question: str,
    options: list[dict[str, str]] | None = None,
    multi_select: bool = False,
) -> dict[str, Any]:
    """Ask the user a question, optionally with multiple-choice options.

    Args:
        question: The question to ask.
        options: Optional list of options. Each dict must have "label" (required),
                 and optionally "id" and "description".
        multi_select: If True, user can select multiple options.
    """
    if not question or not question.strip():
        return {"success": False, "error": "Question cannot be empty"}

    validated_options = None
    if options:
        validated_options = []
        for i, opt in enumerate(options):
            if not isinstance(opt, dict) or "label" not in opt:
                logger.warning("Skipping malformed option at index %d: %s", i, opt)
                continue
            validated_options.append({
                "id": opt.get("id", f"opt_{i}"),
                "label": opt["label"],
                "description": opt.get("description", ""),
            })
        if not validated_options:
            validated_options = None  # Fall back to free-text

    return {
        "success": True,
        "type": "human_input_required",
        "question": question.strip(),
        "options": validated_options,
        "multi_select": multi_select,
        "session_id": str(ctx.session_id) if ctx.session_id else None,
    }
