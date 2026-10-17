/**
 * The `tasks` member of the view (phase 6 U4): host task kinds that supply a
 * `view` appear keyed by task id, as a snapshot and as patches, and a view
 * advanced by patches equals one loaded later.
 */
import { describe, expect, it } from "vitest";
import { fakeText } from "../../src/models/fake.js";
import { defineTask, done, waiting } from "../../src/tasks/define.js";
import { type WatchFrame, WatchHub } from "../../src/watch/stream.js";
import { loadConversationView } from "../../src/watch/view.js";
import { memoryBackend, waitFor } from "../agent/support.js";
import { ClientView, gate } from "./support.js";

// biome-ignore lint/suspicious/noExplicitAny: test access to view internals
type Any = any;

const node = defineTask<{ name: string }, null>({
  kind: "host.node",
  phases: {
    start: (_t, rt) => rt.commit(() => waiting({ signal: "go", phase: "finish" })),
    finish: (t, rt) => rt.commit(() => done({ name: t.input.name })),
  },
  view: (t) => ({ label: t.input.name, attempt: 1 }),
});

const quiet = defineTask<null, null>({
  kind: "host.quiet",
  phases: {
    start: (_t, rt) => rt.commit(() => waiting({ signal: "go", phase: "finish" })),
    finish: (_t, rt) => rt.commit(() => done(null)),
  },
});

const run = defineTask<{ label: string }, { child: string }>({
  kind: "host.run",
  phases: {
    start: (_t, rt) =>
      rt.commit((_tx, ops) => {
        const child = ops.spawn(node, { input: { name: "n1" } });
        ops.spawn(quiet, { input: null });
        return waiting({ on: [child.id], phase: "finish", checkpoint: { child: child.id } });
      }),
    finish: (_t, rt) => rt.commit(() => done({ ok: true })),
  },
  view: (t) => ({ label: t.input.label }),
});

function drain(consumer: { poll(): WatchFrame | null }, client: ClientView): void {
  for (let f = consumer.poll(); f !== null; f = consumer.poll()) client.apply(f);
}

describe("watch view: tasks", () => {
  it("a chat conversation has no host tasks and an empty member", async () => {
    const fx = await memoryBackend.fixture();
    fx.provider.enqueue(fakeText("hi"));
    const w = await fx.open({ tasks: [run, node, quiet] });
    const conv = await w.harness.createConversation({ tenantId: "t1", projectId: "p1" });
    expect((await loadConversationView(w.harness, conv.id)).tasks).toEqual({});
    await (await conv.submit({ text: "x" })).wait();
    expect((await loadConversationView(w.harness, conv.id)).tasks).toEqual({});
    await w.crash();
  });

  it("host tasks with a view definition appear; a view loaded earlier plus patches equals one loaded later", async () => {
    const fx = await memoryBackend.fixture();
    const w = await fx.open({ tasks: [run, node, quiet] });
    const hub = new WatchHub(w.harness);
    const conv = await w.harness.createConversation({ tenantId: "t1", projectId: "p1" });
    const consumer = await hub.watch(conv.id);
    const client = new ClientView();
    client.apply((await consumer.next()) as WatchFrame);
    expect((client.view as Any).tasks).toEqual({});

    const root = await w.harness.runtime.spawn(run, {
      conversationId: conv.id,
      input: { label: "Run A" },
    });
    await waitFor(() =>
      w.harness.runtime.tasks().some((t) => t.kind === "host.node" && t.status === "waiting"),
    );
    const child = w.harness.runtime.tasks().find((t) => t.kind === "host.node");
    expect(child).toBeDefined();
    const live = await loadConversationView(w.harness, conv.id);
    expect(Object.keys(live.tasks).sort()).toEqual([root.id, (child as Any).id].sort());
    expect(live.tasks[root.id]).toMatchObject({
      kind: "host.run",
      parentId: null,
      status: "waiting",
      label: "Run A",
    });
    expect(live.tasks[(child as Any).id]).toMatchObject({
      kind: "host.node",
      parentId: root.id,
      status: "waiting",
      phase: "finish",
      label: "n1",
      attempt: 1,
    });
    expect(live.tasks[(child as Any).id]?.endedAt).toBeUndefined();
    drain(consumer, client);
    expect((client.view as Any).tasks).toEqual(live.tasks);

    const hidden = w.harness.runtime.tasks().find((t) => t.kind === "host.quiet") as Any;
    await w.harness.runtime.signal(hidden.id, "go", null);
    await w.harness.runtime.signal((child as Any).id, "go", null);
    await w.harness.runtime.waitForTask(root.id);
    await w.harness.waitForIdle();
    drain(consumer, client);
    const final = await loadConversationView(w.harness, conv.id);
    expect(client.view).toEqual(JSON.parse(JSON.stringify(final)));
    expect(final.tasks[root.id]).toMatchObject({ status: "done", phase: "finish" });
    expect(typeof final.tasks[root.id]?.endedAt).toBe("string");
    expect(final.tasks[(child as Any).id]?.status).toBe("done");

    // A snapshot taken now (a late watcher) is the same view.
    const late = await hub.watch(conv.id);
    const lateClient = new ClientView();
    lateClient.apply((await late.next()) as WatchFrame);
    expect(lateClient.view).toEqual(client.view);
    await w.crash();
  });

  it("an aborted and a faulted task carry their status and error", async () => {
    const fx = await memoryBackend.fixture();
    const boom = defineTask<null, null>({
      kind: "host.boom",
      phases: {
        start: async () => {
          throw new Error("kaput");
        },
      },
      view: () => ({}),
    });
    const hold = defineTask<null, null>({
      kind: "host.hold",
      phases: { start: (_t, rt) => rt.commit(() => waiting({ signal: "never" })) },
      view: () => ({ label: "hold" }),
    });
    const w = await fx.open({ tasks: [boom, hold] });
    const conv = await w.harness.createConversation({ tenantId: "t1", projectId: "p1" });
    const b = await w.harness.runtime.spawn(boom, { conversationId: conv.id, input: null });
    const h = await w.harness.runtime.spawn(hold, { conversationId: conv.id, input: null });
    await w.harness.runtime.waitForTask(b.id);
    await waitFor(() => w.harness.runtime.task(h.id)?.status === "waiting");
    await w.harness.runtime.abort(h.id, "stop it");
    await w.harness.runtime.waitForTask(h.id);
    const view = await loadConversationView(w.harness, conv.id);
    expect(view.tasks[b.id]).toMatchObject({
      status: "faulted",
      error: expect.stringContaining("kaput"),
    });
    expect(view.tasks[h.id]).toMatchObject({ status: "aborted", error: "stop it" });
    await w.crash();
  });

  it("a waiting task shows its deadline as waitUntil", async () => {
    const fx = await memoryBackend.fixture();
    const sleeper = defineTask<null, null>({
      kind: "host.sleeper",
      phases: {
        start: (_t, rt) => rt.commit(() => waiting({ until: 2_000_000, phase: "end" })),
        end: (_t, rt) => rt.commit(() => done(null)),
      },
      view: () => ({}),
    });
    const w = await fx.open({ tasks: [sleeper] });
    const conv = await w.harness.createConversation({ tenantId: "t1", projectId: "p1" });
    const t = await w.harness.runtime.spawn(sleeper, { conversationId: conv.id, input: null });
    await waitFor(() => w.harness.runtime.task(t.id)?.status === "waiting");
    const view = await loadConversationView(w.harness, conv.id);
    expect(view.tasks[t.id]?.waitUntil).toBe(new Date(2_000_000).toISOString());
    await w.crash();
  });

  it("a worker restart mid-run adds exactly one run_resumed entry for a host-task run", async () => {
    const fx = await memoryBackend.fixture();
    const g = gate();
    const slow = defineTask<null, null>({
      kind: "host.slow",
      phases: {
        start: async (_t, rt) => {
          await g.promise;
          return rt.commit(() => done(null));
        },
      },
      view: () => ({}),
    });
    const w1 = await fx.open({ tasks: [slow] });
    const conv = await w1.harness.createConversation({ tenantId: "t1", projectId: "p1" });
    const t = await w1.harness.runtime.spawn(slow, { conversationId: conv.id, input: null });
    await waitFor(() => w1.harness.runtime.task(t.id)?.status === "running");
    expect(await w1.harness.conversation(conv.id).entries()).toEqual([]);
    await w1.crash();

    const w2 = await fx.open({ tasks: [slow] });
    const entries = await w2.harness.conversation(conv.id).entries();
    expect(entries.filter((e) => e.kind === "run_resumed")).toHaveLength(1);
    expect(entries.find((e) => e.kind === "run_resumed")?.payload).toMatchObject({ runId: t.id });
    g.open();
    await w2.harness.runtime.waitForTask(t.id);
    // No further restart: still exactly one.
    expect(
      (await w2.harness.conversation(conv.id).entries()).filter((e) => e.kind === "run_resumed"),
    ).toHaveLength(1);
    await w2.crash();
  });
});
