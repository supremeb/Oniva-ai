"""
Complex Step Schemas for AI Workflow Builder

Supports multi-action steps (sequential actions) and conditional steps (branching).
Nested sub-workflows deferred to v2.
"""

from enum import Enum
from typing import Any, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


class ActionType(str, Enum):
    """Types of actions within a multi-action step."""

    connector = "connector"
    llm = "llm"
    code = "code"
    http = "http"
    template = "template"
    wait = "wait"
    notification = "notification"


class ConditionOperator(str, Enum):
    """Operators for conditional branching."""

    eq = "eq"
    neq = "neq"
    gt = "gt"
    lt = "lt"
    gte = "gte"
    lte = "lte"
    contains = "contains"
    not_contains = "not_contains"
    exists = "exists"
    not_exists = "not_exists"


class Action(BaseModel):
    """An individual action within a multi-action step."""

    id: str = Field(..., description="Unique identifier for the action")
    type: ActionType = Field(..., description="Type of action")
    label: str = Field(..., description="Display label for the action")
    config: dict = Field(default_factory=dict, description="Action configuration")
    depends_on: Optional[str] = Field(
        None, description="ID of the action this depends on for sequential execution"
    )


class Condition(BaseModel):
    """A condition for branching logic."""

    field: str = Field(..., description="Field to evaluate")
    operator: ConditionOperator = Field(..., description="Comparison operator")
    value: Any = Field(..., description="Value to compare against")


class BaseStep(BaseModel):
    """Base step schema matching the frontend structure."""

    id: str = Field(..., description="Unique identifier for the step")
    type: str = Field(..., description="Step type")
    label: str = Field(..., description="Display label")
    config: dict = Field(default_factory=dict, description="Step configuration")


class Branch(BaseModel):
    """A branch in a conditional step."""

    id: str = Field(..., description="Unique identifier for the branch")
    label: str = Field(..., description="Display label (e.g., 'High Risk', 'Auto-Approve')")
    condition: Condition = Field(..., description="Condition to evaluate")
    steps: list[BaseStep] = Field(
        default_factory=list, description="Steps to execute if condition matches"
    )


class MultiActionStepData(BaseModel):
    """Data for a multi-action step (sequential actions)."""

    complex_type: Literal["multi-action"] = "multi-action"
    actions: list[Action] = Field(
        default_factory=list, description="List of actions to execute in sequence"
    )
    is_expanded: bool = Field(default=False, description="Whether the step is expanded in UI")


class ConditionalStepData(BaseModel):
    """Data for a conditional step (branching logic)."""

    complex_type: Literal["conditional"] = "conditional"
    branches: list[Branch] = Field(
        default_factory=list, description="List of conditional branches"
    )
    default_branch: Optional[Branch] = Field(
        None, description="Default branch if no conditions match"
    )
    is_expanded: bool = Field(default=False, description="Whether the step is expanded in UI")


# Union type for all complex step data variants
ComplexStepData = Union[MultiActionStepData, ConditionalStepData]


class ComplexStep(BaseModel):
    """A complex step that can contain multiple actions or conditional branches."""

    id: str = Field(..., description="Unique identifier for the step")
    type: Literal["complex"] = "complex"
    label: str = Field(..., description="Display label")
    config: dict = Field(default_factory=dict, description="Step configuration")
    complex_data: ComplexStepData = Field(..., description="Complex step specific data")

    @property
    def complex_type(self) -> str:
        """Get the complex type from the data."""
        return self.complex_data.complex_type


# Request/Response schemas for workflow AI chat


class WorkflowAIChatRequest(BaseModel):
    """Request for workflow AI chat endpoint."""

    workflow_id: str = Field(..., description="ID of the workflow being edited")
    message: str = Field(..., description="User's message")
    current_steps: list[dict] = Field(
        default_factory=list, description="Current workflow steps"
    )
    current_edges: Optional[list[dict]] = Field(
        default=None, description="Current workflow edges (source, target, handles)"
    )
    conversation_history: list[dict] = Field(
        default_factory=list, description="Recent conversation messages for context"
    )


class EdgeProposal(BaseModel):
    """A proposed edge connection between nodes."""

    action: Literal["add", "delete"] = "add"
    source: str = Field(..., description="Source node ID")
    target: str = Field(..., description="Target node ID")
    source_handle: Optional[str] = Field(None, alias="sourceHandle")
    target_handle: Optional[str] = Field(None, alias="targetHandle")

    model_config = ConfigDict(populate_by_name=True)


class StepProposal(BaseModel):
    """A proposed step to add/modify/delete in the workflow.

    Canonical definition — used by both the Pydantic AI agent and the API layer.
    """

    id: str = Field(..., description="Unique ID for the step")
    type: str = Field(..., description="Step type: start, llm, connector, code, etc.")
    label: str = Field(default="", description="Display label for the step")
    config: dict = Field(default_factory=dict, description="Step configuration")
    action: Literal["add", "modify", "delete"] = Field(
        default="add", description="What to do with this step"
    )
    position: Optional[int] = Field(
        None, description="Position in workflow (for add action)"
    )
    reason: str = Field(default="", description="Brief explanation for this proposal")


class WorkflowAIChatResponse(BaseModel):
    """Response from workflow AI chat endpoint."""

    message: str = Field(..., description="AI's response message")
    message_type: Literal[
        "text", "oauth_request", "steps_preview", "question", "confirmation", "error"
    ] = Field(..., description="Type of message")
    proposed_steps: Optional[list[StepProposal]] = Field(
        None, description="Proposed changes to workflow steps"
    )
    proposed_edges: Optional[List[EdgeProposal]] = Field(
        None, description="Proposed edge connections between nodes"
    )
    required_connectors: Optional[list[str]] = Field(
        None, description="Connectors that need OAuth"
    )
    question_options: Optional[list[dict]] = Field(
        None, description="Options for question-type messages"
    )


class ApplyStepsRequest(BaseModel):
    """Request to apply proposed steps to workflow."""

    workflow_id: str = Field(..., description="ID of the workflow")
    nodes: list[dict] = Field(..., description="Final node state to persist")
    edges: list[dict] = Field(..., description="Final edge state to persist")


class ApplyStepsResponse(BaseModel):
    """Response after applying steps."""

    success: bool = Field(..., description="Whether the operation succeeded")
    message: str = Field(..., description="Status message")
    version: Optional[int] = Field(None, description="Workflow version after save")
