"""Tests for build_message_history() tool context reconstruction.

Verifies that the enhanced build_message_history() correctly reconstructs
ToolCallPart/ToolReturnPart from metadata.parts data so the LLM has full
tool context on followup turns.
"""

import pytest
from pydantic_ai.messages import (
    ModelRequest, ModelResponse,
    UserPromptPart, TextPart as AiTextPart,
    ToolCallPart, ToolReturnPart,
)

from app.services.agent_resolver import build_message_history


class TestBuildMessageHistoryTextOnly:
    """Regression: text-only messages work as before."""

    def test_text_only_history(self):
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "How are you?"},
        ]
        history = build_message_history(messages)
        assert history is not None
        assert len(history) == 2  # excludes last user message
        assert isinstance(history[0], ModelRequest)
        assert isinstance(history[0].parts[0], UserPromptPart)
        assert history[0].parts[0].content == "Hello"
        assert isinstance(history[1], ModelResponse)
        assert isinstance(history[1].parts[0], AiTextPart)
        assert history[1].parts[0].content == "Hi there!"


class TestBuildMessageHistorySingleToolCall:
    """Single tool call with dict result reconstructs ToolCallPart + ToolReturnPart."""

    def test_single_tool_call_with_result(self):
        messages = [
            {"role": "user", "content": "Browse example.com"},
            {
                "role": "assistant",
                "content": "Here's what I found on example.com...",
                "metadata": {
                    "parts": [
                        {
                            "type": "tool_call",
                            "id": "tc-abc123",
                            "toolName": "browser_navigate",
                            "status": "success",
                            "args": {"url": "https://example.com"},
                            "result": {"title": "Example Inc", "text": "Financial services..."},
                        }
                    ]
                },
            },
            {"role": "user", "content": "What about their app market?"},
        ]
        history = build_message_history(messages)
        assert history is not None
        # user prompt, model response (ToolCallPart + TextPart), tool return request
        assert len(history) == 3

        # First: user prompt
        assert isinstance(history[0], ModelRequest)
        assert history[0].parts[0].content == "Browse example.com"

        # Second: model response with tool call + text
        assert isinstance(history[1], ModelResponse)
        tool_call_parts = [p for p in history[1].parts if isinstance(p, ToolCallPart)]
        text_parts = [p for p in history[1].parts if isinstance(p, AiTextPart)]
        assert len(tool_call_parts) == 1
        assert tool_call_parts[0].tool_name == "browser_navigate"
        assert tool_call_parts[0].tool_call_id == "tc-abc123"
        assert len(text_parts) == 1
        assert "example.com" in text_parts[0].content

        # Third: tool return in a ModelRequest
        assert isinstance(history[2], ModelRequest)
        return_parts = [p for p in history[2].parts if isinstance(p, ToolReturnPart)]
        assert len(return_parts) == 1
        assert return_parts[0].tool_name == "browser_navigate"
        assert return_parts[0].tool_call_id == "tc-abc123"
        assert "Example Inc" in return_parts[0].content


class TestBuildMessageHistoryMultipleToolCalls:
    """Multiple tool calls in one turn are all reconstructed."""

    def test_multiple_tool_calls(self):
        messages = [
            {"role": "user", "content": "Search for AI news and browse openai.com"},
            {
                "role": "assistant",
                "content": "Found some results.",
                "metadata": {
                    "parts": [
                        {
                            "type": "tool_call",
                            "id": "tc-1",
                            "toolName": "web_search",
                            "status": "success",
                            "args": {"query": "AI news"},
                            "result": "Top results: ...",
                        },
                        {
                            "type": "tool_call",
                            "id": "tc-2",
                            "toolName": "browser_navigate",
                            "status": "success",
                            "args": {"url": "https://openai.com"},
                            "result": {"title": "OpenAI", "text": "..."},
                        },
                    ]
                },
            },
            {"role": "user", "content": "Summarize both"},
        ]
        history = build_message_history(messages)
        assert history is not None

        # ModelResponse should have 2 ToolCallParts + 1 TextPart
        response = history[1]
        assert isinstance(response, ModelResponse)
        tool_calls = [p for p in response.parts if isinstance(p, ToolCallPart)]
        assert len(tool_calls) == 2
        assert tool_calls[0].tool_name == "web_search"
        assert tool_calls[1].tool_name == "browser_navigate"

        # ModelRequest should have 2 ToolReturnParts
        request = history[2]
        assert isinstance(request, ModelRequest)
        returns = [p for p in request.parts if isinstance(p, ToolReturnPart)]
        assert len(returns) == 2


class TestBuildMessageHistoryFailedToolCall:
    """Failed tool call reconstructs error message in ToolReturnPart."""

    def test_error_tool_call(self):
        messages = [
            {"role": "user", "content": "Execute some code"},
            {
                "role": "assistant",
                "content": "The code failed.",
                "metadata": {
                    "parts": [
                        {
                            "type": "tool_call",
                            "id": "tc-err",
                            "toolName": "execute_python",
                            "status": "error",
                            "args": {"code": "1/0"},
                            "result": None,
                            "error": "ZeroDivisionError: division by zero",
                        }
                    ]
                },
            },
            {"role": "user", "content": "Fix it"},
        ]
        history = build_message_history(messages)
        assert history is not None

        # Tool return should contain the error
        request = history[2]
        returns = [p for p in request.parts if isinstance(p, ToolReturnPart)]
        assert len(returns) == 1
        assert "[Error: ZeroDivisionError: division by zero]" in returns[0].content


class TestBuildMessageHistoryNullResult:
    """Null result (large output dropped) produces informative placeholder."""

    def test_null_result_placeholder(self):
        messages = [
            {"role": "user", "content": "Browse a big page"},
            {
                "role": "assistant",
                "content": "Here's a summary of the page...",
                "metadata": {
                    "parts": [
                        {
                            "type": "tool_call",
                            "id": "tc-big",
                            "toolName": "browser_navigate",
                            "status": "success",
                            "args": {"url": "https://example.com"},
                            "result": None,  # Too large to store
                        }
                    ]
                },
            },
            {"role": "user", "content": "What did it say?"},
        ]
        history = build_message_history(messages)
        assert history is not None

        request = history[2]
        returns = [p for p in request.parts if isinstance(p, ToolReturnPart)]
        assert len(returns) == 1
        assert "too large to store" in returns[0].content.lower()


class TestBuildMessageHistoryMixedTurns:
    """Mixed sequence: text → tool → text validates full structure."""

    def test_mixed_turns(self):
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi! How can I help?"},
            {"role": "user", "content": "Browse example.com"},
            {
                "role": "assistant",
                "content": "Here's what I found.",
                "metadata": {
                    "parts": [
                        {
                            "type": "tool_call",
                            "id": "tc-mix",
                            "toolName": "browser_navigate",
                            "status": "success",
                            "args": {"url": "https://example.com"},
                            "result": "Example Domain page content",
                        }
                    ]
                },
            },
            {"role": "user", "content": "Thanks, now summarize"},
            {"role": "assistant", "content": "The page contains example content."},
            {"role": "user", "content": "One more question"},
        ]
        history = build_message_history(messages)
        assert history is not None

        # Expected sequence:
        # 0: ModelRequest (user: Hello)
        # 1: ModelResponse (text: Hi!)
        # 2: ModelRequest (user: Browse)
        # 3: ModelResponse (ToolCallPart + TextPart)
        # 4: ModelRequest (ToolReturnPart)
        # 5: ModelRequest (user: Thanks)
        # 6: ModelResponse (text: The page...)
        assert len(history) == 7

        # Verify text-only turns
        assert isinstance(history[0], ModelRequest)
        assert isinstance(history[1], ModelResponse)
        assert isinstance(history[1].parts[0], AiTextPart)

        # Verify tool turn
        assert isinstance(history[3], ModelResponse)
        assert any(isinstance(p, ToolCallPart) for p in history[3].parts)
        assert isinstance(history[4], ModelRequest)
        assert any(isinstance(p, ToolReturnPart) for p in history[4].parts)

        # Verify post-tool text turn
        assert isinstance(history[5], ModelRequest)
        assert isinstance(history[6], ModelResponse)
        assert isinstance(history[6].parts[0], AiTextPart)


class TestBuildMessageHistoryEdgeCases:
    """Edge cases: missing id, pending status, include_last flag."""

    def test_synthetic_tool_call_id_when_missing(self):
        """Missing tool_call_id gets synthetic ID instead of empty string."""
        messages = [
            {"role": "user", "content": "Do something"},
            {
                "role": "assistant",
                "content": "Done.",
                "metadata": {
                    "parts": [
                        {
                            "type": "tool_call",
                            "toolName": "web_search",
                            "status": "success",
                            "args": {"query": "test"},
                            "result": "results",
                        }
                    ]
                },
            },
            {"role": "user", "content": "Next"},
        ]
        history = build_message_history(messages)
        assert history is not None

        response = history[1]
        tc = [p for p in response.parts if isinstance(p, ToolCallPart)][0]
        assert tc.tool_call_id.startswith("tc-reconstructed-")
        assert tc.tool_call_id  # Not empty

    def test_pending_tool_calls_ignored(self):
        """Tool calls with status 'pending' or 'running' are not reconstructed."""
        messages = [
            {"role": "user", "content": "Do something"},
            {
                "role": "assistant",
                "content": "Working on it...",
                "metadata": {
                    "parts": [
                        {
                            "type": "tool_call",
                            "id": "tc-pending",
                            "toolName": "web_search",
                            "status": "running",
                            "args": {"query": "test"},
                        }
                    ]
                },
            },
            {"role": "user", "content": "Next"},
        ]
        history = build_message_history(messages)
        assert history is not None
        # Should be text-only since tool call is still running
        assert len(history) == 2
        assert isinstance(history[1], ModelResponse)
        assert isinstance(history[1].parts[0], AiTextPart)

    def test_include_last_flag(self):
        """include_last=True includes all messages (for DB-loaded history)."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
            {"role": "user", "content": "How are you?"},
        ]
        history = build_message_history(messages, include_last=True)
        assert history is not None
        assert len(history) == 3  # All messages included

    def test_backward_compatible_no_metadata(self):
        """Messages without metadata work exactly as before."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "Bye"},
        ]
        history = build_message_history(messages)
        assert history is not None
        assert len(history) == 2
        assert isinstance(history[0], ModelRequest)
        assert isinstance(history[1], ModelResponse)
