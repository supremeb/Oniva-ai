/**
 * Engine-level limits (R20) with defaults. Hosts may override any of them at
 * harness open; every value must be a positive safe integer.
 */
import { LimitExceeded } from "./errors.js";

export interface EngineLimits {
  /** Tool calls the model may request in a single round. */
  maxToolCallsPerRound: number;
  /** Maximum nesting depth of subagents (the top-level agent is depth 0). */
  maxSubagentDepth: number;
  /** Maximum HTTP request body size, in bytes. */
  maxRequestBodyBytes: number;
  /** Maximum serialized size of one entry in a commit, in bytes. */
  maxEntryBytes: number;
  /** Maximum serialized size of one document snapshot in a commit, in bytes. */
  maxDocumentBytes: number;
  /**
   * Largest tool result, in estimated tokens (characters / 4), the model sees;
   * longer results are cut with a note on how to fetch the rest.
   */
  maxToolResultTokens: number;
}

export type LimitName = keyof EngineLimits;

export const DEFAULT_LIMITS: Readonly<EngineLimits> = Object.freeze({
  maxToolCallsPerRound: 32,
  maxSubagentDepth: 3,
  maxRequestBodyBytes: 1024 * 1024,
  maxEntryBytes: 256 * 1024,
  maxDocumentBytes: 1024 * 1024,
  maxToolResultTokens: 25_000,
});

export function resolveLimits(overrides: Partial<EngineLimits> = {}): Readonly<EngineLimits> {
  const merged: EngineLimits = { ...DEFAULT_LIMITS };
  for (const [name, value] of Object.entries(overrides) as Array<[LimitName, number | undefined]>) {
    if (value === undefined) continue;
    if (!(name in DEFAULT_LIMITS)) throw new RangeError(`unknown limit ${name}`);
    if (!Number.isSafeInteger(value) || value <= 0) {
      throw new RangeError(`limit ${name} must be a positive integer, got ${value}`);
    }
    merged[name] = value;
  }
  return Object.freeze(merged);
}

/** Throw LimitExceeded when `actual` is above the configured maximum. */
export function enforceLimit(
  limits: Readonly<EngineLimits>,
  name: LimitName,
  actual: number,
): void {
  const max = limits[name];
  if (actual > max) throw new LimitExceeded(name, actual, max);
}
