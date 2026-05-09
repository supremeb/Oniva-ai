"""
Pydantic schemas for structured message parts.

These schemas match the frontend MessagePart types in lib/models.ts
for type-safe SSE streaming of rich chat components.

Categories:
- Text & Code: Basic content rendering
- Tool Calls: Tool execution visualization
- Interactive: User input components
- Data Visualization: Tables, charts, metrics
- Status: Loading and notification states
"""

from typing import Optional, List, Dict, Any, Literal, Union
from pydantic import BaseModel, Field
from enum import Enum


# =============================================================================
# ENUMS
# =============================================================================

class ToolCallStatus(str, Enum):
    """Status of a tool call."""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"


class ApprovalStatus(str, Enum):
    """Status of an approval request."""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class RiskLevel(str, Enum):
    """Risk level for approval cards."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ChartType(str, Enum):
    """Supported chart types."""
    BAR = "bar"
    LINE = "line"
    PIE = "pie"
    SCATTER = "scatter"
    AREA = "area"


class SourceType(str, Enum):
    """Types of data sources."""
    DOCUMENT = "document"
    WEB = "web"
    DATABASE = "database"
    API = "api"
    FILE = "file"


class SqlStatus(str, Enum):
    """SQL query execution status."""
    IDLE = "idle"
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"


class ThinkingVariant(str, Enum):
    """Thinking indicator display variants."""
    DEFAULT = "default"
    MINIMAL = "minimal"
    DETAILED = "detailed"
    BRAIN = "brain"


class StepStatus(str, Enum):
    """Status of a step in a process."""
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETE = "complete"
    ERROR = "error"
    SKIPPED = "skipped"


class TimelineEventStatus(str, Enum):
    """Status of a timeline event."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ERROR = "error"


class MetricTrend(str, Enum):
    """Metric trend direction."""
    UP = "up"
    DOWN = "down"
    NEUTRAL = "neutral"


class StatusType(str, Enum):
    """Status indicator types."""
    SUCCESS = "success"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"
    PENDING = "pending"


# =============================================================================
# BASE PART SCHEMAS
# =============================================================================

class MessagePartBase(BaseModel):
    """Base class for all message parts."""
    type: str = Field(..., description="Part type identifier")


# =============================================================================
# TEXT & CODE PARTS
# =============================================================================

class TextPart(MessagePartBase):
    """Plain text or markdown content."""
    type: Literal["text"] = "text"
    content: str = Field(..., description="Text content (supports markdown)")
    isStreaming: Optional[bool] = Field(False, description="Whether text is still streaming")


class CodePart(MessagePartBase):
    """Code block with syntax highlighting."""
    type: Literal["code"] = "code"
    content: str = Field(..., description="Code content")
    language: Optional[str] = Field(None, description="Programming language for highlighting")
    filename: Optional[str] = Field(None, description="Optional filename")


# =============================================================================
# TOOL CALL PARTS
# =============================================================================

class ToolCallStepPart(BaseModel):
    """Single step in a tool call progress."""
    id: str
    label: str
    status: StepStatus = StepStatus.PENDING
    message: Optional[str] = None
    startedAt: Optional[str] = None
    completedAt: Optional[str] = None


class ToolCallPart(MessagePartBase):
    """Tool call visualization."""
    type: Literal["tool_call"] = "tool_call"
    id: str = Field(..., description="Unique tool call ID")
    toolName: str = Field(..., description="Name of the tool being called")
    status: ToolCallStatus = Field(ToolCallStatus.PENDING, description="Current status")
    args: Optional[Dict[str, Any]] = Field(None, description="Tool arguments")
    result: Optional[Any] = Field(None, description="Tool result on success")
    error: Optional[str] = Field(None, description="Error message on failure")
    durationMs: Optional[int] = Field(None, description="Execution time in milliseconds")
    steps: Optional[List[ToolCallStepPart]] = Field(None, description="Progress steps")


# =============================================================================
# INTERACTIVE PARTS
# =============================================================================

class QuestionOptionPart(BaseModel):
    """Single option in an agent question."""
    id: str = Field(..., description="Option identifier")
    label: str = Field(..., description="Display label")
    description: Optional[str] = Field(None, description="Optional description")


class QuestionPart(MessagePartBase):
    """Agent question with multiple choice options."""
    type: Literal["question"] = "question"
    id: str = Field(..., description="Question identifier")
    header: str = Field(..., description="Question header/category")
    question: str = Field(..., description="Question text")
    options: List[QuestionOptionPart] = Field(..., description="Available options")
    multiSelect: Optional[bool] = Field(False, description="Allow multiple selections")
    timeout: Optional[int] = Field(0, description="Timeout in seconds (0 = no timeout)")
    defaultOptionId: Optional[str] = Field(None, description="Default option on timeout")
    includeOther: Optional[bool] = Field(True, description="Include 'Other' text input option")


class ApprovalItemPart(BaseModel):
    """Item requiring approval."""
    label: str = Field(..., description="Item label")
    value: str = Field(..., description="Item value")
    sensitive: Optional[bool] = Field(False, description="Whether value is sensitive")


class ApprovalPart(MessagePartBase):
    """Human-in-the-loop approval card."""
    type: Literal["approval"] = "approval"
    id: str = Field(..., description="Approval identifier")
    title: str = Field(..., description="Approval title")
    description: Optional[str] = Field(None, description="Detailed description")
    riskLevel: Optional[RiskLevel] = Field(RiskLevel.LOW, description="Risk indicator")
    items: Optional[List[ApprovalItemPart]] = Field(None, description="Items to review")
    status: Optional[ApprovalStatus] = Field(ApprovalStatus.PENDING, description="Current status")


class FormFieldPart(BaseModel):
    """Single field in a form."""
    id: str = Field(..., description="Field identifier")
    label: str = Field(..., description="Field label")
    type: Literal["text", "email", "password", "number", "textarea", "select"] = "text"
    placeholder: Optional[str] = None
    required: Optional[bool] = False
    defaultValue: Optional[str] = None
    options: Optional[List[Dict[str, str]]] = Field(None, description="Options for select fields")


class FormPart(MessagePartBase):
    """Multi-field form card."""
    type: Literal["form"] = "form"
    id: str = Field(..., description="Form identifier")
    title: str = Field(..., description="Form title")
    description: Optional[str] = None
    fields: List[FormFieldPart] = Field(..., description="Form fields")
    submitLabel: Optional[str] = Field("Submit", description="Submit button label")


class InputPart(MessagePartBase):
    """Free-form text input."""
    type: Literal["input"] = "input"
    id: str = Field(..., description="Input identifier")
    prompt: str = Field(..., description="Input prompt")
    placeholder: Optional[str] = None
    inputType: Optional[Literal["text", "email", "number", "textarea"]] = "text"
    required: Optional[bool] = False


class OAuthPart(MessagePartBase):
    """OAuth authorization card."""
    type: Literal["oauth"] = "oauth"
    id: str = Field(..., description="OAuth flow identifier")
    serviceName: str = Field(..., description="Service name (e.g., 'Google', 'GitHub')")
    description: Optional[str] = None
    scopes: Optional[List[str]] = Field(None, description="Requested OAuth scopes")
    status: Optional[Literal["pending", "authorizing", "authorized", "error"]] = "pending"
    authUrl: Optional[str] = Field(None, description="OAuth authorization URL")
    accountInfo: Optional[Dict[str, str]] = Field(None, description="Connected account info")


# =============================================================================
# DATA VISUALIZATION PARTS
# =============================================================================

class DataTableColumnPart(BaseModel):
    """Column definition for data tables."""
    key: str = Field(..., description="Column key")
    header: str = Field(..., description="Column header text")
    sortable: Optional[bool] = True
    width: Optional[str] = None


class TablePart(MessagePartBase):
    """Data table with sorting and filtering."""
    type: Literal["table"] = "table"
    columns: List[DataTableColumnPart] = Field(..., description="Column definitions")
    data: List[Dict[str, Any]] = Field(..., description="Table rows")
    title: Optional[str] = None
    sortable: Optional[bool] = True
    searchable: Optional[bool] = False


class ChartPart(MessagePartBase):
    """Chart/graph visualization."""
    type: Literal["chart"] = "chart"
    chartType: ChartType = Field(..., description="Chart type")
    title: Optional[str] = None
    description: Optional[str] = None
    imageUrl: Optional[str] = Field(None, description="Pre-rendered chart image URL")
    svgContent: Optional[str] = Field(None, description="Inline SVG content")


class SourcePart(MessagePartBase):
    """Citation or source reference."""
    type: Literal["source"] = "source"
    sourceType: SourceType = Field(..., description="Type of source")
    title: str = Field(..., description="Source title")
    description: Optional[str] = None
    url: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata")
    citationNumber: Optional[int] = Field(None, description="Citation number for references")


class FilePart(MessagePartBase):
    """File attachment preview with optional artifact enrichment."""
    type: Literal["file"] = "file"
    filename: str = Field(..., description="File name")
    size: Optional[int] = Field(None, description="File size in bytes")
    fileType: Optional[str] = Field(None, description="MIME type or extension")
    downloadUrl: Optional[str] = None
    content: Optional[str] = Field(None, description="Inline text content for preview")
    # Enriched artifact fields (Phase B2 output engine)
    title: Optional[str] = None
    description: Optional[str] = None
    previewUrl: Optional[str] = None
    artifactType: Optional[str] = None
    artifactUri: Optional[str] = None
    tags: Optional[List[str]] = None
    createdAt: Optional[str] = None
    pageCount: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None


class ImagePart(MessagePartBase):
    """Image preview."""
    type: Literal["image"] = "image"
    src: str = Field(..., description="Image source URL")
    alt: Optional[str] = Field(None, description="Alt text")
    caption: Optional[str] = None
    aspectRatio: Optional[str] = Field(None, description="Aspect ratio (e.g., '16:9')")


class LinkPart(MessagePartBase):
    """Link preview with OpenGraph-style metadata."""
    type: Literal["link"] = "link"
    url: str = Field(..., description="Link URL")
    title: Optional[str] = None
    description: Optional[str] = None
    imageUrl: Optional[str] = None
    faviconUrl: Optional[str] = None
    siteName: Optional[str] = None


class SqlPart(MessagePartBase):
    """SQL query preview."""
    type: Literal["sql"] = "sql"
    query: str = Field(..., description="SQL query text")
    database: Optional[str] = None
    status: Optional[SqlStatus] = SqlStatus.IDLE
    executionTime: Optional[int] = Field(None, description="Execution time in ms")
    rowCount: Optional[int] = None
    error: Optional[str] = None


class SqlResultColumnPart(BaseModel):
    """Column definition for SQL results."""
    name: str
    type: Optional[str] = None


class SqlResultPart(MessagePartBase):
    """SQL query result table."""
    type: Literal["sql_result"] = "sql_result"
    columns: List[SqlResultColumnPart] = Field(..., description="Column definitions")
    rows: List[List[Any]] = Field(..., description="Result rows")
    executionTime: Optional[int] = None
    totalRows: Optional[int] = None


class JsonPart(MessagePartBase):
    """JSON data viewer."""
    type: Literal["json"] = "json"
    data: Any = Field(..., description="JSON data to display")
    title: Optional[str] = None
    defaultCollapsedDepth: Optional[int] = Field(2, description="Default collapsed depth")


class DiffLinePart(BaseModel):
    """Single line in a diff."""
    type: Literal["added", "removed", "unchanged"]
    content: str
    lineNumber: Optional[int] = None


class DiffPart(MessagePartBase):
    """Git-style diff viewer."""
    type: Literal["diff"] = "diff"
    diff: Optional[str] = Field(None, description="Raw diff text")
    lines: Optional[List[DiffLinePart]] = Field(None, description="Parsed diff lines")
    title: Optional[str] = None
    oldFileName: Optional[str] = None
    newFileName: Optional[str] = None


class TimelineEventPart(BaseModel):
    """Single event in a timeline."""
    title: str
    description: Optional[str] = None
    status: TimelineEventStatus = TimelineEventStatus.PENDING
    timestamp: Optional[str] = None


class TimelinePart(MessagePartBase):
    """Timeline visualization."""
    type: Literal["timeline"] = "timeline"
    events: List[TimelineEventPart] = Field(..., description="Timeline events")


class MetricPart(MessagePartBase):
    """KPI metric card."""
    type: Literal["metric"] = "metric"
    label: str = Field(..., description="Metric label")
    value: Union[str, int, float] = Field(..., description="Metric value")
    change: Optional[float] = Field(None, description="Change percentage")
    trend: Optional[MetricTrend] = None
    prefix: Optional[str] = Field(None, description="Value prefix (e.g., '$')")
    suffix: Optional[str] = Field(None, description="Value suffix (e.g., '%')")
    context: Optional[str] = Field(None, description="Additional context")


# =============================================================================
# STATUS PARTS
# =============================================================================

class StepPart(BaseModel):
    """Single step in a process."""
    id: str
    label: str
    description: Optional[str] = None
    status: StepStatus = StepStatus.PENDING


class StepsPart(MessagePartBase):
    """Multi-step progress indicator."""
    type: Literal["steps"] = "steps"
    steps: List[StepPart] = Field(..., description="Process steps")
    orientation: Optional[Literal["horizontal", "vertical"]] = "vertical"


class ThinkingPart(MessagePartBase):
    """AI thinking/processing indicator."""
    type: Literal["thinking"] = "thinking"
    variant: Optional[ThinkingVariant] = ThinkingVariant.DEFAULT
    message: Optional[str] = None
    stage: Optional[str] = Field(None, description="Current processing stage")
    showElapsed: Optional[bool] = False
    thinkingContent: Optional[str] = Field(None, description="Streaming reasoning content (accordion variant)")
    isThinking: Optional[bool] = Field(None, description="Whether thinking is still in progress")


class ErrorPart(MessagePartBase):
    """Error message display."""
    type: Literal["error"] = "error"
    id: Optional[str] = None
    message: str = Field(..., description="Error message")
    title: Optional[str] = None
    details: Optional[str] = Field(None, description="Error stack trace or details")
    retryable: Optional[bool] = False


class SuccessPart(MessagePartBase):
    """Success message display."""
    type: Literal["success"] = "success"
    message: str = Field(..., description="Success message")
    title: Optional[str] = None
    details: Optional[str] = None


class WarningPart(MessagePartBase):
    """Warning message display."""
    type: Literal["warning"] = "warning"
    message: str = Field(..., description="Warning message")
    title: Optional[str] = None
    details: Optional[str] = None


class InfoPart(MessagePartBase):
    """Info message display."""
    type: Literal["info"] = "info"
    message: str = Field(..., description="Info message")
    title: Optional[str] = None
    details: Optional[str] = None


# =============================================================================
# DISCRIMINATED UNION TYPE
# =============================================================================

MessagePart = Union[
    # Text & Code
    TextPart,
    CodePart,
    # Tool Calls
    ToolCallPart,
    # Interactive
    QuestionPart,
    ApprovalPart,
    FormPart,
    InputPart,
    OAuthPart,
    # Data Visualization
    TablePart,
    ChartPart,
    SourcePart,
    FilePart,
    ImagePart,
    LinkPart,
    SqlPart,
    SqlResultPart,
    JsonPart,
    DiffPart,
    TimelinePart,
    MetricPart,
    # Status
    StepsPart,
    ThinkingPart,
    ErrorPart,
    SuccessPart,
    WarningPart,
    InfoPart,
]


# =============================================================================
# SSE EVENT SCHEMAS
# =============================================================================

class SSEEventBase(BaseModel):
    """Base class for SSE events."""
    event: str = Field(..., description="Event type")
    id: Optional[str] = Field(None, description="Event ID for client tracking")


class SSETextDeltaEvent(SSEEventBase):
    """Text content streaming delta."""
    event: Literal["text_delta"] = "text_delta"
    delta: str = Field(..., description="Text chunk")
    partIndex: Optional[int] = Field(None, description="Which part this delta belongs to")


class SSEPartEvent(SSEEventBase):
    """Complete message part event."""
    event: Literal["part"] = "part"
    part: MessagePart = Field(..., description="Complete message part")


class SSEPartUpdateEvent(SSEEventBase):
    """Update to an existing message part."""
    event: Literal["part_update"] = "part_update"
    partId: str = Field(..., description="ID of the part being updated")
    updates: Dict[str, Any] = Field(..., description="Fields to update")


class SSEToolCallStartEvent(SSEEventBase):
    """Tool call started event."""
    event: Literal["tool_call_start"] = "tool_call_start"
    toolCallId: str
    toolName: str
    args: Optional[Dict[str, Any]] = None


class SSEToolCallProgressEvent(SSEEventBase):
    """Tool call progress update."""
    event: Literal["tool_call_progress"] = "tool_call_progress"
    toolCallId: str
    step: ToolCallStepPart


class SSEToolCallCompleteEvent(SSEEventBase):
    """Tool call completed event."""
    event: Literal["tool_call_complete"] = "tool_call_complete"
    toolCallId: str
    status: ToolCallStatus
    result: Optional[Any] = None
    error: Optional[str] = None
    durationMs: Optional[int] = None


class SSEQuestionEvent(SSEEventBase):
    """Agent question event requiring user input."""
    event: Literal["question"] = "question"
    question: QuestionPart


class SSEApprovalEvent(SSEEventBase):
    """Approval request event."""
    event: Literal["approval"] = "approval"
    approval: ApprovalPart


class SSEReasoningEvent(SSEEventBase):
    """Chain-of-thought reasoning event."""
    event: Literal["reasoning"] = "reasoning"
    content: str


class SSEContentEvent(SSEEventBase):
    """Legacy content delta event (backwards compatible)."""
    event: Literal["content"] = "content"
    delta: str


class SSEErrorEvent(SSEEventBase):
    """Error event."""
    event: Literal["error"] = "error"
    message: str
    code: Optional[str] = None
    retryable: Optional[bool] = False


class SSEDoneEvent(SSEEventBase):
    """Stream complete event."""
    event: Literal["done"] = "done"
    messageId: Optional[str] = None
    sessionId: Optional[str] = None
    model: Optional[str] = None
    usage: Optional[Dict[str, int]] = Field(None, description="Token usage stats")


# Union of all SSE event types
SSEEvent = Union[
    SSETextDeltaEvent,
    SSEPartEvent,
    SSEPartUpdateEvent,
    SSEToolCallStartEvent,
    SSEToolCallProgressEvent,
    SSEToolCallCompleteEvent,
    SSEQuestionEvent,
    SSEApprovalEvent,
    SSEReasoningEvent,
    SSEContentEvent,
    SSEErrorEvent,
    SSEDoneEvent,
]


# =============================================================================
# ENHANCED MESSAGE SCHEMA
# =============================================================================

class EnhancedChatMessage(BaseModel):
    """
    Enhanced chat message supporting both legacy string content
    and structured message parts.

    Backwards compatible: if parts is None, use content string.
    """
    role: Literal["user", "assistant", "system"] = Field(..., description="Message role")
    content: Optional[str] = Field(None, description="Legacy text content")
    parts: Optional[List[MessagePart]] = Field(None, description="Structured message parts")
    timestamp: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    def get_text_content(self) -> str:
        """Extract plain text content from message."""
        if self.content:
            return self.content
        if self.parts:
            text_parts = []
            for part in self.parts:
                if isinstance(part, TextPart):
                    text_parts.append(part.content)
                elif isinstance(part, CodePart):
                    text_parts.append(f"```{part.language or ''}\n{part.content}\n```")
            return "\n".join(text_parts)
        return ""

    def has_interactive_parts(self) -> bool:
        """Check if message has pending interactive parts requiring user input."""
        if not self.parts:
            return False
        for part in self.parts:
            if isinstance(part, (QuestionPart, ApprovalPart, FormPart, InputPart, OAuthPart)):
                if isinstance(part, ApprovalPart) and part.status != ApprovalStatus.PENDING:
                    continue
                return True
        return False


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    # Enums
    "ToolCallStatus",
    "ApprovalStatus",
    "RiskLevel",
    "ChartType",
    "SourceType",
    "SqlStatus",
    "ThinkingVariant",
    "StepStatus",
    "TimelineEventStatus",
    "MetricTrend",
    "StatusType",
    # Part Schemas
    "MessagePartBase",
    "TextPart",
    "CodePart",
    "ToolCallPart",
    "ToolCallStepPart",
    "QuestionPart",
    "QuestionOptionPart",
    "ApprovalPart",
    "ApprovalItemPart",
    "FormPart",
    "FormFieldPart",
    "InputPart",
    "OAuthPart",
    "TablePart",
    "DataTableColumnPart",
    "ChartPart",
    "SourcePart",
    "FilePart",
    "ImagePart",
    "LinkPart",
    "SqlPart",
    "SqlResultPart",
    "SqlResultColumnPart",
    "JsonPart",
    "DiffPart",
    "DiffLinePart",
    "TimelinePart",
    "TimelineEventPart",
    "MetricPart",
    "StepsPart",
    "StepPart",
    "ThinkingPart",
    "ErrorPart",
    "SuccessPart",
    "WarningPart",
    "InfoPart",
    # Union Type
    "MessagePart",
    # SSE Events
    "SSEEventBase",
    "SSETextDeltaEvent",
    "SSEPartEvent",
    "SSEPartUpdateEvent",
    "SSEToolCallStartEvent",
    "SSEToolCallProgressEvent",
    "SSEToolCallCompleteEvent",
    "SSEQuestionEvent",
    "SSEApprovalEvent",
    "SSEReasoningEvent",
    "SSEContentEvent",
    "SSEErrorEvent",
    "SSEDoneEvent",
    "SSEEvent",
    # Enhanced Message
    "EnhancedChatMessage",
]
