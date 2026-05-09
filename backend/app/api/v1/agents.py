from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List

from app.auth.dependencies import get_current_user
from app.auth.providers.base import AuthUser
from app.core.builtin_agents import BUILTIN_AGENT_UUIDS

router = APIRouter()

class AgentInfo(BaseModel):
    id: str
    name: str
    type: str
    description: str
    status: str

@router.get("", response_model=List[AgentInfo])
@router.get("/", response_model=List[AgentInfo], include_in_schema=False)
async def list_agents(user: AuthUser = Depends(get_current_user)):
    """
    List available agents.

    The General Agent is always first as the default conversational assistant.
    """
    return [
        AgentInfo(
            id=BUILTIN_AGENT_UUIDS["general"],
            name="General Assistant",
            type="general",
            description="Conversational AI helper - handles greetings, general questions, and guides to specialized agents",
            status="available",
        ),
        AgentInfo(
            id=BUILTIN_AGENT_UUIDS["data-retrieval"],
            name="Data Retrieval Agent",
            type="data",
            description="Fetch financial data from external APIs",
            status="available",
        ),
        AgentInfo(
            id=BUILTIN_AGENT_UUIDS["analysis"],
            name="Analysis Agent",
            type="analysis",
            description="Perform financial analysis on retrieved data",
            status="available",
        ),
        AgentInfo(
            id=BUILTIN_AGENT_UUIDS["compliance"],
            name="Compliance Agent",
            type="compliance",
            description="Check regulatory compliance and risk",
            status="available",
        ),
        AgentInfo(
            id=BUILTIN_AGENT_UUIDS["kyc-aml"],
            name="KYC/AML Agent",
            type="compliance",
            description="Validates Know Your Customer and Anti-Money Laundering requirements",
            status="available",
        ),
        AgentInfo(
            id=BUILTIN_AGENT_UUIDS["market-abuse"],
            name="Market Abuse Agent",
            type="compliance",
            description="Detects potential insider trading or market manipulation",
            status="available",
        ),
        AgentInfo(
            id=BUILTIN_AGENT_UUIDS["report-generator"],
            name="Regulatory Report Generator",
            type="compliance",
            description="Synthesizes analysis into formal regulatory reports",
            status="available",
        ),
        AgentInfo(
            id=BUILTIN_AGENT_UUIDS["research"],
            name="Research Agent",
            type="research",
            description="Answer questions using document search (RAG)",
            status="available",
        ),
    ]

@router.get("/{agent_id}", response_model=AgentInfo)
async def get_agent(agent_id: str, user: AuthUser = Depends(get_current_user)):
    """Get agent details by ID."""
    agents = await list_agents(user=user)
    for agent in agents:
        if agent.id == agent_id:
            return agent
    raise HTTPException(status_code=404, detail="Agent not found")
