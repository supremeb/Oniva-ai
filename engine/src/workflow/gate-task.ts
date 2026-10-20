/**
 * The `workflow_gate` task (plan 2026-10-17-003, U34, KTD3).
 *
 * A wait wakes only when every awaited task is terminal or a signal is
 * delivered, so a run that waits on a node alone cannot learn that the node
 * parked on a fan-out. For each node its graph marks `mayFanOut`, the run
 * spawns a gate next to the node and waits on the gate instead. The gate waits
 * on `{ on: [node], signal: "parked" }`: it wakes when the node parks (the node
 * delivers `parked`, U11) or ends for any reason, and then ends. A node that
 * ended `faulted` faults its gate, so the run's `failFast` wait sees the
 * failure at once. The run reads the node itself to tell parked from ended.
 *
 * The gate holds no node semantics. It is re-run safely: both phases only read.
 */
import { defineTask, done, type TaskDefinition, waiting } from "../tasks/define.js";
import { FANOUT_PARKED_SIGNAL } from "./node-task.js";

export const WORKFLOW_GATE_KIND = "workflow_gate";

export interface WorkflowGateInput {
  version: 1;
  /** The node task this gate watches. */
  nodeTaskId: string;
}

export class WorkflowGateError extends Error {
  constructor(message: string) {
    super(`node_failed: ${message}`);
    this.name = "WorkflowGateError";
  }
}

/** The create-once gate task definition. */
export function createWorkflowGateTask(): TaskDefinition<WorkflowGateInput, null> {
  return defineTask<WorkflowGateInput, null>({
    kind: WORKFLOW_GATE_KIND,
    initialPhase: "watch",
    phases: {
      watch: async (task, rt) =>
        rt.commit(() =>
          waiting({
            on: [task.input.nodeTaskId],
            signal: FANOUT_PARKED_SIGNAL,
            phase: "woke",
            checkpoint: null,
          }),
        ),
      woke: async (task, rt) => {
        const node = rt.task(task.input.nodeTaskId);
        const outcome = node?.status === "terminal" ? node.outcome : null;
        return rt.commit(() => {
          if (outcome?.type === "faulted") throw new WorkflowGateError(outcome.message);
          return done(null);
        });
      },
    },
    view: (task) => ({ label: `${task.input.nodeTaskId}:gate` }),
  });
}
