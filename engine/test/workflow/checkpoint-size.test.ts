/**
 * Checkpoints and signals stay bounded however many items a node fans out to
 * (plan 2026-10-17-003, U34, KTD17): the run, every lane, the fan-out node and
 * the `fanout-result` signal hold identifiers and counts, never items or results.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { createWorkflowFanoutLaneTask } from "../../src/workflow/fanout-lane-task.js";
import { createWorkflowGateTask } from "../../src/workflow/gate-task.js";
import { createNodeGateway } from "../../src/workflow/node-gateway.js";
import { createWorkflowNodeTask } from "../../src/workflow/node-task.js";
import { createWorkflowRunTask, WORKFLOW_RUN_KIND } from "../../src/workflow/run-task.js";
import { type AgentFixture, type AgentWorker, memoryBackend } from "../agent/support.js";
import { FakeWorkflowBackend } from "../support/fake-workflow-backend.js";
import { fanGraph, fanout } from "./fanout.suite.js";

const KIB = 1024;
/** Present in every item's output; it must never reach the parent's checkpoints or signals. */
const ITEM_RESULT = "ITEM-RESULT-MARKER";

interface Sizes {
  run: number;
  lane: number;
  node: number;
  signal: number;
  leaked: boolean;
  doneStatus: string | undefined;
}

describe("fan-out checkpoint size (U34)", () => {
  let fx: AgentFixture;
  let be: FakeWorkflowBackend;
  let worker: AgentWorker | undefined;

  beforeEach(async () => {
    fx = await memoryBackend.fixture();
    be = await FakeWorkflowBackend.start();
  });

  afterEach(async () => {
    await worker?.crash().catch(() => {});
    await be.close();
  });

  async function measure(count: number, parallel: number): Promise<Sizes> {
    be.script("fan", "prepare", { body: fanout(count, parallel, { failFast: false }) });
    be.defaultPrepare = {
      contractVersion: 1,
      kind: "final",
      status: "success",
      output: { result: ITEM_RESULT },
      tokensUsed: 1,
    };
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
    worker = w;
    const sizes: Sizes = { run: 0, lane: 0, node: 0, signal: 0, leaked: false, doneStatus: "" };
    const conv = await w.harness.createConversation({ tenantId: "t1", projectId: "p1" });
    const def = w.harness.runtime.registry.get(WORKFLOW_RUN_KIND);
    if (!def) throw new Error("workflow_run is not registered");
    const task = await w.harness.runtime.spawn(def, {
      conversationId: conv.id,
      input: {
        version: 1,
        runId: "run-1",
        runAttempt: 0,
        graph: fanGraph(),
        variables: {},
        errorMode: "stop",
      } as never,
    });
    const parentNode = `${task.id}:r0:fan`;
    w.harness.session.subscribeCommits((event) => {
      for (const rec of [...(event.batch.createTasks ?? []), ...(event.batch.replaceTasks ?? [])]) {
        let bucket: "run" | "lane" | "node" | null = null;
        if (rec.id === task.id) bucket = "run";
        else if (rec.kind === "workflow_fanout_lane") bucket = "lane";
        else if (rec.id === parentNode) bucket = "node";
        if (!bucket) continue;
        const checkpoint = JSON.stringify(rec.checkpoint ?? null);
        const signals = JSON.stringify(rec.signals ?? {});
        sizes[bucket] = Math.max(sizes[bucket], checkpoint.length);
        const result = rec.signals?.["fanout-result"];
        if (result !== undefined)
          sizes.signal = Math.max(sizes.signal, JSON.stringify(result).length);
        if (checkpoint.includes(ITEM_RESULT) || signals.includes(ITEM_RESULT)) sizes.leaked = true;
      }
    });
    const final = await w.harness.runtime.waitForTask(task.id);
    sizes.doneStatus = (final.outcome as { value?: { status?: string } }).value?.status;
    const finish = be
      .calls("finish")
      .find((r) => r.headers["idempotency-key"] === "run-1:fan:r0:a0:finish");
    expect(finish?.body).toMatchObject({ fanout: { count, completed: count, failed: 0 } });
    expect(be.itemKeys("run-1", "fan")).toHaveLength(count);
    await w.crash();
    worker = undefined;
    return sizes;
  }

  it("run, lane and node checkpoints and the fanout-result signal do not grow with the item count", async () => {
    const small = await measure(10, 10);
    await be.close();
    be = await FakeWorkflowBackend.start();
    fx = await memoryBackend.fixture();
    const large = await measure(1000, 10);

    for (const s of [small, large]) {
      expect(s.doneStatus).toBe("completed");
      expect(s.leaked).toBe(false);
      for (const key of ["run", "lane", "node", "signal"] as const) {
        expect(s[key]).toBeGreaterThan(0);
        expect(s[key]).toBeLessThan(8 * KIB);
      }
    }
    for (const key of ["run", "lane", "node", "signal"] as const) {
      expect(large[key] - small[key]).toBeLessThan(KIB);
    }
  }, 120_000);
});
