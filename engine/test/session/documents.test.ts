import { describe, expect, it } from "vitest";
import { z } from "zod";
import { defineDoc, InvalidDocument } from "../../src/session/documents.js";
import type { CommitEvent } from "../../src/session/publish.js";
import { Session } from "../../src/session/session.js";
import { MemoryStorageProvider } from "../../src/storage/memory.js";
import { counterDoc, openHarness } from "./support.js";

describe("session documents", () => {
  it("defineDoc rejects an initial value that fails its schema", () => {
    expect(() =>
      defineDoc({
        kind: "bad",
        scope: "session",
        initial: { n: -1 },
        schema: z.object({ n: z.number().min(0) }),
      }),
    ).toThrow(InvalidDocument);
  });

  it("starts from the initial value and snapshots the draft at commit", async () => {
    const { session, conversationId } = await openHarness();
    const seen: CommitEvent[] = [];
    session.subscribeCommits((e) => seen.push(e));

    await session.commit(async (tx) => {
      const draft = await tx.doc(counterDoc, conversationId);
      expect(draft.value).toEqual({ count: 0, notes: [] });
      draft.value.count += 1;
      draft.update((d) => {
        d.notes.push("a");
      });
    });

    expect(await session.document(counterDoc, conversationId)).toEqual({ count: 1, notes: ["a"] });
    const stored = await session.reader.getDocument("counter", conversationId);
    expect(stored?.data).toEqual({ count: 1, notes: ["a"] });
    expect(seen[0]?.documents).toEqual([
      { kind: "counter", scopeId: conversationId, before: null, after: { count: 1, notes: ["a"] } },
    ]);

    await session.commit(async (tx) => {
      (await tx.doc(counterDoc, conversationId)).set({ count: 5, notes: [] });
    });
    expect(seen[1]?.documents[0]?.before).toEqual({ count: 1, notes: ["a"] });
    expect(seen[1]?.documents[0]?.after).toEqual({ count: 5, notes: [] });
  });

  it("returns the same draft for the same key within one commit", async () => {
    const { session, conversationId } = await openHarness();
    await session.commit(async (tx) => {
      const a = await tx.doc(counterDoc, conversationId);
      const b = await tx.doc(counterDoc, conversationId);
      expect(a).toBe(b);
    });
  });

  it("an unchanged draft writes nothing", async () => {
    const { session, conversationId, flaky } = await openHarness();
    const before = flaky.batches.length;
    await session.commit(async (tx) => {
      await tx.doc(counterDoc, conversationId);
    });
    expect(flaky.batches.length).toBe(before);
  });

  it("a draft cannot be used after its callback settles", async () => {
    const { session, conversationId } = await openHarness();
    const draft = await session.commit((tx) => tx.doc(counterDoc, conversationId));
    expect(() => draft.value).toThrow(/settled/);
    expect(() => draft.set({ count: 9, notes: [] })).toThrow(/settled/);
    expect(await session.document(counterDoc, conversationId)).toEqual({ count: 0, notes: [] });
  });

  it("invalid document data rolls the whole commit back", async () => {
    const { session, conversationId, flaky } = await openHarness();
    const before = flaky.batches.length;
    let entryId = "";
    await expect(
      session.commit(async (tx) => {
        entryId = tx.appendEntry({ conversationId, kind: "x", payload: 1 }).id;
        (await tx.doc(counterDoc, conversationId)).value.count = -3;
      }),
    ).rejects.toBeInstanceOf(InvalidDocument);
    expect(flaky.batches.length).toBe(before);
    expect(await session.reader.getEntry(entryId)).toBeNull();
    expect(await session.document(counterDoc, conversationId)).toEqual({ count: 0, notes: [] });
    // The next commit starts from committed state, not the failed draft.
    await session.commit(async (tx) => {
      expect((await tx.doc(counterDoc, conversationId)).value.count).toBe(0);
    });
  });

  it("a callback that throws discards its draft changes", async () => {
    const { session, conversationId } = await openHarness();
    await expect(
      session.commit(async (tx) => {
        (await tx.doc(counterDoc, conversationId)).value.count = 4;
        throw new Error("nope");
      }),
    ).rejects.toThrow("nope");
    expect(await session.document(counterDoc, conversationId)).toEqual({ count: 0, notes: [] });
  });

  it("document() returns a copy that cannot change committed state", async () => {
    const { session, conversationId } = await openHarness();
    const copy = await session.document(counterDoc, conversationId);
    copy.count = 99;
    expect(await session.document(counterDoc, conversationId)).toEqual({ count: 0, notes: [] });
  });

  it("documents survive a reopen", async () => {
    const provider = new MemoryStorageProvider();
    const { session, conversationId } = await openHarness({}, provider);
    await session.commit(async (tx) => {
      (await tx.doc(counterDoc, conversationId)).set({ count: 3, notes: ["x"] });
    });
    await session.close();
    const again = await Session.open(await provider.open("sess-1"));
    expect(await again.document(counterDoc, conversationId)).toEqual({ count: 3, notes: ["x"] });
  });
});
