"""Pydantic schemas for wide research API."""
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional


class WideResearchExecuteRequest(BaseModel):
    """Request to start wide research execution."""
    task_template: str = Field(
        ..., min_length=1, max_length=10000,
        description="Prompt template with {item} placeholder.",
    )
    items: List[Dict[str, Any]] = Field(
        ..., min_length=1, max_length=500,
        description="List of item dicts to process.",
    )
    agent_id: str = Field(..., min_length=1)
    session_id: str = Field(..., min_length=1)
    max_parallel: int = Field(default=10, ge=1, le=50)
    timeout_per_item: int = Field(default=300, ge=10, le=600)
    synthesize: bool = Field(
        default=True,
        description="Run expert review synthesis after execution.",
    )


class WideResearchItemResponse(BaseModel):
    """Single item result."""
    item_index: int
    item: Dict[str, Any]
    status: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    duration_seconds: float = 0.0


class WideResearchResponse(BaseModel):
    """Response for wide research execution."""
    execution_id: str
    status: str
    total_items: int
    completed: int
    failed: int
    skipped: int
    items: List[WideResearchItemResponse]
    synthesis: Optional[str] = None
    duration_seconds: float = 0.0
