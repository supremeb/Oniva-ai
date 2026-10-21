import { describe, expect, it } from "vitest";
import { compactionThresholds, resolveCompactionSettings } from "../../src/agent/compaction.js";
import {
  capToolResult,
  TOKEN_TRUNCATED_RESULT_MARKER,
  TRUNCATED_RESULT_MARKER,
} from "../../src/agent/tool-task.js";
import { DEFAULT_LIMITS } from "../../src/core/limits.js";

const call = { id: "c1", name: "browse_url" };

describe("token-based tool result cap", () => {
  it("defaults to 25k tokens", () => {
    expect(DEFAULT_LIMITS.maxToolResultTokens).toBe(25_000);
  });

  it("leaves a result within the token cap alone", () => {
    const small = { content: "x".repeat(400) };
    expect(capToolResult(call, small, 256 * 1024, 100)).toBe(small);
  });

  it("cuts a result over the token cap (chars / 4) and says how to get more", () => {
    const out = capToolResult(call, { content: "a".repeat(10_000) }, 256 * 1024, 100);
    const text = out.content as string;
    expect(text.startsWith("a".repeat(400))).toBe(true);
    expect(text).toContain(TOKEN_TRUNCATED_RESULT_MARKER);
    expect(text).toContain("kept 400 of 10000 characters");
    expect(text).toMatch(/offset|page/);
    expect(text).not.toContain(TRUNCATED_RESULT_MARKER);
  });

  it("handles array content and keeps isError", () => {
    const out = capToolResult(
      call,
      { content: [{ type: "text", text: "b".repeat(2_000) }], isError: true },
      256 * 1024,
      50,
    );
    expect(typeof out.content).toBe("string");
    expect(out.isError).toBe(true);
    expect((out.content as string).startsWith("b".repeat(200))).toBe(true);
  });

  it("still applies the byte cap on top", () => {
    const out = capToolResult(call, { content: "z".repeat(100_000) }, 8192, 1_000_000);
    expect(out.content as string).toContain(TRUNCATED_RESULT_MARKER);
  });

  it("without a token cap behaves as before", () => {
    const big = { content: "q".repeat(50_000) };
    expect(capToolResult(call, big, 256 * 1024)).toBe(big);
  });
});

describe("compaction window from the policy", () => {
  const s = resolveCompactionSettings({ reserveTokens: 100, backgroundMargin: 200 });
  it("uses the resolved window over the default", () => {
    expect(compactionThresholds(s, "m", 1000)).toEqual({
      window: 1000,
      blocking: 900,
      background: 700,
    });
  });
  it("explicit settings window still wins; unknown falls back to the default", () => {
    const fixed = resolveCompactionSettings({ contextWindow: 500 });
    expect(compactionThresholds(fixed, "m", 1000).window).toBe(500);
    expect(compactionThresholds(s, "m", undefined).window).toBe(200_000);
  });
});
