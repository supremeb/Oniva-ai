/**
 * A database-backed scenario clock shared by every worker process (KTD10).
 * Test-only: the table is created here, never by the engine's migrations.
 *
 * Time is real time plus an offset stored in `engine_test_clock`. Advancing
 * the offset moves every worker's engine clock forward at once (each polls
 * the row), and pending timers whose deadline the jump passed fire at once.
 * The offset only grows, so `now()` and `monotonic()` never go backwards.
 */
import type { Pool } from "pg";
import type { CancelTimer, Clock } from "../../src/core/clock.js";

export const TEST_CLOCK_TABLE = "engine_test_clock";

interface Timer {
  deadline: number;
  callback: () => void;
  handle: ReturnType<typeof setTimeout> | null;
}

/** Real time plus a monotone offset; pending timers re-arm when it moves. */
export class OffsetClock implements Clock {
  #offset = 0;
  readonly #timers = new Set<Timer>();

  get offsetMs(): number {
    return this.#offset;
  }

  now(): number {
    return Date.now() + this.#offset;
  }

  monotonic(): number {
    return performance.now() + this.#offset;
  }

  setTimer(delayMs: number, callback: () => void): CancelTimer {
    const timer: Timer = { deadline: this.now() + Math.max(0, delayMs), callback, handle: null };
    this.#timers.add(timer);
    this.#arm(timer);
    return () => {
      if (timer.handle) clearTimeout(timer.handle);
      this.#timers.delete(timer);
    };
  }

  sleep(delayMs: number, signal?: AbortSignal): Promise<void> {
    return new Promise<void>((resolve, reject) => {
      if (signal?.aborted) {
        reject(signal.reason);
        return;
      }
      const onAbort = () => {
        cancel();
        reject(signal?.reason);
      };
      const cancel = this.setTimer(delayMs, () => {
        signal?.removeEventListener("abort", onAbort);
        resolve();
      });
      signal?.addEventListener("abort", onAbort, { once: true });
    });
  }

  /** Move to `offsetMs` (ignored unless larger), firing timers the jump made due. */
  setOffset(offsetMs: number): void {
    if (!(offsetMs > this.#offset)) return;
    this.#offset = offsetMs;
    for (const t of this.#timers) {
      if (t.handle) clearTimeout(t.handle);
      this.#arm(t);
    }
  }

  #arm(timer: Timer): void {
    timer.handle = setTimeout(
      () => {
        timer.handle = null;
        if (!this.#timers.delete(timer)) return;
        if (this.now() < timer.deadline) {
          // Real time ran slightly short of the deadline; wait the rest.
          this.#timers.add(timer);
          this.#arm(timer);
          return;
        }
        timer.callback();
      },
      Math.max(0, timer.deadline - this.now()),
    );
  }
}

/** Create the clock table (idempotent, safe to race) and return the current offset. */
export async function ensureTestClockTable(pool: Pool): Promise<number> {
  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    await client.query(
      `SELECT pg_advisory_xact_lock(hashtext('${TEST_CLOCK_TABLE}:' || current_schema()))`,
    );
    await client.query(
      `CREATE TABLE IF NOT EXISTS ${TEST_CLOCK_TABLE} (
         id         integer PRIMARY KEY CHECK (id = 1),
         offset_ms  bigint NOT NULL CHECK (offset_ms >= 0)
       )`,
    );
    await client.query(
      `INSERT INTO ${TEST_CLOCK_TABLE} (id, offset_ms) VALUES (1, 0) ON CONFLICT (id) DO NOTHING`,
    );
    await client.query("COMMIT");
  } catch (err) {
    await client.query("ROLLBACK").catch(() => {});
    throw err;
  } finally {
    client.release();
  }
  return readTestClockOffset(pool);
}

export async function readTestClockOffset(pool: Pool): Promise<number> {
  const { rows } = await pool.query<{ offset_ms: string }>(
    `SELECT offset_ms FROM ${TEST_CLOCK_TABLE} WHERE id = 1`,
  );
  return Number(rows[0]?.offset_ms ?? 0);
}

/** Advance the shared offset by `ms`; returns the new offset. */
export async function advanceTestClock(pool: Pool, ms: number): Promise<number> {
  if (!Number.isSafeInteger(ms) || ms < 0) throw new RangeError("advance must be >= 0 ms");
  const { rows } = await pool.query<{ offset_ms: string }>(
    `UPDATE ${TEST_CLOCK_TABLE} SET offset_ms = offset_ms + $1 WHERE id = 1 RETURNING offset_ms`,
    [ms],
  );
  return Number(rows[0]?.offset_ms ?? 0);
}

/** An {@link OffsetClock} kept in step with the shared row by polling. */
export class SharedTestClock extends OffsetClock {
  readonly #pool: Pool;
  #poll: ReturnType<typeof setInterval> | null = null;

  private constructor(pool: Pool) {
    super();
    this.#pool = pool;
  }

  static async open(pool: Pool, pollMs = 50): Promise<SharedTestClock> {
    const clock = new SharedTestClock(pool);
    clock.setOffset(await ensureTestClockTable(pool));
    clock.#poll = setInterval(() => {
      readTestClockOffset(pool).then(
        (offset) => clock.setOffset(offset),
        () => {},
      );
    }, pollMs);
    clock.#poll.unref();
    return clock;
  }

  async advance(ms: number): Promise<number> {
    const offset = await advanceTestClock(this.#pool, ms);
    this.setOffset(offset);
    return offset;
  }

  close(): void {
    if (this.#poll) clearInterval(this.#poll);
    this.#poll = null;
  }
}
