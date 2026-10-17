/**
 * Task definitions (R5, R7).
 *
 * A task is a durable state machine: a `kind`, named phases and an optional
 * abort handler. The scheduler calls the handler for the task's current
 * phase; the handler does its work and ends by returning a transition through
 * `runtime.commit`, which stores the transition atomically with whatever else
 * the callback wrote (entries, documents, child tasks):
 *
 * - `next(phase, checkpoint)` — checkpoint and continue with another phase;
 * - `waiting({ on?, policy?, signal?, until?, phase?, checkpoint? })` — park
 *   until the tasks in `on` settle, the signal arrives or `until` passes;
 * - `done(value)` — finish (after any owned work still live has finished).
 *
 * A handler that throws ends the task `faulted`. Handlers must tolerate being
 * re-run: a crash between the start of a phase and its committed transition
 * runs that phase again after a restart.
 */
import type { Context } from "../core/context.js";
import type { JsonValue } from "../core/json.js";
import type { NewConversation, Transaction } from "../session/transaction.js";
import type { ConversationRecord, TaskOutcome, TaskRecord, WaitPolicy } from "../storage/types.js";

// ---- reserved signal keys ---------------------------------------------------

/**
 * Signal keys starting with `$` are the runtime's own bookkeeping, stored in
 * `TaskRecord.signals` so that it survives restarts:
 * - `$wake`: why a waiting task last woke ({@link Wake});
 * - `$abort`: a pending abort request (`{ reason }`);
 * - `$outcome`: the outcome a `completing` task will end with.
 */
export const RESERVED_PREFIX = "$";
export const WAKE_KEY = "$wake";
export const ABORT_KEY = "$abort";
export const OUTCOME_KEY = "$outcome";

/** Payload types of the reserved signal keys the runtime writes with {@link withSignal}. */
export interface ReservedSignals {
  [WAKE_KEY]: Wake;
  [OUTCOME_KEY]: TaskOutcome;
}

/** A reserved signal's payload, or undefined when it was never written. */
export function getSignal<K extends keyof ReservedSignals>(
  signals: TaskRecord["signals"],
  key: K,
): ReservedSignals[K] | undefined {
  return signals[key] as unknown as ReservedSignals[K] | undefined;
}

/** `signals` with a reserved signal's payload set. */
export function withSignal<K extends keyof ReservedSignals>(
  signals: TaskRecord["signals"],
  key: K,
  value: ReservedSignals[K],
): TaskRecord["signals"] {
  return { ...signals, [key]: value as unknown as JsonValue };
}

// ---- errors -----------------------------------------------------------------

export type TaskErrorCode =
  | "signal_rejected"
  | "wait_rejected"
  | "task_not_running"
  | "invalid_transition"
  | "spawn_rejected"
  | "runtime_stopped";

/** Base class for task runtime errors; `code` is stable. */
export class TaskError extends Error {
  constructor(
    readonly code: TaskErrorCode,
    message: string,
    options?: ErrorOptions,
  ) {
    super(message, options);
    this.name = new.target.name;
  }
}

/** A signal for an unknown or finished task, or for a key already delivered. */
export class SignalRejected extends TaskError {
  constructor(
    readonly taskId: string,
    readonly key: string,
    reason: string,
  ) {
    super("signal_rejected", `signal ${key} for task ${taskId} rejected: ${reason}`);
  }
}

/** A wait with no condition, on an unknown task, or one that would deadlock. */
export class WaitRejected extends TaskError {
  constructor(
    readonly taskId: string,
    reason: string,
  ) {
    super("wait_rejected", `wait by task ${taskId} rejected: ${reason}`);
  }
}

/** A handler wrote for a task it no longer runs (aborted, transitioned, or the runtime stopped). */
export class TaskNotRunning extends TaskError {
  constructor(
    readonly taskId: string,
    reason: string,
  ) {
    super("task_not_running", `task ${taskId} is not running: ${reason}`);
  }
}

/** A handler returned something that is not a valid transition. */
export class InvalidTransition extends TaskError {
  constructor(
    readonly taskId: string,
    reason: string,
  ) {
    super("invalid_transition", `invalid transition for task ${taskId}: ${reason}`);
  }
}

/** A task could not be created (unknown kind or phase, finished owner, ...). */
export class SpawnRejected extends TaskError {
  constructor(reason: string) {
    super("spawn_rejected", `spawn rejected: ${reason}`);
  }
}

/** The runtime has stopped (closed, lease lost, or session ended). */
export class RuntimeStopped extends TaskError {
  constructor(reason: string, options?: ErrorOptions) {
    super("runtime_stopped", `task runtime stopped: ${reason}`, options);
  }
}

// ---- transitions ------------------------------------------------------------

const TRANSITION = Symbol("oniva.task.transition");

/** Why a waiting task woke. */
export type Wake =
  | { reason: "settled"; at: number }
  | { reason: "signal"; key: string; at: number }
  | { reason: "deadline"; at: number };

export interface WaitRequest<C> {
  /** Task ids to wait on (any tasks in the session, owned or not). */
  on?: string[];
  /** `allSettled` (default) or `failFast`: the first failure aborts the rest of `on`. */
  policy?: WaitPolicy;
  /** Signal key to wait for. */
  signal?: string | null;
  /** Absolute deadline, injected-clock epoch ms. */
  until?: number | null;
  /** Phase to resume in. Defaults to the current phase. */
  phase?: string;
  /** Checkpoint to resume with. Defaults to the current checkpoint. */
  checkpoint?: C;
}

export type Transition<C> = { readonly [TRANSITION]: true } & (
  | { type: "next"; phase: string; checkpoint: C }
  | { type: "waiting"; wait: WaitRequest<C> }
  | { type: "done"; value: JsonValue }
);

/** Continue with `phase`, storing `checkpoint`. */
export function next<C>(phase: string, checkpoint: C): Transition<C> {
  return { [TRANSITION]: true, type: "next", phase, checkpoint };
}

/** Park until a wait condition is met. At least one of `on`, `signal`, `until` is required. */
export function waiting<C>(wait: WaitRequest<C>): Transition<C> {
  return { [TRANSITION]: true, type: "waiting", wait };
}

/** Finish with `value`. A task with live owned work enters `completing` first. */
export function done(value: JsonValue): Transition<never> {
  return { [TRANSITION]: true, type: "done", value };
}

export function isTransition(value: unknown): value is Transition<unknown> {
  return typeof value === "object" && value !== null && TRANSITION in value;
}

const COMMITTED = Symbol("oniva.task.committed");

/** Proof that a handler's transition was committed; only `runtime.commit` makes one. */
export interface Committed {
  readonly [COMMITTED]: Transition<unknown>;
}

export function committed(transition: Transition<unknown>): Committed {
  return { [COMMITTED]: transition };
}

export function committedTransition(value: unknown): Transition<unknown> | undefined {
  if (typeof value !== "object" || value === null || !(COMMITTED in value)) return undefined;
  return (value as Committed)[COMMITTED];
}

// ---- handler-facing views ---------------------------------------------------

/** What a handler sees of its task: a private, typed copy of the record. */
export interface TaskState<I, C> {
  readonly id: string;
  readonly kind: string;
  readonly conversationId: string;
  readonly parentTaskId: string | null;
  readonly phase: string;
  readonly input: I;
  readonly checkpoint: C;
  /** Delivered signal payloads (reserved `$` keys removed). */
  readonly signals: Readonly<Record<string, JsonValue>>;
  /** Why the task last woke from a wait (kept until the next wake), or null if it never has. */
  readonly wake: Wake | null;
  readonly abandonOnRestart: boolean;
  /** The full stored record. */
  readonly record: TaskRecord;
}

export function taskState<I, C>(record: TaskRecord): TaskState<I, C> {
  const rec = structuredClone(record);
  const signals: Record<string, JsonValue> = {};
  for (const [key, value] of Object.entries(rec.signals)) {
    if (!key.startsWith(RESERVED_PREFIX)) signals[key] = value;
  }
  return {
    id: rec.id,
    kind: rec.kind,
    conversationId: rec.conversationId,
    parentTaskId: rec.parentTaskId,
    phase: rec.phase,
    input: rec.input as I,
    checkpoint: rec.checkpoint as C,
    signals,
    wake: getSignal(rec.signals, WAKE_KEY) ?? null,
    abandonOnRestart: rec.abandonOnRestart,
    record: rec,
  };
}

export interface SpawnOptions<I, C> {
  input: I;
  /**
   * The conversation the task belongs to. Defaults to the spawning task's
   * conversation inside a handler commit; required elsewhere.
   */
  conversationId?: string;
  /**
   * The owning task, or null for a task owned by its conversation. Defaults to
   * the spawning task inside a handler commit, otherwise null.
   */
  parentTaskId?: string | null;
  /** Starting phase. Defaults to the definition's `initialPhase`. */
  phase?: string;
  /** Starting checkpoint. Defaults to null. */
  checkpoint?: C;
  /** Abort instead of resuming after a restart. */
  abandonOnRestart?: boolean;
  id?: string;
}

/**
 * Task operations inside one commit, consistent with each other and with
 * committed state. Get them from `runtime.in(tx)` or a handler commit's
 * second argument. Ownership of a conversation created with
 * `tx.createConversation` (instead of `ops.createConversation`) is seen only
 * from the next commit.
 */
export interface TaskOps {
  /** Create a task (status `pending`). */
  spawn<I, C>(def: TaskDefinition<I, C>, options: SpawnOptions<I, C>): TaskRecord;
  /** Create a conversation; with `ownerTaskId`, its tasks are owned by that task. */
  createConversation(input: NewConversation): ConversationRecord;
  /** Deliver a signal; wakes the task if it waits on `key`. Throws `SignalRejected`. */
  signal(taskId: string, key: string, payload: JsonValue): void;
  /** Request abort of a task and its owned work. False if it is already finished. */
  abort(taskId: string, reason: string): boolean;
  /** Request abort of every live task the conversation owns. Returns how many. */
  abortConversation(conversationId: string, reason: string): number;
  /** The task as of this commit, or null. */
  task(id: string): TaskRecord | null;
}

/** The second argument of a phase handler. */
export interface TaskScope<C> {
  /**
   * Commit `fn`'s writes together with the transition it returns, as this
   * task. Rejects (writing nothing) with `TaskNotRunning` if the task was
   * aborted, already transitioned or the runtime stopped, and with
   * `WaitRejected` / `InvalidTransition` for a bad transition. The handler
   * must return the resolved value.
   */
  commit(
    fn: (tx: Transaction, ops: TaskOps) => Transition<C> | Promise<Transition<C>>,
  ): Promise<Committed>;
  /** Commit writes without a transition (progress, intent). Same guard as `commit`. */
  write<T>(fn: (tx: Transaction, ops: TaskOps) => T | Promise<T>): Promise<T>;
  /** Committed state of any task in the session. */
  task(id: string): TaskRecord | null;
  /** The runtime, for reads and helpers. */
  readonly runtime: TaskRuntimeApi;
}

/** The parts of the runtime a handler may use (see `TaskRuntime`). */
export interface TaskRuntimeApi {
  task(id: string): TaskRecord | null;
  tasks(filter?: { conversationId?: string; status?: TaskRecord["status"] }): TaskRecord[];
  readonly registry: TaskRegistry;
}

export type PhaseHandler<I, C> = (
  task: TaskState<I, C>,
  runtime: TaskScope<C>,
  ctx: Context,
) => Promise<Committed>;

/** The scope given to `onAbort`: `commit` stores writes together with the `aborted` outcome. */
export interface AbortScope {
  /** At most once. Without a call the runtime commits the outcome alone. */
  commit(fn: (tx: Transaction, ops: TaskOps) => void | Promise<void>): Promise<void>;
  task(id: string): TaskRecord | null;
  readonly reason: string;
}

export type AbortHandler<I, C> = (
  task: TaskState<I, C>,
  scope: AbortScope,
  ctx: Context,
) => Promise<void> | void;

/** What a task kind shows clients of its tasks in the watch view (see `ConversationView.tasks`). */
export interface TaskViewInfo {
  /** A short human label, e.g. the node name. */
  label?: string;
  /** Which attempt of the work this task is on (1-based). */
  attempt?: number;
}

/**
 * A task kind. `TaskDefinition` without type arguments accepts any definition.
 */
export interface TaskDefinition<I = never, C = never> {
  readonly kind: string;
  readonly phases: Readonly<Record<string, PhaseHandler<I, C>>>;
  readonly initialPhase: string;
  /**
   * Runs once the task's owned work has finished aborting, before the task
   * becomes terminal `aborted`. Runs again if the process dies first. A throw
   * is reported and the task still ends `aborted`.
   */
  readonly onAbort?: AbortHandler<I, C>;
  /**
   * Opt in to the watch view: tasks of this kind appear in `view.tasks`, with
   * these details. Kinds without it stay out of the view.
   */
  readonly view?: (task: TaskState<I, C>) => TaskViewInfo;
}

export interface TaskDefinitionInput<I, C> {
  kind: string;
  phases: Record<string, PhaseHandler<I, C>>;
  /** Defaults to the first phase. */
  initialPhase?: string;
  onAbort?: AbortHandler<I, C>;
  view?: (task: TaskState<I, C>) => TaskViewInfo;
}

export function defineTask<I, C>(input: TaskDefinitionInput<I, C>): TaskDefinition<I, C> {
  const names = Object.keys(input.phases);
  if (!input.kind) throw new TypeError("task kind must be a non-empty string");
  const initialPhase = input.initialPhase ?? names[0];
  if (initialPhase === undefined) throw new TypeError(`task ${input.kind} has no phases`);
  if (!Object.hasOwn(input.phases, initialPhase)) {
    throw new TypeError(`task ${input.kind} has no phase ${initialPhase}`);
  }
  for (const name of names) {
    if (typeof input.phases[name] !== "function") {
      throw new TypeError(`task ${input.kind} phase ${name} is not a function`);
    }
  }
  return Object.freeze({
    kind: input.kind,
    phases: Object.freeze({ ...input.phases }),
    initialPhase,
    ...(input.onAbort ? { onAbort: input.onAbort } : {}),
    ...(input.view ? { view: input.view } : {}),
  });
}

/** Task definitions by kind. */
export class TaskRegistry {
  readonly #defs = new Map<string, TaskDefinition>();

  constructor(defs: Iterable<TaskDefinition> = []) {
    for (const def of defs) this.register(def);
  }

  /** Add a definition. Registering the same object twice is a no-op; another one for a taken kind throws. */
  register<I, C>(def: TaskDefinition<I, C>): this {
    // Erase I/C: the registry is heterogeneous; handlers re-narrow by kind.
    const erased = def as unknown as TaskDefinition;
    const existing = this.#defs.get(def.kind);
    if (existing && existing !== erased) {
      throw new TypeError(`task kind ${def.kind} is already registered`);
    }
    this.#defs.set(def.kind, erased);
    return this;
  }

  get(kind: string): TaskDefinition | undefined {
    return this.#defs.get(kind);
  }

  kinds(): string[] {
    return [...this.#defs.keys()];
  }
}

/** Read access to tasks and conversation ownership, as of some point on the commit line. */
export interface TaskLookup {
  get(id: string): TaskRecord | undefined;
  /** Tasks directly owned by `id`: its children, and the root tasks of conversations it owns. */
  ownedBy(id: string): string[];
  /** The task owning a conversation, or null. */
  conversationOwner(conversationId: string): string | null;
  /** Root (conversation-owned) tasks of a conversation. */
  rootsOf(conversationId: string): string[];
}
