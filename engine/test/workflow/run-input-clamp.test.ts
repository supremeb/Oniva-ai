import { describe, expect, it } from "vitest";
import { MAX_RUN_RETRIES, parseRunInput } from "../../src/workflow/run-task.js";

const base = {
  version: 1,
  runId: "r1",
  runAttempt: 0,
  errorMode: "stop",
  variables: {},
  graph: {
    nodes: [
      {
        id: "a",
        type: "start",
        deps: [],
        policy: { maxRetries: 0, backoffMs: 0, timeoutMs: 0, onError: "stop" },
      },
    ],
  },
};

describe("parseRunInput retry clamp", () => {
  it("clamps a huge retry.max to MAX_RUN_RETRIES", () => {
    const input = parseRunInput({ ...base, retry: { max: 1_000_000_000, delayMs: 5 } });
    expect(input.retry).toEqual({ max: MAX_RUN_RETRIES, delayMs: 5 });
  });

  it("keeps a retry.max within the cap", () => {
    expect(parseRunInput({ ...base, retry: { max: 3, delayMs: 0 } }).retry?.max).toBe(3);
  });
});
