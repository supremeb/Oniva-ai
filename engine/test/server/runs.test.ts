import { afterEach, describe, expect, it } from "vitest";
import { Harness } from "../../src/agent/harness.js";
import { ManualClock } from "../../src/core/clock.js";
import { FakeProvider } from "../../src/models/fake.js";
import { ModelRegistry } from "../../src/models/stream.js";
import { Session } from "../../src/session/session.js";
import { MemoryStorageProvider } from "../../src/storage/memory.js";
import { defineTask, done, waiting } from "../../src/tasks/define.js";
import { allowAll, memoryBackend, waitFor } from "../agent/support.js";
import { AUTH, call, serve } from "./support.js";

const aborts: string[] = [];

/** A host task that waits on the signal `go`. */
function gateTask() {
  return defineTask<null, null>({
    kind: "test.gate",
    phases: {
      wait: async (_task, rt) => rt.commit(() => waiting({ signal: "go", phase: "finish" })),
      finish: async (task, rt) => rt.commit(() => done({ signal: task.signals.go ?? null })),
    },
    onAbort: async () => {
      aborts.push("gate");
    },
  });
}

/** A host task that spawns a child and waits on it. */
function parentTask() {
  return defineTask<null, { child: string }>({
    kind: "test.parent",
    phases: {
      start: async (_task, rt) =>
        rt.commit((_tx, ops) => {
          const child = ops.spawn(gateTask(), { input: null });
          return waiting({ on: [child.id], phase: "collect", checkpoint: { child: child.id } });
        }),
      collect: async (_task, rt) => rt.commit(() => done({ wrapped: true })),
    },
    onAbort: async () => {
      aborts.push("parent");
    },
  });
}

/** A host task: two phases with a deadline wait between them. */
function waitTask() {
  return defineTask<{ until: number }, null>({
    kind: "test.wait",
    phases: {
      start: async (task, rt) =>
        rt.commit(() => waiting({ until: task.input.until, phase: "finish" })),
      finish: async (_task, rt) => rt.commit(() => done({ ok: true })),
    },
  });
}

const cleanups: Array<() => Promise<void>> = [];
afterEach(async () => {
  for (const fn of cleanups.splice(0).reverse()) await fn();
});

async function start(
  workerOptions: Parameters<Awaited<ReturnType<typeof memoryBackend.fixture>>["open"]>[0] = {},
) {
  const fx = await memoryBackend.fixture();
  const w = await fx.open({ settings: { progressIntervalMs: 0 }, ...workerOptions });
  const srv = await serve(w.harness);
  cleanups.push(() => w.crash());
  cleanups.push(() => srv.close());
  return { fx, w, srv, url: srv.url };
}

function getTask(url: string, conversationId: string, taskId: string) {
  return call(url, "GET", `/v1/conversations/${conversationId}/tasks/${taskId}`, {
    headers: AUTH,
  });
}

describe("engine runs and host tasks (U3)", () => {
  it("runs a host task registered via tasks through POST /v1/runs to done", async () => {
    const { url, fx } = await start({ tasks: [waitTask()] });
    const started = await call(url, "POST", "/v1/runs", {
      headers: AUTH,
      body: { id: "run-1", kind: "test.wait", input: { until: fx.clock.now() + 5 } },
    });
    expect(started.status).toBe(201);
    expect(started.body.conversationId).toBe("run-1");
    const taskId = started.body.taskId as string;
    expect(taskId).toMatch(/^task/);

    fx.clock.advance(5);
    await waitFor(async () => (await getTask(url, "run-1", taskId)).body.status === "terminal");
    const final = await getTask(url, "run-1", taskId);
    expect(final.body).toEqual({
      id: taskId,
      kind: "test.wait",
      status: "terminal",
      phase: "finish",
      outcome: { type: "done", value: { ok: true } },
    });
  });

  it("a repeat POST /v1/runs with the same id spawns once and returns the same run", async () => {
    const { url, w } = await start({ tasks: [waitTask()] });
    const body = { id: "run-1", kind: "test.wait", input: { until: 10_000_000 } };
    const first = await call(url, "POST", "/v1/runs", { headers: AUTH, body });
    const second = await call(url, "POST", "/v1/runs", { headers: AUTH, body });
    expect(first.status).toBe(201);
    expect(second.status).toBe(200);
    expect(second.body).toEqual(first.body);
    const roots = w.harness.runtime
      .tasks({ conversationId: "run-1" })
      .filter((t) => t.kind === "test.wait" && t.parentTaskId === null);
    expect(roots).toHaveLength(1);
  });

  it("rejects an unknown task kind and a missing id with 400", async () => {
    const { url } = await start({ tasks: [waitTask()] });
    const unknown = await call(url, "POST", "/v1/runs", {
      headers: AUTH,
      body: { id: "run-x", kind: "no.such.kind" },
    });
    expect(unknown.status).toBe(400);
    expect(unknown.body.error).toBe("bad_request");
    const noId = await call(url, "POST", "/v1/runs", {
      headers: AUTH,
      body: { kind: "test.wait" },
    });
    expect(noId.status).toBe(400);
  });

  it("signals a waiting host task; a duplicate, a finished task and a stranger are rejected", async () => {
    const { url, w } = await start({ tasks: [gateTask(), waitTask()] });
    const started = await call(url, "POST", "/v1/runs", {
      headers: AUTH,
      body: { id: "run-g", kind: "test.gate" },
    });
    const taskId = started.body.taskId as string;
    await waitFor(() => w.harness.runtime.task(taskId)?.status === "waiting");
    const path = `/v1/conversations/run-g/tasks/${taskId}/signal`;

    const noKey = await call(url, "POST", path, { headers: AUTH, body: {} });
    expect(noKey.status).toBe(400);

    const other = await call(url, "POST", "/v1/conversations", { headers: AUTH, body: {} });
    const stranger = await call(
      url,
      "POST",
      `/v1/conversations/${other.body.conversationId}/tasks/${taskId}/signal`,
      { headers: AUTH, body: { key: "go" } },
    );
    expect(stranger.status).toBe(404);

    const ok = await call(url, "POST", path, {
      headers: AUTH,
      body: { key: "go", payload: { n: 1 } },
    });
    expect(ok.status).toBe(200);
    await waitFor(() => w.harness.runtime.task(taskId)?.status === "terminal");
    const final = await getTask(url, "run-g", taskId);
    expect(final.body.outcome).toEqual({ type: "done", value: { signal: { n: 1 } } });

    const dup = await call(url, "POST", path, { headers: AUTH, body: { key: "go" } });
    expect(dup.status).toBe(409);
    expect(dup.body.error).toBe("signal_rejected");
    const late = await call(url, "POST", path, { headers: AUTH, body: { key: "again" } });
    expect(late.status).toBe(409);
  });

  it("returns 404 for a task of another conversation and an unknown task", async () => {
    const { url } = await start({ tasks: [gateTask()] });
    const a = await call(url, "POST", "/v1/runs", {
      headers: AUTH,
      body: { id: "run-a", kind: "test.gate" },
    });
    const b = await call(url, "POST", "/v1/runs", {
      headers: AUTH,
      body: { id: "run-b", kind: "test.gate" },
    });
    const missing = await getTask(url, "run-a", "task_nope");
    expect(missing.status).toBe(404);
    const cross = await getTask(url, "run-b", a.body.taskId as string);
    expect(cross.status).toBe(404);
    const own = await getTask(url, "run-a", a.body.taskId as string);
    expect(own.status).toBe(200);
    expect(own.body.id).toBe(a.body.taskId);
    expect(b.status).toBe(201);
  });

  it("health reports apiVersion and the registered task kinds", async () => {
    const { url } = await start({ tasks: [waitTask()] });
    const withHost = await call(url, "GET", "/v1/health");
    expect(withHost.body.apiVersion).toBe(1);
    expect(withHost.body.taskKinds).toEqual([
      "agent.compaction",
      "agent.generation",
      "agent.tool",
      "test.wait",
    ]);

    const plain = await start();
    const builtins = await call(plain.url, "GET", "/v1/health");
    expect(builtins.body.taskKinds).toEqual(["agent.compaction", "agent.generation", "agent.tool"]);
  });

  it("abort of the container conversation aborts the host task and its children, running onAbort", async () => {
    aborts.length = 0;
    const { url, w } = await start({ tasks: [parentTask(), gateTask()] });
    const started = await call(url, "POST", "/v1/runs", {
      headers: AUTH,
      body: { id: "run-p", kind: "test.parent" },
    });
    const parentId = started.body.taskId as string;
    await waitFor(() => w.harness.runtime.task(parentId)?.status === "waiting");

    const aborted = await call(url, "POST", "/v1/conversations/run-p/abort", {
      headers: AUTH,
      body: { reason: "stop" },
    });
    expect(aborted.status).toBe(200);
    const final = await w.harness.runtime.waitForTask(parentId);
    expect(final.outcome).toEqual({ type: "aborted", reason: "stop" });
    expect(aborts.sort()).toEqual(["gate", "parent"]);
  });

  it("throws at open when a host task kind duplicates a built-in", async () => {
    const provider = new MemoryStorageProvider();
    const session = await Session.open(await provider.open("sess-dup"), {
      clock: new ManualClock(1),
    });
    const models = new ModelRegistry().register(new FakeProvider());
    const dup = defineTask<null, null>({
      kind: "agent.tool",
      phases: { start: async (_task, rt) => rt.commit(() => done(null)) },
    });
    await expect(
      Harness.open(session, {
        models,
        tools: [],
        policy: allowAll,
        clock: new ManualClock(1),
        tasks: [dup],
      }),
    ).rejects.toThrow(/already registered/);
    await session.close();
  });
});
