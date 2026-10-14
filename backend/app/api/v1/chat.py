"""
Chat API with SSE streaming.

SSE Event Types:
- reasoning: Agent's chain-of-thought reasoning
- content / text_delta: Response text delta
- part: Complete message part (tool_call, table, chart, etc.)
- error: Error occurred
- done: Stream complete with metadata
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sse_starlette.sse import EventSourceResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
import json
import logging
import uuid
from typing import Optional, List, AsyncGenerator, Literal
import time

logger = logging.getLogger(__name__)

from app.core.database import AsyncSessionLocal, set_tenant_context
from app.agents.general import general_agent
from app.agents.research import research_agent
from app.agents.data_retrieval import data_retrieval_agent
from app.agents.compliance import compliance_agent
from app.agents.analysis import analysis_agent
from app.agents.ui_testing import ui_testing_agent
from app.auth.dependencies import get_current_user_id, get_current_tenant_id
from app.config import get_settings
from app.services.sse_builder import create_sse_builder
from app.services.agent_circuit_breaker import AgentCircuitBreaker
from app.services.redis_ha import get_redis_service
from app.services.chat_service import (
    extract_reasoning,
    extract_response_text,
    store_memory_async,
    persist_session,
    format_error,
)
from app.core.builtin_agents import uuid_to_slug
from app.services.xml_tool_rescue import parse_xml_tool_calls
from app.agents.skills.context import SkillContext
from app.services.run_event_service import emit_run_event

router = APIRouter()
settings = get_settings()

AGENT_MAP = {
    "general": general_agent,
    "research": research_agent,
    "data-retrieval": data_retrieval_agent,
    "compliance": compliance_agent,
    "analysis": analysis_agent,
}

UI_TESTING_ENABLED = settings.DEBUG or getattr(settings, 'ENABLE_UI_TESTING_AGENT', False)


def is_valid_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except (ValueError, AttributeError):
        return False


class ChatMessage(BaseModel):
    """Typed chat message for input validation (S8)."""
    role: Literal["user", "assistant", "system", "tool"]
    content: str = Field(..., max_length=50000)
    metadata: Optional[dict] = None
    parts: Optional[List[dict]] = None


class ChatRequest(BaseModel):
    messages: List[ChatMessage] = Field(..., max_length=100)
    agentId: Optional[str] = None
    sessionId: Optional[uuid.UUID] = None
    model: Optional[str] = Field(None, description="Override model for this request")
    enableThinking: Optional[bool] = Field(False, description="Enable extended thinking")
    enhancedMode: Optional[bool] = Field(False, description="Enable enhanced SSE protocol")
    streamMode: Optional[Literal["text", "parts", "hybrid"]] = Field("text")
    active_connector_ids: Optional[List[str]] = Field(
        default=None,
        description="Connector IDs to make available for this request"
    )
    active_skill_ids: Optional[List[str]] = Field(
        default=None,
        description="Skill names to enable for this request"
    )
    # Source scoping for web search
    web_search_enabled: Optional[bool] = Field(False, description="Enable web search for this request")
    specific_sites: Optional[List[str]] = Field(default=None, description="Restrict web search to these domains")
    active_knowledge_base_ids: Optional[List[str]] = Field(default=None, description="Knowledge base IDs to search")
    mode: Optional[Literal["chat", "deep_work"]] = Field(None, description="Chat mode: 'chat' (default) or 'deep_work' (plan-first autonomous)")
    projectId: Optional[str] = Field(None, description="Project workspace ID for context injection")


# build_message_history is imported from shared module
from app.services.agent_resolver import build_message_history


def _extract_message_content(latest_message: dict) -> str:
    """Extract text content from a message dict (legacy or enhanced parts format)."""
    if "parts" in latest_message:
        text_parts = [p["text"] for p in latest_message["parts"] if p.get("type") == "text"]
        return " ".join(text_parts) if text_parts else ""
    if "content" in latest_message:
        content = latest_message["content"]
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text_parts = [p.get("content", p.get("text", "")) for p in content if p.get("type") == "text"]
            return " ".join(text_parts) if text_parts else ""
        return str(content)
    return ""


async def _run_agent(agent, message_content, tenant_id, user_id, run_kwargs):
    """Run agent with tenant-scoped DB session. Returns (result, duration_ms)."""
    from sqlalchemy import text as sa_text
    async with AsyncSessionLocal() as db:
        if tenant_id:
            await set_tenant_context(db, str(tenant_id))
        if user_id:
            await db.execute(
                sa_text("SELECT set_config('app.current_user_id', :uid, true)"),
                {"uid": str(user_id)},
            )
        start = time.time()
        result = await agent.run(
            message_content, db=db, user_id=user_id, tenant_id=tenant_id, **run_kwargs,
        )
        await db.commit()
        return result, int((time.time() - start) * 1000)


async def generate_sse_stream(
    request: Request, message_content: str, agent,
    user_id: uuid.UUID, tenant_id: uuid.UUID,
    session_id: Optional[uuid.UUID] = None, agent_id: Optional[str] = None,
    model_override: Optional[str] = None, enable_thinking: bool = False,
    message_history: Optional[List] = None,
    extra_tools: Optional[List] = None, connectors_dict: Optional[dict] = None,
    active_knowledge_base_ids: Optional[List[str]] = None,
    web_search_instruction: Optional[str] = None,
    specific_sites: Optional[List[str]] = None,
    project_instructions: Optional[str] = None,
    project_knowledge_base_id: Optional[str] = None,
    project_default_model: Optional[str] = None,
    project_id: Optional[str] = None,
    project_name: Optional[str] = None,
    project_file_names: Optional[List[str]] = None,
) -> AsyncGenerator[dict, None]:
    """Generate legacy SSE events (reasoning, content, done, error)."""
    event_id = 0
    try:
        run_kwargs = {}
        if session_id:
            run_kwargs['session_id'] = str(session_id)
        if model_override:
            run_kwargs['model_override'] = model_override
        if enable_thinking:
            run_kwargs['enable_thinking'] = True
        if message_history:
            run_kwargs['message_history'] = message_history
        if connectors_dict:
            run_kwargs['connectors'] = connectors_dict
        if extra_tools:
            run_kwargs['extra_tools'] = extra_tools
        if active_knowledge_base_ids:
            run_kwargs['active_knowledge_base_ids'] = active_knowledge_base_ids
        if specific_sites:
            run_kwargs['specific_sites'] = specific_sites
        if project_instructions:
            run_kwargs['project_instructions'] = project_instructions
        if project_knowledge_base_id:
            run_kwargs['project_knowledge_base_id'] = project_knowledge_base_id
        if project_default_model:
            run_kwargs['project_default_model'] = project_default_model
        if project_name:
            run_kwargs['project_name'] = project_name
        if project_file_names:
            run_kwargs['project_file_names'] = project_file_names
        if web_search_instruction:
            run_kwargs['web_search_instruction'] = web_search_instruction

        result, _ = await _run_agent(agent, message_content, tenant_id, user_id, run_kwargs)
        reasoning = extract_reasoning(result)
        response_text = extract_response_text(result.output)
        # Strip any leaked XML tool call markup from the response
        response_text = _strip_function_call_xml(response_text) or response_text

        if reasoning:
            event_id += 1
            yield {"event": "reasoning", "id": str(event_id), "data": json.dumps({"content": reasoning})}

        event_id += 1
        yield {"event": "content", "id": str(event_id), "data": json.dumps({"delta": response_text})}

        store_memory_async(agent, tenant_id, user_id, message_content, response_text, session_id, reasoning)
        created_session_id = await persist_session(
            tenant_id, user_id, session_id, message_content, response_text, reasoning=reasoning,
            project_id=project_id,
        )

        event_id += 1
        reported_model = model_override or getattr(agent, 'model', 'unknown')
        yield {"event": "done", "id": str(event_id), "data": json.dumps({
            "id": str(uuid.uuid4()), "model": reported_model,
            "sessionId": str(created_session_id),
        })}

    except Exception as e:
        logger.exception("Legacy SSE stream error")
        event_id += 1
        yield {"event": "error", "id": str(event_id), "data": json.dumps({"message": format_error(str(e))})}


import os
import re

# Tool names / result keys that indicate a file artifact was produced
_FILE_PRODUCING_TOOLS = {
    "save_analysis_report", "save_research_report", "save_compliance_report",
    "write_file", "save_artifact", "generate_chart",
    "run_code", "run_shell",
}

# Pre-compiled regex patterns for dynamic connector tool names (hot path — compiled once)
_EXECUTE_API_RE = re.compile(r'^execute_(.+)_api$')
_SEARCH_API_RE = re.compile(r'^search_(.+)_api$')

# Sensitive arg keys that should never be forwarded to the frontend
_SENSITIVE_ARG_KEYS = {"api_key", "password", "token", "secret", "credentials"}

# Human-readable label templates for common tools
_TOOL_LABEL_TEMPLATES: dict[str, callable] = {
    "web_search": lambda args: f"Searching for '{args.get('query', '')}'",
    "search_documents": lambda args: f"Searching documents for '{args.get('query', '')}'",
    "fetch_from_connector": lambda args: f"Fetching from {args.get('connector_name', 'connector')}",
    "save_analysis_report": lambda args: f"Writing report: {args.get('title', 'analysis')}",
    "save_research_report": lambda args: f"Writing research report",
    "save_compliance_report": lambda args: f"Writing compliance report",
    "run_code": lambda _: "Running Python code in sandbox",
    "run_shell": lambda args: f"Running command: {args.get('command', 'shell')[:40]}",
    "browse_url": lambda args: f"Browsing {args.get('url', 'website')[:50]}",
    "read_file": lambda args: f"Reading {args.get('path', args.get('filename', 'file'))}",
    "write_file": lambda args: f"Writing {args.get('path', args.get('filename', 'file'))}",
    "list_available_connectors": lambda _: "Listing available connectors",
    "invoke_connector_tool": lambda args: f"Using {args.get('connector_name', 'connector')}",
    "recall_memory": lambda args: f"Recalling: {args.get('query', 'memory')[:40]}",
    "store_memory": lambda _: "Storing memory",
}


_FUNCTION_CALL_XML_RE = re.compile(
    # Complete blocks (all variants: XML, DSML, broken)
    r"<\s*\|?\s*(?:DSML\s*\|?\s*)?function_calls\s*>[\s\S]*?<\s*/?\s*\|?\s*(?:DSML\s*\|?\s*)?(?:inv)?function_calls\s*>"
    # Partial/unclosed block at end
    r"|<\s*\|?\s*(?:DSML\s*\|?\s*)?function_calls\s*>[\s\S]*$"
    # Any tag containing function_calls
    r"|</?[^>]*function_calls[^>]*>"
    # DSML invoke/parameter tags
    r"|<\s*\|?\s*(?:DSML\s*\|?\s*)?/?\s*(?:invoke|parameter)[^>]*>"
    # Standard invoke tags (including <functioninvoke> merged variant)
    r"|</?(?:function)?(?:invoke|antml:invoke|antml:parameter)[^>]*>"
    # Broken fragments: andfunction_calls>, invfunction_calls>, function_cinvoke...
    r"|(?:and|inv)function_calls\s*>"
    r"|function_c\w*invoke[^>]*>"
    # Stray </parameter or "</parameter patterns (with or without closing >)
    r'|"?\s*</?\s*parameter\b[^>]*>?',
    re.IGNORECASE,
)


def _strip_function_call_xml(text: str) -> str:
    """Strip raw function call markup that leaks when Claude falls back to XML/DSML tool format via OpenRouter."""
    return _FUNCTION_CALL_XML_RE.sub("", text).strip()


# Detect the START of a leaked XML tool call block in a text delta.
# These patterns indicate the model is emitting an XML tool call as text
# and subsequent deltas will contain the arguments + closing tags.
_XML_LEAK_OPEN_RE = re.compile(
    r"<\s*\|?\s*(?:DSML\s*\|?\s*)?function_calls\s*>"  # <function_calls>
    r"|<\s*function\s*invoke\b"                           # <functioninvoke (space variant)
    r"|<functioninvoke\b"                                 # <functioninvoke (merged)
    r"|function_c\w*invoke\b"                             # function_cinvoke (mangled)
    r"|<invoke\s+name\s*=",                               # <invoke name= (direct invoke)
    re.IGNORECASE,
)

_XML_LEAK_CLOSE_RE = re.compile(
    r"</?\s*\|?\s*(?:DSML\s*\|?\s*)?(?:inv)?function_calls\s*>"  # </function_calls> or </invfunction_calls>
    r"|</\s*invoke\s*>"                                            # </invoke>
    r"|invfunction_calls\s*>",                                     # invfunction_calls> (broken)
    re.IGNORECASE,
)


def _generate_tool_label(tool_name: str, args: dict | None) -> str:
    """Generate human-readable label from tool name + args."""
    template = _TOOL_LABEL_TEMPLATES.get(tool_name)
    if template and args:
        try:
            return template(args)
        except Exception:
            pass
    elif template:
        try:
            return template({})
        except Exception:
            pass
    # Dynamic connector tool patterns
    m = _EXECUTE_API_RE.match(tool_name)
    if m:
        name = m.group(1).replace('_', ' ').title()
        return f"Running code against {name} API"
    m = _SEARCH_API_RE.match(tool_name)
    if m:
        name = m.group(1).replace('_', ' ').title()
        return f"Searching {name} API operations"
    # Fallback: humanize tool name
    return tool_name.replace("_", " ").title()


def _sanitize_args(args: dict | None) -> dict | None:
    """Remove sensitive values from args before sending to frontend."""
    if not args or not isinstance(args, dict):
        return args
    return {k: v for k, v in args.items() if k.lower() not in _SENSITIVE_ARG_KEYS}

_FILE_RESULT_KEYS = {"artifact_uri", "file_path", "path", "filename"}

_MIME_MAP = {
    "md": "text/markdown", "txt": "text/plain", "pdf": "application/pdf",
    "csv": "text/csv", "json": "application/json", "html": "text/html",
    "py": "text/x-python", "png": "image/png", "jpg": "image/jpeg",
}


def _is_file_producing_tool(tool_name: str) -> bool:
    """Check if a tool name is a file-producing tool (static set or dynamic pattern)."""
    if tool_name in _FILE_PRODUCING_TOOLS:
        return True
    return bool(_EXECUTE_API_RE.match(tool_name))


def _extract_file_info(result_data: dict, tool_name: str) -> tuple[str, int | None, str | None, str | None] | None:
    """Extract (filename, size, file_type, download_url) from a tool result, or None."""
    if not _is_file_producing_tool(tool_name) and not _FILE_RESULT_KEYS.intersection(result_data):
        return None

    # Handle structured results with output_files (from run_code/run_shell)
    output_files = result_data.get("output_files")
    if isinstance(output_files, list) and output_files:
        # Return info for the first file; caller should iterate for multiple
        first_file = output_files[0]
        if isinstance(first_file, str):
            filename = os.path.basename(first_file)
        elif isinstance(first_file, dict):
            filename = first_file.get("filename") or os.path.basename(first_file.get("path", ""))
        else:
            filename = None
        if filename:
            ext = os.path.splitext(filename)[1].lstrip(".").lower()
            file_type = _MIME_MAP.get(ext, f"application/{ext}") if ext else None
            return filename, None, file_type, None

    filename = result_data.get("filename")
    file_path = result_data.get("file_path") or result_data.get("path") or result_data.get("artifact_uri")
    if not filename and file_path:
        filename = os.path.basename(str(file_path))
    if not filename:
        return None

    ext = os.path.splitext(filename)[1].lstrip(".").lower() if filename else None
    file_type = _MIME_MAP.get(ext, f"application/{ext}") if ext else None
    download_url = result_data.get("download_url") or result_data.get("artifact_uri")

    return filename, result_data.get("size"), file_type, download_url


# Tools whose results should be emitted as metric parts
_METRIC_TOOLS = {
    "calculate_pe_ratio", "calculate_roe", "calculate_debt_to_equity",
    "analyze_risk_metrics", "fetch_economic_indicators",
}


def _looks_like_metric(result_data: dict, tool_name: str) -> bool:
    """Check if a tool result looks like a single metric (value + label)."""
    if tool_name in _METRIC_TOOLS:
        return True
    # Heuristic: dict with 'value' key and few other keys
    if "value" in result_data and len(result_data) <= 6:
        return True
    return False


def _extract_metric(result_data: dict, tool_name: str) -> dict | None:
    """Extract metric data from a tool result dict."""
    value = result_data.get("value") or result_data.get("result") or result_data.get("ratio")
    if value is None:
        return None
    label = result_data.get("label") or result_data.get("metric") or tool_name.replace("_", " ").title()
    return {
        "label": str(label),
        "value": value,
        "change": result_data.get("change"),
    }


async def generate_enhanced_sse_stream(
    request: Request, message_content: str, agent,
    user_id: uuid.UUID, tenant_id: uuid.UUID,
    session_id: Optional[uuid.UUID] = None, agent_id: Optional[str] = None,
    stream_mode: str = "hybrid", model_override: Optional[str] = None,
    enable_thinking: bool = False, message_history: Optional[List] = None,
    extra_tools: Optional[List] = None, connectors_dict: Optional[dict] = None,
    active_knowledge_base_ids: Optional[List[str]] = None,
    web_search_instruction: Optional[str] = None,
    specific_sites: Optional[List[str]] = None,
    project_instructions: Optional[str] = None,
    project_knowledge_base_id: Optional[str] = None,
    project_default_model: Optional[str] = None,
    project_id: Optional[str] = None,
    project_name: Optional[str] = None,
    project_file_names: Optional[List[str]] = None,
) -> AsyncGenerator[dict, None]:
    """Generate enhanced SSE events with true streaming via run_stream_events().

    Pydantic AI v1.x run_stream_events() streams ALL events in real-time:
    text deltas, tool calls, tool results, and thinking content.
    """
    from pydantic_ai import (
        FunctionToolCallEvent, FunctionToolResultEvent,
        PartDeltaEvent, PartStartEvent, TextPartDelta, ThinkingPartDelta, ToolCallPartDelta,
    )
    from pydantic_ai.run import AgentRunResultEvent
    from pydantic_ai.messages import TextPart, ThinkingPart
    from app.services.sse_builder import ToolCallStatus

    builder = create_sse_builder()

    # Accumulate structured parts during streaming for persistence.
    # These are plain dicts matching the frontend MessagePart JSON shape
    # so that they can be stored in metadata.parts and restored as richParts.
    accumulated_parts: List[Dict[str, Any]] = []
    _active_thinking_idx: int = -1  # index into accumulated_parts for open thinking segment

    try:
        run_kwargs = {}
        if session_id:
            run_kwargs['session_id'] = str(session_id)
        if model_override:
            run_kwargs['model_override'] = model_override
        if enable_thinking:
            run_kwargs['enable_thinking'] = True
        if connectors_dict:
            run_kwargs['connectors'] = connectors_dict
        if message_history:
            run_kwargs['message_history'] = message_history
        if extra_tools:
            run_kwargs['extra_tools'] = extra_tools
        if active_knowledge_base_ids:
            run_kwargs['active_knowledge_base_ids'] = active_knowledge_base_ids
        if specific_sites:
            run_kwargs['specific_sites'] = specific_sites
        if project_instructions:
            run_kwargs['project_instructions'] = project_instructions
        if project_knowledge_base_id:
            run_kwargs['project_knowledge_base_id'] = project_knowledge_base_id
        if project_default_model:
            run_kwargs['project_default_model'] = project_default_model
        if project_name:
            run_kwargs['project_name'] = project_name
        if project_file_names:
            run_kwargs['project_file_names'] = project_file_names

        full_content = ""
        reasoning_text = ""
        start = time.time()
        usage_dict = None
        tool_start_times: dict[str, float] = {}
        tool_args_cache: dict[str, dict] = {}  # tool_call_id → args for content extraction
        tool_name_cache: dict[str, str] = {}   # tool_call_id → tool_name for intercepts
        final_result = None
        _run_id = str(uuid.uuid4())  # stable run ID for RunEvent correlation

        # State-based XML tool call leak suppression.
        # Some models (DeepSeek via OpenRouter) emit XML tool calls as text
        # across multiple deltas. Per-delta regex can't catch the JSON args
        # that arrive in subsequent deltas after the opening tag. This buffer
        # accumulates text when we detect a leaked opening tag, and suppresses
        # everything until the closing tag arrives (or buffer exceeds limit).
        _xml_leak_buffer = ""
        _xml_leak_suppressing = False
        _XML_LEAK_MAX_BUFFER = 2000  # max chars to buffer before giving up
        _rescued_calls: list = []  # ParsedToolCall objects from leaked XML
        kb_rag_results: list = []  # request-scoped RAG sources (never on the shared agent)

        async with AsyncSessionLocal() as db:
            if tenant_id:
                await set_tenant_context(db, str(tenant_id))
            if user_id:
                from sqlalchemy import text as sa_text
                await db.execute(
                    sa_text("SELECT set_config('app.current_user_id', :uid, true)"),
                    {"uid": str(user_id)},
                )

            # Emit run_started observability event (fire-and-forget)
            await emit_run_event(
                db,
                run_id=_run_id,
                tenant_id=str(tenant_id),
                event_type="run_started",
                session_id=str(session_id) if session_id else None,
                agent_name=agent_id,
            )

            # Build LLM message: clean user message + date context.
            # Web search instructions go to the LLM system context via
            # the `instructions` parameter (NOT the user message) to avoid
            # leaking system prompts to the user.
            from datetime import datetime, timezone
            current_date = datetime.now(timezone.utc).strftime("%B %d, %Y")
            llm_message = f"{message_content}\n\n[Context: Today's date is {current_date}.]"

            if web_search_instruction:
                run_kwargs['web_search_instruction'] = web_search_instruction

            async for event in agent.stream_events(
                llm_message, db=db, user_id=user_id, tenant_id=tenant_id,
                rag_sink=kb_rag_results,
                **run_kwargs,
            ):
                if isinstance(event, PartStartEvent):
                    # PartStartEvent carries the initial content of a new part.
                    # Without this, the first token of text/thinking is silently dropped.
                    if isinstance(event.part, TextPart) and event.part.content:
                        _start_content = event.part.content
                        # State-based XML leak suppression
                        if _xml_leak_suppressing:
                            _xml_leak_buffer += _start_content
                            if _XML_LEAK_CLOSE_RE.search(_xml_leak_buffer) or len(_xml_leak_buffer) > _XML_LEAK_MAX_BUFFER:
                                # End of leaked block — parse for tool calls, then strip remnant
                                _parsed = parse_xml_tool_calls(_xml_leak_buffer)
                                _rescued_calls.extend(_parsed)
                                _cleaned = _strip_function_call_xml(_xml_leak_buffer)
                                _xml_leak_buffer = ""
                                _xml_leak_suppressing = False
                                if _cleaned and not _parsed:
                                    full_content += _cleaned
                                    yield builder.text_delta(_cleaned, part_index=event.index)
                            # else: still buffering, don't emit
                        elif _XML_LEAK_OPEN_RE.search(_start_content):
                            # Detected start of leaked XML block
                            _xml_leak_suppressing = True
                            _xml_leak_buffer = _start_content
                            # Check if the opening AND closing are in the same delta
                            if _XML_LEAK_CLOSE_RE.search(_start_content):
                                _parsed = parse_xml_tool_calls(_xml_leak_buffer)
                                _rescued_calls.extend(_parsed)
                                _cleaned = _strip_function_call_xml(_xml_leak_buffer)
                                _xml_leak_buffer = ""
                                _xml_leak_suppressing = False
                                if _cleaned and not _parsed:
                                    full_content += _cleaned
                                    yield builder.text_delta(_cleaned, part_index=event.index)
                        else:
                            # Normal text — apply per-delta stripping as safety net
                            _lc = _start_content.lower()
                            if "function_calls" in _lc or "invoke" in _lc or "DSML" in _start_content or "</parameter" in _lc:
                                _start_content = _strip_function_call_xml(_start_content)
                            if _start_content:
                                full_content += _start_content
                                yield builder.text_delta(_start_content, part_index=event.index)
                    elif isinstance(event.part, ThinkingPart) and event.part.content:
                        reasoning_text += event.part.content
                        yield builder.reasoning(event.part.content)
                        _active_thinking_idx = len(accumulated_parts)
                        accumulated_parts.append({
                            "type": "thinking",
                            "variant": "accordion",
                            "thinkingContent": event.part.content,
                            "isThinking": False,
                            "showElapsed": True,
                        })

                elif isinstance(event, PartDeltaEvent):
                    if isinstance(event.delta, TextPartDelta):
                        _delta = event.delta.content_delta
                        # State-based XML leak suppression
                        if _xml_leak_suppressing:
                            _xml_leak_buffer += _delta
                            if _XML_LEAK_CLOSE_RE.search(_xml_leak_buffer) or len(_xml_leak_buffer) > _XML_LEAK_MAX_BUFFER:
                                _parsed = parse_xml_tool_calls(_xml_leak_buffer)
                                _rescued_calls.extend(_parsed)
                                _cleaned = _strip_function_call_xml(_xml_leak_buffer)
                                _xml_leak_buffer = ""
                                _xml_leak_suppressing = False
                                if _cleaned and not _parsed:
                                    full_content += _cleaned
                                    yield builder.text_delta(_cleaned, part_index=0)
                        elif _XML_LEAK_OPEN_RE.search(_delta):
                            _xml_leak_suppressing = True
                            _xml_leak_buffer = _delta
                            if _XML_LEAK_CLOSE_RE.search(_delta):
                                _parsed = parse_xml_tool_calls(_xml_leak_buffer)
                                _rescued_calls.extend(_parsed)
                                _cleaned = _strip_function_call_xml(_xml_leak_buffer)
                                _xml_leak_buffer = ""
                                _xml_leak_suppressing = False
                                if _cleaned and not _parsed:
                                    full_content += _cleaned
                                    yield builder.text_delta(_cleaned, part_index=0)
                        else:
                            # Normal delta — per-delta stripping as safety net
                            _dlc = _delta.lower()
                            if "function_calls" in _dlc or "invoke" in _dlc or "DSML" in _delta or "</parameter" in _dlc:
                                _delta = _strip_function_call_xml(_delta)
                            if _delta:
                                full_content += _delta
                                yield builder.text_delta(_delta, part_index=0)
                    elif isinstance(event.delta, ThinkingPartDelta):
                        reasoning_text += event.delta.content_delta
                        yield builder.reasoning(event.delta.content_delta)
                        # Accumulate thinking into structured parts for persistence
                        if _active_thinking_idx >= 0 and _active_thinking_idx < len(accumulated_parts):
                            accumulated_parts[_active_thinking_idx]["thinkingContent"] += event.delta.content_delta
                        else:
                            _active_thinking_idx = len(accumulated_parts)
                            accumulated_parts.append({
                                "type": "thinking",
                                "variant": "accordion",
                                "thinkingContent": event.delta.content_delta,
                                "isThinking": False,
                                "showElapsed": True,
                            })
                    elif isinstance(event.delta, ToolCallPartDelta):
                        pass  # Args streaming — not surfaced to UI

                elif isinstance(event, FunctionToolCallEvent):
                    tool_call_id = event.part.tool_call_id or f"tc-{uuid.uuid4().hex[:8]}"
                    tool_start_times[tool_call_id] = time.monotonic()
                    tool_name_cache[tool_call_id] = event.part.tool_name
                    # Close active thinking segment (next reasoning delta starts a new one)
                    _active_thinking_idx = -1
                    # Parse args (may be dict or JSON string depending on provider)
                    raw_args = event.part.args
                    if isinstance(raw_args, str):
                        try:
                            raw_args = json.loads(raw_args)
                        except (json.JSONDecodeError, TypeError):
                            raw_args = None
                    parsed_args = raw_args if isinstance(raw_args, dict) else None
                    # Stash args for file-producing tools, store_decision, and assess_query_complexity
                    if (_is_file_producing_tool(event.part.tool_name) or event.part.tool_name in ("store_decision", "assess_query_complexity")) and parsed_args:
                        tool_args_cache[tool_call_id] = parsed_args
                    # Generate human-readable label and sanitize args
                    label = _generate_tool_label(event.part.tool_name, parsed_args)
                    safe_args = _sanitize_args(parsed_args)
                    produces_file = _is_file_producing_tool(event.part.tool_name)
                    yield builder.tool_call_start(
                        tool_call_id=tool_call_id,
                        tool_name=event.part.tool_name,
                        args=safe_args,
                        label=label,
                        produces_file=produces_file,
                    )
                    # Accumulate tool_call part for persistence
                    accumulated_parts.append({
                        "type": "tool_call",
                        "id": tool_call_id,
                        "toolName": event.part.tool_name,
                        "status": "running",
                        "args": safe_args,
                        "label": label,
                    })

                elif isinstance(event, FunctionToolResultEvent):
                    tool_call_id = event.tool_call_id or ""
                    elapsed = time.monotonic() - tool_start_times.pop(tool_call_id, time.monotonic())
                    duration_ms = int(elapsed * 1000)
                    is_error = getattr(event.result, 'is_error', False) if hasattr(event, 'result') else False
                    error_msg = None
                    # Extract result summary for frontend display
                    result_summary = None
                    if hasattr(event.result, 'content'):
                        if is_error:
                            error_msg = str(event.result.content)[:200]
                        else:
                            raw_result = event.result.content
                            # Normalize: Pydantic model → dict, JSON string → dict
                            if hasattr(raw_result, 'model_dump'):
                                raw_result = raw_result.model_dump()
                            elif isinstance(raw_result, str) and len(raw_result) < 8000:
                                try:
                                    parsed = json.loads(raw_result)
                                    if isinstance(parsed, dict):
                                        raw_result = parsed
                                except (json.JSONDecodeError, TypeError):
                                    pass
                            if isinstance(raw_result, dict):
                                result_summary = raw_result
                            elif isinstance(raw_result, str) and len(raw_result) < 8000:
                                result_summary = raw_result
                    yield builder.tool_call_complete(
                        tool_call_id=tool_call_id,
                        status=ToolCallStatus.ERROR if is_error else ToolCallStatus.SUCCESS,
                        result=result_summary,
                        error=error_msg,
                        duration_ms=duration_ms,
                    )
                    # Update the accumulated tool_call part with result
                    for _p in reversed(accumulated_parts):
                        if _p.get("type") == "tool_call" and _p.get("id") == tool_call_id:
                            _p["status"] = "error" if is_error else "success"
                            _p["result"] = result_summary
                            _p["error"] = error_msg
                            _p["durationMs"] = duration_ms
                            break

                    # Intelligent routing: emit deep_work_suggested if assess_query_complexity recommends it
                    resolved_tool_name = tool_name_cache.pop(tool_call_id, "")
                    if resolved_tool_name == "assess_query_complexity" and not is_error:
                        cached_args = tool_args_cache.pop(tool_call_id, {})
                        if cached_args.get("needs_deep_work"):
                            yield builder.deep_work_suggested(cached_args.get("reasoning", ""))

                    # Emit file parts for tool results that produced artifacts
                    if not is_error and hasattr(event.result, 'content'):
                        result_data = event.result.content
                        # Normalize: Pydantic model → dict, JSON string → dict
                        if hasattr(result_data, 'model_dump'):
                            result_data = result_data.model_dump()
                        elif isinstance(result_data, str):
                            try:
                                result_data = json.loads(result_data)
                            except (json.JSONDecodeError, TypeError):
                                result_data = None
                        if isinstance(result_data, dict):
                            # Handle structured results with output_files (run_code/run_shell)
                            output_files = result_data.get("output_files", [])
                            if isinstance(output_files, list):
                                for of in output_files:
                                    fname = os.path.basename(of) if isinstance(of, str) else (of.get("filename") if isinstance(of, dict) else None)
                                    if fname:
                                        ext = os.path.splitext(fname)[1].lstrip(".").lower()
                                        ftype = _MIME_MAP.get(ext, f"application/{ext}") if ext else None
                                        yield builder._format_event("part", {"part": {
                                            "type": "file", "filename": fname,
                                            "fileType": ftype, "toolCallId": tool_call_id,
                                        }})

                            # Also check for single-file tool results (save_report, write_file, etc.)
                            if result_data.get('success'):
                                file_info = _extract_file_info(result_data, event.result.tool_name)
                                if file_info:
                                    fname, fsize, ftype, furl = file_info
                                    stashed_args = tool_args_cache.pop(tool_call_id, {})
                                    file_content = (
                                        stashed_args.get("report_content")
                                        or stashed_args.get("content")
                                        or stashed_args.get("text")
                                        or stashed_args.get("markdown")
                                    )
                                    # Emit with toolCallId for correlation
                                    file_data = {"type": "file", "filename": fname, "toolCallId": tool_call_id}
                                    if fsize: file_data["size"] = fsize
                                    if ftype: file_data["fileType"] = ftype
                                    if furl: file_data["downloadUrl"] = furl
                                    if file_content: file_data["content"] = file_content
                                    accumulated_parts.append(file_data)
                                    yield builder._format_event("part", {"part": file_data})

                            # Detect metric-like results (single value with label)
                            if _looks_like_metric(result_data, event.result.tool_name):
                                metric_data = _extract_metric(result_data, event.result.tool_name)
                                if metric_data:
                                    metric_part = {"type": "metric", "toolCallId": tool_call_id, **metric_data}
                                    accumulated_parts.append(metric_part)
                                    yield builder._format_event("part", {"part": metric_part})

                            # Emit memory_saved card for store_decision results
                            if event.result.tool_name == "store_decision" and result_data.get("success") and result_data.get("memory_id"):
                                stashed_args = tool_args_cache.pop(tool_call_id, {})
                                snippet = f"{stashed_args.get('context', '')}\n{stashed_args.get('decision', '')}".strip()[:200]
                                mem_saved_part = {
                                    "type": "memory_saved",
                                    "memoryId": str(result_data["memory_id"]),
                                    "snippet": snippet,
                                    "agentName": stashed_args.get("agent_name", ""),
                                    "actionType": stashed_args.get("action_type", ""),
                                    "toolCallId": tool_call_id,
                                }
                                yield builder._format_event("part", {"part": mem_saved_part})
                                accumulated_parts.append(mem_saved_part)

                            # Emit source events for web search results
                            if event.result.tool_name in ("web_search", "search_web"):
                                search_results = result_data.get("results", [])
                                if isinstance(search_results, list):
                                    for sr in search_results:
                                        if isinstance(sr, dict) and sr.get("url"):
                                            src_part = {
                                                "type": "source",
                                                "sourceType": "url",
                                                "title": sr.get("title", "Web result"),
                                                "url": sr["url"],
                                                "description": sr.get("snippet", ""),
                                            }
                                            yield builder._format_event("source", {
                                                "type": "source",
                                                "title": sr.get("title", "Web result"),
                                                "url": sr["url"],
                                                "snippet": sr.get("snippet", ""),
                                                "sourceType": "url",
                                            })
                                            accumulated_parts.append(src_part)

                            # Emit source events for memory recall results
                            if event.result.tool_name in ("search_past_decisions", "search_entities"):
                                try:
                                    memory_results = result_data.get("results", [])
                                    if isinstance(memory_results, list):
                                        for mem in memory_results[:5]:
                                            if not isinstance(mem, dict):
                                                continue
                                            mem_snippet = (mem.get("context_decision") or "")[:120]
                                            yield builder._format_event("source", {
                                                "title": f"Memory: {mem.get('action_type', 'decision')}",
                                                "description": mem_snippet,
                                                "url": f"/memory?id={mem.get('id', '')}",
                                                "toolCallId": tool_call_id,
                                                "sourceType": "memory",
                                                "metadata": {
                                                    "memoryId": mem.get("id"),
                                                    "similarity": mem.get("similarity"),
                                                    "entities": mem.get("entities", []),
                                                    "agentName": mem.get("agent_name", ""),
                                                },
                                            })
                                            accumulated_parts.append({
                                                "type": "source",
                                                "sourceType": "memory",
                                                "title": f"Memory: {mem.get('action_type', 'decision')}",
                                                "url": f"/memory?id={mem.get('id', '')}",
                                                "description": mem_snippet,
                                            })
                                except Exception:
                                    pass

                elif isinstance(event, AgentRunResultEvent):
                    final_result = event.result
                    if hasattr(final_result, 'usage') and callable(final_result.usage):
                        u = final_result.usage()
                        usage_dict = {
                            "input_tokens": getattr(u, 'request_tokens', 0) or 0,
                            "output_tokens": getattr(u, 'response_tokens', 0) or 0,
                        }

            # Flush any remaining XML leak buffer (model stopped mid-leak)
            if _xml_leak_suppressing and _xml_leak_buffer:
                _parsed = parse_xml_tool_calls(_xml_leak_buffer)
                _rescued_calls.extend(_parsed)
                _cleaned = _strip_function_call_xml(_xml_leak_buffer)
                _xml_leak_buffer = ""
                _xml_leak_suppressing = False
                if _cleaned and not _parsed:
                    full_content += _cleaned
                    yield builder.text_delta(_cleaned, part_index=0)

            # Leaked-XML "tool calls" are never executed (policy bypass: the text
            # is model/user-influenced and skipped the agent allow-list + guards).
            if _rescued_calls:
                logger.warning(
                    "Dropped %d tool call(s) leaked as text (not executed): %s",
                    len(_rescued_calls), [c.tool_name for c in _rescued_calls],
                )

            # Commit any changes made by tools (e.g., store_decision → episodic_memories)
            try:
                await db.commit()
            except Exception as commit_err:
                logger.warning(f"DB commit after streaming failed: {commit_err}")

        # If run_stream_events produced structured output but no text deltas,
        # extract the response text from the final result
        if final_result and not full_content:
            full_content = extract_response_text(final_result.output)
            # Stream the extracted text
            yield builder.text_delta(full_content, part_index=0)

        # Emit SSE source events for Knowledge Base RAG results
        # (collected via the request-scoped rag_sink passed to stream_events)
        if kb_rag_results:
            seen_docs: set[str] = set()
            for r in kb_rag_results:
                doc_name = r.get("document_name") or r.get("source") or "Unknown document"
                doc_id = r.get("document_id", "")
                # Deduplicate by document_id (multiple chunks from same doc)
                if doc_id in seen_docs:
                    continue
                seen_docs.add(doc_id)
                snippet = (r.get("content") or "")[:200]
                score = r.get("score", 0)
                kb_url = f"/knowledge-bases/{r.get('knowledge_base_id', '')}/documents/{doc_id}"
                yield builder._format_event("source", {
                    "type": "source",
                    "title": doc_name,
                    "url": kb_url,
                    "snippet": snippet,
                    "sourceType": "document",
                })
                accumulated_parts.append({
                    "type": "source",
                    "sourceType": "document",
                    "title": doc_name,
                    "url": kb_url,
                    "description": snippet,
                })

        # Append response text as a text part to accumulated_parts so that
        # frontend restores from metadata.parts have the response content
        # in the ChainOfThought rendering path (not just message.content).
        if full_content:
            accumulated_parts.append({
                "type": "text",
                "content": full_content,
                "isStreaming": False,
            })

        duration_ms = int((time.time() - start) * 1000)
        response_text = full_content
        reasoning = reasoning_text or None

        store_memory_async(agent, tenant_id, user_id, message_content, response_text, session_id, reasoning)
        reported_model = model_override or getattr(agent, 'model', 'unknown')
        created_session_id = await persist_session(
            tenant_id, user_id, session_id, message_content, response_text,
            reasoning=reasoning, message_parts=accumulated_parts,
            duration_ms=duration_ms, model=reported_model,
            project_id=project_id,
        )

        # Emit run_completed observability event (fire-and-forget)
        _in_tokens = usage_dict.get("input_tokens") if usage_dict else None
        _out_tokens = usage_dict.get("output_tokens") if usage_dict else None
        await emit_run_event(
            db,
            run_id=_run_id,
            tenant_id=str(tenant_id),
            event_type="run_completed",
            session_id=str(session_id) if session_id else None,
            agent_name=agent_id,
            input_tokens=_in_tokens,
            output_tokens=_out_tokens,
        )

        yield builder.done(
            message_id=str(uuid.uuid4()),
            session_id=str(created_session_id),
            model=reported_model,
            usage=usage_dict,
        )

    except Exception as e:
        logger.exception(f"Streaming error: {e}")
        # Emit run_failed observability event (fire-and-forget, own session)
        try:
            async with AsyncSessionLocal() as _err_db:
                if tenant_id:
                    await set_tenant_context(_err_db, str(tenant_id))
                await emit_run_event(
                    _err_db,
                    run_id=_run_id,
                    tenant_id=str(tenant_id),
                    event_type="run_failed",
                    session_id=str(session_id) if session_id else None,
                    agent_name=agent_id,
                    payload={"error": str(e)},
                )
                await _err_db.commit()
        except Exception:
            logger.warning("Failed to emit run_failed RunEvent", exc_info=True)
        yield builder.error(
            message=format_error(str(e)), title="Chat Error",
            retryable="rate limit" in str(e).lower(),
        )


async def _resolve_custom_agent(agent_id: str, tenant_id: uuid.UUID, chat_request: ChatRequest, user_id: uuid.UUID | None = None):
    """Load a custom agent from DB and wire its skills as Pydantic AI tools.

    Returns (agent, tools, error, workflow_ids) where error is None on success
    or a dict with keys 'code' ('agent_not_found' | 'agent_unauthorized') and
    'message'.  workflow_ids is a list of workflow ID strings attached to the agent.
    """
    from app.services.agent_resolver import resolve_custom_agent

    # Guard: tenant_id must be present for RLS context
    if not tenant_id:
        logger.error("_resolve_custom_agent called without tenant_id")
        return general_agent, [], {"code": "agent_unauthorized", "message": "Authentication required"}, []

    async with AsyncSessionLocal() as agent_db:
        await set_tenant_context(agent_db, str(tenant_id))
        from app.models.custom_agent import CustomAgent

        # S2: Explicit tenant_id filter — prevents cross-tenant agent access
        result = await agent_db.execute(
            select(CustomAgent).where(
                CustomAgent.id == uuid.UUID(agent_id),
                CustomAgent.tenant_id == tenant_id,
            )
        )
        row = result.scalar_one_or_none()

        # S27: Structured error taxonomy instead of silent fallback
        if not row:
            logger.warning("Agent not found or inactive: agent_id=%s, tenant_id=%s", agent_id, tenant_id)
            return general_agent, [], {"code": "agent_not_found", "message": "This agent has been removed or does not exist"}, []

        if not row.is_active:
            logger.info("Agent deactivated: agent_id=%s", agent_id)
            return general_agent, [], {"code": "agent_not_found", "message": "This agent has been deactivated"}, []

        # Ownership / visibility check: user must own the agent or it must be shared
        if user_id and row.user_id != user_id:
            if row.visibility not in ('team', 'tenant'):
                logger.warning("Unauthorized agent access: user=%s, agent=%s, visibility=%s", user_id, agent_id, row.visibility)
                return general_agent, [], {"code": "agent_unauthorized", "message": "This agent is no longer accessible"}, []

            # S17: Team membership verification — ensure user belongs to the agent's team
            if row.visibility == 'team' and row.team_id:
                from app.models.team import TeamMembership
                membership = await agent_db.execute(
                    select(TeamMembership).where(
                        TeamMembership.team_id == row.team_id,
                        TeamMembership.user_id == user_id,
                    )
                )
                if not membership.scalar_one_or_none():
                    logger.warning("Team membership check failed: user=%s, agent=%s, team=%s", user_id, agent_id, row.team_id)
                    return general_agent, [], {"code": "agent_unauthorized", "message": "This agent is no longer accessible"}, []

    agent, tools, connector_ids, knowledge_base_ids, model, workflow_ids = await resolve_custom_agent(row)

    # Caller-side: mutate chat_request.model if not explicitly set
    if not chat_request.model:
        chat_request.model = model

    # Caller-side: merge agent's knowledge bases into request
    # Agent-configured KBs are always included (mandatory). User-selected KBs merged additively.
    if knowledge_base_ids:
        existing = set(chat_request.active_knowledge_base_ids or [])
        merged = list(existing | set(knowledge_base_ids))
        chat_request.active_knowledge_base_ids = merged

    return agent, tools, None, workflow_ids


# _wire_skills_and_connectors imported from shared module
from app.services.agent_resolver import wire_skills_and_connectors as _wire_skills_and_connectors


@router.post("")
@router.post("/", include_in_schema=False)
async def chat(
    request: Request,
    chat_request: ChatRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
):
    """Chat with AI agents using SSE streaming."""
    # Convert typed ChatMessage objects to dicts for downstream functions
    messages_as_dicts = [msg.model_dump(exclude_none=True) for msg in chat_request.messages]

    user_messages = [msg for msg in messages_as_dicts if msg.get("role") == "user"]
    if not user_messages:
        raise HTTPException(status_code=400, detail="No user message found")

    message_content = _extract_message_content(user_messages[-1])
    if not message_content.strip():
        raise HTTPException(status_code=400, detail="Empty message")

    agent_id = chat_request.agentId or "general"
    # Normalise built-in UUID → slug so AGENT_MAP lookup works
    agent_id = uuid_to_slug(agent_id)
    agent = AGENT_MAP.get(agent_id, general_agent)
    custom_agent_tools = []
    agent_workflow_ids = []

    if is_valid_uuid(agent_id):
        redis = await get_redis_service()
        breaker = AgentCircuitBreaker(redis)
        await breaker.check_health(uuid.UUID(agent_id))
        agent, custom_agent_tools, resolve_error, agent_workflow_ids = await _resolve_custom_agent(agent_id, tenant_id, chat_request, user_id=user_id)

        # S2+S27: Return SSE error event for agent resolution failures
        if resolve_error:
            async def _agent_error_stream():
                yield {"event": "error", "id": "1", "data": json.dumps({
                    "message": resolve_error["message"],
                    "code": resolve_error["code"],
                    "retryable": False,
                })}
            return EventSourceResponse(_agent_error_stream())

    # Load default skills for agent type, then merge with user-selected skills
    from app.agents.skills.registry import get_default_skills
    default_skill_names = get_default_skills(agent_id)

    # Merge defaults with user-selected skills (union, defaults always present)
    user_skill_ids = list(chat_request.active_skill_ids or [])
    skill_ids = list(set(default_skill_names + user_skill_ids))

    # Auto-include web_search skill when web search is enabled
    if chat_request.web_search_enabled and "web_search" not in skill_ids:
        skill_ids.append("web_search")

    # Intelligent routing: inject assess_query_complexity when feature flag is on
    if settings.ENABLE_INTELLIGENT_ROUTING and "assess_query_complexity" not in skill_ids:
        skill_ids.insert(0, "assess_query_complexity")

    # Deep Work mode: inject planning skills so the agent can present_plan / ask_user / etc.
    if chat_request.mode == "deep_work":
        for planning_skill in ["present_plan", "update_todo", "ask_user", "complete_task"]:
            if planning_skill not in skill_ids:
                skill_ids.append(planning_skill)

    # M-7: Validate skill IDs against the registry — warn on unknown skills
    if skill_ids:
        from app.agents.skills.registry import SkillRegistry
        known_skills = set(SkillRegistry.get_all_skills().keys())
        unknown_skills = [s for s in skill_ids if s not in known_skills]
        if unknown_skills:
            logger.warning("Unknown skill IDs requested (will be ignored by wiring): %s", unknown_skills)
            # Remove unknown skills so they don't cause silent failures downstream
            skill_ids = [s for s in skill_ids if s in known_skills]

    # M-7: Log unrecognized connector IDs (validation happens in wire_skills_and_connectors
    # which filters by tenant_id, but log here for observability)
    if chat_request.active_connector_ids:
        logger.debug("Connector IDs requested: %s", chat_request.active_connector_ids)

    # Ensure a session_id exists early — needed by sandbox connector tools and
    # workspace skills.  If the frontend didn't send one (first message in a new
    # chat), generate one now.
    effective_session_id = chat_request.sessionId or uuid.uuid4()

    # Wire skills, connectors, and workflows from request
    extra_tools = []
    connectors_dict = {}
    _wf_ids = agent_workflow_ids if is_valid_uuid(agent_id) else []
    if skill_ids or chat_request.active_connector_ids or _wf_ids:
        extra_tools, connectors_dict = await _wire_skills_and_connectors(
            agent=agent,
            skill_ids=skill_ids or None,
            connector_ids=chat_request.active_connector_ids,
            tenant_id=tenant_id,
            user_id=user_id,
            user_session_id=str(effective_session_id),
            workflow_ids=_wf_ids or None,
        )

    # Merge custom agent's configured tools (deduplicate by function name)
    if custom_agent_tools:
        existing_names = {getattr(t, '__name__', '') for t in extra_tools}
        for tool in custom_agent_tools:
            if getattr(tool, '__name__', '') not in existing_names:
                extra_tools.append(tool)

    # Build web search instruction (injected into LLM context, NOT the user message)
    from datetime import datetime, timezone
    current_date = datetime.now(timezone.utc).strftime("%B %d, %Y")

    web_search_instruction = None
    if chat_request.web_search_enabled:
        web_search_instruction = f"Today's date is {current_date}. Web search is enabled. You MUST use the web_search tool to find current information before answering. Do not rely on your training data alone — call web_search with a relevant query first, then synthesize the results into your answer. Always include specific facts, numbers, and data from the search results in your response. When searching, include the current year ({datetime.now(timezone.utc).year}) in your search queries to get recent results."
        if chat_request.specific_sites:
            sites_str = ", ".join(chat_request.specific_sites)
            web_search_instruction += f" Restrict searches to these domains: {sites_str}."

    history = build_message_history(messages_as_dicts)

    # Resolve effective model: explicit request > tenant default provider > agent default
    effective_model = chat_request.model
    if not effective_model:
        from app.services.llm_gateway import resolve_tenant_default_model
        async with AsyncSessionLocal() as db_tmp:
            await set_tenant_context(db_tmp, str(tenant_id))
            tenant_default = await resolve_tenant_default_model(db_tmp, tenant_id)
            if tenant_default:
                effective_model = tenant_default
                logger.info(f"Using tenant default model: {tenant_default}")

    # Wire SessionStorage: ensure filesystem session
    # directory exists so workspace skills, plan/todo recitation, and
    # audit logging have a place to write.
    if settings.USE_SESSION_FILESYSTEM:
        try:
            from app.services.session_storage import get_session_storage
            storage = get_session_storage()
            await storage.get_or_create_session(
                str(tenant_id), str(effective_session_id), str(user_id)
            )
        except Exception as e:
            logger.warning(f"Failed to create filesystem session: {e}")

    # E2B sandbox tools: execute_code + run_shell with lazy sandbox init.
    # Resolve E2B key from user's stored API keys (DB) or env var.
    _e2b_api_key: str | None = settings.E2B_API_KEY or None
    if settings.ENABLE_SANDBOX and not _e2b_api_key:
        try:
            from app.services.sandbox_executor import resolve_e2b_key
            _e2b_api_key = await resolve_e2b_key(tenant_id, user_id)
        except Exception:
            pass

    if settings.ENABLE_SANDBOX and _e2b_api_key:
        from app.services.sandbox_executor import get_sandbox_executor
        _sandbox_executor = get_sandbox_executor()
        _sandbox_session_id = str(effective_session_id)
        _sandbox_e2b_key = _e2b_api_key  # capture for closures

        def _join_log_lines(lines) -> str:
            """Normalize E2B log output (may be list or string)."""
            if isinstance(lines, list):
                return "\n".join(str(line) for line in lines)
            return str(lines) if lines else ""

        async def run_code(ctx, code: str) -> dict:
            """Execute Python code in an isolated sandbox. Returns stdout, stderr, and chart outputs. Use for data analysis, calculations, and visualizations."""
            handle = await _sandbox_executor.get_or_create(
                _sandbox_session_id, api_key=_sandbox_e2b_key,
            )
            result = await handle.execute_code(code)
            return {
                "success": not result.error,
                "stdout": _join_log_lines(result.stdout) or "",
                "stderr": _join_log_lines(result.stderr) or "",
                "output_files": result.output_files or [],
                "duration_seconds": getattr(result, 'duration_seconds', None),
            }

        async def run_shell(ctx, command: str) -> dict:
            """Run a shell command in an isolated sandbox. Use for file operations, installing packages, or running scripts."""
            from e2b_code_interpreter import CommandExitException
            handle = await _sandbox_executor.get_or_create(
                _sandbox_session_id, api_key=_sandbox_e2b_key,
            )
            try:
                result = await handle.run_shell(command)
                return {
                    "success": result.exit_code == 0,
                    "stdout": _join_log_lines(result.stdout) or "",
                    "stderr": _join_log_lines(result.stderr) or "",
                    "exit_code": result.exit_code,
                    "output_files": getattr(result, 'output_files', []) or [],
                }
            except CommandExitException as e:
                logger.warning("Sandbox shell command failed: %s", e)
                return {
                    "success": False,
                    "stdout": _join_log_lines(getattr(e, 'stdout', '')) or "",
                    "stderr": _join_log_lines(getattr(e, 'stderr', '')) or "Command failed",
                    "exit_code": getattr(e, 'exit_code', 1),
                    "output_files": [],
                }

        extra_tools.append(run_code)
        extra_tools.append(run_shell)

        # Browser tools (gated by ENABLE_BROWSER_SANDBOX)
        if settings.ENABLE_BROWSER_SANDBOX:
            from app.security.url_validator import is_url_safe

            async def browser_navigate(ctx, url: str, task: str = "extract the main text content of the page") -> dict:
                """Navigate to a URL using a headless Chromium browser in the sandbox. Renders JavaScript and extracts page content. Use for SPAs, dynamic pages, or when you need to browse a website."""
                error = is_url_safe(url)
                if error:
                    return {"error": error, "status": "blocked"}
                handle = await _sandbox_executor.get_or_create(
                    _sandbox_session_id, api_key=_sandbox_e2b_key,
                )
                script = (
                    'import asyncio, json, os\n'
                    'os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/ms-playwright")\n'
                    'async def main():\n'
                    '    from browser_use import Agent\n'
                    '    from langchain_openai import ChatOpenAI\n'
                    '    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)\n'
                    f'    agent = Agent(task="Go to {url} and {task}", llm=llm)\n'
                    '    result = await agent.run()\n'
                    f'    print(json.dumps({{"status": "success", "url": {url!r}, "content": str(result)}}))\n'
                    'await main()\n'
                )
                try:
                    result = await handle.execute_code(script, timeout=120)
                    stdout = _join_log_lines(result.stdout)
                    if result.error:
                        return {"error": result.error, "status": "failed"}
                    import json as _json
                    try:
                        return _json.loads(stdout.strip().split("\n")[-1])
                    except (ValueError, IndexError):
                        return {"status": "success", "url": url, "content": stdout[:10000]}
                except Exception as e:
                    logger.exception("Browser navigate failed")
                    return {"error": "Browser navigation failed", "status": "failed"}

            async def browser_screenshot(ctx, url: str, full_page: bool = True, filename: str = "screenshot.png") -> dict:
                """Take a screenshot of a URL using headless Chromium in the sandbox. Saves a PNG file. Use for visual verification or capturing dynamic page state."""
                error = is_url_safe(url)
                if error:
                    return {"error": error, "status": "blocked"}
                if "/" in filename or ".." in filename:
                    return {"error": "Invalid filename", "status": "blocked"}
                handle = await _sandbox_executor.get_or_create(
                    _sandbox_session_id, api_key=_sandbox_e2b_key,
                )
                script = (
                    'import asyncio, json, os\n'
                    'os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/ms-playwright")\n'
                    'async def main():\n'
                    '    from playwright.async_api import async_playwright\n'
                    '    async with async_playwright() as p:\n'
                    '        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu"])\n'
                    '        page = await browser.new_page(viewport={"width": 1280, "height": 720})\n'
                    f'        await page.goto({url!r}, wait_until="networkidle", timeout=30000)\n'
                    f'        await page.screenshot(path="/workspace/{filename}", full_page={full_page})\n'
                    '        await browser.close()\n'
                    f'    size = os.path.getsize("/workspace/{filename}")\n'
                    f'    print(json.dumps({{"status": "success", "url": {url!r}, "file_path": "/workspace/{filename}", "size_bytes": size}}))\n'
                    'await main()\n'
                )
                try:
                    result = await handle.execute_code(script, timeout=120)
                    stdout = _join_log_lines(result.stdout)
                    if result.error:
                        return {"error": result.error, "status": "failed"}
                    import json as _json
                    try:
                        return _json.loads(stdout.strip().split("\n")[-1])
                    except (ValueError, IndexError):
                        return {"status": "success", "url": url, "content": stdout[:5000]}
                except Exception as e:
                    logger.exception("Browser screenshot failed")
                    return {"error": "Browser screenshot failed", "status": "failed"}

            extra_tools.append(browser_navigate)
            extra_tools.append(browser_screenshot)

    # Resolve project context (instructions, KB, default model, name, file list)
    project_instructions = None
    project_knowledge_base_id = None
    project_default_model = None
    project_name = None
    project_file_names: list[str] = []
    if chat_request.projectId:
        try:
            from app.services.project_service import ProjectService
            from app.models.knowledge_document import KnowledgeDocument
            async with AsyncSessionLocal() as db_proj:
                await set_tenant_context(db_proj, str(tenant_id))
                proj_service = ProjectService(db_proj)
                proj_role = await proj_service.get_user_role(
                    uuid.UUID(chat_request.projectId), user_id, tenant_id,
                )
                if proj_role:
                    proj = await proj_service._get_project_raw(
                        uuid.UUID(chat_request.projectId), tenant_id,
                    )
                    if proj:
                        project_name = proj.name
                        project_instructions = proj.instructions
                        project_knowledge_base_id = str(proj.knowledge_base_id) if proj.knowledge_base_id else None
                        project_default_model = proj.default_model
                        # Fetch project file names for context
                        if proj.knowledge_base_id:
                            docs_result = await db_proj.execute(
                                select(KnowledgeDocument.filename, KnowledgeDocument.file_type, KnowledgeDocument.status)
                                .where(
                                    KnowledgeDocument.knowledge_base_id == proj.knowledge_base_id,
                                    KnowledgeDocument.tenant_id == tenant_id,
                                    KnowledgeDocument.status == "ready",
                                )
                                .order_by(KnowledgeDocument.filename)
                            )
                            project_file_names = [row.filename for row in docs_result.all()]
        except Exception as e:
            logger.warning(f"Failed to resolve project context: {e}")

    common_kwargs = dict(
        request=request, message_content=message_content, agent=agent,
        user_id=user_id, tenant_id=tenant_id,
        session_id=effective_session_id, agent_id=agent_id,
        model_override=effective_model,
        enable_thinking=chat_request.enableThinking or False,
        message_history=history,
        extra_tools=extra_tools,
        connectors_dict=connectors_dict,
        active_knowledge_base_ids=chat_request.active_knowledge_base_ids,
        web_search_instruction=web_search_instruction,
        specific_sites=chat_request.specific_sites,
        project_instructions=project_instructions,
        project_knowledge_base_id=project_knowledge_base_id,
        project_default_model=project_default_model,
        project_id=chat_request.projectId,
        project_name=project_name,
        project_file_names=project_file_names,
    )

    if chat_request.enhancedMode:
        gen = generate_enhanced_sse_stream(**common_kwargs, stream_mode=chat_request.streamMode or "hybrid")
    else:
        gen = generate_sse_stream(**common_kwargs)

    return EventSourceResponse(
        gen, media_type="text/event-stream", ping=15, send_timeout=30.0,
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            # Prevent GZipMiddleware from buffering SSE events.
            # GZip internally buffers small chunks and only flushes when its
            # buffer fills or the stream ends, which defeats real-time streaming.
            "Content-Encoding": "identity",
        },
    )


# --- UI Testing endpoints (feature-flagged) ---

class UITestRequest(BaseModel):
    command: str = Field(..., description="Test command (e.g., 'show table', 'showcase')")


@router.post("/ui-test")
async def ui_test(
    request: Request,
    test_request: UITestRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
):
    """UI Testing endpoint for demonstrating chat components."""
    if not UI_TESTING_ENABLED:
        raise HTTPException(status_code=403, detail="UI testing agent is disabled.")

    return EventSourceResponse(
        ui_testing_agent.stream_demo(test_request.command),
        media_type="text/event-stream", ping=15, send_timeout=30.0,
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no", "Content-Encoding": "identity"},
    )


@router.get("/ui-test/status")
async def ui_test_status():
    """Check if UI testing agent is enabled."""
    return {
        "enabled": UI_TESTING_ENABLED,
        "commands": [
            "show text", "show code", "show tool", "show question",
            "show approval", "show table", "show chart", "show sql",
            "show json", "show diff", "show timeline", "show metrics",
            "show status", "show file", "show link", "show source",
            "show form", "show steps", "showcase", "show all",
        ],
    }
