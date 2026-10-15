/**
 * Session leases with fencing tokens (R16, KTD3, KTD4).
 *
 * A lease row `engine_session_leases(session_id, owner_id, token, expires_at)`
 * says which worker owns a session. Acquire, renew, release and expiry are all
 * computed in SQL against the database clock, so clock skew between workers
 * cannot split ownership. Timing uses `clock_timestamp()` rather than the
 * transaction-start `now()` so a statement that waited on a row lock compares
 * against the time it actually runs.
 *
 * Rows are never deleted: release only expires the lease, and every change of
 * hands (takeover, or re-acquiring after expiry or release) bumps `token`, so a
 * token is never reused. Storage commits lock the row and compare the token
 * (see `storage.ts`), which rejects a stale owner's writes.
 *
 * {@link LeaseKeeper} holds a lease for a worker: it heartbeats every `ttl/3`
 * and self-fences — aborting its signal and reporting `LeaseLost` — once the
 * monotonic clock passes `lastRenewal + ttl − safetyMargin`, which is before
 * the database would let another worker take over. A host that learns of the
 * loss first (a fence rejection from the tool gateway) calls
 * {@link LeaseKeeper.fenceNow}.
 */
import { hostname } from "node:os";
import type { Pool } from "pg";
import { type CancelTimer, type Clock, realClock } from "../../core/clock.js";
import { Cancelled, LeaseLost, NotOwner } from "../../core/errors.js";
import { uuidv7 } from "../../core/ids.js";
import type { Fence } from "../types.js";

export interface LeaseGrant {
  sessionId: string;
  ownerId: string;
  /** Fencing token; send it with every commit. */
  token: number;
  /** Database time at which the lease lapses unless renewed. */
  expiresAt: Date;
}

export type AcquireResult =
  | { acquired: true; lease: LeaseGrant }
  /** Someone else holds a live lease (`ownerId`), or the row vanished (null). */
  | { acquired: false; ownerId: string | null; expiresAt: Date | null };

export interface LeaseInfo {
  sessionId: string;
  ownerId: string;
  token: number;
  expiresAt: Date;
  /** Whether the lease is unexpired by the database clock. */
  live: boolean;
}

/** The lease operations a {@link LeaseKeeper} needs. */
export interface LeaseStore {
  /** Take the lease if it is free, expired, or already ours. */
  acquire(sessionId: string, ownerId: string, ttlMs: number): Promise<AcquireResult>;
  /** Extend a live lease we hold with `token`. Null when it is no longer ours. */
  renew(sessionId: string, ownerId: string, token: number, ttlMs: number): Promise<Date | null>;
  /** Expire a live lease we hold with `token`. False when it was not ours. */
  release(sessionId: string, ownerId: string, token: number): Promise<boolean>;
}

function checkTtl(ttlMs: number): void {
  if (!Number.isSafeInteger(ttlMs) || ttlMs <= 0) {
    throw new RangeError(`lease ttl must be a positive integer of ms, got ${ttlMs}`);
  }
}

const EXPIRY = "clock_timestamp() + make_interval(secs => $3::double precision / 1000)";

const ACQUIRE_SQL = `
INSERT INTO engine_session_leases AS l
  (session_id, owner_id, token, expires_at, acquired_at, renewed_at)
VALUES ($1, $2, 1, ${EXPIRY}, clock_timestamp(), clock_timestamp())
ON CONFLICT (session_id) DO UPDATE SET
  token = CASE WHEN l.owner_id = EXCLUDED.owner_id AND l.expires_at > clock_timestamp()
               THEN l.token ELSE l.token + 1 END,
  acquired_at = CASE WHEN l.owner_id = EXCLUDED.owner_id AND l.expires_at > clock_timestamp()
                     THEN l.acquired_at ELSE EXCLUDED.acquired_at END,
  owner_id = EXCLUDED.owner_id,
  expires_at = EXCLUDED.expires_at,
  renewed_at = EXCLUDED.renewed_at
WHERE l.expires_at <= clock_timestamp() OR l.owner_id = EXCLUDED.owner_id
RETURNING token, expires_at`;

const RENEW_SQL = `
UPDATE engine_session_leases
   SET expires_at = ${EXPIRY}, renewed_at = clock_timestamp()
 WHERE session_id = $1 AND owner_id = $2 AND token = $4 AND expires_at > clock_timestamp()
RETURNING expires_at`;

const RELEASE_SQL = `
UPDATE engine_session_leases
   SET expires_at = clock_timestamp()
 WHERE session_id = $1 AND owner_id = $2 AND token = $3 AND expires_at > clock_timestamp()
RETURNING token`;

/** Lease operations against `engine_session_leases`. */
export class PostgresLeases implements LeaseStore {
  constructor(readonly pool: Pool) {}

  async acquire(sessionId: string, ownerId: string, ttlMs: number): Promise<AcquireResult> {
    checkTtl(ttlMs);
    const { rows } = await this.pool.query<{ token: string; expires_at: Date }>(ACQUIRE_SQL, [
      sessionId,
      ownerId,
      ttlMs,
    ]);
    const row = rows[0];
    if (row) {
      return {
        acquired: true,
        lease: { sessionId, ownerId, token: Number(row.token), expiresAt: row.expires_at },
      };
    }
    const current = await this.current(sessionId);
    return {
      acquired: false,
      ownerId: current?.ownerId ?? null,
      expiresAt: current?.expiresAt ?? null,
    };
  }

  async renew(
    sessionId: string,
    ownerId: string,
    token: number,
    ttlMs: number,
  ): Promise<Date | null> {
    checkTtl(ttlMs);
    const { rows } = await this.pool.query<{ expires_at: Date }>(RENEW_SQL, [
      sessionId,
      ownerId,
      ttlMs,
      token,
    ]);
    return rows[0]?.expires_at ?? null;
  }

  async release(sessionId: string, ownerId: string, token: number): Promise<boolean> {
    const { rowCount } = await this.pool.query(RELEASE_SQL, [sessionId, ownerId, token]);
    return (rowCount ?? 0) > 0;
  }

  /** The session's lease row, or null if it was never leased. */
  async current(sessionId: string): Promise<LeaseInfo | null> {
    const { rows } = await this.pool.query<{
      owner_id: string;
      token: string;
      expires_at: Date;
      live: boolean;
    }>(
      `SELECT owner_id, token, expires_at, expires_at > clock_timestamp() AS live
         FROM engine_session_leases WHERE session_id = $1`,
      [sessionId],
    );
    const row = rows[0];
    if (!row) return null;
    return {
      sessionId,
      ownerId: row.owner_id,
      token: Number(row.token),
      expiresAt: row.expires_at,
      live: row.live,
    };
  }
}

/** A worker-unique owner id: host, pid and a random suffix. */
export function defaultOwnerId(): string {
  return `${hostname()}:${process.pid}:${uuidv7()}`;
}

export interface LeaseKeeperOptions {
  store: LeaseStore;
  sessionId: string;
  /** Unique per worker incarnation. Defaults to {@link defaultOwnerId}. */
  ownerId?: string;
  /** Lease time-to-live in ms. Heartbeats run every `ttlMs / 3`. */
  ttlMs: number;
  /**
   * How long before the lease could lapse the keeper fences itself.
   * Must be below `2/3 · ttlMs`. Defaults to `ttlMs / 5`.
   */
  safetyMarginMs?: number;
  /** Timer and monotonic source. Defaults to the real clock. */
  clock?: Clock;
  /** Called once when the keeper self-fences (not on a voluntary release). */
  onLost?: (error: LeaseLost) => void;
}

export type LeaseKeeperState = "held" | "lost" | "released";

/** Holds one session lease for a worker; see the module docs. */
export class LeaseKeeper {
  readonly sessionId: string;
  readonly ownerId: string;
  readonly token: number;
  readonly ttlMs: number;
  readonly safetyMarginMs: number;

  readonly #store: LeaseStore;
  readonly #clock: Clock;
  readonly #onLost: ((error: LeaseLost) => void) | undefined;
  readonly #abort = new AbortController();
  #state: LeaseKeeperState = "held";
  #lastRenewal: number;
  #renewals = 0;
  #cancelHeartbeat: CancelTimer | undefined;
  #cancelWatchdog: CancelTimer | undefined;

  /** Acquire the lease and start heartbeating. Throws `NotOwner` if another worker holds it. */
  static async acquire(options: LeaseKeeperOptions): Promise<LeaseKeeper> {
    const ttlMs = options.ttlMs;
    checkTtl(ttlMs);
    const margin = options.safetyMarginMs ?? Math.floor(ttlMs / 5);
    if (!Number.isFinite(margin) || margin < 0 || margin >= (ttlMs * 2) / 3) {
      throw new RangeError(`safety margin must be in [0, 2/3 · ttl), got ${margin}`);
    }
    const clock = options.clock ?? realClock;
    const ownerId = options.ownerId ?? defaultOwnerId();
    const sentAt = clock.monotonic();
    const result = await options.store.acquire(options.sessionId, ownerId, ttlMs);
    if (!result.acquired) {
      throw new NotOwner(options.sessionId, result.ownerId ?? undefined);
    }
    return new LeaseKeeper(options, ownerId, result.lease.token, margin, clock, sentAt);
  }

  private constructor(
    options: LeaseKeeperOptions,
    ownerId: string,
    token: number,
    margin: number,
    clock: Clock,
    acquiredAt: number,
  ) {
    this.sessionId = options.sessionId;
    this.ownerId = ownerId;
    this.token = token;
    this.ttlMs = options.ttlMs;
    this.safetyMarginMs = margin;
    this.#store = options.store;
    this.#clock = clock;
    this.#onLost = options.onLost;
    this.#lastRenewal = acquiredAt;
    this.#armWatchdog();
    this.#scheduleHeartbeat();
  }

  get state(): LeaseKeeperState {
    return this.#state;
  }

  /** True once the keeper has self-fenced. */
  get lost(): boolean {
    return this.#state === "lost";
  }

  /** Successful renewals so far. */
  get renewals(): number {
    return this.#renewals;
  }

  /**
   * Aborts when the lease is lost (reason: `LeaseLost`) or released (reason:
   * `Cancelled`). Tie in-flight work to it.
   */
  get signal(): AbortSignal {
    return this.#abort.signal;
  }

  /** The commit fence. Throws `LeaseLost` once the lease is no longer held. */
  readonly fence = (): Fence => {
    if (this.#state !== "held") throw new LeaseLost(this.sessionId, this.token);
    return { token: this.token };
  };

  /** Stop heartbeating and expire the lease so another worker can take over at once. */
  async release(): Promise<void> {
    if (this.#state !== "held") return;
    this.#state = "released";
    this.#stopTimers();
    this.#abort.abort(new Cancelled(`lease on session ${this.sessionId} released`));
    await this.#store.release(this.sessionId, this.ownerId, this.token);
  }

  /**
   * Self-fence now, without waiting for the watchdog: for a host that learns
   * from elsewhere that its lease is gone (e.g. the tool gateway rejected the
   * fence token). Aborts {@link signal} with `LeaseLost`, calls `onLost` once
   * and gives the lease back best-effort so a successor need not wait for it
   * to lapse. A no-op once the lease is lost or released; never throws.
   */
  fenceNow(reason?: string): void {
    if (this.#state !== "held") return;
    this.#fenceOut(reason);
    try {
      this.#store.release(this.sessionId, this.ownerId, this.token).catch(() => {});
    } catch {
      // Best-effort: the lease lapses on its own.
    }
  }

  #scheduleHeartbeat(): void {
    if (this.#state !== "held") return;
    this.#cancelHeartbeat = this.#clock.setTimer(this.ttlMs / 3, () => {
      this.#cancelHeartbeat = undefined;
      void this.#beat();
    });
  }

  async #beat(): Promise<void> {
    if (this.#state !== "held") return;
    // Measure from before the request: the database's new expiry is at least
    // `sentAt + ttl`, so the deadline derived from it is conservative.
    const sentAt = this.#clock.monotonic();
    let expires: Date | null | undefined;
    try {
      expires = await this.#store.renew(this.sessionId, this.ownerId, this.token, this.ttlMs);
    } catch {
      expires = undefined; // transient; retry on the next beat, the watchdog bounds it
    }
    if (this.#state !== "held") {
      // A renewal that landed after we fenced or released extends a lease nobody
      // is using; give it back so a successor need not wait for it to lapse.
      if (expires) {
        this.#store.release(this.sessionId, this.ownerId, this.token).catch(() => {});
      }
      return;
    }
    if (expires === null) {
      this.#fenceOut();
      return;
    }
    if (expires !== undefined) {
      this.#renewals++;
      this.#lastRenewal = sentAt;
      this.#armWatchdog();
    }
    this.#scheduleHeartbeat();
  }

  #armWatchdog(): void {
    this.#cancelWatchdog?.();
    this.#cancelWatchdog = undefined;
    const delay = this.#lastRenewal + this.ttlMs - this.safetyMarginMs - this.#clock.monotonic();
    if (delay <= 0) {
      this.#fenceOut();
      return;
    }
    this.#cancelWatchdog = this.#clock.setTimer(delay, () => {
      this.#cancelWatchdog = undefined;
      this.#fenceOut();
    });
  }

  #fenceOut(reason?: string): void {
    if (this.#state !== "held") return;
    this.#state = "lost";
    this.#stopTimers();
    const error = new LeaseLost(this.sessionId, this.token);
    if (reason) error.message = `${error.message}: ${reason}`;
    this.#abort.abort(error);
    try {
      this.#onLost?.(error);
    } catch {
      // The keeper is already fenced; a throwing callback changes nothing.
    }
  }

  #stopTimers(): void {
    this.#cancelHeartbeat?.();
    this.#cancelHeartbeat = undefined;
    this.#cancelWatchdog?.();
    this.#cancelWatchdog = undefined;
  }
}
