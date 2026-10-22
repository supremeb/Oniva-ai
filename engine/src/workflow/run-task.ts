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
 * Fan-out (U34). A node the graph marks `mayFanOut` gets a `workflow_gate` task
 * next to it, and the join waits on the gate instead of the node: a wait wakes
 * only when everything it awaits is terminal, so the gate is what ends when the
 * node parks. On wake the run reads each such node. A terminal one settles as
 * usual. A parked one (phase `awaitFanout`, its digest in its checkpoint) is
 * served here: the run spawns `min(parallel, count, maxParallel)`
 * `workflow_fanout_lane` children (`fanout-lane-task.ts`), moves to `fan`, and
 * waits on the lanes. When they are all terminal it sums their counts and
 * delivers the counts-only `fanout-result` signal to the node task, which then
 * calls `finish` itself under its own key and ends. The run never calls
 * `finish`. Results never pass through checkpoints or signals (KTD17). A digest
 * the run cannot serve (count over the limit, a target the graph lacks) is
 * answered with a failure summary and no lanes.
 *
 * Retry and deadline (U35). `retry: { max, delayMs }` retries the run as a
 * whole: a run that ends `failed` with attempts left does not finalize. It
 * enters `waiting({ until: clock.now() + delayMs })` and then goes back to
 * `start` with a fresh checkpoint and `attempt + 1` (the checkpoint's `attempt`,
 * which starts at the input's `runAttempt`). Node and lane task ids and every
 * node key carry `r{attempt}` (KTD4), so the retry re-executes nodes that failed
 * earlier instead of replaying their stored failure, and a fan-out starts
 * afresh. `finalize` is called once, after the last attempt, with `attempts`.
 * A retry that would wake at or after the deadline is not taken. `deadlineMs`
 * (absolute engine-clock epoch ms) bounds every wait of the run. When it is
 * reached the run aborts its live children (reason `run_deadline`, recorded as
 * `skipped`) and ends `failed` with `deadline_exceeded`; a deadline failure
 * never retries.
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
import type { TaskRecord } from "../storage/types.js";
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
import { CheckpointError, decodeCheckpoint, MAX_FANOUT_ITEMS } from "./contract.js";
import {
  countAllowed,
  type FanoutLaneCheckpoint,
  type FanoutLaneValue,
  laneCount,
  laneTaskId,
  WORKFLOW_FANOUT_LANE_KIND,
  type WorkflowFanoutLaneInput,
} from "./fanout-lane-task.js";
import { WORKFLOW_GATE_KIND, type WorkflowGateInput } from "./gate-task.js";
import {
  type ErrorMode,
  GraphError,
  isSettled,
  type NodeRunState,
  type NodeStates,
  resolveGraph,
  resolveTarget,
  validateGraph,
  type WorkflowGraph,
} from "./graph.js";
import { finalizeKey, type NodeGateway } from "./node-gateway.js";
import {
  FANOUT_RESULT_SIGNAL,
  type FanoutDigest,
  WORKFLOW_NODE_KIND,
  type WorkflowNodeCheckpoint,
  type WorkflowNodeInput,
  type WorkflowNodeValue,
} from "./node-task.js";
import {
  CHECKPOINT_VERSION,
  CONTRACT_VERSION,
  type FanoutSummary,
  type FinalizeInput,
  type NodeStatus,
  type RunStatus,
} from "./types.js";

export const WORKFLOW_RUN_KIND = "workflow_run";

/** Cap on run-level retries (the backend scheduler allows at most 10); a larger request is clamped. */
export const MAX_RUN_RETRIES = 10;

export interface WorkflowRunInput {
  version: 1;
  runId: string;
  /** The first run-level attempt (U35); part of every node key. The checkpoint's `attempt` advances it. */
  runAttempt: number;
  /** Retry the whole run on failure: up to `max` more attempts, `delayMs` apart (engine clock). */
  retry?: RunRetry | undefined;
  /** Absolute engine-clock epoch ms after which the run ends `failed` with `deadline_exceeded`. */
  deadlineMs?: number | undefined;
  graph: WorkflowGraph;
  variables: JsonValue;
  errorMode: ErrorMode;
}

export interface RunRetry {
  max: number;
  delayMs: number;
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
  /** Gate tasks of the wave: node id to gate task id (only `mayFanOut` nodes). */
  gates: Record<string, string>;
  /** Fan-outs being served: node id to its lane count and item count. Counts only. */
  fan: Record<string, { lanes: number; count: number }>;
  /** The first failure nobody caught. */
  failure?: RunFailure;
  /** The current run attempt; absent means the input's `runAttempt`. */
  attempt?: number;
  /** The run hit its deadline; it finalizes `failed` and never retries. */
  expired?: boolean;
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
const PHASE_FAN = "fan";
const PHASE_FINALIZE = "finalize";
const SIBLING_FAILED = "sibling_failed";
const RUN_DEADLINE = "run_deadline";
const DEADLINE_ERROR: JsonValue = {
  code: "deadline_exceeded",
  message: "the run passed its deadline",
};

/** The run attempt in force: the checkpoint's, else the input's. */
const attemptOf = (task: State, cp: WorkflowRunCheckpoint | null | undefined): number =>
  cp?.attempt ?? task.input.runAttempt;

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
  let retry: RunRetry | undefined;
  if (input.retry !== undefined) {
    const r = input.retry as JsonObject | null;
    if (
      r === null ||
      typeof r !== "object" ||
      Array.isArray(r) ||
      typeof r.max !== "number" ||
      !Number.isInteger(r.max) ||
      r.max < 0 ||
      typeof r.delayMs !== "number" ||
      !Number.isFinite(r.delayMs) ||
      r.delayMs < 0
    ) {
      throw new WorkflowRunError(
        "invalid_input",
        "retry must be { max: non-negative integer, delayMs: non-negative number }",
      );
    }
    retry = { max: Math.min(r.max, MAX_RUN_RETRIES), delayMs: r.delayMs };
  }
  if (
    input.deadlineMs !== undefined &&
    (typeof input.deadlineMs !== "number" || !Number.isFinite(input.deadlineMs))
  ) {
    throw new WorkflowRunError("invalid_input", "deadlineMs must be a finite epoch ms number");
  }
  const graph = validateGraph(input.graph);
  return {
    version: 1,
    runId: input.runId,
    runAttempt: input.runAttempt,
    graph,
    variables: input.variables ?? null,
    errorMode: input.errorMode,
    ...(retry === undefined ? {} : { retry }),
    ...(input.deadlineMs === undefined ? {} : { deadlineMs: input.deadlineMs }),
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
    return {
      version: CHECKPOINT_VERSION,
      nodes: initialStates(graph),
      wave: {},
      gates: {},
      fan: {},
    };
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
        return { nodes, wave: {}, gates: {}, fan: {} } as unknown as JsonValue;
      },
    });
  } catch (err) {
    if (err instanceof CheckpointError) throw new WorkflowRunError(err.code, err.message);
    throw err;
  }
  const nodes = { ...initialStates(graph), ...cp.nodes };
  return { ...cp, nodes, wave: cp.wave ?? {}, gates: cp.gates ?? {}, fan: cp.fan ?? {} };
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
    attempts: attemptOf(task, cp) + 1,
  };
}

/** Split a node task's fault message `code: text` into its parts. */
function faultError(message: string): JsonValue {
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
    if (
      outcome.type === "aborted" &&
      (outcome.reason === SIBLING_FAILED || outcome.reason === RUN_DEADLINE)
    ) {
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
  const waitPolicy = (task: State) => (task.input.errorMode === "stop" ? "failFast" : "allSettled");

  /** Resolve skips, spawn the ready nodes as the next wave, or move on to `finalize`. */
  function advance(
    task: State,
    rt: Scope,
    ops: TaskOps,
    cp: WorkflowRunCheckpoint,
    nodes: NodeStates,
  ): Next {
    const { graph, errorMode, runId } = task.input;
    const runAttempt = attemptOf(task, cp);
    const { states, ready } = resolveGraph(graph, nodes);
    const stopped = errorMode === "stop" && cp.failure !== undefined;
    if (stopped || ready.length === 0) {
      return next(PHASE_FINALIZE, { ...cp, nodes: states, wave: {}, gates: {}, fan: {} });
    }
    const def = rt.runtime.registry.get(WORKFLOW_NODE_KIND) as unknown as
      | TaskDefinition<WorkflowNodeInput, WorkflowNodeCheckpoint | null>
      | undefined;
    if (!def) throw new Error(`the ${WORKFLOW_NODE_KIND} task kind is not registered`);
    const gateDef = rt.runtime.registry.get(WORKFLOW_GATE_KIND) as unknown as
      | TaskDefinition<WorkflowGateInput, null>
      | undefined;
    const wave: Record<string, string> = {};
    const gates: Record<string, string> = {};
    const awaited: string[] = [];
    for (const nodeId of ready) {
      const node = graph.nodes.find((n) => n.id === nodeId);
      if (!node) continue;
      // Deterministic ids: the gate's id derives from the node's, and a run retry
      // (a new runAttempt) never collides with an earlier attempt's tasks.
      const nodeTaskId = `${task.id}:r${runAttempt}:${nodeId}`;
      const gateTaskId = node.mayFanOut === true ? `${nodeTaskId}:gate` : undefined;
      if (gateTaskId !== undefined && !gateDef) {
        throw new Error(`the ${WORKFLOW_GATE_KIND} task kind is not registered`);
      }
      const spawned = ops.spawn(def, {
        id: nodeTaskId,
        input: {
          version: 1,
          runId,
          runAttempt,
          nodeId,
          nodeType: node.type,
          policy: node.policy,
          ...(gateTaskId === undefined ? {} : { gateTaskId }),
        },
      });
      wave[nodeId] = spawned.id;
      if (gateTaskId !== undefined && gateDef) {
        const gate = ops.spawn(gateDef, {
          id: gateTaskId,
          input: { version: 1, nodeTaskId: spawned.id },
        });
        gates[nodeId] = gate.id;
        awaited.push(gate.id);
      } else {
        awaited.push(spawned.id);
      }
      states[nodeId] = { status: "running" };
    }
    return waiting({
      on: awaited,
      policy: waitPolicy(task),
      ...untilDeadline(task),
      phase: PHASE_JOIN,
      checkpoint: { ...cp, nodes: states, wave, gates, fan: {} },
    });
  }

  /** The deadline as a wait bound, so a parked run wakes when it is reached. */
  function untilDeadline(task: State): { until?: number } {
    return task.input.deadlineMs === undefined ? {} : { until: task.input.deadlineMs };
  }

  const passed = (task: State, ctx: Context): boolean =>
    task.input.deadlineMs !== undefined && ctx.clock.now() >= task.input.deadlineMs;

  /**
   * The deadline is reached: abort whatever is live (nodes, gates, lanes) and
   * wait for it to stop, then settle what ran and go to `finalize` as `expired`.
   */
  function expire(task: State, rt: Scope, ops: TaskOps, cp: WorkflowRunCheckpoint): Next {
    const attempt = attemptOf(task, cp);
    const lanes = Object.entries(cp.fan).flatMap(([nodeId, f]) =>
      Array.from({ length: f.lanes }, (_, lane) => laneTaskId(task.id, attempt, nodeId, lane)),
    );
    const live = [...Object.values(cp.wave), ...Object.values(cp.gates), ...lanes].filter(
      (id) => rt.task(id) !== null && rt.task(id)?.status !== "terminal",
    );
    if (live.length > 0) {
      for (const id of live) ops.abort(id, RUN_DEADLINE);
      return waiting({ on: live, phase: task.phase, checkpoint: { ...cp, expired: true } });
    }
    const failure: RunFailure = { nodeId: "", error: DEADLINE_ERROR };
    const settled = collect({ ...cp, failure }, rt);
    return next(PHASE_FINALIZE, {
      ...cp,
      nodes: settled?.nodes ?? cp.nodes,
      wave: {},
      gates: {},
      fan: {},
      failure,
      expired: true,
    });
  }

  const start = async (task: State, rt: Scope, ctx: Context): Promise<Committed> => {
    const cp = load(task);
    return rt.commit((_tx, ops) =>
      passed(task, ctx) ? expire(task, rt, ops, cp) : advance(task, rt, ops, cp, cp.nodes),
    );
  };

  /** The digest a parked fan-out node left in its checkpoint. */
  function digestOf(node: TaskRecord | null): FanoutDigest | undefined {
    const cp = node?.checkpoint as WorkflowNodeCheckpoint | null | undefined;
    return cp?.fanout;
  }

  const failureSummary = (count: number, code: string, message: string): FanoutSummary => ({
    count,
    completed: 0,
    failed: count,
    cancelled: 0,
    firstError: { code, message },
  });

  /**
   * Serve the parked fan-out nodes: spawn their lanes (or answer at once when
   * there is nothing to run or the digest cannot be served) and wait for them.
   */
  function startFan(
    task: State,
    rt: Scope,
    ops: TaskOps,
    cp: WorkflowRunCheckpoint,
    parked: string[],
  ): Next {
    const laneDef = rt.runtime.registry.get(WORKFLOW_FANOUT_LANE_KIND) as unknown as
      | TaskDefinition<WorkflowFanoutLaneInput, FanoutLaneCheckpoint | null>
      | undefined;
    const { runId, graph, errorMode } = task.input;
    const runAttempt = attemptOf(task, cp);
    const fan: Record<string, { lanes: number; count: number }> = {};
    const laneIds: string[] = [];
    for (const nodeId of parked) {
      const nodeTaskId = cp.wave[nodeId] as string;
      const digest = digestOf(rt.task(nodeTaskId));
      if (!digest) {
        deliver(
          rt,
          ops,
          nodeTaskId,
          failureSummary(0, "fanout_digest_missing", "the node parked without a request"),
        );
        continue;
      }
      let immediate: FanoutSummary | undefined;
      if (!countAllowed(digest.count)) {
        immediate = failureSummary(
          Number.isInteger(digest.count) && digest.count > 0 ? digest.count : 0,
          "fanout_count_invalid",
          `a fan-out holds at most ${MAX_FANOUT_ITEMS} items`,
        );
      } else if (digest.count === 0) {
        immediate = { count: 0, completed: 0, failed: 0, cancelled: 0 };
      } else if (!resolveTarget(graph, digest.target)) {
        immediate = failureSummary(
          digest.count,
          "fanout_target_unknown",
          "the run graph does not hold the fan-out target",
        );
      }
      if (immediate) {
        deliver(rt, ops, nodeTaskId, immediate);
        continue;
      }
      if (!laneDef) throw new Error(`the ${WORKFLOW_FANOUT_LANE_KIND} task kind is not registered`);
      const lanes = laneCount(digest.count, digest.parallel);
      for (let lane = 0; lane < lanes; lane++) {
        const spawned = ops.spawn(laneDef, {
          id: laneTaskId(task.id, runAttempt, nodeId, lane),
          input: {
            version: 1,
            runTaskId: task.id,
            runId,
            runAttempt,
            nodeId,
            lane,
            lanes,
            count: digest.count,
            failFast: digest.failFast,
            target: digest.target,
            errorMode,
          },
        });
        laneIds.push(spawned.id);
      }
      fan[nodeId] = { lanes, count: digest.count };
    }
    if (laneIds.length === 0) {
      return waiting({
        on: Object.values(cp.wave),
        policy: waitPolicy(task),
        ...untilDeadline(task),
        phase: PHASE_JOIN,
        checkpoint: { ...cp, gates: {}, fan: {} },
      });
    }
    return waiting({
      on: laneIds,
      ...untilDeadline(task),
      phase: PHASE_FAN,
      checkpoint: { ...cp, fan },
    });
  }

  /** Deliver the counts-only summary to a parked node, once. */
  function deliver(rt: Scope, ops: TaskOps, nodeTaskId: string, summary: FanoutSummary): void {
    const node = rt.task(nodeTaskId);
    if (!node || node.status === "terminal" || FANOUT_RESULT_SIGNAL in node.signals) return;
    ops.signal(nodeTaskId, FANOUT_RESULT_SIGNAL, summary as unknown as JsonValue);
  }

  /** The counts of a fan-out, from its lanes' committed state. */
  function summarize(
    task: State,
    cp: WorkflowRunCheckpoint,
    rt: Scope,
    nodeId: string,
    f: { lanes: number; count: number },
  ): FanoutSummary {
    let completed = 0;
    let failed = 0;
    let firstError: JsonValue | undefined;
    for (let lane = 0; lane < f.lanes; lane++) {
      const rec = rt.task(laneTaskId(task.id, attemptOf(task, cp), nodeId, lane));
      const outcome = rec?.outcome;
      const part =
        outcome?.type === "done"
          ? (outcome.value as unknown as FanoutLaneValue)
          : (rec?.checkpoint as FanoutLaneCheckpoint | null | undefined);
      if (!part) continue;
      completed += part.completed ?? 0;
      failed += part.failed ?? 0;
      firstError ??= part.firstError;
    }
    return {
      count: f.count,
      completed,
      failed,
      cancelled: Math.max(0, f.count - completed - failed),
      ...(firstError === undefined ? {} : { firstError }),
    };
  }

  const join = async (task: State, rt: Scope, ctx: Context): Promise<Committed> => {
    const cp = load(task);
    return rt.commit((_tx, ops) => {
      if (passed(task, ctx)) return expire(task, rt, ops, cp);
      const policy = waitPolicy(task);
      const bound = untilDeadline(task);
      const parked: string[] = [];
      const live: string[] = [];
      const liveNodes: string[] = [];
      let faulted = false;
      for (const [nodeId, taskId] of Object.entries(cp.wave)) {
        const node = rt.task(taskId);
        if (!node || node.status === "terminal") {
          if (node?.outcome?.type === "faulted") faulted = true;
          continue;
        }
        liveNodes.push(taskId);
        const gateId = cp.gates[nodeId];
        const gate = gateId === undefined ? undefined : rt.task(gateId);
        if (gate?.status === "terminal" && node.phase === "awaitFanout") parked.push(nodeId);
        else live.push(gate && gate.status !== "terminal" ? gate.id : taskId);
      }
      if (faulted && task.input.errorMode === "stop" && liveNodes.length > 0) {
        // The node that failed ended its gate too, so failFast saw it; the nodes
        // behind the other gates are still running and stop with the run.
        const victims = [...liveNodes, ...Object.values(cp.gates)].filter(
          (id) => rt.task(id)?.status !== "terminal",
        );
        for (const id of victims) ops.abort(id, SIBLING_FAILED);
        return waiting({ on: victims, ...bound, phase: PHASE_JOIN });
      }
      if (live.length > 0) return waiting({ on: live, policy, ...bound, phase: PHASE_JOIN });
      if (parked.length > 0) return startFan(task, rt, ops, cp, parked);
      const settled = collect(cp, rt);
      if (!settled) {
        return waiting({
          on: Object.values(cp.wave).filter((id) => rt.task(id)?.status !== "terminal"),
          policy,
          ...bound,
          phase: PHASE_JOIN,
        });
      }
      const after: WorkflowRunCheckpoint = {
        ...cp,
        wave: {},
        gates: {},
        fan: {},
        ...(settled.failure ? { failure: settled.failure } : {}),
      };
      return advance(task, rt, ops, after, settled.nodes);
    });
  };

  const fan = async (task: State, rt: Scope, ctx: Context): Promise<Committed> => {
    const cp = load(task);
    return rt.commit((_tx, ops) => {
      if (passed(task, ctx)) return expire(task, rt, ops, cp);
      const laneIds: string[] = [];
      for (const [nodeId, f] of Object.entries(cp.fan)) {
        for (let lane = 0; lane < f.lanes; lane++) {
          laneIds.push(laneTaskId(task.id, attemptOf(task, cp), nodeId, lane));
        }
      }
      const open = laneIds.filter((id) => rt.task(id)?.status !== "terminal");
      if (open.length > 0) return waiting({ on: open, ...untilDeadline(task), phase: PHASE_FAN });
      for (const [nodeId, f] of Object.entries(cp.fan)) {
        deliver(rt, ops, cp.wave[nodeId] as string, summarize(task, cp, rt, nodeId, f));
      }
      // Only the fan-out nodes are still live; their `finish` now runs.
      return waiting({
        on: Object.values(cp.wave),
        policy: waitPolicy(task),
        ...untilDeadline(task),
        phase: PHASE_JOIN,
        checkpoint: { ...cp, gates: {}, fan: {} },
      });
    });
  };

  const finalize = async (task: State, rt: Scope, ctx: Context): Promise<Committed> => {
    const cp = load(task);
    const status: RunStatus = cp.failure ? "failed" : "completed";
    const attempt = attemptOf(task, cp);
    const { retry } = task.input;
    if (
      status === "failed" &&
      cp.expired !== true &&
      retry &&
      attempt - task.input.runAttempt < retry.max
    ) {
      const until = ctx.clock.now() + retry.delayMs;
      const deadline = task.input.deadlineMs;
      if (deadline === undefined || until < deadline) {
        // Wait out the delay, then run the whole graph again as the next attempt.
        return rt.commit(() =>
          waiting({
            until,
            phase: "start",
            checkpoint: {
              version: CHECKPOINT_VERSION,
              nodes: initialStates(task.input.graph),
              wave: {},
              gates: {},
              fan: {},
              attempt: attempt + 1,
            },
          }),
        );
      }
    }
    const body = finalizeInput(task, cp, status);
    await deps.gateway.finalize(task.input.runId, finalizeKey(task.input.runId, attempt), body, {
      signal: ctx.signal,
    });
    const value: WorkflowRunValue = {
      status,
      nodes: body.nodeSummaries,
      ...(body.error === undefined ? {} : { error: body.error }),
    };
    return rt.commit(() => done(value as unknown as JsonValue));
  };

  const def = defineTask<WorkflowRunInput, WorkflowRunCheckpoint | null>({
    kind: WORKFLOW_RUN_KIND,
    lane: "background",
    phases: { start, [PHASE_JOIN]: join, [PHASE_FAN]: fan, [PHASE_FINALIZE]: finalize },
    initialPhase: "start",
    onAbort: async (task, scope, ctx) => {
      // Children have stopped (bottom-up). Report the cancellation once.
      const cp = load(task);
      await deps.gateway.finalize(
        task.input.runId,
        finalizeKey(task.input.runId, attemptOf(task, cp)),
        finalizeInput(task, cp, "cancelled"),
        { signal: ctx.signal },
      );
      await deps.onAbort?.(task, scope, ctx);
    },
    view: (task) => {
      const states = task.checkpoint?.nodes;
      return {
        label: task.input.runId,
        attempt: attemptOf(task, task.checkpoint) + 1,
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
