"""Pydantic schemas for deep work orchestrator-worker engine."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Learning(BaseModel):
    """Single extracted finding from a source."""
    fact: str = Field(..., max_length=800)
    source_url: str = Field(..., max_length=2000)
    source_type: str = "web"  # "web" | "knowledge_base" | "connector"
    source_title: str | None = Field(None, max_length=200)
    confidence: float = Field(..., ge=0.0, le=1.0)
    quote: str | None = Field(None, max_length=500)
    sub_query: str = ""


class WorkerTask(BaseModel):
    """Orchestrator assigns this to a worker."""
    worker_id: str
    sub_query: str
    tools: list[str]
    max_iterations: int = 8
    task_type: str = "research"  # research | analysis | code_gen | extraction | browser
    output_type: str | None = None


class WorkerResult(BaseModel):
    """Worker output — returned to engine after research loop."""
    worker_id: str
    sub_query: str
    learnings: list[Learning]
    sources: list[str]
    confidence: float = Field(..., ge=0.0, le=1.0)
    iterations_used: int
    error: str | None = None


class DeepWorkPlan(BaseModel):
    """Orchestrator output."""
    title: str = ""
    sub_queries: list[WorkerTask] = Field(..., max_length=10)
    deliverable: str | None = None  # pdf_report | presentation | spreadsheet | dashboard | chart | document | data_export | None
    deliverable_description: str | None = None


class ReflectionResult(BaseModel):
    """Reflector output."""
    score: int = Field(..., ge=0, le=100)
    gaps: list[str]
    should_continue: bool
    gap_filling_tasks: list[WorkerTask] | None = None


# ── Report outline (Phase 2 synthesis) ────────────────────────


class ReportSection(BaseModel):
    """Outline section definition."""
    heading: str
    description: str
    learning_ids: list[int]


class ReportOutline(BaseModel):
    """Outliner output — report structure."""
    title: str
    sections: list[ReportSection]


# ── Intake (pre-plan clarification) ─────────────────────────


class IntakeQuestion(BaseModel):
    """A single clarifying question for the user."""
    text: str
    options: list[str] | None = None  # None = free text input


class IntakeResult(BaseModel):
    """Clarifier output — ask questions or auto-skip."""
    action: Literal["ask", "proceed"]
    questions: list[IntakeQuestion] | None = None  # when action="ask"
    enriched_query: str | None = None               # when action="proceed"

    @model_validator(mode="after")
    def validate_action_fields(self) -> "IntakeResult":
        if self.action == "ask" and not self.questions:
            # Empty questions = treat as auto-skip (defensive)
            self.action = "proceed"
        return self
