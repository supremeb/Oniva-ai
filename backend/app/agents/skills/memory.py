"""
Memory Skills - Agent-Native Memory Access

Provides skills for agents to access the Enhanced Memory System:
- Episodic memory (past decisions with hybrid search)
- Workflow entities (companies, people, regulations)

Phase 3: Agent-Native Tools for Pragmatic Memory Enhancement.
"""

from typing import Dict, Any, List, Optional, Literal
from uuid import UUID

from app.agents.skills.context import SkillContext
from app.config import get_settings


async def store_decision(
    ctx: SkillContext,
    agent_name: str,
    action_type: Literal["analysis", "compliance_check", "recommendation", "research", "chat", "other"],
    context: str,
    decision: str,
    rationale: Optional[str] = None,
    entities: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Store a decision to episodic memory for future recall.

    Use this when you make an important decision that should influence
    future similar situations. The decision will be available via
    search_past_decisions() in future agent runs.

    Args:
        agent_name: Your agent name (e.g., "analysis", "compliance")
        action_type: Type of action ("analysis", "compliance_check", "recommendation", "research", "chat", "other")
        context: What was asked or the situation (e.g., "User asked about AAPL risk profile")
        decision: The decision you made (e.g., "Recommended moderate allocation based on volatility")
        rationale: Why you made this decision (optional but recommended)
        entities: List of entities involved (e.g., ["AAPL", "technology", "growth"])

    Returns:
        Confirmation with memory ID
    """
    settings = get_settings()
    if not getattr(settings, 'USE_EPISODIC_MEMORY', False):
        return {
            "success": False,
            "error": "Episodic memory feature is not enabled",
        }

    if not ctx.db or not ctx.tenant_id:
        return {
            "success": False,
            "error": "Database session or tenant not available",
        }

    try:
        from app.services.episodic_memory import get_episodic_memory_service
        service = get_episodic_memory_service()

        user_id = UUID(str(ctx.user_id)) if not isinstance(ctx.user_id, UUID) else ctx.user_id
        tenant_id = UUID(str(ctx.tenant_id)) if not isinstance(ctx.tenant_id, UUID) else ctx.tenant_id

        memory_id = await service.store_decision(
            db=ctx.db,
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=ctx.session_id,
            agent_name=agent_name,
            action_type=action_type,
            context=context,
            decision=decision,
            rationale=rationale,
            entities=entities or [],
        )

        return {
            "success": True,
            "memory_id": str(memory_id),
            "stored": True,
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
        }


async def search_past_decisions(
    ctx: SkillContext,
    query: str,
    limit: int = 3,
    search_mode: Literal["hybrid", "semantic", "keyword"] = "hybrid",
    agent_name: Optional[str] = None,
    action_type: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Search for similar past decisions using hybrid search.

    Use this before making important decisions to check if similar
    situations have been handled before. Returns past decisions
    with similarity scores.

    Args:
        query: What you're looking for (e.g., "risk assessment for tech stocks")
        limit: Maximum results (default 3 for token budget)
        search_mode: "hybrid" (semantic+keyword, best), "semantic", or "keyword"
        agent_name: Filter to decisions by specific agent (optional)
        action_type: Filter by action type (optional)

    Returns:
        List of similar past decisions with context and outcomes
    """
    settings = get_settings()
    if not getattr(settings, 'USE_EPISODIC_MEMORY', False):
        return {
            "success": False,
            "error": "Episodic memory feature is not enabled",
            "results": [],
        }

    if not ctx.db or not ctx.tenant_id:
        return {
            "success": False,
            "error": "Database session or tenant not available",
            "results": [],
        }

    try:
        from app.services.episodic_memory import get_episodic_memory_service
        service = get_episodic_memory_service()

        user_id = UUID(str(ctx.user_id)) if not isinstance(ctx.user_id, UUID) else ctx.user_id
        tenant_id = UUID(str(ctx.tenant_id)) if not isinstance(ctx.tenant_id, UUID) else ctx.tenant_id

        if search_mode == "hybrid":
            results = await service.hybrid_recall(
                db=ctx.db,
                tenant_id=tenant_id,
                user_id=user_id,
                query=query,
                limit=limit,
                agent_name=agent_name,
                action_type=action_type,
            )
        elif search_mode == "keyword":
            results = await service.keyword_recall(
                db=ctx.db,
                tenant_id=tenant_id,
                user_id=user_id,
                query=query,
                limit=limit,
            )
        else:  # semantic
            results = await service.recall_similar(
                db=ctx.db,
                tenant_id=tenant_id,
                user_id=user_id,
                query=query,
                limit=limit,
                agent_name=agent_name,
                action_type=action_type,
            )

        # Format results for agent consumption
        formatted = []
        for r in results:
            formatted.append({
                "id": str(r.id),
                "agent_name": r.agent_name,
                "action_type": r.action_type,
                "context_decision": r.context_decision,
                "rationale": r.rationale,
                "entities": r.entities,
                "similarity": r.similarity,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            })

        return {
            "success": True,
            "query": query,
            "search_mode": search_mode,
            "results": formatted,
            "count": len(formatted),
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "results": [],
        }


async def search_entities(
    ctx: SkillContext,
    entity_name: str,
    entity_type: Optional[str] = None,
    limit: int = 20,
) -> Dict[str, Any]:
    """
    Search for entities across workflows.

    Use this to find which workflows have analyzed a specific
    company, person, regulation, etc.

    Args:
        entity_name: Entity to search for (e.g., "Apple", "GDPR", "John Smith")
        entity_type: Filter by type ("company", "person", "regulation", etc.)
        limit: Maximum results

    Returns:
        List of entities with workflow references
    """
    if not ctx.db or not ctx.tenant_id:
        return {
            "success": False,
            "error": "Database session or tenant not available",
            "results": [],
        }

    try:
        from app.services.entity_extraction import get_entity_extraction_service
        service = get_entity_extraction_service()

        tenant_id = UUID(str(ctx.tenant_id)) if not isinstance(ctx.tenant_id, UUID) else ctx.tenant_id

        results = await service.search_by_entity(
            db=ctx.db,
            tenant_id=tenant_id,
            entity_name=entity_name,
            entity_type=entity_type,
            limit=limit,
        )

        return {
            "success": True,
            "query": entity_name,
            "entity_type": entity_type,
            "results": results,
            "count": len(results),
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "results": [],
        }


async def get_workflow_entities(
    ctx: SkillContext,
    workflow_id: str,
    entity_type: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    """
    Get entities extracted from a specific workflow.

    Use this to see what companies, people, regulations, etc.
    were mentioned in a workflow's outputs.

    Args:
        workflow_id: Workflow UUID
        entity_type: Filter by type (optional)
        limit: Maximum results

    Returns:
        List of entities from the workflow
    """
    if not ctx.db or not ctx.tenant_id:
        return {
            "success": False,
            "error": "Database session or tenant not available",
            "results": [],
        }

    try:
        from app.services.entity_extraction import get_entity_extraction_service
        service = get_entity_extraction_service()

        tenant_id = UUID(str(ctx.tenant_id)) if not isinstance(ctx.tenant_id, UUID) else ctx.tenant_id

        entities = await service.get_workflow_entities(
            db=ctx.db,
            tenant_id=tenant_id,
            workflow_id=UUID(workflow_id),
            entity_type=entity_type,
            limit=limit,
        )

        return {
            "success": True,
            "workflow_id": workflow_id,
            "entities": [e.to_dict() for e in entities],
            "count": len(entities),
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "results": [],
        }
