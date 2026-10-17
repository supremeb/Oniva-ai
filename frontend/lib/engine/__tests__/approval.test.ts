import { DecisionError, describeApproval, summarizeArguments, fullArguments, argumentsFingerprint, SCRUBBED, toDecisionError } from '../approval';

const apiError = (status: number, detail?: unknown) =>
  Object.assign(new Error('x'), { status, data: detail === undefined ? undefined : { detail } });

describe('toDecisionError', () => {
  it('403 not_authorized (string detail, as FastAPI sends) locks with the ask-an-admin copy', () => {
    const e = toDecisionError(apiError(403, 'not_authorized'));
    expect(e).toBeInstanceOf(DecisionError);
    expect(e).toMatchObject({ message: 'You cannot decide this. Ask an admin.', retryable: false, status: 403 });
  });

  it('403 reads the code from detail.code too', () => {
    expect(toDecisionError(apiError(403, { code: 'not_authorized' })).message).toBe('You cannot decide this. Ask an admin.');
    expect(toDecisionError(apiError(403, { code: 'self_approval_forbidden' }))).toMatchObject({
      message: 'Another admin must approve this.',
      retryable: false,
    });
  });

  it('403 self_approval_forbidden has its own copy', () => {
    expect(toDecisionError(apiError(403, 'self_approval_forbidden'))).toMatchObject({
      message: 'Another admin must approve this.',
      retryable: false,
    });
  });

  it('403 with no or unknown code falls back to the not-allowed copy', () => {
    expect(toDecisionError(apiError(403)).message).toBe('You cannot decide this. Ask an admin.');
    expect(toDecisionError(apiError(403, 'weird')).message).toBe('You cannot decide this. Ask an admin.');
    expect(toDecisionError({ status: 403 }).retryable).toBe(false);
  });

  it('404/409/410 mean already decided or expired and lock', () => {
    for (const s of [404, 409, 410]) {
      expect(toDecisionError(apiError(s))).toMatchObject({
        message: 'This request was already decided or has expired.',
        retryable: false,
      });
    }
    expect(toDecisionError(apiError(409, 'already_decided')).retryable).toBe(false);
  });

  it('409 decision_in_progress is retryable', () => {
    expect(toDecisionError(apiError(409, 'decision_in_progress'))).toMatchObject({
      message: 'Another decision is being processed. Try again in a moment.',
      retryable: true,
    });
  });

  it('409 approval_not_indexed locks with its own copy', () => {
    const e = toDecisionError(apiError(409, 'approval_not_indexed'));
    expect(e.retryable).toBe(false);
    expect(e.message).not.toBe('This request was already decided or has expired.');
  });

  it('5xx and network errors stay retryable', () => {
    expect(toDecisionError(apiError(500))).toMatchObject({ retryable: true, message: 'Could not send your decision. Try again.' });
    expect(toDecisionError(apiError(503)).retryable).toBe(true);
    expect(toDecisionError(new Error('network')).retryable).toBe(true);
    expect(toDecisionError(null).retryable).toBe(true);
  });
});

describe('describeApproval', () => {
  it('returns the reason as is', () => {
    expect(describeApproval({ reason: 'Requires admin approval' })).toBe('Requires admin approval');
    expect(describeApproval({ rule: 'rule_of_two', reason: 'This run read untrusted content' })).toBe('This run read untrusted content');
  });

  it('falls back to rule copy when there is no reason', () => {
    expect(describeApproval({ rule: 'rule_of_two', reason: null })).toMatch(/untrusted content/i);
    expect(describeApproval({ rule: 'requires_approval', reason: '  ' })).toMatch(/approval/i);
  });

  it('is undefined with neither', () => {
    expect(describeApproval({ reason: null })).toBeUndefined();
    expect(describeApproval({})).toBeUndefined();
  });
});

describe('summarizeArguments', () => {
  it('scrubs credential-looking keys and keeps the rest', () => {
    const rows = summarizeArguments({ filename: 'a.md', api_key: 'sk-1', Authorization: 'Bearer x', password: 'p' });
    expect(rows.find((r) => r.label === 'filename')?.value).toBe('a.md');
    for (const l of ['api_key', 'Authorization', 'password']) {
      expect(rows.find((r) => r.label === l)).toMatchObject({ value: SCRUBBED, sensitive: true });
    }
  });
  it('null args give no rows', () => expect(summarizeArguments(null)).toEqual([]));
});

describe('fullArguments (approval cards)', () => {
  const long = 'x'.repeat(5000) + 'TAIL';
  it('never truncates a value, while summarizeArguments still does', () => {
    expect(fullArguments({ code: long })[0].value).toBe(long);
    expect(summarizeArguments({ code: long })[0].value.length).toBeLessThan(long.length);
  });
  it('still scrubs credential keys', () => {
    expect(fullArguments({ api_key: 'sk-1' })[0]).toMatchObject({ value: SCRUBBED, sensitive: true });
  });
  it('fingerprint is short, stable under key order, and changes with any argument byte', () => {
    const a = argumentsFingerprint({ a: 1, b: { c: 2, d: 3 } });
    expect(a).toMatch(/^[0-9a-f]{12}$/);
    expect(argumentsFingerprint({ b: { d: 3, c: 2 }, a: 1 })).toBe(a);
    expect(argumentsFingerprint({ a: 1, b: { c: 2, d: 4 } })).not.toBe(a);
    expect(argumentsFingerprint({ code: long })).not.toBe(argumentsFingerprint({ code: long.slice(0, 200) }));
  });
});
