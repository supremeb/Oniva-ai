import { describe, expect, it } from "vitest";
import { fakeText, fakeToolCall } from "../../src/models/fake.js";
import { gate, gatedTool } from "../watch/support.js";
import { allowAll, memoryBackend, waitFor } from "./support.js";

// biome-ignore lint/suspicious/noExplicitAny: test access to payloads
type Any = any;

async function open(tools: ReturnType<typeof gatedTool>[] = []) {
  const fx = await memoryBackend.fixture();
  const w = await fx.open({ policy: allowAll, tools });
  return { fx, w };
}

describe("user entry requestId", () => {
  it("carries the caller's requestId and dedupes a repeat", async () => {
    const { fx, w } = await open();
    const conv = await w.harness.createConversation({ tenantId: "t", projectId: "p" });
    fx.provider.enqueue(fakeText("hi"));
    const a = await conv.submit({ text: "hello", requestId: "req-1" });
    const b = await conv.submit({ text: "hello", requestId: "req-1" });
    expect(b.id).toBe(a.id);
    await a.wait();
    const users = (await conv.entries()).filter((e) => e.kind === "user");
    expect(users).toHaveLength(1);
    expect(users[0]?.payload as Any).toMatchObject({ requestId: "req-1", submissionId: a.id });
    await w.crash();
  });

  it("falls back to the submission id when no requestId is given", async () => {
    const { fx, w } = await open();
    const conv = await w.harness.createConversation({ tenantId: "t", projectId: "p" });
    fx.provider.enqueue(fakeText("hi"));
    const sub = await conv.submit({ text: "hello" });
    await sub.wait();
    const user = (await conv.entries()).find((e) => e.kind === "user");
    expect(user).toBeDefined();
    expect(((user as Any).payload as Any).requestId).toBe(sub.id);
    await w.crash();
  });

  it("carries the requestId of a steer placed mid-run and of a display write", async () => {
    const gates: [ReturnType<typeof gate>, ReturnType<typeof gate>] = [gate(), gate()];
    const { fx, w: w2 } = await open([gatedTool(gates)]);
    const conv = await w2.harness.createConversation({
      tenantId: "t",
      projectId: "p",
      agent: { tools: ["work"] },
    });
    fx.provider.enqueue(fakeToolCall("work", {}, { id: "c1" }), fakeText("done"));
    const first = await conv.submit({ text: "go", requestId: "r-first" });
    await waitFor(async () => conv.busy());
    const steer = await conv.submit({ text: "also", requestId: "r-steer", whenBusy: "steer" });
    await conv.submit({ text: "note", requestId: "r-note", type: "write" });
    gates[0].open();
    gates[1].open();
    await first.wait();
    await steer.wait();
    const ids = (await conv.entries())
      .filter((e) => e.kind === "user" || e.kind === "display")
      .map((e) => (e.payload as Any).requestId)
      .sort();
    expect(ids).toEqual(["r-first", "r-note", "r-steer"]);
    await w2.crash();
  });
});
