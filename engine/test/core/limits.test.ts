import { describe, expect, it } from "vitest";
import { LimitExceeded } from "../../src/core/errors.js";
import { DEFAULT_LIMITS, enforceLimit, resolveLimits } from "../../src/core/limits.js";

describe("limits", () => {
  it("defines a default for every R20 limit", () => {
    expect(Object.keys(DEFAULT_LIMITS).sort()).toEqual(
      [
        "maxDocumentBytes",
        "maxEntryBytes",
        "maxRequestBodyBytes",
        "maxSubagentDepth",
        "maxToolCallsPerRound",
        "maxToolResultTokens",
      ].sort(),
    );
    for (const v of Object.values(DEFAULT_LIMITS)) {
      expect(Number.isSafeInteger(v) && v > 0).toBe(true);
    }
    expect(Object.isFrozen(DEFAULT_LIMITS)).toBe(true);
  });

  it("merges overrides and validates them", () => {
    const limits = resolveLimits({ maxSubagentDepth: 1 });
    expect(limits.maxSubagentDepth).toBe(1);
    expect(limits.maxToolCallsPerRound).toBe(DEFAULT_LIMITS.maxToolCallsPerRound);
    expect(() => resolveLimits({ maxEntryBytes: 0 })).toThrow(RangeError);
    expect(() => resolveLimits({ maxEntryBytes: 1.5 })).toThrow(RangeError);
  });

  it("enforceLimit throws LimitExceeded only above the max", () => {
    const limits = resolveLimits({ maxToolCallsPerRound: 2 });
    expect(() => enforceLimit(limits, "maxToolCallsPerRound", 2)).not.toThrow();
    expect(() => enforceLimit(limits, "maxToolCallsPerRound", 3)).toThrow(LimitExceeded);
  });
});
