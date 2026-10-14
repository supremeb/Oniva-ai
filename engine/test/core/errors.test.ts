import { describe, expect, it } from "vitest";
import {
  ConversationBusy,
  EngineError,
  isEngineError,
  LeaseLost,
  LimitExceeded,
  NotOwner,
  SessionFailed,
  StorageRequestError,
} from "../../src/core/errors.js";

describe("errors", () => {
  it("are typed, coded EngineErrors", () => {
    const cases: Array<[EngineError, string, string]> = [
      [new SessionFailed("s1", "boom"), "session_failed", "SessionFailed"],
      [
        new StorageRequestError("insert failed", { retryable: true }),
        "storage_request",
        "StorageRequestError",
      ],
      [new LeaseLost("s1", 3), "lease_lost", "LeaseLost"],
      [new ConversationBusy("conv_1"), "conversation_busy", "ConversationBusy"],
      [new NotOwner("s1", "worker-b"), "not_owner", "NotOwner"],
      [new LimitExceeded("maxToolCallsPerRound", 33, 32), "limit_exceeded", "LimitExceeded"],
    ];
    for (const [err, code, name] of cases) {
      expect(err).toBeInstanceOf(Error);
      expect(err).toBeInstanceOf(EngineError);
      expect(isEngineError(err)).toBe(true);
      expect(err.code).toBe(code);
      expect(err.name).toBe(name);
    }
  });

  it("carry structured details", () => {
    expect(new NotOwner("s1", "worker-b").ownerId).toBe("worker-b");
    expect(new LeaseLost("s1", 7).token).toBe(7);
    const limit = new LimitExceeded("maxEntryBytes", 10, 5);
    expect(limit).toMatchObject({ limit: "maxEntryBytes", actual: 10, max: 5 });
    const cause = new Error("pg down");
    const storage = new StorageRequestError("query failed", { retryable: false, cause });
    expect(storage.retryable).toBe(false);
    expect(storage.cause).toBe(cause);
    expect(isEngineError(new Error("plain"))).toBe(false);
  });
});
