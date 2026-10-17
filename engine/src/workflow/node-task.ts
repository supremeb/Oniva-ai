/**
 * The `workflow_node` task (plan 2026-10-17-003, U11, KTD3/KTD4/KTD6/KTD7/KTD16):
 * one node of a workflow run. It holds no node semantics; every node-specific
 * step is a keyed HTTP call to the backend node gateway (`node-gateway.ts`).
 *
 * Input: `{ version: 1, runId, runAttempt, nodeId, nodeType, policy,
 * gateTaskId? }`. Phases:
 *
 * - `prepare`: ask the backend what the node does, under
 *   `{runId}:{nodeId}:r{runAttempt}:a{nodeAttempt}:prepare`. The answer is one of
 *   - `final`: the node is done;
 *   - `llm`: create a child conversation owned by this task, submit the
 *     prompt with `requestId = node:{taskId}` and wait on its run (phase
 *     `awaitChild`). The child is made in the commit that leaves `prepare`, so a
 *     crash cannot leave two children (KTD6);
 *   - `wait`: park on `waiting({ until, signal? })` (phase `awaitWake`).
 *     A `durationMs` is relative to the engine clock at this moment (KTD7);
 *     with a signal key, `slaUntilEpochMs` also bounds the wait (the earlier
 *     deadline wins and wakes the node with `wakeReason: "deadline"`);
 *   - `fanout`: record the request digest, signal the run's gate task
 *     (`gateTaskId`, key `parked`, payload = the digest; skipped without a gate)
 *     in the same commit that parks the node in `awaitFanout`, waiting on the
 *     `fanout-result` signal. The run task executes the children and delivers
 *     a counts-only summary (KTD17); the node then calls `finish` itself.
 * - `awaitChild` / `awaitWake` / `awaitFanout`: gather what `finish` needs (the
 *   child's committed answer, the signal payload or deadline wake, the fan-out
 *   summary), commit it as the intent and move on to `finish`.
 * - `finish`: call the backend `finish` under `...:finish` and settle on its
 *   `final`.
 *
 * Settling: a `failed` result retries while `policy.maxRetries` allows (a
 * deadline wait of `backoffMs`, then `prepare` again with `nodeAttempt + 1`,
 * hence a new key). A `fanout` node never retries and an interrupted call never
 * retries. Otherwise `onError: "continue"` records the failure as the task's
 * `done` value (`continued: true`) and `stop` faults the task, which is how the
 * run's `failFast` wait sees the failure. Other statuses finish `done`.
 *
 * Timeouts: `policy.timeoutMs` bounds each backend call (with its retries) of
 * an attempt; passing it ends the call as `failed` with `error.code =
 * "timeout"`.
 *
 * Recovery: `prepare` is simply asked again (the key returns the stored
 * answer). A `finish` that this process did not just commit the intent for was
 * cut off by a restart. It runs again only when the backend declared the call
 * replay-safe (`llm.replaySafe`); otherwise the engine asks the backend for the
 * outcome stored under the key and, when there is none, settles the node
 * `interrupted` (a failed result with `error.code = "interrupted"`).
 *
 * Abort: the child conversation is owned by this task, so the runtime aborts it
 * first (bottom-up). `onAbort` (a host hook) then runs.
 *
 * Checkpoints carry `version`; a checkpoint of the previous version is
 * upgraded in memory and a newer one faults the task with
 * `unsupported_checkpoint_version` (KTD16).
 */

import {
  GENERATION_TASK_KIND,
  type GenerationCheckpoint,
  type GenerationInput,
} from "../agent/generation.js";
import { appendUserEntry, startRun } from "../agent/inbox.js";
import { agentConfigDoc, applyAgentConfig } from "../agent/prompt.js";
import {
  type SubmissionAnswer,
  type SubmissionUnanswered,
  settleSubmission,
  submissionResult,
  toSubmissionInput,
} from "../agent/submission.js";
import type { Context } from "../core/context.js";
import { errorText } from "../core/errors.js";
import type { JsonObject, JsonValue } from "../core/json.js";
import {
  type AbortHandler,
  type Committed,
  defineTask,
  done,
  next,
  type TaskDefinition,
  type TaskScope,
  type TaskState,
  type Transition,
  waiting,
} from "../tasks/define.js";
import { CheckpointError, decodeCheckpoint, parseFinishInput } from "./contract.js";
import { type NodeGateway, NodeGatewayError, type NodePhase, nodeKey } from "./node-gateway.js";
import {
  CHECKPOINT_VERSION,
  CONTRACT_VERSION,
  type FanoutTarget,
  type FinishInput,
  type NodePolicy,
  type NodeStatus,
  type PrepareFinal,
  type PrepareLlm,
  type PrepareResult,
} from "./types.js";

export const WORKFLOW_NODE_KIND = "workflow_node";

/** The signal a run task sends a gate task when a fan-out node parks. */
export const FANOUT_PARKED_SIGNAL = "parked";
/** The signal a run task sends a parked fan-out node with its counts-only summary. */
export const FANOUT_RESULT_SIGNAL = "fanout-result";

export interface WorkflowNodeInput {
  version: 1;
  runId: string;
  runAttempt: number;
  nodeId: string;
  nodeType: string;
  policy: NodePolicy;
  /** The run's gate task to signal when a fan-out node parks (U34). Absent in unit tests. */
  gateTaskId?: string;
}

/** What the `parked` signal carries: the fan-out request, never item payloads (KTD17). */
export interface FanoutDigest {
  nodeId: string;
  count: number;
  parallel: number;
  failFast: boolean;
  target: FanoutTarget;
  itemsRef: string;
}

export interface WorkflowNodeCheckpoint {
  version: 1;
  /** The node's own retry counter; part of the idempotency key. */
  nodeAttempt: number;
  kind?: "llm" | "wait" | "fanout";
  /** The backend declared a re-run of `finish` after a crash safe. */
  replaySafe?: boolean;
  child?: { conversationId: string; submissionId: string };
  /** The signal a `wait` node also wakes on. */
  signal?: string;
  fanout?: FanoutDigest;
  /** The staged input of the backend `finish` call. */
  finish?: FinishInput;
}

/** The `done` value of a node task the run task reads. */
export interface WorkflowNodeValue {
  nodeId: string;
  status: NodeStatus;
  output: JsonValue;
  tokensUsed: number;
  /** Attempts used, counting the first. */
  attempts: number;
  error?: JsonValue;
  classification?: string;
  selectedHandles?: string[];
  /** The node failed and `onError: "continue"` turned that into a recorded result. */
  continued?: true;
  /** The outcome of a non-replay-safe call was unknown after a restart. */
  interrupted?: true;
}

export type WorkflowNodeErrorCode =
  | "node_failed"
  | "interrupted"
  | "invalid_checkpoint"
  | "unsupported_checkpoint_version";

/** A node task faulted on purpose; the message starts with the stable `code`. */
export class WorkflowNodeError extends Error {
  constructor(
    readonly code: WorkflowNodeErrorCode,
    message: string,
  ) {
    super(`${code}: ${message}`);
    this.name = "WorkflowNodeError";
  }
}

export interface WorkflowNodeDeps {
  gateway: NodeGateway;
  /** Host hook run when a node is aborted, after the child conversation has stopped. */
  onAbort?: AbortHandler<WorkflowNodeInput, WorkflowNodeCheckpoint | null>;
}

type State = TaskState<WorkflowNodeInput, WorkflowNodeCheckpoint | null>;
type Scope = TaskScope<WorkflowNodeCheckpoint | null>;
type Next = Transition<WorkflowNodeCheckpoint | null>;

const SETTLED = new Set(["done", "unanswered"]);
const PHASE_FINISH = "finish";

/** A failed `final` the engine makes up itself (a gateway failure, a timeout, a bad child). */
function engineFailure(code: string, message: string): PrepareFinal {
  return {
    contractVersion: CONTRACT_VERSION,
    kind: "final",
    status: "failed",
    output: null,
    error: { code, message },
    tokensUsed: 0,
  };
}

function errorMessage(error: JsonValue | undefined): string {
  if (error === undefined || error === null) return "the node failed";
  if (typeof error === "string") return error;
  if (typeof error === "object" && !Array.isArray(error)) {
    if (typeof error.message === "string") return error.message;
    if (typeof error.code === "string") return error.code;
  }
  return JSON.stringify(error);
}

/** The node task's checkpoint, upgraded from the previous version when needed. */
function load(task: State): WorkflowNodeCheckpoint {
  if (task.checkpoint === null || task.checkpoint === undefined) {
    return { version: CHECKPOINT_VERSION, nodeAttempt: 0 };
  }
  try {
    return decodeCheckpoint<WorkflowNodeCheckpoint>(task.checkpoint, {
      upgrade: (previous) => {
        const nodeAttempt = (previous as JsonObject).nodeAttempt;
        return { nodeAttempt: typeof nodeAttempt === "number" ? nodeAttempt : 0 };
      },
    });
  } catch (err) {
    if (err instanceof CheckpointError) throw new WorkflowNodeError(err.code, err.message);
    throw err;
  }
}

function keyFor(task: State, cp: WorkflowNodeCheckpoint, phase: NodePhase): string {
  const { runId, nodeId, runAttempt } = task.input;
  return nodeKey({ runId, nodeId, runAttempt, nodeAttempt: cp.nodeAttempt, phase });
}

function callOptions(task: State, ctx: Context) {
  const timeoutMs = task.input.policy.timeoutMs;
  return { signal: ctx.signal, ...(timeoutMs > 0 ? { timeoutMs } : {}) };
}

/** A backend failure as a failed result; cancellation and lease loss propagate. */
function asFailure(err: unknown, ctx: Context): PrepareFinal {
  ctx.throwIfCancelled();
  if (err instanceof NodeGatewayError) return engineFailure(err.code, err.message);
  throw err;
}

function nodeValue(
  task: State,
  cp: WorkflowNodeCheckpoint,
  final: PrepareFinal,
  flags: { continued?: true; interrupted?: true } = {},
): JsonValue {
  const value: WorkflowNodeValue = {
    nodeId: task.input.nodeId,
    status: final.status,
    output: final.output,
    tokensUsed: final.tokensUsed,
    attempts: cp.nodeAttempt + 1,
    ...(final.error === undefined ? {} : { error: final.error }),
    ...(final.classification === undefined ? {} : { classification: final.classification }),
    ...(final.selectedHandles === undefined ? {} : { selectedHandles: final.selectedHandles }),
    ...flags,
  };
  return value as unknown as JsonValue;
}

/**
 * What a result means for the task: done, a retry, or a fault. Pure, so it can
 * run inside a commit callback; throwing aborts that commit (the task faults).
 */
function decide(
  task: State,
  cp: WorkflowNodeCheckpoint,
  final: PrepareFinal,
  ctx: Context,
  interrupted = false,
): Next {
  const { policy } = task.input;
  if (final.status !== "failed") return done(nodeValue(task, cp, final));
  const neverRetries = interrupted || cp.fanout !== undefined || task.input.nodeType === "fanout";
  if (!neverRetries && cp.nodeAttempt < policy.maxRetries) {
    const retry: WorkflowNodeCheckpoint = {
      version: CHECKPOINT_VERSION,
      nodeAttempt: cp.nodeAttempt + 1,
    };
    return policy.backoffMs > 0
      ? waiting({ until: ctx.clock.now() + policy.backoffMs, phase: "prepare", checkpoint: retry })
      : next("prepare", retry);
  }
  if (policy.onError === "continue") {
    return done(
      nodeValue(
        task,
        cp,
        final,
        interrupted ? { continued: true, interrupted: true } : { continued: true },
      ),
    );
  }
  throw new WorkflowNodeError(
    interrupted ? "interrupted" : "node_failed",
    errorMessage(final.error),
  );
}

/** Create the generation child for an `llm` result and wait on its run. */
function spawnChild(
  task: State,
  rt: Scope,
  cp: WorkflowNodeCheckpoint,
  llm: PrepareLlm,
): Promise<Committed> {
  const generation = rt.runtime.registry.get(GENERATION_TASK_KIND) as unknown as
    | TaskDefinition<GenerationInput, GenerationCheckpoint>
    | undefined;
  if (!generation) throw new Error("the generation task kind is not registered");
  return rt.commit(async (tx, ops) => {
    const parent = await tx.getConversation(task.conversationId);
    if (!parent) throw new Error(`unknown conversation ${task.conversationId}`);
    const parentAgent = (await tx.doc(agentConfigDoc, task.conversationId)).value;
    const system =
      llm.responseFormat === "json"
        ? `${llm.system}\n\nReply with a single JSON value and nothing else.`
        : llm.system;
    const model = llm.model ?? parentAgent.model;
    const agent = applyAgentConfig(parentAgent, {
      systemPrompt: system,
      tools: llm.tools ?? [],
      model,
      depth: parentAgent.depth + 1,
      ...(llm.tier === undefined
        ? {}
        : { metadata: { ...parentAgent.metadata, modelTier: llm.tier } }),
      ...(llm.maxRounds === undefined ? {} : { maxRounds: llm.maxRounds }),
    });
    const child = ops.createConversation({
      tenantId: parent.tenantId,
      projectId: parent.projectId,
      ownerTaskId: task.id,
    });
    (await tx.doc(agentConfigDoc, child.id)).set(agent);
    const submissionId = tx.mintId("submission");
    const runId = tx.mintId("task");
    tx.createSubmission({
      id: submissionId,
      conversationId: child.id,
      requestId: `node:${task.id}`,
      input: toSubmissionInput({
        content: llm.prompt,
        type: "input",
        whenBusy: "followUp",
        untrusted: false,
      }),
      status: "active",
      result: null,
      taskId: runId,
    });
    appendUserEntry(tx, child.id, submissionId, llm.prompt, `node:${task.id}`);
    startRun(tx, ops, generation, child.id, submissionId, runId);
    return waiting({
      on: [runId],
      phase: "awaitChild",
      checkpoint: {
        version: CHECKPOINT_VERSION,
        nodeAttempt: cp.nodeAttempt,
        kind: "llm",
        replaySafe: llm.replaySafe,
        child: { conversationId: child.id, submissionId },
      },
    });
  });
}

/** The create-once node task definition for a host. */
export function createWorkflowNodeTask(
  deps: WorkflowNodeDeps,
): TaskDefinition<WorkflowNodeInput, WorkflowNodeCheckpoint | null> {
  /**
   * Tasks whose `finish` intent this process committed and whose `finish` has not
   * started yet. Process memory on purpose: after a restart it is empty, which is
   * how `finish` recognises a call cut off by one.
   */
  const freshIntents = new Set<string>();

  /** Commit the staged `finish` input as the intent, then move on to `finish`. */
  async function stage(
    task: State,
    rt: Scope,
    cp: WorkflowNodeCheckpoint,
    input: FinishInput,
    ctx: Context,
  ): Promise<Committed> {
    let valid: FinishInput;
    try {
      valid = parseFinishInput(input);
    } catch (err) {
      return rt.commit(() =>
        decide(task, cp, engineFailure("invalid_finish_input", errorText(err)), ctx),
      );
    }
    const intent = await rt.commit(() => next(PHASE_FINISH, { ...cp, finish: valid }));
    freshIntents.add(task.id);
    return intent;
  }

  const prepare = async (task: State, rt: Scope, ctx: Context): Promise<Committed> => {
    const cp = load(task);
    const { runId, nodeId, runAttempt, nodeType } = task.input;
    let result: PrepareResult;
    try {
      result = await deps.gateway.prepare(
        { runId, nodeId },
        keyFor(task, cp, "prepare"),
        {
          contractVersion: CONTRACT_VERSION,
          runAttempt,
          nodeAttempt: cp.nodeAttempt,
          nodeType,
          nodeTaskId: task.id,
        },
        callOptions(task, ctx),
      );
    } catch (err) {
      const failed = asFailure(err, ctx);
      return rt.commit(() => decide(task, cp, failed, ctx));
    }
    switch (result.kind) {
      case "final": {
        const final = result;
        return rt.commit(() => decide(task, cp, final, ctx));
      }
      case "llm":
        return spawnChild(task, rt, cp, result);
      case "wait": {
        const { durationMs, untilEpochMs, signal, slaUntilEpochMs } = result;
        const deadline =
          durationMs !== undefined ? ctx.clock.now() + durationMs : (untilEpochMs as number);
        const until =
          signal !== undefined && slaUntilEpochMs !== undefined
            ? Math.min(deadline, slaUntilEpochMs)
            : deadline;
        return rt.commit(() =>
          waiting({
            until,
            ...(signal === undefined ? {} : { signal }),
            phase: "awaitWake",
            checkpoint: {
              version: CHECKPOINT_VERSION,
              nodeAttempt: cp.nodeAttempt,
              kind: "wait",
              replaySafe: false,
              ...(signal === undefined ? {} : { signal }),
            },
          }),
        );
      }
      case "fanout": {
        const digest: FanoutDigest = {
          nodeId,
          count: result.count,
          parallel: result.parallel,
          failFast: result.failFast,
          target: result.target,
          itemsRef: result.itemsRef,
        };
        const gateTaskId = task.input.gateTaskId;
        return rt.commit((_tx, ops) => {
          if (gateTaskId !== undefined) {
            ops.signal(gateTaskId, FANOUT_PARKED_SIGNAL, digest as unknown as JsonValue);
          }
          return waiting({
            signal: FANOUT_RESULT_SIGNAL,
            phase: "awaitFanout",
            checkpoint: {
              version: CHECKPOINT_VERSION,
              nodeAttempt: cp.nodeAttempt,
              kind: "fanout",
              replaySafe: false,
              fanout: digest,
            },
          });
        });
      }
    }
  };

  /** Follow the child's run until its submission settles, then stage its answer. */
  const awaitChild = async (task: State, rt: Scope, ctx: Context): Promise<Committed> => {
    const cp = load(task);
    const link = cp.child;
    if (!link) throw new WorkflowNodeError("invalid_checkpoint", "awaitChild without a child");
    return rt
      .commit(async (tx, ops) => {
        const sub = await tx.getSubmission(link.submissionId);
        if (!sub) {
          return decide(
            task,
            cp,
            engineFailure("generation_failed", "the node's prompt is missing"),
            ctx,
          );
        }
        let status = sub.status;
        let result = submissionResult(sub);
        if (!SETTLED.has(status)) {
          if (sub.status === "active" && sub.taskId !== null) {
            const current = ops.task(sub.taskId);
            if (current && current.status !== "terminal") {
              return waiting({ on: [current.id], phase: "awaitChild" });
            }
            // The child's run ended without settling its input: settle it here.
            const outcome = current?.outcome ?? null;
            const unanswered: SubmissionUnanswered = {
              reason: outcome?.type === "aborted" ? "aborted" : "faulted",
              message:
                outcome?.type === "aborted"
                  ? outcome.reason
                  : outcome?.type === "faulted"
                    ? outcome.message
                    : "the run ended without an answer",
              error: null,
            };
            settleSubmission(tx, sub, "unanswered", unanswered);
            status = "unanswered";
            result = unanswered;
          } else {
            const live = rt.runtime
              .tasks({ conversationId: link.conversationId })
              .filter((t) => t.parentTaskId === null && t.status !== "terminal")
              .map((t) => t.id);
            if (live.length > 0) return waiting({ on: live, phase: "awaitChild" });
            return decide(
              task,
              cp,
              engineFailure("generation_failed", "the node's prompt never started"),
              ctx,
            );
          }
        }
        if (status !== "done") {
          const u = result as SubmissionUnanswered | null;
          return decide(
            task,
            cp,
            engineFailure(
              "generation_failed",
              `the node's generation did not answer (${u?.reason ?? "unanswered"}): ${u?.message ?? ""}`.trim(),
            ),
            ctx,
          );
        }
        const answer = result as SubmissionAnswer | null;
        const input: FinishInput = {
          contractVersion: CONTRACT_VERSION,
          kind: "llm",
          answer: typeof answer?.text === "string" ? answer.text : "",
        };
        return stageInCommit(task, cp, input, ctx);
      })
      .then((committed) => {
        afterStage(task);
        return committed;
      });
  };

  /**
   * `stage` for callers already inside a commit: validates and returns the intent
   * transition. {@link afterStage} then marks the intent fresh.
   */
  function stageInCommit(
    task: State,
    cp: WorkflowNodeCheckpoint,
    input: FinishInput,
    ctx: Context,
  ): Next {
    try {
      const valid = parseFinishInput(input);
      pendingFresh.add(task.id);
      return next(PHASE_FINISH, { ...cp, finish: valid });
    } catch (err) {
      return decide(task, cp, engineFailure("invalid_finish_input", errorText(err)), ctx);
    }
  }
  const pendingFresh = new Set<string>();
  function afterStage(task: State): void {
    if (pendingFresh.delete(task.id)) freshIntents.add(task.id);
  }

  const awaitWake = async (task: State, rt: Scope, ctx: Context): Promise<Committed> => {
    const cp = load(task);
    const reason = task.wake?.reason;
    const signalled = cp.signal !== undefined ? task.signals[cp.signal] : undefined;
    const input: FinishInput = {
      contractVersion: CONTRACT_VERSION,
      kind: "wait",
      wakeReason: reason === "signal" ? "signal" : "deadline",
      ...(reason === "signal" && signalled !== undefined ? { signalPayload: signalled } : {}),
    };
    return stage(task, rt, cp, input, ctx);
  };

  const awaitFanout = async (task: State, rt: Scope, ctx: Context): Promise<Committed> => {
    const cp = load(task);
    const summary = task.signals[FANOUT_RESULT_SIGNAL];
    if (summary === undefined) {
      return rt.commit(() => waiting({ signal: FANOUT_RESULT_SIGNAL, phase: "awaitFanout" }));
    }
    const input = {
      contractVersion: CONTRACT_VERSION,
      kind: "fanout",
      fanout: summary,
    } as unknown as FinishInput;
    return stage(task, rt, cp, input, ctx);
  };

  const finish = async (task: State, rt: Scope, ctx: Context): Promise<Committed> => {
    const cp = load(task);
    const input = cp.finish;
    if (!input) throw new WorkflowNodeError("invalid_checkpoint", "finish without staged input");
    const fresh = freshIntents.delete(task.id);
    const { runId, nodeId } = task.input;
    const key = keyFor(task, cp, "finish");
    const options = callOptions(task, ctx);

    if (!fresh && cp.replaySafe !== true) {
      // Cut off by a restart: the backend either completed the call (its
      // stored outcome is the answer) or the outcome is unknown.
      let stored: PrepareResult | null = null;
      try {
        stored = await deps.gateway.lookup(key, { signal: ctx.signal });
      } catch (err) {
        asFailure(err, ctx);
      }
      if (stored?.kind === "final") {
        const final = stored;
        return rt.commit(() => decide(task, cp, final, ctx));
      }
      const unknown = engineFailure(
        "interrupted",
        "the node's finish call was cut off by a restart and its outcome is unknown",
      );
      return rt.commit(() => decide(task, cp, unknown, ctx, true));
    }

    let final: PrepareFinal;
    try {
      final = await deps.gateway.finish({ runId, nodeId }, key, input, options);
    } catch (err) {
      final = asFailure(err, ctx);
    }
    return rt.commit(() => decide(task, cp, final, ctx));
  };

  return defineTask<WorkflowNodeInput, WorkflowNodeCheckpoint | null>({
    kind: WORKFLOW_NODE_KIND,
    phases: {
      prepare,
      awaitChild,
      awaitWake,
      awaitFanout,
      [PHASE_FINISH]: finish,
    },
    initialPhase: "prepare",
    ...(deps.onAbort ? { onAbort: deps.onAbort } : {}),
    view: (task) => ({
      label: task.input.nodeId,
      attempt: (task.checkpoint?.nodeAttempt ?? 0) + 1,
    }),
  });
}
