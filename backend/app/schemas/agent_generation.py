"""Schemas for AI-powered agent generation (Phase A: Chat-First Creation)."""
from typing import Literal, Optional
from pydantic import BaseModel, Field


class GenerateAgentRequest(BaseModel):
    """Request to generate an agent config from a natural language description."""
    description: str = Field(..., min_length=20, max_length=5000)
    clarification_context: Optional[list[dict[str, str]]] = None


class ClarifyRequest(BaseModel):
    """Request to generate clarifying questions before agent creation."""
    description: str = Field(..., min_length=20, max_length=5000)
    context: Optional[list[dict[str, str]]] = None


class ClarifyQuestion(BaseModel):
    """A single clarifying question for the user."""
    id: str
    text: str
    type: Literal["multiple_choice", "free_text"] = "multiple_choice"
    options: Optional[list[str]] = None


class ClarifyResponse(BaseModel):
    """Response containing clarifying questions."""
    questions: list[ClarifyQuestion]
    should_generate: bool = False


class GenerationReasoning(BaseModel):
    """Explains why the AI chose specific skills, connectors, and model."""
    skill_reasons: dict[str, str] = Field(default_factory=dict)
    connector_reasons: dict[str, str] = Field(default_factory=dict)
    model_reason: str = ""


class GeneratedAgentConfig(BaseModel):
    """Full structured output from the meta-agent."""
    name: str
    description: str
    system_prompt: str
    model: str
    temperature: float = Field(ge=0, le=2)
    max_tokens: int = Field(ge=100, le=128000)
    skills: list[str] = Field(default_factory=list)
    connectors: list[str] = Field(default_factory=list)
    icon: str = "Robot"
    agent_type: str = "custom"
    reasoning: GenerationReasoning = Field(default_factory=GenerationReasoning)
