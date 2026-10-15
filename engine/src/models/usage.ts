/**
 * Token usage arithmetic and cost from a host-supplied pricing table. The
 * engine ships no prices: they change, and hosts may have negotiated rates.
 */
import type { Usage, UsageCost } from "./types.js";

/** USD per million tokens. Cache prices default to 0.1x / 1.25x of `input`. */
export interface ModelPrice {
  input: number;
  output: number;
  cacheRead?: number;
  cacheWrite?: number;
}

/** Keyed by `provider/model` (preferred) or bare `model`. */
export type PricingTable = Readonly<Record<string, ModelPrice>>;

const MILLION = 1_000_000;

export function emptyUsage(): Usage {
  return { inputTokens: 0, outputTokens: 0, cacheReadTokens: 0, cacheWriteTokens: 0 };
}

/** Sum token counts. Cost is summed only when both sides carry one. */
export function addUsage(a: Usage, b: Usage): Usage {
  const sum: Usage = {
    inputTokens: a.inputTokens + b.inputTokens,
    outputTokens: a.outputTokens + b.outputTokens,
    cacheReadTokens: a.cacheReadTokens + b.cacheReadTokens,
    cacheWriteTokens: a.cacheWriteTokens + b.cacheWriteTokens,
  };
  if (a.cost && b.cost) {
    sum.cost = {
      input: a.cost.input + b.cost.input,
      output: a.cost.output + b.cost.output,
      cacheRead: a.cost.cacheRead + b.cost.cacheRead,
      cacheWrite: a.cost.cacheWrite + b.cost.cacheWrite,
      total: a.cost.total + b.cost.total,
    };
  }
  return sum;
}

/** Tokens the request and its reply occupy in the context window. */
export function contextTokens(usage: Usage): number {
  return usage.inputTokens + usage.cacheReadTokens + usage.cacheWriteTokens + usage.outputTokens;
}

export function findPrice(
  table: PricingTable | undefined,
  provider: string,
  model: string,
): ModelPrice | undefined {
  if (!table) return undefined;
  const qualified = `${provider}/${model}`;
  if (Object.hasOwn(table, qualified)) return table[qualified];
  return Object.hasOwn(table, model) ? table[model] : undefined;
}

export function priceUsage(
  usage: Usage,
  provider: string,
  model: string,
  table: PricingTable | undefined,
): UsageCost | undefined {
  const price = findPrice(table, provider, model);
  if (!price) return undefined;
  const input = (usage.inputTokens * price.input) / MILLION;
  const output = (usage.outputTokens * price.output) / MILLION;
  const cacheRead = (usage.cacheReadTokens * (price.cacheRead ?? price.input * 0.1)) / MILLION;
  const cacheWrite = (usage.cacheWriteTokens * (price.cacheWrite ?? price.input * 1.25)) / MILLION;
  return { input, output, cacheRead, cacheWrite, total: input + output + cacheRead + cacheWrite };
}

/** A copy of `usage` with `cost` set (or removed when the model is unpriced). */
export function withCost(
  usage: Usage,
  provider: string,
  model: string,
  table: PricingTable | undefined,
): Usage {
  const { cost: _drop, ...tokens } = usage;
  const cost = priceUsage(tokens, provider, model, table);
  return cost ? { ...tokens, cost } : tokens;
}
