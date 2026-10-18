/**
 * The fan-out suite (plan 2026-10-17-003, U34), run against every storage
 * backend with the fake workflow backend. A fan-out node parks; the run serves
 * it through bounded lanes whose items are child `workflow_run` tasks.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { JsonValue } from "../../src/core/json.js";
import type { CommitEvent } from "../../src/session/publish.js";
import type { TaskRecord } from "../../src/storage/types.js";
import { createWorkflowFanoutLaneTask, laneCount } from "../../src/workflow/fanout-lane-task.js";
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
import { AUTH, call, serve } from "../server/support.js";
import { FakeWorkflowBackend, fixture } from "../support/fake-workflow-backend.js";

const HOUR = 60 * 60 * 1000;
const POLICY = { maxRetries: 0, backoffMs: 0, timeoutMs: 0, onError: "stop" } as const;

export const waitFor2h = { contractVersion: 1, kind: "wait", durationMs: 2 * HOUR };
export const failed = () => fixture("prepare.final.failed.valid");

/** A node that does nothing but ask the backend. */
export const plain = (id: string, deps: string[] = [], extra: Record<string, unknown> = {}) => ({
  id,
  type: "code",
  deps: deps.map((from) => ({ from })),
  policy: { ...POLICY },
  ...extra,
});

/** A graph with one fan-out node `fan` and a one-node body `body`. */
export function fanGraph(over: Partial<WorkflowGraph> = {}, fanNode = "fan"): WorkflowGraph {
  return {
    nodes: [plain(fanNode, [], { type: "iteration", mayFanOut: true })],
    subgraphs: { body: { nodes: [plain("work")] } },
    ...over,
  } as WorkflowGraph;
}

export const fanout = (
  count: number,
  parallel: number,
  over: Record<string, JsonValue> = {},
): Record<string, JsonValue> => ({
  contractVersion: 1,
  kind: "fanout",
  count,
  parallel,
  failFast: true,
  target: { subgraph: "body" },
  itemsRef: "run:run-1:fan",
  ...over,
});

export function defineFanoutSuite(backend: AgentBackend): void {
  describe(`workflow fan-out (${backend.name})`, () => {
    let fx: AgentFixture;
    let be: FakeWorkflowBackend;
    const workers: AgentWorker[] = [];
    const closers: Array<() => Promise<void>> = [];

    beforeEach(async () => {
      fx = await backend.fixture();
      be = await FakeWorkflowBackend.start();
    });

    afterEach(async () => {
      for (const close of closers.splice(0)) await close();
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
    ): Promise<{ id: string; conversationId: string; abort(): Promise<void> }> {
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
      return {
        id: task.id,
        conversationId: conv.id,
        abort: () => conv.abort("user_abort").then(() => undefined),
      };
    }

    const runValue = (task: TaskRecord): WorkflowRunValue => {
      expect(task.outcome?.type).toBe("done");
      return (task.outcome as { value: unknown }).value as WorkflowRunValue;
    };

    /** Item runs (`workflow_run` children of lanes) not yet terminal. */
    const liveItems = (w: AgentWorker, conversationId: string): TaskRecord[] =>
      w.harness.runtime
        .tasks({ conversationId })
        .filter((t) => t.kind === WORKFLOW_RUN_KIND && t.parentTaskId?.includes(":lane"))
        .filter((t) => t.status !== "terminal");

    /** The highest number of live item runs at any commit. */
    function trackLive(w: AgentWorker, conversationId: string): { peak(): number } {
      let peak = 0;
      w.harness.session.subscribeCommits((_e: CommitEvent) => {
        peak = Math.max(peak, liveItems(w, conversationId).length);
      });
      return { peak: () => peak };
    }

    const finishCalls = (nodeId = "fan") =>
      be
        .calls("finish")
        .filter((r) =>
          new RegExp(`^run-1:${nodeId}:r\\d+:a\\d+:finish$`).test(
            r.headers["idempotency-key"] ?? "",
          ),
        );

    it("25 items at parallel 5: at most 5 item runs live at any commit, each item runs once, finish gets the counts", async () => {
      be.script("fan", "prepare", { body: fanout(25, 5) });
      const hold = be.hold("work", "prepare");
      const w = await open();
      const run = await startRun(w, fanGraph());
      const live = trackLive(w, run.conversationId);
      await waitFor(() => hold.waiting === 5 && liveItems(w, run.conversationId).length === 5);
      // The node is still parked while the lanes run; nobody has called its finish.
      const node = w.harness.runtime
        .tasks({ conversationId: run.conversationId })
        .find(
          (t) => t.kind === "workflow_node" && (t.input as { nodeId: string }).nodeId === "fan",
        );
      expect(node).toMatchObject({ status: "waiting", phase: "awaitFanout" });
      expect(finishCalls()).toHaveLength(0);
      expect(liveItems(w, run.conversationId)).toHaveLength(5);

      hold.release();
      const value = runValue(await w.harness.runtime.waitForTask(run.id));
      expect(value).toEqual({ status: "completed", nodes: { fan: "success" } });
      expect(live.peak()).toBeLessThanOrEqual(5);
      expect(live.peak()).toBe(5);

      const keys = be.itemKeys("run-1", "fan");
      expect(keys).toHaveLength(25);
      for (const key of keys) expect(be.executions(key)).toBe(1);
      expect(new Set(keys.map((k) => k.split(":")[2])).size).toBe(25);
      // finish: once, with the node's own key, carrying the counts.
      expect(finishCalls()).toHaveLength(1);
      expect(finishCalls()[0]?.headers["idempotency-key"]).toBe("run-1:fan:r0:a0:finish");
      expect(finishCalls()[0]?.body).toMatchObject({
        kind: "fanout",
        fanout: { count: 25, completed: 25, failed: 0, cancelled: 0 },
      });
      // Every item run finalised, plus the parent.
      expect(be.calls("finalize")).toHaveLength(26);
    });

    it("a restart in the middle of fan-out keeps the bound and re-runs no finished item", async () => {
      be.script("fan", "prepare", { body: fanout(6, 3) });
      // First wave (items 0-2) completes; the second wave (items 3-5) parks for 2 h.
      for (const i of [3, 4, 5]) {
        be.scriptRun(`run-1:fan:i${i}`, "work", "prepare", { body: waitFor2h });
      }
      const w1 = await open();
      const run = await startRun(w1, fanGraph());
      const live1 = trackLive(w1, run.conversationId);
      await waitFor(() =>
        [3, 4, 5].every((i) => be.itemKeys("run-1", "fan").some((k) => k.includes(`:i${i}:`))),
      );
      await w1.harness.runtime.waitForIdle();
      expect(liveItems(w1, run.conversationId)).toHaveLength(3);
      await w1.crash();

      const w2 = await open();
      const live2 = trackLive(w2, run.conversationId);
      fx.clock.advance(2 * HOUR);
      const value = runValue(await w2.harness.runtime.waitForTask(run.id));
      expect(value.status).toBe("completed");
      expect(Math.max(live1.peak(), live2.peak())).toBeLessThanOrEqual(3);
      const keys = be.itemKeys("run-1", "fan");
      expect(keys).toHaveLength(6);
      for (const key of keys) expect(be.executions(key)).toBe(1);
      expect(finishCalls()).toHaveLength(1);
      expect(finishCalls()[0]?.body).toMatchObject({
        fanout: { count: 6, completed: 6, failed: 0, cancelled: 0 },
      });
    });

    it("failFast: one failed item aborts the other lanes' live items; finish gets the failure and the run fails", async () => {
      be.script("fan", "prepare", { body: fanout(9, 3, { failFast: true }) });
      be.scriptRun("run-1:fan:i0", "work", "prepare", { body: failed() });
      for (const i of [1, 2]) {
        be.scriptRun(`run-1:fan:i${i}`, "work", "prepare", { body: waitFor2h });
      }
      be.script("fan", "finish", { body: failed() });
      const w = await open();
      const run = await startRun(w, fanGraph());
      const value = runValue(await w.harness.runtime.waitForTask(run.id));
      expect(value).toMatchObject({ status: "failed", nodes: { fan: "failed" } });
      expect(finishCalls()).toHaveLength(1);
      expect(finishCalls()[0]?.body).toMatchObject({
        kind: "fanout",
        fanout: {
          count: 9,
          completed: 0,
          failed: 1,
          cancelled: 8,
          firstError: { code: "node_failed" },
        },
      });
      const items = w.harness.runtime
        .tasks({ conversationId: run.conversationId })
        .filter((t) => t.kind === WORKFLOW_RUN_KIND && t.id !== run.id);
      expect(items.every((t) => t.status === "terminal")).toBe(true);
      expect(items.filter((t) => t.outcome?.type === "aborted").length).toBeGreaterThanOrEqual(2);
      // Items that never started stay unstarted.
      expect(be.itemKeys("run-1", "fan").length).toBeLessThan(9);
    });

    it("failFast off: every item settles and the summary counts the failures", async () => {
      be.script("fan", "prepare", { body: fanout(8, 2, { failFast: false }) });
      for (const i of [1, 4])
        be.scriptRun(`run-1:fan:i${i}`, "work", "prepare", { body: failed() });
      const w = await open();
      const run = await startRun(w, fanGraph());
      const value = runValue(await w.harness.runtime.waitForTask(run.id));
      expect(value.status).toBe("completed");
      expect(finishCalls()[0]?.body).toMatchObject({
        fanout: {
          count: 8,
          completed: 6,
          failed: 2,
          cancelled: 0,
          firstError: { code: "node_failed" },
        },
      });
      expect(be.itemKeys("run-1", "fan")).toHaveLength(8);
    });

    describe("gate", () => {
      it("a mayFanOut node that returns final without parking wakes the run through the gate", async () => {
        const w = await open();
        const run = await startRun(w, fanGraph());
        const value = runValue(await w.harness.runtime.waitForTask(run.id));
        expect(value).toEqual({ status: "completed", nodes: { fan: "success" } });
        expect(be.itemKeys("run-1", "fan")).toHaveLength(0);
        expect(finishCalls()).toHaveLength(0);
      });

      it("a node that fails before parking wakes the run", async () => {
        be.script("fan", "prepare", { body: failed() });
        const w = await open();
        const run = await startRun(w, fanGraph());
        const value = runValue(await w.harness.runtime.waitForTask(run.id));
        expect(value).toMatchObject({
          status: "failed",
          nodes: { fan: "failed" },
          error: { code: "node_failed", nodeId: "fan" },
        });
      });

      it("a failing sibling aborts a fan-out node that has not parked, under stop", async () => {
        be.script("fan", "prepare", { body: waitFor2h });
        be.script("bad", "prepare", { body: failed() });
        const w = await open();
        const run = await startRun(w, {
          nodes: [plain("fan", [], { type: "iteration", mayFanOut: true }), plain("bad")],
          subgraphs: { body: { nodes: [plain("work")] } },
        });
        const value = runValue(await w.harness.runtime.waitForTask(run.id));
        expect(value).toMatchObject({ status: "failed", nodes: { fan: "skipped", bad: "failed" } });
      });

      it("a restart while the node is parked does not call prepare again", async () => {
        be.script("fan", "prepare", { body: fanout(2, 1) });
        const hold = be.hold("work", "prepare");
        const w1 = await open();
        const run = await startRun(w1, fanGraph());
        await waitFor(() => hold.waiting === 1);
        await w1.crash();
        hold.release();
        const w2 = await open();
        const value = runValue(await w2.harness.runtime.waitForTask(run.id));
        expect(value.status).toBe("completed");
        expect(be.total("fan", "prepare")).toBe(1);
        expect(finishCalls()).toHaveLength(1);
      });
    });

    describe("answers without lanes", () => {
      it("an empty fan-out finishes the node with zero counts", async () => {
        be.script("fan", "prepare", { body: fanout(0, 1) });
        const w = await open();
        const run = await startRun(w, fanGraph());
        const value = runValue(await w.harness.runtime.waitForTask(run.id));
        expect(value.status).toBe("completed");
        expect(finishCalls()[0]?.body).toMatchObject({
          fanout: { count: 0, completed: 0, failed: 0, cancelled: 0 },
        });
      });

      it("a target the graph lacks is answered as a failure, with no lane", async () => {
        be.script("fan", "prepare", { body: fanout(3, 2, { target: { subgraph: "missing" } }) });
        const w = await open();
        const run = await startRun(w, fanGraph());
        const value = runValue(await w.harness.runtime.waitForTask(run.id));
        expect(value.status).toBe("completed");
        expect(finishCalls()[0]?.body).toMatchObject({
          fanout: { count: 3, failed: 3, firstError: { code: "fanout_target_unknown" } },
        });
        expect(be.itemKeys("run-1", "fan")).toHaveLength(0);
      });

      it("1001 items is refused: the node fails and no lane starts", async () => {
        be.script("fan", "prepare", { body: fanout(1001, 5) });
        const w = await open();
        const run = await startRun(w, fanGraph());
        const value = runValue(await w.harness.runtime.waitForTask(run.id));
        expect(value).toMatchObject({ status: "failed", nodes: { fan: "failed" } });
        expect(be.itemKeys("run-1", "fan")).toHaveLength(0);
        expect(finishCalls()).toHaveLength(0);
      });
    });

    it("lanes: parallel is clamped to 50 and to the item count", () => {
      expect(laneCount(1000, 500)).toBe(50);
      expect(laneCount(1000, 10)).toBe(10);
      expect(laneCount(3, 50)).toBe(3);
      expect(laneCount(0, 5)).toBe(0);
      expect(laneCount(5, 0)).toBe(1);
    });

    describe("subworkflow", () => {
      it("target.workflowId runs the referenced graph once", async () => {
        be.script("sub", "prepare", {
          body: fanout(1, 1, { target: { workflowId: "wf-1" }, itemsRef: "run:run-1:sub" }),
        });
        const w = await open();
        const run = await startRun(w, {
          nodes: [plain("sub", [], { type: "subworkflow", mayFanOut: true })],
          workflows: { "wf-1": { nodes: [plain("inner1"), plain("inner2", ["inner1"])] } },
        });
        const value = runValue(await w.harness.runtime.waitForTask(run.id));
        expect(value).toEqual({ status: "completed", nodes: { sub: "success" } });
        expect(be.keys("prepare")).toEqual([
          "run-1:sub:r0:a0:prepare",
          "run-1:sub:i0:inner1:r0:a0:prepare",
          "run-1:sub:i0:inner2:r0:a0:prepare",
        ]);
        expect(finishCalls("sub")[0]?.body).toMatchObject({
          fanout: { count: 1, completed: 1, failed: 0, cancelled: 0 },
        });
      });

      it("a graph nested beyond depth 3 or referencing itself is rejected with 400 and nothing spawns", async () => {
        const w = await open();
        const srv = await serve(w.harness);
        closers.push(() => srv.close());
        const leaf: WorkflowGraph = { nodes: [plain("leaf")] };
        const deep: WorkflowGraph = {
          nodes: [plain("a")],
          workflows: {
            w1: {
              nodes: [plain("b")],
              workflows: {
                w2: {
                  nodes: [plain("c")],
                  workflows: { w3: { nodes: [plain("d")], workflows: { w4: leaf } } },
                },
              },
            },
          },
        };
        const recursive: WorkflowGraph = {
          nodes: [plain("a")],
          workflows: { w1: { nodes: [plain("b")], workflows: { w1: leaf } } },
        };
        for (const [i, graph] of [deep, recursive].entries()) {
          const res = await call(srv.url, "POST", "/v1/runs", {
            headers: AUTH,
            body: {
              id: `bad-${i}`,
              kind: WORKFLOW_RUN_KIND,
              input: {
                version: 1,
                runId: `bad-${i}`,
                runAttempt: 0,
                graph,
                variables: {},
                errorMode: "stop",
              },
            },
          });
          expect(res.status).toBe(400);
        }
        expect(w.harness.runtime.tasks().filter((t) => t.kind === WORKFLOW_RUN_KIND)).toHaveLength(
          0,
        );
      });
    });

    it("aborting the container conversation aborts lanes, items and the parked node, and finalizes the run once as cancelled", async () => {
      be.script("fan", "prepare", { body: fanout(6, 3) });
      const hold = be.hold("work", "prepare");
      const w = await open();
      const run = await startRun(w, fanGraph());
      await waitFor(() => hold.waiting === 3 && liveItems(w, run.conversationId).length === 3);
      await run.abort();
      const final = await w.harness.runtime.waitForTask(run.id);
      expect(final.outcome?.type).toBe("aborted");
      const all = w.harness.runtime.tasks({ conversationId: run.conversationId });
      expect(all.every((t) => t.status === "terminal")).toBe(true);
      const byKind = (kind: string) => all.filter((t) => t.kind === kind);
      expect(byKind("workflow_fanout_lane").every((t) => t.outcome?.type === "aborted")).toBe(true);
      expect(byKind("workflow_fanout_lane")).toHaveLength(3);
      expect(
        all.find(
          (t) => t.kind === "workflow_node" && (t.input as { nodeId: string }).nodeId === "fan",
        )?.outcome?.type,
      ).toBe("aborted");
      const parentFinalizes = be
        .calls("finalize")
        .filter((r) => r.headers["idempotency-key"] === "run-1:r0:finalize");
      expect(parentFinalizes).toHaveLength(1);
      expect(parentFinalizes[0]?.body).toMatchObject({ status: "cancelled" });
      expect(finishCalls()).toHaveLength(0);
    });
  });
}
