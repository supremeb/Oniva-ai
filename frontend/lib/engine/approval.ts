/**
 * Approval helpers (plan 2026-10-15-001, U8): error mapping for the decision
 * route and credential scrubbing of the arguments shown on an approval card.
 */

/** A failed decision with the message the card shows. `retryable: false` locks the card. */
export class DecisionError extends Error {
  constructor(
    message: string,
    readonly retryable: boolean,
    readonly status?: number
  ) {
    super(message);
    this.name = 'DecisionError';
  }
}

/** Map a failed `decide` call (an APIError-like with `status`) to the card's inline error. */
export function toDecisionError(e: unknown): DecisionError {
  const status = typeof (e as { status?: unknown } | null)?.status === 'number' ? (e as { status: number }).status : undefined;
  if (status === 403) return new DecisionError('You cannot decide this.', false, status);
  if (status === 404 || status === 409 || status === 410) {
    return new DecisionError('This request was already decided or has expired.', false, status);
  }
  return new DecisionError('Could not send your decision. Try again.', true, status);
}

const SENSITIVE_KEY = /pass(word|wd)?|secret|token|api[-_]?key|authorization|credential|cookie|private[-_]?key|bearer|session/i;
const MAX_VALUE = 160;
export const SCRUBBED = '[hidden]';

/** Short, credential-scrubbed `label: value` rows for an approval's arguments. */
export function summarizeArguments(
  args: { [key: string]: unknown } | null | undefined
): Array<{ label: string; value: string; sensitive?: boolean }> {
  if (!args) return [];
  return Object.entries(args).map(([label, raw]) => {
    if (SENSITIVE_KEY.test(label)) return { label, value: SCRUBBED, sensitive: true };
    let value = typeof raw === 'string' ? raw : JSON.stringify(raw) ?? '';
    if (value.length > MAX_VALUE) value = `${value.slice(0, MAX_VALUE)}...`;
    return { label, value };
  });
}
