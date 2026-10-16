import { describe, expect, it } from "vitest";
import { Context } from "../../src/core/context.js";
import {
  OpenAICompatibleProvider,
  openRouterProvider,
} from "../../src/models/openai-compatible.js";
import { ModelRegistry } from "../../src/models/stream.js";
import type { ModelCall, StreamEvent, TerminalEvent } from "../../src/models/types.js";

type FetchLike = (url: string | URL | Request, init?: RequestInit) => Promise<Response>;

interface Captured {
  url: string;
  headers: Headers;
  body: Record<string, unknown>;
}

function chunks(frames: Array<Record<string, unknown> | "[DONE]">, comment = true): string {
  const lines = frames.map((f) => `data: ${f === "[DONE]" ? f : JSON.stringify(f)}\n\n`);
  return (comment ? ": OPENROUTER PROCESSING\n\n" : "") + lines.join("");
}

function sse(body: string): Response {
  return new Response(body, { status: 200, headers: { "content-type": "text/event-stream" } });
}

function delta(d: Record<string, unknown>, finish: string | null = null) {
  return { id: "gen-1", choices: [{ index: 0, delta: d, finish_reason: finish }] };
}

function setup(respond: (init: RequestInit) => Response | Promise<Response>) {
  const calls: Captured[] = [];
  const fetch: FetchLike = async (url, init = {}) => {
    calls.push({
      url: String(url),
      headers: new Headers(init.headers),
      body: JSON.parse(String(init.body)) as Record<string, unknown>,
    });
    return respond(init);
  };
  const provider = openRouterProvider({ fetch, headers: { "X-Title": "Oniva" } });
  return { registry: new ModelRegistry().register(provider), provider, calls };
}

function call(overrides: Partial<ModelCall> = {}): ModelCall {
  return {
    provider: "openrouter",
    model: "openai/gpt-4o-mini",
    apiKey: "sk-or-test-key",
    maxTokens: 512,
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

const USAGE = {
  prompt_tokens: 130,
  completion_tokens: 12,
  prompt_tokens_details: { cached_tokens: 100, cache_write_tokens: 5 },
};

describe("OpenAICompatibleProvider streaming", () => {
  it("streams text with the request shape and usage (cache tokens split out)", async () => {
    const { registry, calls } = setup(() =>
      sse(
        chunks([
          delta({ role: "assistant", content: "" }),
          delta({ content: "Hello" }),
          delta({ content: " world" }),
          delta({}, "stop"),
          { id: "gen-1", choices: [], usage: USAGE },
          "[DONE]",
        ]),
      ),
    );
    const { events, terminal } = await run(registry, call());
    expect(events.map((e) => e.type)).toEqual([
      "start",
      "block_start",
      "block_delta",
      "block_delta",
      "block_end",
      "done",
    ]);
    expect(events[0]).toEqual({
      type: "start",
      provider: "openrouter",
      model: "openai/gpt-4o-mini",
    });
    expect(events[2]).toEqual({
      type: "block_delta",
      index: 0,
      delta: { type: "text", text: "Hello" },
    });
    if (terminal.type !== "done") throw new Error("expected done");
    expect(terminal.stopReason).toBe("end_turn");
    expect(terminal.message.blocks).toEqual([{ type: "text", text: "Hello world" }]);
    expect(terminal.usage).toEqual({
      inputTokens: 25,
      outputTokens: 12,
      cacheReadTokens: 100,
      cacheWriteTokens: 5,
    });
    expect(calls[0]?.url).toBe("https://openrouter.ai/api/v1/chat/completions");
    expect(calls[0]?.headers.get("authorization")).toBe("Bearer sk-or-test-key");
    expect(calls[0]?.headers.get("x-title")).toBe("Oniva");
    expect(calls[0]?.body).toMatchObject({
      model: "openai/gpt-4o-mini",
      stream: true,
      stream_options: { include_usage: true },
      max_tokens: 512,
    });
  });

  it("assembles parallel tool calls streamed per index", async () => {
    const { registry } = setup(() =>
      sse(
        chunks([
          delta({ content: "Checking" }),
          delta({
            tool_calls: [
              {
                index: 0,
                id: "call_a",
                type: "function",
                function: { name: "weather", arguments: "" },
              },
            ],
          }),
          delta({
            tool_calls: [
              {
                index: 1,
                id: "call_b",
                type: "function",
                function: { name: "time", arguments: "" },
              },
            ],
          }),
          delta({ tool_calls: [{ index: 0, function: { arguments: '{"city":' } }] }),
          delta({ tool_calls: [{ index: 1, function: { arguments: '{"tz":"UTC"}' } }] }),
          delta({ tool_calls: [{ index: 0, function: { arguments: '"Paris"}' } }] }),
          delta({}, "tool_calls"),
          { choices: [], usage: { prompt_tokens: 10, completion_tokens: 4 } },
          "[DONE]",
        ]),
      ),
    );
    const { events, terminal } = await run(registry, call());
    if (terminal.type !== "done") throw new Error("expected done");
    expect(terminal.stopReason).toBe("tool_use");
    expect(terminal.message.blocks).toEqual([
      { type: "text", text: "Checking" },
      { type: "tool_call", id: "call_a", name: "weather", arguments: { city: "Paris" } },
      { type: "tool_call", id: "call_b", name: "time", arguments: { tz: "UTC" } },
    ]);
    expect(terminal.usage).toMatchObject({ inputTokens: 10, outputTokens: 4, cacheReadTokens: 0 });
    const ends = events.filter((e) => e.type === "block_end").map((e) => e.index);
    expect(ends).toEqual([0, 1, 2]);
  });

  it("treats a provider's stop finish with tool calls as tool_use and keeps bad JSON invalid", async () => {
    const { registry } = setup(() =>
      sse(
        chunks([
          delta({
            tool_calls: [{ index: 0, id: "c1", function: { name: "t", arguments: '{"a":' } }],
          }),
          delta({}, "stop"),
          "[DONE]",
        ]),
      ),
    );
    const { terminal } = await run(registry, call());
    if (terminal.type !== "done") throw new Error("expected done");
    expect(terminal.stopReason).toBe("tool_use");
    expect(terminal.message.blocks[0]).toMatchObject({ invalidArguments: '{"a":' });
  });

  it("ignores reasoning deltas from non-Claude models and never puts them in text", async () => {
    const { registry } = setup(() =>
      sse(
        chunks([
          delta({ reasoning: "thinking about it", reasoning_content: "more" }),
          delta({ reasoning_details: [{ type: "reasoning.text", text: "x" }] }),
          delta({ content: "Answer" }),
          delta({}, "stop"),
          "[DONE]",
        ]),
      ),
    );
    const { events, terminal } = await run(
      registry,
      call({ model: "deepseek/deepseek-v4.1-flash" }),
    );
    if (terminal.type !== "done") throw new Error("expected done");
    expect(terminal.message.blocks).toEqual([{ type: "text", text: "Answer" }]);
    expect(events.filter((e) => e.type === "block_start")).toHaveLength(1);
  });

  it("maps finish reasons length and content_filter", async () => {
    for (const [finish, reason] of [
      ["length", "max_tokens"],
      ["content_filter", "refusal"],
    ] as const) {
      const { registry } = setup(() =>
        sse(chunks([delta({ content: "x" }), delta({}, finish), "[DONE]"])),
      );
      const { terminal } = await run(registry, call());
      expect(terminal.type === "done" && terminal.stopReason).toBe(reason);
    }
  });

  it("splits events across arbitrary network chunk boundaries", async () => {
    const text = chunks([delta({ content: "Hi" }), delta({}, "stop"), "[DONE]"]);
    const { registry } = setup(
      () =>
        new Response(
          new ReadableStream({
            start(c) {
              const enc = new TextEncoder();
              for (let i = 0; i < text.length; i += 7) c.enqueue(enc.encode(text.slice(i, i + 7)));
              c.close();
            },
          }),
          { status: 200 },
        ),
    );
    const { terminal } = await run(registry, call());
    expect(terminal.type === "done" && terminal.message.blocks).toEqual([
      { type: "text", text: "Hi" },
    ]);
  });
});

describe("OpenAICompatibleProvider errors", () => {
  const cases: Array<[number, string, string, boolean]> = [
    [401, "No auth credentials found", "auth", false],
    [403, "forbidden", "auth", false],
    [429, "rate limit", "rate_limited", true],
    [500, "boom", "overloaded", true],
    [503, "no provider available", "overloaded", true],
    [529, "overloaded", "overloaded", true],
    [400, "bad parameter", "invalid_request", false],
    [404, "no such model", "invalid_request", false],
  ];
  for (const [status, message, kind, retryable] of cases) {
    it(`maps HTTP ${status} to ${kind}`, async () => {
      const { registry } = setup(
        () => new Response(JSON.stringify({ error: { message, code: status } }), { status }),
      );
      const { events, terminal } = await run(registry, call());
      expect(events.map((e) => e.type)).toEqual(["error"]);
      if (terminal.type !== "error") throw new Error("expected error");
      expect(terminal.error).toMatchObject({ kind, status, message, retryable });
    });
  }

  it("maps a 400 context-length message to context_overflow", async () => {
    const message =
      "This endpoint's maximum context length is 200000 tokens. However, you requested about 250000 tokens";
    const { registry } = setup(
      () => new Response(JSON.stringify({ error: { message, code: 400 } }), { status: 400 }),
    );
    const { terminal } = await run(registry, call());
    expect(terminal.type === "error" && terminal.error.kind).toBe("context_overflow");
  });

  it("maps a transport failure to network", async () => {
    const { registry } = setup(() => {
      throw new TypeError("fetch failed");
    });
    const { terminal } = await run(registry, call());
    expect(terminal.type === "error" && terminal.error).toMatchObject({
      kind: "network",
      retryable: true,
    });
  });

  it("maps an error object inside the stream and keeps the partial text", async () => {
    const { registry } = setup(() =>
      sse(
        chunks([
          delta({ content: "partial" }),
          {
            error: { code: 502, message: "upstream died" },
            choices: [{ delta: {}, finish_reason: "error" }],
          },
        ]),
      ),
    );
    const { terminal } = await run(registry, call());
    if (terminal.type !== "error") throw new Error("expected error");
    expect(terminal.error.kind).toBe("overloaded");
    expect(terminal.partial.blocks).toEqual([{ type: "text", text: "partial" }]);
  });

  it("fails with network when the stream ends before a finish reason", async () => {
    const { registry } = setup(() => sse(chunks([delta({ content: "cut" })])));
    const { terminal } = await run(registry, call());
    expect(terminal.type === "error" && terminal.error.kind).toBe("network");
  });
});

describe("OpenAICompatibleProvider abort", () => {
  it("an abort mid-stream yields error(aborted) and keeps the partial message", async () => {
    const { registry } = setup((init) => {
      const enc = new TextEncoder();
      const body = new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(enc.encode(chunks([delta({ content: "Hel" })])));
          init.signal?.addEventListener("abort", () => {
            controller.error(new DOMException("aborted", "AbortError"));
          });
        },
      });
      return new Response(body, { status: 200 });
    });
    const ctx = Context.background();
    const stream = registry.stream(ctx, call());
    for await (const e of stream) {
      if (e.type === "block_delta") ctx.cancel();
    }
    const terminal = await stream.result();
    if (terminal.type !== "error") throw new Error("expected error");
    expect(terminal.error.kind).toBe("aborted");
    expect(terminal.partial.blocks).toEqual([{ type: "text", text: "Hel" }]);
  });

  it("does not touch the network when already cancelled", async () => {
    const { registry, calls } = setup(() => sse(""));
    const ctx = Context.background();
    ctx.cancel();
    const { terminal } = await run(registry, call(), ctx);
    expect(terminal.type === "error" && terminal.error.kind).toBe("aborted");
    expect(calls).toHaveLength(0);
  });
});

describe("OpenAICompatibleProvider request mapping", () => {
  it("round-trips an assistant tool call and its tool result", () => {
    const { provider } = setup(() => sse(""));
    const params = provider.buildParams({
      model: "openai/gpt-4o-mini",
      apiKey: "k",
      maxTokens: 100,
      tools: [
        {
          name: "weather",
          description: "Get weather",
          parameters: { type: "object", properties: { city: { type: "string" } } },
        },
      ],
      messages: [
        { role: "system", text: "Be brief." },
        {
          role: "user",
          content: [
            { type: "text", text: "Weather in " },
            { type: "text", text: "Paris?" },
          ],
        },
        {
          role: "assistant",
          provider: "anthropic",
          model: "claude-opus-5-5",
          blocks: [
            { type: "thinking", thinking: "hmm", signature: "sig" },
            { type: "text", text: "Looking." },
            { type: "tool_call", id: "call_1", name: "weather", arguments: { city: "Paris" } },
            { type: "tool_call", id: "call_2", name: "weather", arguments: { city: "Rome" } },
          ],
        },
        {
          role: "tool_result",
          toolCallId: "call_1",
          toolName: "weather",
          content: "18C",
          isError: false,
        },
        {
          role: "tool_result",
          toolCallId: "call_2",
          toolName: "weather",
          content: [{ type: "text", text: "no data" }],
          isError: true,
        },
        {
          role: "assistant",
          provider: "openrouter",
          model: "m",
          blocks: [{ type: "tool_call", id: "call_3", name: "weather", arguments: {} }],
        },
      ],
    });
    expect(params.tools).toEqual([
      {
        type: "function",
        function: {
          name: "weather",
          description: "Get weather",
          parameters: { type: "object", properties: { city: { type: "string" } } },
        },
      },
    ]);
    expect(params.messages).toEqual([
      { role: "system", content: "Be brief." },
      {
        role: "user",
        content: [
          { type: "text", text: "Weather in " },
          { type: "text", text: "Paris?" },
        ],
      },
      {
        role: "assistant",
        content: "Looking.",
        tool_calls: [
          {
            id: "call_1",
            type: "function",
            function: { name: "weather", arguments: '{"city":"Paris"}' },
          },
          {
            id: "call_2",
            type: "function",
            function: { name: "weather", arguments: '{"city":"Rome"}' },
          },
        ],
      },
      { role: "tool", tool_call_id: "call_1", content: "18C" },
      { role: "tool", tool_call_id: "call_2", content: "no data" },
      {
        role: "assistant",
        content: null,
        tool_calls: [
          { id: "call_3", type: "function", function: { name: "weather", arguments: "{}" } },
        ],
      },
    ]);
  });

  it("puts cache breakpoints on Claude models only, and not when cache is off", () => {
    const provider = openRouterProvider({ fetch: (async () => sse("")) as FetchLike });
    const base = {
      apiKey: "k",
      maxTokens: 10,
      messages: [
        { role: "system" as const, text: "sys" },
        { role: "user" as const, content: "q" },
      ],
    };
    const claude = provider.buildParams({ ...base, model: "anthropic/claude-sonnet-5.5" });
    expect(claude.messages).toEqual([
      {
        role: "system",
        content: [{ type: "text", text: "sys", cache_control: { type: "ephemeral" } }],
      },
      {
        role: "user",
        content: [{ type: "text", text: "q", cache_control: { type: "ephemeral" } }],
      },
    ]);
    const other = provider.buildParams({ ...base, model: "openai/gpt-4o-mini" });
    expect(other.messages[1]).toEqual({ role: "user", content: "q" });
    const off = provider.buildParams({
      ...base,
      model: "anthropic/claude-sonnet-5.5",
      cache: false,
    });
    expect(off.messages[1]).toEqual({ role: "user", content: "q" });
  });

  it("is configurable by name, base URL and headers", async () => {
    const urls: string[] = [];
    const provider = new OpenAICompatibleProvider({
      name: "custom",
      baseURL: "http://localhost:9/v1/",
      fetch: async (url) => {
        urls.push(String(url));
        return sse(chunks([delta({ content: "ok" }), delta({}, "stop"), "[DONE]"]));
      },
    });
    const registry = new ModelRegistry().register(provider);
    const { terminal } = await run(registry, call({ provider: "custom" }));
    expect(terminal.type).toBe("done");
    expect(urls).toEqual(["http://localhost:9/v1/chat/completions"]);
  });
});
