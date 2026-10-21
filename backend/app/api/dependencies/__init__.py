"""
Shared API dependencies for Oniva.

Common validation and access control dependencies used across API endpoints.
"""
from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from uuid import UUID
from typing import Optional
from dataclasses import dataclass

from app.core.database import get_db
from app.auth.dependencies import get_current_user
from app.auth.providers.base import AuthUser
from app.models.custom_agent import CustomAgent
from app.core.builtin_agents import uuid_to_slug


# Built-in agent types that don't require database lookup
BUILTIN_AGENTS = {
    "general": {
        "name": "General Assistant",
        "type": "general",
        "description": "Conversational AI helper - handles greetings, general questions, and guides to specialized agents",
    },
    "data-retrieval": {
        "name": "Data Retrieval Agent",
        "type": "data",
        "description": "Fetches and aggregates financial data from multiple sources",
    },
    "analysis": {
        "name": "Analysis Agent",
        "type": "analysis",
        "description": "Performs financial analysis and calculations",
    },
    "compliance": {
        "name": "Compliance Agent",
        "type": "compliance",
        "description": "Checks regulatory compliance and risk assessment",
    },
    "research": {
        "name": "Research Agent",
        "type": "research",
        "description": "RAG-based document research and Q&A",
    },
    "kyc-aml": {
        "name": "KYC/AML Agent",
        "type": "compliance",
        "description": "Validates Know Your Customer and Anti-Money Laundering requirements",
    },
    "market-abuse": {
        "name": "Market Abuse Agent",
        "type": "compliance",
        "description": "Detects potential insider trading or market manipulation",
    },
    "report-generator": {
        "name": "Regulatory Report Generator",
        "type": "compliance",
        "description": "Synthesizes analysis into formal regulatory reports",
    },
}


@dataclass
class AgentInfo:
    """Unified agent info for both custom and built-in agents."""
    id: str
    name: str
    type: str
    description: str
    is_builtin: bool
    custom_agent: Optional[CustomAgent] = None


def parse_agent_id(agent_id_str: str) -> tuple[Optional[UUID], str]:
    """
    Parse agent_id string to (uuid, original_str).
    Returns (UUID, str) if valid UUID, (None, str) if built-in agent type.
    """
    try:
        return UUID(agent_id_str), agent_id_str
    except ValueError:
        return None, agent_id_str


async def verify_agent_access(
    agent_id: str,
    db: AsyncSession = Depends(get_db, scope="function"),
    user: AuthUser = Depends(get_current_user)
) -> AgentInfo:
    """
    Dependency that verifies user has access to the specified agent.

    Supports both:
    - Custom agents (UUID IDs stored in database)
    - Built-in agents (string IDs like 'analysis', 'data-retrieval')

    Returns AgentInfo if found/valid, raises 404 otherwise.

    Usage:
        @router.get("/agents/{agent_id}/...")
        async def endpoint(
            agent_id: str,
            agent: AgentInfo = Depends(verify_agent_access),
            db: AsyncSession = Depends(get_db, scope="function")
        ):
            if agent.is_builtin:
                # Handle built-in agent
            else:
                # Handle custom agent (agent.custom_agent available)
            pass
    """
    # Normalise: if a built-in UUID was passed, convert to slug for lookup
    lookup_key = uuid_to_slug(agent_id)

    # Check if it's a built-in agent (by slug or UUID)
    if lookup_key in BUILTIN_AGENTS:
        builtin = BUILTIN_AGENTS[lookup_key]
        return AgentInfo(
            id=agent_id,
            name=builtin["name"],
            type=builtin["type"],
            description=builtin["description"],
            is_builtin=True,
            custom_agent=None,
        )

    # Try to parse as UUID for custom agent lookup
    agent_uuid, _ = parse_agent_id(agent_id)

    if agent_uuid is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_id}' not found. Expected UUID or one of: {list(BUILTIN_AGENTS.keys())}"
        )

    user_id = UUID(user.id)

    result = await db.execute(
        select(CustomAgent).where(
            CustomAgent.id == agent_uuid,
            CustomAgent.user_id == user_id
        )
    )
    custom_agent = result.scalar_one_or_none()

    if not custom_agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found"
        )

    return AgentInfo(
        id=str(custom_agent.id),
        name=custom_agent.name,
        type=custom_agent.agent_type or "custom",
        description=custom_agent.description or "",
        is_builtin=False,
        custom_agent=custom_agent,
    )
