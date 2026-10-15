/**
 * The harness (R2, R8, R9, R20): conversations answering inputs through
 * durable generation and tool tasks over one session.
 *
 * `Harness.open(storageOrSession, options)` opens (or adopts) the session,
 * registers the agent's task kinds and opens the task runtime, which resumes
 * whatever the previous process left: a generation re-requests, a tool task
 * cut off between intent and result is recovered (see `tool-task.ts`).
 *
 * Tool calls that need approval wait durably (U9); `decideApproval` records a
 * human decision and resumes the call, `approvals` lists a conversation's
 * requests.
 *
 * Submissions whose run ended without settling them (a generation aborted or
 * faulted) are settled `unanswered` by the harness, both as it happens and
 * when the harness opens, together with the steers placed into that run; the
 * conversation's next queued input then starts, so follow-ups never stall.
 */
import { type Clock, realClock } from "../core/clock.js";
import { type EngineLimits, resolveLimits } from "../core/limits.js";
import type { ModelRegistry } from "../models/stream.js";
import type { CommitEvent } from "../session/publish.js";
import { type FenceProvider, Session } from "../session/session.js";
import type { Redact } from "../session/transaction.js";
import {
  MAX_PAGE_LIMIT,
  type StorageProvider,
  type SubmissionRecord,
  type TaskRecord,
} from "../storage/types.js";
import { RuntimeStopped, type TaskDefinition } from "../tasks/define.js";
import { type RecoveryContext, TaskRuntime } from "../tasks/runtime.js";
import { type ToolDefinition, ToolRegistry } from "../tools/registry.js";
import {
  type ApprovalItem,
  applyDecision,
  approvalsDoc,
  type DecideApprovalInput,
} from "./approvals.js";
import { compactionTaskDefinition } from "./compaction.js";
import { ENTRY_KINDS, toEntryPayload } from "./context-builder.js";
import {
  type AgentCore,
  Conversation,
  isBusy,
  Submission,
  type SubmissionSettled,
} from "./conversation.js";
import {
  clearLive,
  GENERATION_TASK_KIND,
  type GenerationCheckpoint,
  type GenerationInput,
  generationTaskDefinition,
  settleSubmission,
} from "./generation.js";
import { type AgentErrorReporter, type AgentHooks, HookRunner } from "./hooks.js";
import { Inbox } from "./inbox.js";
import type { HostPolicy } from "./policy.js";
import { DEFAULT_PROGRESS_INTERVAL_MS } from "./progress.js";
import {
  type AgentConfigInput,
  agentConfigDoc,
  applyAgentConfig,
  type CompactionOverrides,
} from "./prompt.js";
import type { SubmissionUnanswered } from "./submission.js";
import { type AgentDeps, TOOL_TASK_KIND, toolTaskDefinition } from "./tool-task.js";

export interface AgentSettings {
  /** Minimum ms between progress commits (partial model and tool output). */
  progressIntervalMs: number;
  /** Most task handlers running at once. */
  concurrency: number;
}

export const DEFAULT_AGENT_SETTINGS: Readonly<AgentSettings> = Object.freeze({
  progressIntervalMs: DEFAULT_PROGRESS_INTERVAL_MS,
  concurrency: 16,
});

export interface HarnessOptions {
  /** Required when opening from a storage provider. */
  sessionId?: string;
  models: ModelRegistry;
  // biome-ignore lint/suspicious/noExplicitAny: tools with any argument type
  tools: ToolRegistry | Iterable<ToolDefinition<any>>;
  policy: HostPolicy;
  hooks?: AgentHooks | readonly AgentHooks[];
  /** Host redaction (R20). Applied by the session the harness opens. */
  redact?: Redact;
  /** Injected clock for engine logic. Defaults to the real clock. */
  clock?: Clock;
  settings?: Partial<AgentSettings>;
  limits?: Partial<EngineLimits>;
  /** Lease fence for commits (`LeaseKeeper.fence`), for a session the harness opens. */
  fence?: FenceProvider;
  /** Stops the harness's runtime when aborted (`LeaseKeeper.signal`). */
  signal?: AbortSignal;
  /** Receives non-fatal errors: hook throws, progress-write failures, late handler throws. */
  onError?: AgentErrorReporter;
  /** Harness-level compaction settings; a conversation's `agent.compaction` overrides them. */
  compaction?: CompactionOverrides;
}

export interface NewConversationInput {
  tenantId: string;
  projectId: string;
  agent?: AgentConfigInput;
  id?: string;
}

interface Waiter {
  resolve(settled: SubmissionSettled): void;
  reject(error: unknown): void;
}

const SETTLED = new Set(["done", "unanswered"]);

/**
 * Recovery hook: for each root conversation whose run had a task in flight
 * (the run's own generation or any descendant subagent's), append one
 * display-only `run_resumed` entry to the root conversation, in the recovery
 * commit itself, so a restart adds exactly one per run and a replay of the
 * recovery commit cannot add another.
 */
async function markResumedRuns({
  tx,
  interrupted,
  live,
  conversationOwner,
}: RecoveryContext): Promise<void> {
  const byId = new Map(live.map((t) => [t.id, t]));
  const roots = new Set<string>();
  for (const t of interrupted) {
    let conversationId = t.conversationId;
    for (let hops = 0; hops < 64; hops++) {
      const ownerId = conversationOwner(conversationId);
      const owner = ownerId === null ? undefined : byId.get(ownerId);
      if (!owner) {
        roots.add(conversationId);
        break;
      }
      conversationId = owner.conversationId;
    }
  }
  for (const conversationId of roots) {
    const run = live.find(
      (t) =>
        t.conversationId === conversationId &&
        t.kind === GENERATION_TASK_KIND &&
        t.parentTaskId === null,
    );
    if (!run) continue;
    tx.appendEntry({
      conversationId,
      taskId: run.id,
      kind: ENTRY_KINDS.runResumed,
      payload: toEntryPayload(ENTRY_KINDS.runResumed, { runId: run.id, at: tx.now }),
    });
  }
}

export class Harness implements AgentCore {
  readonly session: Session;
  readonly runtime: TaskRuntime;
  readonly tools: ToolRegistry;
  readonly clock: Clock;
  readonly limits: Readonly<EngineLimits>;
  readonly generation: TaskDefinition<GenerationInput, GenerationCheckpoint>;
  readonly #ownsSession: boolean;
  readonly #report: AgentErrorReporter;
  readonly #waiters = new Map<string, Set<Waiter>>();
  readonly #unsubscribe: () => void;
  #stopped: unknown = null;

  private constructor(
    session: Session,
    runtime: TaskRuntime,
    parts: {
      ownsSession: boolean;
      tools: ToolRegistry;
      clock: Clock;
      limits: Readonly<EngineLimits>;
      generation: TaskDefinition<GenerationInput, GenerationCheckpoint>;
      report: AgentErrorReporter;
      unsubscribe: () => void;
    },
  ) {
    this.session = session;
    this.runtime = runtime;
    this.tools = parts.tools;
    this.clock = parts.clock;
    this.limits = parts.limits;
    this.generation = parts.generation;
    this.#ownsSession = parts.ownsSession;
    this.#report = parts.report;
    this.#unsubscribe = parts.unsubscribe;
  }

  /** Open over a storage provider (opening session `sessionId`) or an open session. */
  static async open(source: StorageProvider | Session, options: HarnessOptions): Promise<Harness> {
    const clock = options.clock ?? realClock;
    const limits = resolveLimits(options.limits);
    const settings: AgentSettings = { ...DEFAULT_AGENT_SETTINGS, ...options.settings };
    if (!Number.isFinite(settings.progressIntervalMs) || settings.progressIntervalMs < 0) {
      throw new RangeError("progressIntervalMs must be a non-negative number");
    }
    const report: AgentErrorReporter = options.onError ?? (() => {});
    const tools =
      options.tools instanceof ToolRegistry ? options.tools : new ToolRegistry(options.tools);

    let session: Session;
    const ownsSession = !(source instanceof Session);
    if (source instanceof Session) {
      session = source;
    } else {
      if (!options.sessionId) throw new TypeError("sessionId is required to open from storage");
      session = await Session.open(await source.open(options.sessionId), {
        clock,
        ...(options.limits ? { limits: options.limits } : {}),
        ...(options.redact ? { redact: options.redact } : {}),
        ...(options.fence ? { fence: options.fence } : {}),
        onObserverError: (err) => report(err, { during: "settle" }),
      });
    }

    const deps: AgentDeps = {
      session,
      models: options.models,
      tools,
      policy: options.policy,
      hooks: new HookRunner(options.hooks, report),
      clock,
      limits,
      progressIntervalMs: settings.progressIntervalMs,
      report,
      freshIntents: new Set(),
    };
    const toolTask = toolTaskDefinition(deps);
    const compaction = compactionTaskDefinition(deps, options.compaction);
    const generation = generationTaskDefinition(deps, toolTask, compaction);

    // Follow commits before the runtime starts, so no settlement is missed.
    let harness: Harness | null = null;
    const early: CommitEvent[] = [];
    const unsubscribe = session.subscribeCommits((event) => {
      if (harness) harness.#onCommit(event);
      else early.push(event);
    });

    let runtime: TaskRuntime;
    try {
      runtime = await TaskRuntime.open(session, {
        // Subagents need no kind of their own: they run as the tool task's
        // `subagent` phase over child conversations of these same kinds.
        tasks: [generation, toolTask, compaction],
        clock,
        concurrency: settings.concurrency,
        ...(options.signal ? { signal: options.signal } : {}),
        onError: (err, info) =>
          report(err, { during: info.during, ...(info.taskId ? { taskId: info.taskId } : {}) }),
        onRecover: markResumedRuns,
      });
    } catch (err) {
      unsubscribe();
      if (ownsSession) await session.close();
      throw err;
    }
    const h = new Harness(session, runtime, {
      ownsSession,
      tools,
      clock,
      limits,
      generation,
      report,
      unsubscribe,
    });
    harness = h;
    void runtime.closed.then((reason) => h.#stop(reason));
    for (const event of early.splice(0)) h.#onCommit(event);
    await h.#settleOrphans(null);
    return h;
  }

  /** Create a conversation bound to a tenant and project, with its agent configuration. */
  async createConversation(input: NewConversationInput): Promise<Conversation> {
    const id = await this.runtime.commit(async (tx, ops) => {
      const conv = ops.createConversation({
        tenantId: input.tenantId,
        projectId: input.projectId,
        ...(input.id === undefined ? {} : { id: input.id }),
      });
      const draft = await tx.doc(agentConfigDoc, conv.id);
      draft.set(applyAgentConfig(agentConfigDoc.initial, input.agent ?? {}));
      return conv.id;
    });
    return new Conversation(this, id);
  }

  /** A handle on an existing conversation (not checked until used). */
  conversation(id: string): Conversation {
    return new Conversation(this, id);
  }

  /** A handle on an existing submission, e.g. after a restart. */
  submission(id: string): Submission {
    return new Submission(this, id);
  }

  async waitForSubmission(id: string): Promise<SubmissionSettled> {
    if (this.#stopped !== null) throw this.#stoppedError();
    const { promise, waiter } = this.#addWaiter(id);
    try {
      const rec = await this.session.reader.getSubmission(id);
      if (!rec) throw new Error(`unknown submission ${id}`);
      if (SETTLED.has(rec.status)) this.#resolve(rec);
    } catch (err) {
      this.#dropWaiter(id, waiter);
      throw err;
    }
    return promise;
  }

  /**
   * Decide a pending approval: verify the conversation owns the tool call,
   * record the decision with `principal` and signal the call, in one commit.
   * Rejects with `ApprovalRejected` (writing nothing) for a call of another
   * conversation, an expired or already decided request, or bad input.
   */
  decideApproval(input: DecideApprovalInput): Promise<ApprovalItem> {
    return this.runtime.commit((tx, ops) => applyDecision(tx, ops, input, TOOL_TASK_KIND));
  }

  /** A conversation's approval requests (pending and closed), oldest first. */
  async approvals(conversationId: string): Promise<ApprovalItem[]> {
    return (await this.session.document(approvalsDoc, conversationId)).items;
  }

  /** Resolves once no task handler is running and nothing is due. */
  waitForIdle(): Promise<void> {
    return this.runtime.waitForIdle();
  }

  /** Stop the runtime (cancelling running handlers) and close the session if the harness opened it. */
  async close(): Promise<void> {
    await this.runtime.close();
    this.#stop(new RuntimeStopped("closed"));
    if (this.#ownsSession) await this.session.close();
  }

  // ---- internals ------------------------------------------------------------

  #addWaiter(id: string): { promise: Promise<SubmissionSettled>; waiter: Waiter } {
    let waiter!: Waiter;
    const promise = new Promise<SubmissionSettled>((resolve, reject) => {
      waiter = { resolve, reject };
    });
    let set = this.#waiters.get(id);
    if (!set) {
      set = new Set();
      this.#waiters.set(id, set);
    }
    set.add(waiter);
    return { promise, waiter };
  }

  #dropWaiter(id: string, waiter: Waiter): void {
    const set = this.#waiters.get(id);
    set?.delete(waiter);
    if (set?.size === 0) this.#waiters.delete(id);
  }

  #resolve(rec: SubmissionRecord): void {
    const set = this.#waiters.get(rec.id);
    if (!set) return;
    this.#waiters.delete(rec.id);
    const { seq: _seq, ...record } = rec as SubmissionRecord & { seq?: number };
    for (const w of set) {
      w.resolve({
        status: record.status as "done" | "unanswered",
        result: structuredClone(record.result),
        record: structuredClone(record),
      });
    }
  }

  #onCommit(event: CommitEvent): void {
    const b = event.batch;
    for (const s of [...(b.createSubmissions ?? []), ...(b.replaceSubmissions ?? [])]) {
      if (SETTLED.has(s.status)) this.#resolve(s);
    }
    const ended = (b.replaceTasks ?? []).filter(
      (t) =>
        t.kind === GENERATION_TASK_KIND && t.status === "terminal" && t.outcome?.type !== "done",
    );
    if (ended.length > 0 && this.#stopped === null) {
      void this.#settleOrphans(ended.map((t) => t.id)).catch((err) => {
        if (this.#stopped === null) this.#report(err, { during: "settle" });
      });
    }
  }

  /**
   * Settle active submissions whose current generation has ended without
   * settling them. `taskIds` limits the check to those generations; null
   * checks every active submission (on open).
   */
  async #settleOrphans(taskIds: string[] | null): Promise<void> {
    const candidates: SubmissionRecord[] = [];
    if (taskIds === null) {
      for (let cursor: string | null = null; ; ) {
        const page = await this.session.reader.listConversations({ limit: MAX_PAGE_LIMIT, cursor });
        for (const c of page.items) {
          const subs = await this.session.reader.listSubmissions(c.id, {
            statuses: ["active"],
            limit: MAX_PAGE_LIMIT,
          });
          candidates.push(...subs.items);
        }
        cursor = page.nextCursor;
        if (cursor === null) break;
      }
    } else {
      for (const id of taskIds) {
        const task = this.runtime.task(id);
        const submissionId = (task?.input as GenerationInput | undefined)?.submissionId;
        if (!submissionId) continue;
        const sub = await this.session.reader.getSubmission(submissionId);
        if (sub) candidates.push(sub);
      }
    }
    const orphans = candidates.filter((s) => {
      if (s.status !== "active" || s.taskId === null) return false;
      const task = this.runtime.task(s.taskId);
      return task === null || task.status === "terminal";
    });
    if (orphans.length === 0) return;
    await this.runtime.commit(async (tx, ops) => {
      // Reads first: a commit may not read tables after its first write.
      const current: Array<{ sub: SubmissionRecord; task: TaskRecord | null }> = [];
      for (const o of orphans) {
        const sub = await tx.getSubmission(o.id);
        const task = sub?.taskId ? this.runtime.task(sub.taskId) : null;
        if (sub?.status === "active" && (task === null || task.status === "terminal")) {
          current.push({ sub, task });
        }
      }
      const inboxes = new Map<string, Inbox>();
      for (const { sub } of current) {
        if (!inboxes.has(sub.conversationId)) {
          inboxes.set(sub.conversationId, await Inbox.load(tx, sub.conversationId));
        }
      }
      const runs: Array<{ conversationId: string; runId: string; result: SubmissionUnanswered }> =
        [];
      for (const { sub, task } of current) {
        const outcome = task?.outcome ?? null;
        const reason = outcome?.type === "aborted" ? "aborted" : "faulted";
        const message =
          outcome?.type === "aborted"
            ? outcome.reason
            : outcome?.type === "faulted"
              ? outcome.message
              : "the run ended without an answer";
        const result: SubmissionUnanswered = { reason, message, error: null };
        settleSubmission(tx, sub, "unanswered", result);
        inboxes.get(sub.conversationId)?.subs.delete(sub.id);
        const runId = (task?.input as GenerationInput | undefined)?.runId;
        if (task?.kind === GENERATION_TASK_KIND && runId !== undefined) {
          runs.push({ conversationId: sub.conversationId, runId, result });
        }
        if (task) await clearLive(tx, sub.conversationId, task.id);
      }
      // The ended runs settle the steers placed into them, and an idle
      // conversation starts its next queued input (once).
      const started = new Set<string>();
      for (const run of runs) {
        const inbox = inboxes.get(run.conversationId) as Inbox;
        const start = !started.has(run.conversationId) && !isBusy(this.runtime, run.conversationId);
        started.add(run.conversationId);
        inbox.endRun(ops, this.generation, run.runId, "unanswered", run.result, start);
      }
      for (const [conversationId, inbox] of inboxes) {
        if (started.has(conversationId) || isBusy(this.runtime, conversationId)) continue;
        inbox.startNext(ops, this.generation);
      }
    });
  }

  #stop(reason: unknown): void {
    if (this.#stopped !== null) return;
    this.#stopped = reason ?? new RuntimeStopped("stopped");
    this.#unsubscribe();
    const error = this.#stoppedError();
    for (const set of this.#waiters.values()) for (const w of set) w.reject(error);
    this.#waiters.clear();
  }

  #stoppedError(): RuntimeStopped {
    const r = this.#stopped;
    if (r instanceof RuntimeStopped) return r;
    return new RuntimeStopped(r instanceof Error ? r.message : String(r), { cause: r });
  }
}
