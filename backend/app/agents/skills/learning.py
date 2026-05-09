"""
Learning Skills - Agent-Native Learning Access

Provides skills for agents to explicitly save and search learnings:
- save_learning: Save a discovered pattern or gotcha
- search_learnings: Search for relevant learnings from past experience
"""

from typing import Any, Dict, Literal
from uuid import UUID

from app.agents.skills.context import SkillContext
from app.config import get_settings


async def save_learning(
    ctx: SkillContext,
    title: str,
    content: str,
    pattern_type: Literal[
        "data_quality", "api_error", "calculation", "domain_insight", "general"
    ] = "general",
) -> Dict[str, Any]:
    """
    Save a discovered pattern or gotcha for future reference.

    Use this when you discover something important during execution that
    should be remembered for future runs - error patterns, API quirks,
    data quality issues, domain insights, etc.

    Args:
        title: Short title for the learning (e.g., "Yahoo Finance API rate limit")
        content: Full explanation of the learning and how to handle it
        pattern_type: Category: data_quality, api_error, calculation, domain_insight, general

    Returns:
        Confirmation with learning ID, or duplicate notice
    """
    settings = get_settings()
    if not getattr(settings, 'USE_AGENT_LEARNING', False):
        return {
            "success": False,
            "error": "Agent learning feature is not enabled",
        }

    if not ctx.db or not ctx.tenant_id:
        return {
            "success": False,
            "error": "Database session or tenant not available",
        }

    try:
        from app.services.agent_learning import get_agent_learning_service
        service = get_agent_learning_service()

        tenant_id = UUID(str(ctx.tenant_id)) if not isinstance(ctx.tenant_id, UUID) else ctx.tenant_id

        # Use agent_id from context if available
        agent_id = None
        if hasattr(ctx, 'agent_id') and ctx.agent_id:
            agent_id = UUID(str(ctx.agent_id)) if not isinstance(ctx.agent_id, UUID) else ctx.agent_id

        learning_id = await service.save_learning(
            db=ctx.db,
            tenant_id=tenant_id,
            title=title,
            content=content,
            agent_id=agent_id,
            pattern_type=pattern_type,
        )

        if learning_id:
            return {
                "success": True,
                "learning_id": str(learning_id),
                "message": f"Learning saved: {title}",
            }
        else:
            return {
                "success": True,
                "learning_id": None,
                "message": "Duplicate learning already exists",
            }
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to save learning: {str(e)}",
        }


async def search_learnings(
    ctx: SkillContext,
    query: str,
    limit: int = 5,
) -> Dict[str, Any]:
    """
    Search for previously discovered patterns relevant to current task.

    Use this to check if there are known gotchas or patterns before
    attempting an operation that might fail.

    Args:
        query: What to search for (e.g., "Yahoo Finance API errors")
        limit: Max results (default 5)

    Returns:
        List of relevant learnings with similarity scores
    """
    settings = get_settings()
    if not getattr(settings, 'USE_AGENT_LEARNING', False):
        return {
            "success": False,
            "error": "Agent learning feature is not enabled",
        }

    if not ctx.db or not ctx.tenant_id:
        return {
            "success": False,
            "error": "Database session or tenant not available",
        }

    try:
        from app.services.agent_learning import get_agent_learning_service
        service = get_agent_learning_service()

        tenant_id = UUID(str(ctx.tenant_id)) if not isinstance(ctx.tenant_id, UUID) else ctx.tenant_id

        agent_id = None
        if hasattr(ctx, 'agent_id') and ctx.agent_id:
            agent_id = UUID(str(ctx.agent_id)) if not isinstance(ctx.agent_id, UUID) else ctx.agent_id

        rows = await service.search_relevant_learnings(
            db=ctx.db,
            tenant_id=tenant_id,
            query=query,
            agent_id=agent_id,
            limit=min(limit, 10),
            min_similarity=0.5,  # Slightly lower threshold for explicit search
        )

        learnings = [
            {
                "id": str(row.id),
                "title": row.title,
                "content": row.content,
                "pattern_type": row.pattern_type,
                "source": row.source,
                "similarity": round(float(row.similarity), 3),
            }
            for row in rows
        ]

        return {
            "success": True,
            "learnings": learnings,
            "count": len(learnings),
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to search learnings: {str(e)}",
        }


# Skill definitions for registry
LEARNING_SKILLS = {
    "save_learning": {
        "name": "save_learning",
        "display_name": "Save Learning",
        "description": "Save a discovered pattern or gotcha for future reference",
        "category": "learning",
        "parameters": {
            "title": {"type": "string", "description": "Short title for the learning"},
            "content": {"type": "string", "description": "Full explanation"},
            "pattern_type": {
                "type": "string",
                "description": "Category: data_quality, api_error, calculation, domain_insight, general",
            },
        },
        "required_parameters": ["title", "content"],
    },
    "search_learnings": {
        "name": "search_learnings",
        "display_name": "Search Learnings",
        "description": "Search for previously discovered patterns relevant to current task",
        "category": "learning",
        "parameters": {
            "query": {"type": "string", "description": "What to search for"},
            "limit": {"type": "integer", "description": "Max results (default 5)"},
        },
        "required_parameters": ["query"],
    },
}
