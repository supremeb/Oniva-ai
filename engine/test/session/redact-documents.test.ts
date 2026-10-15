import { describe, expect, it } from "vitest";
import type { JsonValue } from "../../src/core/json.js";
import { InvalidDocument } from "../../src/session/documents.js";
import type { CommitEvent } from "../../src/session/publish.js";
import { Session } from "../../src/session/session.js";
import type { RedactTarget } from "../../src/session/transaction.js";
import { counterDoc, openHarness } from "./support.js";

const SECRET = "sk-docsecret1234567890";
const mask = (v: JsonValue): JsonValue =>
  JSON.parse(JSON.stringify(v).replaceAll(SECRET, "[REDACTED]"));

describe("session document redaction (R20)", () => {
  it("redacts changed documents in storage, the commit event and later reads", async () => {
    const targets: RedactTarget[] = [];
    const { session, conversationId, flaky, provider } = await openHarness({
      redact: (v, t) => {
        targets.push(t);
        return mask(v);
      },
    });
    const events: CommitEvent[] = [];
    session.subscribeCommits((e) => events.push(e));

    await session.commit(async (tx) => {
      (await tx.doc(counterDoc, conversationId)).value.notes.push(`key ${SECRET}`);
    });

    expect(targets).toContainEqual({ record: "document", kind: counterDoc.kind });
    expect(JSON.stringify(flaky.batches)).not.toContain(SECRET);
    expect(JSON.stringify(events)).not.toContain(SECRET);
    expect(events.at(-1)?.documents[0]?.after).toEqual({
      count: 0,
      notes: ["key [REDACTED]"],
    });
    expect(await session.document(counterDoc, conversationId)).toEqual({
      count: 0,
      notes: ["key [REDACTED]"],
    });
    await session.close();

    const reopened = await Session.open(await provider.open("sess-1"));
    expect(await reopened.document(counterDoc, conversationId)).toEqual({
      count: 0,
      notes: ["key [REDACTED]"],
    });
    await reopened.close();
  });

  it("a redactor that breaks the document schema rolls the commit back", async () => {
    const { session, conversationId } = await openHarness({
      redact: (v, t) => (t.record === "document" ? { broken: true } : v),
    });
    await expect(
      session.commit(async (tx) => {
        (await tx.doc(counterDoc, conversationId)).value.count = 3;
      }),
    ).rejects.toBeInstanceOf(InvalidDocument);
    expect(session.state).toBe("open");
    expect(await session.document(counterDoc, conversationId)).toEqual({ count: 0, notes: [] });
  });

  it("a write that redacts back to the committed value stores nothing", async () => {
    const { session, conversationId, flaky } = await openHarness({ redact: mask });
    await session.commit(async (tx) => {
      (await tx.doc(counterDoc, conversationId)).value.notes.push(SECRET);
    });
    const before = flaky.batches.length;
    await session.commit(async (tx) => {
      (await tx.doc(counterDoc, conversationId)).set({ count: 0, notes: [SECRET] });
    });
    expect(flaky.batches.length).toBe(before);
  });
});
