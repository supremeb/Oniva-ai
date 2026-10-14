import { describe, expect, it } from "vitest";
import { fakeToolCall } from "../../src/models/fake.js";
import { type WatchFrame, WatchHub } from "../../src/watch/stream.js";
import { loadConversationView, UnknownConversation } from "../../src/watch/view.js";
import { memoryBackend, waitFor } from "../agent/support.js";
import { ClientView, gate, gatedTool } from "./support.js";

// biome-ignore lint/suspicious/noExplicitAny: test access to view internals
type Any = any;

async function setup(options: { retainFrames?: number; maxLag?: number } = {}) {
  const fx = await memoryBackend.fixture();
  const w = await fx.open({ settings: { progressIntervalMs: 0 } });
  const hub = new WatchHub(w.harness, options);
  const conv = await w.harness.createConversation({ tenantId: "t1", projectId: "p1" });
  return { fx, w, hub, conv };
}

function drain(consumer: { poll(): WatchFrame | null }, client: ClientView): number {
  let n = 0;
  for (let f = consumer.poll(); f !== null; f = consumer.poll()) {
    client.apply(f);
    n++;
  }
  return n;
}

describe("watch stream", () => {
  it("a watcher attached mid-run gets a snapshot, then patches that reproduce the final view", async () => {
    const fx = await memoryBackend.fixture();
    const gates: [ReturnType<typeof gate>, ReturnType<typeof gate>] = [gate(), gate()];
    fx.provider.enqueue(fakeToolCall("work", {}, { id: "c1", text: "Working on it" }), {
      steps: [{ type: "text", text: "All done, the work is finished.", chunkSize: 4 }],
    });
    const w = await fx.open({ tools: [gatedTool(gates)], settings: { progressIntervalMs: 0 } });
    const hub = new WatchHub(w.harness);
    const conv = await w.harness.createConversation({
      tenantId: "t1",
      projectId: "p1",
      agent: { systemPrompt: "s", tools: ["work"] },
    });
    const sub = await conv.submit({ text: "go" });
    await waitFor(async () => {
      const v = await loadConversationView(w.harness.session, conv.id);
      return Object.values(v.toolOutput).some((o) => o.text.includes("a"));
    });

    const consumer = await hub.watch(conv.id);
    const client = new ClientView();
    const first = await consumer.next();
    expect(first && "snapshot" in first).toBe(true);
    client.apply(first as WatchFrame);
    expect(Object.values((client.view as Any).toolOutput).length).toBe(1);
    expect((client.view as Any).entries.length).toBeGreaterThan(0);

    gates[0].open();
    await waitFor(async () => {
      const v = await loadConversationView(w.harness.session, conv.id);
      return Object.values(v.toolOutput).some((o) => o.text.includes("b"));
    });
    gates[1].open();
    expect((await sub.wait()).status).toBe("done");
    await w.harness.waitForIdle();

    const patches = drain(consumer, client);
    expect(patches).toBeGreaterThan(2);
    expect(client.frames.slice(1).every((f) => "patch" in f)).toBe(true);
    const final = await loadConversationView(w.harness.session, conv.id);
    expect(client.view).toEqual(final);
    expect(client.cursor).toBeLessThanOrEqual((await hub.current(conv.id)).cursor);
    expect((await hub.current(conv.id)).view).toEqual(final);
    expect(final.entries.map((e) => e.kind)).toContain("tool_result");
    expect(final.entries.at(-1)?.kind).toBe("assistant");
    expect(final.live).toEqual({ runId: null, taskId: null, message: null });
    consumer.close();
    await hub.close();
    await w.crash();
  });

  it("only publishes frames for commits that touch the conversation, after they commit", async () => {
    const { w, hub, conv } = await setup();
    const other = await w.harness.createConversation({ tenantId: "t1", projectId: "p1" });
    const consumer = await hub.watch(conv.id);
    const client = new ClientView();
    client.apply((await consumer.next()) as WatchFrame);
    await other.submit({ text: "elsewhere", type: "write" });
    expect(consumer.poll()).toBeNull();
    await conv.submit({ text: "here", type: "write" });
    const frame = consumer.poll() as Any;
    expect(frame.patch).toEqual([
      { op: "add", path: "/entries/0", value: expect.objectContaining({ kind: "display" }) },
    ]);
    expect(frame.cursor).toBe(w.harness.session.lastSeq);
    client.apply(frame);
    expect(client.view).toEqual(await loadConversationView(w.harness.session, conv.id));
    await hub.close();
    await w.crash();
  });

  it("resuming inside the window delivers only missed frames; outside it, a snapshot", async () => {
    const { w, hub, conv } = await setup({ retainFrames: 5 });
    const first = await hub.watch(conv.id);
    const client = new ClientView();
    client.apply((await first.next()) as WatchFrame);
    for (let i = 0; i < 3; i++) await conv.submit({ text: `m${i}`, type: "write" });
    drain(first, client);
    first.close();
    const resumeAt = client.cursor;

    for (let i = 3; i < 6; i++) await conv.submit({ text: `m${i}`, type: "write" });
    const resumed = await hub.watch(conv.id, { cursor: resumeAt });
    const frames: WatchFrame[] = [];
    for (let f = resumed.poll(); f !== null; f = resumed.poll()) frames.push(f);
    expect(frames).toHaveLength(3);
    expect(frames.every((f) => "patch" in f && f.cursor > resumeAt)).toBe(true);
    for (const f of frames) client.apply(f);
    expect(client.view).toEqual(await loadConversationView(w.harness.session, conv.id));

    // Resuming at the current cursor delivers nothing until the next commit.
    const idle = await hub.watch(conv.id, { cursor: client.cursor });
    expect(idle.poll()).toBeNull();

    // An early cursor has fallen out of the 5-frame window.
    const late = await hub.watch(conv.id, { cursor: (client.frames[0] as WatchFrame).cursor });
    const snap = late.poll() as Any;
    expect(snap.snapshot).toEqual(await loadConversationView(w.harness.session, conv.id));
    expect(late.poll()).toBeNull();
    // A cursor from the future (another process) also gets a snapshot.
    const future = await hub.watch(conv.id, { cursor: 10_000 });
    expect("snapshot" in (future.poll() as WatchFrame)).toBe(true);
    await hub.close();
    await w.crash();
  });

  it("a slow consumer never blocks commits and gets a snapshot once >100 frames behind", async () => {
    const { w, hub, conv } = await setup();
    const slow = await hub.watch(conv.id);
    const steady = await hub.watch(conv.id);
    const client = new ClientView();
    client.apply((await steady.next()) as WatchFrame);
    const lagging = new ClientView();
    lagging.apply((await slow.next()) as WatchFrame);
    const resumeAt = lagging.cursor;

    // 130 commits complete while `slow` reads nothing.
    for (let i = 0; i < 130; i++) {
      await conv.submit({ text: `m${i}`, type: "write" });
      drain(steady, client);
    }
    expect(slow.pending).toBeLessThanOrEqual(101);
    const next = slow.poll() as Any;
    expect(next.snapshot).toBeDefined();
    expect(next.cursor).toBe(w.harness.session.lastSeq);
    lagging.apply(next);
    expect(slow.poll()).toBeNull();
    const final = await loadConversationView(w.harness.session, conv.id);
    expect(lagging.view).toEqual(final);
    expect(client.view).toEqual(final);
    expect(client.frames.filter((f) => "snapshot" in f)).toHaveLength(1);

    // Resuming >100 frames back (inside the 500-frame window) also gets a snapshot.
    const resumed = await hub.watch(conv.id, { cursor: resumeAt });
    expect("snapshot" in (resumed.poll() as WatchFrame)).toBe(true);
    await hub.close();
    await w.crash();
  });

  it("next() waits for a frame and resolves null once closed", async () => {
    const { w, hub, conv } = await setup();
    const consumer = await hub.watch(conv.id, { cursor: w.harness.session.lastSeq });
    const pending = consumer.next();
    await conv.submit({ text: "x", type: "write" });
    expect("patch" in ((await pending) as WatchFrame)).toBe(true);
    const ended = consumer.next();
    consumer.close();
    expect(await ended).toBeNull();
    await hub.close();
    await w.crash();
  });

  it("rejects an unknown conversation", async () => {
    const { w, hub } = await setup();
    await expect(hub.watch("conv_missing")).rejects.toBeInstanceOf(UnknownConversation);
    await hub.close();
    await w.crash();
  });

  it("validates its options", async () => {
    const { w } = await setup();
    expect(() => new WatchHub(w.harness, { retainFrames: 0 })).toThrow(RangeError);
    expect(() => new WatchHub(w.harness, { maxLag: -1 })).toThrow(RangeError);
    await w.crash();
  });
});
