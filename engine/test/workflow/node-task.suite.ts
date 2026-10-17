/**
 * The `workflow_node` task suite (plan 2026-10-17-003, U11), run against every
 * storage backend with the fake workflow backend (`fake-workflow-backend.ts`).
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { agentConfigDoc } from "../../src/agent/prompt.js";
import { fakeText } from "../../src/models/fake.js";
import type { TaskRecord } from "../../src/storage/types.js";
import { defineTask, done, SignalRejected, taskState, waiting } from "../../src/tasks/define.js";
import { createNodeGateway } from "../../src/workflow/node-gateway.js";
import {
  createWorkflowNodeTask,
  FANOUT_PARKED_SIGNAL,
  FANOUT_RESULT_SIGNAL,
  WORKFLOW_NODE_KIND,
  type WorkflowNodeInput,
  type WorkflowNodeValue,
} from "../../src/workflow/node-task.js";
import {
  type AgentBackend,
  type AgentFixture,
  type AgentWorker,
  waitFor,
} from "../agent/support.js";
import { FakeWorkflowBackend, fixture } from "../support/fake-workflow-backend.js";

const HOUR = 60 * 60 * 1000;
const KEY = /^run-1:n1:r\d+:a\d+:(prepare|finish)$/;

const POLICY = { maxRetries: 0, backoffMs: 0, timeoutMs: 0, onError: "stop" } as const;

function input(over: Partial<WorkflowNodeInput> = {}): WorkflowNodeInput {
  return {
    version: 1,
    runId: "run-1",
    runAttempt: 0,
    nodeId: "n1",
    nodeType: "code",
    policy: { ...POLICY },
    ...over,
  };
}

const llm = (over: Record<string, unknown> = {}) => ({
  ...fixture("prepare.llm.valid"),
  tools: [],
  ...over,
});

const valueOfDone = (task: TaskRecord): WorkflowNodeValue => {
  expect(task.outcome?.type).toBe("done");
  return (task.outcome as { value: unknown }).value as WorkflowNodeValue;
};

/** A host task that waits for the fan-out `parked` signal, standing in for the run's gate. */
const gateTask = () =>
  defineTask<null, null>({
    kind: "test_gate",
    phases: {
      start: async (_t, rt) =>
        rt.commit(() => waiting({ signal: FANOUT_PARKED_SIGNAL, phase: "parked" })),
      parked: async (task, rt) =>
        rt.commit(() => done({ digest: task.signals[FANOUT_PARKED_SIGNAL] ?? null })),
    },
  });

export function defineNodeTaskSuite(backend: AgentBackend): void {
  describe(`workflow node task (${backend.name})`, () => {
    let fx: AgentFixture;
    let be: FakeWorkflowBackend;
    const workers: AgentWorker[] = [];
    const aborted: Array<{ phase: string; childrenTerminal: boolean | null }> = [];

    beforeEach(async () => {
      fx = await backend.fixture();
      be = await FakeWorkflowBackend.start();
      aborted.length = 0;
    });

    afterEach(async () => {
      for (const w of workers.splice(0)) await w.crash().catch(() => {});
      await be.close();
    });

    /** Open a worker whose node task talks to the fake backend. */
    async function open(): Promise<AgentWorker> {
      const holder: { w?: AgentWorker } = {};
      const gateway = createNodeGateway({
        baseUrl: be.baseUrl,
        serviceToken: be.serviceToken,
        fence: () => null,
        clock: fx.clock,
        retry: { attempts: 1 },
      });
      const node = createWorkflowNodeTask({
        gateway,
        onAbort: (task) => {
          const child = task.checkpoint?.child;
          const w = holder.w;
          aborted.push({
            phase: task.phase,
            childrenTerminal:
              child && w
                ? w.harness.runtime
                    .tasks({ conversationId: child.conversationId })
                    .every((t) => t.status === "terminal")
                : null,
          });
        },
      });
      const w = await fx.open({ tasks: [node, gateTask()] });
      holder.w = w;
      workers.push(w);
      return w;
    }

    async function spawn(
      w: AgentWorker,
      over: Partial<WorkflowNodeInput> = {},
      checkpoint?: unknown,
    ): Promise<{ id: string; conversationId: string }> {
      const conv = await w.harness.createConversation({ tenantId: "t1", projectId: "p1" });
      const def = w.harness.runtime.registry.get(WORKFLOW_NODE_KIND);
      if (!def) throw new Error("workflow_node is not registered");
      const task = await w.harness.runtime.spawn(def, {
        conversationId: conv.id,
        input: input(over) as never,
        ...(checkpoint === undefined ? {} : { checkpoint: checkpoint as never }),
      });
      return { id: task.id, conversationId: conv.id };
    }

    const statusOf = (w: AgentWorker, id: string) => w.harness.runtime.task(id)?.status;

    it("a final prepare completes the node once with the backend's result", async () => {
      const w = await open();
      const { id } = await spawn(w);
      const value = valueOfDone(await w.harness.runtime.waitForTask(id));
      expect(value).toMatchObject({
        nodeId: "n1",
        status: "success",
        output: { text: "all done" },
        tokensUsed: 12,
        attempts: 1,
        selectedHandles: ["true"],
      });
      expect(be.keys()).toEqual(["run-1:n1:r0:a0:prepare"]);
      expect(be.total("n1", "prepare")).toBe(1);
      expect(be.calls("finish")).toHaveLength(0);
    });

    it("a restart between prepare and commit re-asks with the same key and gets the stored outcome", async () => {
      be.script("n1", "prepare", { hangAfterEffect: fixture("prepare.final.valid") });
      const w1 = await open();
      const { id } = await spawn(w1);
      await waitFor(() => be.executions("run-1:n1:r0:a0:prepare") === 1);
      await w1.crash();

      const w2 = await open();
      const value = valueOfDone(await w2.harness.runtime.waitForTask(id));
      expect(value.status).toBe("success");
      expect(be.executions("run-1:n1:r0:a0:prepare")).toBe(1);
      expect(be.keys("prepare")).toEqual(["run-1:n1:r0:a0:prepare", "run-1:n1:r0:a0:prepare"]);
    });

    it("keys follow {runId}:{nodeId}:r{n}:a{n}:{phase}; runAttempt makes a different key", async () => {
      const w = await open();
      const a = await spawn(w, { runAttempt: 0 });
      const b = await spawn(w, { runAttempt: 1 });
      await w.harness.runtime.waitForTask(a.id);
      await w.harness.runtime.waitForTask(b.id);
      const keys = be.keys();
      expect(keys.sort()).toEqual(["run-1:n1:r0:a0:prepare", "run-1:n1:r1:a0:prepare"]);
      for (const key of keys) expect(key).toMatch(KEY);
      // The backend ran both: a different runAttempt is not a replay.
      expect(be.total("n1", "prepare")).toBe(2);
      // The headers carry the key.
      expect(
        be
          .calls("prepare")
          .map((r) => r.headers["idempotency-key"])
          .sort(),
      ).toEqual(keys);
      expect(be.calls("prepare")[0]?.headers.authorization).toBe(`Bearer ${be.serviceToken}`);
    });

    it("an llm prepare spawns one child conversation whose answer reaches finish", async () => {
      be.script("n1", "prepare", { body: llm() });
      be.script("n1", "finish", { body: fixture("prepare.final.valid") });
      fx.provider.enqueue(fakeText("urgent"));
      const w = await open();
      const { id, conversationId } = await spawn(w);
      const value = valueOfDone(await w.harness.runtime.waitForTask(id));
      expect(value.status).toBe("success");

      const all = (await w.harness.session.reader.listConversations({ limit: 100 })).items;
      const children = all.filter((c) => c.ownerTaskId === id);
      expect(children).toHaveLength(1);
      const child = children[0];
      expect(await w.harness.session.document(agentConfigDoc, child?.id as string)).toMatchObject({
        systemPrompt: expect.stringContaining("You classify."),
        tools: [],
        depth: 1,
        metadata: { modelTier: "mid" },
        maxRounds: 3,
      });
      const subs = await w.harness.session.reader.listSubmissions(child?.id as string, {
        limit: 10,
      });
      expect(subs.items).toHaveLength(1);
      expect(subs.items[0]).toMatchObject({ requestId: `node:${id}`, status: "done" });
      expect(all.map((c) => c.id)).toContain(conversationId);

      expect(be.calls("finish")).toHaveLength(1);
      expect(be.calls("finish")[0]?.body).toEqual({
        contractVersion: 1,
        kind: "llm",
        answer: "urgent",
      });
      expect(be.keys("finish")).toEqual(["run-1:n1:r0:a0:finish"]);
    });

    it("a restart while the child works does not create a second child", async () => {
      be.script("n1", "prepare", { body: llm() });
      fx.provider.enqueue(
        {
          steps: [
            { type: "delay", ms: HOUR },
            { type: "text", text: "slow" },
          ],
        },
        fakeText("answer"),
      );
      const w1 = await open();
      const { id } = await spawn(w1);
      await waitFor(() => w1.harness.runtime.task(id)?.phase === "awaitChild");
      await waitFor(() => fx.provider.requests.length === 1);
      await w1.crash();

      const w2 = await open();
      const value = valueOfDone(await w2.harness.runtime.waitForTask(id));
      expect(value.status).toBe("success");
      const all = (await w2.harness.session.reader.listConversations({ limit: 100 })).items;
      expect(all.filter((c) => c.ownerTaskId === id)).toHaveLength(1);
      expect(be.total("n1", "prepare")).toBe(1);
      expect(be.calls("finish")[0]?.body).toMatchObject({ answer: "answer" });
    });

    it("a wait of 2 h completes when the clock passes the deadline, not before", async () => {
      be.script("n1", "prepare", { body: fixture("prepare.wait.duration.valid") });
      const w = await open();
      const { id } = await spawn(w);
      await w.harness.runtime.waitForIdle();
      expect(statusOf(w, id)).toBe("waiting");
      fx.clock.advance(2 * HOUR - 1000);
      await w.harness.runtime.waitForIdle();
      expect(statusOf(w, id)).toBe("waiting");
      fx.clock.advance(1000);
      const value = valueOfDone(await w.harness.runtime.waitForTask(id));
      expect(value.status).toBe("success");
      expect(be.calls("finish")[0]?.body).toEqual({
        contractVersion: 1,
        kind: "wait",
        wakeReason: "deadline",
      });
    });

    it("a wait with a signal key wakes on the signal and hands the payload to finish", async () => {
      be.script("n1", "prepare", {
        body: {
          contractVersion: 1,
          kind: "wait",
          untilEpochMs: fx.clock.now() + 24 * HOUR,
          signal: "approval",
        },
      });
      const w = await open();
      const { id } = await spawn(w);
      await w.harness.runtime.waitForIdle();
      expect(statusOf(w, id)).toBe("waiting");
      await w.harness.runtime.signal(id, "approval", { approved: true });
      const value = valueOfDone(await w.harness.runtime.waitForTask(id));
      expect(value.status).toBe("success");
      expect(be.calls("finish")[0]?.body).toEqual({
        contractVersion: 1,
        kind: "wait",
        wakeReason: "signal",
        signalPayload: { approved: true },
      });
    });

    it("an SLA earlier than the wait deadline wakes a signal wait with the deadline reason", async () => {
      be.script("n1", "prepare", {
        body: {
          contractVersion: 1,
          kind: "wait",
          untilEpochMs: fx.clock.now() + 24 * HOUR,
          signal: "approval",
          slaUntilEpochMs: fx.clock.now() + HOUR,
        },
      });
      const w = await open();
      const { id } = await spawn(w);
      await w.harness.runtime.waitForIdle();
      fx.clock.advance(HOUR);
      valueOfDone(await w.harness.runtime.waitForTask(id));
      expect(be.calls("finish")[0]?.body).toMatchObject({ wakeReason: "deadline" });
    });

    it("retries a failure after the backoff with a new key (a1)", async () => {
      be.script("n1", "prepare", { body: fixture("prepare.final.failed.valid") });
      const w = await open();
      const { id } = await spawn(w, { policy: { ...POLICY, maxRetries: 2, backoffMs: 1000 } });
      await waitFor(() => w.harness.runtime.task(id)?.status === "waiting");
      expect(be.keys("prepare")).toEqual(["run-1:n1:r0:a0:prepare"]);
      fx.clock.advance(999);
      await w.harness.runtime.waitForIdle();
      expect(be.keys("prepare")).toHaveLength(1);
      fx.clock.advance(1);
      const value = valueOfDone(await w.harness.runtime.waitForTask(id));
      expect(value).toMatchObject({ status: "success", attempts: 2 });
      expect(be.keys("prepare")).toEqual(["run-1:n1:r0:a0:prepare", "run-1:n1:r0:a1:prepare"]);
    });

    it("a failure with no retries left faults the task on stop and is recorded on continue", async () => {
      be.script("n1", "prepare", { body: fixture("prepare.final.failed.valid") });
      be.script("n2", "prepare", { body: fixture("prepare.final.failed.valid") });
      const w = await open();
      const stop = await spawn(w);
      const cont = await spawn(w, { nodeId: "n2", policy: { ...POLICY, onError: "continue" } });
      const stopped = await w.harness.runtime.waitForTask(stop.id);
      expect(stopped.outcome).toMatchObject({
        type: "faulted",
        message: expect.stringContaining("node_failed: node blew up"),
      });
      const value = valueOfDone(await w.harness.runtime.waitForTask(cont.id));
      expect(value).toMatchObject({ status: "failed", continued: true, error: { code: "boom" } });
    });

    it("timeoutMs ends an in-flight call as failed(timeout)", async () => {
      be.script("n1", "prepare", { hang: true });
      const w = await open();
      const { id } = await spawn(w, {
        policy: { ...POLICY, timeoutMs: 5000, onError: "continue" },
      });
      await waitFor(() => be.calls("prepare").length === 1);
      fx.clock.advance(5000);
      const value = valueOfDone(await w.harness.runtime.waitForTask(id));
      expect(value).toMatchObject({ status: "failed", error: { code: "timeout" } });
    });

    it("abort during a wait ends the node aborted and runs onAbort", async () => {
      be.script("n1", "prepare", { body: fixture("prepare.wait.duration.valid") });
      const w = await open();
      const { id } = await spawn(w);
      await w.harness.runtime.waitForIdle();
      expect(await w.harness.runtime.abort(id, "user")).toBe(true);
      const final = await w.harness.runtime.waitForTask(id);
      expect(final.outcome).toEqual({ type: "aborted", reason: "user" });
      expect(aborted).toEqual([{ phase: "awaitWake", childrenTerminal: null }]);
      expect(be.calls("finish")).toHaveLength(0);
    });

    it("the abort path aborts the child conversation before onAbort runs", async () => {
      be.script("n1", "prepare", { body: llm() });
      fx.provider.enqueue({
        steps: [
          { type: "delay", ms: HOUR },
          { type: "text", text: "late" },
        ],
      });
      const w = await open();
      const { id } = await spawn(w);
      await waitFor(() => w.harness.runtime.task(id)?.phase === "awaitChild");
      await w.harness.runtime.abort(id, "user");
      const final = await w.harness.runtime.waitForTask(id);
      expect(final.outcome).toEqual({ type: "aborted", reason: "user" });
      expect(aborted).toEqual([{ phase: "awaitChild", childrenTerminal: true }]);
      const checkpoint = w.harness.runtime.task(id)?.checkpoint as unknown as {
        child: { conversationId: string };
      };
      const child = checkpoint.child;
      const childTasks = w.harness.runtime.tasks({ conversationId: child.conversationId });
      expect(childTasks.length).toBeGreaterThan(0);
      expect(childTasks.every((t) => t.outcome?.type === "aborted")).toBe(true);
    });

    it("a non-replay-safe finish cut off by a restart is interrupted when the backend has no outcome", async () => {
      be.script("n1", "prepare", { body: llm({ replaySafe: false }) });
      be.script("n1", "finish", { hang: true });
      fx.provider.enqueue(fakeText("ok"));
      const w1 = await open();
      const { id } = await spawn(w1, { policy: { ...POLICY, onError: "continue" } });
      await waitFor(() => be.calls("finish").length === 1);
      await w1.crash();

      const w2 = await open();
      const value = valueOfDone(await w2.harness.runtime.waitForTask(id));
      expect(value).toMatchObject({
        status: "failed",
        interrupted: true,
        continued: true,
        error: { code: "interrupted" },
      });
      // It asked the backend for the stored outcome; it did not run finish again.
      expect(be.calls("finish")).toHaveLength(1);
      expect(
        be.requests.some(
          (r) => r.method === "GET" && r.path.endsWith("/run-1%3An1%3Ar0%3Aa0%3Afinish"),
        ),
      ).toBe(true);
    });

    it("a non-replay-safe finish that completed in the backend uses the stored outcome", async () => {
      be.script("n1", "prepare", { body: llm({ replaySafe: false }) });
      be.script("n1", "finish", {
        hangAfterEffect: { ...fixture("prepare.final.valid"), output: { text: "stored" } },
      });
      fx.provider.enqueue(fakeText("ok"));
      const w1 = await open();
      const { id } = await spawn(w1);
      await waitFor(() => be.executions("run-1:n1:r0:a0:finish") === 1);
      await w1.crash();

      const w2 = await open();
      const value = valueOfDone(await w2.harness.runtime.waitForTask(id));
      expect(value).toMatchObject({ status: "success", output: { text: "stored" } });
      expect(be.executions("run-1:n1:r0:a0:finish")).toBe(1);
      expect(be.calls("finish")).toHaveLength(1);
    });

    it("a replay-safe finish cut off by a restart runs again under the same key", async () => {
      be.script("n1", "prepare", { body: llm({ replaySafe: true }) });
      be.script("n1", "finish", { hang: true });
      fx.provider.enqueue(fakeText("ok"));
      const w1 = await open();
      const { id } = await spawn(w1);
      await waitFor(() => be.calls("finish").length === 1);
      await w1.crash();

      const w2 = await open();
      const value = valueOfDone(await w2.harness.runtime.waitForTask(id));
      expect(value.status).toBe("success");
      expect(be.keys("finish")).toEqual(["run-1:n1:r0:a0:finish", "run-1:n1:r0:a0:finish"]);
      expect(be.executions("run-1:n1:r0:a0:finish")).toBe(1);
    });

    describe("fan-out park", () => {
      const fanout = () => ({ body: fixture("prepare.fanout.valid") });
      const summary = {
        count: 10,
        completed: 9,
        failed: 1,
        cancelled: 0,
        firstError: "item 3 failed",
      };

      async function park(w: AgentWorker) {
        const conv = await w.harness.createConversation({ tenantId: "t1", projectId: "p1" });
        const gateDef = w.harness.runtime.registry.get("test_gate");
        if (!gateDef) throw new Error("gate not registered");
        // The gate lives in the same conversation as the node, like the run task.
        const gateTask = await w.harness.runtime.spawn(gateDef, {
          conversationId: conv.id,
          input: null as never,
        });
        const def = w.harness.runtime.registry.get(WORKFLOW_NODE_KIND);
        if (!def) throw new Error("workflow_node is not registered");
        const node = await w.harness.runtime.spawn(def, {
          conversationId: conv.id,
          input: input({ nodeId: "n1", nodeType: "fanout", gateTaskId: gateTask.id }) as never,
        });
        return { gateId: gateTask.id, nodeId: node.id };
      }

      it("parks in awaitFanout, signals the gate once, and finishes with its own key on the result", async () => {
        be.script("n1", "prepare", fanout());
        be.script("n1", "finish", { body: fixture("prepare.final.valid") });
        const w = await open();
        const { gateId, nodeId } = await park(w);
        await waitFor(() => statusOf(w, gateId) === "terminal");
        expect(w.harness.runtime.task(gateId)?.outcome).toEqual({
          type: "done",
          value: {
            digest: {
              nodeId: "n1",
              count: 10,
              parallel: 5,
              failFast: true,
              target: { subgraph: "subgraph-1" },
              itemsRef: "batch:123",
            },
          },
        });
        const node = w.harness.runtime.task(nodeId);
        expect(node).toMatchObject({ status: "waiting", phase: "awaitFanout" });
        expect(be.calls("finish")).toHaveLength(0);

        await w.harness.runtime.signal(nodeId, FANOUT_RESULT_SIGNAL, summary);
        const value = valueOfDone(await w.harness.runtime.waitForTask(nodeId));
        expect(value.status).toBe("success");
        expect(be.keys("finish")).toEqual(["run-1:n1:r0:a0:finish"]);
        expect(be.calls("finish")[0]?.body).toEqual({
          contractVersion: 1,
          kind: "fanout",
          fanout: summary,
        });
        // A duplicate result is rejected.
        await expect(
          w.harness.runtime.signal(nodeId, FANOUT_RESULT_SIGNAL, summary),
        ).rejects.toBeInstanceOf(SignalRejected);
        expect(be.calls("finish")).toHaveLength(1);
      });

      it("a restart while parked does not call prepare again", async () => {
        be.script("n1", "prepare", fanout());
        const w1 = await open();
        const { nodeId } = await park(w1);
        await waitFor(() => w1.harness.runtime.task(nodeId)?.phase === "awaitFanout");
        await w1.crash();

        const w2 = await open();
        await w2.harness.runtime.waitForIdle();
        expect(w2.harness.runtime.task(nodeId)).toMatchObject({
          status: "waiting",
          phase: "awaitFanout",
        });
        expect(be.calls("prepare")).toHaveLength(1);
        await w2.harness.runtime.signal(nodeId, FANOUT_RESULT_SIGNAL, summary);
        const value = valueOfDone(await w2.harness.runtime.waitForTask(nodeId));
        expect(value.status).toBe("success");
        expect(be.calls("prepare")).toHaveLength(1);
        expect(be.calls("finish")).toHaveLength(1);
      });

      it("abort while parked ends the node aborted", async () => {
        be.script("n1", "prepare", fanout());
        const w = await open();
        const { nodeId } = await park(w);
        await waitFor(() => w.harness.runtime.task(nodeId)?.phase === "awaitFanout");
        await w.harness.runtime.abort(nodeId, "user");
        const final = await w.harness.runtime.waitForTask(nodeId);
        expect(final.outcome).toEqual({ type: "aborted", reason: "user" });
        expect(aborted).toEqual([{ phase: "awaitFanout", childrenTerminal: null }]);
        expect(be.calls("finish")).toHaveLength(0);
      });

      it("a fan-out node never retries, whatever the policy says", async () => {
        be.script("n1", "prepare", fanout());
        be.script("n1", "finish", { body: fixture("prepare.final.failed.valid") });
        const w = await open();
        const conv = await w.harness.createConversation({ tenantId: "t1", projectId: "p1" });
        const def = w.harness.runtime.registry.get(WORKFLOW_NODE_KIND);
        if (!def) throw new Error("workflow_node is not registered");
        const node = await w.harness.runtime.spawn(def, {
          conversationId: conv.id,
          input: input({
            nodeType: "fanout",
            policy: { ...POLICY, maxRetries: 3, backoffMs: 10, onError: "continue" },
          }) as never,
        });
        await waitFor(() => w.harness.runtime.task(node.id)?.phase === "awaitFanout");
        await w.harness.runtime.signal(node.id, FANOUT_RESULT_SIGNAL, summary);
        const value = valueOfDone(await w.harness.runtime.waitForTask(node.id));
        expect(value).toMatchObject({ status: "failed", attempts: 1, continued: true });
        expect(be.calls("finish")).toHaveLength(1);
        expect(be.calls("prepare")).toHaveLength(1);
      });
    });

    describe("checkpoints", () => {
      it("a golden previous-version checkpoint decodes and the task completes", async () => {
        const w = await open();
        const { id } = await spawn(w, {}, fixture("checkpoint.previous-version"));
        const value = valueOfDone(await w.harness.runtime.waitForTask(id));
        expect(value).toMatchObject({ status: "success", attempts: 1 });
      });

      it("a checkpoint with an unknown newer version fails with unsupported_checkpoint_version", async () => {
        const w = await open();
        const { id } = await spawn(w, {}, { version: 99, nodeAttempt: 0 });
        const final = await w.harness.runtime.waitForTask(id);
        expect(final.outcome).toMatchObject({
          type: "faulted",
          message: expect.stringContaining("unsupported_checkpoint_version"),
        });
        expect(be.calls("prepare")).toHaveLength(0);
      });
    });

    it("shows the node in the watch view with its label and attempt", async () => {
      be.script("n1", "prepare", { body: fixture("prepare.wait.duration.valid") });
      const w = await open();
      const { id } = await spawn(w, { nodeId: "n1" });
      await w.harness.runtime.waitForIdle();
      const def = w.harness.runtime.registry.get(WORKFLOW_NODE_KIND);
      const record = w.harness.runtime.task(id) as TaskRecord;
      expect(def?.view?.(taskState(record) as never)).toEqual({
        label: "n1",
        attempt: 1,
      });
    });
  });
}
