"""Patch dangling tool calls from interrupted agent runs.

When a server crashes mid-execution, the recovered message history may contain
ToolCallPart entries without corresponding ToolReturnPart responses. This causes
the agent to become confused on recovery. This module injects synthetic
cancellation responses for any orphaned tool calls.
"""
from pydantic_ai.messages import (
    ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart,
)

ModelMessage = ModelRequest | ModelResponse


def patch_dangling_tool_calls(messages: list[ModelMessage]) -> list[ModelMessage]:
    """Fix dangling tool calls from interrupted runs.

    Scans message history for ToolCallPart entries without corresponding
    ToolReturnPart responses and injects synthetic cancellation responses.
    """
    responded_ids: set[str] = set()
    for msg in messages:
        for part in msg.parts:
            if isinstance(part, ToolReturnPart) and part.tool_call_id:
                responded_ids.add(part.tool_call_id)

    patched: list[ModelMessage] = []
    for msg in messages:
        patched.append(msg)
        dangling = [
            part for part in msg.parts
            if isinstance(part, ToolCallPart) and part.tool_call_id not in responded_ids
        ]
        for part in dangling:
            patched.append(ModelRequest(parts=[ToolReturnPart(
                tool_name=part.tool_name,
                tool_call_id=part.tool_call_id,
                content="[Tool call cancelled: server restarted during execution. "
                        "You may retry this tool call if needed.]",
            )]))
    return patched
