/**
 * The four workflow task kinds a worker registers (plan 2026-10-17-003, U35,
 * KTD16): `workflow_run`, `workflow_node`, `workflow_fanout_lane` and
 * `workflow_gate`, sharing one node gateway client. A worker passes them as
 * `hostTasks`, which is what lists them in `/v1/health` `taskKinds` and lets
 * the backend capability probe pass.
 */
import type { TaskDefinition } from "../tasks/define.js";
import { createWorkflowFanoutLaneTask } from "./fanout-lane-task.js";
import { createWorkflowGateTask } from "./gate-task.js";
import type { NodeGateway } from "./node-gateway.js";
import { createWorkflowNodeTask } from "./node-task.js";
import { createWorkflowRunTask } from "./run-task.js";

export function createWorkflowTasks(gateway: NodeGateway): readonly TaskDefinition[] {
  return [
    createWorkflowRunTask({ gateway }),
    createWorkflowNodeTask({ gateway }),
    createWorkflowFanoutLaneTask(),
    createWorkflowGateTask(),
  ] as unknown as readonly TaskDefinition[];
}
