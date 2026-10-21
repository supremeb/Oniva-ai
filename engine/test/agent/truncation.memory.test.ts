/**
 * A reply cut off at the output token limit (stopReason max_tokens) with no tool call is not an answer: the
 * run continues with a notice asking the model to finish, at most MAX_TRUNCATION_CONTINUES times in a row.
 * Reasoning models can spend the whole output budget before emitting anything, which used to end a Deep Work
 * run with an empty answer and no report.
 */
import { describe, expect, it } from "vitest";
import { MAX_TRUNCATION_CONTINUES, TRUNCATION_NOTICE } from "../../src/agent/generation.js";
import { fakeText } from "../../src/models/fake.js";
import type { Message, ModelRequest } from "../../src/models/types.js";
import { memoryBackend } from "./support.js";

const lastUserText = (messages: Message[]) => {
  const last = messages.at(-1);
  return last?.role === "user" && typeof last.content === "string" ? last.content : null;
};

describe("replies cut off at the output token limit", () => {
  it("continue the run with a notice and the final reply answers it; an empty cut-off never reaches the model", async () => {
    const fx = await memoryBackend.fixture();
    fx.provider.enqueue(
      fakeText("", { stopReason: "max_tokens" }),
      fakeText("Half a rep", { stopReason: "max_tokens" }),
      fakeText("The whole report."),
    );
    const w = await fx.open();
    const conv = await w.harness.createConversation({
      tenantId: "t1",
      projectId: "p1",
      agent: { systemPrompt: "s" },
    });
    const settled = await (await conv.submit({ text: "write the report" })).wait();
    expect(settled.status).toBe("done");
    expect(
      (settled as { answer?: { text?: string } }).answer?.text ?? JSON.stringify(settled),
    ).toContain("The whole report.");

    expect(fx.provider.requests).toHaveLength(3);
    const [first, second, third] = fx.provider.requests as [
      ModelRequest,
      ModelRequest,
      ModelRequest,
    ];
    expect(lastUserText(first.messages)).toBe("write the report");
    // the empty cut-off is not replayed; the notice is sent, not stored
    expect(second.messages.filter((m) => m.role === "assistant")).toHaveLength(0);
    expect(lastUserText(second.messages)).toBe(TRUNCATION_NOTICE);
    // a cut-off with text is replayed so the model can pick up where it stopped
    expect(JSON.stringify(third.messages)).toContain("Half a rep");
    expect(lastUserText(third.messages)).toBe(TRUNCATION_NOTICE);
    const stored = (await conv.entries()).filter((e) => e.kind === "user");
    expect(stored).toHaveLength(1);
    await w.crash();
  });

  it("stops continuing after MAX_TRUNCATION_CONTINUES cut-offs in a row and answers with the last reply", async () => {
    const fx = await memoryBackend.fixture();
    const cut = Array.from({ length: MAX_TRUNCATION_CONTINUES + 1 }, (_, i) =>
      fakeText(`part ${i}`, { stopReason: "max_tokens" }),
    );
    fx.provider.enqueue(...cut, fakeText("never asked"));
    const w = await fx.open();
    const conv = await w.harness.createConversation({
      tenantId: "t1",
      projectId: "p1",
      agent: { systemPrompt: "s" },
    });
    const settled = await (await conv.submit({ text: "go" })).wait();
    expect(settled.status).toBe("done");
    expect(fx.provider.requests).toHaveLength(MAX_TRUNCATION_CONTINUES + 1);
    await w.crash();
  });
});
