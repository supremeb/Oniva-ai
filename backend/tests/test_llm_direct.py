"""Unit tests for the direct provider layer (no LiteLLM).

Covers the request body each provider receives, the normalized response,
streaming chunk sequences and embedding batching.
"""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import llm_direct
from app.services.llm_direct import (
    DirectProvider,
    DirectRequest,
    DirectResponse,
    DirectStreamChunk,
    DirectToolCall,
)


class _AsyncIter:
    """Minimal async iterator over a list, standing in for an SDK stream."""

    def __init__(self, items):
        self._items = list(items)

    def __aiter__(self):
        self._iter = iter(self._items)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


def _openai_client(**overrides):
    client = MagicMock()
    client.chat.completions.create = AsyncMock(**overrides)
    return client


def _anthropic_client(**overrides):
    client = MagicMock()
    client.messages.create = AsyncMock(**overrides)
    return client


def _openai_response(content="hello", tool_calls=None, cached_tokens=None):
    usage = SimpleNamespace(prompt_tokens=5, completion_tokens=7)
    usage.prompt_tokens_details = (
        SimpleNamespace(cached_tokens=cached_tokens) if cached_tokens is not None else None
    )
    return SimpleNamespace(
        model="gpt-4o-mini",
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))],
        usage=usage,
    )


class TestRequestBodies:
    """The SDK call each provider receives matches that provider's API."""

    @pytest.mark.asyncio
    async def test_openai_request_body(self):
        client = _openai_client(return_value=_openai_response())
        provider = DirectProvider(provider_type="openai", api_key="sk-test")
        request = DirectRequest(
            messages=[
                {"role": "system", "content": "be brief"},
                {"role": "user", "content": "hi"},
            ],
            model="openai/gpt-4o-mini",
            temperature=0.2,
            max_tokens=64,
            tools=[{"type": "function", "function": {"name": "search", "parameters": {}}}],
            tool_choice="auto",
        )

        with patch.object(llm_direct, "AsyncOpenAI", return_value=client):
            await llm_direct.complete(request, provider)

        kwargs = client.chat.completions.create.call_args.kwargs
        assert kwargs["model"] == "gpt-4o-mini"
        # OpenAI keeps the system message inline.
        assert kwargs["messages"] == request.messages
        assert kwargs["temperature"] == 0.2
        assert kwargs["max_tokens"] == 64
        assert kwargs["tools"] == request.tools
        assert kwargs["tool_choice"] == "auto"

    @pytest.mark.asyncio
    async def test_anthropic_request_body(self):
        client = _anthropic_client(return_value=SimpleNamespace(
            model="claude-3-haiku-20240307",
            content=[SimpleNamespace(type="text", text="hi")],
            usage=SimpleNamespace(
                input_tokens=3, output_tokens=1,
                cache_read_input_tokens=0, cache_creation_input_tokens=0,
            ),
        ))
        provider = DirectProvider(provider_type="anthropic", api_key="sk-ant")
        request = DirectRequest(
            messages=[
                {"role": "system", "content": "be brief"},
                {"role": "user", "content": "hi"},
            ],
            model="anthropic/claude-3-haiku-20240307",
            temperature=0.0,
            max_tokens=32,
            tools=[{"type": "function", "function": {
                "name": "search", "description": "find", "parameters": {"type": "object"},
            }}],
            tool_choice="auto",
        )

        with patch.object(llm_direct, "AsyncAnthropic", return_value=client):
            await llm_direct.complete(request, provider)

        kwargs = client.messages.create.call_args.kwargs
        assert kwargs["model"] == "claude-3-haiku-20240307"
        # Anthropic takes the system prompt out of the messages array.
        assert kwargs["system"] == "be brief"
        assert kwargs["messages"] == [{"role": "user", "content": "hi"}]
        assert kwargs["max_tokens"] == 32
        assert kwargs["tools"][0]["input_schema"] == {"type": "object"}
        assert kwargs["tools"][0]["name"] == "search"
        assert kwargs["tool_choice"] == {"type": "auto"}

    @pytest.mark.asyncio
    async def test_anthropic_system_blocks_passed_through(self):
        client = _anthropic_client(return_value=SimpleNamespace(
            model="claude-3-haiku-20240307",
            content=[SimpleNamespace(type="text", text="hi")],
            usage=SimpleNamespace(
                input_tokens=1, output_tokens=1,
                cache_read_input_tokens=0, cache_creation_input_tokens=0,
            ),
        ))
        provider = DirectProvider(provider_type="anthropic", api_key="sk-ant")
        system = [{"type": "text", "text": "cached", "cache_control": {"type": "ephemeral"}}]
        request = DirectRequest(
            messages=[{"role": "user", "content": "hi"}],
            model="claude-3-haiku-20240307",
            system=system,
        )

        with patch.object(llm_direct, "AsyncAnthropic", return_value=client):
            await llm_direct.complete(request, provider)

        assert client.messages.create.call_args.kwargs["system"] == system

    @pytest.mark.asyncio
    async def test_openrouter_uses_base_url_and_slug(self):
        client = _openai_client(return_value=_openai_response())
        provider = DirectProvider(provider_type="openrouter", api_key="sk-or")
        request = DirectRequest(messages=[{"role": "user", "content": "hi"}], model="openai/gpt-4o")

        with patch.object(llm_direct, "AsyncOpenAI", return_value=client) as mock_cls:
            await llm_direct.complete(request, provider)

        assert mock_cls.call_args.kwargs["base_url"] == llm_direct.settings.OPENROUTER_BASE_URL
        assert client.chat.completions.create.call_args.kwargs["model"] == "openai/gpt-4o"

    @pytest.mark.asyncio
    async def test_custom_provider_honours_api_base(self):
        client = _openai_client(return_value=_openai_response())
        provider = DirectProvider(
            provider_type="custom", api_key="token", api_base="http://localhost:8001/v1",
        )
        request = DirectRequest(messages=[{"role": "user", "content": "hi"}], model="llama-3.1-70b")

        with patch.object(llm_direct, "AsyncOpenAI", return_value=client) as mock_cls:
            await llm_direct.complete(request, provider)

        assert mock_cls.call_args.kwargs["base_url"] == "http://localhost:8001/v1"
        assert client.chat.completions.create.call_args.kwargs["model"] == "llama-3.1-70b"


class TestCompleteResponse:
    @pytest.mark.asyncio
    async def test_openai_normalizes_usage_and_cache(self):
        client = _openai_client(return_value=_openai_response(content="yo", cached_tokens=4))
        provider = DirectProvider(provider_type="openai", api_key="sk-test")
        request = DirectRequest(messages=[{"role": "user", "content": "hi"}], model="gpt-4o-mini")

        with patch.object(llm_direct, "AsyncOpenAI", return_value=client):
            response = await llm_direct.complete(request, provider)

        assert isinstance(response, DirectResponse)
        assert response.content == "yo"
        assert response.input_tokens == 5
        assert response.output_tokens == 7
        assert response.cache_read_tokens == 4

    @pytest.mark.asyncio
    async def test_openai_tool_calls(self):
        tool_call = SimpleNamespace(id="call_1", function=SimpleNamespace(
            name="search", arguments='{"q": "x"}',
        ))
        client = _openai_client(return_value=_openai_response(content=None, tool_calls=[tool_call]))
        provider = DirectProvider(provider_type="openai", api_key="sk-test")
        request = DirectRequest(messages=[{"role": "user", "content": "hi"}], model="gpt-4o-mini")

        with patch.object(llm_direct, "AsyncOpenAI", return_value=client):
            response = await llm_direct.complete(request, provider)

        assert response.tool_calls == [DirectToolCall(id="call_1", name="search", arguments='{"q": "x"}')]

    @pytest.mark.asyncio
    async def test_anthropic_tool_use_becomes_tool_call(self):
        client = _anthropic_client(return_value=SimpleNamespace(
            model="claude-3-haiku-20240307",
            content=[
                SimpleNamespace(type="text", text="thinking"),
                SimpleNamespace(type="tool_use", id="tu_1", name="search", input={"q": "x"}),
            ],
            usage=SimpleNamespace(
                input_tokens=2, output_tokens=3,
                cache_read_input_tokens=1, cache_creation_input_tokens=2,
            ),
        ))
        provider = DirectProvider(provider_type="anthropic", api_key="sk-ant")
        request = DirectRequest(messages=[{"role": "user", "content": "hi"}], model="claude-3-haiku-20240307")

        with patch.object(llm_direct, "AsyncAnthropic", return_value=client):
            response = await llm_direct.complete(request, provider)

        assert response.content == "thinking"
        assert response.tool_calls == [DirectToolCall(id="tu_1", name="search", arguments='{"q": "x"}')]
        assert response.cache_read_tokens == 1
        assert response.cache_creation_tokens == 2


class TestStreaming:
    @pytest.mark.asyncio
    async def test_openai_stream_yields_chunks(self):
        events = [
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="Hel"), finish_reason=None)]),
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="lo"), finish_reason=None)]),
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None), finish_reason="stop")]),
        ]
        client = _openai_client(return_value=_AsyncIter(events))
        provider = DirectProvider(provider_type="openai", api_key="sk-test")
        request = DirectRequest(messages=[{"role": "user", "content": "hi"}], model="gpt-4o-mini")

        with patch.object(llm_direct, "AsyncOpenAI", return_value=client):
            chunks = [c async for c in llm_direct.stream(request, provider)]

        assert chunks == [
            DirectStreamChunk(content="Hel"),
            DirectStreamChunk(content="lo"),
            DirectStreamChunk(content="", finish_reason="stop"),
        ]

    @pytest.mark.asyncio
    async def test_anthropic_stream_maps_events(self):
        events = [
            SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text="Hel")),
            SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text="lo")),
            SimpleNamespace(type="message_delta", delta=SimpleNamespace(stop_reason="end_turn")),
        ]
        client = _anthropic_client(return_value=_AsyncIter(events))
        provider = DirectProvider(provider_type="anthropic", api_key="sk-ant")
        request = DirectRequest(messages=[{"role": "user", "content": "hi"}], model="claude-3-haiku-20240307")

        with patch.object(llm_direct, "AsyncAnthropic", return_value=client):
            chunks = [c async for c in llm_direct.stream(request, provider)]

        assert chunks == [
            DirectStreamChunk(content="Hel"),
            DirectStreamChunk(content="lo"),
            DirectStreamChunk(content="", finish_reason="end_turn"),
        ]


class TestEmbed:
    @pytest.mark.asyncio
    async def test_embed_preserves_input_order(self):
        client = MagicMock()
        client.embeddings.create = AsyncMock(side_effect=[
            SimpleNamespace(data=[SimpleNamespace(index=1, embedding=[0.2]),
                                  SimpleNamespace(index=0, embedding=[0.1])]),
        ])
        provider = DirectProvider(provider_type="openai", api_key="sk-test")

        with patch.object(llm_direct, "AsyncOpenAI", return_value=client):
            vectors = await llm_direct.embed(["a", "b"], provider, model="text-embedding-3-small")

        assert vectors == [[0.1], [0.2]]

    @pytest.mark.asyncio
    async def test_embed_batches_over_provider_limit(self):
        client = MagicMock()
        client.embeddings.create = AsyncMock(side_effect=[
            SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[0.0])]),
            SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[1.0])]),
        ])
        provider = DirectProvider(provider_type="openai", api_key="sk-test")

        with patch.object(llm_direct, "AsyncOpenAI", return_value=client), \
                patch.object(llm_direct, "EMBED_BATCH_SIZE", 1):
            vectors = await llm_direct.embed(["a", "b"], provider, model="text-embedding-3-small")

        assert vectors == [[0.0], [1.0]]
        assert client.embeddings.create.call_count == 2

    @pytest.mark.asyncio
    async def test_embed_rejects_anthropic(self):
        provider = DirectProvider(provider_type="anthropic", api_key="sk-ant")

        with pytest.raises(ValueError):
            await llm_direct.embed(["a"], provider, model="claude-3-haiku-20240307")


class TestContextWindow:
    def test_known_model(self):
        from app.config.llm_pricing import context_window_for

        assert context_window_for("openai/gpt-4o") == 128_000

    def test_dotted_openrouter_slug(self):
        from app.config.llm_pricing import context_window_for

        assert context_window_for("openrouter/anthropic/claude-opus-5.5") == 200_000

    def test_unknown_model_defaults(self):
        from app.config.llm_pricing import DEFAULT_CONTEXT_WINDOW, context_window_for

        assert context_window_for("something-unknown") == DEFAULT_CONTEXT_WINDOW


def _anthropic_text_response():
    return SimpleNamespace(
        model="claude-3-haiku-20240307",
        content=[SimpleNamespace(type="text", text="done")],
        usage=SimpleNamespace(
            input_tokens=1, output_tokens=1,
            cache_read_input_tokens=0, cache_creation_input_tokens=0,
        ),
    )


class TestAnthropicHistoryConversion:
    """The Fleet tool loop sends OpenAI-shaped history; Anthropic must receive its own shape."""

    @pytest.mark.asyncio
    async def test_background_agent_tool_loop_history(self):
        # Exact shape background_agent_executor builds after a tool-calling turn.
        messages = [
            {"role": "system", "content": "you are an agent"},
            {"role": "user", "content": "find x"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "tu_1", "type": "function", "function": {"name": "search", "arguments": '{"q": "x"}'}},
                {"id": "tu_2", "type": "function", "function": {"name": "fetch", "arguments": "not json"}},
            ]},
            {"role": "tool", "tool_call_id": "tu_1", "content": '{"hits": 1}'},
            {"role": "tool", "tool_call_id": "tu_2", "content": "boom"},
            {"role": "user", "content": "[Operator Intervention] hurry"},
        ]
        client = _anthropic_client(return_value=_anthropic_text_response())
        provider = DirectProvider(provider_type="anthropic", api_key="sk-ant")
        request = DirectRequest(messages=messages, model="claude-3-haiku-20240307")

        with patch.object(llm_direct, "AsyncAnthropic", return_value=client):
            await llm_direct.complete(request, provider)

        kwargs = client.messages.create.call_args.kwargs
        assert kwargs["system"] == "you are an agent"
        assert kwargs["messages"] == [
            {"role": "user", "content": "find x"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "tu_1", "name": "search", "input": {"q": "x"}},
                {"type": "tool_use", "id": "tu_2", "name": "fetch", "input": {}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tu_1", "content": '{"hits": 1}'},
                {"type": "tool_result", "tool_use_id": "tu_2", "content": "boom"},
                {"type": "text", "text": "[Operator Intervention] hurry"},
            ]},
        ]

    def test_assistant_text_kept_before_tool_use_and_empty_dropped(self):
        converted = llm_direct._to_anthropic_messages([
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": "checking", "tool_calls": [
                {"id": "a", "type": "function", "function": {"name": "t", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "a", "content": "ok"},
            {"role": "assistant", "content": ""},
            {"role": "assistant", "content": "final"},
        ])

        assert converted[1]["content"][0] == {"type": "text", "text": "checking"}
        assert converted[1]["content"][1]["type"] == "tool_use"
        # The empty assistant turn disappears; the next assistant message follows the tool result.
        assert [m["role"] for m in converted] == ["user", "assistant", "user", "assistant"]
        assert converted[-1]["content"] == "final"

    def test_consecutive_user_messages_merge(self):
        converted = llm_direct._to_anthropic_messages([
            {"role": "user", "content": "a"},
            {"role": "user", "content": "b"},
        ])

        assert converted == [{"role": "user", "content": [
            {"type": "text", "text": "a"}, {"type": "text", "text": "b"},
        ]}]

    @pytest.mark.asyncio
    async def test_anthropic_tool_use_round_trips_to_json_arguments(self):
        client = _anthropic_client(return_value=SimpleNamespace(
            model="claude-3-haiku-20240307",
            content=[SimpleNamespace(type="tool_use", id="tu_9", name="search", input={"q": "é"})],
            usage=SimpleNamespace(
                input_tokens=1, output_tokens=1,
                cache_read_input_tokens=0, cache_creation_input_tokens=0,
            ),
        ))
        provider = DirectProvider(provider_type="anthropic", api_key="sk-ant")
        request = DirectRequest(messages=[{"role": "user", "content": "hi"}], model="claude-3-haiku-20240307")

        with patch.object(llm_direct, "AsyncAnthropic", return_value=client):
            response = await llm_direct.complete(request, provider)

        assert response.content == ""
        assert json.loads(response.tool_calls[0].arguments) == {"q": "é"}


class TestOpenAIReasoning:
    def _provider(self):
        return DirectProvider(provider_type="openrouter", api_key="sk-or")

    @pytest.mark.asyncio
    async def test_empty_content_falls_back_to_reasoning_content(self):
        resp = _openai_response(content=None)
        resp.choices[0].message.reasoning_content = "the answer is 4"
        client = _openai_client(return_value=resp)

        with patch.object(llm_direct, "AsyncOpenAI", return_value=client):
            response = await llm_direct.complete(
                DirectRequest(messages=[{"role": "user", "content": "2+2"}], model="deepseek/deepseek-r1"),
                self._provider(),
            )

        assert response.content == "the answer is 4"
        assert response.reasoning == "the answer is 4"

    @pytest.mark.asyncio
    async def test_reasoning_from_model_extra_does_not_replace_content(self):
        resp = _openai_response(content="4")
        resp.choices[0].message.model_extra = {"reasoning": "added them"}
        client = _openai_client(return_value=resp)

        with patch.object(llm_direct, "AsyncOpenAI", return_value=client):
            response = await llm_direct.complete(
                DirectRequest(messages=[{"role": "user", "content": "2+2"}], model="deepseek/deepseek-r1"),
                self._provider(),
            )

        assert response.content == "4"
        assert response.reasoning == "added them"

    @pytest.mark.asyncio
    async def test_tool_call_turn_does_not_use_reasoning_as_content(self):
        tool_call = SimpleNamespace(id="c", function=SimpleNamespace(name="t", arguments="{}"))
        resp = _openai_response(content=None, tool_calls=[tool_call])
        resp.choices[0].message.reasoning_content = "thinking"
        client = _openai_client(return_value=resp)

        with patch.object(llm_direct, "AsyncOpenAI", return_value=client):
            response = await llm_direct.complete(
                DirectRequest(messages=[{"role": "user", "content": "x"}], model="deepseek/deepseek-r1"),
                self._provider(),
            )

        assert response.content == ""
        assert response.tool_calls and response.reasoning == "thinking"


class TestAzureClient:
    def test_classic_azure_resource_uses_azure_client(self):
        provider = DirectProvider(
            provider_type="azure", api_key="k", api_base="https://res.openai.azure.com",
        )

        with patch.object(llm_direct, "AsyncAzureOpenAI") as azure_cls:
            llm_direct._openai_client(provider)

        kwargs = azure_cls.call_args.kwargs
        assert kwargs["azure_endpoint"] == "https://res.openai.azure.com"
        assert kwargs["api_version"] == llm_direct.AZURE_API_VERSION

    def test_azure_v1_endpoint_uses_plain_client(self):
        provider = DirectProvider(
            provider_type="azure", api_key="k", api_base="https://res.openai.azure.com/openai/v1/",
        )

        with patch.object(llm_direct, "AsyncOpenAI") as openai_cls:
            llm_direct._openai_client(provider)

        assert openai_cls.call_args.kwargs["base_url"] == provider.api_base
