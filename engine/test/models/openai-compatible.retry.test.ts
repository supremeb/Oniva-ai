import { describe, expect, it } from "vitest";
import { Context } from "../../src/core/context.js";
import { OpenAICompatibleProvider } from "../../src/models/openai-compatible.js";
import { describeError, type RetryInfo, rateLimitResetMs } from "../../src/models/retry.js";
import { ModelRegistry } from "../../src/models/stream.js";
import type { ModelCall, StreamEvent, TerminalEvent } from "../../src/models/types.js";

type Step = () => Response | Promise<Response>;

const frames = (items: Array<Record<string, unknown> | "[DONE]">): string =>
  items.map((f) => `data: ${f === "[DONE]" ? f : JSON.stringify(f)}\n\n`).join("");
const chunk = (d: Record<string, unknown>, finish: string | null = null) => ({
  choices: [{ index: 0, delta: d, finish_reason: finish }],
});
const ok =
  (text: string): Step =>
  () =>
    new Response(frames([chunk({ content: text }), chunk({}, "stop"), "[DONE]"]), {
      status: 200,
      headers: { "content-type": "text/event-stream" },
    });
const status =
  (code: number, headers: Record<string, string> = {}): Step =>
  () =>
    new Response(JSON.stringify({ error: { message: `status ${code}` } }), {
      status: code,
      headers,
    });
const netError = (): Step => () => {
  throw new TypeError("fetch failed", {
    cause: Object.assign(new Error("other side closed"), { code: "UND_ERR_SOCKET" }),
  });
};
/** A 200 whose body errors after `before` frames. */
const brokenStream =
  (before: Array<Record<string, unknown>>): Step =>
  () => {
    const enc = new TextEncoder();
    let sent = false;
    const body = new ReadableStream<Uint8Array>({
      pull(controller) {
        if (!sent) {
          sent = true;
          if (before.length > 0) controller.enqueue(enc.encode(frames(before)));
          return;
        }
        controller.error(
          new TypeError("terminated", {
            cause: Object.assign(new Error("other side closed"), { code: "ECONNRESET" }),
          }),
        );
      },
    });
    return new Response(body, { status: 200, headers: { "content-type": "text/event-stream" } });
  };

function setup(steps: Step[], retry: { maxRetries?: number; budgetMs?: number } = {}) {
  let calls = 0;
  const sleeps: number[] = [];
  const retries: RetryInfo[] = [];
  const provider = new OpenAICompatibleProvider({
    name: "p",
    baseURL: "https://example.test/v1",
    fetch: async () => {
      const step = steps[Math.min(calls++, steps.length - 1)] as Step;
      return step();
    },
    retry: {
      ...retry,
      random: () => 1,
      sleep: async (ms) => {
        sleeps.push(ms);
      },
      onRetry: (i) => retries.push(i),
    },
  });
  return {
    registry: new ModelRegistry().register(provider),
    calls: () => calls,
    sleeps,
    retries,
  };
}

const call: ModelCall = {
  provider: "p",
  model: "m",
  apiKey: "sk-secret",
  maxTokens: 16,
  messages: [{ role: "user", content: "Hi" }],
};

async function run(registry: ModelRegistry, ctx = Context.background()) {
  const stream = registry.stream(ctx, call);
  const events: StreamEvent[] = [];
  for await (const e of stream) events.push(e);
  const terminal: TerminalEvent = await stream.result();
  return { events, terminal };
}

const text = (t: TerminalEvent): string =>
  t.type === "done" ? t.message.blocks.map((b) => (b.type === "text" ? b.text : "")).join("") : "";

describe("OpenAI-compatible retries", () => {
  it("retries a network error and yields one clean answer", async () => {
    const s = setup([netError(), ok("Hello")]);
    const { events, terminal } = await run(s.registry);
    expect(s.calls()).toBe(2);
    expect(text(terminal)).toBe("Hello");
    expect(events.filter((e) => e.type === "start")).toHaveLength(1);
    expect(s.retries).toHaveLength(1);
    expect(s.retries[0]?.error.message).toBe("fetch failed (UND_ERR_SOCKET: other side closed)");
  });

  it("retries a stream that breaks before the first delta without duplicating output", async () => {
    const s = setup([brokenStream([]), ok("Once")]);
    const { events, terminal } = await run(s.registry);
    expect(s.calls()).toBe(2);
    expect(text(terminal)).toBe("Once");
    expect(events.filter((e) => e.type === "block_start")).toHaveLength(1);
    expect(events.filter((e) => e.type === "start")).toHaveLength(1);
  });

  it("honours Retry-After on a 503", async () => {
    const s = setup([status(503, { "retry-after": "2" }), ok("Hi")]);
    const { terminal } = await run(s.registry);
    expect(text(terminal)).toBe("Hi");
    expect(s.sleeps).toEqual([2000]);
  });

  it("honours x-ratelimit-reset on a 429 and retries once it clears", async () => {
    const reset = String(Date.now() + 1500);
    const s = setup([status(429, { "x-ratelimit-reset": reset }), ok("Hi")]);
    const { terminal } = await run(s.registry);
    expect(text(terminal)).toBe("Hi");
    expect(s.sleeps).toHaveLength(1);
    expect(s.sleeps[0]).toBeGreaterThan(1000);
    expect(s.sleeps[0]).toBeLessThanOrEqual(1500);
  });

  it("backs off exponentially, then gives up with a retryable error", async () => {
    const s = setup([status(503)], { maxRetries: 3 });
    const { terminal } = await run(s.registry);
    expect(s.calls()).toBe(4);
    expect(s.sleeps).toEqual([500, 1000, 2000]);
    if (terminal.type !== "error") throw new Error("expected error");
    expect(terminal.error).toMatchObject({ kind: "overloaded", retryable: true, status: 503 });
  });

  it("stops when a server delay does not fit the time budget", async () => {
    const s = setup([status(429, { "retry-after": "120" }), ok("late")], { budgetMs: 10_000 });
    const { terminal } = await run(s.registry);
    expect(s.calls()).toBe(1);
    expect(terminal).toMatchObject({ type: "error", error: { kind: "rate_limited" } });
  });

  it("retries 408 and 409", async () => {
    const s = setup([status(408), status(409), ok("ok")]);
    expect(text((await run(s.registry)).terminal)).toBe("ok");
    expect(s.calls()).toBe(3);
  });

  it.each([400, 401, 402, 403, 404, 413, 422])("never retries HTTP %i", async (code) => {
    const s = setup([status(code), ok("no")]);
    const { terminal } = await run(s.registry);
    expect(s.calls()).toBe(1);
    expect(terminal.type).toBe("error");
  });

  it("never retries a context overflow", async () => {
    const s = setup([
      () =>
        new Response(JSON.stringify({ error: { message: "maximum context length exceeded" } }), {
          status: 400,
        }),
      ok("no"),
    ]);
    const { terminal } = await run(s.registry);
    expect(s.calls()).toBe(1);
    expect(terminal).toMatchObject({ type: "error", error: { kind: "context_overflow" } });
  });

  it("does not retry once a delta was emitted: retryable error, partial kept", async () => {
    const s = setup([brokenStream([chunk({ content: "Par" })]), ok("never")]);
    const { events, terminal } = await run(s.registry);
    expect(s.calls()).toBe(1);
    expect(s.sleeps).toEqual([]);
    if (terminal.type !== "error") throw new Error("expected error");
    expect(terminal.error).toMatchObject({ kind: "network", retryable: true });
    expect(terminal.error.message).toContain("ECONNRESET: other side closed");
    expect(terminal.partial.blocks).toEqual([{ type: "text", text: "Par" }]);
    expect(events.filter((e) => e.type === "block_delta")).toHaveLength(1);
  });

  it("stops retrying when aborted during the backoff", async () => {
    const ctx = Context.background();
    let calls = 0;
    const provider = new OpenAICompatibleProvider({
      name: "p",
      baseURL: "https://example.test/v1",
      fetch: async () => {
        calls++;
        return status(503)();
      },
      retry: {
        sleep: async () => {
          ctx.cancel(new Error("user stop"));
        },
      },
    });
    const { terminal } = await run(new ModelRegistry().register(provider), ctx);
    expect(calls).toBe(1);
    expect(terminal).toMatchObject({ type: "error", error: { kind: "aborted", retryable: false } });
  });

  it("can be turned off", async () => {
    const s = setup([netError(), ok("x")], { maxRetries: 0 });
    const { terminal } = await run(s.registry);
    expect(s.calls()).toBe(1);
    expect(terminal).toMatchObject({ type: "error", error: { kind: "network", retryable: true } });
  });

  it("puts the cause code in the error message and never the key", async () => {
    const s = setup([netError()], { maxRetries: 0 });
    const { terminal } = await run(s.registry);
    if (terminal.type !== "error") throw new Error("expected error");
    expect(terminal.error.message).toBe("fetch failed (UND_ERR_SOCKET: other side closed)");
    expect(JSON.stringify(terminal)).not.toContain("sk-secret");
  });
});

describe("retry helpers", () => {
  it("describeError walks the cause chain and aggregate errors", () => {
    const agg = new AggregateError([Object.assign(new Error("x"), { code: "ETIMEDOUT" })]);
    expect(describeError(new TypeError("fetch failed", { cause: agg }))).toContain("ETIMEDOUT: x");
    expect(describeError(new Error("plain"))).toBe("plain");
  });

  it("rateLimitResetMs accepts ms and seconds timestamps", () => {
    const now = 1_800_000_000_000;
    expect(rateLimitResetMs(String(now + 2500), now)).toBe(2500);
    expect(rateLimitResetMs(String(now / 1000 + 3), now)).toBe(3000);
    expect(rateLimitResetMs(null, now)).toBeNull();
    expect(rateLimitResetMs("abc", now)).toBeNull();
  });
});
