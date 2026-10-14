import { describe, expect, it } from "vitest";
import { SessionFailed, StorageRequestError } from "../../src/core/errors.js";
import type { CommitEvent } from "../../src/session/publish.js";
import { Session } from "../../src/session/session.js";
import { MemoryStorageProvider } from "../../src/storage/memory.js";
import { deferred, newTask, openHarness } from "./support.js";

describe("session commit line", () => {
  it("returns the callback's value and stamps ids and times", async () => {
    const { session, conversationId, clock } = await openHarness();
    const entry = await session.commit((tx) =>
      tx.appendEntry({ conversationId, kind: "user", payload: { text: "hi" } }),
    );
    expect(entry.id).toMatch(/^ent_/);
    expect(entry.createdAt).toBe(clock.now());
    expect(entry.taskId).toBeNull();
    expect(await session.reader.getEntry(entry.id)).toMatchObject({ payload: { text: "hi" } });
    expect(session.lastSeq).toBe(2);
  });

  it("runs two concurrent commits one after the other; the second sees the first", async () => {
    const { session, conversationId } = await openHarness();
    const gate = deferred();
    const order: string[] = [];
    let taskId = "";

    const first = session.commit(async (tx) => {
      order.push("first:start");
      taskId = newTask(tx, conversationId).id;
      await gate.promise;
      order.push("first:end");
    });
    const second = session.commit(async (tx) => {
      order.push("second:start");
      return tx.getTask(taskId);
    });

    await new Promise((r) => setImmediate(r));
    expect(order).toEqual(["first:start"]);
    gate.resolve();
    await first;
    const seen = await second;
    expect(order).toEqual(["first:start", "first:end", "second:start"]);
    expect(seen?.id).toBe(taskId);
  });

  it("rolls back the whole commit when the callback throws", async () => {
    const { session, conversationId, flaky } = await openHarness();
    const before = flaky.batches.length;
    let entryId = "";
    await expect(
      session.commit((tx) => {
        entryId = tx.appendEntry({ conversationId, kind: "user", payload: 1 }).id;
        newTask(tx, conversationId);
        throw new Error("boom");
      }),
    ).rejects.toThrow("boom");
    expect(flaky.batches.length).toBe(before);
    expect(await session.reader.getEntry(entryId)).toBeNull();
    expect(session.state).toBe("open");
    // The line keeps working.
    await session.commit((tx) => tx.appendEntry({ conversationId, kind: "user", payload: 2 }));
  });

  it("skips the storage commit when nothing was written", async () => {
    const { session, flaky } = await openHarness();
    const seen: CommitEvent[] = [];
    session.subscribeCommits((e) => seen.push(e));
    const before = flaky.batches.length;
    expect(await session.commit(() => 42)).toBe(42);
    expect(flaky.batches.length).toBe(before);
    expect(seen).toEqual([]);
  });

  it("throws on a table read after a table write", async () => {
    const { session, conversationId } = await openHarness();
    await expect(
      session.commit(async (tx) => {
        await tx.getConversation(conversationId); // reads before writes are fine
        tx.appendEntry({ conversationId, kind: "user", payload: null });
        await tx.getConversation(conversationId);
      }),
    ).rejects.toThrow(/read after .*write/);
  });

  it("revokes the transaction after the callback settles", async () => {
    const { session, conversationId } = await openHarness();
    let leaked: Parameters<Parameters<typeof session.commit>[0]>[0] | undefined;
    await session.commit((tx) => {
      leaked = tx;
    });
    expect(() => leaked?.appendEntry({ conversationId, kind: "user", payload: null })).toThrow(
      /settled/,
    );
  });

  it("notifies observers only after adoption, with the stored batch and seq", async () => {
    const { session, conversationId } = await openHarness();
    const gate = deferred();
    const seen: CommitEvent[] = [];
    session.subscribeCommits((e) => {
      seen.push(e);
    });

    const pending = session.commit(async (tx) => {
      tx.appendEntry({ conversationId, kind: "user", payload: "uncommitted" });
      await gate.promise;
    });
    await new Promise((r) => setImmediate(r));
    expect(seen).toEqual([]);
    gate.resolve();
    await pending;

    expect(seen).toHaveLength(1);
    const event = seen[0];
    expect(event?.seq).toBe(session.lastSeq);
    expect(event?.sessionId).toBe("sess-1");
    expect(event?.batch.appendEntries?.[0]?.payload).toBe("uncommitted");

    // A rolled-back commit is never observed.
    await expect(
      session.commit((tx) => {
        tx.appendEntry({ conversationId, kind: "user", payload: "nope" });
        throw new Error("rollback");
      }),
    ).rejects.toThrow();
    expect(seen).toHaveLength(1);
  });

  it("isolates observer errors and supports unsubscribe", async () => {
    const { session, conversationId } = await openHarness();
    const calls: number[] = [];
    session.subscribeCommits(() => {
      throw new Error("observer bug");
    });
    const off = session.subscribeCommits((e) => calls.push(e.seq));
    await session.commit((tx) => tx.appendEntry({ conversationId, kind: "x", payload: 1 }));
    off();
    await session.commit((tx) => tx.appendEntry({ conversationId, kind: "x", payload: 2 }));
    expect(calls).toHaveLength(1);
    expect(session.state).toBe("open");
  });

  it("passes the fence from the provider to storage", async () => {
    let token = 7;
    const { session, conversationId, flaky } = await openHarness({ fence: () => ({ token }) });
    token = 8;
    await session.commit((tx) => tx.appendEntry({ conversationId, kind: "x", payload: 1 }));
    expect(flaky.fences.at(-1)).toEqual({ token: 8 });
  });

  it("a request error from storage fails only that call", async () => {
    const { session, conversationId } = await openHarness();
    await expect(
      session.commit((tx) => {
        tx.appendEntry({ conversationId: "conv_missing", kind: "x", payload: 1 });
      }),
    ).rejects.toBeInstanceOf(StorageRequestError);
    expect(session.state).toBe("open");
    await session.commit((tx) => tx.appendEntry({ conversationId, kind: "x", payload: 1 }));
  });

  it("a fatal storage error fails the session", async () => {
    const { session, conversationId, flaky } = await openHarness();
    const boom = new Error("disk on fire");
    flaky.failNextCommit = boom;
    const gate = deferred();

    const failing = session.commit(async (tx) => {
      tx.appendEntry({ conversationId, kind: "x", payload: 1 });
      await gate.promise;
    });
    const queued = session.commit((tx) =>
      tx.appendEntry({ conversationId, kind: "x", payload: 2 }),
    );
    gate.resolve();

    await expect(failing).rejects.toBe(boom);
    await expect(queued).rejects.toBeInstanceOf(SessionFailed);
    await expect(session.commit(() => 1)).rejects.toBeInstanceOf(SessionFailed);
    await expect(session.reader.getConversation(conversationId)).rejects.toBeInstanceOf(
      SessionFailed,
    );
    expect(session.state).toBe("failed");
    expect(session.signal.aborted).toBe(true);
    await expect(session.closed).resolves.toEqual({ reason: "failed", error: boom });
  });

  it("a throwing fence provider is fatal", async () => {
    const lost = new Error("lease lost");
    let leaseHeld = true;
    const { session, conversationId } = await openHarness({
      fence: () => {
        if (!leaseHeld) throw lost;
        return { token: 1 };
      },
    });
    leaseHeld = false;
    await expect(
      session.commit((tx) => tx.appendEntry({ conversationId, kind: "x", payload: 1 })),
    ).rejects.toBe(lost);
    expect(session.state).toBe("failed");
  });

  it("fail() fails the session from outside", async () => {
    const { session } = await openHarness();
    const why = new Error("self-fenced");
    session.fail(why);
    await expect(session.closed).resolves.toEqual({ reason: "failed", error: why });
    await expect(session.commit(() => 1)).rejects.toBeInstanceOf(SessionFailed);
  });

  it("close() drains the line, then rejects later commits", async () => {
    const { session, conversationId } = await openHarness();
    const gate = deferred();
    const inFlight = session.commit(async (tx) => {
      tx.appendEntry({ conversationId, kind: "x", payload: 1 });
      await gate.promise;
      return "done";
    });
    const closing = session.close();
    await expect(session.commit(() => 1)).rejects.toBeInstanceOf(SessionFailed);
    gate.resolve();
    await expect(inFlight).resolves.toBe("done");
    await closing;
    expect(session.state).toBe("closed");
    await expect(session.closed).resolves.toEqual({ reason: "closed" });
    await session.close(); // idempotent
  });

  it("reopening reads back committed state and continues the seq", async () => {
    const provider = new MemoryStorageProvider();
    const { session, conversationId } = await openHarness({}, provider);
    await session.commit((tx) => tx.appendEntry({ conversationId, kind: "x", payload: 1 }));
    const seq = session.lastSeq;
    await session.close();

    const again = await Session.open(await provider.open("sess-1"));
    expect(again.lastSeq).toBe(seq);
    const page = await again.reader.listEntries(conversationId);
    expect(page.items).toHaveLength(1);
    await again.commit((tx) => tx.appendEntry({ conversationId, kind: "x", payload: 2 }));
    expect(again.lastSeq).toBe(seq + 1);
  });
});
