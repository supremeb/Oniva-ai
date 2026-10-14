/**
 * The task runtime (R5, R7, R10): durable tasks over one session.
 *
 * `TaskRuntime.open(session, { tasks })` loads the session's tasks on the
 * commit line and, in the same commit, recovers from the previous process:
 * `running` tasks go back to `pending` (their current phase runs again), and
 * live `abandonOnRestart` tasks are aborted with reason `restart`. Waits,
 * deadlines and pending aborts are then picked up where they were.
 *
 * Stopping: `close()`, the `signal` option (wire the lease keeper's signal
 * here, KTD3) and the session ending all stop the runtime. Stopping cancels
 * every running handler's context and rejects any commit the runtime or a
 * handler attempts afterwards; nothing is marked aborted, because the next
 * owner resumes the durable state.
 */
import { type Clock, realClock } from "../core/clock.js";
import type { JsonValue } from "../core/json.js";
import type { Session } from "../session/session.js";
import type { Transaction } from "../session/transaction.js";
import { MAX_PAGE_LIMIT, type TaskRecord, type TaskStatus } from "../storage/types.js";
import { abortReason } from "./abort.js";
import {
  RuntimeStopped,
  type SpawnOptions,
  type TaskDefinition,
  type TaskOps,
  TaskRegistry,
  type TaskRuntimeApi,
} from "./define.js";
import { TaskIndex } from "./ownership.js";
import {
  ABORT_REASONS,
  Scheduler,
  type SchedulerState,
  type TaskErrorReporter,
} from "./scheduler.js";

export type { TaskErrorContext, TaskErrorReporter } from "./scheduler.js";

export const DEFAULT_TASK_CONCURRENCY = 16;

export interface TaskRuntimeOptions {
  /** Task definitions (every kind stored in the session should be registered). */
  tasks: Iterable<TaskDefinition> | TaskRegistry;
  /** Injected clock for deadlines; use the session's clock. Defaults to the real clock. */
  clock?: Clock;
  /** Most handlers running at once. Defaults to {@link DEFAULT_TASK_CONCURRENCY}. */
  concurrency?: number;
  /** Stops the runtime when aborted (for example `LeaseKeeper.signal`). */
  signal?: AbortSignal;
  /** Receives errors that do not fail a task: late handler throws, `onAbort` throws, scheduler failures. */
  onError?: TaskErrorReporter;
}

export class TaskRuntime implements TaskRuntimeApi {
  readonly session: Session;
  readonly registry: TaskRegistry;
  /** Resolves with the reason once the runtime has stopped. */
  readonly closed: Promise<unknown>;
  readonly #index: TaskIndex;
  readonly #scheduler: Scheduler;
  readonly #detach: () => void;

  private constructor(session: Session, options: TaskRuntimeOptions) {
    const concurrency = options.concurrency ?? DEFAULT_TASK_CONCURRENCY;
    if (!Number.isSafeInteger(concurrency) || concurrency < 1) {
      throw new RangeError(`concurrency must be a positive integer, got ${concurrency}`);
    }
    this.session = session;
    this.registry =
      options.tasks instanceof TaskRegistry ? options.tasks : new TaskRegistry(options.tasks);
    this.#index = new TaskIndex();
    this.#scheduler = new Scheduler({
      session,
      index: this.#index,
      registry: this.registry,
      clock: options.clock ?? realClock,
      concurrency,
      report: options.onError ?? (() => {}),
      api: this,
    });
    this.closed = this.#scheduler.stopped;

    const stops = [session.signal, ...(options.signal ? [options.signal] : [])];
    const listeners = stops.map((signal) => {
      const onAbort = () => this.#scheduler.halt(signal.reason);
      signal.addEventListener("abort", onAbort, { once: true });
      return () => signal.removeEventListener("abort", onAbort);
    });
    this.#detach = () => {
      for (const off of listeners) off();
    };
    void this.closed.then(this.#detach);
    for (const signal of stops) if (signal.aborted) this.#scheduler.halt(signal.reason);
  }

  /** Load the session's tasks, recover from the previous process and start scheduling. */
  static async open(session: Session, options: TaskRuntimeOptions): Promise<TaskRuntime> {
    const runtime = new TaskRuntime(session, options);
    try {
      await runtime.#recover();
    } catch (err) {
      runtime.#scheduler.halt(err);
      throw err;
    }
    runtime.#scheduler.kick();
    return runtime;
  }

  get state(): SchedulerState {
    return this.#scheduler.state;
  }

  /** Committed state of a task (a copy), or null. */
  task(id: string): TaskRecord | null {
    const t = this.#index.get(id);
    return t ? structuredClone(t) : null;
  }

  /** Committed tasks (copies), in creation order. */
  tasks(filter: { conversationId?: string; status?: TaskStatus } = {}): TaskRecord[] {
    return this.#index
      .all()
      .filter(
        (t) =>
          (filter.conversationId === undefined || t.conversationId === filter.conversationId) &&
          (filter.status === undefined || t.status === filter.status),
      )
      .map((t) => structuredClone(t));
  }

  /** Task operations inside a `session.commit` callback (consistent within the commit). */
  in(tx: Transaction): TaskOps {
    if (this.state !== "running") throw new RuntimeStopped("cannot use task operations");
    return this.#scheduler.writes(tx).ops(null);
  }

  /** `session.commit` with task operations; rejected once the runtime has stopped. */
  commit<T>(fn: (tx: Transaction, ops: TaskOps) => T | Promise<T>): Promise<T> {
    return this.#scheduler.commit((tx, w) => fn(tx, w.ops(null)));
  }

  /** Create a task in its own commit. */
  spawn<I, C>(def: TaskDefinition<I, C>, options: SpawnOptions<I, C>): Promise<TaskRecord> {
    return this.commit((_tx, ops) => ops.spawn(def, options));
  }

  /** Deliver a signal. Rejects with `SignalRejected` for a finished task or a delivered key. */
  signal(taskId: string, key: string, payload: JsonValue): Promise<void> {
    return this.commit((_tx, ops) => ops.signal(taskId, key, payload));
  }

  /** Abort a task and its owned work (bottom-up). False if it had already finished. */
  abort(taskId: string, reason: string): Promise<boolean> {
    return this.commit((_tx, ops) => ops.abort(taskId, reason));
  }

  /** Abort every live task a conversation owns. Resolves to how many were aborted. */
  abortConversation(conversationId: string, reason: string): Promise<number> {
    return this.commit((_tx, ops) => ops.abortConversation(conversationId, reason));
  }

  /** Resolves with the task's terminal record. */
  waitForTask(id: string): Promise<TaskRecord> {
    return this.#scheduler.waitForTask(id);
  }

  /**
   * Resolves once no handler or abort handler is running and nothing is due:
   * every live task is waiting on something that has not happened yet.
   */
  waitForIdle(): Promise<void> {
    return this.#scheduler.waitForIdle();
  }

  /**
   * Stop: cancel running handlers and reject later commits. Does not wait for
   * handlers that ignore their context. The session stays open (close it
   * separately). Idempotent.
   */
  async close(): Promise<void> {
    this.#scheduler.halt(new RuntimeStopped("closed"));
    this.#detach();
    await this.#scheduler.drained();
  }

  async #recover(): Promise<void> {
    await this.#scheduler.commit(async (tx, w) => {
      for (let cursor: string | null = null; ; ) {
        const page = await tx.listConversations({ limit: MAX_PAGE_LIMIT, cursor });
        for (const c of page.items) this.#index.putConversation(c);
        cursor = page.nextCursor;
        if (cursor === null) break;
      }
      for (let cursor: string | null = null; ; ) {
        const page = await tx.listTasks({ limit: MAX_PAGE_LIMIT, cursor });
        for (const t of page.items) this.#index.putTask(t);
        cursor = page.nextCursor;
        if (cursor === null) break;
      }
      this.#scheduler.follow();
      for (const t of this.#index.live()) {
        if (t.status === "running") w.put({ ...t, status: "pending" });
      }
      for (const t of this.#index.live()) {
        if (t.abandonOnRestart && abortReason(t) === null) w.abort(t.id, ABORT_REASONS.restart);
      }
    });
  }
}
