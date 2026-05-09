"""Pydantic schemas for Agent Studio API.

Covers chat sessions, test cases, evaluation, and deployment gates.
"""
from typing import Optional, List, Dict, Any, Literal
from pydantic import BaseModel, Field
from datetime import datetime
from uuid import UUID


# =============================================================================
# Chat Session Schemas
# =============================================================================

class ChatMessage(BaseModel):
    """Individual message in a chat session."""
    id: UUID
    role: Literal["user", "assistant"]
    content: str
    timestamp: datetime
    feedback: Optional[Literal["positive", "negative"]] = None
    test_case_id: Optional[UUID] = None
    tokens_used: Optional[int] = None
    latency_ms: Optional[int] = None


class CreateChatSessionRequest(BaseModel):
    """Request to create a new chat session."""
    name: Optional[str] = Field(None, max_length=255)
    agent_version_id: Optional[UUID] = None


class ChatSessionSummary(BaseModel):
    """Summary of a chat session for list views."""
    id: UUID
    agent_id: UUID
    agent_version_id: Optional[UUID] = None
    name: Optional[str] = None
    status: str
    total_messages: int = 0
    positive_feedback_count: int = 0
    negative_feedback_count: int = 0
    created_at: datetime
    updated_at: datetime
    last_message_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ChatSessionResponse(BaseModel):
    """Full chat session with messages."""
    id: UUID
    tenant_id: UUID
    user_id: UUID
    agent_id: UUID
    agent_version_id: Optional[UUID] = None
    name: Optional[str] = None
    status: str
    messages: List[ChatMessage] = Field(default_factory=list)
    total_messages: int = 0
    total_tokens_used: int = 0
    total_latency_ms: int = 0
    positive_feedback_count: int = 0
    negative_feedback_count: int = 0
    created_at: datetime
    updated_at: datetime
    last_message_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class SendMessageRequest(BaseModel):
    """Request to send a message in a chat session."""
    content: str = Field(..., min_length=1)
    context: Optional[Dict[str, Any]] = None
    enhancedMode: Optional[bool] = None


class FeedbackRequest(BaseModel):
    """Request to submit feedback on a message."""
    feedback: Literal["positive", "negative"]
    notes: Optional[str] = None


class FeedbackResponse(BaseModel):
    """Response after submitting feedback."""
    message_id: UUID
    feedback: str
    test_case_id: UUID
    test_case_name: str
    test_case_created: bool = True


# =============================================================================
# Agent Version Config Validation
# =============================================================================

class AgentVersionConfig(BaseModel):
    """Validation schema for AgentVersion.config JSONB."""
    system_prompt: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(None, ge=1, le=128000)
    skills: Optional[List[str]] = None
    connector_ids: Optional[List[str]] = None
    knowledge_base_ids: Optional[List[str]] = None
    workflow_ids: Optional[List[str]] = None
    instruction_format: Optional[str] = None
    thinking_model: Optional[str] = None

    class Config:
        extra = "ignore"


# =============================================================================
# Test Case Schemas
# =============================================================================

class TestCaseLastResult(BaseModel):
    """Last evaluation result for a test case."""
    passed: bool
    actual_output: str
    score: Optional[float] = None
    evaluated_at: datetime
    duration_ms: Optional[int] = None


class CreateTestCaseRequest(BaseModel):
    """Request to create a new test case."""
    name: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    category: Optional[str] = Field(None, max_length=100)
    input: str = Field(..., min_length=1)
    expected_output: Optional[str] = None
    context: Optional[Dict[str, Any]] = None
    is_positive: bool = True
    evaluation_type: Literal["exact", "contains", "llm_judge"] = "llm_judge"
    evaluation_criteria: Optional[Dict[str, Any]] = None
    is_baseline: bool = False


class UpdateTestCaseRequest(BaseModel):
    """Request to update an existing test case."""
    name: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    category: Optional[str] = Field(None, max_length=100)
    input: Optional[str] = None
    expected_output: Optional[str] = None
    context: Optional[Dict[str, Any]] = None
    is_positive: Optional[bool] = None
    evaluation_type: Optional[Literal["exact", "contains", "llm_judge"]] = None
    evaluation_criteria: Optional[Dict[str, Any]] = None
    is_baseline: Optional[bool] = None
    is_active: Optional[bool] = None


class TestCaseResponse(BaseModel):
    """Full test case response."""
    id: UUID
    tenant_id: UUID
    agent_id: UUID
    name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    input: str
    expected_output: Optional[str] = None
    context: Optional[Dict[str, Any]] = None
    source: str
    source_session_id: Optional[UUID] = None
    source_message_id: Optional[UUID] = None
    is_positive: bool
    evaluation_type: str
    evaluation_criteria: Optional[Dict[str, Any]] = None
    last_result: Optional[TestCaseLastResult] = None
    pass_count: int = 0
    fail_count: int = 0
    is_active: bool = True
    is_baseline: bool = False
    created_at: datetime
    updated_at: datetime
    last_evaluated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class TestCaseListResponse(BaseModel):
    """Paginated list of test cases."""
    items: List[TestCaseResponse]
    total: int
    offset: int = 0
    limit: int = 50


class ImportTestCasesResult(BaseModel):
    """Result of importing test cases."""
    imported: int
    skipped: int
    errors: List[str] = Field(default_factory=list)


# =============================================================================
# Evaluation Schemas
# =============================================================================

class EvaluationRequest(BaseModel):
    """Request to run evaluation."""
    version_id: Optional[UUID] = None
    baseline_only: bool = False
    test_case_ids: Optional[List[UUID]] = None


class CategoryBreakdown(BaseModel):
    """Accuracy breakdown for a category."""
    accuracy: float
    passed: int
    failed: int
    total: int


class AccuracySummary(BaseModel):
    """Summary of accuracy metrics for an agent."""
    agent_id: UUID
    version_id: Optional[UUID] = None
    accuracy: float
    total_tests: int
    passed_tests: int
    failed_tests: int
    category_breakdown: Dict[str, CategoryBreakdown] = Field(default_factory=dict)
    last_evaluated_at: Optional[datetime] = None


class AccuracyHistoryEntry(BaseModel):
    """Single entry in accuracy history."""
    id: UUID
    version_id: Optional[UUID] = None
    version: Optional[str] = None
    accuracy: float
    total_tests: int
    passed_tests: int
    failed_tests: int
    accuracy_delta: Optional[float] = None
    evaluated_at: datetime

    class Config:
        from_attributes = True


class TestRunResult(BaseModel):
    """Result of running a single test case."""
    test_case_id: UUID
    passed: bool
    actual_output: str
    expected_output: Optional[str] = None
    score: Optional[float] = None
    evaluation_type: str
    duration_ms: int
    tokens_used: Optional[int] = None


class EvaluationProgress(BaseModel):
    """Progress update during evaluation."""
    total: int
    completed: int
    passed: int
    failed: int
    current_test_case_id: Optional[UUID] = None
    current_test_case_name: Optional[str] = None


class EvaluationResult(BaseModel):
    """Final result of evaluation run."""
    evaluation_run_id: UUID
    agent_id: UUID
    version_id: Optional[UUID] = None
    accuracy: float
    total_tests: int
    passed_tests: int
    failed_tests: int
    duration_ms: int
    category_breakdown: Dict[str, CategoryBreakdown] = Field(default_factory=dict)
    test_results: List[TestRunResult]
    evaluated_at: datetime


# =============================================================================
# Deploy Gate Schemas
# =============================================================================

class DeployGateCheck(BaseModel):
    """Individual gate check result."""
    name: str
    passed: bool
    value: str
    threshold: Optional[str] = None
    message: str


class DeployGateStatus(BaseModel):
    """Status of all deploy gate checks."""
    can_deploy: bool
    checks: List[DeployGateCheck]
    current_accuracy: Optional[float] = None
    new_accuracy: Optional[float] = None
    accuracy_delta: Optional[float] = None
    current_version: Optional[str] = None
    new_version: str


class DeployRequest(BaseModel):
    """Request to deploy a version."""
    force: bool = False
    notes: Optional[str] = None
    force_reason: Optional[str] = None  # Required if force=True


class DeployResult(BaseModel):
    """Result of deployment attempt."""
    success: bool
    version_id: UUID
    version: str
    action: Literal["deploy", "blocked"]
    gate_checks: List[DeployGateCheck]
    accuracy_at_deploy: Optional[float] = None
    block_reason: Optional[str] = None
    deployed_at: Optional[datetime] = None


class RollbackRequest(BaseModel):
    """Request to rollback to a previous version."""
    target_version_id: UUID
    reason: str = Field(..., min_length=1)


class RollbackResult(BaseModel):
    """Result of rollback."""
    success: bool
    previous_version_id: UUID
    current_version_id: UUID
    reason: str
    rolled_back_at: datetime


class GenerateTestsRequest(BaseModel):
    """Request to auto-generate test cases from agent instructions."""
    instructions: str = Field(..., min_length=10)
    existing_test_ids: Optional[List[str]] = None
    count: int = Field(default=5, ge=1, le=10)


class GeneratedTestCase(BaseModel):
    """A single LLM-generated test case."""
    input: str
    expected_output: str
    category: str
    evaluation_type: Literal["exact", "contains", "llm_judge"] = "llm_judge"


class GenerateTestsResponse(BaseModel):
    """Response containing generated test cases."""
    test_cases: List[GeneratedTestCase]


class DeployHistoryEntry(BaseModel):
    """Entry in deployment history."""
    id: UUID
    version_id: UUID
    version: str
    action: str
    status: str
    gate_check_passed: bool
    accuracy_at_deploy: Optional[float] = None
    previous_accuracy: Optional[float] = None
    accuracy_delta: Optional[float] = None
    block_reason: Optional[str] = None
    deployed_by: UUID
    notes: Optional[str] = None
    force_deployed: bool = False
    force_reason: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True
