/**
 * The `workflow_run` task (plan 2026-10-17-003, U12, KTD3/KTD4/KTD5/KTD16):
 * the DAG scheduler of one workflow run. It owns a ready-set, spawns one
 * `workflow_node` child per ready node, joins, and finalises. It holds no node
 * semantics: node steps and the final status are keyed backend calls.
 *
 * Input: `{ version: 1, runId, runAttempt, graph, variables, errorMode }`. The
 * compiled `graph` is described in `graph.ts`; `variables` stay with the
 * backend, which owns the run (the engine never reads them).
 *
 * Phases:
 * - `start`: spawn every ready node as a `workflow_node` child owned by the run,
 *   and park in `join`. Also the re-entry point after a checkpoint of the
 *   previous version is upgraded.
 * - `join`: `waiting({ on: wave, policy })` with `failFast` for `errorMode:
 *   "stop"` and `allSettled` for `continue`. The wait wakes when the whole
 *   wave is terminal, so dispatch is in waves (KTD3): the next nodes start
 *   when every node of the spawned wave has settled, and a slow branch delays
 *   the later nodes of a fast one. On wake the committed outcome of each child
 *   updates the node states in the checkpoint, branch selection
 *   (`selectedHandles`) and skip propagation run, and the newly ready nodes
 *   are spawned, or the run moves to `finalize`.
 * - `finalize`: call the backend `finalize` (key `{runId}:r{runAttempt}:finalize`)
 *   with the status, node summaries and the first uncaught error, then `done`.
 *
 * Failures. A node whose own policy stops on error faults its task; under
 * `stop` the `failFast` wait aborts the siblings still in flight (reason
 * `sibling_failed`, recorded as `skipped`) and the run ends `failed` with the
 * failing node's error. Under `continue` the failure is recorded, its
 * descendants are skipped, the other branches finish, and the run ends
 * `failed` with everything settled. A failure a node's own `onError:
 * "continue"` caught does not fail the run.
 *
 * Fan-out is not served here yet (U34): a node that parks on `fanout` signals
 * the run (`parked`), and the run aborts it and records `fanout_unsupported`.
 *
 * Abort. The run owns its node children, so the runtime aborts them first
 * (bottom-up); `onAbort` then calls `finalize` once with `cancelled`. The key
 * makes a repeat after a crash harmless.
 *
 * Checkpoints carry `version`; a checkpoint of the previous version is
 * upgraded in memory and a newer one faults the task with
 * `unsupported_checkpoint_version` (KTD16). They hold node ids, statuses and
 * task ids only (KTD17).
 */
import type { Context } from "../core/context.js";
import type { JsonObject, JsonValue } from "../core/json.js";
import {
  type AbortHandler,
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
import { CheckpointError, decodeCheckpoint } from "./contract.js";
import {
  type ErrorMode,
  GraphError,
  isSettled,
  type NodeRunState,
  type NodeStates,
  resolveGraph,
  validateGraph,
  type WorkflowGraph,
} from "./graph.js";
import { finalizeKey, type NodeGateway } from "./node-gateway.js";
import {
  FANOUT_PARKED_SIGNAL,
  WORKFLOW_NODE_KIND,
  type WorkflowNodeCheckpoint,
  type WorkflowNodeInput,
  type WorkflowNodeValue,
} from "./node-task.js";
import {
  CHECKPOINT_VERSION,
  CONTRACT_VERSION,
  type FinalizeInput,
  type NodeStatus,
  type RunStatus,
} from "./types.js";

export const WORKFLOW_RUN_KIND = "workflow_run";

export interface WorkflowRunInput {
  version: 1;
  runId: string;
  /** The run-level retry counter (U35); part of every node key. */
  runAttempt: number;
  graph: WorkflowGraph;
  variables: JsonValue;
  errorMode: ErrorMode;
}

export interface RunFailure {
  nodeId: string;
  error: JsonValue;
}

export interface WorkflowRunCheckpoint {
  version: 1;
  nodes: NodeStates;
  /** The wave in flight: node id to its task id. */
  wave: Record<string, string>;
  /** Nodes the run aborted because they parked on an unsupported fan-out. */
  rejected: string[];
  /** The first failure nobody caught. */
  failure?: RunFailure;
}

/** The `done` value of a run task. */
export interface WorkflowRunValue {
  status: RunStatus;
  nodes: Record<string, NodeStatus>;
  error?: JsonValue;
}

export type WorkflowRunErrorCode =
  | "invalid_input"
  | "invalid_checkpoint"
  | "unsupported_checkpoint_version";

/** A run task faulted on purpose; the message starts with the stable `code`. */
export class WorkflowRunError extends Error {
  constructor(
    readonly code: WorkflowRunErrorCode,
    message: string,
  ) {
    super(`${code}: ${message}`);
    this.name = "WorkflowRunError";
  }
}

export interface WorkflowRunDeps {
  gateway: NodeGateway;
  /** Host hook run after the run's own `cancelled` finalize. */
  onAbort?: AbortHandler<WorkflowRunInput, WorkflowRunCheckpoint | null>;
}

type State = TaskState<WorkflowRunInput, WorkflowRunCheckpoint | null>;
type Scope = TaskScope<WorkflowRunCheckpoint | null>;
type Next = Transition<WorkflowRunCheckpoint | null>;

const PHASE_JOIN = "join";
const PHASE_FINALIZE = "finalize";
const FANOUT_UNSUPPORTED = "fanout_unsupported";
const SIBLING_FAILED = "sibling_failed";

/**
 * Check a run input. Returns the typed input, or throws {@link GraphError} /
 * {@link WorkflowRunError}. `POST /v1/runs` calls this (through the
 * definition's `validateInput`) so a bad graph is a 400 with nothing spawned.
 */
export function parseRunInput(raw: unknown): WorkflowRunInput {
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) {
    throw new WorkflowRunError("invalid_input", "the run input must be an object");
  }
  const input = raw as JsonObject;
  if (input.version !== 1) throw new WorkflowRunError("invalid_input", "version must be 1");
  if (typeof input.runId !== "string" || input.runId.length === 0) {
    throw new WorkflowRunError("invalid_input", "runId must be a non-empty string");
  }
  if (
    typeof input.runAttempt !== "number" ||
    !Number.isInteger(input.runAttempt) ||
    input.runAttempt < 0
  ) {
    throw new WorkflowRunError("invalid_input", "runAttempt must be a non-negative integer");
  }
  if (input.errorMode !== "stop" && input.errorMode !== "continue") {
    throw new WorkflowRunError("invalid_input", 'errorMode must be "stop" or "continue"');
  }
  const graph = validateGraph(input.graph);
  return {
    version: 1,
    runId: input.runId,
    runAttempt: input.runAttempt,
    graph,
    variables: input.variables ?? null,
    errorMode: input.errorMode,
  };
}

/** `null` when the input is acceptable, otherwise the reason. */
export function runInputProblem(raw: unknown): string | null {
  try {
    parseRunInput(raw);
    return null;
  } catch (err) {
    if (err instanceof GraphError || err instanceof WorkflowRunError) return err.message;
    throw err;
  }
}

function initialStates(graph: WorkflowGraph): NodeStates {
  return Object.fromEntries(graph.nodes.map((n) => [n.id, { status: "pending" } as NodeRunState]));
}

/** The run's checkpoint, upgraded from the previous version when needed and filled in for every node. */
function load(task: State): WorkflowRunCheckpoint {
  const { graph } = task.input;
  if (task.checkpoint === null || task.checkpoint === undefined) {
    return { version: CHECKPOINT_VERSION, nodes: initialStates(graph), wave: {}, rejected: [] };
  }
  let cp: WorkflowRunCheckpoint;
  try {
    cp = decodeCheckpoint<WorkflowRunCheckpoint>(task.checkpoint, {
      upgrade: (previous) => {
        // Version 0 kept bare statuses and a derived `pending` list.
        const old = (previous as JsonObject).nodes;
        const nodes: NodeStates = {};
        if (old !== null && typeof old === "object" && !Array.isArray(old)) {
          for (const [id, status] of Object.entries(old)) {
            if (typeof status === "string") {
              nodes[id] = { status: status as NodeRunState["status"] };
            }
          }
        }
        return { nodes, wave: {}, rejected: [] } as unknown as JsonValue;
      },
    });
  } catch (err) {
    if (err instanceof CheckpointError) throw new WorkflowRunError(err.code, err.message);
    throw err;
  }
  const nodes = { ...initialStates(graph), ...cp.nodes };
  return { ...cp, nodes, wave: cp.wave ?? {}, rejected: cp.rejected ?? [] };
}

function summaries(states: NodeStates): Record<string, NodeStatus> {
  const out: Record<string, NodeStatus> = {};
  for (const [id, state] of Object.entries(states)) {
    out[id] = isSettled(state) ? (state.status as NodeStatus) : "skipped";
  }
  return out;
}

function finalizeInput(task: State, cp: WorkflowRunCheckpoint, status: RunStatus): FinalizeInput {
  return {
    contractVersion: CONTRACT_VERSION,
    status,
    nodeSummaries: summaries(cp.nodes),
    ...(status === "failed" && cp.failure ? { error: cp.failure.error } : {}),
    attempts: task.input.runAttempt + 1,
  };
}

/** Split a node task's fault message `code: text` into its parts. */
function faultError(message: string): JsonValue {
  if (message.includes("signal already delivered")) {
    return { code: FANOUT_UNSUPPORTED, message: "fan-out nodes are not supported by this run" };
  }
  const m = /^([a-z_]+): ([\s\S]*)$/.exec(message);
  return m ? { code: m[1] as string, message: m[2] as string } : { code: "node_failed", message };
}

/**
 * Settle the wave's children into node states. Pure over the committed task
 * records. Returns null when a child has not ended yet.
 */
function collect(
  cp: WorkflowRunCheckpoint,
  rt: Scope,
): { nodes: NodeStates; failure: RunFailure | undefined } | null {
  const nodes: NodeStates = { ...cp.nodes };
  let failure = cp.failure;
  for (const [nodeId, taskId] of Object.entries(cp.wave)) {
    const child = rt.task(taskId);
    if (child?.status !== "terminal" || !child.outcome) return null;
    const outcome = child.outcome;
    if (outcome.type === "done") {
      const value = outcome.value as unknown as WorkflowNodeValue;
      const caught = value.status === "failed" && value.continued === true;
      nodes[nodeId] = {
        status: value.status,
        ...(value.selectedHandles === undefined ? {} : { handles: value.selectedHandles }),
        ...(caught ? { continued: true } : {}),
      };
      if (value.status === "failed" && !caught && !failure) {
        failure = {
          nodeId,
          error: value.error ?? { code: "node_failed", message: "the node failed" },
        };
      }
      continue;
    }
    if (outcome.type === "aborted" && cp.rejected.includes(nodeId)) {
      nodes[nodeId] = { status: "failed" };
      failure ??= {
        nodeId,
        error: { code: FANOUT_UNSUPPORTED, message: "fan-out nodes are not supported by this run" },
      };
      continue;
    }
    if (outcome.type === "aborted" && outcome.reason === SIBLING_FAILED) {
      nodes[nodeId] = { status: "skipped" };
      continue;
    }
    nodes[nodeId] = { status: "failed" };
    failure ??= {
      nodeId,
      error:
        outcome.type === "faulted"
          ? { ...(faultError(outcome.message) as JsonObject), nodeId }
          : { code: "aborted", message: outcome.type === "aborted" ? outcome.reason : "aborted" },
    };
  }
  return { nodes, failure };
}

export function createWorkflowRunTask(deps: WorkflowRunDeps): TaskDefinition<
  WorkflowRunInput,
  WorkflowRunCheckpoint | null
> & {
  validateInput(input: unknown): string | null;
} {
  /** Resolve skips, spawn the ready nodes as the next wave, or move on to `finalize`. */
  function advance(
    task: State,
    rt: Scope,
    ops: TaskOps,
    cp: WorkflowRunCheckpoint,
    nodes: NodeStates,
  ): Next {
    const { graph, errorMode, runId, runAttempt } = task.input;
    const { states, ready } = resolveGraph(graph, nodes);
    const stopped = errorMode === "stop" && cp.failure !== undefined;
    if (stopped || ready.length === 0) {
      return next(PHASE_FINALIZE, { ...cp, nodes: states, wave: {} });
    }
    const def = rt.runtime.registry.get(WORKFLOW_NODE_KIND) as unknown as
      | TaskDefinition<WorkflowNodeInput, WorkflowNodeCheckpoint | null>
      | undefined;
    if (!def) throw new Error(`the ${WORKFLOW_NODE_KIND} task kind is not registered`);
    const wave: Record<string, string> = {};
    for (const nodeId of ready) {
      const node = graph.nodes.find((n) => n.id === nodeId);
      if (!node) continue;
      const spawned = ops.spawn(def, {
        input: {
          version: 1,
          runId,
          runAttempt,
          nodeId,
          nodeType: node.type,
          policy: node.policy,
          gateTaskId: task.id,
        },
      });
      wave[nodeId] = spawned.id;
      states[nodeId] = { status: "running" };
    }
    return waiting({
      on: Object.values(wave),
      policy: errorMode === "stop" ? "failFast" : "allSettled",
      // The parked signal can be delivered once; after that waiting on it would wake at once.
      ...(FANOUT_PARKED_SIGNAL in task.signals ? {} : { signal: FANOUT_PARKED_SIGNAL }),
      phase: PHASE_JOIN,
      checkpoint: { ...cp, nodes: states, wave },
    });
  }

  const start = async (task: State, rt: Scope): Promise<Committed> => {
    const cp = load(task);
    return rt.commit((_tx, ops) => advance(task, rt, ops, cp, cp.nodes));
  };

  const join = async (task: State, rt: Scope): Promise<Committed> => {
    const cp = load(task);
    return rt.commit((_tx, ops) => {
      const wake = task.wake;
      if (wake?.reason === "signal" && wake.key === FANOUT_PARKED_SIGNAL) {
        // U34 serves fan-out; until then a parked node is a failure.
        const digest = task.signals[FANOUT_PARKED_SIGNAL];
        const nodeId =
          digest !== null && typeof digest === "object" && !Array.isArray(digest)
            ? (digest as JsonObject).nodeId
            : undefined;
        const childId = typeof nodeId === "string" ? cp.wave[nodeId] : undefined;
        const rejected = [...cp.rejected];
        if (typeof nodeId === "string" && childId !== undefined && !rejected.includes(nodeId)) {
          ops.abort(childId, FANOUT_UNSUPPORTED);
          rejected.push(nodeId);
        }
        return waiting({
          on: Object.values(cp.wave),
          policy: task.input.errorMode === "stop" ? "failFast" : "allSettled",
          phase: PHASE_JOIN,
          checkpoint: { ...cp, rejected },
        });
      }
      const settled = collect(cp, rt);
      if (!settled) {
        const live = Object.values(cp.wave).filter((id) => rt.task(id)?.status !== "terminal");
        return waiting({
          on: live,
          policy: task.input.errorMode === "stop" ? "failFast" : "allSettled",
          phase: PHASE_JOIN,
        });
      }
      const after: WorkflowRunCheckpoint = {
        ...cp,
        wave: {},
        ...(settled.failure ? { failure: settled.failure } : {}),
      };
      return advance(task, rt, ops, after, settled.nodes);
    });
  };

  const finalize = async (task: State, rt: Scope, ctx: Context): Promise<Committed> => {
    const cp = load(task);
    const status: RunStatus = cp.failure ? "failed" : "completed";
    const body = finalizeInput(task, cp, status);
    await deps.gateway.finalize(
      task.input.runId,
      finalizeKey(task.input.runId, task.input.runAttempt),
      body,
      { signal: ctx.signal },
    );
    const value: WorkflowRunValue = {
      status,
      nodes: body.nodeSummaries,
      ...(body.error === undefined ? {} : { error: body.error }),
    };
    return rt.commit(() => done(value as unknown as JsonValue));
  };

  const def = defineTask<WorkflowRunInput, WorkflowRunCheckpoint | null>({
    kind: WORKFLOW_RUN_KIND,
    phases: { start, [PHASE_JOIN]: join, [PHASE_FINALIZE]: finalize },
    initialPhase: "start",
    onAbort: async (task, scope, ctx) => {
      // Children have stopped (bottom-up). Report the cancellation once.
      const cp = load(task);
      await deps.gateway.finalize(
        task.input.runId,
        finalizeKey(task.input.runId, task.input.runAttempt),
        finalizeInput(task, cp, "cancelled"),
        { signal: ctx.signal },
      );
      await deps.onAbort?.(task, scope, ctx);
    },
    view: (task) => {
      const states = task.checkpoint?.nodes;
      return {
        label: task.input.runId,
        attempt: task.input.runAttempt + 1,
        ...(states ? { nodes: summariesView(states) } : {}),
      };
    },
  });
  return Object.freeze({ ...def, validateInput: runInputProblem });
}

/** The compact `{ nodeId: status }` the watch view shows. */
function summariesView(states: NodeStates): Record<string, string> {
  return Object.fromEntries(Object.entries(states).map(([id, s]) => [id, s.status]));
}
