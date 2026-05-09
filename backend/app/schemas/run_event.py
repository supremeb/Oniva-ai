"""Pydantic schemas for RunEvent — unified agent run observability."""
from pydantic import BaseModel, Field
from typing import Optional, Any, Dict
from datetime import datetime
from uuid import UUID


# Valid event_type values:
#   run_started        — emitted when a run begins
#   run_completed      — emitted when a run finishes successfully
#   run_failed         — emitted when a run terminates with an error
#   tool_call_started  — emitted before a tool is invoked
#   tool_call_completed — emitted after a tool returns
#   content_delta      — streaming text chunk from an LLM response
#   thinking_delta     — streaming reasoning/thinking chunk
#   worker_activity    — deep work worker lifecycle event
#   phase_transition   — state machine phase change


class RunEventCreate(BaseModel):
    """Schema for persisting a new RunEvent row."""
    run_id: UUID = Field(..., description="ID of the run that produced this event")
    session_id: Optional[UUID] = Field(None, description="Chat or workflow session ID, if applicable")
    tenant_id: UUID = Field(..., description="Owning tenant")
    event_type: str = Field(..., description="Event category (e.g. run_started, tool_call_completed)")
    agent_name: Optional[str] = Field(None, description="Name of the agent that emitted the event")
    timestamp: Optional[datetime] = Field(None, description="Event timestamp; defaults to server utcnow if omitted")
    input_tokens: Optional[int] = Field(None, ge=0, description="LLM prompt tokens consumed by this event")
    output_tokens: Optional[int] = Field(None, ge=0, description="LLM completion tokens produced by this event")
    payload: Optional[Dict[str, Any]] = Field(None, description="Arbitrary event-specific data")


class RunEventResponse(BaseModel):
    """Schema for RunEvent API responses."""
    id: UUID
    run_id: UUID
    session_id: Optional[UUID] = None
    tenant_id: UUID
    event_type: str
    agent_name: Optional[str] = None
    timestamp: datetime
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    payload: Optional[Dict[str, Any]] = None

    model_config = {"from_attributes": True}
