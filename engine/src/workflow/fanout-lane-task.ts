/**
 * The `workflow_fanout_lane` task (plan 2026-10-17-003, U34, KTD3/KTD17).
 *
 * A parked fan-out node with `count` items and `parallel` P is served by
 * `min(P, count, maxParallel)` lanes. Lane `k` handles indexes `k, k+L, k+2L, …`
 * (`L` is the lane count) strictly one at a time: for index `i` it starts a
 * child `workflow_run` with the deterministic id
 * `{runTaskId}:r{runAttempt}:{nodeId}:i{i}`, waits on it, records the item's
 * status, and moves on. At most one child per lane is live, so at most P
 * children are live at any commit, and a restart resumes each lane from its
 * checkpointed `nextIndex`: the spawn and the checkpoint are one commit, so a
 * finished item is never started again and the bound survives the restart.
 *
 * The item's graph is the `target` resolved against the owning run's graph
 * (an iteration body or a referenced workflow). Its `runId` is the backend's
 * item execution id: `uuid5(runId, "{nodeId}:{index}")` when the run id is a
 * UUID (the backend creates those rows in the fan-out `prepare`), otherwise
 * `{runId}:{nodeId}:i{index}`.
 *
 * Results never pass through here (KTD17). The item run's own `finalize`
 * writes them to the backend. The lane keeps counts and, for the first
 * failure, a bounded error `{ code, message }`; its checkpoint, input and
 * `done` value do not grow with the item count.
 *
 * With `failFast`, the first failed item ends the lane and aborts its sibling
 * lanes (and so their live items); with it off, every item runs.
 */
import { createHash } from "node:crypto";
import type { JsonObject, JsonValue } from "../core/json.js";
import {
  type Committed,
  defineTask,
  done,
  next,
  type TaskDefinition,
  type TaskOps,
  type TaskScope,
  type TaskState,
  type Transition,
  waiting,
} from "../tasks/define.js";
import { CheckpointError, decodeCheckpoint, MAX_FANOUT_ITEMS, MAX_PARALLEL } from "./contract.js";
import { resolveTarget, type WorkflowGraph } from "./graph.js";
import { CHECKPOINT_VERSION, type FanoutTarget } from "./types.js";

export const WORKFLOW_FANOUT_LANE_KIND = "workflow_fanout_lane";

const RUN_KIND = "workflow_run";
const PHASE_SPAWN = "spawn";
const PHASE_COLLECT = "collect";
const SIBLING_FAILED = "sibling_failed";
const MAX_ERROR_TEXT = 256;

export interface WorkflowFanoutLaneInput {
  version: 1;
  /** The run task that owns the lane; its graph holds the target. */
  runTaskId: string;
  /** The backend run id of that run. */
  runId: string;
  runAttempt: number;
  nodeId: string;
  /** This lane's index, `0 <= lane < lanes`. */
  lane: number;
  lanes: number;
  count: number;
  failFast: boolean;
  target: FanoutTarget;
  errorMode: "stop" | "continue";
}

export interface FanoutLaneCheckpoint {
  version: 1;
  /** The next index this lane starts (or is running). */
  nextIndex: number;
  completed: number;
  failed: number;
  /** The first failure the lane saw, bounded. */
  firstError?: JsonValue;
  /** The item in flight. */
  current?: { index: number; taskId: string };
}

/** What a lane ends with (`done`): counts only. */
export interface FanoutLaneValue {
  completed: number;
  failed: number;
  firstError?: JsonValue;
}

/** How many lanes serve a fan-out: `min(parallel, count, maxParallel)`, at least 0. */
export function laneCount(count: number, parallel: number): number {
  if (count <= 0) return 0;
  return Math.max(1, Math.min(Math.floor(parallel), count, MAX_PARALLEL));
}

/** Is a count one the engine serves? */
export const countAllowed = (count: number): boolean =>
  Number.isInteger(count) && count >= 0 && count <= MAX_FANOUT_ITEMS;

/** The deterministic task id of lane `lane`. */
export function laneTaskId(
  runTaskId: string,
  runAttempt: number,
  nodeId: string,
  lane: number,
): string {
  return `${runTaskId}:r${runAttempt}:${nodeId}:lane${lane}`;
}

/** The deterministic task id of item `index`. */
export function itemTaskId(
  runTaskId: string,
  runAttempt: number,
  nodeId: string,
  index: number,
): string {
  return `${runTaskId}:r${runAttempt}:${nodeId}:i${index}`;
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** The backend run id of item `index`: `uuid5(runId, "{nodeId}:{index}")` for a UUID run id. */
export function itemRunId(runId: string, nodeId: string, index: number): string {
  if (!UUID.test(runId)) return `${runId}:${nodeId}:i${index}`;
  const namespace = Buffer.from(runId.replaceAll("-", ""), "hex");
  const hash = createHash("sha1")
    .update(namespace)
    .update(`${nodeId}:${index}`, "utf8")
    .digest()
    .subarray(0, 16);
  hash[6] = ((hash[6] as number) & 0x0f) | 0x50;
  hash[8] = ((hash[8] as number) & 0x3f) | 0x80;
  const hex = hash.toString("hex");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

type State = TaskState<WorkflowFanoutLaneInput, FanoutLaneCheckpoint | null>;
type Scope = TaskScope<FanoutLaneCheckpoint | null>;
type Next = Transition<FanoutLaneCheckpoint | null>;

function load(task: State): FanoutLaneCheckpoint {
  if (task.checkpoint === null || task.checkpoint === undefined) {
    return { version: CHECKPOINT_VERSION, nextIndex: task.input.lane, completed: 0, failed: 0 };
  }
  try {
    return decodeCheckpoint<FanoutLaneCheckpoint>(task.checkpoint, {
      upgrade: () => ({
        nextIndex: task.input.lane,
        completed: 0,
        failed: 0,
      }),
    });
  } catch (err) {
    if (err instanceof CheckpointError) throw new Error(`${err.code}: ${err.message}`);
    throw err;
  }
}

function bounded(error: JsonValue | undefined): JsonValue {
  const raw = error ?? { code: "item_failed", message: "the item failed" };
  if (typeof raw === "object" && raw !== null && !Array.isArray(raw)) {
    const o = raw as JsonObject;
    const code = typeof o.code === "string" ? o.code.slice(0, 64) : "item_failed";
    const message = typeof o.message === "string" ? o.message : code;
    return { code, message: message.slice(0, MAX_ERROR_TEXT) };
  }
  return { code: "item_failed", message: String(raw).slice(0, MAX_ERROR_TEXT) };
}

/** How an item child ended: completed, failed (with its error) or cancelled. */
function itemOutcome(
  scope: Scope,
  taskId: string,
): { kind: "completed" } | { kind: "failed"; error: JsonValue } | { kind: "cancelled" } | null {
  const child = scope.task(taskId);
  if (child?.status !== "terminal" || !child.outcome) return null;
  const outcome = child.outcome;
  if (outcome.type === "aborted") return { kind: "cancelled" };
  if (outcome.type === "faulted") {
    return { kind: "failed", error: { code: "item_faulted", message: outcome.message } };
  }
  const value = outcome.value as { status?: string; error?: JsonValue } | null;
  if (value?.status === "completed") return { kind: "completed" };
  if (value?.status === "cancelled") return { kind: "cancelled" };
  return { kind: "failed", error: bounded(value?.error) };
}

/** Spawn the item at `index` and wait for it; the checkpoint moves in the same commit. */
function startItem(task: State, scope: Scope, ops: TaskOps, cp: FanoutLaneCheckpoint): Next {
  const input = task.input;
  const run = scope.task(input.runTaskId);
  const def = scope.runtime.registry.get(RUN_KIND);
  if (!run || !def) throw new Error(`the ${RUN_KIND} task is not available to lane ${task.id}`);
  const graph = (run.input as { graph?: WorkflowGraph }).graph;
  const itemGraph = graph ? resolveTarget(graph, input.target) : null;
  if (!itemGraph) throw new Error(`fan-out target of node ${input.nodeId} is not in the run graph`);
  const index = cp.nextIndex;
  const taskId = itemTaskId(input.runTaskId, input.runAttempt, input.nodeId, index);
  ops.spawn(def as unknown as TaskDefinition<JsonValue, JsonValue>, {
    id: taskId,
    input: {
      version: 1,
      runId: itemRunId(input.runId, input.nodeId, index),
      runAttempt: input.runAttempt,
      graph: itemGraph,
      variables: null,
      errorMode: input.errorMode,
    } as unknown as JsonValue,
  });
  return waiting({
    on: [taskId],
    phase: PHASE_COLLECT,
    checkpoint: { ...cp, current: { index, taskId } },
  });
}

/** The create-once lane task definition. */
export function createWorkflowFanoutLaneTask(): TaskDefinition<
  WorkflowFanoutLaneInput,
  FanoutLaneCheckpoint | null
> {
  const finished = (cp: FanoutLaneCheckpoint): Next => {
    const value: FanoutLaneValue = {
      completed: cp.completed,
      failed: cp.failed,
      ...(cp.firstError === undefined ? {} : { firstError: cp.firstError }),
    };
    return done(value as unknown as JsonValue);
  };

  const spawn = async (task: State, rt: Scope): Promise<Committed> => {
    const cp = load(task);
    return rt.commit((_tx, ops) => {
      if (cp.nextIndex >= task.input.count) return finished(cp);
      return startItem(task, rt, ops, cp);
    });
  };

  const collect = async (task: State, rt: Scope): Promise<Committed> => {
    const cp = load(task);
    return rt.commit((_tx, ops) => {
      const current = cp.current;
      if (!current) return next(PHASE_SPAWN, cp);
      const outcome = itemOutcome(rt, current.taskId);
      if (!outcome) return waiting({ on: [current.taskId], phase: PHASE_COLLECT });
      const after: FanoutLaneCheckpoint = {
        version: CHECKPOINT_VERSION,
        nextIndex: current.index + task.input.lanes,
        completed: cp.completed + (outcome.kind === "completed" ? 1 : 0),
        failed: cp.failed + (outcome.kind === "failed" ? 1 : 0),
        ...(cp.firstError !== undefined
          ? { firstError: cp.firstError }
          : outcome.kind === "failed"
            ? { firstError: outcome.error }
            : {}),
      };
      if (outcome.kind === "failed" && task.input.failFast) {
        for (let k = 0; k < task.input.lanes; k++) {
          if (k === task.input.lane) continue;
          ops.abort(
            laneTaskId(task.input.runTaskId, task.input.runAttempt, task.input.nodeId, k),
            SIBLING_FAILED,
          );
        }
        return finished(after);
      }
      if (after.nextIndex >= task.input.count) return finished(after);
      return startItem(task, rt, ops, after);
    });
  };

  return defineTask<WorkflowFanoutLaneInput, FanoutLaneCheckpoint | null>({
    kind: WORKFLOW_FANOUT_LANE_KIND,
    initialPhase: PHASE_SPAWN,
    phases: { [PHASE_SPAWN]: spawn, [PHASE_COLLECT]: collect },
    view: (task) => ({
      label: `${task.input.nodeId}#${task.input.lane}`,
      attempt: task.input.runAttempt + 1,
    }),
  });
}
