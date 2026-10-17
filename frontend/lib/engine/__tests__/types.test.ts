import { WireError, parseConversationView, type ConversationView } from '../types';

const base = (extra: Record<string, unknown> = {}) => ({
  entries: [],
  live: { runId: null, taskId: null, message: null },
  inbox: { queue: [], placed: [] },
  approvals: { items: [] },
  usage: { requests: 0, total: {}, byModel: {}, byPurpose: {} },
  agent: {},
  toolOutput: {},
  ...extra,
});

describe('parseConversationView tasks', () => {
  it('accepts a view without tasks (older engine)', () => {
    expect(parseConversationView(base()).tasks).toBeUndefined();
  });

  it('accepts a tasks map', () => {
    const tasks = { t1: { kind: 'workflow_run', parentId: null, status: 'running', phase: 'start', startedAt: '2026-01-01T00:00:00.000Z' } };
    const v: ConversationView = parseConversationView(base({ tasks }));
    expect(v.tasks?.t1.kind).toBe('workflow_run');
  });

  it('rejects a malformed tasks member', () => {
    expect(() => parseConversationView(base({ tasks: [] }))).toThrow(WireError);
    expect(() => parseConversationView(base({ tasks: 'x' }))).toThrow(WireError);
  });
});
