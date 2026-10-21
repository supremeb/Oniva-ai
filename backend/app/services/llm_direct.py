"""
Direct provider layer — native provider SDK calls (OpenAI, Anthropic, Azure, OpenRouter).

`openai`, `openrouter`, `azure` and `custom` providers go through `AsyncOpenAI`
with an optional `base_url`; `anthropic` goes through `AsyncAnthropic`. The
request each provider receives is exactly what this module builds, so no routing
library sits between the gateway and the vendor API.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any, Optional

from anthropic import AsyncAnthropic
from openai import AsyncAzureOpenAI, AsyncOpenAI

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# OpenAI-compatible embeddings endpoints reject oversized input arrays; batch under this.
EMBED_BATCH_SIZE = 100

# Anthropic requires max_tokens; fall back to this when the caller leaves it open.
DEFAULT_MAX_TOKENS = 4096

# A model id may carry a routing prefix ("openai/gpt-4o"); only these prefixes are stripped.
_KNOWN_PROVIDER_PREFIXES = frozenset(
    {"openai", "anthropic", "openrouter", "azure", "google", "custom"}
)

# Provider config carries no api_version; classic Azure deployments use this GA version.
AZURE_API_VERSION = "2024-10-21"

# OpenAI tool_choice strings map onto Anthropic's object form.
_ANTHROPIC_TOOL_CHOICE: dict[str, dict[str, str]] = {
    "auto": {"type": "auto"},
    "required": {"type": "any"},
    "none": {"type": "none"},
}


@dataclass
class DirectProvider:
    """The subset of provider configuration the direct layer needs."""

    provider_type: str
    api_key: str
    api_base: Optional[str] = None


@dataclass
class DirectToolCall:
    """A tool call in OpenAI's `{id, name, arguments}` shape (arguments as JSON text)."""

    id: str
    name: str
    arguments: str


@dataclass
class DirectRequest:
    """A completion or stream request, provider-agnostic."""

    messages: list[dict[str, Any]]
    model: str
    temperature: float = 0.7
    max_tokens: int | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | None = None
    # Anthropic system prompt (string or cache-marked block list); extracted from
    # messages when None.
    system: Any = None


@dataclass
class DirectResponse:
    """A normalized completion response."""

    content: str
    model: str
    input_tokens: int
    output_tokens: int
    tool_calls: list[DirectToolCall] | None = None
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    # Reasoning/thinking text from models that expose it (DeepSeek, Kimi, QwQ via OpenRouter).
    reasoning: str | None = None


@dataclass
class DirectStreamChunk:
    """One streamed delta; a chunk with a finish_reason carries no content."""

    content: str
    finish_reason: str | None = None


def resolve_model(model: str, provider_type: str) -> str:
    """The bare model id a provider adapter expects (routing prefixes and OpenRouter slugs removed)."""
    if provider_type == "openrouter":
        # Imported lazily: llm_gateway imports this module at its own import time.
        from app.services.llm_gateway import to_openrouter_model_id

        return to_openrouter_model_id(model)
    prefix, sep, rest = model.partition("/")
    if sep and prefix in _KNOWN_PROVIDER_PREFIXES:
        return rest
    return model


def _split_system(messages: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
    """Pull system messages out of the array (Anthropic takes them as a top-level param)."""
    parts: list[str] = []
    rest: list[dict[str, Any]] = []
    for message in messages:
        if message.get("role") == "system":
            content = message.get("content", "")
            if isinstance(content, str):
                parts.append(content)
        else:
            rest.append(message)
    return ("\n".join(parts) if parts else None), rest


def _text_blocks(content: Any) -> list[dict[str, Any]]:
    """Normalize string or block-list content to a list of content blocks (empty text dropped)."""
    if content is None:
        return []
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content.strip() else []
    return [
        block for block in content
        if not (isinstance(block, dict) and block.get("type") == "text" and not str(block.get("text", "")).strip())
    ]


def _parse_tool_arguments(arguments: Any) -> dict[str, Any]:
    """Tool-call arguments as the dict Anthropic's `tool_use.input` needs; bad JSON becomes `{}`."""
    if isinstance(arguments, dict):
        return arguments
    try:
        parsed = json.loads(arguments) if arguments else {}
    except (TypeError, ValueError):
        logger.warning("Unparseable tool call arguments; sending empty input")
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _to_anthropic_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert OpenAI-shaped history to the Anthropic Messages shape.

    Assistant `tool_calls` become `tool_use` blocks, `role: "tool"` messages become `tool_result`
    blocks inside a user message, empty text blocks are dropped and consecutive same-role messages
    are merged (the API requires alternating roles).
    """
    converted: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        if role == "tool":
            blocks: list[dict[str, Any]] = [{
                "type": "tool_result",
                "tool_use_id": message.get("tool_call_id", ""),
                "content": message.get("content") or "",
            }]
            role = "user"
        elif role == "assistant":
            blocks = _text_blocks(message.get("content"))
            for call in message.get("tool_calls") or []:
                function = call.get("function", {})
                blocks.append({
                    "type": "tool_use",
                    "id": call.get("id", ""),
                    "name": function.get("name", ""),
                    "input": _parse_tool_arguments(function.get("arguments")),
                })
        else:
            role = "user"
            blocks = _text_blocks(message.get("content"))
        if not blocks:
            continue
        if converted and converted[-1]["role"] == role:
            converted[-1]["content"].extend(blocks)
        else:
            converted.append({"role": role, "content": blocks})

    # A lone text block stays a plain string, matching what callers send for simple turns.
    for message in converted:
        content = message["content"]
        if len(content) == 1 and content[0]["type"] == "text" and len(content[0]) == 2:
            message["content"] = content[0]["text"]
    return converted


def _to_anthropic_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """Convert OpenAI function tools to Anthropic's `{name, description, input_schema}` form."""
    if not tools:
        return None
    converted: list[dict[str, Any]] = []
    for tool in tools:
        if "input_schema" in tool:
            converted.append(tool)
            continue
        function = tool.get("function", tool)
        native = {
            "name": function["name"],
            "description": function.get("description", ""),
            "input_schema": function.get("parameters") or {"type": "object", "properties": {}},
        }
        if "cache_control" in tool:
            native["cache_control"] = tool["cache_control"]
        converted.append(native)
    return converted


def _to_anthropic_tool_choice(tool_choice: str | None) -> dict[str, str] | None:
    if not tool_choice:
        return None
    return _ANTHROPIC_TOOL_CHOICE.get(tool_choice, {"type": "auto"})


def _is_classic_azure(provider: DirectProvider) -> bool:
    """Classic Azure OpenAI resource URL (model = deployment name), not a `/openai/v1` endpoint."""
    return (
        provider.provider_type == "azure"
        and bool(provider.api_base)
        and "/openai/v1" not in (provider.api_base or "")
    )


def _openai_client(provider: DirectProvider) -> AsyncOpenAI:
    if _is_classic_azure(provider):
        return AsyncAzureOpenAI(
            api_key=provider.api_key,
            azure_endpoint=provider.api_base,
            api_version=AZURE_API_VERSION,
        )
    kwargs: dict[str, Any] = {"api_key": provider.api_key}
    if provider.provider_type == "openrouter":
        kwargs["base_url"] = provider.api_base or settings.OPENROUTER_BASE_URL
    elif provider.api_base:
        kwargs["base_url"] = provider.api_base
    return AsyncOpenAI(**kwargs)


def _anthropic_client(provider: DirectProvider) -> AsyncAnthropic:
    kwargs: dict[str, Any] = {"api_key": provider.api_key}
    if provider.api_base:
        kwargs["base_url"] = provider.api_base
    return AsyncAnthropic(**kwargs)


def _openai_params(request: DirectRequest, provider: DirectProvider) -> dict[str, Any]:
    params: dict[str, Any] = {
        "model": resolve_model(request.model, provider.provider_type),
        "messages": request.messages,
        "temperature": request.temperature,
    }
    if request.max_tokens:
        params["max_tokens"] = request.max_tokens
    if request.tools:
        params["tools"] = request.tools
    if request.tool_choice:
        params["tool_choice"] = request.tool_choice
    return params


def _anthropic_params(request: DirectRequest) -> dict[str, Any]:
    system = request.system
    messages = request.messages
    if system is None:
        system, messages = _split_system(messages)
    params: dict[str, Any] = {
        "model": resolve_model(request.model, "anthropic"),
        "messages": _to_anthropic_messages(messages),
        "temperature": request.temperature,
        "max_tokens": request.max_tokens or DEFAULT_MAX_TOKENS,
    }
    if system:
        params["system"] = system
    tools = _to_anthropic_tools(request.tools)
    if tools:
        params["tools"] = tools
    tool_choice = _to_anthropic_tool_choice(request.tool_choice)
    if tool_choice:
        params["tool_choice"] = tool_choice
    return params


async def complete(request: DirectRequest, provider: DirectProvider) -> DirectResponse:
    """Run one completion and normalize the reply."""
    if provider.provider_type == "anthropic":
        return await _complete_anthropic(request, provider)
    return await _complete_openai(request, provider)


async def _complete_openai(request: DirectRequest, provider: DirectProvider) -> DirectResponse:
    client = _openai_client(provider)
    response = await client.chat.completions.create(**_openai_params(request, provider))

    usage = getattr(response, "usage", None)
    cache_read = 0
    details = getattr(usage, "prompt_tokens_details", None)
    if details is not None:
        cache_read = getattr(details, "cached_tokens", 0) or 0

    message = response.choices[0].message
    raw_tool_calls = message.tool_calls
    tool_calls = [
        DirectToolCall(id=tc.id, name=tc.function.name, arguments=tc.function.arguments)
        for tc in raw_tool_calls
    ] if raw_tool_calls else None

    reasoning = _openai_reasoning(message)
    # Reasoning models (kimi, DeepSeek R1, QwQ) may put the answer in the reasoning field only.
    content = message.content or ("" if tool_calls else reasoning or "")

    return DirectResponse(
        content=content,
        model=response.model,
        input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        tool_calls=tool_calls,
        cache_read_tokens=cache_read,
        reasoning=reasoning,
    )


def _openai_reasoning(message: Any) -> str | None:
    """Reasoning text an OpenAI-compatible reply carries (`reasoning_content`, or OpenRouter's `reasoning`)."""
    extra = getattr(message, "model_extra", None) or {}
    for name in ("reasoning_content", "reasoning"):
        value = getattr(message, name, None) or extra.get(name)
        if isinstance(value, str) and value:
            return value
    return None


async def _complete_anthropic(request: DirectRequest, provider: DirectProvider) -> DirectResponse:
    client = _anthropic_client(provider)
    response = await client.messages.create(**_anthropic_params(request))

    text_parts: list[str] = []
    thinking_parts: list[str] = []
    tool_calls: list[DirectToolCall] = []
    for block in response.content:
        block_type = getattr(block, "type", None)
        if block_type == "thinking":
            thinking_parts.append(getattr(block, "thinking", "") or "")
        elif block_type == "text":
            text_parts.append(getattr(block, "text", ""))
        elif block_type == "tool_use":
            tool_calls.append(DirectToolCall(
                id=block.id,
                name=block.name,
                arguments=json.dumps(getattr(block, "input", {})),
            ))

    usage = response.usage
    return DirectResponse(
        content="".join(text_parts),
        model=response.model,
        input_tokens=getattr(usage, "input_tokens", 0) or 0,
        output_tokens=getattr(usage, "output_tokens", 0) or 0,
        tool_calls=tool_calls or None,
        cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        cache_creation_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        reasoning="".join(thinking_parts) or None,
    )


async def stream(request: DirectRequest, provider: DirectProvider) -> AsyncGenerator[DirectStreamChunk, None]:
    """Stream one completion, yielding normalized deltas."""
    if provider.provider_type == "anthropic":
        async for chunk in _stream_anthropic(request, provider):
            yield chunk
    else:
        async for chunk in _stream_openai(request, provider):
            yield chunk


async def _stream_openai(
    request: DirectRequest, provider: DirectProvider
) -> AsyncGenerator[DirectStreamChunk, None]:
    client = _openai_client(provider)
    params = _openai_params(request, provider)
    params["stream"] = True
    response = await client.chat.completions.create(**params)
    async for chunk in response:
        choices = getattr(chunk, "choices", None)
        if not choices:
            continue
        choice = choices[0]
        delta = getattr(choice, "delta", None)
        content = getattr(delta, "content", None) if delta is not None else None
        if content:
            yield DirectStreamChunk(content=content)
        if getattr(choice, "finish_reason", None):
            yield DirectStreamChunk(content="", finish_reason=choice.finish_reason)


async def _stream_anthropic(
    request: DirectRequest, provider: DirectProvider
) -> AsyncGenerator[DirectStreamChunk, None]:
    client = _anthropic_client(provider)
    params = _anthropic_params(request)
    params["stream"] = True
    response = await client.messages.create(**params)
    async for event in response:
        event_type = getattr(event, "type", None)
        if event_type == "content_block_delta":
            delta = getattr(event, "delta", None)
            text = getattr(delta, "text", None) if delta is not None else None
            if getattr(delta, "type", None) == "text_delta" and text:
                yield DirectStreamChunk(content=text)
        elif event_type == "message_delta":
            delta = getattr(event, "delta", None)
            stop_reason = getattr(delta, "stop_reason", None) if delta is not None else None
            if stop_reason:
                yield DirectStreamChunk(content="", finish_reason=stop_reason)


async def embed(
    texts: list[str],
    provider: DirectProvider,
    model: str,
) -> list[list[float]]:
    """Embed `texts` in input order, batching over the provider's per-request limit."""
    if provider.provider_type == "anthropic":
        raise ValueError("Anthropic does not provide an embeddings API")

    client = _openai_client(provider)
    resolved_model = resolve_model(model, provider.provider_type)
    vectors: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = list(texts[start:start + EMBED_BATCH_SIZE])
        response = await client.embeddings.create(model=resolved_model, input=batch)
        # Some providers return batches out of order; index is the source of truth.
        vectors.extend(item.embedding for item in sorted(response.data, key=lambda d: d.index))
    return vectors
