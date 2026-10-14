import { describe, expect, it } from "vitest";
import { ManualClock } from "../../src/core/clock.js";
import { Context } from "../../src/core/context.js";
import { FakeProvider, fakeContextOverflow, fakeText } from "../../src/models/fake.js";
import { ModelRegistry } from "../../src/models/stream.js";
import type { ModelCall, StreamEvent } from "../../src/models/types.js";

function call(overrides: Partial<ModelCall> = {}): ModelCall {
  return {
    provider: "fake",
    model: "fake-1",
    apiKey: "k",
    maxTokens: 1000,
    messages: [{ role: "user", content: "hi" }],
    ...overrides,
  };
}

async function collect(iter: AsyncIterable<StreamEvent>): Promise<StreamEvent[]> {
  const out: StreamEvent[] = [];
  for await (const e of iter) out.push(e);
  return out;
}

describe("FakeProvider", () => {
  it("streams text then a tool call; the aggregated message keeps both in order with usage", async () => {
    const fake = new FakeProvider();
    fake.enqueue({
      steps: [
        { type: "text", text: "Let me check." },
        { type: "tool_call", id: "call_1", name: "lookup", arguments: { q: "x" } },
      ],
      usage: { inputTokens: 12, outputTokens: 7 },
    });
    const registry = new ModelRegistry().register(fake);
    const events = await collect(registry.stream(Context.background(), call()));

    expect(events.map((e) => e.type)).toEqual([
      "start",
      "block_start",
      "block_delta",
      "block_end",
      "block_start",
      "block_delta",
      "block_end",
      "done",
    ]);
    const done = events.at(-1);
    if (done?.type !== "done") throw new Error("expected done");
    expect(done.stopReason).toBe("tool_use");
    expect(done.message).toEqual({
      role: "assistant",
      provider: "fake",
      model: "fake-1",
      stopReason: "tool_use",
      blocks: [
        { type: "text", text: "Let me check." },
        { type: "tool_call", id: "call_1", name: "lookup", arguments: { q: "x" } },
      ],
    });
    expect(done.usage).toEqual({
      inputTokens: 12,
      outputTokens: 7,
      cacheReadTokens: 0,
      cacheWriteTokens: 0,
    });
    expect(fake.requests).toHaveLength(1);
    expect(fake.requests[0]?.model).toBe("fake-1");
  });

  it("streams thinking and chunked text, and result() returns the terminal event", async () => {
    const fake = new FakeProvider();
    fake.enqueue({
      steps: [
        { type: "thinking", thinking: "hmm" },
        { type: "text", text: "abcdef", chunkSize: 2 },
      ],
    });
    const stream = new ModelRegistry().register(fake).stream(Context.background(), call());
    const events = await collect(stream);
    const deltas = events.filter((e) => e.type === "block_delta");
    expect(deltas).toHaveLength(4);
    const result = await stream.result();
    expect(result.type).toBe("done");
    if (result.type !== "done") return;
    expect(result.stopReason).toBe("end_turn");
    expect(result.message.blocks).toEqual([
      { type: "thinking", thinking: "hmm" },
      { type: "text", text: "abcdef" },
    ]);
    // Default usage is a character estimate, never zero output.
    expect(result.usage.outputTokens).toBeGreaterThan(0);
    expect(result.usage.inputTokens).toBeGreaterThan(0);
  });

  it("honours delays on the injected clock", async () => {
    const clock = new ManualClock(0);
    const fake = new FakeProvider();
    fake.enqueue({ steps: [{ type: "delay", ms: 1000 }, ...fakeText("late").steps] });
    const stream = new ModelRegistry().register(fake).stream(Context.background(clock), call());
    let settled = false;
    const p = stream.result().then((r) => {
      settled = true;
      return r;
    });
    await clock.advanceAsync(999);
    expect(settled).toBe(false);
    await clock.advanceAsync(1);
    const r = await p;
    expect(r.type).toBe("done");
  });

  it("an abort mid-stream yields error(aborted) and keeps the partial message", async () => {
    const clock = new ManualClock(0);
    const ctx = Context.background(clock);
    const fake = new FakeProvider();
    fake.enqueue({
      steps: [{ type: "text", text: "partial answer", chunkSize: 8 }],
      chunkDelayMs: 100,
    });
    const stream = new ModelRegistry().register(fake).stream(ctx, call());
    const seen: StreamEvent[] = [];
    const consumer = (async () => {
      for await (const e of stream) {
        seen.push(e);
        if (e.type === "block_delta") ctx.cancel();
      }
    })();
    await clock.advanceAsync(100);
    await consumer;
    const last = seen.at(-1);
    if (last?.type !== "error") throw new Error(`expected error, got ${last?.type}`);
    expect(last.error.kind).toBe("aborted");
    expect(last.partial.blocks).toEqual([{ type: "text", text: "partial " }]);
    expect(seen.filter((e) => e.type === "error")).toHaveLength(1);
  });

  it("does not call the provider when the context is already cancelled", async () => {
    const fake = new FakeProvider();
    fake.enqueue(fakeText("never"));
    const ctx = Context.background();
    ctx.cancel();
    const r = await new ModelRegistry().register(fake).stream(ctx, call()).result();
    expect(r.type === "error" && r.error.kind).toBe("aborted");
    expect(fake.requests).toHaveLength(0);
  });

  it("plays scripted errors and context overflow as error events", async () => {
    const fake = new FakeProvider();
    fake.enqueue(fakeContextOverflow());
    fake.enqueue({
      steps: [
        { type: "text", text: "so far" },
        { type: "error", kind: "overloaded", message: "busy" },
      ],
    });
    const registry = new ModelRegistry().register(fake);
    const first = await registry.stream(Context.background(), call()).result();
    expect(first.type === "error" && first.error).toMatchObject({
      kind: "context_overflow",
      retryable: false,
    });
    const second = await registry.stream(Context.background(), call()).result();
    if (second.type !== "error") throw new Error("expected error");
    expect(second.error).toMatchObject({ kind: "overloaded", message: "busy", retryable: true });
    expect(second.partial.blocks).toEqual([{ type: "text", text: "so far" }]);
  });

  it("supports function responses and reports an exhausted script as an error", async () => {
    const fake = new FakeProvider({ name: "scripted" });
    fake.enqueue((req) => fakeText(`echo ${req.messages.length}`));
    const registry = new ModelRegistry().register(fake);
    const ok = await registry.stream(Context.background(), call({ provider: "scripted" })).result();
    expect(ok.type === "done" && ok.message.blocks).toEqual([{ type: "text", text: "echo 1" }]);
    const none = await registry
      .stream(Context.background(), call({ provider: "scripted" }))
      .result();
    expect(none.type === "error" && none.error.kind).toBe("provider");
  });
});
