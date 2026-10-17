/**
 * The `workflow_run` task suite (plan 2026-10-17-003, U12), run against every
 * storage backend with the fake workflow backend (`fake-workflow-backend.ts`).
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { TaskRecord } from "../../src/storage/types.js";
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

type Dep = string | [string, string];

interface NodeSpec {
  id: string;
  deps: Dep[];
  handles?: string[];
  onError?: "stop" | "continue";
}

const N = (id: string, deps: Dep[] = [], extra: Partial<NodeSpec> = {}): NodeSpec => ({
  id,
  deps,
  ...extra,
});

function graphOf(...specs: NodeSpec[]): WorkflowRunInput["graph"] {
  return {
    nodes: specs.map((s) => ({
      id: s.id,
      type: "code",
      deps: s.deps.map((d) => (typeof d === "string" ? { from: d } : { from: d[0], handle: d[1] })),
      policy: { ...POLICY, ...(s.onError ? { onError: s.onError } : {}) },
      ...(s.handles ? { handles: s.handles } : {}),
    })),
  };
}

const waitFor2h = { contractVersion: 1, kind: "wait", durationMs: 2 * HOUR };
const failed = () => fixture("prepare.final.failed.valid");
const selecting = (...handles: string[]) => ({
  ...fixture("prepare.final.valid"),
  selectedHandles: handles,
});

export function defineRunTaskSuite(backend: AgentBackend): void {
  describe(`workflow run task (${backend.name})`, () => {
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
        tasks: [createWorkflowNodeTask({ gateway }), createWorkflowRunTask({ gateway })],
      });
      workers.push(w);
      return w;
    }

    async function startRun(
      w: AgentWorker,
      graph: WorkflowRunInput["graph"],
      over: Partial<WorkflowRunInput> = {},
      checkpoint?: unknown,
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
        ...(checkpoint === undefined ? {} : { checkpoint: checkpoint as never }),
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

    const finalizeBody = () => be.calls("finalize").map((r) => r.body);

    it("a linear 3-node graph runs in order and finalises completed", async () => {
      const w = await open();
      const run = await startRun(w, graphOf(N("a"), N("b", ["a"]), N("c", ["b"])));
      const value = runValue(await w.harness.runtime.waitForTask(run.id));
      expect(value).toEqual({
        status: "completed",
        nodes: { a: "success", b: "success", c: "success" },
      });
      expect(be.keys("prepare")).toEqual([
        "run-1:a:r0:a0:prepare",
        "run-1:b:r0:a0:prepare",
        "run-1:c:r0:a0:prepare",
      ]);
      expect(finalizeBody()).toEqual([
        {
          contractVersion: 1,
          status: "completed",
          nodeSummaries: { a: "success", b: "success", c: "success" },
          attempts: 1,
        },
      ]);
      expect(be.calls("finalize")[0]?.headers["idempotency-key"]).toBe("run-1:r0:finalize");
    });

    it("a diamond runs both branches concurrently and the join node once, after both", async () => {
      be.script("b", "prepare", { body: waitFor2h });
      be.script("c", "prepare", { body: waitFor2h });
      const w = await open();
      const run = await startRun(
        w,
        graphOf(N("a"), N("b", ["a"]), N("c", ["a"]), N("d", ["b", "c"])),
      );
      await waitFor(() => be.total("b", "prepare") === 1 && be.total("c", "prepare") === 1);
      await w.harness.runtime.waitForIdle();
      // Both branches are parked at once; the join has not started.
      expect(be.total("d", "prepare")).toBe(0);
      fx.clock.advance(2 * HOUR);
      const value = runValue(await w.harness.runtime.waitForTask(run.id));
      expect(value.status).toBe("completed");
      expect(be.total("d", "prepare")).toBe(1);
      const keys = be.keys("prepare");
      expect(keys.indexOf("run-1:d:r0:a0:prepare")).toBeGreaterThan(
        Math.max(keys.indexOf("run-1:b:r0:a0:prepare"), keys.indexOf("run-1:c:r0:a0:prepare")),
      );
    });

    it("a condition selecting 'true' skips the false branch and its descendants; a merge with one live input runs", async () => {
      be.script("c", "prepare", { body: selecting("true") });
      const w = await open();
      const run = await startRun(
        w,
        graphOf(
          N("c", [], { handles: ["true", "false"] }),
          N("t", [["c", "true"]]),
          N("f", [["c", "false"]]),
          N("f2", ["f"]),
          N("m", ["t", "f2"]),
        ),
      );
      const value = runValue(await w.harness.runtime.waitForTask(run.id));
      expect(value).toEqual({
        status: "completed",
        nodes: { c: "success", t: "success", f: "skipped", f2: "skipped", m: "success" },
      });
      expect(be.total("f", "prepare")).toBe(0);
      expect(be.total("f2", "prepare")).toBe(0);
      expect(be.total("m", "prepare")).toBe(1);
    });

    it("failFast: a failing branch aborts the sibling in flight and the run fails with its error", async () => {
      be.script("b", "prepare", { body: failed() });
      be.script("c", "prepare", { body: waitFor2h });
      const w = await open();
      const run = await startRun(
        w,
        graphOf(N("a"), N("b", ["a"]), N("c", ["a"]), N("d", ["b", "c"])),
      );
      const value = runValue(await w.harness.runtime.waitForTask(run.id));
      expect(value).toMatchObject({
        status: "failed",
        nodes: { a: "success", b: "failed", c: "skipped", d: "skipped" },
        error: { code: "node_failed", message: "node blew up", nodeId: "b" },
      });
      const sibling = w.harness.runtime
        .tasks({ conversationId: run.conversationId })
        .find((t) => t.kind === "workflow_node" && (t.input as { nodeId: string }).nodeId === "c");
      expect(sibling?.outcome).toEqual({ type: "aborted", reason: "sibling_failed" });
      expect(be.total("d", "prepare")).toBe(0);
      expect(finalizeBody()).toHaveLength(1);
      expect(finalizeBody()[0]).toMatchObject({ status: "failed", error: { code: "node_failed" } });
    });

    it("continue mode: the failure is recorded, the other branch completes, and the run fails with all settled", async () => {
      be.script("b", "prepare", { body: failed() });
      const w = await open();
      const run = await startRun(
        w,
        graphOf(N("a"), N("b", ["a"]), N("c", ["a"]), N("d", ["b"]), N("e", ["c"])),
        { errorMode: "continue" },
      );
      const value = runValue(await w.harness.runtime.waitForTask(run.id));
      expect(value).toMatchObject({
        status: "failed",
        nodes: { a: "success", b: "failed", c: "success", d: "skipped", e: "success" },
        error: { code: "node_failed", message: "node blew up" },
      });
      expect(be.total("e", "prepare")).toBe(1);
    });

    it("a failure the node's own onError=continue caught does not fail the run, and its successors run", async () => {
      be.script("b", "prepare", { body: failed() });
      const w = await open();
      const run = await startRun(
        w,
        graphOf(N("a"), N("b", ["a"], { onError: "continue" }), N("c", ["b"])),
      );
      const value = runValue(await w.harness.runtime.waitForTask(run.id));
      expect(value).toEqual({
        status: "completed",
        nodes: { a: "success", b: "failed", c: "success" },
      });
    });

    it("wave dispatch: a fast branch's successor starts only after the slow node settles", async () => {
      be.script("slow", "prepare", { body: waitFor2h });
      const w = await open();
      const run = await startRun(
        w,
        graphOf(N("root"), N("slow", ["root"]), N("fast", ["root"]), N("fastNext", ["fast"])),
      );
      await waitFor(() => be.total("slow", "prepare") === 1 && be.total("fast", "prepare") === 1);
      await w.harness.runtime.waitForIdle();
      fx.clock.advance(2 * HOUR - 1);
      await w.harness.runtime.waitForIdle();
      // fast already settled, but its successor waits for the whole wave.
      expect(be.total("fastNext", "prepare")).toBe(0);
      fx.clock.advance(1);
      const value = runValue(await w.harness.runtime.waitForTask(run.id));
      expect(value.status).toBe("completed");
      expect(be.total("fastNext", "prepare")).toBe(1);
    });

    it("a restart in the middle of a join duplicates no node prepare", async () => {
      be.script("b", "prepare", { body: waitFor2h });
      const w1 = await open();
      const run = await startRun(
        w1,
        graphOf(N("a"), N("b", ["a"]), N("c", ["a"]), N("d", ["b", "c"])),
      );
      await waitFor(() => be.total("b", "prepare") === 1 && be.total("c", "prepare") === 1);
      await w1.harness.runtime.waitForIdle();
      await w1.crash();

      const w2 = await open();
      fx.clock.advance(2 * HOUR);
      const value = runValue(await w2.harness.runtime.waitForTask(run.id));
      expect(value.status).toBe("completed");
      for (const key of be.keys("prepare")) expect(be.executions(key)).toBe(1);
      expect(be.keys("prepare").filter((k) => k.includes(":d:"))).toHaveLength(1);
      expect(be.total("a", "prepare")).toBe(1);
      expect(be.calls("finalize")).toHaveLength(1);
    });

    it("aborting the container conversation ends every child aborted and finalizes once as cancelled", async () => {
      be.script("b", "prepare", { body: waitFor2h });
      const w = await open();
      const run = await startRun(w, graphOf(N("a"), N("b", ["a"]), N("c", ["b"])));
      await waitFor(() => be.total("b", "prepare") === 1);
      await w.harness.runtime.waitForIdle();
      await run.abort();
      const final = await w.harness.runtime.waitForTask(run.id);
      expect(final.outcome?.type).toBe("aborted");
      const children = w.harness.runtime
        .tasks({ conversationId: run.conversationId })
        .filter((t) => t.kind === "workflow_node");
      expect(children.length).toBeGreaterThan(0);
      expect(children.every((t) => t.status === "terminal")).toBe(true);
      expect(
        children.filter((t) => t.outcome?.type !== "done").map((t) => t.outcome?.type),
      ).toEqual(["aborted"]);
      expect(be.calls("finalize")).toHaveLength(1);
      expect(finalizeBody()[0]).toMatchObject({
        status: "cancelled",
        nodeSummaries: { a: "success", b: "skipped", c: "skipped" },
        attempts: 1,
      });
    });

    it("the golden previous-version run checkpoint decodes and the run completes", async () => {
      const w = await open();
      const golden = fixture("checkpoint.previous-version");
      const run = await startRun(w, graphOf(N("n1"), N("n2", ["n1"])), {}, golden);
      const value = runValue(await w.harness.runtime.waitForTask(run.id));
      expect(value).toEqual({ status: "completed", nodes: { n1: "success", n2: "success" } });
      expect(be.total("n1", "prepare")).toBe(0);
      expect(be.total("n2", "prepare")).toBe(1);
    });

    it("a checkpoint from a newer version faults the run with unsupported_checkpoint_version", async () => {
      const w = await open();
      const run = await startRun(w, graphOf(N("n1")), {}, { version: 99 });
      const final = await w.harness.runtime.waitForTask(run.id);
      expect(final.outcome).toMatchObject({
        type: "faulted",
        message: expect.stringContaining("unsupported_checkpoint_version"),
      });
    });

    it("the view shows a compact nodes summary", async () => {
      be.script("b", "prepare", { body: waitFor2h });
      const w = await open();
      const run = await startRun(w, graphOf(N("a"), N("b", ["a"])));
      await waitFor(() => be.total("b", "prepare") === 1);
      await w.harness.runtime.waitForIdle();
      const view = await w.harness.session.reader.getConversation(run.conversationId);
      expect(view).not.toBeNull();
      const task = w.harness.runtime.task(run.id) as TaskRecord;
      const def = w.harness.runtime.registry.get(WORKFLOW_RUN_KIND);
      const shown = def?.view?.({
        ...task,
        input: task.input,
        checkpoint: task.checkpoint,
      } as never);
      expect(shown).toMatchObject({
        label: "run-1",
        attempt: 1,
        nodes: { a: "success", b: "running" },
      });
    });

    describe("POST /v1/runs", () => {
      async function serveRuns() {
        const w = await open();
        const srv = await serve(w.harness);
        closers.push(() => srv.close());
        return { w, url: srv.url };
      }

      const post = (url: string, id: string, graph: unknown) =>
        call(url, "POST", "/v1/runs", {
          headers: AUTH,
          body: {
            id,
            kind: WORKFLOW_RUN_KIND,
            input: {
              version: 1,
              runId: id,
              runAttempt: 0,
              graph,
              variables: {},
              errorMode: "stop",
            },
          },
        });

      it("rejects a cycle, an unknown dependency and 96 nodes with 400 and spawns nothing; accepts 95", async () => {
        const { w, url } = await serveRuns();
        const chain = (n: number) =>
          graphOf(...Array.from({ length: n }, (_, i) => N(`n${i}`, i ? [`n${i - 1}`] : [])));
        const bad = [graphOf(N("a", ["b"]), N("b", ["a"])), graphOf(N("a", ["ghost"])), chain(96)];
        for (const [i, graph] of bad.entries()) {
          const res = await post(url, `bad-${i}`, graph);
          expect(res.status).toBe(400);
          expect(res.body.error).toBe("bad_request");
          expect(await w.harness.session.reader.getConversation(`bad-${i}`)).toBeNull();
        }
        expect(w.harness.runtime.tasks().filter((t) => t.kind === WORKFLOW_RUN_KIND)).toHaveLength(
          0,
        );

        const ok = await post(url, "ok-95", chain(95));
        expect(ok.status).toBe(201);
        await w.harness.runtime.waitForTask(ok.body.taskId as string);
      });
    });
  });
}
