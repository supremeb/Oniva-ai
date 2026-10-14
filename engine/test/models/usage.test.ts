import { describe, expect, it } from "vitest";
import type { Usage } from "../../src/models/types.js";
import {
  addUsage,
  contextTokens,
  emptyUsage,
  findPrice,
  type PricingTable,
  priceUsage,
} from "../../src/models/usage.js";

const usage: Usage = {
  inputTokens: 1_000_000,
  outputTokens: 500_000,
  cacheReadTokens: 2_000_000,
  cacheWriteTokens: 400_000,
};

describe("usage pricing", () => {
  it("prices every token class from the host table", () => {
    const table: PricingTable = {
      "m-1": { input: 4, output: 20, cacheRead: 0.2, cacheWrite: 5 },
    };
    const cost = priceUsage(usage, "anthropic", "m-1", table);
    expect(cost).toEqual({ input: 4, output: 10, cacheRead: 0.4, cacheWrite: 2, total: 16.4 });
  });

  it("derives cache prices from the input price when absent", () => {
    const cost = priceUsage(usage, "p", "m", { m: { input: 10, output: 50 } });
    expect(cost?.cacheRead).toBeCloseTo(2);
    expect(cost?.cacheWrite).toBeCloseTo(5);
  });

  it("prefers a provider-qualified entry and returns undefined for unpriced models", () => {
    const table: PricingTable = { m: { input: 1, output: 1 }, "p/m": { input: 2, output: 2 } };
    expect(findPrice(table, "p", "m")?.input).toBe(2);
    expect(findPrice(table, "q", "m")?.input).toBe(1);
    expect(priceUsage(usage, "p", "other", table)).toBeUndefined();
    expect(priceUsage(usage, "p", "m", undefined)).toBeUndefined();
  });

  it("adds usage and counts context tokens", () => {
    const sum = addUsage(emptyUsage(), usage);
    expect(sum).toEqual(usage);
    expect(contextTokens(usage)).toBe(3_900_000);
  });
});
