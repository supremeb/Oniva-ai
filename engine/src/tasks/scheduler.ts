/**
 * The task scheduler (R5, R7, KTD10).
 *
 * All task state changes happen on the session's commit line, and all
 * decisions are made from the {@link TaskIndex}, which inside a commit
 * callback is exactly committed state. Handlers and abort handlers run off
 * the line; they reach it only through guarded commits.
 *
 * After every adopted commit the scheduler "pumps": in one commit it
 * propagates abort requests to owned work, applies `failFast`, wakes waiting
 * tasks whose condition holds, finishes `completing` tasks whose owned work
 * has settled, and reserves pending tasks (`pending → running`) up to the
 * concurrency limit. It then starts handlers for the reserved tasks, starts
 * abort finishing for aborting tasks whose owned work is terminal, and arms
 * injected-clock timers for `until` deadlines.
 *
 * A phase handler runs in an invocation: `next` keeps the task `running` and
 * the invocation continues with the new phase; `waiting` and `done` end it.
 */
import type { CancelTimer, Clock } from "../core/clock.js";
import { Context } from "../core/context.js";
import { Cancelled } from "../core/errors.js";
import type { JsonValue } from "../core/json.js";
import type { CommitEvent } from "../session/publish.js";
import type { Session } from "../session/session.js";
import type { NewConversation, Transaction } from "../session/transaction.js";
import type { ConversationRecord, TaskOutcome, TaskRecord } from "../storage/types.js";
import {
  aborted,
  abortReady,
  abortReason,
  abortTargets,
  inheritedAbort,
  withAbortRequest,
} from "./abort.js";
import {
  type AbortScope,
  type Committed,
  committed,
  committedTransition,
  getSignal,
  InvalidTransition,
  isTransition,
  OUTCOME_KEY,
  RuntimeStopped,
  type SpawnOptions,
  SpawnRejected,
  type TaskDefinition,
  type TaskLookup,
  TaskNotRunning,
  type TaskOps,
  type TaskRegistry,
  type TaskRuntimeApi,
  type TaskScope,
  type Transition,
  taskState,
  WaitRejected,
  withSignal,
} from "./define.js";
import { checkWaitTargets, hasLiveOwned, type TaskIndex } from "./ownership.js";
import { deliverSignal, dueWake, failFastVictims, wake } from "./signals.js";

/** Where a reported error came from. */
export interface TaskErrorContext {
  during: "handler" | "abort" | "scheduler";
  taskId?: string;
}

export type TaskErrorReporter = (error: unknown, context: TaskErrorContext) => void;

/** Reason given to runtime-initiated aborts. */
export const ABORT_REASONS = Object.freeze({
  restart: "restart",
  siblingFailed: "sibling_failed",
  ownerFaulted: "owner_faulted",
} as const);

const copy = <T>(value: T): T => structuredClone(value);

// ---- commit-scoped task writes ------------------------------------------------

/**
 * Task writes within one transaction, layered over the index so that every
 * operation in the commit sees the ones before it.
 */
export class TaskWrites implements TaskLookup {
  readonly #tx: Transaction;
  readonly #index: TaskIndex;
  readonly #registry: TaskRegistry;
  readonly now: number;
  readonly #records = new Map<string, TaskRecord>();
  readonly #children = new Map<string, string[]>();
  readonly #roots = new Map<string, string[]>();
  readonly #convOwner = new Map<string, string | null>();
  readonly #ownedConvs = new Map<string, string[]>();

  constructor(tx: Transaction, index: TaskIndex, registry: TaskRegistry, now: number) {
    this.#tx = tx;
    this.#index = index;
    this.#registry = registry;
    this.now = now;
  }

  get(id: string): TaskRecord | undefined {
    return this.#records.get(id) ?? this.#index.get(id);
  }

  ownedBy(id: string): string[] {
    const out = [...this.#index.ownedBy(id), ...(this.#children.get(id) ?? [])];
    for (const conv of this.#ownedConvs.get(id) ?? []) out.push(...this.rootsOf(conv));
    return out;
  }

  conversationOwner(conversationId: string): string | null {
    return this.#convOwner.has(conversationId)
      ? (this.#convOwner.get(conversationId) ?? null)
      : this.#index.conversationOwner(conversationId);
  }

  rootsOf(conversationId: string): string[] {
    return [...this.#index.rootsOf(conversationId), ...(this.#roots.get(conversationId) ?? [])];
  }

  /** Replace a task (or update one created in this commit). */
  put(record: TaskRecord): void {
    this.#tx.replaceTask(record);
    this.#records.set(record.id, record);
  }

  createConversation(input: NewConversation): ConversationRecord {
    const conv = this.#tx.createConversation(input);
    this.#convOwner.set(conv.id, conv.ownerTaskId);
    if (conv.ownerTaskId !== null) push(this.#ownedConvs, conv.ownerTaskId, conv.id);
    return conv;
  }

  spawn<I, C>(
    def: TaskDefinition<I, C>,
    options: SpawnOptions<I, C>,
    spawner: TaskRecord | null,
  ): TaskRecord {
    const registered = this.#registry.get(def.kind);
    if (!registered) throw new SpawnRejected(`task kind ${def.kind} is not registered`);
    const phase = options.phase ?? registered.initialPhase;
    if (!Object.hasOwn(registered.phases, phase)) {
      throw new SpawnRejected(`task kind ${def.kind} has no phase ${phase}`);
    }
    const conversationId = options.conversationId ?? spawner?.conversationId;
    if (conversationId === undefined) throw new SpawnRejected("conversationId is required");
    const parentTaskId =
      options.parentTaskId === undefined ? (spawner?.id ?? null) : options.parentTaskId;
    let abortRequest: string | null = null;
    if (parentTaskId !== null) {
      const parent = this.get(parentTaskId);
      if (!parent) throw new SpawnRejected(`unknown owner task ${parentTaskId}`);
      if (parent.status === "terminal") {
        throw new SpawnRejected(`owner task ${parentTaskId} has finished`);
      }
      abortRequest = abortReason(parent) ?? inheritedAbort(this, parent);
    } else {
      const owner = this.conversationOwner(conversationId);
      const ownerTask = owner === null ? undefined : this.get(owner);
      if (ownerTask) {
        if (ownerTask.status === "terminal") {
          throw new SpawnRejected(
            `owner task ${owner} of conversation ${conversationId} has finished`,
          );
        }
        abortRequest = abortReason(ownerTask) ?? inheritedAbort(this, ownerTask);
      }
    }
    let rec = this.#tx.createTask({
      ...(options.id === undefined ? {} : { id: options.id }),
      kind: def.kind,
      conversationId,
      parentTaskId,
      status: "pending",
      phase,
      checkpoint: (options.checkpoint ?? null) as JsonValue,
      input: options.input as JsonValue,
      waiting: null,
      signals: {},
      outcome: null,
      abandonOnRestart: options.abandonOnRestart ?? false,
    });
    if (abortRequest !== null) {
      rec = withAbortRequest(rec, abortRequest);
      this.#tx.replaceTask(rec);
    }
    this.#records.set(rec.id, rec);
    if (parentTaskId !== null) push(this.#children, parentTaskId, rec.id);
    else push(this.#roots, conversationId, rec.id);
    return copy(rec);
  }

  signal(taskId: string, key: string, payload: JsonValue): void {
    this.put(deliverSignal(this.get(taskId), taskId, key, payload, this.now));
  }

  abort(taskId: string, reason: string): boolean {
    const task = this.get(taskId);
    if (!task || task.status === "terminal") return false;
    for (const t of abortTargets(this, taskId)) this.put(withAbortRequest(t, reason));
    return true;
  }

  abortConversation(conversationId: string, reason: string): number {
    let n = 0;
    for (const id of this.rootsOf(conversationId)) if (this.abort(id, reason)) n++;
    return n;
  }

  /** Finish `task` with `outcome`, via `completing` while it owns live work. */
  finish(task: TaskRecord, outcome: TaskOutcome, abortOwned: string | null): void {
    if (!hasLiveOwned(this, task.id)) {
      this.put({ ...task, status: "terminal", waiting: null, outcome });
      return;
    }
    if (abortOwned !== null) {
      for (const id of this.ownedBy(task.id)) this.abort(id, abortOwned);
    }
    const current = this.get(task.id) as TaskRecord;
    this.put({
      ...current,
      status: "completing",
      waiting: null,
      signals: withSignal(current.signals, OUTCOME_KEY, outcome),
    });
  }

  ops(spawner: TaskRecord | null): TaskOps {
    return {
      spawn: (def, options) => this.spawn(def, options, spawner),
      createConversation: (input) => this.createConversation(input),
      signal: (taskId, key, payload) => this.signal(taskId, key, payload),
      abort: (taskId, reason) => this.abort(taskId, reason),
      abortConversation: (conversationId, reason) => this.abortConversation(conversationId, reason),
      task: (id) => {
        const t = this.get(id);
        return t ? copy(t) : null;
      },
    };
  }
}

function push(map: Map<string, string[]>, key: string, value: string): void {
  const list = map.get(key);
  if (list) list.push(value);
  else map.set(key, [value]);
}

// ---- scheduler ----------------------------------------------------------------

interface Step {
  transition: Transition<unknown> | null;
  committing: boolean;
}

interface Invocation {
  ctx: Context;
  step: Step | null;
}

interface Waiter<T> {
  resolve: (value: T) => void;
  reject: (error: unknown) => void;
}

export interface SchedulerOptions {
  session: Session;
  index: TaskIndex;
  registry: TaskRegistry;
  clock: Clock;
  concurrency: number;
  report: TaskErrorReporter;
  api: TaskRuntimeApi;
}

export type SchedulerState = "running" | "stopped";

export class Scheduler {
  readonly #session: Session;
  readonly #index: TaskIndex;
  readonly #registry: TaskRegistry;
  readonly #clock: Clock;
  readonly #concurrency: number;
  readonly #report: TaskErrorReporter;
  readonly #api: TaskRuntimeApi;
  readonly #root: Context;
  readonly #writes = new WeakMap<Transaction, TaskWrites>();
  readonly #invocations = new Map<string, Invocation>();
  readonly #aborters = new Set<string>();
  readonly #timers = new Map<string, { until: number; cancel: CancelTimer }>();
  readonly #taskWaiters = new Map<string, Waiter<TaskRecord>[]>();
  #idleWaiters: Waiter<void>[] = [];
  #state: SchedulerState = "running";
  #stopReason: unknown = null;
  #pumpActive = false;
  #pumpDone: Promise<void> = Promise.resolve();
  #dirty = false;
  #unsubscribe: (() => void) | null = null;
  readonly #resolveStopped: (reason: unknown) => void;
  /** Resolves with the stop reason once the scheduler stops. */
  readonly stopped: Promise<unknown>;

  constructor(options: SchedulerOptions) {
    this.#session = options.session;
    this.#index = options.index;
    this.#registry = options.registry;
    this.#clock = options.clock;
    this.#concurrency = options.concurrency;
    this.#report = options.report;
    this.#api = options.api;
    this.#root = Context.background(options.clock);
    let resolve!: (reason: unknown) => void;
    this.stopped = new Promise((r) => {
      resolve = r;
    });
    this.#resolveStopped = resolve;
  }

  get state(): SchedulerState {
    return this.#state;
  }

  /** Start following adopted commits. Call on the commit line, after loading the index. */
  follow(): void {
    this.#unsubscribe ??= this.#session.subscribeCommits((event) => this.#onCommit(event));
  }

  /** Task writes for `tx`, shared by every caller within the same commit. */
  writes(tx: Transaction): TaskWrites {
    let w = this.#writes.get(tx);
    if (!w) {
      w = new TaskWrites(tx, this.#index, this.#registry, this.#clock.now());
      this.#writes.set(tx, w);
    }
    return w;
  }

  /** A commit that is rejected (writing nothing) once the scheduler has stopped. */
  commit<T>(fn: (tx: Transaction, writes: TaskWrites) => T | Promise<T>): Promise<T> {
    if (this.#state !== "running") return Promise.reject(this.#stoppedError());
    return this.#session.commit(async (tx) => {
      this.#assertRunning();
      const value = await fn(tx, this.writes(tx));
      this.#assertRunning();
      return value;
    });
  }

  /** Run the pump now. */
  kick(): void {
    if (this.#state !== "running") return;
    this.#dirty = true;
    if (this.#pumpActive) return;
    this.#pumpActive = true;
    this.#pumpDone = this.#pump();
  }

  waitForTask(id: string): Promise<TaskRecord> {
    const task = this.#index.get(id);
    if (task?.status === "terminal") return Promise.resolve(copy(task));
    if (this.#state !== "running") return Promise.reject(this.#stoppedError());
    if (!task) return Promise.reject(new Error(`unknown task ${id}`));
    return new Promise((resolve, reject) => {
      const list = this.#taskWaiters.get(id) ?? [];
      list.push({ resolve, reject });
      this.#taskWaiters.set(id, list);
    });
  }

  waitForIdle(): Promise<void> {
    if (this.#state !== "running") return Promise.reject(this.#stoppedError());
    const p = new Promise<void>((resolve, reject) => {
      this.#idleWaiters.push({ resolve, reject });
    });
    this.kick();
    return p;
  }

  /** Stop scheduling and cancel every running handler with `reason`. Idempotent. */
  halt(reason: unknown): void {
    if (this.#state !== "running") return;
    this.#state = "stopped";
    this.#stopReason = reason;
    this.#unsubscribe?.();
    this.#unsubscribe = null;
    for (const t of this.#timers.values()) t.cancel();
    this.#timers.clear();
    this.#root.cancel(reason);
    const error = this.#stoppedError();
    for (const list of this.#taskWaiters.values()) for (const w of list) w.reject(error);
    this.#taskWaiters.clear();
    for (const w of this.#idleWaiters.splice(0)) w.reject(error);
    this.#resolveStopped(reason);
  }

  /** Settles once any in-flight pump has finished. */
  async drained(): Promise<void> {
    await this.#pumpDone;
  }

  // ---- commit observation ---------------------------------------------------

  #onCommit(event: CommitEvent): void {
    const b = event.batch;
    for (const c of b.createConversations ?? []) this.#index.putConversation(c);
    const changed = [...(b.createTasks ?? []), ...(b.replaceTasks ?? [])];
    for (const t of changed) this.#index.putTask(t);
    for (const t of changed) {
      const inv = this.#invocations.get(t.id);
      const reason = abortReason(t);
      if (inv && reason !== null) inv.ctx.cancel(new Cancelled(`task aborted: ${reason}`));
      if (t.status === "terminal") {
        const waiters = this.#taskWaiters.get(t.id);
        this.#taskWaiters.delete(t.id);
        for (const w of waiters ?? []) w.resolve(copy(t));
      }
    }
    if (changed.length > 0 || (b.createConversations?.length ?? 0) > 0) this.kick();
  }

  // ---- pump -------------------------------------------------------------------

  async #pump(): Promise<void> {
    try {
      while (this.#dirty && this.#state === "running") {
        this.#dirty = false;
        await this.#step();
      }
    } catch (err) {
      if (this.#state === "running") {
        this.#report(err, { during: "scheduler" });
        this.halt(err);
      }
    } finally {
      this.#pumpActive = false;
      this.#settleIdle();
    }
  }

  async #step(): Promise<void> {
    const reserved = await this.commit((_tx, w) => {
      const now = w.now;
      const live = () => this.#index.live().map((t) => w.get(t.id) as TaskRecord);
      // Owned work of an aborting task is aborted too.
      for (const t of live()) {
        if (abortReason(t) !== null) continue;
        const inherited = inheritedAbort(w, t);
        if (inherited !== null) w.put(withAbortRequest(t, inherited));
      }
      // failFast: the first failure aborts the rest of the wait set.
      for (const t of live()) {
        if (abortReason(t) !== null) continue;
        for (const id of failFastVictims(t, w)) w.abort(id, ABORT_REASONS.siblingFailed);
      }
      for (const t of live()) {
        if (abortReason(t) !== null) continue;
        if (t.status === "waiting") {
          const reason = dueWake(t, w, now);
          if (reason) w.put(wake(t, reason));
        } else if (t.status === "completing" && !hasLiveOwned(w, t.id)) {
          // `finish` sets $outcome before a task enters `completing`.
          const outcome = getSignal(t.signals, OUTCOME_KEY) as TaskOutcome;
          w.put({ ...t, status: "terminal", outcome });
        }
      }
      const ids: string[] = [];
      let slots = this.#concurrency - this.#invocations.size;
      for (const t of live()) {
        if (slots <= 0) break;
        if (t.status !== "pending" || abortReason(t) !== null || this.#invocations.has(t.id)) {
          continue;
        }
        w.put({ ...t, status: "running" });
        ids.push(t.id);
        slots--;
      }
      return ids;
    });

    for (const id of reserved) void this.#invoke(id);
    for (const t of this.#index.live()) {
      if (this.#invocations.has(t.id) || this.#aborters.has(t.id)) continue;
      if (abortReady(this.#index, t)) void this.#finishAbort(t.id);
    }
    this.#armTimers();
  }

  #armTimers(): void {
    const wanted = new Map<string, number>();
    for (const t of this.#index.live()) {
      if (t.status === "waiting" && t.waiting?.until != null && abortReason(t) === null) {
        wanted.set(t.id, t.waiting.until);
      }
    }
    for (const [id, timer] of this.#timers) {
      if (wanted.get(id) !== timer.until) {
        timer.cancel();
        this.#timers.delete(id);
      }
    }
    for (const [id, until] of wanted) {
      if (this.#timers.has(id)) continue;
      const cancel = this.#clock.setTimer(until - this.#clock.now(), () => {
        this.#timers.delete(id);
        this.kick();
      });
      this.#timers.set(id, { until, cancel });
    }
  }

  #settleIdle(): void {
    if (this.#state !== "running") return;
    if (this.#pumpActive || this.#dirty || this.#invocations.size > 0 || this.#aborters.size > 0) {
      return;
    }
    for (const w of this.#idleWaiters.splice(0)) w.resolve();
  }

  // ---- handler invocations ------------------------------------------------------

  async #invoke(taskId: string): Promise<void> {
    const inv: Invocation = { ctx: this.#root.child(), step: null };
    this.#invocations.set(taskId, inv);
    try {
      for (;;) {
        const rec = this.#index.get(taskId);
        if (this.#state !== "running" || !rec || rec.status !== "running") return;
        if (abortReason(rec) !== null) return;
        const def = this.#registry.get(rec.kind);
        const handler = def?.phases[rec.phase];
        if (!handler) {
          const why = def
            ? `task kind ${rec.kind} has no phase ${rec.phase}`
            : `task kind ${rec.kind} is not registered`;
          await this.#fault(taskId, inv, new InvalidTransition(taskId, why));
          return;
        }
        const step: Step = { transition: null, committing: false };
        inv.step = step;
        let failure: { error: unknown } | null = null;
        let result: unknown;
        try {
          result = await handler(
            taskState<never, never>(rec),
            this.#scope(taskId, inv, step) as TaskScope<never>,
            inv.ctx,
          );
        } catch (error) {
          failure = { error };
        }
        if (this.#abandoned(taskId, inv)) return;
        const transition = step.transition;
        if (!transition) {
          const error =
            failure?.error ??
            new InvalidTransition(taskId, "a handler must return the result of runtime.commit");
          await this.#fault(taskId, inv, error);
          return;
        }
        // The transition is committed; a late throw or odd return value changes nothing.
        if (failure) {
          this.#report(failure.error, { during: "handler", taskId });
        } else if (committedTransition(result) !== transition) {
          this.#report(
            new InvalidTransition(taskId, "the handler returned something other than its commit"),
            { during: "handler", taskId },
          );
        }
        if (transition.type !== "next") return;
      }
    } catch (err) {
      if (this.#state === "running") this.#report(err, { during: "handler", taskId });
    } finally {
      inv.ctx.cancel();
      this.#invocations.delete(taskId);
      this.kick();
    }
  }

  /** True when the invocation's result no longer matters (stopped, or task aborted). */
  #abandoned(taskId: string, inv: Invocation): boolean {
    if (this.#state !== "running" || this.#invocations.get(taskId) !== inv) return true;
    const rec = this.#index.get(taskId);
    return !rec || abortReason(rec) !== null;
  }

  #assertCurrent(w: TaskWrites, taskId: string, inv: Invocation, step: Step): TaskRecord {
    const rec = w.get(taskId);
    if (this.#invocations.get(taskId) !== inv || inv.step !== step) {
      throw new TaskNotRunning(taskId, "the invocation has ended");
    }
    if (step.transition) throw new TaskNotRunning(taskId, "the phase has already transitioned");
    if (rec?.status !== "running") {
      throw new TaskNotRunning(taskId, `status is ${rec?.status ?? "unknown"}`);
    }
    if (abortReason(rec) !== null) throw new TaskNotRunning(taskId, "the task is being aborted");
    return rec;
  }

  #scope(taskId: string, inv: Invocation, step: Step): TaskScope<unknown> {
    return {
      commit: (fn) => {
        if (step.committing || step.transition) {
          return Promise.reject(
            new TaskNotRunning(taskId, "the phase has already committed a transition"),
          );
        }
        step.committing = true;
        return this.commit(async (tx, w) => {
          const rec = this.#assertCurrent(w, taskId, inv, step);
          const transition = await fn(tx, w.ops(rec));
          if (!isTransition(transition)) {
            throw new InvalidTransition(
              taskId,
              "commit callbacks must return next(), waiting() or done()",
            );
          }
          this.#apply(w, this.#assertCurrent(w, taskId, inv, step), transition);
          return transition;
        }).then(
          (transition): Committed => {
            step.transition = transition;
            return committed(transition);
          },
          (err: unknown) => {
            step.committing = false;
            throw err;
          },
        );
      },
      write: (fn) =>
        this.commit(async (tx, w) => {
          const rec = this.#assertCurrent(w, taskId, inv, step);
          return fn(tx, w.ops(rec));
        }),
      task: (id) => this.#api.task(id),
      runtime: this.#api,
    };
  }

  #apply(w: TaskWrites, rec: TaskRecord, t: Transition<unknown>): void {
    const def = this.#registry.get(rec.kind);
    const checkPhase = (phase: string) => {
      if (!def || !Object.hasOwn(def.phases, phase)) {
        throw new InvalidTransition(rec.id, `task kind ${rec.kind} has no phase ${phase}`);
      }
    };
    switch (t.type) {
      case "next":
        checkPhase(t.phase);
        w.put({ ...rec, phase: t.phase, checkpoint: t.checkpoint as JsonValue });
        return;
      case "waiting": {
        const req = t.wait;
        const on = [...new Set(req.on ?? [])];
        const signal = req.signal ?? null;
        const until = req.until ?? null;
        const policy = req.policy ?? "allSettled";
        if (policy !== "allSettled" && policy !== "failFast") {
          throw new WaitRejected(rec.id, `unknown policy ${String(policy)}`);
        }
        if (until !== null && !Number.isFinite(until)) {
          throw new WaitRejected(rec.id, "until must be a finite number");
        }
        if (on.length === 0 && signal === null && until === null) {
          throw new WaitRejected(rec.id, "a wait needs at least one of on, signal or until");
        }
        checkWaitTargets(w, rec.id, on);
        const phase = req.phase ?? rec.phase;
        checkPhase(phase);
        w.put({
          ...rec,
          status: "waiting",
          phase,
          checkpoint: ("checkpoint" in req ? req.checkpoint : rec.checkpoint) as JsonValue,
          waiting: { on, policy, signal, until },
        });
        return;
      }
      case "done":
        w.finish(rec, { type: "done", value: t.value }, null);
        return;
    }
  }

  async #fault(taskId: string, inv: Invocation, error: unknown): Promise<void> {
    const outcome: TaskOutcome = {
      type: "faulted",
      message: error instanceof Error ? error.message : String(error),
      detail: { name: error instanceof Error ? error.name : typeof error },
    };
    try {
      await this.commit((_tx, w) => {
        const rec = w.get(taskId);
        if (this.#invocations.get(taskId) !== inv || !rec || rec.status !== "running") return;
        if (abortReason(rec) !== null) return;
        w.finish(rec, outcome, ABORT_REASONS.ownerFaulted);
      });
    } catch (err) {
      if (this.#state === "running") this.#report(err, { during: "handler", taskId });
    }
  }

  // ---- abort finishing ------------------------------------------------------------

  async #finishAbort(taskId: string): Promise<void> {
    this.#aborters.add(taskId);
    const ctx = this.#root.child();
    try {
      const rec = this.#index.get(taskId);
      const reason = rec ? abortReason(rec) : null;
      if (!rec || reason === null) return;
      let finished = false;
      let committing = false;
      const finish = async (fn?: (tx: Transaction, ops: TaskOps) => void | Promise<void>) => {
        committing = true;
        try {
          await this.commit(async (tx, w) => {
            const cur = w.get(taskId);
            if (!cur || !abortReady(w, cur)) {
              throw new TaskNotRunning(taskId, "the task is no longer ready to finish aborting");
            }
            if (fn) await fn(tx, w.ops(cur));
            w.put(aborted(w.get(taskId) as TaskRecord, reason));
          });
          finished = true;
        } finally {
          committing = false;
        }
      };
      const def = this.#registry.get(rec.kind);
      if (def?.onAbort) {
        const scope: AbortScope = {
          reason,
          task: (id) => this.#api.task(id),
          commit: (fn) =>
            finished || committing
              ? Promise.reject(new TaskNotRunning(taskId, "the abort has already been committed"))
              : finish(fn),
        };
        try {
          await def.onAbort(taskState<never, never>(rec), scope, ctx);
        } catch (err) {
          if (this.#state === "running") this.#report(err, { during: "abort", taskId });
        }
      }
      if (!finished && this.#state === "running") await finish();
    } catch (err) {
      if (this.#state === "running") this.#report(err, { during: "abort", taskId });
    } finally {
      ctx.cancel();
      this.#aborters.delete(taskId);
      this.kick();
    }
  }

  // ---- state ----------------------------------------------------------------------

  #assertRunning(): void {
    if (this.#state !== "running") throw this.#stoppedError();
  }

  #stoppedError(): RuntimeStopped {
    const r = this.#stopReason;
    return new RuntimeStopped(r instanceof Error ? r.message : String(r), { cause: r });
  }
}
