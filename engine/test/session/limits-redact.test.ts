import { describe, expect, it } from "vitest";
import { LimitExceeded } from "../../src/core/errors.js";
import type { JsonValue } from "../../src/core/json.js";
import type { Redact, RedactTarget } from "../../src/session/session.js";
import { counterDoc, newTask, openHarness } from "./support.js";

const TOKEN = /sk-[A-Za-z0-9]{16,}/g;

function maskTokens(value: JsonValue): JsonValue {
  if (typeof value === "string") return value.replace(TOKEN, "[REDACTED]");
  if (Array.isArray(value)) return value.map(maskTokens);
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, maskTokens(v)]));
  }
  return value;
}

describe("session limits", () => {
  it("an oversized entry fails with LimitExceeded and rolls back", async () => {
    const { session, conversationId, flaky } = await openHarness({
      limits: { maxEntryBytes: 200 },
    });
    const before = flaky.batches.length;
    let smallId = "";
    const err = await session
      .commit((tx) => {
        smallId = tx.appendEntry({ conversationId, kind: "x", payload: "ok" }).id;
        tx.appendEntry({ conversationId, kind: "x", payload: "y".repeat(500) });
      })
      .catch((e: unknown) => e);
    expect(err).toBeInstanceOf(LimitExceeded);
    expect((err as LimitExceeded).limit).toBe("maxEntryBytes");
    expect(flaky.batches.length).toBe(before);
    expect(await session.reader.getEntry(smallId)).toBeNull();
    expect(session.state).toBe("open");
  });

  it("an oversized document fails with LimitExceeded", async () => {
    const { session, conversationId } = await openHarness({ limits: { maxDocumentBytes: 64 } });
    await expect(
      session.commit(async (tx) => {
        (await tx.doc(counterDoc, conversationId)).value.notes.push("z".repeat(100));
      }),
    ).rejects.toBeInstanceOf(LimitExceeded);
    expect(await session.document(counterDoc, conversationId)).toEqual({ count: 0, notes: [] });
  });

  it("non-JSON payloads roll back without failing the session", async () => {
    const { session, conversationId } = await openHarness();
    await expect(
      session.commit((tx) => {
        tx.appendEntry({ conversationId, kind: "x", payload: { n: Number.NaN } });
      }),
    ).rejects.toThrow();
    expect(session.state).toBe("open");
  });
});

describe("session redaction", () => {
  it("masks a token-shaped string in a tool result before it is stored", async () => {
    const targets: RedactTarget[] = [];
    const redact: Redact = (value, target) => {
      targets.push(target);
      return maskTokens(value);
    };
    const { session, conversationId, flaky } = await openHarness({ redact });
    const secret = "sk-abcdefghijklmnop1234";

    const entry = await session.commit((tx) =>
      tx.appendEntry({
        conversationId,
        kind: "tool_result",
        payload: { output: `token is ${secret}`, isError: false },
      }),
    );
    expect(entry.payload).toEqual({ output: "token is [REDACTED]", isError: false });
    const stored = await session.reader.getEntry(entry.id);
    expect(stored?.payload).toEqual({ output: "token is [REDACTED]", isError: false });
    expect(JSON.stringify(flaky.batches)).not.toContain(secret);
    expect(targets).toContainEqual({ record: "entry", kind: "tool_result", field: "payload" });
  });

  it("redacts tool arguments in task input and error text in outcomes", async () => {
    const secret = "sk-ZZZZZZZZZZZZZZZZZZZZ";
    const { session, conversationId } = await openHarness({ redact: (v) => maskTokens(v) });
    const task = await session.commit((tx) => {
      const t = newTask(tx, conversationId, "tool");
      return tx.replaceTask({ ...t, input: { args: { key: secret } } });
    });
    expect(task.input).toEqual({ args: { key: "[REDACTED]" } });

    await session.commit((tx) =>
      tx.replaceTask({
        ...task,
        status: "terminal",
        outcome: { type: "faulted", message: `bad key ${secret}`, detail: null },
      }),
    );
    const stored = await session.reader.getTask(task.id);
    expect(JSON.stringify(stored)).not.toContain(secret);
    expect(stored?.outcome).toEqual({
      type: "faulted",
      message: "bad key [REDACTED]",
      detail: null,
    });
  });

  it("a throwing redact hook rolls back the commit", async () => {
    const { session, conversationId } = await openHarness({
      redact: (v, t) => {
        if (t.record === "entry") throw new Error("redactor down");
        return v;
      },
    });
    await expect(
      session.commit((tx) => tx.appendEntry({ conversationId, kind: "x", payload: 1 })),
    ).rejects.toThrow("redactor down");
    expect(session.state).toBe("open");
  });
});
