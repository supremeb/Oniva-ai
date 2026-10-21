/**
 * The `memory_curate` task (plan 2026-10-21-001, U30, KTD18): a thin, durable
 * driver for the memory curator. It holds no memory semantics; the digest, the
 * curator prompt, delta parsing, the taint rule and budgets all live in the
 * backend (`gateway.ts`, `contract.ts`).
 *
 * The backend starts it with `POST /v1/runs` (`kind: "memory_curate"`) and a
 * deterministic run id, so a repeated trigger is one run. Input:
 * `{ version: 1, conversationId, runId }` naming the SOURCE run to curate.
 * Phases:
 *
 * - `prepare`: ask the backend under `curate:{conversationId}:{runId}:prepare`.
 *   `skip` ends the task `done` with no child. `llm` creates a tool-less child
 *   conversation owned by this task (system and prompt from the backend,
 *   `metadata.purpose = "memory.curate"`, `metadata.modelTier = tier`) and waits
 *   on its run (phase `reflect`). The child is made in the commit that leaves
 *   `prepare`, so a crash cannot leave two children.
 * - `reflect`: follow the child's run until its submission settles, then stage
 *   its answer text as the intent of `apply`.
 * - `apply`: post the answer and the opaque `ctx` under
 *   `curate:{conversationId}:{runId}:apply` and finish `done` with the backend's
 *   outcome. Every call is keyed, so a repeat after a crash is answered from the
 *   backend's stored outcome and never applies twice.
 *
 * Failure: a transient gateway failure (`gateway_unavailable`, `timeout`) is
 * asked again under the same key after a backoff, at most `maxAttempts` times
 * per phase; a definitive one, a failed child or an exhausted budget faults the
 * task with a stable code (`prepare_failed`, `reflect_failed`, `apply_failed`).
 * Nothing retries forever, and a curator failure never touches the source run
 * (it lives in its own conversation).
 *
 * Abort: the child conversation is owned by this task, so the runtime aborts it
 * first (bottom-up). `onAbort` (a host hook) then runs.
 *
 * Checkpoints carry `version`; a newer one faults the task with
 * `unsupported_checkpoint_version`.
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
import type { JsonValue } from "../core/json.js";
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
import {
  type CuratePrepareLlm,
  MEMORY_CHECKPOINT_VERSION,
  MEMORY_CONTRACT_VERSION,
  MEMORY_LIMITS,
  type MemoryTier,
} from "./contract.js";
import { curateKey, type MemoryGateway, MemoryGatewayError } from "./gateway.js";

export const MEMORY_CURATE_KIND = "memory_curate";

/** The policy purpose the curator's model requests are made for. */
export const MEMORY_CURATE_PURPOSE = "memory.curate";

export const DEFAULT_CURATE_MAX_ATTEMPTS = 3;
export const DEFAULT_CURATE_BACKOFF_MS = 10_000;

export interface MemoryCurateInput {
  version: 1;
  /** The source conversation being curated (the backend's conversation id). */
  conversationId: string;
  /** The source run being curated. */
  runId: string;
}

export interface MemoryCurateCheckpoint {
  version: 1;
  /** Failed attempts in the current phase (bounded by `maxAttempts`). */
  attempts?: number;
  /** The opaque token `prepare` handed out, echoed to `apply`. */
  ctx?: string;
  child?: { conversationId: string; submissionId: string };
  /** The child's answer, staged for `apply`. */
  answer?: string;
}

/** The `done` value of a curator task. */
export type MemoryCurateValue =
  | { status: "skipped"; reason?: string }
  | {
      status: "applied" | "skipped_at_apply";
      counts?: Record<string, number>;
      reason?: string;
    };

export type MemoryCurateErrorCode =
  | "prepare_failed"
  | "reflect_failed"
  | "apply_failed"
  | "invalid_checkpoint"
  | "unsupported_checkpoint_version";

/** A curator task faulted on purpose; the message starts with the stable `code`. */
export class MemoryCurateError extends Error {
  constructor(
    readonly code: MemoryCurateErrorCode,
    message: string,
  ) {
    super(`${code}: ${message}`);
    this.name = "MemoryCurateError";
  }
}

export interface MemoryCurateDeps {
  gateway: MemoryGateway;
  /** Attempts per phase for a transient gateway failure (the first counts). Default 3. */
  maxAttempts?: number;
  /** Wait before attempt n+1 is `backoffMs * n`. Default 10 s. */
  backoffMs?: number;
  /** Deadline of one gateway call, retries included. Default none. */
  callTimeoutMs?: number;
  /** Host hook run when the task is aborted, after the child conversation has stopped. */
  onAbort?: AbortHandler<MemoryCurateInput, MemoryCurateCheckpoint | null>;
}

type State = TaskState<MemoryCurateInput, MemoryCurateCheckpoint | null>;
type Scope = TaskScope<MemoryCurateCheckpoint | null>;

const PHASE_PREPARE = "prepare";
const PHASE_REFLECT = "reflect";
const PHASE_APPLY = "apply";
const SETTLED = new Set(["done", "unanswered"]);

function load(task: State): MemoryCurateCheckpoint {
  const cp = task.checkpoint;
  if (cp === null || cp === undefined) return { version: MEMORY_CHECKPOINT_VERSION };
  const version = (cp as { version?: unknown }).version;
  if (typeof version === "number" && version > MEMORY_CHECKPOINT_VERSION) {
    throw new MemoryCurateError(
      "unsupported_checkpoint_version",
      `checkpoint version ${version} is newer than ${MEMORY_CHECKPOINT_VERSION}`,
    );
  }
  if (version !== MEMORY_CHECKPOINT_VERSION) {
    throw new MemoryCurateError("invalid_checkpoint", "the checkpoint has no valid version");
  }
  return cp;
}

/** Why a start input is rejected (`POST /v1/runs` answers 400), or null. */
export function memoryCurateInputProblem(input: unknown): string | null {
  if (typeof input !== "object" || input === null || Array.isArray(input)) {
    return "input must be an object";
  }
  const o = input as Record<string, unknown>;
  if (o.version !== 1) return "input.version must be 1";
  for (const key of ["conversationId", "runId"] as const) {
    const v = o[key];
    if (typeof v !== "string" || v.length === 0 || v.length > MEMORY_LIMITS.idChars) {
      return `input.${key} must be a non-empty string of at most ${MEMORY_LIMITS.idChars} characters`;
    }
  }
  return null;
}

/** The create-once curator task definition for a host. */
export function createMemoryCurateTask(
  deps: MemoryCurateDeps,
): TaskDefinition<MemoryCurateInput, MemoryCurateCheckpoint | null> {
  const maxAttempts = deps.maxAttempts ?? DEFAULT_CURATE_MAX_ATTEMPTS;
  const backoffMs = deps.backoffMs ?? DEFAULT_CURATE_BACKOFF_MS;
  const call = (ctx: Context) => ({
    signal: ctx.signal,
    ...(deps.callTimeoutMs !== undefined && deps.callTimeoutMs > 0
      ? { timeoutMs: deps.callTimeoutMs }
      : {}),
  });

  /**
   * A gateway failure in `phase`: wait and ask again under the same key while
   * the budget allows a transient one, otherwise fault. Cancellation and lease
   * loss propagate.
   */
  function failed(
    err: unknown,
    phase: typeof PHASE_PREPARE | typeof PHASE_APPLY,
    cp: MemoryCurateCheckpoint,
    rt: Scope,
    ctx: Context,
  ): Promise<Committed> {
    ctx.throwIfCancelled();
    if (!(err instanceof MemoryGatewayError)) throw err;
    const attempts = (cp.attempts ?? 0) + 1;
    const transient = err.code === "gateway_unavailable" || err.code === "timeout";
    if (transient && attempts < maxAttempts) {
      return rt.commit(() =>
        waiting({
          until: ctx.clock.now() + backoffMs * attempts,
          phase,
          checkpoint: { ...cp, attempts },
        }),
      );
    }
    throw new MemoryCurateError(`${phase}_failed`, `${err.code}: ${err.message}`);
  }

  /** Create the tool-less child for an `llm` result and wait on its run. */
  function spawnChild(
    task: State,
    rt: Scope,
    llm: CuratePrepareLlm,
    tier: MemoryTier,
  ): Promise<Committed> {
    const generation = rt.runtime.registry.get(GENERATION_TASK_KIND) as unknown as
      | TaskDefinition<GenerationInput, GenerationCheckpoint>
      | undefined;
    if (!generation) throw new Error("the generation task kind is not registered");
    return rt.commit(async (tx, ops) => {
      const parent = await tx.getConversation(task.conversationId);
      if (!parent) throw new Error(`unknown conversation ${task.conversationId}`);
      const parentAgent = (await tx.doc(agentConfigDoc, task.conversationId)).value;
      const agent = applyAgentConfig(parentAgent, {
        systemPrompt: llm.system,
        tools: [],
        depth: parentAgent.depth + 1,
        metadata: {
          ...parentAgent.metadata,
          purpose: MEMORY_CURATE_PURPOSE,
          modelTier: tier,
        },
      });
      const child = ops.createConversation({
        tenantId: parent.tenantId,
        projectId: parent.projectId,
        ownerTaskId: task.id,
      });
      (await tx.doc(agentConfigDoc, child.id)).set(agent);
      const submissionId = tx.mintId("submission");
      const runId = tx.mintId("task");
      const requestId = `curate:${task.id}`;
      tx.createSubmission({
        id: submissionId,
        conversationId: child.id,
        requestId,
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
      appendUserEntry(tx, child.id, submissionId, llm.prompt, requestId);
      startRun(tx, ops, generation, child.id, submissionId, runId);
      return waiting({
        on: [runId],
        phase: PHASE_REFLECT,
        checkpoint: {
          version: MEMORY_CHECKPOINT_VERSION,
          ctx: llm.ctx,
          child: { conversationId: child.id, submissionId },
        },
      });
    });
  }

  const prepare = async (task: State, rt: Scope, ctx: Context): Promise<Committed> => {
    const cp = load(task);
    const { conversationId, runId } = task.input;
    let result: Awaited<ReturnType<MemoryGateway["prepare"]>>;
    try {
      result = await deps.gateway.prepare(
        curateKey(conversationId, runId, PHASE_PREPARE),
        {
          contractVersion: MEMORY_CONTRACT_VERSION,
          conversationId,
          runId,
          curateConversationId: task.conversationId,
          curateTaskId: task.id,
        },
        call(ctx),
      );
    } catch (err) {
      return failed(err, PHASE_PREPARE, cp, rt, ctx);
    }
    if (result.kind === "skip") {
      const value: MemoryCurateValue = {
        status: "skipped",
        ...(result.reason === undefined ? {} : { reason: result.reason }),
      };
      return rt.commit(() => done(value as unknown as JsonValue));
    }
    return spawnChild(task, rt, result, result.tier);
  };

  /** Follow the child's run until its submission settles, then stage its answer for `apply`. */
  const reflect = async (task: State, rt: Scope, _ctx: Context): Promise<Committed> => {
    const cp = load(task);
    const link = cp.child;
    if (!link || cp.ctx === undefined) {
      throw new MemoryCurateError("invalid_checkpoint", "reflect without a child");
    }
    const ctxToken = cp.ctx;
    return rt.commit(async (tx, ops): Promise<Transition<MemoryCurateCheckpoint | null>> => {
      const sub = await tx.getSubmission(link.submissionId);
      if (!sub) throw new MemoryCurateError("reflect_failed", "the curator prompt is missing");
      let status = sub.status;
      let result = submissionResult(sub);
      if (!SETTLED.has(status)) {
        if (sub.status === "active" && sub.taskId !== null) {
          const current = ops.task(sub.taskId);
          if (current && current.status !== "terminal") {
            return waiting({ on: [current.id], phase: PHASE_REFLECT });
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
          if (live.length > 0) return waiting({ on: live, phase: PHASE_REFLECT });
          throw new MemoryCurateError("reflect_failed", "the curator prompt never started");
        }
      }
      if (status !== "done") {
        const u = result as SubmissionUnanswered | null;
        throw new MemoryCurateError(
          "reflect_failed",
          `the curator did not answer (${u?.reason ?? "unanswered"}): ${u?.message ?? ""}`.trim(),
        );
      }
      const answer = (result as SubmissionAnswer | null)?.text;
      if (typeof answer !== "string" || answer.trim().length === 0) {
        throw new MemoryCurateError("reflect_failed", "the curator answered with no text");
      }
      if (answer.length > MEMORY_LIMITS.answerChars) {
        throw new MemoryCurateError(
          "reflect_failed",
          `the curator's answer is longer than ${MEMORY_LIMITS.answerChars} characters`,
        );
      }
      return next(PHASE_APPLY, {
        version: MEMORY_CHECKPOINT_VERSION,
        ctx: ctxToken,
        child: link,
        answer,
      });
    });
  };

  const apply = async (task: State, rt: Scope, ctx: Context): Promise<Committed> => {
    const cp = load(task);
    if (cp.answer === undefined || cp.ctx === undefined) {
      throw new MemoryCurateError("invalid_checkpoint", "apply without a staged answer");
    }
    const { conversationId, runId } = task.input;
    let response: Awaited<ReturnType<MemoryGateway["apply"]>>;
    try {
      // Replay-safe: the key makes a repeat after a crash return the stored outcome.
      response = await deps.gateway.apply(
        curateKey(conversationId, runId, PHASE_APPLY),
        {
          contractVersion: MEMORY_CONTRACT_VERSION,
          conversationId,
          runId,
          ctx: cp.ctx,
          answer: cp.answer,
        },
        call(ctx),
      );
    } catch (err) {
      return failed(err, PHASE_APPLY, cp, rt, ctx);
    }
    const value: MemoryCurateValue = {
      status: response.status === "applied" ? "applied" : "skipped_at_apply",
      ...(response.counts === undefined ? {} : { counts: response.counts }),
      ...(response.reason === undefined ? {} : { reason: response.reason }),
    };
    return rt.commit(() => done(value as unknown as JsonValue));
  };

  const def = defineTask<MemoryCurateInput, MemoryCurateCheckpoint | null>({
    kind: MEMORY_CURATE_KIND,
    phases: { [PHASE_PREPARE]: prepare, [PHASE_REFLECT]: reflect, [PHASE_APPLY]: apply },
    initialPhase: PHASE_PREPARE,
    ...(deps.onAbort ? { onAbort: deps.onAbort } : {}),
    view: (task) => ({
      label: `curate ${task.input.conversationId}`,
      attempt: (task.checkpoint?.attempts ?? 0) + 1,
    }),
  });
  return Object.freeze({ ...def, validateInput: memoryCurateInputProblem });
}
