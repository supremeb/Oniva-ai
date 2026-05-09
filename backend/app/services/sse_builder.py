"""
SSE Event Builder Service

Helper functions to construct Server-Sent Events for the chat UI.
Provides type-safe builders for all message part types.
"""

import json
from typing import Any, Dict, List, Optional, Union

from app.schemas.message_parts import (
    # Enums
    ToolCallStatus,
    ApprovalStatus,
    RiskLevel,
    ChartType,
    SourceType,
    SqlStatus,
    ThinkingVariant,
    StepStatus,
    MetricTrend,
    # Part types
    CodePart,
    QuestionPart,
    QuestionOptionPart,
    ApprovalPart,
    ApprovalItemPart,
    TablePart,
    DataTableColumnPart,
    ChartPart,
    SourcePart,
    FilePart,
    ImagePart,
    LinkPart,
    SqlPart,
    SqlResultPart,
    SqlResultColumnPart,
    JsonPart,
    DiffPart,
    TimelinePart,
    TimelineEventPart,
    MetricPart,
    StepsPart,
    StepPart,
    ThinkingPart,
    SuccessPart,
    WarningPart,
    InfoPart,
    MessagePart,
)


class SSEBuilder:
    """
    Builder class for constructing SSE events.

    Usage:
        builder = SSEBuilder()

        # Stream text content
        yield builder.text_delta("Hello, ")
        yield builder.text_delta("world!")

        # Send a complete tool call part
        yield builder.tool_call_start("tool-123", "web_search", {"query": "test"})
        yield builder.tool_call_progress("tool-123", "step-1", "Searching...", StepStatus.ACTIVE)
        yield builder.tool_call_complete("tool-123", ToolCallStatus.SUCCESS, result={"found": True})

        # Send completion
        yield builder.done(session_id="sess-123")
    """

    def __init__(self):
        self._event_id = 0

    def _next_id(self) -> str:
        """Get next event ID."""
        self._event_id += 1
        return str(self._event_id)

    def _format_event(self, event: str, data: Dict[str, Any]) -> Dict[str, str]:
        """Format as SSE event dict for EventSourceResponse."""
        return {
            "event": event,
            "id": self._next_id(),
            "data": json.dumps(data)
        }

    # =========================================================================
    # TEXT STREAMING
    # =========================================================================

    def text_delta(self, delta: str, part_index: Optional[int] = None) -> Dict[str, str]:
        """Send a text content delta (streaming chunk)."""
        data = {"delta": delta}
        if part_index is not None:
            data["partIndex"] = part_index
        return self._format_event("text_delta", data)

    def content(self, delta: str) -> Dict[str, str]:
        """Legacy content event (backwards compatible)."""
        return self._format_event("content", {"delta": delta})

    def reasoning(self, content: str) -> Dict[str, str]:
        """Send chain-of-thought reasoning."""
        return self._format_event("reasoning", {"content": content})

    # =========================================================================
    # COMPLETE PARTS
    # =========================================================================

    def part(self, part: MessagePart) -> Dict[str, str]:
        """Send a complete message part."""
        return self._format_event("part", {"part": part.model_dump()})

    def part_update(self, part_id: str, updates: Dict[str, Any]) -> Dict[str, str]:
        """Update an existing message part."""
        return self._format_event("part_update", {
            "partId": part_id,
            "updates": updates
        })

    # =========================================================================
    # TOOL CALLS
    # =========================================================================

    def tool_call_start(
        self,
        tool_call_id: str,
        tool_name: str,
        args: Optional[Dict[str, Any]] = None,
        label: Optional[str] = None,
        produces_file: bool = False,
    ) -> Dict[str, str]:
        """Signal start of a tool call."""
        data = {
            "toolCallId": tool_call_id,
            "toolName": tool_name,
        }
        if args:
            data["args"] = args
        if label:
            data["label"] = label
        if produces_file:
            data["producesFile"] = True
        return self._format_event("tool_call_start", data)

    def tool_call_progress(
        self,
        tool_call_id: str,
        step_id: str,
        label: str,
        status: StepStatus = StepStatus.ACTIVE,
        message: Optional[str] = None
    ) -> Dict[str, str]:
        """Send tool call progress update."""
        step = {
            "id": step_id,
            "label": label,
            "status": status.value,
        }
        if message:
            step["message"] = message
        return self._format_event("tool_call_progress", {
            "toolCallId": tool_call_id,
            "step": step
        })

    def tool_call_complete(
        self,
        tool_call_id: str,
        status: ToolCallStatus,
        result: Optional[Any] = None,
        error: Optional[str] = None,
        duration_ms: Optional[int] = None
    ) -> Dict[str, str]:
        """Signal completion of a tool call."""
        data = {
            "toolCallId": tool_call_id,
            "status": status.value,
        }
        if result is not None:
            data["result"] = result
        if error:
            data["error"] = error
        if duration_ms is not None:
            data["durationMs"] = duration_ms
        return self._format_event("tool_call_complete", data)

    # =========================================================================
    # INTERACTIVE EVENTS
    # =========================================================================

    def question(
        self,
        question_id: str,
        header: str,
        question_text: str,
        options: List[Dict[str, str]],
        multi_select: bool = False,
        timeout: int = 0,
        default_option_id: Optional[str] = None
    ) -> Dict[str, str]:
        """Send an agent question requiring user input."""
        question_part = QuestionPart(
            id=question_id,
            header=header,
            question=question_text,
            options=[QuestionOptionPart(**opt) for opt in options],
            multiSelect=multi_select,
            timeout=timeout,
            defaultOptionId=default_option_id
        )
        return self._format_event("question", {"question": question_part.model_dump()})

    def approval(
        self,
        approval_id: str,
        title: str,
        description: Optional[str] = None,
        risk_level: RiskLevel = RiskLevel.LOW,
        items: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, str]:
        """Send an approval request."""
        approval_part = ApprovalPart(
            id=approval_id,
            title=title,
            description=description,
            riskLevel=risk_level,
            items=[ApprovalItemPart(**item) for item in (items or [])],
            status=ApprovalStatus.PENDING
        )
        return self._format_event("approval", {"approval": approval_part.model_dump()})

    # =========================================================================
    # STATUS EVENTS
    # =========================================================================

    def thinking(
        self,
        message: Optional[str] = None,
        variant: ThinkingVariant = ThinkingVariant.DEFAULT,
        stage: Optional[str] = None,
        thinking_content: Optional[str] = None,
        is_thinking: Optional[bool] = None,
    ) -> Dict[str, str]:
        """Send thinking indicator."""
        thinking_part = ThinkingPart(
            variant=variant,
            message=message,
            stage=stage,
            showElapsed=True,
            thinkingContent=thinking_content,
            isThinking=is_thinking,
        )
        return self.part(thinking_part)

    def error(
        self,
        message: str,
        title: Optional[str] = None,
        details: Optional[str] = None,
        retryable: bool = False
    ) -> Dict[str, str]:
        """Send an error event."""
        return self._format_event("error", {
            "message": message,
            "title": title,
            "details": details,
            "retryable": retryable
        })

    def success_message(
        self,
        message: str,
        title: Optional[str] = None,
        details: Optional[str] = None
    ) -> Dict[str, str]:
        """Send a success message part."""
        success_part = SuccessPart(
            message=message,
            title=title,
            details=details
        )
        return self.part(success_part)

    def warning_message(
        self,
        message: str,
        title: Optional[str] = None,
        details: Optional[str] = None
    ) -> Dict[str, str]:
        """Send a warning message part."""
        warning_part = WarningPart(
            message=message,
            title=title,
            details=details
        )
        return self.part(warning_part)

    def info_message(
        self,
        message: str,
        title: Optional[str] = None,
        details: Optional[str] = None
    ) -> Dict[str, str]:
        """Send an info message part."""
        info_part = InfoPart(
            message=message,
            title=title,
            details=details
        )
        return self.part(info_part)

    # =========================================================================
    # DATA VISUALIZATION
    # =========================================================================

    def table(
        self,
        columns: List[Dict[str, str]],
        data: List[Dict[str, Any]],
        title: Optional[str] = None,
        sortable: bool = True,
        searchable: bool = False
    ) -> Dict[str, str]:
        """Send a data table."""
        table_part = TablePart(
            columns=[DataTableColumnPart(**col) for col in columns],
            data=data,
            title=title,
            sortable=sortable,
            searchable=searchable
        )
        return self.part(table_part)

    def chart(
        self,
        chart_type: ChartType,
        title: Optional[str] = None,
        description: Optional[str] = None,
        image_url: Optional[str] = None,
        svg_content: Optional[str] = None
    ) -> Dict[str, str]:
        """Send a chart visualization."""
        chart_part = ChartPart(
            chartType=chart_type,
            title=title,
            description=description,
            imageUrl=image_url,
            svgContent=svg_content
        )
        return self.part(chart_part)

    def sql_query(
        self,
        query: str,
        database: Optional[str] = None,
        status: SqlStatus = SqlStatus.RUNNING
    ) -> Dict[str, str]:
        """Send a SQL query preview."""
        sql_part = SqlPart(
            query=query,
            database=database,
            status=status
        )
        return self.part(sql_part)

    def sql_result(
        self,
        columns: List[Dict[str, str]],
        rows: List[List[Any]],
        execution_time: Optional[int] = None,
        total_rows: Optional[int] = None
    ) -> Dict[str, str]:
        """Send SQL query results."""
        result_part = SqlResultPart(
            columns=[SqlResultColumnPart(**col) for col in columns],
            rows=rows,
            executionTime=execution_time,
            totalRows=total_rows or len(rows)
        )
        return self.part(result_part)

    def json_data(
        self,
        data: Any,
        title: Optional[str] = None,
        collapsed_depth: int = 2
    ) -> Dict[str, str]:
        """Send JSON data for tree view."""
        json_part = JsonPart(
            data=data,
            title=title,
            defaultCollapsedDepth=collapsed_depth
        )
        return self.part(json_part)

    def diff(
        self,
        diff_text: str,
        title: Optional[str] = None,
        old_filename: Optional[str] = None,
        new_filename: Optional[str] = None
    ) -> Dict[str, str]:
        """Send a diff view."""
        diff_part = DiffPart(
            diff=diff_text,
            title=title,
            oldFileName=old_filename,
            newFileName=new_filename
        )
        return self.part(diff_part)

    def metric(
        self,
        label: str,
        value: Union[str, int, float],
        change: Optional[float] = None,
        trend: Optional[MetricTrend] = None,
        prefix: Optional[str] = None,
        suffix: Optional[str] = None,
        context: Optional[str] = None
    ) -> Dict[str, str]:
        """Send a metric card."""
        metric_part = MetricPart(
            label=label,
            value=value,
            change=change,
            trend=trend,
            prefix=prefix,
            suffix=suffix,
            context=context
        )
        return self.part(metric_part)

    def source(
        self,
        source_type: SourceType,
        title: str,
        description: Optional[str] = None,
        url: Optional[str] = None,
        citation_number: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, str]:
        """Send a source/citation card."""
        source_part = SourcePart(
            sourceType=source_type,
            title=title,
            description=description,
            url=url,
            citationNumber=citation_number,
            metadata=metadata
        )
        return self.part(source_part)

    def source_event(
        self,
        title: str,
        url: str,
        snippet: Optional[str] = None,
        tool_call_id: Optional[str] = None,
    ) -> Dict[str, str]:
        """Send a lightweight source SSE event (for SourcesPill aggregation)."""
        data: Dict[str, Any] = {"type": "source", "title": title, "url": url}
        if snippet:
            data["snippet"] = snippet
        if tool_call_id:
            data["toolCallId"] = tool_call_id
        return self._format_event("source", data)

    def file(
        self,
        filename: str,
        size: Optional[int] = None,
        file_type: Optional[str] = None,
        download_url: Optional[str] = None,
        content: Optional[str] = None,
    ) -> Dict[str, str]:
        """Send a file preview, optionally with inline text content."""
        file_part = FilePart(
            filename=filename,
            size=size,
            fileType=file_type,
            downloadUrl=download_url,
            content=content,
        )
        return self.part(file_part)

    def image(
        self,
        src: str,
        alt: Optional[str] = None,
        caption: Optional[str] = None,
        aspect_ratio: Optional[str] = None
    ) -> Dict[str, str]:
        """Send an image preview."""
        image_part = ImagePart(
            src=src,
            alt=alt,
            caption=caption,
            aspectRatio=aspect_ratio
        )
        return self.part(image_part)

    def link(
        self,
        url: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        image_url: Optional[str] = None,
        site_name: Optional[str] = None
    ) -> Dict[str, str]:
        """Send a link preview."""
        link_part = LinkPart(
            url=url,
            title=title,
            description=description,
            imageUrl=image_url,
            siteName=site_name
        )
        return self.part(link_part)

    def code_block(
        self,
        code: str,
        language: Optional[str] = None,
        filename: Optional[str] = None
    ) -> Dict[str, str]:
        """Send a code block."""
        code_part = CodePart(
            content=code,
            language=language,
            filename=filename
        )
        return self.part(code_part)

    def timeline(
        self,
        events: List[Dict[str, Any]]
    ) -> Dict[str, str]:
        """Send a timeline visualization."""
        timeline_part = TimelinePart(
            events=[TimelineEventPart(**event) for event in events]
        )
        return self.part(timeline_part)

    def steps(
        self,
        steps: List[Dict[str, Any]],
        orientation: str = "vertical"
    ) -> Dict[str, str]:
        """Send a step progress indicator."""
        steps_part = StepsPart(
            steps=[StepPart(**step) for step in steps],
            orientation=orientation
        )
        return self.part(steps_part)

    # =========================================================================
    # WORKFLOW EXECUTION (Phase 2: Agent-triggered workflows)
    # =========================================================================

    def workflow_preview(self, data: Dict[str, Any]) -> Dict[str, str]:
        """Send a workflow step preview for user approval.

        Emitted when the agent's workflow tool returns pending_approval status.
        Frontend shows an approval card with step details.
        """
        return self._format_event("workflow_preview", data)

    def workflow_progress(
        self,
        workflow_id: str,
        step_id: str,
        label: str,
        status: str,
        message: Optional[str] = None,
        elapsed_ms: Optional[int] = None,
    ) -> Dict[str, str]:
        """Send per-node workflow execution progress.

        Args:
            workflow_id: UUID of the workflow being executed
            step_id: Node ID within the workflow
            label: Human-readable node label
            status: One of 'active', 'complete', 'error', 'halted'
            message: Optional status message
            elapsed_ms: Milliseconds since execution started
        """
        data: Dict[str, Any] = {
            "workflowId": workflow_id,
            "stepId": step_id,
            "label": label,
            "status": status,
        }
        if message:
            data["message"] = message
        if elapsed_ms is not None:
            data["elapsedMs"] = elapsed_ms
        return self._format_event("workflow_progress", data)

    def workflow_complete(
        self,
        workflow_id: str,
        workflow_name: str,
        results: Dict[str, Any],
        executed_nodes: int,
        total_nodes: int,
        elapsed_ms: Optional[int] = None,
    ) -> Dict[str, str]:
        """Send workflow execution completion event.

        Args:
            workflow_id: UUID of the completed workflow
            workflow_name: Human-readable workflow name
            results: Final execution results for all nodes
            executed_nodes: Number of nodes that were executed
            total_nodes: Total number of nodes in the workflow
            elapsed_ms: Total execution time in milliseconds
        """
        data: Dict[str, Any] = {
            "workflowId": workflow_id,
            "workflowName": workflow_name,
            "results": results,
            "executedNodes": executed_nodes,
            "totalNodes": total_nodes,
        }
        if elapsed_ms is not None:
            data["elapsedMs"] = elapsed_ms
        return self._format_event("workflow_complete", data)

    # =========================================================================
    # INTELLIGENT ROUTING
    # =========================================================================

    def deep_work_suggested(self, reasoning: str) -> Dict[str, str]:
        """Suggest deep work mode to the user via a banner."""
        return self._format_event("deep_work_suggested", {"reasoning": reasoning})

    # =========================================================================
    # COMPLETION
    # =========================================================================

    def done(
        self,
        message_id: Optional[str] = None,
        session_id: Optional[str] = None,
        model: Optional[str] = None,
        usage: Optional[Dict[str, int]] = None
    ) -> Dict[str, str]:
        """Signal stream completion."""
        data = {}
        if message_id:
            data["messageId"] = message_id
        if session_id:
            data["sessionId"] = session_id
        if model:
            data["model"] = model
        if usage:
            data["usage"] = usage
        return self._format_event("done", data)


# Singleton instance for convenience
sse_builder = SSEBuilder()


def create_sse_builder() -> SSEBuilder:
    """Create a new SSE builder instance."""
    return SSEBuilder()
