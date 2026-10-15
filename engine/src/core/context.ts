/**
 * Cancellation context built on AbortSignal. A context has an optional
 * absolute deadline (on its clock) and a cancellation reason. Cancelling a
 * parent cancels every descendant with the parent's reason; cancelling a child
 * never affects its parent.
 */
import { type CancelTimer, type Clock, realClock } from "./clock.js";
import { Cancelled, DeadlineExceeded } from "./errors.js";

export interface ChildOptions {
  /** Relative timeout in ms from the clock's `now()`. */
  timeoutMs?: number;
  /** Absolute deadline in clock epoch ms. */
  deadline?: number;
}

export class Context {
  readonly clock: Clock;
  readonly deadline: number | undefined;
  readonly #controller = new AbortController();
  readonly #parent: Context | undefined;
  readonly #children = new Set<Context>();
  #cancelTimer: CancelTimer | undefined;

  private constructor(clock: Clock, parent: Context | undefined, deadline: number | undefined) {
    this.clock = clock;
    this.#parent = parent;
    this.deadline = deadline;
  }

  /** A root context that is never cancelled unless `cancel` is called on it. */
  static background(clock: Clock = realClock): Context {
    return new Context(clock, undefined, undefined);
  }

  get signal(): AbortSignal {
    return this.#controller.signal;
  }

  get cancelled(): boolean {
    return this.#controller.signal.aborted;
  }

  /** The cancellation reason, or undefined while live. */
  get reason(): unknown {
    return this.cancelled ? this.#controller.signal.reason : undefined;
  }

  child(options: ChildOptions = {}): Context {
    let deadline = this.deadline;
    const own =
      options.deadline ??
      (options.timeoutMs === undefined ? undefined : this.clock.now() + options.timeoutMs);
    if (own !== undefined && (deadline === undefined || own < deadline)) deadline = own;

    const child = new Context(this.clock, this, deadline);
    if (this.cancelled) {
      child.#controller.abort(this.reason);
      return child;
    }
    this.#children.add(child);
    if (deadline !== undefined) {
      const at = deadline;
      child.#cancelTimer = this.clock.setTimer(at - this.clock.now(), () => {
        child.cancel(new DeadlineExceeded(at));
      });
    }
    return child;
  }

  /** Cancel this context and its descendants. Idempotent; the first reason wins. */
  cancel(reason: unknown = new Cancelled()): void {
    if (this.cancelled) return;
    this.#controller.abort(reason);
    this.#cancelTimer?.();
    this.#cancelTimer = undefined;
    if (this.#parent) this.#parent.#children.delete(this);
    const children = [...this.#children];
    this.#children.clear();
    for (const c of children) c.cancel(reason);
  }

  throwIfCancelled(): void {
    if (this.cancelled) throw this.reason;
  }

  /** Live (uncancelled) direct children; exposed for leak checks. */
  childCount(): number {
    return this.#children.size;
  }
}
