import { describe, expect, it } from "vitest";
import { Context } from "../../src/core/context.js";
import { AnthropicProvider } from "../../src/models/anthropic.js";
import { ModelRegistry } from "../../src/models/stream.js";
import type { ModelCall, StreamEvent, TerminalEvent } from "../../src/models/types.js";
import {
  MAX_TOKENS_FIXTURE,
  MID_STREAM_ERROR_FIXTURE,
  PROMPT_TOO_LONG_BODY,
  RATE_LIMIT_BODY,
  STALL_PREFIX,
  TEXT_FIXTURE,
  THINKING_FIXTURE,
  TOOL_USE_FIXTURE,
  WINDOW_EXCEEDED_FIXTURE,
} from "./fixtures/anthropic-sse.js";

interface Captured {
  url: string;
  headers: Headers;
  body: Record<string, unknown>;
}

type FetchLike = (url: string | URL | Request, init?: RequestInit) => Promise<Response>;

function sseResponse(body: string): Response {
  return new Response(body, { status: 200, headers: { "content-type": "text/event-stream" } });
}

function fakeFetch(respond: (init: RequestInit) => Response | Promise<Response>): {
  fetch: FetchLike;
  calls: Captured[];
} {
  const calls: Captured[] = [];
  const fetch: FetchLike = async (url, init = {}) => {
    calls.push({
      url: String(url),
      headers: new Headers(init.headers),
      body: JSON.parse(String(init.body)) as Record<string, unknown>,
    });
    return respond(init);
  };
  return { fetch, calls };
}

function setup(respond: (init: RequestInit) => Response | Promise<Response>) {
  const { fetch, calls } = fakeFetch(respond);
  const provider = new AnthropicProvider({ fetch, maxRetries: 0 });
  const registry = new ModelRegistry().register(provider);
  return { registry, calls };
}

function call(overrides: Partial<ModelCall> = {}): ModelCall {
  return {
    provider: "anthropic",
    model: "claude-opus-5-5",
    apiKey: "sk-ant-test-key",
    maxTokens: 4096,
    messages: [{ role: "user", content: "Hi" }],
    ...overrides,
  };
}

async function run(
  registry: ModelRegistry,
  c: ModelCall,
  ctx = Context.background(),
): Promise<{ events: StreamEvent[]; terminal: TerminalEvent }> {
  const stream = registry.stream(ctx, c);
  const events: StreamEvent[] = [];
  for await (const e of stream) events.push(e);
  return { events, terminal: await stream.result() };
}

describe("AnthropicProvider SSE mapping", () => {
  it("maps a text stream to start/block/done with usage", async () => {
    const { registry, calls } = setup(() => sseResponse(TEXT_FIXTURE));
    const { events, terminal } = await run(registry, call());
    expect(events.map((e) => e.type)).toEqual([
      "start",
      "block_start",
      "block_delta",
      "block_delta",
      "block_end",
      "done",
    ]);
    expect(events[0]).toEqual({ type: "start", provider: "anthropic", model: "claude-opus-5-5" });
    expect(events[2]).toEqual({
      type: "block_delta",
      index: 0,
      delta: { type: "text", text: "Hello" },
    });
    if (terminal.type !== "done") throw new Error("expected done");
    expect(terminal.stopReason).toBe("end_turn");
    expect(terminal.message).toEqual({
      role: "assistant",
      provider: "anthropic",
      model: "claude-opus-5-5",
      stopReason: "end_turn",
      blocks: [{ type: "text", text: "Hello world" }],
    });
    expect(terminal.usage).toEqual({
      inputTokens: 25,
      outputTokens: 12,
      cacheReadTokens: 100,
      cacheWriteTokens: 5,
    });
    expect(calls).toHaveLength(1);
    expect(calls[0]?.url).toContain("/v1/messages");
    expect(calls[0]?.headers.get("x-api-key")).toBe("sk-ant-test-key");
    expect(calls[0]?.body.stream).toBe(true);
  });

  it("maps thinking (with signature), redacted thinking and text", async () => {
    const { registry } = setup(() => sseResponse(THINKING_FIXTURE));
    const { events, terminal } = await run(registry, call({ thinking: "high" }));
    const thinkingDeltas = events.filter(
      (e) => e.type === "block_delta" && e.delta.type === "thinking",
    );
    expect(thinkingDeltas).toHaveLength(2);
    if (terminal.type !== "done") throw new Error("expected done");
    expect(terminal.message.blocks).toEqual([
      { type: "thinking", thinking: "Two plus two.", signature: "sig-abc" },
      { type: "thinking", thinking: "", redacted: true, data: "opaque-blob" },
      { type: "text", text: "4" },
    ]);
  });

  it("maps tool use with streamed argument JSON", async () => {
    const { registry } = setup(() => sseResponse(TOOL_USE_FIXTURE));
    const { events, terminal } = await run(registry, call());
    const argDeltas = events.filter(
      (e) => e.type === "block_delta" && e.delta.type === "tool_args",
    );
    expect(argDeltas.length).toBeGreaterThanOrEqual(2);
    if (terminal.type !== "done") throw new Error("expected done");
    expect(terminal.stopReason).toBe("tool_use");
    expect(terminal.message.blocks).toEqual([
      { type: "text", text: "Checking the weather." },
      { type: "tool_call", id: "toolu_01", name: "get_weather", arguments: { location: "Paris" } },
    ]);
  });

  it("maps a max_tokens stop", async () => {
    const { registry } = setup(() => sseResponse(MAX_TOKENS_FIXTURE));
    const { terminal } = await run(registry, call({ model: "claude-haiku-5-5" }));
    if (terminal.type !== "done") throw new Error("expected done");
    expect(terminal.stopReason).toBe("max_tokens");
    expect(terminal.message.model).toBe("claude-haiku-5-5");
    expect(terminal.message.blocks).toEqual([{ type: "text", text: "The answer is" }]);
  });

  it("maps a context-window stop to a context_overflow error with the partial message", async () => {
    const { registry } = setup(() => sseResponse(WINDOW_EXCEEDED_FIXTURE));
    const { terminal } = await run(registry, call());
    if (terminal.type !== "error") throw new Error("expected error");
    expect(terminal.error.kind).toBe("context_overflow");
    expect(terminal.partial.blocks).toEqual([{ type: "text", text: "Long" }]);
  });
});

describe("AnthropicProvider errors", () => {
  it("classifies 'prompt is too long' as context_overflow", async () => {
    const { registry } = setup(() => Response.json(PROMPT_TOO_LONG_BODY, { status: 400 }));
    const { events, terminal } = await run(registry, call());
    expect(events).toHaveLength(1);
    if (terminal.type !== "error") throw new Error("expected error");
    expect(terminal.error).toMatchObject({
      kind: "context_overflow",
      status: 400,
      retryable: false,
    });
  });

  it("classifies 429 as retryable rate_limited", async () => {
    const { registry } = setup(() => Response.json(RATE_LIMIT_BODY, { status: 429 }));
    const { terminal } = await run(registry, call());
    expect(terminal.type === "error" && terminal.error).toMatchObject({
      kind: "rate_limited",
      status: 429,
      retryable: true,
    });
  });

  it("classifies 401 as auth without echoing the key", async () => {
    const { registry } = setup(() =>
      Response.json(
        { type: "error", error: { type: "authentication_error", message: "invalid x-api-key" } },
        { status: 401 },
      ),
    );
    const { terminal } = await run(registry, call());
    if (terminal.type !== "error") throw new Error("expected error");
    expect(terminal.error.kind).toBe("auth");
    expect(JSON.stringify(terminal)).not.toContain("sk-ant-test-key");
  });

  it("maps an in-stream overloaded error and keeps the partial message", async () => {
    const { registry } = setup(() => sseResponse(MID_STREAM_ERROR_FIXTURE));
    const { terminal } = await run(registry, call());
    if (terminal.type !== "error") throw new Error("expected error");
    expect(terminal.error).toMatchObject({ kind: "overloaded", retryable: true });
    expect(terminal.partial.blocks).toEqual([{ type: "text", text: "Partial" }]);
  });

  it("an abort mid-stream yields error(aborted) and keeps the partial message", async () => {
    const ctx = Context.background();
    const { registry } = setup((init) => {
      const body = new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(new TextEncoder().encode(STALL_PREFIX));
          init.signal?.addEventListener("abort", () => {
            controller.error(new DOMException("aborted", "AbortError"));
          });
        },
      });
      return new Response(body, { status: 200, headers: { "content-type": "text/event-stream" } });
    });
    const stream = registry.stream(ctx, call());
    const events: StreamEvent[] = [];
    for await (const e of stream) {
      events.push(e);
      if (e.type === "block_delta") ctx.cancel();
    }
    const terminal = await stream.result();
    if (terminal.type !== "error") throw new Error("expected error");
    expect(terminal.error.kind).toBe("aborted");
    expect(terminal.partial.blocks).toEqual([{ type: "text", text: "Thinking out lo" }]);
  });

  it("does not touch the network when the context is already cancelled", async () => {
    const { registry, calls } = setup(() => sseResponse(TEXT_FIXTURE));
    const ctx = Context.background();
    ctx.cancel();
    const { terminal } = await run(registry, call(), ctx);
    expect(terminal.type === "error" && terminal.error.kind).toBe("aborted");
    expect(calls).toHaveLength(0);
  });
});

describe("AnthropicProvider request mapping", () => {
  it("builds system, tools, cache breakpoints, tool results and adaptive effort", async () => {
    const { registry, calls } = setup(() => sseResponse(TEXT_FIXTURE));
    await run(
      registry,
      call({
        thinking: "high",
        tools: [
          {
            name: "get_weather",
            description: "Weather lookup",
            parameters: { type: "object", properties: { location: { type: "string" } } },
          },
        ],
        messages: [
          { role: "system", text: "You are terse." },
          { role: "user", content: "Weather in Paris and Rome?" },
          {
            role: "assistant",
            provider: "anthropic",
            model: "claude-opus-5-5",
            blocks: [
              { type: "thinking", thinking: "two calls", signature: "sig-1" },
              { type: "thinking", thinking: "", redacted: true, data: "blob" },
              { type: "text", text: "" },
              {
                type: "tool_call",
                id: "t1",
                name: "get_weather",
                arguments: { location: "Paris" },
              },
              { type: "tool_call", id: "t2", name: "get_weather", arguments: { location: "Rome" } },
            ],
          },
          {
            role: "tool_result",
            toolCallId: "t1",
            toolName: "get_weather",
            content: "sunny",
            isError: false,
          },
          {
            role: "tool_result",
            toolCallId: "t2",
            toolName: "get_weather",
            content: [{ type: "text", text: "unavailable" }],
            isError: true,
          },
          { role: "system", text: "Answer in one line." },
        ],
      }),
    );
    const body = calls[0]?.body as Record<string, unknown>;
    expect(body.model).toBe("claude-opus-5-5");
    expect(body.max_tokens).toBe(4096);
    expect(body.system).toEqual([
      { type: "text", text: "You are terse.", cache_control: { type: "ephemeral" } },
    ]);
    expect(body.thinking).toEqual({ type: "adaptive", display: "summarized" });
    expect(body.output_config).toEqual({ effort: "high" });
    expect(body.tools).toEqual([
      {
        name: "get_weather",
        description: "Weather lookup",
        input_schema: { type: "object", properties: { location: { type: "string" } } },
        eager_input_streaming: true,
      },
    ]);
    expect(body.messages).toEqual([
      { role: "user", content: [{ type: "text", text: "Weather in Paris and Rome?" }] },
      {
        role: "assistant",
        content: [
          { type: "thinking", thinking: "two calls", signature: "sig-1" },
          { type: "redacted_thinking", data: "blob" },
          { type: "tool_use", id: "t1", name: "get_weather", input: { location: "Paris" } },
          { type: "tool_use", id: "t2", name: "get_weather", input: { location: "Rome" } },
        ],
      },
      {
        role: "user",
        content: [
          { type: "tool_result", tool_use_id: "t1", content: "sunny", is_error: false },
          {
            type: "tool_result",
            tool_use_id: "t2",
            content: [{ type: "text", text: "unavailable" }],
            is_error: true,
            cache_control: { type: "ephemeral" },
          },
        ],
      },
      { role: "system", content: "Answer in one line." },
    ]);
  });

  it("drops thinking from other models, merges user text after tool results, honours cache:false", async () => {
    const { registry, calls } = setup(() => sseResponse(TEXT_FIXTURE));
    await run(
      registry,
      call({
        cache: false,
        thinking: "off",
        messages: [
          { role: "user", content: "q" },
          {
            role: "assistant",
            provider: "anthropic",
            model: "claude-haiku-5-5",
            blocks: [
              { type: "thinking", thinking: "x", signature: "s" },
              { type: "tool_call", id: "t1", name: "f", arguments: {} },
            ],
          },
          { role: "tool_result", toolCallId: "t1", toolName: "f", content: "r", isError: false },
          { role: "user", content: "and then?" },
        ],
      }),
    );
    const body = calls[0]?.body as Record<string, unknown>;
    expect(body.system).toBeUndefined();
    expect(body.thinking).toBeUndefined();
    expect(body.output_config).toEqual({ effort: "low" });
    expect(body.messages).toEqual([
      { role: "user", content: [{ type: "text", text: "q" }] },
      { role: "assistant", content: [{ type: "tool_use", id: "t1", name: "f", input: {} }] },
      {
        role: "user",
        content: [
          { type: "tool_result", tool_use_id: "t1", content: "r", is_error: false },
          { type: "text", text: "and then?" },
        ],
      },
    ]);
  });

  it("maps effort to a token budget on legacy budget-thinking models", async () => {
    const { registry, calls } = setup(() => sseResponse(TEXT_FIXTURE));
    await run(registry, call({ model: "claude-haiku-4-5", maxTokens: 20_000, thinking: "medium" }));
    const body = calls[0]?.body as Record<string, unknown>;
    expect(body.thinking).toEqual({ type: "enabled", budget_tokens: 8192 });
    expect(body.output_config).toBeUndefined();
    await run(registry, call({ model: "claude-haiku-4-5", maxTokens: 4000, thinking: "max" }));
    const clamped = calls[1]?.body as Record<string, unknown>;
    expect(clamped.thinking).toEqual({ type: "enabled", budget_tokens: 3999 });
    await run(registry, call({ model: "claude-haiku-4-5", maxTokens: 1000, thinking: "low" }));
    expect(calls[2]?.body.thinking).toBeUndefined();
  });
});
