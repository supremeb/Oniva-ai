/**
 * Injected clock for engine logic (scheduler deadlines, approval expiry,
 * progress throttling). Lease timing uses the database clock instead.
 */

export type CancelTimer = () => void;

export interface Clock {
  /** Wall-clock epoch milliseconds. */
  now(): number;
  /** Monotonic milliseconds; only differences are meaningful. */
  monotonic(): number;
  /** Run `callback` once `delayMs` has elapsed. Returns a cancel function. */
  setTimer(delayMs: number, callback: () => void): CancelTimer;
  /** Resolve after `delayMs`; reject with `signal.reason` if aborted first. */
  sleep(delayMs: number, signal?: AbortSignal): Promise<void>;
}

function sleepWith(clock: Clock, delayMs: number, signal?: AbortSignal): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    if (signal?.aborted) {
      reject(signal.reason);
      return;
    }
    const onAbort = (): void => {
      cancel();
      reject(signal?.reason);
    };
    const cancel = clock.setTimer(delayMs, () => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    });
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

export const realClock: Clock = {
  now: () => Date.now(),
  monotonic: () => performance.now(),
  setTimer(delayMs, callback) {
    const handle = setTimeout(callback, Math.max(0, delayMs));
    return () => clearTimeout(handle);
  },
  sleep(delayMs, signal) {
    return sleepWith(realClock, delayMs, signal);
  },
};

interface ManualTimer {
  deadline: number;
  seq: number;
  callback: () => void;
}

/**
 * A clock that only moves when told to. Timers fire in deadline order (ties in
 * scheduling order), with `now()` set to each timer's deadline as it fires.
 */
export class ManualClock implements Clock {
  #now: number;
  readonly #start: number;
  #seq = 0;
  #timers: ManualTimer[] = [];

  constructor(startMs = 0) {
    this.#now = startMs;
    this.#start = startMs;
  }

  now(): number {
    return this.#now;
  }

  monotonic(): number {
    return this.#now - this.#start;
  }

  setTimer(delayMs: number, callback: () => void): CancelTimer {
    const timer: ManualTimer = {
      deadline: this.#now + Math.max(0, delayMs),
      seq: this.#seq++,
      callback,
    };
    this.#timers.push(timer);
    return () => {
      const i = this.#timers.indexOf(timer);
      if (i >= 0) this.#timers.splice(i, 1);
    };
  }

  sleep(delayMs: number, signal?: AbortSignal): Promise<void> {
    return sleepWith(this, delayMs, signal);
  }

  pendingTimers(): number {
    return this.#timers.length;
  }

  /** Move time forward by `ms`, firing due timers synchronously. */
  advance(ms: number): void {
    const target = this.#checkedTarget(ms);
    for (let t = this.#takeDue(target); t; t = this.#takeDue(target)) {
      this.#now = t.deadline;
      t.callback();
    }
    this.#now = target;
  }

  /**
   * Like `advance`, but yields to the event loop after each timer so promise
   * chains started by a timer can schedule follow-up timers before time moves on.
   */
  async advanceAsync(ms: number): Promise<void> {
    const target = this.#checkedTarget(ms);
    await yieldToEventLoop();
    for (let t = this.#takeDue(target); t; t = this.#takeDue(target)) {
      this.#now = t.deadline;
      t.callback();
      await yieldToEventLoop();
    }
    this.#now = target;
  }

  #checkedTarget(ms: number): number {
    if (!Number.isFinite(ms) || ms < 0) {
      throw new RangeError(`ManualClock cannot advance by ${ms}`);
    }
    return this.#now + ms;
  }

  #takeDue(target: number): ManualTimer | undefined {
    let best = -1;
    for (let i = 0; i < this.#timers.length; i++) {
      const t = this.#timers[i] as ManualTimer;
      if (t.deadline > target) continue;
      const b = this.#timers[best];
      if (!b || t.deadline < b.deadline || (t.deadline === b.deadline && t.seq < b.seq)) {
        best = i;
      }
    }
    return best < 0 ? undefined : this.#timers.splice(best, 1)[0];
  }
}

function yieldToEventLoop(): Promise<void> {
  return new Promise((resolve) => setImmediate(resolve));
}
