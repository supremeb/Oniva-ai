/**
 * The run-level retry and deadline suite (plan 2026-10-17-003, U35), run against
 * every storage backend with the fake workflow backend. The run retries as a
 * whole on a deadline wait; node keys carry `r{runAttempt}`.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { TaskRecord } from "../../src/storage/types.js";
import { createWorkflowFanoutLaneTask } from "../../src/workflow/fanout-lane-task.js";
import { createWorkflowGateTask } from "../../src/workflow/gate-task.js";
import type { WorkflowGraph } from "../../src/workflow/graph.js";
import { createNodeGateway } from "../../src/workflow/node-gateway.js";
import { createWorkflowNodeTask } from "../../src/workflow/node-task.js";
import {
  createWorkflowRunTask,
  WORKFLOW_RUN_KIND,
  type WorkflowRunInput,
  type WorkflowRunValue,
} from "../../src/workflow/run-task.js";
import {
  type AgentBackend,
  type AgentFixture,
  type AgentWorker,
  waitFor,
} from "../agent/support.js";
import { FakeWorkflowBackend } from "../support/fake-workflow-backend.js";
import { failed, fanGraph, fanout, plain, waitFor2h } from "./fanout.suite.js";

const HOUR = 60 * 60 * 1000;
const DELAY = 5_000;

export function defineRunRetrySuite(backend: AgentBackend): void {
  describe(`workflow run retry and deadline (${backend.name})`, () => {
    let fx: AgentFixture;
    let be: FakeWorkflowBackend;
    const workers: AgentWorker[] = [];

    beforeEach(async () => {
      fx = await backend.fixture();
      be = await FakeWorkflowBackend.start();
    });

    afterEach(async () => {
      for (const w of workers.splice(0)) await w.crash().catch(() => {});
      await be.close();
    });

    async function open(): Promise<AgentWorker> {
      const gateway = createNodeGateway({
        baseUrl: be.baseUrl,
        serviceToken: be.serviceToken,
        fence: () => null,
        clock: fx.clock,
        retry: { attempts: 1 },
      });
      const w = await fx.open({
        tasks: [
          createWorkflowNodeTask({ gateway }),
          createWorkflowRunTask({ gateway }),
          createWorkflowFanoutLaneTask(),
          createWorkflowGateTask(),
        ],
      });
      workers.push(w);
      return w;
    }

    async function startRun(
      w: AgentWorker,
      graph: WorkflowGraph,
      over: Partial<WorkflowRunInput> = {},
    ): Promise<{ id: string; conversationId: string }> {
      const conv = await w.harness.createConversation({ tenantId: "t1", projectId: "p1" });
      const def = w.harness.runtime.registry.get(WORKFLOW_RUN_KIND);
      if (!def) throw new Error("workflow_run is not registered");
      const input: WorkflowRunInput = {
        version: 1,
        runId: "run-1",
        runAttempt: 0,
        graph,
        variables: {},
        errorMode: "stop",
        ...over,
      };
      const task = await w.harness.runtime.spawn(def, {
        conversationId: conv.id,
        input: input as never,
      });
      return { id: task.id, conversationId: conv.id };
    }

    const runValue = (task: TaskRecord): WorkflowRunValue => {
      expect(task.outcome?.type).toBe("done");
      return (task.outcome as { value: unknown }).value as WorkflowRunValue;
    };

    const chain: WorkflowGraph = { nodes: [plain("a"), plain("b", ["a"])] };
    const retry = (max: number) => ({ max, delayMs: DELAY });
    const prepareKeys = () => be.keys("prepare");
    const finalizes = () => be.calls("finalize");

    it("root retry: the first attempt fails, the run wakes exactly at delayMs, and the keys carry r0 then r1", async () => {
      be.script("a", "prepare", { body: failed() });
      const w = await open();
      const t0 = fx.clock.now();
      const run = await startRun(w, chain, { retry: retry(1) });
      await w.harness.runtime.waitForIdle();
      expect(prepareKeys()).toEqual(["run-1:a:r0:a0:prepare"]);
      expect(w.harness.runtime.task(run.id)).toMatchObject({ status: "waiting" });
      expect(w.harness.runtime.task(run.id)?.waiting?.until).toBe(t0 + DELAY);
      // Not finalised between attempts.
      expect(finalizes()).toHaveLength(0);

      fx.clock.advance(DELAY - 1);
      await w.harness.runtime.waitForIdle();
      expect(prepareKeys()).toHaveLength(1);
      expect(w.harness.runtime.task(run.id)?.status).toBe("waiting");

      fx.clock.advance(1);
      const value = runValue(await w.harness.runtime.waitForTask(run.id));
      expect(value).toEqual({ status: "completed", nodes: { a: "success", b: "success" } });
      expect(prepareKeys()).toEqual([
        "run-1:a:r0:a0:prepare",
        "run-1:a:r1:a0:prepare",
        "run-1:b:r1:a0:prepare",
      ]);
      expect(finalizes()).toHaveLength(1);
      expect(finalizes()[0]?.headers["idempotency-key"]).toBe("run-1:r1:finalize");
      expect(finalizes()[0]?.body).toMatchObject({ status: "completed", attempts: 2 });
    });

    it("a run retry re-executes a node that failed in run attempt 0", async () => {
      be.script("a", "prepare", { body: failed() });
      const w = await open();
      const run = await startRun(w, chain, { retry: retry(1) });
      await w.harness.runtime.waitForIdle();
      // The backend stored the failure under the r0 key.
      expect(be.outcome("run-1:a:r0:a0:prepare")).toMatchObject({
        kind: "final",
        status: "failed",
      });
      fx.clock.advance(DELAY);
      const value = runValue(await w.harness.runtime.waitForTask(run.id));
      expect(value.status).toBe("completed");
      // The r1 key is a different key: prepare ran again and succeeded.
      expect(be.executions("run-1:a:r0:a0:prepare")).toBe(1);
      expect(be.executions("run-1:a:r1:a0:prepare")).toBe(1);
      expect(be.outcome("run-1:a:r1:a0:prepare")).toMatchObject({ status: "success" });
      expect(be.total("a", "prepare")).toBe(2);
    });

    it("retries exhausted: the run ends failed with attempts = max + 1 and finalizes once", async () => {
      be.script("a", "prepare", { body: failed() }, { body: failed() }, { body: failed() });
      const w = await open();
      const run = await startRun(w, chain, { retry: retry(2) });
      for (let i = 0; i < 2; i++) {
        await w.harness.runtime.waitForIdle();
        expect(w.harness.runtime.task(run.id)?.status).toBe("waiting");
        fx.clock.advance(DELAY);
      }
      const value = runValue(await w.harness.runtime.waitForTask(run.id));
      expect(value).toMatchObject({
        status: "failed",
        nodes: { a: "failed", b: "skipped" },
        error: { code: "node_failed", nodeId: "a" },
      });
      expect(prepareKeys()).toEqual([
        "run-1:a:r0:a0:prepare",
        "run-1:a:r1:a0:prepare",
        "run-1:a:r2:a0:prepare",
      ]);
      expect(finalizes()).toHaveLength(1);
      expect(finalizes()[0]?.body).toMatchObject({ status: "failed", attempts: 3 });
    });

    it("without retry a failed run finalizes at once", async () => {
      be.script("a", "prepare", { body: failed() });
      const w = await open();
      const run = await startRun(w, chain);
      const value = runValue(await w.harness.runtime.waitForTask(run.id));
      expect(value.status).toBe("failed");
      expect(finalizes()[0]?.body).toMatchObject({ attempts: 1 });
    });

    it("a restart during the retry delay wakes at the deadline and runs attempt 1 once", async () => {
      be.script("a", "prepare", { body: failed() });
      const w1 = await open();
      const run = await startRun(w1, chain, { retry: retry(1) });
      await w1.harness.runtime.waitForIdle();
      expect(prepareKeys()).toEqual(["run-1:a:r0:a0:prepare"]);
      await w1.crash();

      const w2 = await open();
      await w2.harness.runtime.waitForIdle();
      expect(prepareKeys()).toHaveLength(1);
      fx.clock.advance(DELAY);
      const value = runValue(await w2.harness.runtime.waitForTask(run.id));
      expect(value.status).toBe("completed");
      expect(be.executions("run-1:a:r0:a0:prepare")).toBe(1);
      expect(be.executions("run-1:a:r1:a0:prepare")).toBe(1);
      expect(be.executions("run-1:b:r1:a0:prepare")).toBe(1);
      expect(finalizes()).toHaveLength(1);
    });

    it("a deadline in the past ends the run failed with deadline_exceeded, with no retry and one finalize", async () => {
      const w = await open();
      const run = await startRun(w, chain, {
        retry: retry(3),
        deadlineMs: fx.clock.now() - 1,
      });
      const value = runValue(await w.harness.runtime.waitForTask(run.id));
      expect(value).toMatchObject({
        status: "failed",
        nodes: { a: "skipped", b: "skipped" },
        error: { code: "deadline_exceeded" },
      });
      expect(prepareKeys()).toHaveLength(0);
      expect(finalizes()).toHaveLength(1);
      expect(finalizes()[0]?.body).toMatchObject({
        status: "failed",
        error: { code: "deadline_exceeded" },
      });
    });

    it("a deadline reached mid-run aborts live children and finalizes once", async () => {
      be.script("a", "prepare", { body: waitFor2h });
      const w = await open();
      const run = await startRun(w, chain, {
        retry: retry(3),
        deadlineMs: fx.clock.now() + HOUR,
      });
      await w.harness.runtime.waitForIdle();
      expect(w.harness.runtime.task(run.id)?.status).toBe("waiting");
      fx.clock.advance(HOUR);
      const value = runValue(await w.harness.runtime.waitForTask(run.id));
      expect(value).toMatchObject({
        status: "failed",
        nodes: { a: "skipped", b: "skipped" },
        error: { code: "deadline_exceeded" },
      });
      const live = w.harness.runtime
        .tasks({ conversationId: run.conversationId })
        .filter((t) => t.status !== "terminal");
      expect(live).toHaveLength(0);
      const node = w.harness.runtime
        .tasks({ conversationId: run.conversationId })
        .find((t) => t.kind === "workflow_node");
      expect(node?.outcome?.type).toBe("aborted");
      expect(prepareKeys()).toEqual(["run-1:a:r0:a0:prepare"]);
      expect(finalizes()).toHaveLength(1);
    });

    it("a deadline reached while items fan out aborts the lanes and their items", async () => {
      be.script("fan", "prepare", { body: fanout(4, 2) });
      for (const i of [0, 1, 2, 3]) {
        be.scriptRun(`run-1:fan:i${i}`, "work", "prepare", { body: waitFor2h });
      }
      const w = await open();
      const run = await startRun(w, fanGraph(), { deadlineMs: fx.clock.now() + HOUR });
      await waitFor(() => be.itemKeys("run-1", "fan").length === 2);
      await w.harness.runtime.waitForIdle();
      fx.clock.advance(HOUR);
      const value = runValue(await w.harness.runtime.waitForTask(run.id));
      expect(value).toMatchObject({ status: "failed", error: { code: "deadline_exceeded" } });
      const all = w.harness.runtime.tasks({ conversationId: run.conversationId });
      expect(all.every((t) => t.status === "terminal")).toBe(true);
      expect(be.itemKeys("run-1", "fan")).toHaveLength(2);
      // The run finalized once; the aborted items report cancelled under their own run ids.
      expect(finalizes().filter((r) => r.path.includes("/runs/run-1/finalize"))).toHaveLength(1);
    });

    it("a retry that would pass the deadline is not taken", async () => {
      be.script("a", "prepare", { body: failed() });
      const w = await open();
      const run = await startRun(w, chain, {
        retry: retry(1),
        deadlineMs: fx.clock.now() + DELAY,
      });
      const value = runValue(await w.harness.runtime.waitForTask(run.id));
      expect(value).toMatchObject({ status: "failed", error: { code: "node_failed" } });
      expect(prepareKeys()).toEqual(["run-1:a:r0:a0:prepare"]);
      expect(finalizes()).toHaveLength(1);
    });

    it("a retry after a fan-out run starts a fresh fan-out whose ids carry the new runAttempt", async () => {
      be.script("fan", "prepare", { body: fanout(2, 1) }, { body: fanout(2, 1) });
      be.scriptRun("run-1:fan:i0", "work", "prepare", { body: failed() });
      be.script("fan", "finish", { body: failed() });
      const w = await open();
      const run = await startRun(w, fanGraph(), { retry: retry(1) });
      await w.harness.runtime.waitForIdle();
      expect(w.harness.runtime.task(run.id)?.status).toBe("waiting");
      fx.clock.advance(DELAY);
      const value = runValue(await w.harness.runtime.waitForTask(run.id));
      expect(value).toEqual({ status: "completed", nodes: { fan: "success" } });

      const ids = w.harness.runtime.tasks({ conversationId: run.conversationId }).map((t) => t.id);
      expect(ids.some((id) => id.includes(":r0:fan:lane0"))).toBe(true);
      expect(ids.some((id) => id.includes(":r1:fan:lane0"))).toBe(true);
      expect(ids.some((id) => id.includes(":r1:fan:i1"))).toBe(true);
      const keys = be.itemKeys("run-1", "fan");
      expect(keys.some((k) => k.includes(":r0:"))).toBe(true);
      expect(keys.filter((k) => k.includes(":r1:"))).toHaveLength(2);
      for (const k of keys) expect(be.executions(k)).toBe(1);
      expect(finalizes().filter((r) => r.path.includes("/runs/run-1/finalize"))).toHaveLength(1);
    });
  });
}
