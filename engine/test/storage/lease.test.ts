import assert from "node:assert/strict";
import { describe, expect, it } from "vitest";
import { ManualClock, realClock } from "../../src/core/clock.js";
import { Context } from "../../src/core/context.js";
import { LeaseLost, NotOwner, SessionFailed } from "../../src/core/errors.js";
import { newId, uuidv7 } from "../../src/core/ids.js";
import { Session } from "../../src/session/session.js";
import {
  type AcquireResult,
  LeaseKeeper,
  type LeaseStore,
  PostgresLeases,
} from "../../src/storage/postgres/lease.js";
import { PostgresStorageProvider } from "../../src/storage/postgres/storage.js";
import type { ConversationRecord } from "../../src/storage/types.js";
import { usePostgres } from "../support/pg.js";

const env = usePostgres();

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));
const newSession = () => `sess-${uuidv7()}`;
const conversation = (): ConversationRecord => ({
  id: newId("conversation"),
  tenantId: "t",
  projectId: "p",
  ownerTaskId: null,
  createdAt: 1,
});

describe("postgres lease", () => {
  it("lets exactly one of many racing clients acquire", async () => {
    const sid = newSession();
    const pools = Array.from({ length: 8 }, () => env.newPool({ max: 1 }));
    const results = await Promise.all(
      pools.map((p, i) => new PostgresLeases(p).acquire(sid, `worker-${i}`, 10_000)),
    );
    const winners = results.filter((r) => r.acquired);
    assert.equal(winners.length, 1);
    const winner = winners[0] as Extract<AcquireResult, { acquired: true }>;
    for (const r of results) {
      if (!r.acquired) assert.equal(r.ownerId, winner.lease.ownerId);
    }
    for (const p of pools) await p.end();
  });

  it("keeps the token for a live re-acquire and bumps it on every takeover", async () => {
    const sid = newSession();
    const leases = new PostgresLeases(env.pool);
    const a = await leases.acquire(sid, "a", 10_000);
    assert.ok(a.acquired);
    const again = await leases.acquire(sid, "a", 10_000);
    assert.ok(again.acquired);
    assert.equal(again.lease.token, a.lease.token);
    const blocked = await leases.acquire(sid, "b", 10_000);
    assert.equal(blocked.acquired, false);

    assert.equal(await leases.release(sid, "a", a.lease.token), true);
    const b = await leases.acquire(sid, "b", 10_000);
    assert.ok(b.acquired);
    assert.ok(b.lease.token > a.lease.token);
    // A released owner coming back after a takeover gets yet another token.
    assert.equal(await leases.release(sid, "b", b.lease.token), true);
    const a2 = await leases.acquire(sid, "a", 10_000);
    assert.ok(a2.acquired);
    assert.ok(a2.lease.token > b.lease.token);
    // Renewing or releasing with a stale token does nothing.
    assert.equal(await leases.renew(sid, "a", a.lease.token, 10_000), null);
    assert.equal(await leases.release(sid, "b", b.lease.token), false);
    assert.equal((await leases.current(sid))?.token, a2.lease.token);
  });

  it("after heartbeats stop past the TTL, a second client takes over and the stale commit writes nothing", async () => {
    const sid = newSession();
    const leasesA = new PostgresLeases(env.newPool({ max: 2 }));
    const leasesB = new PostgresLeases(env.newPool({ max: 2 }));
    const a = await leasesA.acquire(sid, "a", 300);
    assert.ok(a.acquired);
    assert.equal((await leasesB.acquire(sid, "b", 300)).acquired, false);

    await sleep(450);
    const b = await leasesB.acquire(sid, "b", 10_000);
    assert.ok(b.acquired);
    assert.ok(b.lease.token > a.lease.token);

    const storage = await new PostgresStorageProvider(env.pool).open(sid);
    const c = conversation();
    const err = await storage.commit({ createConversations: [c] }, { token: a.lease.token }).then(
      () => null,
      (e: unknown) => e,
    );
    assert.ok(err instanceof LeaseLost);
    assert.equal(err.token, a.lease.token);
    assert.equal(await storage.lastSeq(), 0);
    assert.equal(await storage.getConversation(c.id), null);
    assert.equal(await leasesA.renew(sid, "a", a.lease.token, 300), null);
    assert.deepEqual(await storage.commit({ createConversations: [c] }, { token: b.lease.token }), {
      seq: 1,
    });
  });

  it("refuses a commit once the holder's lease has expired, even without a takeover", async () => {
    const sid = newSession();
    const leases = new PostgresLeases(env.pool);
    const a = await leases.acquire(sid, "a", 200);
    assert.ok(a.acquired);
    await sleep(300);
    const storage = await new PostgresStorageProvider(env.pool).open(sid);
    await expect(
      storage.commit({ createConversations: [conversation()] }, { token: a.lease.token }),
    ).rejects.toBeInstanceOf(LeaseLost);
    assert.equal(await storage.lastSeq(), 0);
  });
});

describe("lease keeper on postgres", () => {
  it("throws NotOwner when another worker holds the lease", async () => {
    const sid = newSession();
    const store = new PostgresLeases(env.pool);
    const a = await LeaseKeeper.acquire({ store, sessionId: sid, ownerId: "a", ttlMs: 5_000 });
    const err = await LeaseKeeper.acquire({
      store,
      sessionId: sid,
      ownerId: "b",
      ttlMs: 5_000,
    }).then(
      () => null,
      (e: unknown) => e,
    );
    assert.ok(err instanceof NotOwner);
    assert.equal(err.ownerId, "a");
    await a.release();
  });

  it("keeps the lease across several TTLs by heartbeating", async () => {
    const sid = newSession();
    const ttlMs = 400;
    const keeper = await LeaseKeeper.acquire({
      store: new PostgresLeases(env.newPool({ max: 2 })),
      sessionId: sid,
      ownerId: "a",
      ttlMs,
      safetyMarginMs: 100,
    });
    const rival = new PostgresLeases(env.newPool({ max: 2 }));
    const started = performance.now();
    while (performance.now() - started < ttlMs * 4) {
      assert.equal((await rival.acquire(sid, "b", ttlMs)).acquired, false);
      await sleep(50);
    }
    assert.equal(keeper.lost, false);
    assert.ok(keeper.renewals >= 9, `expected at least 9 renewals, got ${keeper.renewals}`);
    const storage = await new PostgresStorageProvider(env.pool).open(sid);
    assert.deepEqual(
      await storage.commit({ createConversations: [conversation()] }, keeper.fence()),
      {
        seq: 1,
      },
    );

    await keeper.release();
    const b = await rival.acquire(sid, "b", ttlMs);
    assert.ok(b.acquired);
    assert.ok(b.lease.token > keeper.token);
    assert.throws(() => keeper.fence(), LeaseLost);
  });

  it("self-fences a blocked heartbeat, aborting in-flight work before a second worker can acquire", async () => {
    const sid = newSession();
    const real = new PostgresLeases(env.newPool({ max: 2 }));
    let blocked = false;
    let unblock!: () => void;
    const gate = new Promise<void>((r) => {
      unblock = r;
    });
    // A store whose heartbeats hang once `blocked` is set (a stalled event
    // loop or a partition between the worker and the database).
    const store: LeaseStore = {
      acquire: (...args) => real.acquire(...args),
      release: (...args) => real.release(...args),
      renew: async (...args) => {
        if (blocked) await gate;
        return real.renew(...args);
      },
    };
    const root = Context.background();
    const lostEvents: LeaseLost[] = [];
    const ttlMs = 600;
    const keeper = await LeaseKeeper.acquire({
      store,
      sessionId: sid,
      ownerId: "a",
      ttlMs,
      safetyMarginMs: 150,
      onLost: (e) => {
        lostEvents.push(e);
        root.cancel(e);
      },
    });
    const storage = await new PostgresStorageProvider(env.pool).open(sid);
    const session = await Session.open(storage, { fence: keeper.fence });
    keeper.signal.addEventListener("abort", () => session.fail(keeper.signal.reason));
    await session.commit((tx) => {
      tx.createConversation({ tenantId: "t", projectId: "p" });
    });

    // An in-flight "tool call" tied to a task context.
    const toolCtx = root.child();
    const tool = realClock.sleep(60_000, toolCtx.signal).then(
      () => ({ abortedAt: Number.NaN, reason: null as unknown }),
      (reason: unknown) => ({ abortedAt: performance.now(), reason }),
    );

    blocked = true;
    const rival = new PostgresLeases(env.newPool({ max: 2 }));
    let acquiredAt = Number.NaN;
    let rivalToken = 0;
    for (let i = 0; i < 400; i++) {
      const r = await rival.acquire(sid, "b", 10_000);
      if (r.acquired) {
        acquiredAt = performance.now();
        rivalToken = r.lease.token;
        break;
      }
      await sleep(10);
    }
    assert.ok(Number.isFinite(acquiredAt), "the rival eventually acquires");
    const { abortedAt, reason } = await tool;
    assert.ok(reason instanceof LeaseLost);
    assert.ok(
      abortedAt < acquiredAt,
      `tool aborted at ${abortedAt.toFixed(1)}, rival acquired at ${acquiredAt.toFixed(1)}`,
    );
    assert.ok(rivalToken > keeper.token);
    assert.equal(lostEvents.length, 1);
    assert.equal(keeper.lost, true);
    assert.ok(keeper.signal.aborted);
    assert.throws(() => keeper.fence(), LeaseLost);

    // The fenced session refuses to commit; the stale token is refused by storage too.
    assert.equal(session.state, "failed");
    await expect(session.commit(() => {})).rejects.toBeInstanceOf(SessionFailed);
    const stale = await new PostgresStorageProvider(env.pool).open(sid);
    await expect(
      stale.commit({ createConversations: [conversation()] }, { token: keeper.token }),
    ).rejects.toBeInstanceOf(LeaseLost);
    assert.equal(await stale.lastSeq(), 1);

    // The stalled heartbeat finally lands, finds a newer token, and changes nothing.
    unblock();
    await sleep(50);
    assert.equal((await rival.current(sid))?.token, rivalToken);
    assert.equal(lostEvents.length, 1);
  });
});

/** A scriptable in-memory lease store for deterministic keeper timing tests. */
class StubStore implements LeaseStore {
  renewCalls = 0;
  mode: "ok" | "hang" | "lost" | "error" = "ok";
  released = 0;

  async acquire(sessionId: string, ownerId: string): Promise<AcquireResult> {
    return {
      acquired: true,
      lease: { sessionId, ownerId, token: 7, expiresAt: new Date() },
    };
  }
  renew(): Promise<Date | null> {
    this.renewCalls++;
    switch (this.mode) {
      case "ok":
        return Promise.resolve(new Date());
      case "lost":
        return Promise.resolve(null);
      case "error":
        return Promise.reject(new Error("connection reset"));
      case "hang":
        return new Promise(() => {});
    }
  }
  async release(): Promise<boolean> {
    this.released++;
    return true;
  }
}

describe("lease keeper timing (manual clock)", () => {
  const start = async (store: StubStore, clock: ManualClock, onLost?: (e: LeaseLost) => void) =>
    LeaseKeeper.acquire({
      store,
      sessionId: "s1",
      ownerId: "w1",
      ttlMs: 3_000,
      safetyMarginMs: 500,
      clock,
      ...(onLost ? { onLost } : {}),
    });

  it("renews every ttl/3 and never fences while renewals succeed", async () => {
    const clock = new ManualClock(1_000);
    const store = new StubStore();
    const keeper = await start(store, clock);
    await clock.advanceAsync(30_000);
    assert.equal(keeper.lost, false);
    assert.equal(store.renewCalls, 30);
    assert.deepEqual(keeper.fence(), { token: 7 });
    await keeper.release();
    assert.equal(store.released, 1);
    assert.equal(clock.pendingTimers(), 0);
  });

  it("fences exactly at lastRenewal + ttl - safetyMargin when heartbeats hang", async () => {
    const clock = new ManualClock();
    const store = new StubStore();
    const lost: LeaseLost[] = [];
    const keeper = await start(store, clock, (e) => lost.push(e));
    await clock.advanceAsync(1_000); // renewal at t=1000 succeeds
    store.mode = "hang";
    await clock.advanceAsync(2_499); // t=3499: deadline is 1000 + 3000 - 500 = 3500
    assert.equal(keeper.lost, false);
    await clock.advanceAsync(1);
    assert.equal(keeper.lost, true);
    assert.equal(lost.length, 1);
    assert.ok(keeper.signal.reason instanceof LeaseLost);
    assert.throws(() => keeper.fence(), LeaseLost);
    await clock.advanceAsync(10_000);
    assert.equal(lost.length, 1);
    await keeper.release();
    assert.equal(store.released, 0, "a fenced keeper does not touch the lease");
  });

  it("keeps trying through transient renew errors until the deadline", async () => {
    const clock = new ManualClock();
    const store = new StubStore();
    const keeper = await start(store, clock);
    store.mode = "error";
    await clock.advanceAsync(1_000); // t=1000: renewal fails
    assert.equal(keeper.lost, false);
    store.mode = "ok";
    await clock.advanceAsync(3_499); // renewals at 2000, 3000, 4000 succeed
    assert.equal(keeper.lost, false);
    assert.equal(store.renewCalls, 4);
    store.mode = "error";
    await clock.advanceAsync(2_000); // t=6499: deadline is 4000 + 3000 - 500 = 6500
    assert.equal(keeper.lost, false);
    await clock.advanceAsync(1);
    assert.equal(keeper.lost, true);
  });

  it("fenceNow self-fences at once, reports the loss once and gives the lease back", async () => {
    const clock = new ManualClock();
    const store = new StubStore();
    const lost: LeaseLost[] = [];
    const keeper = await start(store, clock, (e) => lost.push(e));
    keeper.fenceNow("gateway rejected the fence");
    assert.equal(keeper.lost, true);
    assert.equal(keeper.state, "lost");
    assert.ok(keeper.signal.aborted);
    assert.ok(keeper.signal.reason instanceof LeaseLost);
    assert.match((keeper.signal.reason as LeaseLost).message, /gateway rejected the fence/);
    assert.equal(lost.length, 1);
    assert.throws(() => keeper.fence(), LeaseLost);
    assert.equal(clock.pendingTimers(), 0);
    await clock.advanceAsync(0);
    assert.equal(store.released, 1, "the lease is given back best-effort");
    keeper.fenceNow();
    assert.equal(lost.length, 1);
    await keeper.release();
    assert.equal(store.released, 1);
  });

  it("fenceNow after a release, or with a failing store, changes nothing and never throws", async () => {
    const clock = new ManualClock();
    const store = new StubStore();
    const lost: LeaseLost[] = [];
    const keeper = await start(store, clock, (e) => lost.push(e));
    await keeper.release();
    keeper.fenceNow();
    assert.equal(keeper.state, "released");
    assert.equal(lost.length, 0);

    const failing = new StubStore();
    failing.release = () => Promise.reject(new Error("down"));
    const k2 = await start(failing, clock, () => {
      throw new Error("callback throws");
    });
    k2.fenceNow();
    await clock.advanceAsync(0);
    assert.equal(k2.lost, true);
  });

  it("fences immediately when a renewal reports the lease gone", async () => {
    const clock = new ManualClock();
    const store = new StubStore();
    const keeper = await start(store, clock);
    store.mode = "lost";
    await clock.advanceAsync(1_000);
    assert.equal(keeper.lost, true);
    assert.equal(clock.pendingTimers(), 0);
  });
});
