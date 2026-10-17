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

const NOT_AUTHORIZED = 'You cannot decide this. Ask an admin.';

/** The machine code the backend sent: FastAPI's `detail` string, or `detail.code`. */
function errorCode(e: unknown): string | undefined {
  const detail = (e as { data?: { detail?: unknown } } | null)?.data?.detail;
  if (typeof detail === 'string') return detail;
  const code = (detail as { code?: unknown } | null | undefined)?.code;
  return typeof code === 'string' ? code : undefined;
}

/** Map a failed `decide` call (an APIError-like with `status` and parsed `data`) to the card's inline error. */
export function toDecisionError(e: unknown): DecisionError {
  const status = typeof (e as { status?: unknown } | null)?.status === 'number' ? (e as { status: number }).status : undefined;
  const code = errorCode(e);
  if (status === 403) {
    if (code === 'self_approval_forbidden') return new DecisionError('Another admin must approve this.', false, status);
    return new DecisionError(NOT_AUTHORIZED, false, status);
  }
  if (status === 409 && code === 'decision_in_progress') {
    return new DecisionError('Another decision is being processed. Try again in a moment.', true, status);
  }
  if (status === 409 && code === 'approval_not_indexed') {
    return new DecisionError('This request cannot be decided from here yet. Ask an admin.', false, status);
  }
  if (status === 404 || status === 409 || status === 410) {
    return new DecisionError('This request was already decided or has expired.', false, status);
  }
  if (status === 400 || status === 422) {
    return new DecisionError('That answer was not accepted. Check it and try again.', true, status);
  }
  return new DecisionError('Could not send your decision. Try again.', true, status);
}

const RULE_COPY: Record<string, string> = {
  rule_of_two: 'This run read untrusted content, so this action needs a person to approve it.',
  requires_approval: 'This action requires approval.',
};

/** Copy for why an approval is needed. The server-written reason wins; the rule is the fallback. */
export function describeApproval(a: { rule?: string | null; reason?: string | null }): string | undefined {
  const reason = a.reason?.trim();
  if (reason) return reason;
  return a.rule ? RULE_COPY[a.rule] : undefined;
}

const SENSITIVE_KEY = /pass(word|wd)?|secret|token|api[-_]?key|authorization|credential|cookie|private[-_]?key|bearer|session/i;
const MAX_VALUE = 160;
export const SCRUBBED = '[hidden]';

type ArgRow = { label: string; value: string; sensitive?: boolean };

function rows(args: { [key: string]: unknown } | null | undefined, max: number): ArgRow[] {
  if (!args) return [];
  return Object.entries(args).map(([label, raw]) => {
    if (SENSITIVE_KEY.test(label)) return { label, value: SCRUBBED, sensitive: true };
    let value = typeof raw === 'string' ? raw : JSON.stringify(raw) ?? '';
    if (value.length > max) value = `${value.slice(0, max)}...`;
    return { label, value };
  });
}

/** Short, credential-scrubbed `label: value` rows (values truncated). Never use for an approval decision. */
export function summarizeArguments(args: { [key: string]: unknown } | null | undefined): ArgRow[] {
  return rows(args, MAX_VALUE);
}

/** Credential-scrubbed rows with the FULL value of every argument: what an approver must see. */
export function fullArguments(args: { [key: string]: unknown } | null | undefined): ArgRow[] {
  return rows(args, Number.POSITIVE_INFINITY);
}

function canonical(v: unknown): string {
  if (Array.isArray(v)) return `[${v.map(canonical).join(',')}]`;
  if (v !== null && typeof v === 'object') {
    const o = v as Record<string, unknown>;
    return `{${Object.keys(o).sort().map((k) => `${JSON.stringify(k)}:${canonical(o[k])}`).join(',')}}`;
  }
  return JSON.stringify(v) ?? 'null';
}

/** 12 hex chars of a 64-bit FNV-1a over the key-sorted canonical JSON of the arguments. */
export function argumentsFingerprint(args: { [key: string]: unknown } | null | undefined): string {
  const text = canonical(args ?? {});
  let h1 = 0x811c9dc5;
  let h2 = 0xcbf29ce4;
  for (let i = 0; i < text.length; i++) {
    const c = text.charCodeAt(i);
    h1 = Math.imul(h1 ^ c, 0x01000193) >>> 0;
    h2 = Math.imul(h2 ^ c ^ (h1 & 0xff), 0x85ebca6b) >>> 0;
  }
  return (h1.toString(16).padStart(8, '0') + h2.toString(16).padStart(8, '0')).slice(0, 12);
}
