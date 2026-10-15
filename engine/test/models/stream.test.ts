import { describe, expect, it } from "vitest";
import { Context } from "../../src/core/context.js";
import { FakeProvider, fakeText } from "../../src/models/fake.js";
import { MessageBuilder, ModelRegistry } from "../../src/models/stream.js";
import type {
  ModelInput,
  ModelProvider,
  ModelRequest,
  ModelResolution,
  StreamEvent,
} from "../../src/models/types.js";

const input: ModelInput = { messages: [{ role: "user", content: "hello" }] };

function allow(overrides: Partial<Extract<ModelResolution, { ok: true }>> = {}): ModelResolution {
  return {
    ok: true,
    provider: "fake",
    model: "fake-1",
    apiKey: "sk-test",
    maxTokens: 256,
    ...overrides,
  };
}

describe("ModelRegistry.resolveAndStream", () => {
  it("a denial yields an error event without calling any provider", async () => {
    let calls = 0;
    const spy: ModelProvider = {
      name: "fake",
      // biome-ignore lint/correctness/useYield: never iterated in this test
      async *stream() {
        calls++;
      },
    };
    const registry = new ModelRegistry().register(spy);
    const events: StreamEvent[] = [];
    const stream = registry.resolveAndStream(
      Context.background(),
      async () => ({ ok: false, reason: "quota exhausted", code: "quota" }),
      input,
    );
    for await (const e of stream) events.push(e);
    expect(events).toHaveLength(1);
    const [e] = events;
    if (e?.type !== "error") throw new Error("expected error");
    expect(e.error).toEqual({
      kind: "denied",
      message: "quota exhausted",
      retryable: false,
      code: "quota",
    });
    expect(e.partial.blocks).toEqual([]);
    expect(calls).toBe(0);
  });

  it("a throwing resolver is a denial too", async () => {
    const registry = new ModelRegistry().register(new FakeProvider());
    const r = await registry
      .resolveAndStream(
        Context.background(),
        async () => {
          throw new Error("policy down");
        },
        input,
      )
      .result();
    expect(r.type === "error" && r.error).toMatchObject({ kind: "denied", retryable: false });
  });

  it("passes the resolved model, key, maxTokens and thinking to the provider", async () => {
    const fake = new FakeProvider();
    fake.enqueue(fakeText("ok"));
    const registry = new ModelRegistry().register(fake);
    const r = await registry
      .resolveAndStream(Context.background(), async () => allow({ thinking: "high" }), {
        ...input,
        cache: false,
      })
      .result();
    expect(r.type).toBe("done");
    const req = fake.requests[0] as ModelRequest;
    expect(req).toMatchObject({
      model: "fake-1",
      apiKey: "sk-test",
      maxTokens: 256,
      thinking: "high",
      cache: false,
    });
  });

  it("an unknown provider yields a provider error", async () => {
    const r = await new ModelRegistry()
      .resolveAndStream(Context.background(), async () => allow({ provider: "nope" }), input)
      .result();
    expect(r.type === "error" && r.error.kind).toBe("provider");
  });
});

describe("ModelRegistry.stream", () => {
  it("attaches cost from the host pricing table", async () => {
    const fake = new FakeProvider();
    fake.enqueue({ ...fakeText("hi"), usage: { inputTokens: 1_000_000, outputTokens: 100_000 } });
    const registry = new ModelRegistry({
      pricing: { "fake-1": { input: 4, output: 20 } },
    }).register(fake);
    const r = await registry
      .resolveAndStream(Context.background(), async () => allow(), input)
      .result();
    if (r.type !== "done") throw new Error("expected done");
    expect(r.usage.cost?.total).toBeCloseTo(6);
  });

  it("converts a thrown provider exception into an error event with the partial message", async () => {
    const thrower: ModelProvider = {
      name: "boom",
      async *stream(_ctx, req) {
        const b = new MessageBuilder("boom", req.model);
        yield b.start();
        const s = b.startBlock({ type: "text", text: "" });
        yield s;
        yield b.delta(s.index, { type: "text", text: "half" });
        throw new Error("socket closed");
      },
    };
    const stream = new ModelRegistry().register(thrower).stream(Context.background(), {
      ...input,
      provider: "boom",
      model: "m",
      apiKey: "k",
      maxTokens: 1,
    });
    const r = await stream.result();
    if (r.type !== "error") throw new Error("expected error");
    expect(r.error.kind).toBe("provider");
    expect(r.error.message).toContain("socket closed");
    expect(r.partial.blocks).toEqual([{ type: "text", text: "half" }]);
  });

  it("reports a stream that ends without a terminal event", async () => {
    const quiet: ModelProvider = {
      name: "quiet",
      async *stream(_ctx, req) {
        yield new MessageBuilder("quiet", req.model).start();
      },
    };
    const r = await new ModelRegistry()
      .register(quiet)
      .stream(Context.background(), {
        ...input,
        provider: "quiet",
        model: "m",
        apiKey: "k",
        maxTokens: 1,
      })
      .result();
    expect(r.type === "error" && r.error).toMatchObject({ kind: "provider", retryable: true });
  });

  it("replays events to a second iterator and rejects duplicate registration", async () => {
    const fake = new FakeProvider();
    fake.enqueue(fakeText("x"));
    const registry = new ModelRegistry().register(fake);
    expect(() => registry.register(new FakeProvider())).toThrow(/already registered/);
    const stream = registry.stream(Context.background(), {
      ...input,
      provider: "fake",
      model: "m",
      apiKey: "k",
      maxTokens: 1,
    });
    const a: string[] = [];
    const b: string[] = [];
    for await (const e of stream) a.push(e.type);
    for await (const e of stream) b.push(e.type);
    expect(b).toEqual(a);
    expect(fake.requests).toHaveLength(1);
  });
});

describe("MessageBuilder", () => {
  it("parses streamed tool arguments and flags invalid JSON", () => {
    const b = new MessageBuilder("p", "m");
    b.start();
    const ok = b.startBlock({ type: "tool_call", id: "1", name: "t", arguments: {} });
    b.delta(ok.index, { type: "tool_args", partialJson: '{"a":' });
    b.delta(ok.index, { type: "tool_args", partialJson: "1}" });
    const end = b.endBlock(ok.index);
    expect(end.block).toEqual({ type: "tool_call", id: "1", name: "t", arguments: { a: 1 } });
    const bad = b.startBlock({ type: "tool_call", id: "2", name: "t", arguments: {} });
    b.delta(bad.index, { type: "tool_args", partialJson: "[1,2" });
    expect(b.endBlock(bad.index).block).toEqual({
      type: "tool_call",
      id: "2",
      name: "t",
      arguments: {},
      invalidArguments: "[1,2",
    });
    const done = b.done("tool_use");
    expect(done.message.blocks).toHaveLength(2);
  });

  it("folds foreign events with apply()", () => {
    const src = new MessageBuilder("p", "m");
    const dst = new MessageBuilder("p", "m");
    const events: StreamEvent[] = [src.start()];
    const s = src.startBlock({ type: "text", text: "a" });
    events.push(s, src.delta(s.index, { type: "text", text: "b" }));
    for (const e of events) dst.apply(e);
    expect(dst.snapshot().blocks).toEqual([{ type: "text", text: "ab" }]);
  });
});
