/**
 * Compaction settings v2 (U11): the context-reduction knobs exist, validate,
 * and default to off so old stored configs behave as before.
 */
import { describe, expect, it } from "vitest";
import {
  DEFAULT_COMPACTION_SETTINGS,
  resolveCompactionSettings,
} from "../../src/agent/compaction.js";
import { parseAgentConfigInput } from "../../src/agent/prompt.js";

const NEW_KEYS = [
  "offloadTokens",
  "clearTriggerTokens",
  "keepToolResults",
  "clearAtLeastTokens",
  "excludeTools",
  "structuredSummary",
  "archiveSummarized",
] as const;

const OLD_DEFAULTS = {
  enabled: true,
  contextWindow: null,
  contextWindows: {},
  reserveTokens: 16_384,
  backgroundMargin: 16_384,
  keepRecentTokens: 20_000,
};

describe("compaction settings v2", () => {
  it("resolves old configs to the pre-existing values, with the new knobs off", () => {
    const s = resolveCompactionSettings();
    expect(s).toEqual({
      ...OLD_DEFAULTS,
      offloadTokens: 0,
      clearTriggerTokens: 0,
      keepToolResults: 5,
      clearAtLeastTokens: 8000,
      excludeTools: [],
      structuredSummary: false,
      archiveSummarized: false,
    });
    expect(Object.keys(DEFAULT_COMPACTION_SETTINGS).sort()).toEqual(
      [...Object.keys(OLD_DEFAULTS), ...NEW_KEYS].sort(),
    );
    // an old override (only old keys) resolves like before
    expect(resolveCompactionSettings({ reserveTokens: 100 })).toMatchObject({
      ...OLD_DEFAULTS,
      reserveTokens: 100,
      offloadTokens: 0,
      clearTriggerTokens: 0,
    });
  });

  it("does not change what is serialized for an agent without the new keys", () => {
    const old = parseAgentConfigInput({ compaction: { reserveTokens: 100 } });
    expect(old.ok && old.value.compaction).toEqual({ reserveTokens: 100 });
    const none = parseAgentConfigInput({ compaction: null });
    expect(none.ok && none.value.compaction).toBeNull();
  });

  it("accepts every new field in an override", () => {
    const value = {
      compaction: {
        offloadTokens: 4000,
        clearTriggerTokens: 50_000,
        keepToolResults: 3,
        clearAtLeastTokens: 1000,
        excludeTools: ["memory_view"],
        structuredSummary: true,
        archiveSummarized: true,
      },
    };
    const parsed = parseAgentConfigInput(value);
    expect(parsed.ok && parsed.value.compaction).toEqual(value.compaction);
    expect(resolveCompactionSettings(value.compaction)).toMatchObject(value.compaction);
  });

  it.each([
    ["offloadTokens", "x"],
    ["offloadTokens", -1],
    ["offloadTokens", 1.5],
    ["clearTriggerTokens", "x"],
    ["clearTriggerTokens", -1],
    ["keepToolResults", "x"],
    ["keepToolResults", -1],
    ["clearAtLeastTokens", "x"],
    ["clearAtLeastTokens", -5],
    ["excludeTools", "memory_view"],
    ["excludeTools", [1]],
    ["excludeTools", [null]],
    ["structuredSummary", "yes"],
    ["structuredSummary", 1],
    ["archiveSummarized", "no"],
  ])("rejects %s = %j", (key, bad) => {
    expect(parseAgentConfigInput({ compaction: { [key]: bad } }).ok).toBe(false);
    expect(() => resolveCompactionSettings({ [key]: bad } as never)).toThrow(RangeError);
  });

  it("still rejects unknown keys", () => {
    expect(parseAgentConfigInput({ compaction: { offloadTokenz: 1 } }).ok).toBe(false);
  });

  it("layers overrides: harness default, then conversation", () => {
    const s = resolveCompactionSettings(
      { offloadTokens: 4000, excludeTools: ["a"], keepToolResults: 2 },
      { offloadTokens: 0, excludeTools: ["b"], structuredSummary: true },
      null,
      undefined,
    );
    expect(s.offloadTokens).toBe(0);
    expect(s.excludeTools).toEqual(["b"]);
    expect(s.keepToolResults).toBe(2);
    expect(s.structuredSummary).toBe(true);
    expect(s.clearAtLeastTokens).toBe(8000);
  });

  it("does not let a resolved result mutate the shared defaults", () => {
    const s = resolveCompactionSettings();
    s.excludeTools.push("x");
    expect(DEFAULT_COMPACTION_SETTINGS.excludeTools).toEqual([]);
    expect(resolveCompactionSettings().excludeTools).toEqual([]);
  });
});
